use std::time::Instant;

use parking_lot::Mutex;

use crate::error::CoreError;
use crate::voice::pacer::{AfterFn, LaneFn, Pace, Tick};
use crate::voice::types::FRAME;

pub struct ManualPacer(Mutex<Lanes>);

struct Lanes {
    now: Instant,
    lanes: Vec<LaneFn>,
    afters: Vec<AfterFn>,
}

impl ManualPacer {
    pub fn new() -> Self {
        Self(Mutex::new(Lanes {
            now: Instant::now(),
            lanes: Vec::new(),
            afters: Vec::new(),
        }))
    }

    pub fn tick(&self, n: u32) {
        let mut pacer = self.0.lock();
        for _ in 0..n {
            pacer.now += FRAME;
            let tick = Tick {
                now: pacer.now,
                skipped: 0,
            };
            pacer.lanes.retain_mut(|lane| lane(tick));
            let deadline = Instant::now() + FRAME;
            pacer.afters.retain_mut(|after| after(tick, deadline));
        }
    }

    pub fn lanes(&self) -> usize {
        self.0.lock().lanes.len()
    }
}

impl Pace for ManualPacer {
    fn assign_pair(&self, lane: LaneFn, after: AfterFn) -> Result<(), CoreError> {
        let mut pacer = self.0.lock();
        pacer.lanes.push(lane);
        pacer.afters.push(after);
        Ok(())
    }
}
