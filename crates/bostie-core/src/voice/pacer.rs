use std::any::Any;
use std::io;
use std::panic::{self, AssertUnwindSafe};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::thread;
use std::time::{Duration, Instant};

use parking_lot::Mutex;

use crate::error::CoreError;
use crate::voice::types::FRAME;

pub const AFTER_MARGIN: Duration = Duration::from_millis(2);

const LANES_PER_THREAD: usize = 4;
const LATE_REANCHOR: Duration = FRAME.saturating_mul(3);
const WORK_SMOOTHING: u64 = 8;
const AFTER_BUCKETS: usize = 32;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Tick {
    pub now: Instant,
    pub skipped: u32,
}

pub type LaneFn = Box<dyn FnMut(Tick) -> bool + Send>;
pub type AfterFn = Box<dyn FnMut(Tick, Instant) -> bool + Send>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ThreadLoad {
    pub lanes: usize,
    pub work_ewma: Duration,
    pub work_max: Duration,
    pub after_ewma: Duration,
    pub after_max: Duration,
    pub after_p50: Duration,
    pub after_p99: Duration,
    pub late_ticks: u64,
    pub lane_panics: u64,
}

pub struct PacerPool {
    mode: Mode,
    epoch: Instant,
}

enum Mode {
    Threads(Mutex<Vec<Worker>>),
    #[cfg(test)]
    Manual(Box<Mutex<Manual>>),
}

struct Worker {
    sender: Sender<Assignment>,
    load: Arc<Load>,
}

struct Assignment {
    lane: LaneFn,
    after: Option<AfterFn>,
}

#[derive(Default)]
struct Load {
    lanes: AtomicUsize,
    work_ewma: AtomicU64,
    work_max: AtomicU64,
    after_ewma: AtomicU64,
    after_max: AtomicU64,
    after_micros: [AtomicU64; AFTER_BUCKETS],
    late_ticks: AtomicU64,
    lane_panics: AtomicU64,
}

#[cfg(test)]
struct Manual {
    lanes: Vec<LaneFn>,
    afters: Vec<AfterFn>,
    now: Instant,
    load: Load,
}

impl PacerPool {
    pub fn new() -> Self {
        Self {
            mode: Mode::Threads(Mutex::new(Vec::new())),
            epoch: Instant::now(),
        }
    }

    #[cfg(test)]
    pub fn manual() -> Self {
        Self {
            mode: Mode::Manual(Box::new(Mutex::new(Manual {
                lanes: Vec::new(),
                afters: Vec::new(),
                now: Instant::now(),
                load: Load::default(),
            }))),
            epoch: Instant::now(),
        }
    }

    #[cfg(test)]
    pub fn tick(&self, n: u32) {
        for _ in 0..n {
            self.tick_skipping(0);
        }
    }

    #[cfg(test)]
    pub fn tick_skipping(&self, skipped: u32) {
        let Mode::Manual(manual) = &self.mode else {
            return;
        };
        let mut manual = manual.lock();
        let Manual {
            lanes,
            afters,
            now,
            load,
        } = &mut *manual;
        *now += FRAME.saturating_mul(skipped.saturating_add(1));
        load.late_ticks
            .fetch_add(u64::from(skipped), Ordering::Relaxed);
        let tick = Tick { now: *now, skipped };
        run_lanes(lanes, tick, load);
        run_afters(afters, tick, Instant::now() + FRAME, load);
        load.lanes.store(lanes.len(), Ordering::Relaxed);
    }

    pub fn assign(&self, lane: LaneFn) -> Result<usize, CoreError> {
        self.place(Assignment { lane, after: None })
    }

    pub fn assign_pair(&self, lane: LaneFn, after: AfterFn) -> Result<usize, CoreError> {
        self.place(Assignment {
            lane,
            after: Some(after),
        })
    }

    fn place(&self, assignment: Assignment) -> Result<usize, CoreError> {
        match &self.mode {
            Mode::Threads(workers) => assign(&mut workers.lock(), assignment, self.epoch),
            #[cfg(test)]
            Mode::Manual(manual) => {
                let mut manual = manual.lock();
                let Manual {
                    lanes,
                    afters,
                    load,
                    ..
                } = &mut *manual;
                assignment.join(lanes, afters);
                load.lanes.fetch_add(1, Ordering::Relaxed);
                Ok(0)
            }
        }
    }

