use std::time::Duration;

#[derive(Debug, Clone, Copy)]
pub(crate) struct Limits {
    pub(super) max_retry_wait: Duration,
    pub(super) backoff_base: Duration,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            max_retry_wait: Duration::from_secs(30),
            backoff_base: Duration::from_millis(500),
        }
    }
}
