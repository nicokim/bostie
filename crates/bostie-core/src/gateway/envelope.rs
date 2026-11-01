use std::sync::LazyLock;
use std::time::Duration;

use bytes::Bytes;
use sonic_rs::{JsonValueTrait, LazyValue, PointerTree, Value};

use crate::error::CoreError;

const MAX_COMMAND_BYTES: usize = 4096;
const IDENTIFY_PREFIX: &[u8] = br#"{"op":2,"d":"#;

static ENVELOPE: LazyLock<PointerTree> = LazyLock::new(|| tree(&["op", "s", "t"]));
static READY: LazyLock<PointerTree> = LazyLock::new(|| tree(&["session_id", "resume_gateway_url"]));

pub(crate) fn tree(keys: &[&str]) -> PointerTree {
    let mut tree = PointerTree::new();
    for key in keys {
        tree.add_path([*key]);
    }
    tree
}

#[derive(Debug, thiserror::Error)]
pub(crate) enum EnvelopeError {
    #[error("invalid JSON at line {line} column {column}")]
    Json { line: usize, column: usize },
    #[error("missing or invalid `{0}`")]
    Field(&'static str),
}

impl From<sonic_rs::Error> for EnvelopeError {
    fn from(error: sonic_rs::Error) -> Self {
        Self::Json {
            line: error.line(),
            column: error.column(),
        }
    }
}

#[derive(Debug)]
pub(crate) enum Frame {
    Dispatch { seq: u64, forward: Option<Forward> },
    Heartbeat,
    Reconnect,
    InvalidSession { resumable: bool },
    Hello { heartbeat_interval: Duration },
    HeartbeatAck,
    Other { op: u64 },
}

#[derive(Debug)]
pub(crate) struct Forward {
    pub(crate) name: Box<str>,
    pub(crate) data: Bytes,
}

impl Frame {
    pub(crate) fn parse(
        frame: &[u8],
        wanted: impl FnOnce(&str) -> bool,
    ) -> Result<Self, EnvelopeError> {
        let mut fields = sonic_rs::get_many(frame, &ENVELOPE)?.into_iter();
        let mut field = || fields.next().flatten();
        let (op, seq, name) = (field(), field(), field());
        let op = op
            .and_then(|op| op.as_u64())
            .ok_or(EnvelopeError::Field("op"))?;
        Ok(match op {
            0 => {
                let seq = seq
                    .and_then(|seq| seq.as_u64())
                    .ok_or(EnvelopeError::Field("s"))?;
                let name = name.ok_or(EnvelopeError::Field("t"))?;
                let name = name.as_str().ok_or(EnvelopeError::Field("t"))?;
                let forward = if wanted(name) {
                    Some(Forward {
                        name: name.into(),
                        data: Bytes::copy_from_slice(data(frame)?.as_raw_str().as_bytes()),
                    })
                } else {
                    None
                };
                Self::Dispatch { seq, forward }
            }
            1 => Self::Heartbeat,
            7 => Self::Reconnect,
            9 => Self::InvalidSession {
                resumable: data(frame)?.as_bool().unwrap_or(false),
            },
            10 => Self::Hello {
                heartbeat_interval: sonic_rs::get(frame, ["d", "heartbeat_interval"])
                    .ok()
                    .and_then(|interval| interval.as_u64())
                    .filter(|&millis| (1..=u64::from(u32::MAX)).contains(&millis))
                    .map(Duration::from_millis)
                    .ok_or(EnvelopeError::Field("d.heartbeat_interval"))?,
            },
            11 => Self::HeartbeatAck,
            op => Self::Other { op },
        })
    }
}

fn data(frame: &[u8]) -> Result<LazyValue<'_>, EnvelopeError> {
    sonic_rs::get(frame, ["d"]).map_err(|error| {
        if error.is_not_found() {
            EnvelopeError::Field("d")
        } else {
            error.into()
        }
    })
}

pub(crate) struct ReadySession {
    pub(crate) id: String,
    pub(crate) resume_url: String,
}

pub(crate) fn ready_session(data: &[u8]) -> Option<ReadySession> {
    let fields = sonic_rs::get_many(data, &READY).ok()?;
    let mut fields = fields.iter().map(|field| field.as_ref()?.as_str());
    let id = fields.next().flatten()?.to_owned();
    let resume_url = fields.next().flatten()?.to_owned();
    Some(ReadySession { id, resume_url })
}

