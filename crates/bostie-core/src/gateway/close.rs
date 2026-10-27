#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum CloseAction {
    Resume,
    Reidentify,
    Fatal,
}

pub(crate) const CLOSE_CODES: &[(u16, CloseAction, &str)] = &[
    (4000, CloseAction::Resume, "unknown error"),
    (4001, CloseAction::Resume, "unknown opcode"),
    (4002, CloseAction::Resume, "decode error"),
    (4003, CloseAction::Reidentify, "not authenticated"),
    (4004, CloseAction::Fatal, "authentication failed"),
    (4005, CloseAction::Resume, "already authenticated"),
    (4007, CloseAction::Reidentify, "invalid seq"),
    (4008, CloseAction::Resume, "rate limited"),
    (4009, CloseAction::Reidentify, "session timed out"),
    (4010, CloseAction::Fatal, "invalid shard"),
    (4011, CloseAction::Fatal, "sharding required"),
    (4012, CloseAction::Fatal, "invalid API version"),
    (4013, CloseAction::Fatal, "invalid intents"),
    (4014, CloseAction::Fatal, "disallowed intents"),
];

pub(crate) fn classify(code: u16) -> (CloseAction, &'static str) {
    CLOSE_CODES
        .iter()
        .find(|(known, ..)| *known == code)
        .map_or(
            (CloseAction::Resume, "closed by Discord"),
            |&(_, action, text)| (action, text),
        )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn documented_codes_map_to_their_reconnect_rule() {
        let reconnect_false = [4004, 4010, 4011, 4012, 4013, 4014];
        let start_new_session = [4003, 4007, 4009];

        for code in (4000..=4014).filter(|&code| code != 4006) {
            let (action, _) = classify(code);
            let expected = if reconnect_false.contains(&code) {
                CloseAction::Fatal
            } else if start_new_session.contains(&code) {
                CloseAction::Reidentify
            } else {
                CloseAction::Resume
            };
            assert_eq!(action, expected, "close code {code}");
        }
        assert_eq!(CLOSE_CODES.len(), 14);
    }

    #[test]
    fn unknown_and_normal_codes_resume() {
        for code in [1000, 1001, 1006, 4006, 4999] {
            assert_eq!(classify(code), (CloseAction::Resume, "closed by Discord"));
        }
    }
}