    pub fn lanes(&self) -> usize {
        self.threads().iter().map(|thread| thread.lanes).sum()
    }

    pub fn threads(&self) -> Vec<ThreadLoad> {
        match &self.mode {
            Mode::Threads(workers) => workers
                .lock()
                .iter()
                .map(|worker| worker.load.snapshot())
                .collect(),
            #[cfg(test)]
            Mode::Manual(manual) => vec![manual.lock().load.snapshot()],
        }
    }
}

impl Default for PacerPool {
    fn default() -> Self {
        Self::new()
    }
}

fn assign(
    workers: &mut Vec<Worker>,
    assignment: Assignment,
    epoch: Instant,
) -> Result<usize, CoreError> {
    let index = workers
        .iter()
        .enumerate()
        .map(|(index, worker)| (index, worker.load.lanes.load(Ordering::Relaxed)))
        .filter(|&(_, lanes)| lanes < LANES_PER_THREAD)
        .min_by_key(|&(_, lanes)| lanes)
        .map(|(index, _)| index);
    let index = match index {
        Some(index) => index,
        None => {
            workers.push(Worker::spawn(workers.len(), epoch)?);
            workers.len() - 1
        }
    };
    let Some(worker) = workers.get_mut(index) else {
        return Err(CoreError::Runtime(io::Error::other(
            "the voice pacer lost a thread",
        )));
    };
    worker.load.lanes.fetch_add(1, Ordering::Relaxed);
    if let Err(mpsc::SendError(assignment)) = worker.sender.send(assignment) {
        *worker = Worker::spawn(index, epoch)?;
        worker.load.lanes.fetch_add(1, Ordering::Relaxed);
        worker.sender.send(assignment).map_err(|_| {
            CoreError::Runtime(io::Error::other("the voice pacer thread ended at once"))
        })?;
    }
    Ok(index)
}

impl Worker {
    fn spawn(index: usize, epoch: Instant) -> Result<Self, CoreError> {
        let (sender, receiver) = mpsc::channel();
        let load = Arc::new(Load::default());
        let thread_load = Arc::clone(&load);
        let origin = epoch + phase(index);
        thread::Builder::new()
            .name(format!("bostie-voice-{index}"))
            .spawn(move || pace(&receiver, &thread_load, &Wall, origin))
            .map_err(CoreError::Runtime)?;
        Ok(Self { sender, load })
    }
}

impl Assignment {
    fn join(self, lanes: &mut Vec<LaneFn>, afters: &mut Vec<AfterFn>) {
        lanes.push(self.lane);
        afters.extend(self.after);
    }
}

impl Load {
    fn snapshot(&self) -> ThreadLoad {
        let after_max = Duration::from_nanos(self.after_max.load(Ordering::Relaxed));
        let after_micros = self
            .after_micros
            .each_ref()
            .map(|count| count.load(Ordering::Relaxed));
        ThreadLoad {
            lanes: self.lanes.load(Ordering::Relaxed),
            work_ewma: Duration::from_nanos(self.work_ewma.load(Ordering::Relaxed)),
            work_max: Duration::from_nanos(self.work_max.load(Ordering::Relaxed)),
            after_ewma: Duration::from_nanos(self.after_ewma.load(Ordering::Relaxed)),
            after_max,
            after_p50: quantile(&after_micros, 50).min(after_max),
            after_p99: quantile(&after_micros, 99).min(after_max),
            late_ticks: self.late_ticks.load(Ordering::Relaxed),
            lane_panics: self.lane_panics.load(Ordering::Relaxed),
        }
    }

    fn record_work(&self, work: Duration) {
        smooth(&self.work_ewma, &self.work_max, work);
    }

    fn record_after(&self, after: Duration) {
        smooth(&self.after_ewma, &self.after_max, after);
        let bucket =
            usize::try_from(u128::BITS - after.as_micros().leading_zeros()).unwrap_or(usize::MAX);
        if let Some(count) = self.after_micros.get(bucket).or(self.after_micros.last()) {
            count.fetch_add(1, Ordering::Relaxed);
        }
    }
}

