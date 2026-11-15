use std::io;
use std::mem;
use std::panic::{self, AssertUnwindSafe};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Weak};
use std::thread;
use std::time::{Duration, Instant};

use parking_lot::Mutex;

use crate::error::CoreError;
use crate::voice::types::FRAME;
use crate::voice::{nanos, panic_message};

pub(crate) const AFTER_MARGIN: Duration = Duration::from_millis(2);

const LANES_PER_THREAD: usize = 4;
const LATE_REANCHOR: Duration = FRAME.saturating_mul(3);
const WORK_SMOOTHING: u64 = 8;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct Tick {
    pub now: Instant,
    pub skipped: u32,
}

pub(crate) type LaneFn = Box<dyn FnMut(Tick) -> bool + Send>;
pub(crate) type AfterFn = Box<dyn FnMut(Tick, Instant) -> bool + Send>;

pub(crate) trait Pace: Send + Sync {
    fn assign_pair(&self, lane: LaneFn, after: AfterFn) -> Result<(), CoreError>;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct PacerTuning {
    pub(crate) rebalance_above: Duration,
    pub(crate) rebalance_after: Duration,
}

impl Default for PacerTuning {
    fn default() -> Self {
        Self {
            rebalance_above: Duration::from_millis(3),
            rebalance_after: Duration::from_secs(1),
        }
    }
}

#[derive(Clone, Default)]
struct Seat(Arc<()>);

impl Seat {
    fn is(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.0, &other.0)
    }
}

pub(crate) struct PacerPool {
    workers: Arc<Mutex<Vec<Worker>>>,
    placement: Placement,
}

#[derive(Debug, Clone, Copy)]
struct Placement {
    epoch: Instant,
    tuning: PacerTuning,
}

struct Worker {
    sender: Sender<Assignment>,
    load: Arc<Load>,
}

struct Home {
    index: usize,
    load: Arc<Load>,
    workers: Weak<Mutex<Vec<Worker>>>,
    placement: Placement,
}

struct Assignment {
    seat: Seat,
    lane: LaneFn,
    after: AfterFn,
    cost: u64,
}

struct After {
    seat: Seat,
    call: AfterFn,
    cost: u64,
}

#[derive(Default)]
struct Strain {
    since: Option<Instant>,
}

#[derive(Default)]
struct Load {
    lanes: AtomicUsize,
    after_ewma: AtomicU64,
}

impl PacerPool {
    pub(crate) fn new() -> Self {
        Self::tuned(PacerTuning::default())
    }

    fn tuned(tuning: PacerTuning) -> Self {
        Self {
            workers: Arc::new(Mutex::new(Vec::new())),
            placement: Placement {
                epoch: Instant::now(),
                tuning,
            },
        }
    }

    fn place(&self, assignment: Assignment) -> Result<(), CoreError> {
        let mut workers = self.workers.lock();
        let index = self.placement.place(&workers);
        deliver(
            &self.workers,
            &mut workers,
            index,
            assignment,
            self.placement,
        )
        .map_err(|(error, _)| error)
    }
}

impl Pace for PacerPool {
    fn assign_pair(&self, lane: LaneFn, after: AfterFn) -> Result<(), CoreError> {
        self.place(Assignment::new(lane, after))
    }
}

impl Placement {
    fn origin(&self, index: usize) -> Instant {
        self.epoch + phase(index)
    }

    fn weight(&self, load: &Load) -> u128 {
        let per_lane = self.tuning.rebalance_above.as_nanos() / LANES_PER_THREAD as u128;
        per_lane * load.lanes() as u128 + load.receive().as_nanos()
    }

    fn has_room(&self, load: &Load, cost: Duration) -> bool {
        load.lanes() < LANES_PER_THREAD
            && load.receive().saturating_add(cost) < self.tuning.rebalance_above
    }

