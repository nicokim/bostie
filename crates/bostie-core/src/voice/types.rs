use std::fmt;
use std::ops::RangeInclusive;
use std::path::PathBuf;
use std::time::Duration;

use bytes::Bytes;

use crate::error::CoreError;
use crate::ids::{ChannelId, GuildId, ShardId, UserId};

pub const SAMPLE_RATE: u32 = 48_000;
pub const CHANNELS: usize = 2;
pub const FRAME_SAMPLES: usize = 960;
pub const FRAME_LEN: usize = FRAME_SAMPLES * CHANNELS;
pub const FRAME: Duration = Duration::from_millis(20);
pub const SILENCE_FRAME: [u8; 3] = [0xF8, 0xFF, 0xFE];
pub const VOICE_GATEWAY_VERSION: u8 = 8;
pub const MAX_DAVE_PROTOCOL_VERSION: u16 = 1;

const BITRATES: RangeInclusive<u32> = 500..=512_000;
const MAX_PACKET_LOSS: u8 = 100;
const MAX_COMPLEXITY: u8 = 10;
const RATES: RangeInclusive<u32> = 8_000..=192_000;
const MAX_RESAMPLER_BLOCK: u32 = 960;
const BUFFERS: RangeInclusive<Duration> = FRAME..=Duration::from_secs(60);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VoiceStatus {
    Connecting,
    Ready,
    Resuming,
    Reconnecting,
    Disconnected,
}

