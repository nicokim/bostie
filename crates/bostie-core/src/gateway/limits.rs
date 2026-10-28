use std::ops::RangeInclusive;
use std::time::Duration;

#[cfg(not(test))]
pub(super) const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
#[cfg(test)]
pub(super) const CONNECT_TIMEOUT: Duration = Duration::from_secs(2);

#[cfg(not(test))]
pub(super) const HELLO_TIMEOUT: Duration = Duration::from_secs(20);
#[cfg(test)]
pub(super) const HELLO_TIMEOUT: Duration = Duration::from_secs(1);

#[cfg(not(test))]
pub(super) const CLOSE_TIMEOUT: Duration = Duration::from_secs(2);
#[cfg(test)]
pub(super) const CLOSE_TIMEOUT: Duration = Duration::from_millis(200);

#[cfg(not(test))]
pub(super) const INVALID_SESSION_DELAY: RangeInclusive<Duration> =
    Duration::from_secs(1)..=Duration::from_secs(5);
#[cfg(test)]
pub(super) const INVALID_SESSION_DELAY: RangeInclusive<Duration> =
    Duration::from_millis(10)..=Duration::from_millis(30);

#[cfg(not(test))]
pub(super) const BACKOFF_BASE: Duration = Duration::from_secs(1);
#[cfg(test)]
pub(super) const BACKOFF_BASE: Duration = Duration::from_millis(10);

#[cfg(not(test))]
pub(super) const BACKOFF_MAX: Duration = Duration::from_secs(60);
#[cfg(test)]
pub(super) const BACKOFF_MAX: Duration = Duration::from_millis(100);

#[cfg(not(test))]
pub(super) const IDENTIFY_SPACING: Duration = Duration::from_secs(5);
#[cfg(test)]
pub(super) const IDENTIFY_SPACING: Duration = Duration::from_millis(20);

#[cfg(not(test))]
pub(super) const MAX_DECOMPRESSED: usize = 128 * 1024 * 1024;
#[cfg(test)]
pub(super) const MAX_DECOMPRESSED: usize = 1024 * 1024;

#[cfg(not(test))]
pub(super) const SCRATCH_KEEP: usize = 8 * 1024 * 1024;
#[cfg(test)]
pub(super) const SCRATCH_KEEP: usize = 64 * 1024;