    fn lightest<'a>(&self, workers: impl Iterator<Item = (usize, &'a Worker)>) -> Option<usize> {
        workers
            .min_by_key(|(_, worker)| self.weight(&worker.load))
            .map(|(index, _)| index)
    }

    fn place(&self, workers: &[Worker]) -> usize {
        self.lightest(
            workers
                .iter()
                .enumerate()
                .filter(|(_, worker)| self.has_room(&worker.load, Duration::ZERO)),
        )
        .unwrap_or(workers.len())
    }

    fn relocate(
        &self,
        workers: &[Worker],
        from: usize,
        cost: Duration,
        now: Instant,
        due: Instant,
    ) -> Option<usize> {
        let in_time = |index| grid_floor(self.origin(index), now) + FRAME <= due;
        self.lightest(workers.iter().enumerate().filter(|&(index, worker)| {
            index != from && self.has_room(&worker.load, cost) && in_time(index)
        }))
        .or_else(|| in_time(workers.len()).then_some(workers.len()))
    }
}

fn deliver(
    pool: &Arc<Mutex<Vec<Worker>>>,
    workers: &mut Vec<Worker>,
    index: usize,
    assignment: Assignment,
    placement: Placement,
) -> Result<(), (CoreError, Assignment)> {
    if index == workers.len() {
        match Worker::spawn(index, pool, placement) {
            Ok(worker) => workers.push(worker),
            Err(error) => return Err((error, assignment)),
        }
    }
    let Some(worker) = workers.get_mut(index) else {
        let lost = io::Error::other("the voice pacer lost a thread");
        return Err((CoreError::Runtime(lost), assignment));
    };
    worker.load.lanes.fetch_add(1, Ordering::Relaxed);
    let Err(mpsc::SendError(assignment)) = worker.sender.send(assignment) else {
        return Ok(());
    };
    match Worker::spawn(index, pool, placement) {
        Ok(fresh) => *worker = fresh,
        Err(error) => return Err((error, assignment)),
    }
    worker.load.lanes.fetch_add(1, Ordering::Relaxed);
    worker
        .sender
        .send(assignment)
        .map_err(|mpsc::SendError(assignment)| {
            let ended = io::Error::other("the voice pacer thread ended at once");
            (CoreError::Runtime(ended), assignment)
        })
}

impl Worker {
    fn spawn(
        index: usize,
        pool: &Arc<Mutex<Vec<Self>>>,
        placement: Placement,
    ) -> Result<Self, CoreError> {
        let (sender, receiver) = mpsc::channel();
        let load = Arc::new(Load::default());
        let home = Home {
            index,
            load: Arc::clone(&load),
            workers: Arc::downgrade(pool),
            placement,
        };
        thread::Builder::new()
            .name(format!("bostie-voice-{index}"))
            .spawn(move || pace(&receiver, &home, &Wall))
            .map_err(CoreError::Runtime)?;
        Ok(Self { sender, load })
    }
}

impl Home {
    fn rebalance(
        &self,
        lanes: &mut Vec<(Seat, LaneFn)>,
        afters: &mut Vec<After>,
        now: Instant,
        due: Instant,
    ) {
        if afters.len() < 2 {
            return;
        }
        let Some(pool) = self.workers.upgrade() else {
            return;
        };
        let Some((at, cost)) = afters
            .iter()
            .map(|after| after.cost)
            .enumerate()
            .max_by_key(|&(_, cost)| cost)
        else {
            return;
        };
        let mut workers = pool.lock();
        let Some(index) =
            self.placement
                .relocate(&workers, self.index, Duration::from_nanos(cost), now, due)
        else {
            return;
        };
        let Some(assignment) = Assignment::leave(lanes, afters, at) else {
            return;
        };
        match deliver(&pool, &mut workers, index, assignment, self.placement) {
            Ok(()) => {
                self.load.lanes.fetch_sub(1, Ordering::Relaxed);
                let receive = self.load.after_ewma.load(Ordering::Relaxed);
                self.load
                    .after_ewma
                    .store(receive.saturating_sub(cost), Ordering::Relaxed);
                if let Some(worker) = workers.get(index) {
                    worker.load.after_ewma.fetch_add(cost, Ordering::Relaxed);
                }
                tracing::debug!(
                    from = self.index,
                    to = index,
                    "a voice connection moved to a lighter pacer thread"
                );
            }
            Err((error, assignment)) => {
                tracing::warn!(%error, "a voice connection could not move to a lighter pacer thread");
                assignment.join(lanes, afters);
            }
        }
    }
}