pub(crate) fn identify_frame(payload: &[u8]) -> Result<Bytes, CoreError> {
    let value: Value = sonic_rs::from_slice(payload)
        .map_err(|error| invalid("the identify payload", EnvelopeError::from(error)))?;
    if !value.is_object() {
        return Err(CoreError::InvalidRequest(
            "the identify payload must be a JSON object".to_owned(),
        ));
    }
    let mut frame = Vec::with_capacity(IDENTIFY_PREFIX.len() + payload.len() + 1);
    frame.extend_from_slice(IDENTIFY_PREFIX);
    frame.extend_from_slice(payload);
    frame.push(b'}');
    check_size(&frame)?;
    Ok(Bytes::from(frame))
}

pub(crate) fn check_command(payload: &[u8]) -> Result<(), CoreError> {
    check_size(payload)?;
    let value: Value = sonic_rs::from_slice(payload)
        .map_err(|error| invalid("the gateway payload", EnvelopeError::from(error)))?;
    if !value.is_object() {
        return Err(CoreError::InvalidRequest(
            "a gateway payload must be a JSON object".to_owned(),
        ));
    }
    match value.get("op").and_then(|op| op.as_i64()) {
        None => Err(invalid("the gateway payload", EnvelopeError::Field("op"))),
        Some(op @ (1 | 2 | 6)) => Err(CoreError::InvalidRequest(format!(
            "op {op} is sent by bostie itself"
        ))),
        Some(_) => Ok(()),
    }
}

fn check_size(payload: &[u8]) -> Result<(), CoreError> {
    if payload.len() > MAX_COMMAND_BYTES {
        return Err(CoreError::InvalidRequest(format!(
            "a gateway payload must be at most {MAX_COMMAND_BYTES} bytes, got {}",
            payload.len()
        )));
    }
    Ok(())
}

