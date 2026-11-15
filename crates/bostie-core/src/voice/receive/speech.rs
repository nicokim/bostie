#![deny(clippy::arithmetic_side_effects)]

use crate::voice::types::SILENCE_FRAME;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Edge {
    Start { ssrc: u32, at: i64 },
    Stop { ssrc: u32, at: i64, duration: i64 },
}

#[derive(Debug, Clone, Default)]
pub(crate) struct Activity {
    since: Option<(u32, i64)>,
    end: i64,
}

impl Activity {
    pub(crate) fn is_speaking(&self) -> bool {
        self.since.is_some()
    }

    pub(crate) fn packet(
        &mut self,
        ssrc: u32,
        at: i64,
        samples: u32,
        speech: bool,
    ) -> Option<Edge> {
        if !speech {
            return None;
        }
        let end = at.saturating_add(i64::from(samples));
        if self.since.is_some() {
            self.end = self.end.max(end);
            return None;
        }
        self.since = Some((ssrc, at));
        self.end = end;
        Some(Edge::Start { ssrc, at })
    }

    pub(crate) fn expire(&mut self, now: i64, hangover: i64) -> Option<Edge> {
        let (ssrc, start) = self.since?;
        if now.saturating_sub(self.end) < hangover {
            return None;
        }
        self.since = None;
        Some(Edge::Stop {
            ssrc,
            at: self.end,
            duration: self.end.saturating_sub(start),
        })
    }
}

pub(crate) fn is_speech(payload: &[u8], silence: bool) -> bool {
    !silence && payload != SILENCE_FRAME
}

#[cfg(test)]
mod tests {
    use super::*;

    const SSRC: u32 = 0x8000_0001;
    const FRAME: i64 = 960;

    #[test]
    fn speech_starts_on_a_speech_packet_and_stops_after_the_hangover() {
        let voice = [0x78, 0x01, 0x02];
        assert!(is_speech(&voice, false));
        assert!(!is_speech(&voice, true));
        assert!(!is_speech(&SILENCE_FRAME, false));

        let hangover = 10 * FRAME;

        for (speech, end) in [
            (&[0, FRAME][..], 2 * FRAME),
            (&[0, 2 * FRAME, FRAME][..], 3 * FRAME),
        ] {
            let mut activity = Activity::default();

            let mut edges: Vec<Option<Edge>> = speech
                .iter()
                .map(|&at| activity.packet(SSRC, at, 960, is_speech(&voice, false)))
                .collect();
            edges.push(activity.packet(SSRC, end, 960, is_speech(&SILENCE_FRAME, false)));
            edges.push(activity.packet(SSRC, end + FRAME, 960, is_speech(&voice, true)));
            edges.push(activity.expire(end + hangover - 1, hangover));
            assert!(activity.is_speaking());
            let stop = activity.expire(end + hangover, hangover);

            assert_eq!(edges[0], Some(Edge::Start { ssrc: SSRC, at: 0 }));
            assert!(edges[1..].iter().all(Option::is_none), "{edges:?}");
            assert_eq!(
                stop,
                Some(Edge::Stop {
                    ssrc: SSRC,
                    at: end,
                    duration: end
                })
            );
            assert!(!activity.is_speaking());
            assert_eq!(activity.expire(10 * hangover, hangover), None);
        }
    }
}