impl VoiceStatus {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Connecting => "connecting",
            Self::Ready => "ready",
            Self::Resuming => "resuming",
            Self::Reconnecting => "reconnecting",
            Self::Disconnected => "disconnected",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DisconnectReason {
    Requested,
    Removed,
    CallTerminated,
    RateLimited,
    Timeout,
    SessionInvalid,
    AuthenticationFailed,
    DaveRequired,
    DaveFailed,
    ProtocolError,
    Closed,
}

impl DisconnectReason {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Requested => "requested",
            Self::Removed => "removed",
            Self::CallTerminated => "call_terminated",
            Self::RateLimited => "rate_limited",
            Self::Timeout => "timeout",
            Self::SessionInvalid => "session_invalid",
            Self::AuthenticationFailed => "authentication_failed",
            Self::DaveRequired => "dave_required",
            Self::DaveFailed => "dave_failed",
            Self::ProtocolError => "protocol_error",
            Self::Closed => "closed",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Direction {
    Send,
    Receive,
}

impl Direction {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Send => "send",
            Self::Receive => "receive",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Application {
    Audio,
    Voip,
    LowDelay,
}

impl Application {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Audio => "audio",
            Self::Voip => "voip",
            Self::LowDelay => "lowdelay",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct EncoderSettings {
    pub bitrate: u32,
    pub application: Application,
    pub fec: bool,
    pub packet_loss: u8,
    pub complexity: u8,
}

impl EncoderSettings {
    pub fn validate(&self) -> Result<(), CoreError> {
        if !BITRATES.contains(&self.bitrate) {
            return Err(CoreError::InvalidRequest(format!(
                "bitrate must be from {} to {} bits per second, got {}",
                BITRATES.start(),
                BITRATES.end(),
                self.bitrate
            )));
        }
        if self.packet_loss > MAX_PACKET_LOSS {
            return Err(CoreError::InvalidRequest(format!(
                "packet loss must be at most {MAX_PACKET_LOSS} percent, got {}",
                self.packet_loss
            )));
        }
        if self.complexity > MAX_COMPLEXITY {
            return Err(CoreError::InvalidRequest(format!(
                "complexity must be at most {MAX_COMPLEXITY}, got {}",
                self.complexity
            )));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SampleFormat {
    S16Le,
    F32Le,
}

impl SampleFormat {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::S16Le => "s16le",
            Self::F32Le => "f32le",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PcmFormat {
    pub rate: u32,
    pub channels: u8,
    pub sample: SampleFormat,
}

impl PcmFormat {
    pub fn validate(&self) -> Result<(), CoreError> {
        if !RATES.contains(&self.rate) {
            return Err(CoreError::InvalidRequest(format!(
                "sample rate must be from {} to {} Hz, got {}",
                RATES.start(),
                RATES.end(),
                self.rate
            )));
        }
        if SAMPLE_RATE / gcd(self.rate, SAMPLE_RATE) > MAX_RESAMPLER_BLOCK {
            return Err(CoreError::InvalidRequest(format!(
                "sample rate {} Hz does not resample to {SAMPLE_RATE} Hz in 20 ms blocks: \
                 {SAMPLE_RATE} / gcd(rate, {SAMPLE_RATE}) must be at most {MAX_RESAMPLER_BLOCK}",
                self.rate
            )));
        }
        if !matches!(self.channels, 1 | 2) {
            return Err(CoreError::InvalidRequest(format!(
                "channels must be 1 or 2, got {}",
                self.channels
            )));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SinkFormat {
    Pcm(PcmFormat),
    Opus,
}

impl SinkFormat {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Pcm(_) => "pcm",
            Self::Opus => "opus",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StallPolicy {
    Hold,
    Drop,
}

impl StallPolicy {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Hold => "hold",
            Self::Drop => "drop",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SinkOptions {
    pub format: SinkFormat,
    pub buffer: Duration,
    pub volume: f32,
    pub stall: StallPolicy,
}

impl SinkOptions {
    pub fn validate(&self) -> Result<(), CoreError> {
        if let SinkFormat::Pcm(format) = self.format {
            format.validate()?;
        }
        if !BUFFERS.contains(&self.buffer) {
            return Err(CoreError::InvalidRequest(format!(
                "buffer must be from {:?} to {:?}, got {:?}",
                BUFFERS.start(),
                BUFFERS.end(),
                self.buffer
            )));
        }
        if !(self.volume.is_finite() && self.volume >= 0.0) {
            return Err(CoreError::InvalidRequest(format!(
                "volume must be a finite number of at least 0, got {}",
                self.volume
            )));
        }
        Ok(())
    }
}

#[derive(Debug, Clone)]
pub struct ConnectRequest {
    pub guild: GuildId,
    pub channel: ChannelId,
    pub shard: ShardId,
    pub user: UserId,
    pub self_mute: bool,
    pub self_deaf: bool,
    pub timeout: Duration,
    pub server_change_delay: Duration,
    pub encoder: EncoderSettings,
    pub speaking: u8,
}

#[derive(Clone)]
pub enum SourceSpec {
    File(PathBuf),
    Bytes {
        data: Bytes,
        hint: Option<String>,
    },
    Url {
        url: String,
        headers: Vec<(String, String)>,
    },
}

impl fmt::Debug for SourceSpec {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::File(path) => f.debug_tuple("File").field(path).finish(),
            Self::Bytes { data, hint } => f
                .debug_struct("Bytes")
                .field("len", &data.len())
                .field("hint", hint)
                .finish(),
            Self::Url { url, headers } => f
                .debug_struct("Url")
                .field("url", url)
                .field(
                    "headers",
                    &headers.iter().map(|(name, _)| name).collect::<Vec<_>>(),
                )
                .finish(),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProposalsOp {
    Append,
    Revoke,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DaveOp {
    PrepareTransition {
        transition_id: u16,
        protocol_version: u16,
    },
    ExecuteTransition {
        transition_id: u16,
    },
    PrepareEpoch {
        transition_id: Option<u16>,
        epoch: u64,
        protocol_version: u16,
    },
    ExternalSender(Bytes),
    Proposals {
        operation: ProposalsOp,
        data: Bytes,
    },
    AnnounceCommit {
        transition_id: u16,
        commit: Bytes,
    },
    Welcome {
        transition_id: u16,
        welcome: Bytes,
    },
}

const fn gcd(mut a: u32, mut b: u32) -> u32 {
    while b != 0 {
        (a, b) = (b, a % b);
    }
    a
}

#[cfg(test)]
mod tests {
    use super::*;

    fn encoder() -> EncoderSettings {
        EncoderSettings {
            bitrate: 64_000,
            application: Application::Audio,
            fec: true,
            packet_loss: 10,
            complexity: 10,
        }
    }

    #[test]
    fn source_debug_hides_header_values_and_payload_bytes() {
        let url = SourceSpec::Url {
            url: "https://example.com/a.ogg".to_owned(),
            headers: vec![("Authorization".to_owned(), "Bearer secret".to_owned())],
        };
        let bytes = SourceSpec::Bytes {
            data: Bytes::from_static(b"payload"),
            hint: None,
        };
        let url_debug = format!("{url:?}");
        assert!(url_debug.contains("Authorization"));
        assert!(!url_debug.contains("secret"));
        assert_eq!(format!("{bytes:?}"), "Bytes { len: 7, hint: None }");
    }

    fn pcm(rate: u32, channels: u8) -> PcmFormat {
        PcmFormat {
            rate,
            channels,
            sample: SampleFormat::S16Le,
        }
    }

    fn sink(format: SinkFormat, buffer: Duration, volume: f32) -> SinkOptions {
        SinkOptions {
            format,
            buffer,
            volume,
            stall: StallPolicy::Hold,
        }
    }

    fn rejection(result: Result<(), CoreError>) -> String {
        match result {
            Err(CoreError::InvalidRequest(message)) => message,
            other => panic!("expected InvalidRequest, got {other:?}"),
        }
    }

    #[test]
    fn names_cross_to_python_in_lowercase_snake_case() {
        let statuses = [
            VoiceStatus::Connecting,
            VoiceStatus::Ready,
            VoiceStatus::Resuming,
            VoiceStatus::Reconnecting,
            VoiceStatus::Disconnected,
        ]
        .map(VoiceStatus::as_str);
        let reasons = [
            DisconnectReason::Requested,
            DisconnectReason::Removed,
            DisconnectReason::CallTerminated,
            DisconnectReason::RateLimited,
            DisconnectReason::Timeout,
            DisconnectReason::SessionInvalid,
            DisconnectReason::AuthenticationFailed,
            DisconnectReason::DaveRequired,
            DisconnectReason::DaveFailed,
            DisconnectReason::ProtocolError,
            DisconnectReason::Closed,
        ]
        .map(DisconnectReason::as_str);

        assert_eq!(
            statuses,
            [
                "connecting",
                "ready",
                "resuming",
                "reconnecting",
                "disconnected"
            ]
        );
        assert_eq!(
            reasons,
            [
                "requested",
                "removed",
                "call_terminated",
                "rate_limited",
                "timeout",
                "session_invalid",
                "authentication_failed",
                "dave_required",
                "dave_failed",
                "protocol_error",
                "closed",
            ]
        );
        assert_eq!(
            [Direction::Send, Direction::Receive].map(Direction::as_str),
            ["send", "receive"]
        );
        assert_eq!(
            [Application::Audio, Application::Voip, Application::LowDelay].map(Application::as_str),
            ["audio", "voip", "lowdelay"]
        );
        assert_eq!(
            [SampleFormat::S16Le, SampleFormat::F32Le].map(SampleFormat::as_str),
            ["s16le", "f32le"]
        );
        assert_eq!(
            [StallPolicy::Hold, StallPolicy::Drop].map(StallPolicy::as_str),
            ["hold", "drop"]
        );
        assert_eq!(
            [SinkFormat::Pcm(pcm(48_000, 2)), SinkFormat::Opus].map(SinkFormat::as_str),
            ["pcm", "opus"]
        );
    }

    #[test]
    fn encoder_settings_accept_their_bounds() {
        for settings in [
            encoder(),
            EncoderSettings {
                bitrate: 500,
                packet_loss: 0,
                complexity: 0,
                ..encoder()
            },
            EncoderSettings {
                bitrate: 512_000,
                packet_loss: 100,
                complexity: 10,
                ..encoder()
            },
        ] {
            assert!(settings.validate().is_ok(), "{settings:?}");
        }
    }

    #[test]
    fn encoder_settings_reject_values_past_their_bounds() {
        let low_bitrate = EncoderSettings {
            bitrate: 499,
            ..encoder()
        };
        let high_bitrate = EncoderSettings {
            bitrate: 512_001,
            ..encoder()
        };
        let packet_loss = EncoderSettings {
            packet_loss: 101,
            ..encoder()
        };
        let complexity = EncoderSettings {
            complexity: 11,
            ..encoder()
        };

        assert!(rejection(low_bitrate.validate()).contains("bitrate"));
        assert!(rejection(high_bitrate.validate()).contains("bitrate"));
        assert!(rejection(packet_loss.validate()).contains("packet loss"));
        assert!(rejection(complexity.validate()).contains("complexity"));
    }

    #[test]
    fn common_rates_pass_the_resampler_rule() {
        for rate in [
            8_000, 11_025, 12_000, 16_000, 22_050, 24_000, 32_000, 44_100, 48_000, 88_200, 96_000,
            176_400, 192_000,
        ] {
            for channels in [1, 2] {
                assert!(pcm(rate, channels).validate().is_ok(), "{rate} Hz");
            }
        }
    }

    #[test]
    fn rates_whose_resampler_block_exceeds_a_frame_name_the_rule() {
        for rate in [8_001, 44_101] {
            let message = rejection(pcm(rate, 2).validate());

            assert!(message.contains("gcd(rate, 48000)"), "{message}");
            assert!(message.contains(&rate.to_string()), "{message}");
        }
    }

    #[test]
    fn rates_outside_8_to_192_khz_are_rejected() {
        for rate in [0, 7_999, 192_001] {
            assert!(rejection(pcm(rate, 2).validate()).contains("from 8000 to 192000 Hz"));
        }
    }

    #[test]
    fn only_mono_and_stereo_are_accepted() {
        for channels in [0, 3, 6] {
            assert!(rejection(pcm(48_000, channels).validate()).contains("channels"));
        }
    }

    #[test]
    fn sink_options_accept_their_bounds() {
        for options in [
            sink(SinkFormat::Opus, FRAME, 0.0),
            sink(
                SinkFormat::Pcm(pcm(24_000, 1)),
                Duration::from_secs(60),
                2.5,
            ),
        ] {
            assert!(options.validate().is_ok(), "{options:?}");
        }
    }

    #[test]
    fn sink_options_reject_buffers_outside_20_ms_to_60_s() {
        for buffer in [
            Duration::ZERO,
            Duration::from_millis(19),
            Duration::from_secs(60) + Duration::from_nanos(1),
        ] {
            assert!(rejection(sink(SinkFormat::Opus, buffer, 1.0).validate()).contains("buffer"));
        }
    }

    #[test]
    fn sink_options_reject_negative_and_non_finite_volumes() {
        for volume in [-0.1, f32::NAN, f32::INFINITY, f32::NEG_INFINITY] {
            assert!(rejection(sink(SinkFormat::Opus, FRAME, volume).validate()).contains("volume"));
        }
    }

    #[test]
    fn sink_options_check_their_pcm_format() {
        let options = sink(SinkFormat::Pcm(pcm(8_001, 2)), FRAME, 1.0);

        assert!(rejection(options.validate()).contains("gcd"));
    }
}
