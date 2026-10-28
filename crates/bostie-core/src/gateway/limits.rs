use std::ops::RangeInclusive;
use std::time::Duration;

#[derive(Debug, Clone)]
pub(crate) struct Limits {
    pub(super) connect_timeout: Duration,
    pub(super) hello_timeout: Duration,
    pub(super) close_timeout: Duration,
    pub(super) invalid_session_delay: RangeInclusive<Duration>,
    pub(super) backoff_base: Duration,
    pub(super) backoff_max: Duration,
    pub(super) identify_spacing: Duration,
    pub(super) max_decompressed: usize,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            connect_timeout: Duration::from_secs(10),
            hello_timeout: Duration::from_secs(20),
            close_timeout: Duration::from_secs(2),
            invalid_session_delay: Duration::from_secs(1)..=Duration::from_secs(5),
            backoff_base: Duration::from_secs(1),
            backoff_max: Duration::from_secs(60),
            identify_spacing: Duration::from_secs(5),
            max_decompressed: 128 * 1024 * 1024,
        }
    }
}