fn invalid(what: &str, error: EnvelopeError) -> CoreError {
    CoreError::InvalidRequest(format!("{what} has {error}"))
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    pub(crate) const SESSION: &str = include_str!("testdata/session.jsonl");

    pub(crate) fn fixture(needle: &str) -> &'static str {
        SESSION
            .lines()
            .find(|line| line.contains(needle))
            .unwrap_or_else(|| panic!("no fixture frame contains {needle}"))
    }

    fn forward_all(frame: &str) -> Frame {
        Frame::parse(frame.as_bytes(), |_| true).unwrap()
    }

    #[test]
    fn reads_every_fixture_frame_like_a_full_parse() {
        for line in SESSION.lines() {
            let expected: Value = sonic_rs::from_str(line).unwrap();
            let op = expected["op"].as_u64().unwrap();
            match forward_all(line) {
                Frame::Dispatch {
                    seq,
                    forward: Some(forward),
                } => {
                    assert_eq!(op, 0);
                    assert_eq!(Some(seq), expected["s"].as_u64());
                    assert_eq!(Some(&*forward.name), expected["t"].as_str());
                    let data: Value = sonic_rs::from_slice(&forward.data).unwrap();
                    assert_eq!(data, expected["d"]);
                }
                Frame::Hello { heartbeat_interval } => {
                    assert_eq!(op, 10);
                    assert_eq!(heartbeat_interval, Duration::from_millis(41_250));
                }
                Frame::InvalidSession { resumable } => {
                    assert_eq!(op, 9);
                    assert_eq!(Some(resumable), expected["d"].as_bool());
                }
                Frame::Heartbeat => assert_eq!(op, 1),
                Frame::Reconnect => assert_eq!(op, 7),
                Frame::HeartbeatAck => assert_eq!(op, 11),
                other => panic!("unexpected {other:?} for {line}"),
            }
        }
    }

    #[test]
    fn filtered_dispatches_keep_only_the_sequence() {
        let frame = fixture(r#""t":"TYPING_START""#);

        let parsed = Frame::parse(frame.as_bytes(), |name| name != "TYPING_START").unwrap();

        assert!(matches!(
            parsed,
            Frame::Dispatch {
                seq: 3,
                forward: None
            }
        ));
    }

    #[test]
    fn key_order_does_not_matter() {
        let frame = r#"{"d":{"id":"1"},"op":0,"s":5,"t":"GUILD_DELETE"}"#;

        let Frame::Dispatch {
            seq: 5,
            forward: Some(forward),
        } = forward_all(frame)
        else {
            panic!("expected a dispatch");
        };
        assert_eq!(&*forward.name, "GUILD_DELETE");
        assert_eq!(&forward.data[..], br#"{"id":"1"}"#);
    }

    #[test]
    fn a_quoted_d_inside_values_is_not_the_payload() {
        let frame = r#"{"t":"X\"d\":{","s":2,"op":0,"d":{"note":"\"d\":[]"}}"#;

        let Frame::Dispatch {
            forward: Some(forward),
            ..
        } = forward_all(frame)
        else {
            panic!("expected a dispatch");
        };
        assert_eq!(&*forward.name, r#"X"d":{"#);
        assert_eq!(&forward.data[..], br#"{"note":"\"d\":[]"}"#);
    }

    #[test]
    fn missing_or_invalid_fields_are_errors() {
        let cases = [
            (r#"{"t":null,"s":null,"d":null}"#, "op"),
            (r#"{"t":"READY","op":0,"d":{}}"#, "s"),
            (r#"{"t":null,"s":4,"op":0,"d":{}}"#, "t"),
            (
                r#"{"t":null,"s":null,"op":10,"d":{"heartbeat_interval":0}}"#,
                "d.heartbeat_interval",
            ),
            (
                r#"{"t":null,"s":null,"op":10,"d":{}}"#,
                "d.heartbeat_interval",
            ),
            (r#"{"t":"READY","s":1,"op":0}"#, "d"),
            (r#"{"t":null,"s":null,"op":9}"#, "d"),
        ];
        for (frame, field) in cases {
            let error = Frame::parse(frame.as_bytes(), |_| true).unwrap_err();
            assert!(
                matches!(error, EnvelopeError::Field(name) if name == field),
                "{frame}: {error}"
            );
        }
        assert!(matches!(
            Frame::parse(br#"{"t":null,"op":"#, |_| true),
            Err(EnvelopeError::Json { .. })
        ));
    }

    #[test]
    fn reads_the_session_from_ready() {
        let frame = fixture(r#""t":"READY""#);
        let data = sonic_rs::get(frame, ["d"]).unwrap();

        let session = ready_session(data.as_raw_str().as_bytes()).unwrap();

        assert_eq!(session.id, "70374e9c7263b9d5e16c4f418ff85dc1");
        assert_eq!(session.resume_url, "ws://{address}/gateway-resume");
    }

    #[test]
    fn a_ready_without_resume_gateway_url_has_no_session() {
        for data in [
            r#"{"session_id":"x"}"#,
            r#"{"session_id":"x","resume_gateway_url":null}"#,
            r#"{"resume_gateway_url":"wss://gateway.discord.gg"}"#,
        ] {
            assert!(ready_session(data.as_bytes()).is_none(), "{data}");
        }
    }

    #[test]
    fn an_identify_fits_in_4096_bytes_once_wrapped_as_op_2() {
        let identify = |length: usize| format!(r#"{{"token":"{}"}}"#, "x".repeat(length - 12));

        let largest = identify_frame(identify(4083).as_bytes()).unwrap();

        assert_eq!(largest.len(), MAX_COMMAND_BYTES);
        assert_eq!(
            largest,
            [IDENTIFY_PREFIX, identify(4083).as_bytes(), b"}"].concat()
        );
        assert!(matches!(
            identify_frame(identify(4084).as_bytes()),
            Err(CoreError::InvalidRequest(_))
        ));
    }

    #[test]
    fn commands_are_checked_before_queueing() {
        let too_long = format!(r#"{{"op":3,"d":"{}"}}"#, "x".repeat(MAX_COMMAND_BYTES));
        let rejected = [
            too_long.as_str(),
            r#"{"op":1,"d":null}"#,
            r#"{"op":2,"d":{}}"#,
            r#"{"op":6,"d":{}}"#,
            r#"{"d":{}}"#,
            r#"{"op":"4"}"#,
            "[4]",
            r#"{"op":4,"#,
        ];
        for payload in rejected {
            assert!(
                matches!(
                    check_command(payload.as_bytes()),
                    Err(CoreError::InvalidRequest(_))
                ),
                "{payload}"
            );
        }
        assert!(check_command(br#"{"op":4,"d":{"guild_id":"1","channel_id":null}}"#).is_ok());
        assert!(identify_frame(br#"{"token":"t","intents":1}"#).is_ok());
        assert!(identify_frame(b"[]").is_err());
    }
}
