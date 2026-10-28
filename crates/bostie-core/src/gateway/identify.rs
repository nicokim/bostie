use std::time::Duration;

use parking_lot::Mutex;
use tokio::time::Instant;

const BUDGET_PERIOD: Duration = Duration::from_secs(24 * 60 * 60);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct IdentifyLimit {
    pub remaining: u32,
    pub total: u32,
    pub reset_after: Duration,
}

pub(crate) struct IdentifyGate {
    state: Mutex<GateState>,
    spacing: Duration,
}

#[derive(Default)]
struct GateState {
    budget: Option<Budget>,
    next_slot: Option<Instant>,
}

struct Budget {
    remaining: u32,
    total: u32,
    resets_at: Instant,
}

impl Budget {
    fn refill_if_due(&mut self, now: Instant) {
        if now >= self.resets_at {
            self.remaining = self.total;
            self.resets_at = now + BUDGET_PERIOD;
        }
    }
}

impl IdentifyGate {
    pub(crate) fn new(spacing: Duration) -> Self {
        Self {
            state: Mutex::default(),
            spacing,
        }
    }

    pub(crate) fn seed(&self, limit: IdentifyLimit, now: Instant) {
        self.state.lock().budget = Some(Budget {
            remaining: limit.remaining,
            total: limit.total,
            resets_at: now
                .checked_add(limit.reset_after)
                .unwrap_or(now + BUDGET_PERIOD),
        });
    }

    pub(crate) fn exhausted_until(&self, now: Instant) -> Option<Instant> {
        let mut state = self.state.lock();
        let budget = state.budget.as_mut()?;
        budget.refill_if_due(now);
        (budget.remaining == 0).then_some(budget.resets_at)
    }

    pub(crate) fn reserve(&self, now: Instant) -> Option<Instant> {
        let mut state = self.state.lock();
        if let Some(budget) = state.budget.as_mut() {
            budget.refill_if_due(now);
            budget.remaining = budget.remaining.checked_sub(1)?;
        }
        let slot = state.next_slot.map_or(now, |next| next.max(now));
        state.next_slot = Some(slot + self.spacing);
        Some(slot)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SPACING: Duration = Duration::from_secs(5);

    fn seeded(remaining: u32, reset_after: Duration, now: Instant) -> IdentifyGate {
        let gate = IdentifyGate::new(SPACING);
        gate.seed(
            IdentifyLimit {
                remaining,
                total: 1000,
                reset_after,
            },
            now,
        );
        gate
    }

    #[test]
    fn identifies_are_spaced() {
        let gate = IdentifyGate::new(SPACING);
        let start = Instant::now();

        assert_eq!(gate.reserve(start), Some(start));
        assert_eq!(gate.reserve(start), Some(start + SPACING));
        let later = start + SPACING * 10;
        assert_eq!(gate.reserve(later), Some(later));
    }

    #[test]
    fn an_unseeded_gate_never_runs_out() {
        let gate = IdentifyGate::new(SPACING);
        let now = Instant::now();

        for _ in 0..2000 {
            assert!(gate.reserve(now).is_some());
        }

        assert_eq!(gate.exhausted_until(now), None);
    }

    #[test]
    fn an_empty_budget_waits_for_the_reset_and_refills() {
        let start = Instant::now();
        let reset = Duration::from_secs(30);
        let gate = seeded(1, reset, start);

        assert_eq!(gate.exhausted_until(start), None);
        assert_eq!(gate.reserve(start), Some(start));
        assert_eq!(gate.reserve(start), None);
        assert_eq!(gate.exhausted_until(start), Some(start + reset));

        let after_reset = start + reset;
        assert_eq!(gate.exhausted_until(after_reset), None);
        for _ in 0..1000 {
            assert!(gate.reserve(after_reset).is_some());
        }
        assert_eq!(gate.reserve(after_reset), None);
        assert_eq!(
            gate.exhausted_until(after_reset),
            Some(after_reset + BUDGET_PERIOD)
        );
    }
}
