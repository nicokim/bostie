use bostie_core::voice::types::{
    Application, Bandwidth, Direction, DisconnectReason, Overflow, ReceiveStall, SampleFormat,
    SignalType, StallPolicy, VoiceStatus,
};
use bostie_core::voice::{Ended, SourceKind};
use bostie_core::{CoreError, FailureKind};
use pyo3::prelude::*;

use crate::Error;
use crate::voice::{ReceiveKind, SinkKind};

pub(crate) trait Named: Copy + 'static {
    const OPTION: &'static str;
    const NAMES: &'static [(&'static str, Self)];

    fn name(self) -> &'static str;
}

#[derive(Clone, Copy)]
pub(crate) struct Choice<T>(pub(crate) T);

impl<T: Named> FromPyObject<'_, '_> for Choice<T> {
    type Error = PyErr;

    fn extract(obj: Borrowed<'_, '_, PyAny>) -> PyResult<Self> {
        let name = <&str>::extract(obj)?;
        T::NAMES
            .iter()
            .find(|(known, _)| *known == name)
            .map(|&(_, value)| Self(value))
            .ok_or_else(|| unknown(T::OPTION, name).into())
    }
}

pub(crate) fn names<T: Named>() -> Vec<&'static str> {
    T::NAMES.iter().map(|&(name, _)| name).collect()
}

fn unknown(name: &str, value: &str) -> Error {
    CoreError::InvalidRequest(format!("unknown {name} {value:?}")).into()
}

macro_rules! named {
    ($kind:ty, $option:literal, { $($name:literal => $variant:ident),+ $(,)? }) => {
        impl Named for $kind {
            const OPTION: &'static str = $option;
            const NAMES: &'static [(&'static str, Self)] = &[$(($name, Self::$variant)),+];

            fn name(self) -> &'static str {
                match self {
                    $(Self::$variant => $name),+
                }
            }
        }
    };
}

named!(Application, "application", {
    "audio" => Audio,
    "voip" => Voip,
    "lowdelay" => LowDelay,
});

named!(Bandwidth, "bandwidth", {
    "auto" => Auto,
    "narrow" => Narrow,
    "medium" => Medium,
    "wide" => Wide,
    "superwide" => SuperWide,
    "full" => Full,
});

named!(SignalType, "signal_type", {
    "auto" => Auto,
    "voice" => Voice,
    "music" => Music,
});

named!(SampleFormat, "sample", {
    "s16le" => S16Le,
    "f32le" => F32Le,
});

named!(SinkKind, "kind", {
    "pcm" => Pcm,
    "opus" => Opus,
});

named!(StallPolicy, "stall", {
    "hold" => Hold,
    "drop" => Drop,
});

named!(ReceiveKind, "kind", {
    "pcm" => Pcm,
    "opus" => Opus,
    "packets" => Packets,
});

named!(Overflow, "overflow", {
    "drop_oldest" => DropOldest,
    "drop_newest" => DropNewest,
});

named!(ReceiveStall, "stall", {
    "wait" => Wait,
    "end" => End,
});

named!(VoiceStatus, "voice state", {
    "connecting" => Connecting,
    "ready" => Ready,
    "resuming" => Resuming,
    "reconnecting" => Reconnecting,
    "disconnected" => Disconnected,
});

named!(DisconnectReason, "disconnect reason", {
    "requested" => Requested,
    "removed" => Removed,
    "call_terminated" => CallTerminated,
    "rate_limited" => RateLimited,
    "timeout" => Timeout,
    "session_invalid" => SessionInvalid,
    "authentication_failed" => AuthenticationFailed,
    "dave_required" => DaveRequired,
    "dave_failed" => DaveFailed,
    "protocol_error" => ProtocolError,
    "closed" => Closed,
});

named!(Direction, "direction", {
    "send" => Send,
    "receive" => Receive,
});

named!(Ended, "closed reason", {
    "closed" => Closed,
    "disconnected" => Disconnected,
    "moved" => Moved,
    "reconnecting" => Reconnecting,
});

named!(FailureKind, "failure kind", {
    "transport" => Transport,
    "invalid_token" => InvalidToken,
    "no_token" => NoToken,
    "request_budget" => RequestBudget,
    "bug" => Bug,
});

named!(SourceKind, "source kind", {
    "pcm" => Pcm,
    "opus" => Opus,
});
