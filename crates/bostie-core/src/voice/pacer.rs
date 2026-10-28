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

const LANES_PER_THREAD: usize = 4;
const LATE_REANCHOR: Duration = FRAME.saturating_mul(3);
const WORK_SMOOTHING: u64 = 8;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Tick {
    pub now: Instant,
    pub skipped: u32,
}

pub type LaneFn = Box<dyn FnMut(Tick) -> bool + Send>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ThreadLoad {
    pub lanes: usize,
    pub work_ewma: Duration,
    pub work_max: Duration,
    pub late_ticks: u64,
    pub lane_panics: u64,
}

pub struct PacerPool {
    mode: Mode,
}

enum Mode {
    Threads(Mutex<Vec<Worker>>),
    #[cfg(test)]
    Manual(Mutex<Manual>),
}

struct Worker {
    sender: Sender<LaneFn>,
    load: Arc<Load>,
}

#[derive(Default)]
struct Load {
    lanes: AtomicUsize,
    work_ewma: AtomicU64,
    work_max: AtomicU64,
    late_ticks: AtomicU64,
    lane_panics: AtomicU64,
}

#[cfg(test)]
struct Manual {
    lanes: Vec<LaneFn>,
    now: Instant,
    load: Load,
}

impl PacerPool {
    pub fn new() -> Self {
        Self {
            mode: Mode::Threads(Mutex::new(Vec::new())),
        }
    }