impl Assignment {
    fn new(lane: LaneFn, after: AfterFn) -> Self {
        Self {
            seat: Seat::default(),
            lane,
            after,
            cost: 0,
        }
    }

    fn join(self, lanes: &mut Vec<(Seat, LaneFn)>, afters: &mut Vec<After>) {
        afters.push(After {
            seat: self.seat.clone(),
            call: self.after,
            cost: self.cost,
        });
        lanes.push((self.seat, self.lane));
    }

    fn leave(lanes: &mut Vec<(Seat, LaneFn)>, afters: &mut Vec<After>, at: usize) -> Option<Self> {
        let seat = &afters.get(at)?.seat;
        let lane_at = lanes.iter().position(|(other, _)| other.is(seat))?;
        let (seat, lane) = lanes.remove(lane_at);
        let After { call, cost, .. } = afters.remove(at);
        Some(Self {
            seat,
            lane,
            after: call,
            cost,
        })
    }
}

impl Strain {
    fn sustained(&mut self, receive: Duration, now: Instant, tuning: &PacerTuning) -> bool {
        if receive < tuning.rebalance_above {
            self.since = None;
            return false;
        }
        let since = *self.since.get_or_insert(now);
        if now.saturating_duration_since(since) < tuning.rebalance_after {
            return false;
        }
        self.since = None;
        true
    }
}

impl Load {
    fn lanes(&self) -> usize {
        self.lanes.load(Ordering::Relaxed)
    }

    fn receive(&self) -> Duration {
        Duration::from_nanos(self.after_ewma.load(Ordering::Relaxed))
    }

    fn record_after(&self, after: Duration) {
        let average = ewma(self.after_ewma.load(Ordering::Relaxed), after);
        self.after_ewma.store(average, Ordering::Relaxed);
    }
}

