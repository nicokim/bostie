use std::collections::VecDeque;
use std::time::Duration;

use tokio::time::Instant;

const WINDOW: Duration = Duration::from_secs(60);
const SENDS_PER_WINDOW: usize = 120;
const MAX_HEARTBEAT_RESERVE: usize = 10;

pub(crate) struct SendLimiter {
    sent: VecDeque<Instant>,
    command_limit: usize,
}

impl SendLimiter {
    pub(crate) fn new(heartbeat_interval: Duration) -> Self {
        Self {
            sent: VecDeque::with_capacity(SENDS_PER_WINDOW),
            command_limit: SENDS_PER_WINDOW - heartbeat_reserve(heartbeat_interval),
        }
    }

    pub(crate) fn record(&mut self, now: Instant) {
        if self.sent.len() == SENDS_PER_WINDOW {
            self.sent.pop_front();
        }
        self.sent.push_back(now);
    }

    pub(crate) fn next_slot(&mut self, now: Instant) -> Instant {
        while self
            .sent
            .front()
            .is_some_and(|&sent| now.duration_since(sent) >= WINDOW)
        {
            self.sent.pop_front();
        }
        self.sent
            .len()
            .checked_sub(self.command_limit)
            .and_then(|oldest_blocking| self.sent.get(oldest_blocking))
            .map_or(now, |&sent| sent + WINDOW)
    }
}

fn heartbeat_reserve(interval: Duration) -> usize {
    let beats = WINDOW.as_millis().div_ceil(interval.as_millis().max(1));
    usize::try_from(beats.saturating_add(1)).map_or(MAX_HEARTBEAT_RESERVE, |reserve| {
        reserve.min(MAX_HEARTBEAT_RESERVE)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reserve_covers_a_minute_of_heartbeats_up_to_ten() {
        assert_eq!(heartbeat_reserve(Duration::from_millis(41_250)), 3);
        assert_eq!(heartbeat_reserve(Duration::from_millis(500)), 10);
        assert_eq!(heartbeat_reserve(Duration::from_millis(1)), 10);
    }

    #[test]
    fn commands_wait_once_the_window_is_full() {
        let mut limiter = SendLimiter::new(Duration::from_millis(41_250));
        let start = Instant::now();

        for step in 0..117 {
            let now = start + Duration::from_millis(step);
            assert_eq!(limiter.next_slot(now), now);
            limiter.record(now);
        }

        let now = start + Duration::from_secs(1);
        assert_eq!(limiter.next_slot(now), start + WINDOW);
        let freed = start + WINDOW;
        assert_eq!(limiter.next_slot(freed), freed);
    }

    #[test]
    fn heartbeats_count_against_the_window() {
        let mut limiter = SendLimiter::new(Duration::from_millis(500));
        let start = Instant::now();

        for _ in 0..105 {
            limiter.record(start);
        }
        let heartbeat = start + Duration::from_secs(10);
        for _ in 0..5 {
            limiter.record(heartbeat);
        }

        let now = start + Duration::from_secs(20);
        assert_eq!(limiter.next_slot(now), start + WINDOW);
        assert_eq!(limiter.next_slot(start + WINDOW), start + WINDOW);
    }
}