fn smooth(ewma: &AtomicU64, max: &AtomicU64, took: Duration) {
    let took = u64::try_from(took.as_nanos()).unwrap_or(u64::MAX);
    let average = ewma.load(Ordering::Relaxed);
    ewma.store(
        average - average / WORK_SMOOTHING + took / WORK_SMOOTHING,
        Ordering::Relaxed,
    );
    max.fetch_max(took, Ordering::Relaxed);
}

fn quantile(micros: &[u64], percent: u64) -> Duration {
    let rank = micros
        .iter()
        .sum::<u64>()
        .saturating_mul(percent)
        .div_ceil(100)
        .max(1);
    let mut seen = 0;
    for (&count, bucket) in micros.iter().zip(0_u32..) {
        seen += count;
        if seen >= rank {
            return Duration::from_micros(1 << bucket);
        }
    }
    Duration::ZERO
}

trait Clock {
    fn now(&self) -> Instant;
    fn sleep_until(&self, deadline: Instant);
}

struct Wall;

impl Clock for Wall {
    fn now(&self) -> Instant {
        Instant::now()
    }

    fn sleep_until(&self, deadline: Instant) {
        if let Some(wait) = deadline.checked_duration_since(Instant::now()) {
            thread::sleep(wait);
        }
    }
}

fn phase(index: usize) -> Duration {
    let turn = u128::from(u32::try_from(index).unwrap_or(u32::MAX).reverse_bits());
    Duration::from_nanos(u64::try_from((FRAME.as_nanos() * turn) >> u32::BITS).unwrap_or(0))
}

fn grid_floor(origin: Instant, at: Instant) -> Instant {
    let frame = FRAME.as_nanos();
    let ticks = at.saturating_duration_since(origin).as_nanos() / frame;
    u64::try_from(ticks * frame)
        .ok()
        .and_then(|nanos| origin.checked_add(Duration::from_nanos(nanos)))
        .unwrap_or(at)
}

fn grid_ceil(origin: Instant, at: Instant) -> Instant {
    let floor = grid_floor(origin, at);
    if floor < at { floor + FRAME } else { floor }
}

fn pace(receiver: &Receiver<Assignment>, load: &Load, clock: &impl Clock, origin: Instant) {
    let mut lanes: Vec<LaneFn> = Vec::new();
    let mut afters: Vec<AfterFn> = Vec::new();
    let mut next = clock.now();
    loop {
        if lanes.is_empty() && afters.is_empty() {
            let Ok(assignment) = receiver.recv() else {
                return;
            };
            assignment.join(&mut lanes, &mut afters);
            next = grid_ceil(origin, clock.now());
        }
        for assignment in receiver.try_iter() {
            assignment.join(&mut lanes, &mut afters);
        }
        clock.sleep_until(next);
        let now = clock.now();
        let late = now.saturating_duration_since(next);
        let skipped = if late > LATE_REANCHOR {
            let anchor = grid_floor(origin, now);
            let missed = anchor.saturating_duration_since(next).as_nanos() / FRAME.as_nanos();
            next = anchor;
            u32::try_from(missed).unwrap_or(u32::MAX)
        } else {
            0
        };
        load.late_ticks
            .fetch_add(u64::from(skipped), Ordering::Relaxed);
        let tick = Tick { now, skipped };
        let before = lanes.len();
        run_lanes(&mut lanes, tick, load);
        let sent = clock.now();
        load.record_work(sent.saturating_duration_since(now));
        load.lanes
            .fetch_sub(before - lanes.len(), Ordering::Relaxed);
        if !afters.is_empty() {
            run_afters(&mut afters, tick, next + FRAME - AFTER_MARGIN, load);
            load.record_after(clock.now().saturating_duration_since(sent));
        }
        next += FRAME;
    }
}

fn run_lanes(lanes: &mut Vec<LaneFn>, tick: Tick, load: &Load) {
    lanes.retain_mut(|lane| contained(load, "a voice lane", || lane(tick)));
}

