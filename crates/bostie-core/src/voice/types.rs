use std::collections::HashSet;
use std::fmt;
use std::ops::RangeInclusive;
use std::path::PathBuf;
use std::time::Duration;

use bytes::Bytes;

use crate::error::CoreError;
use crate::ids::{ChannelId, GuildId, Handle, ShardId, UserId};

pub const SAMPLE_RATE: u32 = 48_000;
pub(crate) const NANOS_PER_SECOND: u128 = 1_000_000_000;
pub const CHANNELS: usize = 2;
pub(crate) const FRAME_48K: u32 = 960;
pub const FRAME_SAMPLES: usize = FRAME_48K as usize;
pub const FRAME_LEN: usize = FRAME_SAMPLES * CHANNELS;
pub const FRAME: Duration = Duration::from_millis(20);
pub const SILENCE_FRAME: [u8; 3] = [0xF8, 0xFF, 0xFE];
pub const VOICE_GATEWAY_VERSION: u8 = 8;
pub const MAX_DAVE_PROTOCOL_VERSION: u16 = 1;
pub const DECODE_RATES: [u32; 5] = [8_000, 12_000, 16_000, 24_000, 48_000];
pub const MAX_JITTER: Duration = Duration::from_secs(1);
pub const SPEECH_HANGOVERS: RangeInclusive<Duration> = FRAME..=Duration::from_secs(10);
pub const BITRATES: RangeInclusive<u32> = 500..=512_000;
pub const MAX_COMPLEXITY: u8 = 10;
pub const RATES: RangeInclusive<u32> = 8_000..=192_000;
pub const MAX_RESAMPLER_BLOCK: u32 = 960;
pub const BUFFERS: RangeInclusive<Duration> = FRAME..=Duration::from_secs(60);
pub const MAX_BANDS: usize = 16;
pub const BAND_FREQUENCIES: RangeInclusive<f32> = 20.0..=20_000.0;
pub const MAX_GAIN: f32 = 24.0;
pub const SPEEDS: RangeInclusive<f32> = 0.5..=2.0;

const MAX_PACKET_LOSS: u8 = 100;

pub(crate) fn samples_in(duration: Duration, rate: u32) -> u64 {
    let samples = duration.as_nanos().saturating_mul(u128::from(rate)) / NANOS_PER_SECOND;
    u64::try_from(samples).unwrap_or(u64::MAX)
}

