use std::time::Duration;

pub(crate) fn backoff(base: Duration, max: Duration, attempt: u32) -> Duration {
    let factor = 1_u32.checked_shl(attempt).unwrap_or(u32::MAX);
    base.saturating_mul(factor)
        .min(max)
        .mul_f64(0.5 + fastrand::f64() / 2.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_backoff_doubles_up_to_its_cap_and_jitters_down_to_half() {
        let base = Duration::from_millis(500);
        let max = Duration::from_secs(5);
        for (attempt, full) in [(0, 500), (1, 1_000), (3, 4_000), (4, 5_000), (40, 5_000)] {
            let full = Duration::from_millis(full);
            for _ in 0..100 {
                let delay = backoff(base, max, attempt);
                assert!(full / 2 <= delay && delay <= full, "{attempt}: {delay:?}");
            }
        }
    }
}
