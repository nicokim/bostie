#![deny(clippy::arithmetic_side_effects)]

use std::mem;

const PEAK_TICKS: u32 = 24;

#[derive(Debug, Default)]
pub(super) struct Peak {
    current: usize,
    previous: usize,
    ticks: u32,
}

impl Peak {
    pub(super) fn observe(&mut self, used: usize) {
        self.current = self.current.max(used);
    }

    pub(super) fn advance(&mut self) {
        self.ticks = self.ticks.saturating_add(1);
        if self.ticks >= PEAK_TICKS {
            self.previous = mem::take(&mut self.current);
            self.ticks = 0;
        }
    }

    pub(super) fn shrink(&self, capacity: usize, shrink_to: impl FnOnce(usize)) {
        let largest = self.current.max(self.previous);
        if capacity > largest.saturating_mul(4) {
            shrink_to(largest.saturating_mul(2));
        }
    }
}
