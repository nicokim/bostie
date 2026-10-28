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

#[cfg(not(test))]
const LANES_PER_THREAD: usize = 4;
#[cfg(test)]
const LANES_PER_THREAD: usize = 2;
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
            .spawn(move || pace(&receiver, &thread_load))
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

fn pace(receiver: &Receiver<LaneFn>, load: &Load) {
    let mut lanes: Vec<LaneFn> = Vec::new();
    let mut next = Instant::now();
    loop {
        if lanes.is_empty() {
            let Ok(lane) = receiver.recv() else {
                return;
            };
            lanes.push(lane);
            next = Instant::now();
        }
        lanes.extend(receiver.try_iter());
        if let Some(wait) = next.checked_duration_since(Instant::now()) {
            thread::sleep(wait);
        }
        let now = Instant::now();
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
        load.record_work(now.elapsed());
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

    const PATIENCE: Duration = Duration::from_secs(2);

    fn recorder() -> (LaneFn, Receiver<(Tick, Instant)>) {
        let (sender, receiver) = mpsc::channel();
        let lane: LaneFn = Box::new(move |tick| sender.send((tick, Instant::now())).is_ok());
        (lane, receiver)
    }

    fn sleeper(on_tick: usize, sleep: Duration) -> (LaneFn, Receiver<(Tick, Instant)>) {
        let (sender, receiver) = mpsc::channel();
        let mut count = 0;
        let lane: LaneFn = Box::new(move |tick| {
            let alive = sender.send((tick, Instant::now())).is_ok();
            count += 1;
            if count == on_tick {
                thread::sleep(sleep);
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

    fn millis(from: Instant, to: Instant) -> f64 {
        to.duration_since(from).as_secs_f64() * 1_000.0
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
        let pool = PacerPool::new();
        let (lane, ticks) = sleeper(usize::MAX, Duration::ZERO);
        let mut busy = 0;
        let worker: LaneFn = Box::new(move |_| {
            busy += 1;
            thread::sleep(Duration::from_millis(5));
            busy < 1_000
        });
        pool.assign(worker).unwrap();
        pool.assign(lane).unwrap();

        let ticks = take(&ticks, 11);

        let elapsed = millis(ticks[0].1, ticks[10].1);
        assert!((195.0..=215.0).contains(&elapsed), "{elapsed} ms");
        assert!(ticks.iter().all(|(tick, _)| tick.skipped == 0));
    }

    #[test]
    fn a_long_tick_reanchors_once_with_the_missed_frames_and_no_burst() {
        let pool = PacerPool::new();
        let (lane, ticks) = sleeper(3, Duration::from_millis(100));
        pool.assign(lane).unwrap();

        let ticks = take(&ticks, 7);

        let skipped: Vec<u32> = ticks.iter().map(|(tick, _)| tick.skipped).collect();
        assert_eq!(skipped[..3], [0, 0, 0]);
        assert!((4..=5).contains(&skipped[3]), "{skipped:?}");
        assert_eq!(skipped[4..], [0, 0, 0]);
        for pair in ticks[3..].windows(2) {
            let gap = millis(pair[0].1, pair[1].1);
            assert!(gap > 15.0, "burst: {gap} ms");
        }
    }

    #[test]
    fn two_late_frames_catch_up_at_once_without_skipping() {
        let pool = PacerPool::new();
        let (lane, ticks) = sleeper(3, Duration::from_millis(50));
        pool.assign(lane).unwrap();

        let ticks = take(&ticks, 7);

        assert!(ticks.iter().all(|(tick, _)| tick.skipped == 0));
        let catch_up = millis(ticks[3].1, ticks[4].1);
        assert!(catch_up < 5.0, "{catch_up} ms");
        let back_on_time = millis(ticks[2].1, ticks[5].1);
        assert!((58.0..=66.0).contains(&back_on_time), "{back_on_time} ms");
    }

    #[test]
    fn a_lane_past_the_thread_capacity_spawns_a_second_thread() {
        let pool = PacerPool::new();
        let mut receivers = Vec::new();

        let threads: Vec<usize> = (0..3)
            .map(|_| {
                let (lane, receiver) = recorder();
                let thread = pool.assign(lane).unwrap();
                take(&receiver, 1);
                receivers.push(receiver);
                thread
            })
            .collect();

        assert_eq!(threads, [0, 0, 1]);
        assert_eq!(pool.threads().len(), 2);
        assert_eq!(pool.lanes(), 3);
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

        thread::sleep(Duration::from_millis(100));

        assert!(before.work_ewma > Duration::ZERO);
        assert_eq!(pool.threads()[0], before);
    }

    #[test]
    fn a_new_lane_on_an_idle_thread_ticks_at_once() {
        let pool = PacerPool::new();
        pool.assign(Box::new(|_| false)).unwrap();
        wait_until(|| pool.lanes() == 0);
        thread::sleep(Duration::from_millis(30));
        let (lane, ticks) = recorder();

        let assigned = Instant::now();
        pool.assign(lane).unwrap();
        let (_, first) = take(&ticks, 1)[0];

        assert!(millis(assigned, first) < 5.0);
    }

    #[test]
    fn dropping_the_pool_ends_its_idle_threads() {
        let pool = PacerPool::new();
        for _ in 0..3 {
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
        let mut count = 0_u32;
        loop {
            match ticks.recv_timeout(PATIENCE) {
                Ok((_, at)) if at < end => {
                    count += 1;
                    let ideal = start + FRAME * count;
                    lateness.push(at.saturating_duration_since(ideal));
                }
                Ok(_) => break,
                Err(RecvTimeoutError::Timeout | RecvTimeoutError::Disconnected) => {
                    panic!("the clock stopped")
                }
            }
        }
        lateness.sort();

        assert!((98..=102).contains(&count), "{count} ticks");
        let p99 = lateness[lateness.len() * 99 / 100 - 1];
        assert!(p99 <= Duration::from_millis(5), "p99 lateness {p99:?}");
    }
}
