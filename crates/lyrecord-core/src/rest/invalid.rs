use std::collections::VecDeque;
use std::time::{Duration, Instant};

const SLOT: Duration = Duration::from_secs(60);
const WINDOW: Duration = Duration::from_secs(11 * 60);
const WARN_AT: [u32; 3] = [1_000, 5_000, 9_000];
const REFUSE_AT: u32 = 9_500;
const RESUME_BELOW: u32 = 9_000;

#[derive(Default)]
pub(super) struct InvalidRequests {
    slots: VecDeque<Slot>,
    refusing: bool,
}

struct Slot {
    start: Instant,
    count: u32,
}

impl InvalidRequests {
    pub(super) fn record(&mut self, now: Instant) -> Option<u32> {
        match self.slots.back_mut() {
            Some(slot) if now.saturating_duration_since(slot.start) < SLOT => {
                slot.count = slot.count.saturating_add(1);
            }
            _ => self.slots.push_back(Slot {
                start: now,
                count: 1,
            }),
        }
        let count = self.count(now);
        self.refusing |= count >= REFUSE_AT;
        WARN_AT.contains(&count).then_some(count)
    }

    pub(super) fn refusal(&mut self, now: Instant) -> Option<Duration> {
        let mut count = self.count(now);
        self.refusing &= count >= RESUME_BELOW;
        if !self.refusing {
            return None;
        }
        self.slots.iter().find_map(|slot| {
            count = count.saturating_sub(slot.count);
            (count < RESUME_BELOW)
                .then(|| WINDOW.saturating_sub(now.saturating_duration_since(slot.start)))
        })
    }

    pub(super) fn count(&mut self, now: Instant) -> u32 {
        while self
            .slots
            .front()
            .is_some_and(|slot| now.saturating_duration_since(slot.start) >= WINDOW)
        {
            self.slots.pop_front();
        }
        self.slots
            .iter()
            .fold(0, |total, slot| total.saturating_add(slot.count))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn record(invalid: &mut InvalidRequests, times: u32, now: Instant) -> Vec<u32> {
        (0..times).filter_map(|_| invalid.record(now)).collect()
    }

    #[test]
    fn counts_the_last_ten_minutes_and_forgets_after_eleven() {
        let start = Instant::now();
        let mut invalid = InvalidRequests::default();

        record(&mut invalid, 600, start);
        record(&mut invalid, 400, start + Duration::from_secs(5 * 60));

        assert_eq!(invalid.count(start + Duration::from_secs(10 * 60)), 1_000);
        assert_eq!(invalid.count(start + Duration::from_secs(11 * 60)), 400);
        assert_eq!(invalid.count(start + Duration::from_secs(16 * 60)), 0);
    }

    #[test]
    fn warns_once_per_threshold_crossing() {
        let start = Instant::now();
        let mut invalid = InvalidRequests::default();

        assert_eq!(record(&mut invalid, 9_200, start), [1_000, 5_000, 9_000]);
        assert!(record(&mut invalid, 100, start).is_empty());

        let later = start + Duration::from_secs(11 * 60);
        assert_eq!(record(&mut invalid, 1_000, later), [1_000]);
    }

    #[test]
    fn refuses_from_the_hard_limit_until_back_under_the_resume_mark() {
        let start = Instant::now();
        let minute = |minutes: u32| start + SLOT * minutes;
        let mut invalid = InvalidRequests::default();
        record(&mut invalid, 300, minute(0));
        record(&mut invalid, 400, minute(1));
        record(&mut invalid, 8_799, minute(2));
        assert_eq!(invalid.refusal(minute(2)), None);

        invalid.record(minute(2));

        assert_eq!(invalid.refusal(minute(2)), Some(WINDOW - SLOT));
        assert_eq!(invalid.count(minute(11)), 9_200);
        assert_eq!(invalid.refusal(minute(11)), Some(SLOT));
        assert_eq!(invalid.refusal(minute(12)), None);
    }
}
