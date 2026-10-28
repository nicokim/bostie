use std::time::Duration;

#[cfg(not(test))]
pub(super) const MAX_RETRY_WAIT: Duration = Duration::from_secs(30);
#[cfg(test)]
pub(super) const MAX_RETRY_WAIT: Duration = Duration::from_secs(1);

#[cfg(not(test))]
pub(super) const BACKOFF_BASE: Duration = Duration::from_millis(500);
#[cfg(test)]
pub(super) const BACKOFF_BASE: Duration = Duration::from_millis(10);