    #[cfg(test)]
    pub fn manual() -> Self {
        Self {
            mode: Mode::Manual(Mutex::new(Manual {
                lanes: Vec::new(),
                now: Instant::now(),
                load: Load::default(),
            })),
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
        let Manual { lanes, now, load } = &mut *manual;
        *now += FRAME.saturating_mul(skipped.saturating_add(1));
        load.late_ticks
            .fetch_add(u64::from(skipped), Ordering::Relaxed);
        run_lanes(lanes, Tick { now: *now, skipped }, load);
        load.lanes.store(lanes.len(), Ordering::Relaxed);
    }

    pub fn assign(&self, lane: LaneFn) -> Result<usize, CoreError> {
        match &self.mode {
            Mode::Threads(workers) => assign(&mut workers.lock(), lane),
            #[cfg(test)]
            Mode::Manual(manual) => {
                let mut manual = manual.lock();
                manual.lanes.push(lane);
                manual.load.lanes.fetch_add(1, Ordering::Relaxed);
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

fn assign(workers: &mut Vec<Worker>, lane: LaneFn) -> Result<usize, CoreError> {
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
            workers.push(Worker::spawn(workers.len())?);
            workers.len() - 1
        }
    };
    let Some(worker) = workers.get_mut(index) else {
        return Err(CoreError::Runtime(io::Error::other(
            "the voice pacer lost a thread",
        )));
    };
    worker.load.lanes.fetch_add(1, Ordering::Relaxed);
    if let Err(mpsc::SendError(lane)) = worker.sender.send(lane) {
        *worker = Worker::spawn(index)?;
        worker.load.lanes.fetch_add(1, Ordering::Relaxed);
        worker.sender.send(lane).map_err(|_| {
            CoreError::Runtime(io::Error::other("the voice pacer thread ended at once"))
        })?;
    }
    Ok(index)
}

impl Worker {
    fn spawn(index: usize) -> Result<Self, CoreError> {
        let (sender, receiver) = mpsc::channel();
        let load = Arc::new(Load::default());
        let thread_load = Arc::clone(&load);
        thread::Builder::new()
            .name(format!("bostie-voice-{index}"))
            .spawn(move || pace(&receiver, &thread_load, &Wall))
            .map_err(CoreError::Runtime)?;
        Ok(Self { sender, load })
    }
}

impl Load {
    fn snapshot(&self) -> ThreadLoad {
        ThreadLoad {
            lanes: self.lanes.load(Ordering::Relaxed),
            work_ewma: Duration::from_nanos(self.work_ewma.load(Ordering::Relaxed)),
            work_max: Duration::from_nanos(self.work_max.load(Ordering::Relaxed)),
            late_ticks: self.late_ticks.load(Ordering::Relaxed),
            lane_panics: self.lane_panics.load(Ordering::Relaxed),
        }
    }

    fn record_work(&self, work: Duration) {
        let work = u64::try_from(work.as_nanos()).unwrap_or(u64::MAX);
        let ewma = self.work_ewma.load(Ordering::Relaxed);
        self.work_ewma.store(
            ewma - ewma / WORK_SMOOTHING + work / WORK_SMOOTHING,
            Ordering::Relaxed,
        );
        self.work_max.fetch_max(work, Ordering::Relaxed);
    }
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

fn pace(receiver: &Receiver<LaneFn>, load: &Load, clock: &impl Clock) {
    let mut lanes: Vec<LaneFn> = Vec::new();
    let mut next = clock.now();
    loop {
        if lanes.is_empty() {
            let Ok(lane) = receiver.recv() else {
                return;
            };
            lanes.push(lane);
            next = clock.now();
        }
        lanes.extend(receiver.try_iter());
        clock.sleep_until(next);
        let now = clock.now();
        let late = now.saturating_duration_since(next);
        let skipped = if late > LATE_REANCHOR {
            next = now;
            u32::try_from(late.as_nanos() / FRAME.as_nanos()).unwrap_or(u32::MAX)
        } else {
            0
        };
        load.late_ticks
            .fetch_add(u64::from(skipped), Ordering::Relaxed);
        let before = lanes.len();
        run_lanes(&mut lanes, Tick { now, skipped }, load);
        load.record_work(clock.now().saturating_duration_since(now));
        load.lanes
            .fetch_sub(before - lanes.len(), Ordering::Relaxed);
        next += FRAME;
    }
}

fn run_lanes(lanes: &mut Vec<LaneFn>, tick: Tick, load: &Load) {
    lanes.retain_mut(
        |lane| match panic::catch_unwind(AssertUnwindSafe(|| lane(tick))) {
            Ok(keep) => keep,
            Err(payload) => {
                load.lane_panics.fetch_add(1, Ordering::Relaxed);
                tracing::error!(
                    panic = panic_message(payload.as_ref()),
                    "a voice lane panicked and was dropped"
                );
                false
            }
        },
    );
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

    fn pace_on(clock: &Synthetic, lanes: Vec<LaneFn>) -> Sender<LaneFn> {
        let (sender, receiver) = mpsc::channel();
        for lane in lanes {
            sender.send(lane).unwrap();
        }
        let clock = clock.clone();
        thread::spawn(move || pace(&receiver, &Load::default(), &clock));
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
    fn a_long_tick_reanchors_once_with_the_missed_frames_and_no_burst() {
        let clock = Synthetic::new();
        let (lane, ticks) = synthetic_recorder(&clock, 3, Duration::from_millis(100));
        let _pacer = pace_on(&clock, vec![lane]);

        let ticks = take(&ticks, 7);

        let skipped: Vec<u32> = ticks.iter().map(|(tick, _)| tick.skipped).collect();
        assert_eq!(skipped, [0, 0, 0, 4, 0, 0, 0]);
        for pair in ticks[3..].windows(2) {
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
    fn a_new_lane_on_an_idle_thread_ticks_at_once() {
        let clock = Synthetic::new();
        let (ran, on_ran) = mpsc::channel();
        let leaving: LaneFn = Box::new(move |_| {
            ran.send(()).unwrap();
            false
        });
        let pacer = pace_on(&clock, vec![leaving]);
        on_ran.recv_timeout(PATIENCE).unwrap();
        clock.advance(Duration::from_millis(5));
        let (lane, ticks) = synthetic_recorder(&clock, usize::MAX, Duration::ZERO);

        let assigned = clock.now();
        pacer.send(lane).unwrap();
        let (tick, _) = take(&ticks, 1)[0];

        assert_eq!(
            tick,
            Tick {
                now: assigned,
                skipped: 0
            }
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