pub(crate) fn duration_of(samples: u64, rate: u32) -> Duration {
    let nanos = u128::from(samples) * NANOS_PER_SECOND / u128::from(rate);
    Duration::from_nanos(u64::try_from(nanos).unwrap_or(u64::MAX))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VoiceStatus {
    Connecting,
    Ready,
    Resuming,
    Reconnecting,
    Disconnected,
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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Direction {
    Send,
    Receive,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Speaking {
    Started,
    Stopped(Duration),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Application {
    Audio,
    Voip,
    LowDelay,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Bandwidth {
    Auto,
    Narrow,
    Medium,
    Wide,
    SuperWide,
    Full,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SignalType {
    Auto,
    Voice,
    Music,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EncoderSettings {
    pub bitrate: u32,
    pub application: Application,
    pub fec: bool,
    pub packet_loss: u8,
    pub complexity: u8,
    pub bandwidth: Bandwidth,
    pub signal: SignalType,
}

impl EncoderSettings {
    pub(crate) fn validate(self) -> Result<(), CoreError> {
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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PcmFormat {
    pub rate: u32,
    pub channels: u8,
    pub sample: SampleFormat,
}

impl PcmFormat {
    pub(crate) fn validate(self) -> Result<(), CoreError> {
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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StallPolicy {
    Hold,
    Drop,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SinkOptions {
    pub format: SinkFormat,
    pub buffer: Duration,
    pub volume: f32,
    pub stall: StallPolicy,
    pub after: Option<Handle>,
    pub crossfade: Duration,
}

impl SinkOptions {
    pub(crate) fn validate(&self) -> Result<(), CoreError> {
        if let SinkFormat::Pcm(format) = self.format {
            format.validate()?;
        }
        validate_buffer(self.buffer)?;
        if self.crossfade > *BUFFERS.end() {
            return Err(CoreError::InvalidRequest(format!(
                "crossfade must be at most {:?}, got {:?}",
                BUFFERS.end(),
                self.crossfade
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

#[derive(Debug, Clone, PartialEq)]
pub struct Filters {
    pub bands: Vec<(f32, f32)>,
    pub bass: f32,
    pub speed: f32,
    pub pitch: f32,
}

impl Filters {
    pub(crate) fn validate(&self) -> Result<(), CoreError> {
        let invalid = |text: String| Err(CoreError::InvalidRequest(text));
        if self.bands.len() > MAX_BANDS {
            return invalid(format!(
                "an equalizer takes at most {MAX_BANDS} bands, got {}",
                self.bands.len()
            ));
        }
        let gains = -MAX_GAIN..=MAX_GAIN;
        for &(frequency, gain) in &self.bands {
            if !BAND_FREQUENCIES.contains(&frequency) {
                return invalid(format!(
                    "a band frequency must be from {} to {} Hz, got {frequency}",
                    BAND_FREQUENCIES.start(),
                    BAND_FREQUENCIES.end()
                ));
            }
            if !gains.contains(&gain) {
                return invalid(format!(
                    "a band gain must be from -{MAX_GAIN} to {MAX_GAIN} dB, got {gain}"
                ));
            }
        }
        if !gains.contains(&self.bass) {
            return invalid(format!(
                "bass must be from -{MAX_GAIN} to {MAX_GAIN} dB, got {}",
                self.bass
            ));
        }
        for (name, value) in [("speed", self.speed), ("pitch", self.pitch)] {
            if !SPEEDS.contains(&value) {
                return invalid(format!(
                    "{name} must be from {} to {}, got {value}",
                    SPEEDS.start(),
                    SPEEDS.end()
                ));
            }
        }
        Ok(())
    }

    #[expect(clippy::float_cmp)]
    pub(crate) fn is_neutral(&self) -> bool {
        self.bands.iter().all(|&(_, gain)| gain == 0.0)
            && self.bass == 0.0
            && self.speed == 1.0
            && self.pitch == 1.0
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReceiveFormat {
    Pcm(PcmFormat),
    Mixed(PcmFormat),
    Opus,
    Packets,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Overflow {
    DropOldest,
    DropNewest,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReceiveStall {
    Wait,
    End,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReceiveOptions {
    pub format: ReceiveFormat,
    pub users: Option<HashSet<UserId>>,
    pub exclude: HashSet<UserId>,
    pub include_self: bool,
    pub buffer: Duration,
    pub jitter: Duration,
    pub markers: bool,
    pub overflow: Overflow,
    pub stall: ReceiveStall,
}

impl ReceiveOptions {
    pub(crate) fn validate(&self) -> Result<(), CoreError> {
        if let ReceiveFormat::Pcm(format) | ReceiveFormat::Mixed(format) = self.format {
            format.validate()?;
            if !DECODE_RATES.contains(&format.rate) {
                return Err(CoreError::InvalidRequest(format!(
                    "receive decodes at 8, 12, 16, 24 or 48 kHz, got {}; \
                     resample other rates yourself",
                    format.rate
                )));
            }
        }
        validate_buffer(self.buffer)?;
        if self.jitter > MAX_JITTER {
            return Err(CoreError::InvalidRequest(format!(
                "jitter must be at most {MAX_JITTER:?}, got {:?}",
                self.jitter
            )));
        }
        if self.markers && matches!(self.format, ReceiveFormat::Mixed(_)) {
            return Err(CoreError::InvalidRequest(
                "markers=True needs per-user frames; a mix has none".to_owned(),
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct ReceiveTuning {
    pub(crate) hold: Duration,
    pub(crate) hold_packets: usize,
    pub(crate) unmapped_ssrcs: usize,
    pub(crate) arrivals: usize,
    pub(crate) stream_idle: Duration,
    pub(crate) max_conceal: Duration,
    pub(crate) replay_window: u32,
    pub(crate) dave_retries: usize,
    pub(crate) dave_budget: Duration,
    pub(crate) decrypt_tolerance: u32,
    pub(crate) dave_grace: Duration,
    pub(crate) downgrade_expiry: u32,
    pub(crate) upgrade_passthrough: u32,
}

impl Default for ReceiveTuning {
    fn default() -> Self {
        Self {
            hold: Duration::from_secs(1),
            hold_packets: 50,
            unmapped_ssrcs: 16,
            arrivals: 2_048,
            stream_idle: Duration::from_secs(30),
            max_conceal: Duration::from_millis(100),
            replay_window: 4_096,
            dave_retries: 200,
            dave_budget: Duration::from_micros(200),
            decrypt_tolerance: 36,
            dave_grace: Duration::from_secs(10),
            downgrade_expiry: 24,
            upgrade_passthrough: 10,
        }
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

fn validate_buffer(buffer: Duration) -> Result<(), CoreError> {
    if BUFFERS.contains(&buffer) {
        return Ok(());
    }
    Err(CoreError::InvalidRequest(format!(
        "buffer must be from {:?} to {:?}, got {buffer:?}",
        BUFFERS.start(),
        BUFFERS.end(),
    )))
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
            bandwidth: Bandwidth::Auto,
            signal: SignalType::Auto,
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
            after: None,
            crossfade: Duration::ZERO,
        }
    }

    fn receive(format: ReceiveFormat) -> ReceiveOptions {
        ReceiveOptions {
            format,
            users: None,
            exclude: HashSet::new(),
            include_self: false,
            buffer: Duration::from_secs(2),
            jitter: Duration::from_millis(100),
            markers: false,
            overflow: Overflow::DropOldest,
            stall: ReceiveStall::Wait,
        }
    }

    fn rejection(result: Result<(), CoreError>) -> String {
        match result {
            Err(CoreError::InvalidRequest(message)) => message,
            other => panic!("expected InvalidRequest, got {other:?}"),
        }
    }

    #[test]
    fn encoder_settings_are_checked_against_their_bounds() {
        let with = |bitrate, packet_loss, complexity| EncoderSettings {
            bitrate,
            packet_loss,
            complexity,
            ..encoder()
        };

        for settings in [encoder(), with(500, 0, 0), with(512_000, 100, 10)] {
            assert!(settings.validate().is_ok(), "{settings:?}");
        }
        for (settings, field) in [
            (with(499, 10, 10), "bitrate"),
            (with(512_001, 10, 10), "bitrate"),
            (with(64_000, 101, 10), "packet loss"),
            (with(64_000, 10, 11), "complexity"),
        ] {
            assert!(
                rejection(settings.validate()).contains(field),
                "{settings:?}"
            );
        }
    }

    #[test]
    fn filters_are_checked_against_their_bounds() {
        let with = |bands: Vec<(f32, f32)>, bass, speed, pitch| Filters {
            bands,
            bass,
            speed,
            pitch,
        };
        let neutral = || with(Vec::new(), 0.0, 1.0, 1.0);

        for filters in [
            neutral(),
            with(vec![(20.0, -24.0); 16], 24.0, 0.5, 2.0),
            with(vec![(20_000.0, 24.0)], -24.0, 2.0, 0.5),
        ] {
            assert!(filters.validate().is_ok(), "{filters:?}");
        }
        for (filters, field) in [
            (
                with(vec![(100.0, 0.0); 17], 0.0, 1.0, 1.0),
                "at most 16 bands",
            ),
            (with(vec![(19.9, 0.0)], 0.0, 1.0, 1.0), "band frequency"),
            (with(vec![(100.0, 24.1)], 0.0, 1.0, 1.0), "band gain"),
            (with(Vec::new(), -24.1, 1.0, 1.0), "bass"),
            (with(Vec::new(), 0.0, 2.1, 1.0), "speed"),
            (with(Vec::new(), 0.0, 1.0, 0.49), "pitch"),
        ] {
            assert!(rejection(filters.validate()).contains(field), "{filters:?}");
        }
    }

    #[test]
    fn pcm_formats_are_checked_against_their_bounds() {
        let refused: [(u32, u8, &[&str]); 5] = [
            (8_016, 2, &["gcd(rate, 48000)", "8016"]),
            (7_999, 2, &["from 8000 to 192000 Hz"]),
            (192_001, 2, &["from 8000 to 192000 Hz"]),
            (48_000, 0, &["channels"]),
            (48_000, 3, &["channels"]),
        ];

        for (rate, channels) in [
            (8_000, 1),
            (8_050, 2),
            (11_025, 1),
            (44_100, 2),
            (48_000, 1),
            (192_000, 2),
        ] {
            assert!(pcm(rate, channels).validate().is_ok(), "{rate} Hz");
        }
        for (rate, channels, fragments) in refused {
            let message = rejection(pcm(rate, channels).validate());
            for fragment in fragments {
                assert!(message.contains(fragment), "{message}");
            }
        }
    }

    #[test]
    fn sink_options_are_checked_against_their_bounds() {
        let opus = |buffer, volume| sink(SinkFormat::Opus, buffer, volume);
        let long = Duration::from_secs(60);

        for options in [
            opus(FRAME, 0.0),
            sink(SinkFormat::Pcm(pcm(24_000, 1)), long, 2.5),
        ] {
            assert!(options.validate().is_ok(), "{options:?}");
        }
        for (options, field) in [
            (opus(Duration::ZERO, 1.0), "buffer"),
            (opus(Duration::from_millis(19), 1.0), "buffer"),
            (opus(long + Duration::from_nanos(1), 1.0), "buffer"),
            (opus(FRAME, -0.1), "volume"),
            (opus(FRAME, f32::NAN), "volume"),
            (opus(FRAME, f32::INFINITY), "volume"),
            (opus(FRAME, f32::NEG_INFINITY), "volume"),
            (sink(SinkFormat::Pcm(pcm(8_001, 2)), FRAME, 1.0), "gcd"),
        ] {
            assert!(rejection(options.validate()).contains(field), "{options:?}");
        }
    }

    #[test]
    fn receive_options_are_checked_against_their_bounds_and_the_decode_rates() {
        let stereo = ReceiveFormat::Pcm(pcm(48_000, 2));
        let mixed = ReceiveFormat::Mixed(pcm(48_000, 2));

        let accepted = DECODE_RATES
            .map(|rate| receive(ReceiveFormat::Pcm(pcm(rate, 1))))
            .into_iter()
            .chain([
                ReceiveOptions {
                    users: Some(HashSet::new()),
                    jitter: MAX_JITTER,
                    ..receive(ReceiveFormat::Packets)
                },
                ReceiveOptions {
                    jitter: Duration::ZERO,
                    ..receive(mixed)
                },
                ReceiveOptions {
                    markers: true,
                    ..receive(ReceiveFormat::Opus)
                },
            ]);
        for options in accepted {
            assert!(options.validate().is_ok(), "{options:?}");
        }
        for (options, fragment) in [
            (
                receive(ReceiveFormat::Pcm(pcm(44_100, 2))),
                "receive decodes at 8, 12, 16, 24 or 48 kHz, got 44100",
            ),
            (receive(ReceiveFormat::Pcm(pcm(16_000, 3))), "channels"),
            (
                receive(ReceiveFormat::Mixed(pcm(44_100, 2))),
                "receive decodes at 8, 12, 16, 24 or 48 kHz, got 44100",
            ),
            (
                ReceiveOptions {
                    markers: true,
                    ..receive(mixed)
                },
                "markers=True needs per-user frames",
            ),
            (
                ReceiveOptions {
                    jitter: MAX_JITTER + Duration::from_nanos(1),
                    ..receive(stereo)
                },
                "jitter",
            ),
            (
                ReceiveOptions {
                    buffer: Duration::from_secs(60) + Duration::from_nanos(1),
                    ..receive(stereo)
                },
                "buffer",
            ),
        ] {
            assert!(
                rejection(options.validate()).contains(fragment),
                "{options:?}"
            );
        }
    }
}
