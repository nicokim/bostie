use std::borrow::Cow;
use std::fmt;
use std::net::IpAddr;
use std::sync::LazyLock;
use std::time::Duration;

use bytes::Bytes;
use sonic_rs::{JsonValueTrait, LazyValue, PointerTree, Value};

use super::types::{DaveOp, MAX_DAVE_PROTOCOL_VERSION, ProposalsOp};
use super::ws::WS_MAX_MESSAGE;
use crate::ids::{ChannelId, GuildId, UserId};

pub mod op {
    pub const IDENTIFY: u8 = 0;
    pub const SELECT_PROTOCOL: u8 = 1;
    pub const READY: u8 = 2;
    pub const HEARTBEAT: u8 = 3;
    pub const SESSION_DESCRIPTION: u8 = 4;
    pub const SPEAKING: u8 = 5;
    pub const HEARTBEAT_ACK: u8 = 6;
    pub const RESUME: u8 = 7;
    pub const HELLO: u8 = 8;
    pub const RESUMED: u8 = 9;
    pub const CLIENTS_CONNECT: u8 = 11;
    pub const CLIENT_DISCONNECT: u8 = 13;
    pub const DAVE_PREPARE_TRANSITION: u8 = 21;
    pub const DAVE_EXECUTE_TRANSITION: u8 = 22;
    pub const DAVE_TRANSITION_READY: u8 = 23;
    pub const DAVE_PREPARE_EPOCH: u8 = 24;
    pub const DAVE_MLS_EXTERNAL_SENDER: u8 = 25;
    pub const DAVE_MLS_KEY_PACKAGE: u8 = 26;
    pub const DAVE_MLS_PROPOSALS: u8 = 27;
    pub const DAVE_MLS_COMMIT_WELCOME: u8 = 28;
    pub const DAVE_MLS_ANNOUNCE_COMMIT_TRANSITION: u8 = 29;
    pub const DAVE_MLS_WELCOME: u8 = 30;
    pub const DAVE_MLS_INVALID_COMMIT_WELCOME: u8 = 31;
}

const MAIN_GATEWAY_VOICE_STATE_UPDATE: u8 = 4;
const MAX_HEARTBEAT_INTERVAL_MS: f64 = 600_000.0;
const SECRET_KEY_LEN: usize = 32;
const BINARY_HEADER: usize = 3;
const PROPOSALS_HEADER: usize = 4;
const TRANSITION_HEADER: usize = 5;

