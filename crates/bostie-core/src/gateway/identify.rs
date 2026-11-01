use std::collections::HashMap;
use std::num::NonZeroU32;
use std::time::Duration;

use parking_lot::Mutex;
use tokio::time::Instant;

use crate::ids::ShardId;

const BUDGET_PERIOD: Duration = Duration::from_hours(24);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct IdentifyLimit {
    pub remaining: u32,
    pub total: u32,
    pub reset_after: Duration,
    pub max_concurrency: NonZeroU32,
}

pub(crate) struct IdentifyGate {
    state: Mutex<GateState>,
    spacing: Duration,
}

struct GateState {
    budget: Option<Budget>,
    concurrency: NonZeroU32,
    next_slots: HashMap<u32, Instant>,
}

impl Default for GateState {
    fn default() -> Self {
        Self {
            budget: None,
            concurrency: NonZeroU32::MIN,
            next_slots: HashMap::new(),
        }
    }
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
        let mut state = self.state.lock();
        state.budget = Some(Budget {
            remaining: limit.remaining,
            total: limit.total,
            resets_at: now
                .checked_add(limit.reset_after)
                .unwrap_or(now + BUDGET_PERIOD),
        });
        state.concurrency = limit.max_concurrency;
    }

    pub(crate) fn exhausted_until(&self, now: Instant) -> Option<Instant> {
        let mut state = self.state.lock();
        let budget = state.budget.as_mut()?;
        budget.refill_if_due(now);
        (budget.remaining == 0).then_some(budget.resets_at)
    }

    pub(crate) fn reserve(&self, shard: ShardId, now: Instant) -> Option<Instant> {
        let mut state = self.state.lock();
        if let Some(budget) = state.budget.as_mut() {
            budget.refill_if_due(now);
            budget.remaining = budget.remaining.checked_sub(1)?;
        }
        let key = u32::from(shard) % state.concurrency;
        let slot = state
            .next_slots
            .get(&key)
            .map_or(now, |&next| next.max(now));
        state.next_slots.insert(key, slot + self.spacing);
        Some(slot)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SPACING: Duration = Duration::from_secs(5);

    fn shard(id: u32) -> ShardId {
        ShardId::from(id)
    }

    fn seeded(
        remaining: u32,
        reset_after: Duration,
        concurrency: u32,
        now: Instant,
    ) -> IdentifyGate {
        let gate = IdentifyGate::new(SPACING);
        gate.seed(
            IdentifyLimit {
                remaining,
                total: 1000,
                reset_after,
                max_concurrency: NonZeroU32::new(concurrency).unwrap(),
            },
            now,
        );
        gate
    }

    #[test]
    fn identifies_are_spaced() {
        let gate = IdentifyGate::new(SPACING);
        let start = Instant::now();

        assert_eq!(gate.reserve(shard(0), start), Some(start));
        assert_eq!(gate.reserve(shard(1), start), Some(start + SPACING));
        let later = start + SPACING * 10;
        assert_eq!(gate.reserve(shard(2), later), Some(later));
    }

    #[test]
    fn each_concurrency_bucket_spaces_its_own_identifies() {
        let start = Instant::now();
        let gate = seeded(1000, BUDGET_PERIOD, 16, start);

        for id in 0..16 {
            assert_eq!(gate.reserve(shard(id), start), Some(start));
        }
        assert_eq!(gate.reserve(shard(16), start), Some(start + SPACING));
        assert_eq!(gate.reserve(shard(31), start), Some(start + SPACING));
        assert_eq!(gate.reserve(shard(32), start), Some(start + SPACING * 2));
    }

    #[test]
    fn an_unseeded_gate_never_runs_out() {
        let gate = IdentifyGate::new(SPACING);
        let now = Instant::now();

        for _ in 0..2000 {
            assert!(gate.reserve(shard(0), now).is_some());
        }

        assert_eq!(gate.exhausted_until(now), None);
    }

    #[test]
    fn an_empty_budget_refuses_without_a_slot_until_the_reset_refills_it() {
        let start = Instant::now();
        let reset = SPACING / 5;
        let gate = seeded(1, reset, 1, start);

        assert_eq!(gate.exhausted_until(start), None);
        assert_eq!(gate.reserve(shard(0), start), Some(start));
        for _ in 0..3 {
            assert_eq!(gate.reserve(shard(0), start), None);
        }
        assert_eq!(gate.exhausted_until(start), Some(start + reset));

        let after_reset = start + reset;
        assert_eq!(gate.exhausted_until(after_reset), None);
        assert_eq!(gate.reserve(shard(0), after_reset), Some(start + SPACING));
        for _ in 1..1000 {
            assert!(gate.reserve(shard(0), after_reset).is_some());
        }
        assert_eq!(gate.reserve(shard(0), after_reset), None);
        assert_eq!(
            gate.exhausted_until(after_reset),
            Some(after_reset + BUDGET_PERIOD)
        );
    }
}
