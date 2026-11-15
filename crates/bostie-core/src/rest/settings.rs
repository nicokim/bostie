use std::time::Duration;

#[derive(Debug, Clone, Copy)]
pub struct HttpSettings {
    pub max_rate_limit_wait: Duration,
    pub retries: u32,
    pub backoff_base: Duration,
    pub connect_timeout: Duration,
    pub read_timeout: Duration,
    pub rate_limiter: bool,
}

impl Default for HttpSettings {
    fn default() -> Self {
        Self {
            max_rate_limit_wait: Duration::from_secs(30),
            retries: 2,
            backoff_base: Duration::from_millis(500),
            connect_timeout: Duration::from_secs(10),
            read_timeout: Duration::from_secs(60),
            rate_limiter: true,
        }
    }
}