static ENVELOPE: LazyLock<PointerTree> = LazyLock::new(|| {
    let mut tree = PointerTree::new();
    for key in ["op", "seq", "d"] {
        tree.add_path([key]);
    }
    tree
});

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum ProtoError {
    #[error("invalid JSON at line {line} column {column}")]
    Json { line: usize, column: usize },
    #[error("missing or invalid `op`")]
    MissingOpcode,
    #[error("op {op} has a missing or invalid `{field}`")]
    Field { op: u64, field: &'static str },
    #[error("a binary message of {len} bytes has no opcode")]
    NoOpcode { len: usize },
    #[error("binary op {op} is {len} bytes, shorter than its {header}-byte header")]
    Truncated { op: u8, len: usize, header: usize },
    #[error("op 27 has an unknown proposals operation {0}")]
    ProposalsOperation(u8),
    #[error("a message of {len} bytes is over the {WS_MAX_MESSAGE}-byte limit")]
    TooLarge { len: usize },
}

impl From<sonic_rs::Error> for ProtoError {
    fn from(error: sonic_rs::Error) -> Self {
        Self::Json {
            line: error.line(),
            column: error.column(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Inbound {
    Hello { interval: Duration },
    Ready(Ready),
    SessionDescription(SessionDescription),
    Speaking { user: UserId, ssrc: u32, flags: u32 },
    HeartbeatAck { t: u64 },
    Resumed,
    ClientsConnect { users: Vec<UserId> },
    ClientDisconnect { user: UserId },
    Dave(DaveOp),
    Other { op: u64 },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Ready {
    pub ssrc: u32,
    pub ip: IpAddr,
    pub port: u16,
    pub modes: Vec<Box<str>>,
}

#[derive(Clone, PartialEq, Eq)]
pub struct SessionDescription {
    pub mode: Box<str>,
    pub secret_key: [u8; SECRET_KEY_LEN],
    pub dave_protocol_version: u16,
}

impl fmt::Debug for SessionDescription {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("SessionDescription")
            .field("mode", &self.mode)
            .field("dave_protocol_version", &self.dave_protocol_version)
            .finish_non_exhaustive()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Frame {
    pub seq: Option<u64>,
    pub inbound: Result<Inbound, ProtoError>,
}

pub fn parse_text(text: &[u8]) -> Frame {
    if text.len() > WS_MAX_MESSAGE {
        return Frame {
            seq: None,
            inbound: Err(ProtoError::TooLarge { len: text.len() }),
        };
    }
    let fields = match sonic_rs::get_many(text, &ENVELOPE) {
        Ok(fields) => fields,
        Err(error) => {
            return Frame {
                seq: None,
                inbound: Err(error.into()),
            };
        }
    };
    let mut fields = fields.into_iter();
    let (op, seq, d) = (
        fields.next().flatten(),
        fields.next().flatten(),
        fields.next().flatten(),
    );
    Frame {
        seq: seq.and_then(|seq| seq.as_u64()),
        inbound: op
            .and_then(|op| op.as_u64())
            .ok_or(ProtoError::MissingOpcode)
            .and_then(|op| json(op, d.as_ref())),
    }
}

pub fn parse_binary(message: Bytes) -> Frame {
    let seq = message
        .first_chunk::<2>()
        .map(|&seq| u64::from(u16::from_be_bytes(seq)));
    let inbound = if message.len() > WS_MAX_MESSAGE {
        Err(ProtoError::TooLarge { len: message.len() })
    } else {
        binary(&message)
    };
    Frame { seq, inbound }
}

fn json(op_code: u64, d: Option<&LazyValue<'_>>) -> Result<Inbound, ProtoError> {
    let body = Body { op: op_code, d };
    let Ok(code) = u8::try_from(op_code) else {
        return Ok(Inbound::Other { op: op_code });
    };
    Ok(match code {
        op::READY => Inbound::Ready(Ready {
            ssrc: body.required("ssrc", as_u32)?,
            ip: body.required("ip", |value| value.as_str()?.parse().ok())?,
            port: body.required("port", as_u16)?,
            modes: body.required("modes", strings)?,
        }),
        op::SESSION_DESCRIPTION => Inbound::SessionDescription(SessionDescription {
            mode: body.required("mode", |value| value.as_str().map(Box::from))?,
            secret_key: body.required("secret_key", secret_key)?,
            dave_protocol_version: body.optional("dave_protocol_version", as_u16)?.unwrap_or(0),
        }),
        op::SPEAKING => Inbound::Speaking {
            user: body.required("user_id", user)?,
            ssrc: body.required("ssrc", as_u32)?,
            flags: body.required("speaking", as_u32)?,
        },
        op::HEARTBEAT_ACK => Inbound::HeartbeatAck {
            t: body.required("t", |value| value.as_u64())?,
        },
        op::HELLO => Inbound::Hello {
            interval: body.required("heartbeat_interval", heartbeat_interval)?,
        },
        op::RESUMED => Inbound::Resumed,
        op::CLIENTS_CONNECT => Inbound::ClientsConnect {
            users: body.required("user_ids", users)?,
        },
        op::CLIENT_DISCONNECT => Inbound::ClientDisconnect {
            user: body.required("user_id", user)?,
        },
        op::DAVE_PREPARE_TRANSITION => Inbound::Dave(DaveOp::PrepareTransition {
            transition_id: body.required("transition_id", as_u16)?,
            protocol_version: body.required("protocol_version", as_u16)?,
        }),
        op::DAVE_EXECUTE_TRANSITION => Inbound::Dave(DaveOp::ExecuteTransition {
            transition_id: body.required("transition_id", as_u16)?,
        }),
        op::DAVE_PREPARE_EPOCH => Inbound::Dave(DaveOp::PrepareEpoch {
            transition_id: body.optional("transition_id", as_u16)?,
            epoch: body.required("epoch", |value| value.as_u64())?,
            protocol_version: body.required("protocol_version", as_u16)?,
        }),
        _ => Inbound::Other { op: op_code },
    })
}

fn binary(message: &Bytes) -> Result<Inbound, ProtoError> {
    let len = message.len();
    let Some(&code) = message.get(2) else {
        return Err(ProtoError::NoOpcode { len });
    };
    let truncated = |header| ProtoError::Truncated {
        op: code,
        len,
        header,
    };
    let body = |header| {
        if len < header {
            Err(truncated(header))
        } else {
            Ok(message.slice(header..))
        }
    };
    let transition_id = || {
        message
            .first_chunk::<TRANSITION_HEADER>()
            .map(|&[.., high, low]| u16::from_be_bytes([high, low]))
            .ok_or(truncated(TRANSITION_HEADER))
    };
    let dave = match code {
        op::DAVE_MLS_EXTERNAL_SENDER => DaveOp::ExternalSender(body(BINARY_HEADER)?),
        op::DAVE_MLS_PROPOSALS => {
            let Some(&[.., operation]) = message.first_chunk::<PROPOSALS_HEADER>() else {
                return Err(truncated(PROPOSALS_HEADER));
            };
            let operation = match operation {
                0 => ProposalsOp::Append,
                1 => ProposalsOp::Revoke,
                unknown => return Err(ProtoError::ProposalsOperation(unknown)),
            };
            DaveOp::Proposals {
                operation,
                data: body(PROPOSALS_HEADER)?,
            }
        }
        op::DAVE_MLS_ANNOUNCE_COMMIT_TRANSITION => DaveOp::AnnounceCommit {
            transition_id: transition_id()?,
            commit: body(TRANSITION_HEADER)?,
        },
        op::DAVE_MLS_WELCOME => DaveOp::Welcome {
            transition_id: transition_id()?,
            welcome: body(TRANSITION_HEADER)?,
        },
        _ => {
            return Ok(Inbound::Other {
                op: u64::from(code),
            });
        }
    };
    Ok(Inbound::Dave(dave))
}

struct Body<'a, 'b> {
    op: u64,
    d: Option<&'a LazyValue<'b>>,
}

impl Body<'_, '_> {
    fn required<T>(
        &self,
        field: &'static str,
        read: impl FnOnce(&LazyValue<'_>) -> Option<T>,
    ) -> Result<T, ProtoError> {
        self.optional(field, read)?
            .ok_or(ProtoError::Field { op: self.op, field })
    }

    fn optional<T>(
        &self,
        field: &'static str,
        read: impl FnOnce(&LazyValue<'_>) -> Option<T>,
    ) -> Result<Option<T>, ProtoError> {
        match self.d.and_then(|d| d.get(field)) {
            None => Ok(None),
            Some(value) if value.is_null() => Ok(None),
            Some(value) => read(&value)
                .map(Some)
                .ok_or(ProtoError::Field { op: self.op, field }),
        }
    }
}

fn as_u16(value: &LazyValue<'_>) -> Option<u16> {
    u16::try_from(value.as_u64()?).ok()
}

fn as_u32(value: &LazyValue<'_>) -> Option<u32> {
    u32::try_from(value.as_u64()?).ok()
}

fn user(value: &LazyValue<'_>) -> Option<UserId> {
    let id = match value.as_str() {
        Some(text) => text.parse().ok()?,
        None => value.as_u64()?,
    };
    Some(UserId::from(id))
}

fn users(value: &LazyValue<'_>) -> Option<Vec<UserId>> {
    sonic_rs::to_array_iter(value.as_raw_str())
        .map(|item| user(&item.ok()?))
        .collect()
}

fn strings(value: &LazyValue<'_>) -> Option<Vec<Box<str>>> {
    sonic_rs::to_array_iter(value.as_raw_str())
        .map(|item| item.ok()?.as_str().map(Box::from))
        .collect()
}

fn secret_key(value: &LazyValue<'_>) -> Option<[u8; SECRET_KEY_LEN]> {
    let mut items = sonic_rs::to_array_iter(value.as_raw_str());
    let mut key = [0; SECRET_KEY_LEN];
    for byte in &mut key {
        *byte = u8::try_from(items.next()?.ok()?.as_u64()?).ok()?;
    }
    items.next().is_none().then_some(key)
}

fn heartbeat_interval(value: &LazyValue<'_>) -> Option<Duration> {
    let millis = value
        .as_f64()
        .filter(|&millis| millis > 0.0 && millis <= MAX_HEARTBEAT_INTERVAL_MS)?;
    Duration::try_from_secs_f64(millis / 1000.0).ok()
}

pub fn identify(server: GuildId, user: UserId, session_id: &str, token: &str) -> Bytes {
    Bytes::from(format!(
        r#"{{"op":{},"d":{{"server_id":"{server}","user_id":"{user}","session_id":{},"token":{},"max_dave_protocol_version":{MAX_DAVE_PROTOCOL_VERSION}}}}}"#,
        op::IDENTIFY,
        quoted(session_id),
        quoted(token),
    ))
}

pub fn select_protocol(ip: IpAddr, port: u16, mode: &str) -> Bytes {
    Bytes::from(format!(
        r#"{{"op":{},"d":{{"protocol":"udp","data":{{"address":"{ip}","port":{port},"mode":{}}}}}}}"#,
        op::SELECT_PROTOCOL,
        quoted(mode),
    ))
}

pub fn heartbeat(t: u64, seq_ack: Option<u64>) -> Bytes {
    Bytes::from(format!(
        r#"{{"op":{},"d":{{"t":{t},"seq_ack":{}}}}}"#,
        op::HEARTBEAT,
        seq_ack_number(seq_ack),
    ))
}

pub fn speaking(flags: u8, ssrc: u32) -> Bytes {
    Bytes::from(format!(
        r#"{{"op":{},"d":{{"speaking":{flags},"delay":0,"ssrc":{ssrc}}}}}"#,
        op::SPEAKING,
    ))
}

pub fn resume(server: GuildId, session_id: &str, token: &str, seq_ack: Option<u64>) -> Bytes {
    Bytes::from(format!(
        r#"{{"op":{},"d":{{"server_id":"{server}","session_id":{},"token":{},"seq_ack":{}}}}}"#,
        op::RESUME,
        quoted(session_id),
        quoted(token),
        seq_ack_number(seq_ack),
    ))
}

pub fn transition_ready(transition_id: u16) -> Bytes {
    Bytes::from(format!(
        r#"{{"op":{},"d":{{"transition_id":{transition_id}}}}}"#,
        op::DAVE_TRANSITION_READY,
    ))
}

pub fn invalid_commit_welcome(transition_id: u16) -> Bytes {
    Bytes::from(format!(
        r#"{{"op":{},"d":{{"transition_id":{transition_id}}}}}"#,
        op::DAVE_MLS_INVALID_COMMIT_WELCOME,
    ))
}

pub fn key_package(key_package: &[u8]) -> Bytes {
    Bytes::from([&[op::DAVE_MLS_KEY_PACKAGE], key_package].concat())
}

pub fn commit_welcome(commit_welcome: &[u8]) -> Bytes {
    Bytes::from([&[op::DAVE_MLS_COMMIT_WELCOME], commit_welcome].concat())
}

pub fn voice_state_update(
    guild: GuildId,
    channel: Option<ChannelId>,
    self_mute: bool,
    self_deaf: bool,
) -> Bytes {
    let channel = channel.map_or_else(|| "null".to_owned(), |channel| format!(r#""{channel}""#));
    Bytes::from(format!(
        r#"{{"op":{MAIN_GATEWAY_VOICE_STATE_UPDATE},"d":{{"guild_id":"{guild}","channel_id":{channel},"self_mute":{self_mute},"self_deaf":{self_deaf}}}}}"#,
    ))
}

fn quoted(text: &str) -> String {
    Value::from(Cow::Borrowed(text)).to_string()
}

fn seq_ack_number(seq_ack: Option<u64>) -> String {
    seq_ack.map_or_else(|| "-1".to_owned(), |seq| seq.to_string())
}

#[cfg(test)]
mod tests {
    use std::net::Ipv4Addr;

    use proptest::collection::vec;
    use proptest::prelude::*;

    use super::*;

    const SESSION: &str = include_str!("testdata/session.jsonl");

    fn fixture(needle: &str) -> &'static str {
        SESSION
            .lines()
            .find(|line| line.contains(needle))
            .unwrap_or_else(|| panic!("no fixture frame contains {needle}"))
    }

    fn hex(text: &str) -> Vec<u8> {
        (0..text.len())
            .step_by(2)
            .map(|at| u8::from_str_radix(&text[at..at + 2], 16).unwrap())
            .collect()
    }

    fn fixture_bytes(line: &str) -> Option<Vec<u8>> {
        let binary = sonic_rs::get(line, ["binary"]).ok()?;
        Some(hex(binary.as_str().unwrap()))
    }

    fn parse_fixture(line: &str) -> Frame {
        match fixture_bytes(line) {
            Some(bytes) => parse_binary(Bytes::from(bytes)),
            None => parse_text(line.as_bytes()),
        }
    }

    fn inbound(text: &str) -> Inbound {
        parse_text(text.as_bytes()).inbound.unwrap()
    }

    fn error(text: &str) -> ProtoError {
        parse_text(text.as_bytes()).inbound.unwrap_err()
    }

    fn binary_frame(seq: u16, op: u8, payload: &[u8]) -> Bytes {
        Bytes::from([&seq.to_be_bytes()[..], &[op], payload].concat())
    }

    fn opcode(inbound: &Inbound) -> u8 {
        match inbound {
            Inbound::Hello { .. } => op::HELLO,
            Inbound::Ready(_) => op::READY,
            Inbound::SessionDescription(_) => op::SESSION_DESCRIPTION,
            Inbound::Speaking { .. } => op::SPEAKING,
            Inbound::HeartbeatAck { .. } => op::HEARTBEAT_ACK,
            Inbound::Resumed => op::RESUMED,
            Inbound::ClientsConnect { .. } => op::CLIENTS_CONNECT,
            Inbound::ClientDisconnect { .. } => op::CLIENT_DISCONNECT,
            Inbound::Dave(DaveOp::PrepareTransition { .. }) => op::DAVE_PREPARE_TRANSITION,
            Inbound::Dave(DaveOp::ExecuteTransition { .. }) => op::DAVE_EXECUTE_TRANSITION,
            Inbound::Dave(DaveOp::PrepareEpoch { .. }) => op::DAVE_PREPARE_EPOCH,
            Inbound::Dave(DaveOp::ExternalSender(_)) => op::DAVE_MLS_EXTERNAL_SENDER,
            Inbound::Dave(DaveOp::Proposals { .. }) => op::DAVE_MLS_PROPOSALS,
            Inbound::Dave(DaveOp::AnnounceCommit { .. }) => op::DAVE_MLS_ANNOUNCE_COMMIT_TRANSITION,
            Inbound::Dave(DaveOp::Welcome { .. }) => op::DAVE_MLS_WELCOME,
            Inbound::Other { op } => u8::try_from(*op).unwrap(),
        }
    }

    #[test]
    fn every_fixture_frame_parses_and_keeps_its_seq() {
        let mut ops = Vec::new();
        for line in SESSION.lines() {
            let frame = parse_fixture(line);
            let expected_seq = match fixture_bytes(line) {
                Some(bytes) => Some(u64::from(u16::from_be_bytes([bytes[0], bytes[1]]))),
                None => sonic_rs::get(line, ["seq"])
                    .ok()
                    .and_then(|seq| seq.as_u64()),
            };

            assert_eq!(frame.seq, expected_seq, "{line}");
            let inbound = frame
                .inbound
                .unwrap_or_else(|error| panic!("{line}: {error}"));
            ops.push(opcode(&inbound));
        }
        ops.sort_unstable();
        ops.dedup();

        assert_eq!(
            ops,
            [
                2, 4, 5, 6, 8, 9, 11, 12, 13, 15, 18, 20, 21, 22, 24, 25, 27, 29, 30
            ]
        );
    }

    #[test]
    fn hello_reads_a_float_interval_in_milliseconds() {
        assert_eq!(
            inbound(fixture(r#""op":8,"#)),
            Inbound::Hello {
                interval: Duration::from_secs_f64(13.750_25)
            }
        );
        assert_eq!(
            inbound(r#"{"op":8,"d":{"heartbeat_interval":41250}}"#),
            Inbound::Hello {
                interval: Duration::from_millis(41_250)
            }
        );
    }

    #[test]
    fn hello_intervals_must_be_positive_and_at_most_10_minutes() {
        assert_eq!(
            inbound(r#"{"op":8,"d":{"heartbeat_interval":600000}}"#),
            Inbound::Hello {
                interval: Duration::from_secs(600)
            }
        );
        for interval in ["0", "-1", "600000.5", "1e400", "\"41250\"", "null"] {
            let frame = format!(r#"{{"op":8,"d":{{"heartbeat_interval":{interval}}}}}"#);

            assert_eq!(
                error(&frame),
                ProtoError::Field {
                    op: 8,
                    field: "heartbeat_interval"
                },
                "{interval}"
            );
        }
    }

    #[test]
    fn ready_keeps_ssrcs_above_2_31_and_ignores_its_heartbeat_interval() {
        let Inbound::Ready(ready) = inbound(fixture(r#""op":2,"#)) else {
            panic!("expected Ready");
        };

        assert_eq!(ready.ssrc, 3_456_789_012);
        assert_eq!(ready.ip, IpAddr::V4(Ipv4Addr::new(203, 0, 113, 17)));
        assert_eq!(ready.port, 50013);
        assert_eq!(ready.modes.len(), 7);
        assert_eq!(&*ready.modes[0], "aead_aes256_gcm_rtpsize");
        assert_eq!(&*ready.modes[2], "aead_xchacha20_poly1305_rtpsize");
    }

    #[test]
    fn ready_reads_ipv6_addresses_and_the_full_u32_range() {
        let Inbound::Ready(ready) = inbound(
            r#"{"op":2,"d":{"ssrc":4294967295,"ip":"2001:db8::1","port":65535,"modes":[]}}"#,
        ) else {
            panic!("expected Ready");
        };

        assert_eq!(ready.ssrc, u32::MAX);
        assert_eq!(ready.ip, "2001:db8::1".parse::<IpAddr>().unwrap());
        assert_eq!(ready.port, u16::MAX);
        assert!(ready.modes.is_empty());
    }

    #[test]
    fn session_description_reads_the_mode_key_and_dave_version() {
        let Inbound::SessionDescription(description) = inbound(fixture(r#""op":4,"#)) else {
            panic!("expected SessionDescription");
        };

        assert_eq!(&*description.mode, "aead_aes256_gcm_rtpsize");
        assert_eq!(description.secret_key[0], 251);
        assert_eq!(description.secret_key[31], 163);
        assert_eq!(description.dave_protocol_version, 1);
    }

    fn description_with_key(length: usize) -> String {
        let key = vec!["7"; length].join(",");
        format!(r#"{{"op":4,"d":{{"mode":"m","secret_key":[{key}],"dave_protocol_version":1}}}}"#)
    }

    #[test]
    fn session_description_needs_a_key_of_exactly_32_bytes() {
        assert_eq!(
            inbound(&description_with_key(32)),
            Inbound::SessionDescription(SessionDescription {
                mode: Box::from("m"),
                secret_key: [7; 32],
                dave_protocol_version: 1,
            })
        );
        for length in [0, 31, 33] {
            assert_eq!(
                error(&description_with_key(length)),
                ProtoError::Field {
                    op: 4,
                    field: "secret_key"
                },
                "{length} bytes"
            );
        }
        for key in [r#""abc""#, "[256,0]", "[-1]", "{}"] {
            let frame = format!(r#"{{"op":4,"d":{{"mode":"m","secret_key":{key}}}}}"#);

            assert!(
                matches!(error(&frame), ProtoError::Field { op: 4, .. }),
                "{key}"
            );
        }
    }

    #[test]
    fn a_missing_or_null_dave_protocol_version_is_0() {
        let key = vec!["1"; 32].join(",");
        for version in ["", r#","dave_protocol_version":null"#] {
            let frame = format!(r#"{{"op":4,"d":{{"mode":"m","secret_key":[{key}]{version}}}}}"#);

            let Inbound::SessionDescription(description) = inbound(&frame) else {
                panic!("expected SessionDescription");
            };
            assert_eq!(description.dave_protocol_version, 0);
        }
    }

    #[test]
    fn session_description_debug_hides_the_secret_key() {
        let Inbound::SessionDescription(description) = inbound(&description_with_key(32)) else {
            panic!("expected SessionDescription");
        };

        let debug = format!("{description:?}");

        assert!(debug.contains("mode"), "{debug}");
        assert!(!debug.contains("secret_key"), "{debug}");
        assert!(!debug.contains('7'), "{debug}");
    }

    #[test]
    fn speaking_and_client_ops_read_their_user_ids() {
        assert_eq!(
            inbound(fixture(r#""op":5,"#)),
            Inbound::Speaking {
                user: UserId::from(1_335_927_767_040_131_073),
                ssrc: 2_789_012_345,
                flags: 1,
            }
        );
        assert_eq!(
            inbound(fixture(r#""op":11,"#)),
            Inbound::ClientsConnect {
                users: vec![
                    UserId::from(1_335_927_767_040_131_073),
                    UserId::from(1_329_027_298_099_331_074)
                ]
            }
        );
        assert_eq!(
            inbound(fixture(r#""op":13,"#)),
            Inbound::ClientDisconnect {
                user: UserId::from(1_329_027_298_099_331_074)
            }
        );
    }

    #[test]
    fn user_ids_are_read_from_strings_or_numbers_and_rejected_otherwise() {
        assert_eq!(
            inbound(r#"{"op":13,"d":{"user_id":42}}"#),
            Inbound::ClientDisconnect {
                user: UserId::from(42)
            }
        );
        for user_ids in [r#"["1","x"]"#, r#""1""#, "[-1]", "[true]"] {
            let frame = format!(r#"{{"op":11,"d":{{"user_ids":{user_ids}}}}}"#);

            assert_eq!(
                error(&frame),
                ProtoError::Field {
                    op: 11,
                    field: "user_ids"
                },
                "{user_ids}"
            );
        }
    }

    #[test]
    fn heartbeat_ack_and_resumed_parse() {
        assert_eq!(
            inbound(fixture(r#""op":6,"#)),
            Inbound::HeartbeatAck {
                t: 1_730_000_000_000
            }
        );
        assert_eq!(inbound(fixture(r#""op":9,"#)), Inbound::Resumed);
        assert_eq!(inbound(r#"{"op":9}"#), Inbound::Resumed);
    }

    #[test]
    fn dave_json_ops_parse_into_dave_ops() {
        assert_eq!(
            inbound(fixture(r#""op":21,"#)),
            Inbound::Dave(DaveOp::PrepareTransition {
                transition_id: 12,
                protocol_version: 0
            })
        );
        assert_eq!(
            inbound(fixture(r#""op":22,"#)),
            Inbound::Dave(DaveOp::ExecuteTransition { transition_id: 12 })
        );
        assert_eq!(
            inbound(fixture(
                r#""transition_id":13,"protocol_version":1,"epoch":1"#
            )),
            Inbound::Dave(DaveOp::PrepareEpoch {
                transition_id: Some(13),
                epoch: 1,
                protocol_version: 1
            })
        );
        assert_eq!(
            inbound(fixture(r#""d":{"protocol_version":1,"epoch":2}"#)),
            Inbound::Dave(DaveOp::PrepareEpoch {
                transition_id: None,
                epoch: 2,
                protocol_version: 1
            })
        );
    }

    #[test]
    fn binary_dave_ops_read_their_fields_at_their_offsets() {
        let external_sender = binary_frame(1, 25, &[0xA0, 0xA1]);
        let append = binary_frame(2, 27, &[0, 0x02, 0xB0, 0xB1]);
        let revoke = binary_frame(3, 27, &[1, 0x01, 0xC0]);
        let commit = binary_frame(4, 29, &[0x01, 0x02, 0xD0, 0xD1]);
        let welcome = binary_frame(5, 30, &[0xFF, 0xFE, 0xE0]);

        let parsed = [external_sender, append, revoke, commit, welcome]
            .map(|message| parse_binary(message).inbound.unwrap());

        assert_eq!(
            parsed,
            [
                Inbound::Dave(DaveOp::ExternalSender(Bytes::from_static(&[0xA0, 0xA1]))),
                Inbound::Dave(DaveOp::Proposals {
                    operation: ProposalsOp::Append,
                    data: Bytes::from_static(&[0x02, 0xB0, 0xB1])
                }),
                Inbound::Dave(DaveOp::Proposals {
                    operation: ProposalsOp::Revoke,
                    data: Bytes::from_static(&[0x01, 0xC0])
                }),
                Inbound::Dave(DaveOp::AnnounceCommit {
                    transition_id: 0x0102,
                    commit: Bytes::from_static(&[0xD0, 0xD1])
                }),
                Inbound::Dave(DaveOp::Welcome {
                    transition_id: 0xFFFE,
                    welcome: Bytes::from_static(&[0xE0])
                }),
            ]
        );
    }

    #[test]
    fn binary_frames_take_their_seq_from_the_big_endian_prefix() {
        let frame = parse_binary(binary_frame(0x0102, 25, &[]));

        assert_eq!(frame.seq, Some(258));
        assert_eq!(
            frame.inbound,
            Ok(Inbound::Dave(DaveOp::ExternalSender(Bytes::new())))
        );
    }

    #[test]
    fn fixture_binary_ops_keep_the_mls_bytes_after_their_header() {
        let Inbound::Dave(DaveOp::Proposals { operation, data }) =
            parse_fixture(fixture("000f1b")).inbound.unwrap()
        else {
            panic!("expected proposals");
        };
        assert_eq!(operation, ProposalsOp::Append);
        assert_eq!(data[0], 0x0c);
        assert_eq!(data.len(), 13);

        let Inbound::Dave(DaveOp::AnnounceCommit {
            transition_id,
            commit,
        }) = parse_fixture(fixture("00111d")).inbound.unwrap()
        else {
            panic!("expected a commit");
        };
        assert_eq!(transition_id, 258);
        assert_eq!(&commit[..4], [0x00, 0x01, 0x00, 0x01]);
    }

    #[test]
    fn unknown_ops_are_other_in_either_framing() {
        for op in [12, 15, 18, 20] {
            let needle = format!(r#""op":{op},"#);

            assert_eq!(inbound(fixture(&needle)), Inbound::Other { op });
        }
        assert_eq!(inbound(r#"{"op":25,"d":{}}"#), Inbound::Other { op: 25 });
        assert_eq!(
            inbound(r#"{"op":4096,"d":{}}"#),
            Inbound::Other { op: 4096 }
        );
        assert_eq!(
            parse_binary(binary_frame(7, 2, b"{}")).inbound,
            Ok(Inbound::Other { op: 2 })
        );
    }

    #[test]
    fn binary_frames_shorter_than_their_header_are_invalid() {
        let cases: [(&[u8], ProtoError); 6] = [
            (&[], ProtoError::NoOpcode { len: 0 }),
            (&[0], ProtoError::NoOpcode { len: 1 }),
            (&[0, 1], ProtoError::NoOpcode { len: 2 }),
            (
                &[0, 1, 27],
                ProtoError::Truncated {
                    op: 27,
                    len: 3,
                    header: 4,
                },
            ),
            (
                &[0, 1, 29, 0],
                ProtoError::Truncated {
                    op: 29,
                    len: 4,
                    header: 5,
                },
            ),
            (
                &[0, 1, 30, 0],
                ProtoError::Truncated {
                    op: 30,
                    len: 4,
                    header: 5,
                },
            ),
        ];
        for (bytes, expected) in cases {
            assert_eq!(
                parse_binary(Bytes::copy_from_slice(bytes)).inbound,
                Err(expected),
                "{bytes:?}"
            );
        }
    }

    #[test]
    fn an_invalid_frame_keeps_any_seq_it_carries() {
        assert_eq!(parse_binary(Bytes::from_static(&[0])).seq, None);
        assert_eq!(parse_binary(Bytes::from_static(&[0, 9])).seq, Some(9));
        assert_eq!(
            parse_binary(Bytes::from_static(&[0, 9, 29, 0])).seq,
            Some(9)
        );

        let frame = parse_text(br#"{"op":2,"d":{"ssrc":"1"},"seq":41}"#);

        assert_eq!(frame.seq, Some(41));
        assert_eq!(
            frame.inbound,
            Err(ProtoError::Field {
                op: 2,
                field: "ssrc"
            })
        );
    }

    #[test]
    fn proposals_with_an_unknown_operation_are_invalid() {
        for operation in [2, 255] {
            assert_eq!(
                parse_binary(binary_frame(1, 27, &[operation, 0])).inbound,
                Err(ProtoError::ProposalsOperation(operation))
            );
        }
    }

    #[test]
    fn messages_over_the_size_limit_are_invalid() {
        let largest = binary_frame(3, 25, &vec![0; WS_MAX_MESSAGE - BINARY_HEADER]);
        assert!(parse_binary(largest).inbound.is_ok());

        let over = binary_frame(3, 25, &vec![0; WS_MAX_MESSAGE - BINARY_HEADER + 1]);
        let frame = parse_binary(over);
        assert_eq!(frame.seq, Some(3));
        assert_eq!(
            frame.inbound,
            Err(ProtoError::TooLarge {
                len: WS_MAX_MESSAGE + 1
            })
        );

        let text = format!(
            r#"{{"op":6,"d":{{"t":1,"pad":"{}"}}}}"#,
            "x".repeat(WS_MAX_MESSAGE)
        );
        assert_eq!(error(&text), ProtoError::TooLarge { len: text.len() });
    }

    #[test]
    fn malformed_json_bodies_name_the_op_and_field() {
        let cases = [
            (
                r#"{"op":2,"d":{"ssrc":4294967296,"ip":"1.2.3.4","port":1,"modes":[]}}"#,
                2,
                "ssrc",
            ),
            (
                r#"{"op":2,"d":{"ssrc":1,"ip":"localhost","port":1,"modes":[]}}"#,
                2,
                "ip",
            ),
            (
                r#"{"op":2,"d":{"ssrc":1,"ip":"1.2.3.4","port":65536,"modes":[]}}"#,
                2,
                "port",
            ),
            (
                r#"{"op":2,"d":{"ssrc":1,"ip":"1.2.3.4","port":1,"modes":"x"}}"#,
                2,
                "modes",
            ),
            (
                r#"{"op":2,"d":{"ssrc":1,"ip":"1.2.3.4","port":1,"modes":[1]}}"#,
                2,
                "modes",
            ),
            (r#"{"op":2}"#, 2, "ssrc"),
            (
                r#"{"op":5,"d":{"user_id":"1","ssrc":1,"speaking":-1}}"#,
                5,
                "speaking",
            ),
            (r#"{"op":6,"d":{"t":"now"}}"#, 6, "t"),
            (r#"{"op":6,"d":1501184119561}"#, 6, "t"),
            (
                r#"{"op":21,"d":{"transition_id":65536,"protocol_version":0}}"#,
                21,
                "transition_id",
            ),
            (r#"{"op":22,"d":{}}"#, 22, "transition_id"),
            (
                r#"{"op":24,"d":{"transition_id":"1","epoch":1,"protocol_version":1}}"#,
                24,
                "transition_id",
            ),
            (r#"{"op":24,"d":{"protocol_version":1}}"#, 24, "epoch"),
        ];
        for (frame, op, field) in cases {
            assert_eq!(error(frame), ProtoError::Field { op, field }, "{frame}");
        }
        assert_eq!(error(r#"{"d":{}}"#), ProtoError::MissingOpcode);
        assert_eq!(error(r#"{"op":"2"}"#), ProtoError::MissingOpcode);
        assert!(matches!(error(r#"{"op":2,"d":"#), ProtoError::Json { .. }));
    }

    #[test]
    fn identify_matches_the_documented_payload() {
        let identify = identify(
            GuildId::from(41_771_983_423_143_937),
            UserId::from(104_694_319_306_248_192),
            "my_session_id",
            "my_token",
        );

        assert_eq!(
            identify,
            r#"{"op":0,"d":{"server_id":"41771983423143937","user_id":"104694319306248192","session_id":"my_session_id","token":"my_token","max_dave_protocol_version":1}}"#
        );
    }

    #[test]
    fn identify_escapes_its_strings() {
        let identify = identify(GuildId::from(1), UserId::from(2), "a\"b", "c\\d");

        let value: Value = sonic_rs::from_slice(&identify).unwrap();
        assert_eq!(value["d"]["session_id"].as_str(), Some("a\"b"));
        assert_eq!(value["d"]["token"].as_str(), Some("c\\d"));
    }

    #[test]
    fn select_protocol_matches_the_documented_payload() {
        let select = select_protocol(
            IpAddr::V4(Ipv4Addr::LOCALHOST),
            1337,
            "aead_aes256_gcm_rtpsize",
        );

        assert_eq!(
            select,
            r#"{"op":1,"d":{"protocol":"udp","data":{"address":"127.0.0.1","port":1337,"mode":"aead_aes256_gcm_rtpsize"}}}"#
        );
    }

    #[test]
    fn heartbeat_sends_t_and_seq_ack_or_minus_one() {
        assert_eq!(
            heartbeat(1_501_184_119_561, Some(10)),
            r#"{"op":3,"d":{"t":1501184119561,"seq_ack":10}}"#
        );
        assert_eq!(
            heartbeat(1_501_184_119_561, None),
            r#"{"op":3,"d":{"t":1501184119561,"seq_ack":-1}}"#
        );
    }

    #[test]
    fn speaking_sends_flags_delay_0_and_ssrc() {
        assert_eq!(
            speaking(5, 3_456_789_012),
            r#"{"op":5,"d":{"speaking":5,"delay":0,"ssrc":3456789012}}"#
        );
    }

    #[test]
    fn resume_matches_the_documented_payload() {
        assert_eq!(
            resume(
                GuildId::from(41_771_983_423_143_937),
                "my_session_id",
                "my_token",
                Some(10)
            ),
            r#"{"op":7,"d":{"server_id":"41771983423143937","session_id":"my_session_id","token":"my_token","seq_ack":10}}"#
        );
        assert_eq!(
            resume(GuildId::from(1), "s", "t", None),
            r#"{"op":7,"d":{"server_id":"1","session_id":"s","token":"t","seq_ack":-1}}"#
        );
    }

    #[test]
    fn dave_json_replies_carry_the_transition_id() {
        assert_eq!(
            transition_ready(10),
            r#"{"op":23,"d":{"transition_id":10}}"#
        );
        assert_eq!(
            invalid_commit_welcome(32),
            r#"{"op":31,"d":{"transition_id":32}}"#
        );
    }

    #[test]
    fn dave_binary_replies_are_the_opcode_then_the_payload() {
        assert_eq!(key_package(&[1, 2, 3]), [26, 1, 2, 3][..]);
        assert_eq!(commit_welcome(&[4, 5]), [28, 4, 5][..]);
        assert_eq!(commit_welcome(&[]), [28][..]);
    }

    #[test]
    fn voice_state_update_always_carries_all_four_fields() {
        let guild = GuildId::from(41_771_983_423_143_937);
        let channel = ChannelId::from(127_121_515_262_115_840);

        assert_eq!(
            voice_state_update(guild, Some(channel), false, false),
            r#"{"op":4,"d":{"guild_id":"41771983423143937","channel_id":"127121515262115840","self_mute":false,"self_deaf":false}}"#
        );
        assert_eq!(
            voice_state_update(guild, None, true, true),
            r#"{"op":4,"d":{"guild_id":"41771983423143937","channel_id":null,"self_mute":true,"self_deaf":true}}"#
        );
    }

    const KEYS: &[&str] = &[
        "ssrc",
        "ip",
        "port",
        "modes",
        "mode",
        "secret_key",
        "dave_protocol_version",
        "user_id",
        "user_ids",
        "speaking",
        "t",
        "heartbeat_interval",
        "transition_id",
        "protocol_version",
        "epoch",
    ];

    fn json_text() -> impl Strategy<Value = String> {
        let leaf = prop_oneof![
            Just("null".to_owned()),
            Just("1e400".to_owned()),
            any::<bool>().prop_map(|value| value.to_string()),
            any::<i64>().prop_map(|value| value.to_string()),
            any::<u64>().prop_map(|value| value.to_string()),
            (0_u64..300).prop_map(|value| value.to_string()),
            any::<f64>()
                .prop_filter("JSON has no NaN or infinity", |value| value.is_finite())
                .prop_map(|value| format!("{value:?}")),
            "[ -~]{0,12}".prop_map(|text| sonic_rs::to_string(&text).unwrap()),
            "[0-9]{1,20}".prop_map(|digits| format!("\"{digits}\"")),
        ];
        leaf.prop_recursive(3, 48, 34, |inner| {
            prop_oneof![
                vec(inner.clone(), 0..34).prop_map(|items| format!("[{}]", items.join(","))),
                vec((prop::sample::select(KEYS), inner), 0..6).prop_map(|fields| {
                    let fields: Vec<String> = fields
                        .into_iter()
                        .map(|(key, value)| format!("\"{key}\":{value}"))
                        .collect();
                    format!("{{{}}}", fields.join(","))
                }),
            ]
        })
    }

    proptest! {
        #[test]
        fn random_text_never_panics(bytes in vec(any::<u8>(), 0..256)) {
            let _ = parse_text(&bytes);
        }

        #[test]
        fn random_binary_never_panics(bytes in vec(any::<u8>(), 0..64)) {
            let _ = parse_binary(Bytes::from(bytes));
        }

        #[test]
        fn json_of_any_shape_never_panics(op in 0_u8..40, d in json_text(), seq in json_text()) {
            let frame = format!(r#"{{"op":{op},"d":{d},"seq":{seq}}}"#);

            let _ = parse_text(frame.as_bytes());
        }

        #[test]
        fn binary_ops_always_report_the_seq_prefix(
            seq in any::<u16>(),
            op in prop_oneof![Just(25_u8), Just(27), Just(29), Just(30), any::<u8>()],
            payload in vec(any::<u8>(), 0..40),
        ) {
            let frame = parse_binary(binary_frame(seq, op, &payload));

            prop_assert_eq!(frame.seq, Some(u64::from(seq)));
        }
    }
}