fn run_afters(afters: &mut Vec<AfterFn>, tick: Tick, deadline: Instant, load: &Load) {
    afters.retain_mut(|after| contained(load, "a voice receive phase", || after(tick, deadline)));
    if !afters.is_empty() {
        afters.rotate_left(1);
    }
}

fn contained(load: &Load, what: &str, call: impl FnOnce() -> bool) -> bool {
    panic::catch_unwind(AssertUnwindSafe(call)).unwrap_or_else(|payload| {
        load.lane_panics.fetch_add(1, Ordering::Relaxed);
        tracing::error!(
            panic = panic_message(payload.as_ref()),
            "{what} panicked and was dropped"
        );
        false
    })
}

fn panic_message(payload: &(dyn Any + Send)) -> &str {
    payload
        .downcast_ref::<&str>()
        .copied()
        .or_else(|| payload.downcast_ref::<String>().map(String::as_str))
        .unwrap_or("unknown panic")
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::AtomicBool;
    use std::sync::mpsc::RecvTimeoutError;

    use super::*;

    const PATIENCE: Duration = Duration::from_secs(10);

    #[derive(Clone)]
    struct Synthetic(Arc<Mutex<Instant>>);

    impl Synthetic {
        fn new() -> Self {
            Self(Arc::new(Mutex::new(Instant::now())))
        }

        fn advance(&self, by: Duration) {
            *self.0.lock() += by;
        }
    }

    impl Clock for Synthetic {
        fn now(&self) -> Instant {
            *self.0.lock()
        }

        fn sleep_until(&self, deadline: Instant) {
            let mut now = self.0.lock();
            *now = (*now).max(deadline);
        }
    }

    fn pace_on(clock: &Synthetic, lanes: Vec<LaneFn>) -> Sender<Assignment> {
        let lanes = lanes
            .into_iter()
            .map(|lane| Assignment { lane, after: None });
        pace_loaded(clock, lanes.collect(), Arc::default())
    }

    fn pace_loaded(
        clock: &Synthetic,
        assignments: Vec<Assignment>,
        load: Arc<Load>,
    ) -> Sender<Assignment> {
        let (sender, receiver) = mpsc::channel();
        for assignment in assignments {
            sender.send(assignment).unwrap();
        }
        let clock = clock.clone();
        let origin = clock.now();
        thread::spawn(move || pace(&receiver, &load, &clock, origin));
        sender
    }

    fn recorder() -> (LaneFn, Receiver<(Tick, Instant)>) {
        let (sender, receiver) = mpsc::channel();
        let lane: LaneFn = Box::new(move |tick| sender.send((tick, Instant::now())).is_ok());
        (lane, receiver)
    }

    fn synthetic_recorder(
        clock: &Synthetic,
        on_tick: usize,
        work: Duration,
    ) -> (LaneFn, Receiver<(Tick, Instant)>) {
        let (sender, receiver) = mpsc::sync_channel(0);
        let clock = clock.clone();
        let mut count = 0;
        let lane: LaneFn = Box::new(move |tick| {
            let alive = sender.send((tick, clock.now())).is_ok();
            count += 1;
            if count == on_tick {
                clock.advance(work);
            }
            alive
        });
        (lane, receiver)
    }

    fn take(receiver: &Receiver<(Tick, Instant)>, count: usize) -> Vec<(Tick, Instant)> {
        (0..count)
            .map(|_| receiver.recv_timeout(PATIENCE).unwrap())
            .collect()
    }

    fn wait_until(mut condition: impl FnMut() -> bool) {
        let deadline = Instant::now() + PATIENCE;
        while !condition() {
            assert!(Instant::now() < deadline, "timed out");
            thread::sleep(Duration::from_millis(5));
        }
    }

    #[test]
    fn ticks_keep_absolute_deadlines_when_lanes_take_time() {
        let clock = Synthetic::new();
        let busy: LaneFn = {
            let clock = clock.clone();
            let mut calls = 0;
            Box::new(move |_| {
                calls += 1;
                clock.advance(Duration::from_millis(5));
                calls < 11
            })
        };
        let (lane, ticks) = synthetic_recorder(&clock, usize::MAX, Duration::ZERO);
        let _pacer = pace_on(&clock, vec![busy, lane]);

        let ticks = take(&ticks, 11);

        assert_eq!(ticks[10].1 - ticks[0].1, FRAME * 10);
        assert!(ticks.iter().all(|(tick, _)| tick.skipped == 0));
    }

    #[test]
    fn a_long_tick_reanchors_once_on_the_grid_with_the_missed_frames_and_no_burst() {
        let clock = Synthetic::new();
        let (lane, ticks) = synthetic_recorder(&clock, 3, Duration::from_millis(110));
        let _pacer = pace_on(&clock, vec![lane]);

        let ticks = take(&ticks, 7);

        let skipped: Vec<u32> = ticks.iter().map(|(tick, _)| tick.skipped).collect();
        assert_eq!(skipped, [0, 0, 0, 4, 0, 0, 0]);
        assert_eq!(ticks[3].1 - ticks[0].1, Duration::from_millis(150));
        assert_eq!(ticks[4].1 - ticks[0].1, FRAME * 8);
        for pair in ticks[4..].windows(2) {
            assert_eq!(pair[1].1 - pair[0].1, FRAME);
        }
    }

    #[test]
    fn two_late_frames_catch_up_at_once_without_skipping() {
        let clock = Synthetic::new();
        let (lane, ticks) = synthetic_recorder(&clock, 3, Duration::from_millis(50));
        let _pacer = pace_on(&clock, vec![lane]);

        let ticks = take(&ticks, 7);

        assert!(ticks.iter().all(|(tick, _)| tick.skipped == 0));
        assert_eq!(ticks[4].1, ticks[3].1);
        assert_eq!(ticks[5].1 - ticks[2].1, FRAME * 3);
    }

    #[test]
    fn a_lane_past_the_thread_capacity_spawns_a_second_thread() {
        let pool = PacerPool::new();
        let mut receivers = Vec::new();

        let threads: Vec<usize> = (0..=LANES_PER_THREAD)
            .map(|_| {
                let (lane, receiver) = recorder();
                let thread = pool.assign(lane).unwrap();
                take(&receiver, 1);
                receivers.push(receiver);
                thread
            })
            .collect();

        assert_eq!(threads, [vec![0; LANES_PER_THREAD], vec![1]].concat());
        assert_eq!(pool.threads().len(), 2);
        assert_eq!(pool.lanes(), LANES_PER_THREAD + 1);
    }

    #[test]
    fn a_lane_that_returns_false_leaves() {
        let pool = PacerPool::new();
        pool.assign(Box::new(|_| false)).unwrap();

        wait_until(|| pool.lanes() == 0);
    }

    #[test]
    fn a_panicking_lane_is_dropped_and_counted_while_its_neighbours_keep_ticking() {
        struct Dropped(Arc<AtomicBool>);
        impl Drop for Dropped {
            fn drop(&mut self) {
                self.0.store(true, Ordering::Relaxed);
            }
        }
        let pool = PacerPool::new();
        let (neighbour, ticks) = recorder();
        let dropped = Arc::new(AtomicBool::new(false));
        let guard = Dropped(Arc::clone(&dropped));
        let mut calls = 0;
        let panicking: LaneFn = Box::new(move |_| {
            let _guard = &guard;
            calls += 1;
            assert!(calls < 2, "lane failure");
            true
        });
        assert_eq!(pool.assign(neighbour).unwrap(), 0);
        assert_eq!(pool.assign(panicking).unwrap(), 0);

        wait_until(|| pool.threads()[0].lane_panics == 1);
        take(&ticks, 5);

        assert!(dropped.load(Ordering::Relaxed));
        assert_eq!(pool.lanes(), 1);
    }

    #[test]
    fn an_idle_thread_blocks_instead_of_ticking() {
        let pool = PacerPool::new();
        pool.assign(Box::new(|_| {
            thread::sleep(Duration::from_millis(2));
            false
        }))
        .unwrap();
        wait_until(|| pool.lanes() == 0);
        let before = pool.threads()[0];

        thread::sleep(FRAME * 3);

        assert!(before.work_ewma > Duration::ZERO);
        assert_eq!(pool.threads()[0], before);
    }

    #[test]
    fn a_new_lane_on_an_idle_thread_waits_for_the_threads_next_tick() {
        let clock = Synthetic::new();
        let origin = clock.now();
        let (ran, on_ran) = mpsc::channel();
        let leaving: LaneFn = Box::new(move |_| {
            ran.send(()).unwrap();
            false
        });
        let pacer = pace_on(&clock, vec![leaving]);
        on_ran.recv_timeout(PATIENCE).unwrap();
        clock.advance(Duration::from_millis(5));
        let (lane, ticks) = synthetic_recorder(&clock, usize::MAX, Duration::ZERO);

        pacer.send(Assignment { lane, after: None }).unwrap();
        let (tick, _) = take(&ticks, 1)[0];

        assert_eq!(
            tick,
            Tick {
                now: origin + FRAME,
                skipped: 0
            }
        );
    }

    #[test]
    fn threads_tick_at_offsets_spread_across_the_frame() {
        let offsets: Vec<Duration> = (0..8).map(phase).collect();

        assert_eq!(
            offsets,
            [0, 10_000, 5_000, 15_000, 2_500, 12_500, 7_500, 17_500].map(Duration::from_micros)
        );
    }

    #[test]
    fn dropping_the_pool_ends_its_idle_threads() {
        let pool = PacerPool::new();
        for _ in 0..=LANES_PER_THREAD {
            pool.assign(Box::new(|_| false)).unwrap();
        }
        wait_until(|| pool.lanes() == 0);
        let Mode::Threads(workers) = &pool.mode else {
            unreachable!()
        };
        let loads: Vec<_> = workers
            .lock()
            .iter()
            .map(|worker| Arc::downgrade(&worker.load))
            .collect();

        drop(pool);

        wait_until(|| loads.iter().all(|load| load.strong_count() == 0));
    }

    #[test]
    fn a_thread_with_lanes_outlives_the_pool_until_its_lanes_leave() {
        let pool = PacerPool::new();
        let (lane, ticks) = recorder();
        pool.assign(lane).unwrap();

        drop(pool);
        take(&ticks, 3);
        drop(ticks);
    }

    #[test]
    fn the_manual_pool_calls_each_lane_once_per_tick_on_a_synthetic_clock() {
        let pool = PacerPool::manual();
        let (first, first_ticks) = recorder();
        let (second, second_ticks) = recorder();
        let mut calls = 0;
        let leaving: LaneFn = Box::new(move |_| {
            calls += 1;
            calls < 2
        });
        let panicking: LaneFn = Box::new(|_| panic!("lane failure"));
        for lane in [first, second, leaving, panicking] {
            assert_eq!(pool.assign(lane).unwrap(), 0);
        }

        pool.tick(5);
        pool.tick_skipping(3);

        let first: Vec<Tick> = first_ticks.try_iter().map(|(tick, _)| tick).collect();
        assert_eq!(first.len(), 6);
        assert_eq!(second_ticks.try_iter().count(), 6);
        for pair in first[..5].windows(2) {
            assert_eq!(pair[1].now - pair[0].now, FRAME);
        }
        assert_eq!(first[5].now - first[4].now, FRAME * 4);
        assert_eq!(first[5].skipped, 3);
        let load = pool.threads()[0];
        assert_eq!((load.lanes, load.lane_panics, load.late_ticks), (2, 1, 3));
    }

    #[derive(Debug, PartialEq, Eq)]
    enum Call {
        Lane(usize),
        After(usize),
    }

    #[test]
    fn after_closures_run_once_per_tick_after_every_lane() {
        let clock = Synthetic::new();
        let origin = clock.now();
        let (sender, calls) = mpsc::sync_channel(0);
        let assignments = (0..3)
            .map(|index| {
                let (lane_calls, after_calls) = (sender.clone(), sender.clone());
                let clock = clock.clone();
                let mut count = 0;
                let lane: LaneFn = Box::new(move |tick| {
                    lane_calls.send((Call::Lane(index), tick.now, None)).is_ok()
                });
                let after: AfterFn = Box::new(move |tick, deadline| {
                    count += 1;
                    assert!(index != 1 || count < 2, "receive failure");
                    if index == 0 {
                        let work = if count == 3 { 12_000 } else { 500 };
                        clock.advance(Duration::from_micros(work));
                    }
                    after_calls
                        .send((Call::After(index), tick.now, Some(deadline)))
                        .is_ok()
                });
                Assignment {
                    lane,
                    after: Some(after),
                }
            })
            .collect();
        let load = Arc::new(Load::default());
        let _pacer = pace_loaded(&clock, assignments, Arc::clone(&load));

        let calls: Vec<_> = (0..22)
            .map(|_| calls.recv_timeout(PATIENCE).unwrap())
            .collect();

        let mut expected = Vec::new();
        for (tick, afters) in (0..).zip([vec![0, 1, 2], vec![2, 0], vec![0, 2], vec![2, 0]]) {
            let now = origin + FRAME * tick;
            let deadline = now + FRAME - AFTER_MARGIN;
            expected.extend((0..3).map(|lane| (Call::Lane(lane), now, None)));
            expected.extend(
                afters
                    .into_iter()
                    .map(|after| (Call::After(after), now, Some(deadline))),
            );
        }
        expected.push((Call::Lane(0), origin + FRAME * 4, None));
        assert_eq!(calls, expected);
        let load = load.snapshot();
        assert_eq!(load.lane_panics, 1);
        assert_eq!(load.work_max, Duration::ZERO);
        assert_eq!(
            (load.after_p50, load.after_p99, load.after_max),
            (
                Duration::from_micros(512),
                Duration::from_millis(12),
                Duration::from_millis(12)
            )
        );
    }

    #[test]
    fn an_after_closure_keeps_its_thread_ticking_after_its_lane_left() {
        let clock = Synthetic::new();
        let origin = clock.now();
        let (sender, ticks) = mpsc::sync_channel(0);
        let assignment = Assignment {
            lane: Box::new(|_| false),
            after: Some(Box::new(move |tick, _| sender.send(tick.now).is_ok())),
        };
        let _pacer = pace_loaded(&clock, vec![assignment], Arc::default());

        let ticks: Vec<Instant> = (0..3)
            .map(|_| ticks.recv_timeout(PATIENCE).unwrap())
            .collect();

        assert_eq!(ticks, [0, 1, 2].map(|tick| origin + FRAME * tick));
    }

    #[test]
    fn the_clock_ticks_100_times_in_2_seconds_with_a_small_p99_lateness() {
        let pool = PacerPool::new();
        let (lane, ticks) = recorder();
        pool.assign(lane).unwrap();
        let (_, start) = ticks.recv_timeout(PATIENCE).unwrap();
        let end = start + Duration::from_secs(2);

        let mut lateness = Vec::new();
        let (mut count, mut frames, mut anchor, mut since) = (0_u32, 0_u32, start, 0_u32);
        loop {
            match ticks.recv_timeout(PATIENCE) {
                Ok((tick, at)) if at < end => {
                    count += 1;
                    frames += 1 + tick.skipped;
                    (anchor, since) = if tick.skipped > 0 {
                        (at, 0)
                    } else {
                        (anchor, since + 1)
                    };
                    lateness.push(at.saturating_duration_since(anchor + FRAME * since));
                }
                Ok(_) => break,
                Err(RecvTimeoutError::Timeout | RecvTimeoutError::Disconnected) => {
                    panic!("the clock stopped")
                }
            }
        }
        lateness.sort();
        let quantile = |q: usize| lateness[lateness.len() * q / 100 - 1];

        if crate::strict_timing() {
            assert!((98..=102).contains(&count), "{count} ticks");
            let p99 = quantile(99);
            assert!(p99 <= Duration::from_millis(5), "p99 lateness {p99:?}");
        } else {
            assert!((90..=102).contains(&frames), "{frames} frames");
            let median = quantile(50);
            assert!(median <= FRAME / 2, "median lateness {median:?}");
        }
    }
}
