use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

const UNKNOWN: u64 = u64::MAX;

pub(crate) struct Latency(AtomicU64);

impl Latency {
    pub(crate) fn get(&self) -> Option<Duration> {
        match self.0.load(Ordering::Relaxed) {
            UNKNOWN => None,
            nanos => Some(Duration::from_nanos(nanos)),
        }
    }

    pub(crate) fn set(&self, latency: Duration) {
        let nanos = u64::try_from(latency.as_nanos()).unwrap_or(UNKNOWN);
        self.0.store(nanos.min(UNKNOWN - 1), Ordering::Relaxed);
    }
}

impl Default for Latency {
    fn default() -> Self {
        Self(AtomicU64::new(UNKNOWN))
    }
}