fn ewma(average: u64, took: Duration) -> u64 {
    average - average / WORK_SMOOTHING + nanos(took) / WORK_SMOOTHING
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

fn pace(receiver: &Receiver<Assignment>, home: &Home, clock: &impl Clock) {
    let load = &*home.load;
    let origin = home.placement.origin(home.index);
    let mut lanes: Vec<(Seat, LaneFn)> = Vec::new();
    let mut afters: Vec<After> = Vec::new();
    let mut strain = Strain::default();
    let mut shed = false;
    let mut next = clock.now();
    loop {
        if lanes.is_empty() && afters.is_empty() {
            let Ok(assignment) = receiver.recv() else {
                return;
            };
            assignment.join(&mut lanes, &mut afters);
            next = grid_ceil(origin, clock.now());
        }
        clock.sleep_until(next);
        for assignment in receiver.try_iter() {
            assignment.join(&mut lanes, &mut afters);
        }
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
        let tick = Tick { now, skipped };
        let before = lanes.len();
        run_lanes(&mut lanes, tick);
        let sent = clock.now();
        load.lanes
            .fetch_sub(before - lanes.len(), Ordering::Relaxed);
        if mem::take(&mut shed) {
            home.rebalance(&mut lanes, &mut afters, sent, next + FRAME);
        }
        if !afters.is_empty() {
            let deadline = next + FRAME.saturating_sub(AFTER_MARGIN);
            let done = run_afters(&mut afters, tick, deadline, clock, sent);
            load.record_after(done.saturating_duration_since(sent));
            shed = strain.sustained(load.receive(), done, &home.placement.tuning);
            if afters.is_empty() {
                load.after_ewma.store(0, Ordering::Relaxed);
            }
        }
        next += FRAME;
    }
}

fn run_lanes(lanes: &mut Vec<(Seat, LaneFn)>, tick: Tick) {
    lanes.retain_mut(|(_, lane)| contained("a voice lane", || lane(tick)));
}

fn run_afters(
    afters: &mut Vec<After>,
    tick: Tick,
    deadline: Instant,
    clock: &impl Clock,
    start: Instant,
) -> Instant {
    let mut mark = start;
    afters.retain_mut(|after| {
        let alive = contained("a voice receive phase", || (after.call)(tick, deadline));
        let end = clock.now();
        after.cost = ewma(after.cost, end.saturating_duration_since(mark));
        mark = end;
        alive
    });
    if !afters.is_empty() {
        afters.rotate_left(1);
    }
    mark
}

fn contained(what: &str, call: impl FnOnce() -> bool) -> bool {
    panic::catch_unwind(AssertUnwindSafe(call)).unwrap_or_else(|payload| {
        tracing::error!(
            panic = panic_message(payload.as_ref()),
            "{what} panicked and was dropped"
        );
        false
    })
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::AtomicBool;
    use std::sync::mpsc::{RecvTimeoutError, SyncSender};

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

    fn alone(lane: LaneFn) -> Assignment {
        Assignment::new(lane, Box::new(|_, _| false))
    }

    fn pace_on(clock: &Synthetic, lanes: Vec<LaneFn>) -> Sender<Assignment> {
        let lanes = lanes.into_iter().map(alone);
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
        let home = home_alone(clock.now(), load);
        thread::spawn(move || pace(&receiver, &home, &clock));
        sender
    }

    fn home_alone(epoch: Instant, load: Arc<Load>) -> Home {
        Home {
            index: 0,
            load,
            workers: Weak::new(),
            placement: Placement {
                epoch,
                tuning: PacerTuning::default(),
            },
        }
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

    fn assign(pool: &PacerPool, mut lane: LaneFn) -> usize {
        let (sender, placed) = mpsc::channel();
        let named: LaneFn = Box::new(move |tick| {
            drop(sender.send(thread::current().name().map(str::to_owned)));
            lane(tick)
        });
        pool.place(alone(named)).unwrap();
        let name = placed.recv_timeout(PATIENCE).unwrap().unwrap();
        name.trim_start_matches("bostie-voice-").parse().unwrap()
    }

    fn threads(pool: &PacerPool) -> Vec<usize> {
        pool.workers
            .lock()
            .iter()
            .map(|worker| worker.load.lanes())
            .collect()
    }

    fn lanes(pool: &PacerPool) -> usize {
        threads(pool).iter().sum()
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
    fn a_long_tick_reanchors_on_the_grid_without_a_burst() {
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
        let (placed, _receivers): (Vec<usize>, Vec<_>) = (0..=LANES_PER_THREAD)
            .map(|_| {
                let (lane, receiver) = recorder();
                let thread = assign(&pool, lane);
                take(&receiver, 1);
                (thread, receiver)
            })
            .unzip();

        assert_eq!(placed, [vec![0; LANES_PER_THREAD], vec![1]].concat());
        assert_eq!(threads(&pool).len(), 2);
        assert_eq!(lanes(&pool), LANES_PER_THREAD + 1);
    }

    #[test]
    fn a_lane_for_a_thread_that_died_gets_a_fresh_thread_in_its_place() {
        let pool = PacerPool::new();
        let (sender, receiver) = mpsc::channel();
        drop(receiver);
        pool.workers.lock().push(Worker {
            sender,
            load: Arc::default(),
        });
        let (lane, ticks) = recorder();

        let placed = assign(&pool, lane);
        take(&ticks, 1);

        assert_eq!(placed, 0);
        assert_eq!(threads(&pool), [1]);
    }

    #[test]
    fn a_lane_that_returns_false_leaves() {
        let pool = PacerPool::new();
        assign(&pool, Box::new(|_| false));

        wait_until(|| lanes(&pool) == 0);
    }

    #[test]
    fn a_panicking_lane_is_dropped_and_its_neighbours_keep_ticking() {
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
        assert_eq!(assign(&pool, neighbour), 0);
        assert_eq!(assign(&pool, panicking), 0);

        wait_until(|| dropped.load(Ordering::Relaxed));
        take(&ticks, 5);

        assert_eq!(lanes(&pool), 1);
    }

    #[test]
    fn an_idle_thread_blocks_instead_of_ticking() {
        struct Watched {
            clock: Synthetic,
            sleeps: SyncSender<Instant>,
        }
        impl Clock for Watched {
            fn now(&self) -> Instant {
                self.clock.now()
            }

            fn sleep_until(&self, deadline: Instant) {
                self.sleeps.send(deadline).unwrap();
                self.clock.sleep_until(deadline);
            }
        }
        let synthetic = Synthetic::new();
        let origin = synthetic.now();
        let (sender, receiver) = mpsc::channel();
        sender.send(alone(Box::new(|_| false))).unwrap();
        let (sleeps, slept) = mpsc::sync_channel(0);
        let clock = Watched {
            clock: synthetic,
            sleeps,
        };
        let home = home_alone(origin, Arc::default());
        thread::spawn(move || pace(&receiver, &home, &clock));

        let first = slept.recv_timeout(PATIENCE);
        drop(sender);

        assert_eq!(first, Ok(origin));
        assert_eq!(
            slept.recv_timeout(PATIENCE),
            Err(RecvTimeoutError::Disconnected)
        );
    }

    #[test]
    fn a_lane_handed_to_a_sleeping_thread_runs_on_the_next_tick() {
        struct Handing {
            clock: Synthetic,
            handover: Mutex<Option<(Sender<Assignment>, Assignment)>>,
        }
        impl Clock for Handing {
            fn now(&self) -> Instant {
                self.clock.now()
            }

            fn sleep_until(&self, deadline: Instant) {
                let handed = self.handover.lock().take();
                if let Some((sender, assignment)) = handed {
                    sender.send(assignment).unwrap();
                }
                self.clock.sleep_until(deadline);
            }
        }
        let synthetic = Synthetic::new();
        let origin = synthetic.now();
        let (sender, receiver) = mpsc::channel();
        let (first, _first_ticks) = recorder();
        let (second, second_ticks) = synthetic_recorder(&synthetic, usize::MAX, Duration::ZERO);
        sender.send(alone(first)).unwrap();
        let clock = Handing {
            clock: synthetic,
            handover: Mutex::new(Some((sender, alone(second)))),
        };
        let home = home_alone(origin, Arc::default());
        thread::spawn(move || pace(&receiver, &home, &clock));

        let (tick, _) = take(&second_ticks, 1)[0];

        assert_eq!(tick.now, origin);
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

        pacer.send(alone(lane)).unwrap();
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
            assign(&pool, Box::new(|_| false));
        }
        wait_until(|| lanes(&pool) == 0);
        let loads: Vec<_> = pool
            .workers
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
        assign(&pool, lane);

        drop(pool);
        take(&ticks, 3);
        drop(ticks);
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
                let mut count = 0;
                let lane: LaneFn = Box::new(move |tick| {
                    lane_calls.send((Call::Lane(index), tick.now, None)).is_ok()
                });
                let after: AfterFn = Box::new(move |tick, deadline| {
                    count += 1;
                    assert!(index != 1 || count < 2, "receive failure");
                    after_calls
                        .send((Call::After(index), tick.now, Some(deadline)))
                        .is_ok()
                });
                Assignment::new(lane, after)
            })
            .collect();
        let _pacer = pace_loaded(&clock, assignments, Arc::default());

        let calls: Vec<_> = (0..22)
            .map(|_| calls.recv_timeout(PATIENCE).unwrap())
            .collect();

        let mut expected = Vec::new();
        for (tick, afters) in (0..).zip([vec![0, 1, 2], vec![2, 0], vec![0, 2], vec![2, 0]]) {
            let now = origin + FRAME * tick;
            let deadline = now + FRAME.saturating_sub(AFTER_MARGIN);
            expected.extend((0..3).map(|lane| (Call::Lane(lane), now, None)));
            expected.extend(
                afters
                    .into_iter()
                    .map(|after| (Call::After(after), now, Some(deadline))),
            );
        }
        expected.push((Call::Lane(0), origin + FRAME * 4, None));
        assert_eq!(calls, expected);
    }

    #[test]
    fn an_after_closure_keeps_its_thread_ticking_after_its_lane_left() {
        let clock = Synthetic::new();
        let origin = clock.now();
        let (sender, ticks) = mpsc::sync_channel(0);
        let assignment = Assignment::new(
            Box::new(|_| false),
            Box::new(move |tick, _| sender.send(tick.now).is_ok()),
        );
        let _pacer = pace_loaded(&clock, vec![assignment], Arc::default());

        let ticks: Vec<Instant> = (0..3)
            .map(|_| ticks.recv_timeout(PATIENCE).unwrap())
            .collect();

        assert_eq!(ticks, [0, 1, 2].map(|tick| origin + FRAME * tick));
    }

    #[test]
    fn a_thread_whose_last_receive_phase_left_reports_no_receive_load() {
        let clock = Synthetic::new();
        let mut ticks = 0;
        let lane: LaneFn = Box::new(move |_| {
            ticks += 1;
            ticks < 4
        });
        let after: AfterFn = {
            let clock = clock.clone();
            let mut calls = 0;
            Box::new(move |_, _| {
                calls += 1;
                clock.advance(Duration::from_millis(4));
                calls < 3
            })
        };
        let load = Arc::new(Load::default());
        load.lanes.store(1, Ordering::Relaxed);
        let assignment = Assignment::new(lane, after);
        let _pacer = pace_loaded(&clock, vec![assignment], Arc::clone(&load));

        wait_until(|| load.lanes() == 0);

        assert_eq!(load.receive(), Duration::ZERO);
    }

    fn loaded(threads: &[(usize, u64)]) -> Vec<Worker> {
        threads
            .iter()
            .map(|&(lanes, receive_micros)| {
                let load = Load::default();
                load.lanes.store(lanes, Ordering::Relaxed);
                load.after_ewma
                    .store(receive_micros * 1_000, Ordering::Relaxed);
                Worker {
                    sender: mpsc::channel().0,
                    load: Arc::new(load),
                }
            })
            .collect()
    }

    #[test]
    fn a_new_lane_goes_to_the_lightest_thread_never_a_heavy_one() {
        let placement = Placement {
            epoch: Instant::now(),
            tuning: PacerTuning::default(),
        };

        assert_eq!(placement.place(&loaded(&[(2, 0), (1, 0)])), 1);
        assert_eq!(placement.place(&loaded(&[(1, 2_000), (2, 0)])), 1);
        assert_eq!(
            placement.place(&loaded(&[(0, 3_000), (LANES_PER_THREAD, 0)])),
            2
        );
    }

    #[test]
    fn a_moving_connection_goes_to_a_light_thread_that_ticks_sooner() {
        let epoch = Instant::now();
        let placement = Placement {
            epoch,
            tuning: PacerTuning::default(),
        };
        let workers = loaded(&[(4, 3_500), (1, 2_500), (1, 500), (LANES_PER_THREAD, 0)]);
        let cost = Duration::from_millis(1);
        let at = |millis: u64| epoch + Duration::from_millis(100 + millis);

        assert_eq!(
            placement.relocate(&workers, 0, cost, at(1), at(20)),
            Some(2)
        );
        assert_eq!(placement.relocate(&workers, 0, cost, at(6), at(20)), None);
        assert_eq!(
            placement.relocate(&workers[..3], 0, cost, at(6), at(20)),
            Some(3)
        );
    }

    #[test]
    fn a_thread_sheds_only_after_a_whole_heavy_window() {
        let tuning = PacerTuning::default();
        let heavy = tuning.rebalance_above;
        let start = Instant::now();
        let at = |millis| start + Duration::from_millis(millis);
        let mut strain = Strain::default();

        assert!(!strain.sustained(heavy, at(0), &tuning));
        assert!(!strain.sustained(
            heavy.saturating_sub(Duration::from_micros(1)),
            at(500),
            &tuning
        ));
        assert!(!strain.sustained(heavy, at(600), &tuning));
        assert!(!strain.sustained(heavy, at(1_599), &tuning));
        assert!(strain.sustained(heavy, at(1_600), &tuning));
        assert!(!strain.sustained(heavy, at(1_620), &tuning));
    }

    #[test]
    fn a_heavy_thread_moves_a_connection_with_its_receive_work() {
        let pool = PacerPool::tuned(PacerTuning {
            rebalance_above: Duration::from_millis(3),
            rebalance_after: FRAME * 5,
        });
        let (sender, calls) = mpsc::channel();
        let thread_name = || thread::current().name().unwrap_or_default().to_owned();
        let receive_work = phase(1) / 2 + Duration::from_millis(1);
        for connection in 0..2 {
            let (lane_calls, after_calls) = (sender.clone(), sender.clone());
            let mut sent = 0_u32;
            let lane: LaneFn = Box::new(move |_| {
                sent += 1;
                lane_calls
                    .send((connection, Some(sent), thread_name()))
                    .is_ok()
            });
            let after: AfterFn = Box::new(move |_, _| {
                thread::sleep(receive_work);
                after_calls.send((connection, None, thread_name())).is_ok()
            });
            pool.place(Assignment::new(lane, after)).unwrap();
        }

        let mut by_connection: [Vec<(Option<u32>, String)>; 2] = Default::default();
        let moved_calls = loop {
            let (connection, sent, name) = calls.recv_timeout(PATIENCE).unwrap();
            by_connection[connection].push((sent, name));
            let settled = |calls: &&Vec<(Option<u32>, String)>| {
                calls
                    .iter()
                    .filter(|(sent, name)| sent.is_some() && name == "bostie-voice-1")
                    .count()
                    >= 3
            };
            if let Some(moved) = by_connection.iter().find(settled) {
                break moved.clone();
            }
        };

        let names: Vec<&str> = moved_calls.iter().map(|(_, name)| name.as_str()).collect();
        assert!(names.is_sorted(), "{names:?}");
        assert!(
            moved_calls
                .iter()
                .any(|(sent, name)| sent.is_none() && name == "bostie-voice-1")
        );
        let sent: Vec<u32> = moved_calls.iter().filter_map(|(sent, _)| *sent).collect();
        assert!(
            sent.iter()
                .copied()
                .eq(1..=u32::try_from(sent.len()).unwrap()),
            "{sent:?}"
        );
        assert_eq!(threads(&pool), [1, 1]);
    }

    #[test]
    fn the_clock_ticks_about_100_frames_in_2_seconds_on_time_at_the_median() {
        let pool = PacerPool::new();
        let (lane, ticks) = recorder();
        assign(&pool, lane);
        let (_, start) = ticks.recv_timeout(PATIENCE).unwrap();
        let end = start + Duration::from_secs(2);

        let mut lateness = Vec::new();
        let (mut frames, mut anchor, mut since) = (0_u32, start, 0_u32);
        loop {
            match ticks.recv_timeout(PATIENCE) {
                Ok((tick, at)) if at < end => {
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
        let median = lateness[lateness.len() / 2 - 1];

        assert!((90..=102).contains(&frames), "{frames} frames");
        assert!(median <= FRAME / 2, "median lateness {median:?}");
    }
}
