use super::types::DisconnectReason;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CloseAction {
    Resume,
    Rejoin,
    WaitForGateway(DisconnectReason),
    Fatal(DisconnectReason),
}

const AUTHENTICATION_FAILED: u16 = 4004;

pub const CLOSE_CODES: &[(u16, CloseAction, &str)] = &[
    (
        4001,
        CloseAction::Fatal(DisconnectReason::ProtocolError),
        "unknown opcode",
    ),
    (
        4002,
        CloseAction::Fatal(DisconnectReason::ProtocolError),
        "failed to decode payload",
    ),
    (
        4003,
        CloseAction::Fatal(DisconnectReason::ProtocolError),
        "not authenticated",
    ),
    (
        AUTHENTICATION_FAILED,
        CloseAction::Rejoin,
        "authentication failed",
    ),
    (
        4005,
        CloseAction::Fatal(DisconnectReason::ProtocolError),
        "already authenticated",
    ),
    (4006, CloseAction::Rejoin, "session no longer valid"),
    (4009, CloseAction::Rejoin, "session timeout"),
    (4011, CloseAction::Rejoin, "server not found"),
    (
        4012,
        CloseAction::Fatal(DisconnectReason::ProtocolError),
        "unknown protocol",
    ),
    (
        4014,
        CloseAction::WaitForGateway(DisconnectReason::Removed),
        "disconnected",
    ),
    (4015, CloseAction::Resume, "voice server crashed"),
    (
        4016,
        CloseAction::Fatal(DisconnectReason::ProtocolError),
        "unknown encryption mode",
    ),
    (
        4017,
        CloseAction::Fatal(DisconnectReason::DaveRequired),
        "E2EE/DAVE protocol required",
    ),
    (
        4020,
        CloseAction::Fatal(DisconnectReason::ProtocolError),
        "bad request",
    ),
    (
        4021,
        CloseAction::Fatal(DisconnectReason::RateLimited),
        "disconnected: rate limited",
    ),
    (
        4022,
        CloseAction::WaitForGateway(DisconnectReason::CallTerminated),
        "disconnected: call terminated",
    ),
];

pub fn classify(code: u16) -> (CloseAction, &'static str) {
    if let Some(&(_, action, text)) = CLOSE_CODES.iter().find(|(known, ..)| *known == code) {
        return (action, text);
    }
    if (4000..=4999).contains(&code) {
        (CloseAction::Rejoin, "unknown close code")
    } else {
        (CloseAction::Resume, "connection lost")
    }
}

pub fn rejoin_limit_reason(last_code: Option<u16>) -> DisconnectReason {
    if last_code == Some(AUTHENTICATION_FAILED) {
        DisconnectReason::AuthenticationFailed
    } else {
        DisconnectReason::SessionInvalid
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const DOCUMENTED: [u16; 16] = [
        4001, 4002, 4003, 4004, 4005, 4006, 4009, 4011, 4012, 4014, 4015, 4016, 4017, 4020, 4021,
        4022,
    ];

    #[test]
    fn every_documented_code_maps_to_its_action() {
        let protocol_error = CloseAction::Fatal(DisconnectReason::ProtocolError);
        let expected = [
            (4001, protocol_error),
            (4002, protocol_error),
            (4003, protocol_error),
            (4004, CloseAction::Rejoin),
            (4005, protocol_error),
            (4006, CloseAction::Rejoin),
            (4009, CloseAction::Rejoin),
            (4011, CloseAction::Rejoin),
            (4012, protocol_error),
            (4014, CloseAction::WaitForGateway(DisconnectReason::Removed)),
            (4015, CloseAction::Resume),
            (4016, protocol_error),
            (4017, CloseAction::Fatal(DisconnectReason::DaveRequired)),
            (4020, protocol_error),
            (4021, CloseAction::Fatal(DisconnectReason::RateLimited)),
            (
                4022,
                CloseAction::WaitForGateway(DisconnectReason::CallTerminated),
            ),
        ];

        let codes: Vec<u16> = CLOSE_CODES.iter().map(|&(code, ..)| code).collect();
        assert_eq!(codes, DOCUMENTED);
        for (code, action) in expected {
            assert_eq!(classify(code).0, action, "close code {code}");
        }
    }

    #[test]
    fn undocumented_codes_from_4001_to_4022_rejoin_as_unknown() {
        for code in (4001..=4022).filter(|code| !DOCUMENTED.contains(code)) {
            assert_eq!(
                classify(code),
                (CloseAction::Rejoin, "unknown close code"),
                "close code {code}"
            );
        }
        for code in [4000, 4023, 4999] {
            assert_eq!(classify(code), (CloseAction::Rejoin, "unknown close code"));
        }
    }

    #[test]
    fn codes_outside_4xxx_are_a_lost_connection_that_resumes() {
        for code in [1000, 1001, 1005, 1006, 1011, 3000, 5000] {
            assert_eq!(classify(code), (CloseAction::Resume, "connection lost"));
        }
    }

    #[test]
    fn running_out_of_rejoins_after_4004_is_an_authentication_failure() {
        assert_eq!(
            rejoin_limit_reason(Some(4004)),
            DisconnectReason::AuthenticationFailed
        );
        for last_code in [Some(4006), Some(4009), Some(4011), Some(4999), None] {
            assert_eq!(
                rejoin_limit_reason(last_code),
                DisconnectReason::SessionInvalid
            );
        }
    }
}
