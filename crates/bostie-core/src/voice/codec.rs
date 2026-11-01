use std::ops::Range;
use std::os::raw::c_int;

use bytes::{Bytes, BytesMut};
use opus::{Bitrate, Channels};

use crate::error::CoreError;
use crate::voice::convert::Converter;
use crate::voice::types::{
    Application, EncoderSettings, FRAME_LEN, FRAME_SAMPLES, PcmFormat, SAMPLE_RATE, SampleFormat,
};

pub const MAX_PACKET: usize = 1_275;
const MAX_DECODE: u32 = 5_760;

pub fn packet_samples(packet: &[u8]) -> Option<u32> {
    if !fits_libopus(packet) || !is_well_framed(packet) {
        return None;
    }
    let samples = opus::packet::get_nb_samples(packet, SAMPLE_RATE).ok()?;
    u32::try_from(samples).ok().filter(|&samples| samples > 0)
}

pub fn is_stereo_20ms(packet: &[u8]) -> bool {
    fits_libopus(packet)
        && matches!(opus::packet::get_nb_channels(packet), Ok(Channels::Stereo))
        && matches!(
            opus::packet::get_nb_samples(packet, SAMPLE_RATE),
            Ok(FRAME_SAMPLES)
        )
}

pub fn received_samples(packet: &[u8]) -> Option<u32> {
    packet_samples(packet).filter(|_| packet.iter().any(|&byte| byte != 0))
}

fn fits_libopus(packet: &[u8]) -> bool {
    c_int::try_from(packet.len()).is_ok()
}

fn is_well_framed(packet: &[u8]) -> bool {
    let Some((&toc, body)) = packet.split_first() else {
        return false;
    };
    let last = match toc & 0x03 {
        0 => Some(body.len()),
        1 => (body.len() % 2 == 0).then_some(body.len() / 2),
        2 => frame_length(body)
            .and_then(|(length, used)| body.len().checked_sub(used)?.checked_sub(length)),
        _ => last_of_many(body),
    };
    last.is_some_and(|last| last <= MAX_PACKET)
}

fn last_of_many(body: &[u8]) -> Option<usize> {
    let (&header, mut rest) = body.split_first()?;
    let count = usize::from(header & 0x3F);
    if count == 0 {
        return None;
    }
    let mut len = rest.len();
    if header & 0x40 != 0 {
        loop {
            let (&padding, tail) = rest.split_first().filter(|_| len > 0)?;
            rest = tail;
            len = len.checked_sub(1 + usize::from(padding.min(254)))?;
            if padding < 255 {
                break;
            }
        }
    }
    if header & 0x80 == 0 {
        let last = len / count;
        return (last * count == len).then_some(last);
    }
    let mut lengths = rest.get(..len)?;
    let mut last = len;
    for _ in 1..count {
        let (length, used) = frame_length(lengths)?;
        lengths = lengths.get(used..).filter(|tail| tail.len() >= length)?;
        last = last.checked_sub(used + length)?;
    }
    Some(last)
}

fn frame_length(bytes: &[u8]) -> Option<(usize, usize)> {
    match *bytes {
        [first, ..] if first < 252 => Some((usize::from(first), 1)),
        [first, second, ..] => Some((usize::from(second) * 4 + usize::from(first), 2)),
        _ => None,
    }
}

pub struct Encoder {
    opus: opus::Encoder,
    settings: EncoderSettings,
    packet: [u8; MAX_PACKET],
}

impl Encoder {
    pub fn new(settings: EncoderSettings) -> Result<Self, CoreError> {
        settings.validate()?;
        let application = match settings.application {
            Application::Audio => opus::Application::Audio,
            Application::Voip => opus::Application::Voip,
            Application::LowDelay => opus::Application::LowDelay,
        };
        let mut opus =
            opus::Encoder::new(SAMPLE_RATE, Channels::Stereo, application).map_err(rejected)?;
        configure(&mut opus, &settings)?;
        Ok(Self {
            opus,
            settings,
            packet: [0; MAX_PACKET],
        })
    }

    #[cfg(test)]
    pub fn settings(&self) -> EncoderSettings {
        self.settings
    }

    pub fn set(&mut self, settings: EncoderSettings) -> Result<(), CoreError> {
        if settings.application != self.settings.application {
            *self = Self::new(settings)?;
            return Ok(());
        }
        settings.validate()?;
        configure(&mut self.opus, &settings)?;
        self.settings = settings;
        Ok(())
    }

    pub fn encode(&mut self, frame: &[f32]) -> Option<&[u8]> {
        match self.opus.encode_float(frame, &mut self.packet) {
            Ok(length) => self.packet.get(..length),
            Err(error) => {
                tracing::debug!(%error, "opus could not encode a frame");
                None
            }
        }
    }

    pub fn reset(&mut self) {
        if let Err(error) = self.opus.reset_state() {
            tracing::debug!(%error, "opus could not reset the encoder");
        }
    }
}

fn configure(opus: &mut opus::Encoder, settings: &EncoderSettings) -> Result<(), CoreError> {
    let bitrate = i32::try_from(settings.bitrate).unwrap_or(i32::MAX);
    opus.set_bitrate(Bitrate::Bits(bitrate)).map_err(rejected)?;
    opus.set_inband_fec(settings.fec).map_err(rejected)?;
    opus.set_packet_loss_perc(i32::from(settings.packet_loss))
        .map_err(rejected)?;
    opus.set_complexity(i32::from(settings.complexity))
        .map_err(rejected)
}

fn rejected(error: opus::Error) -> CoreError {
    CoreError::InvalidRequest(format!("libopus rejected the encoder settings: {error}"))
}

#[derive(Debug, thiserror::Error)]
#[error("opus could not decode a packet: {0}")]
pub struct DecodeError(#[from] opus::Error);

pub struct Decoder {
    inner: opus::Decoder,
    rate: u32,
    channels: u8,
}

impl Decoder {
    pub fn new(rate: u32, channels: u8) -> Result<Self, CoreError> {
        let layout = match channels {
            1 => Channels::Mono,
            2 => Channels::Stereo,
            _ => {
                return Err(CoreError::InvalidRequest(format!(
                    "channels must be 1 or 2, got {channels}"
                )));
            }
        };
        let inner = opus::Decoder::new(rate, layout).map_err(|error| {
            CoreError::InvalidRequest(format!("libopus cannot decode at {rate} Hz: {error}"))
        })?;
        Ok(Self {
            inner,
            rate,
            channels,
        })
    }

    pub fn decode(&mut self, packet: &[u8], scratch: &mut Vec<f32>) -> Result<usize, DecodeError> {
        let output = self.output(scratch, MAX_DECODE);
        Ok(self.inner.decode_float(packet, output, false)?)
    }

    pub fn conceal(
        &mut self,
        next: Option<&[u8]>,
        samples48: u32,
        scratch: &mut Vec<f32>,
    ) -> Result<usize, DecodeError> {
        let output = self.output(scratch, samples48.min(MAX_DECODE));
        Ok(self
            .inner
            .decode_float(next.unwrap_or_default(), output, next.is_some())?)
    }

    pub fn reset(&mut self) {
        if let Err(error) = self.inner.reset_state() {
            tracing::debug!(%error, "opus could not reset the decoder");
        }
    }

    fn output<'a>(&self, scratch: &'a mut Vec<f32>, samples48: u32) -> &'a mut [f32] {
        let len = at_rate(samples48 as usize, self.rate) * usize::from(self.channels);
        if scratch.len() < len {
            scratch.resize(len, 0.0);
        }
        scratch.get_mut(..len).unwrap_or_default()
    }
}

pub fn split(samples: usize, rate: u32, channels: u8) -> impl Iterator<Item = Range<usize>> {
    let channels = usize::from(channels);
    let frame = (at_rate(FRAME_SAMPLES, rate) * channels).max(1);
    let total = samples.saturating_mul(channels);
    (0..total)
        .step_by(frame)
        .map(move |start| start..start.saturating_add(frame).min(total))
}

pub fn write_samples(pcm: &[f32], sample: SampleFormat, out: &mut BytesMut) {
    match sample {
        SampleFormat::S16Le => write_each(pcm, out, |value| to_s16(value).to_le_bytes()),
        SampleFormat::F32Le => write_each(pcm, out, f32::to_le_bytes),
    }
}

fn write_each<const N: usize>(pcm: &[f32], out: &mut BytesMut, convert: impl Fn(f32) -> [u8; N]) {
    let start = out.len();
    out.resize(start + pcm.len() * N, 0);
    let (samples, _) = out
        .get_mut(start..)
        .unwrap_or_default()
        .as_chunks_mut::<N>();
    for (bytes, &value) in samples.iter_mut().zip(pcm) {
        *bytes = convert(value);
    }
}

fn to_s16(value: f32) -> i16 {
    let scaled = value.clamp(-1.0, 1.0) * 32_767.0;
    (scaled + 0.5_f32.copysign(scaled)) as i16
}

fn at_rate(samples48: usize, rate: u32) -> usize {
    samples48 * rate as usize / SAMPLE_RATE as usize
}

pub struct FrameEncoder {
    converter: Converter,
    encoder: Encoder,
    pcm: Vec<f32>,
}

impl FrameEncoder {
    pub fn new(format: PcmFormat, settings: EncoderSettings) -> Result<Self, CoreError> {
        Ok(Self {
            converter: Converter::new(format)?,
            encoder: Encoder::new(settings)?,
            pcm: Vec::new(),
        })
    }

    pub fn encode(&mut self, data: &[u8]) -> Vec<Bytes> {
        self.converter.push(data, &mut self.pcm);
        self.packets()
    }

    pub fn flush(&mut self) -> Vec<Bytes> {
        self.converter.flush(&mut self.pcm);
        let partial = self.pcm.len() % FRAME_LEN;
        if partial > 0 {
            self.pcm.resize(self.pcm.len() + FRAME_LEN - partial, 0.0);
        }
        self.packets()
    }

    fn packets(&mut self) -> Vec<Bytes> {
        let (frames, _) = self.pcm.as_chunks::<FRAME_LEN>();
        let packets = frames
            .iter()
            .filter_map(|frame| self.encoder.encode(frame).map(Bytes::copy_from_slice))
            .collect();
        let used = frames.len() * FRAME_LEN;
        self.pcm.drain(..used);
        packets
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use std::f32::consts::TAU;

    use proptest::collection::vec;
    use proptest::prelude::*;

    use super::*;

    pub(crate) fn settings() -> EncoderSettings {
        EncoderSettings {
            bitrate: 64_000,
            application: Application::Audio,
            fec: true,
            packet_loss: 10,
            complexity: 10,
        }
    }

    pub(crate) fn packet(channels: Channels, frames: usize) -> Vec<u8> {
        let mut encoder =
            opus::Encoder::new(SAMPLE_RATE, channels, opus::Application::Audio).unwrap();
        let input: Vec<f32> = (0..frames)
            .flat_map(|frame| {
                let sample = (TAU * 440.0 * frame as f32 / SAMPLE_RATE as f32).sin() * 0.5;
                let right = if channels == Channels::Stereo {
                    -sample
                } else {
                    sample
                };
                [sample, right].into_iter().take(channels as usize)
            })
            .collect();
        encoder.encode_vec_float(&input, 4_000).unwrap()
    }

    #[test]
    fn packet_samples_comes_from_libopus_parsing() {
        assert_eq!(packet_samples(&packet(Channels::Stereo, 960)), Some(960));
        assert_eq!(packet_samples(&packet(Channels::Mono, 2_880)), Some(2_880));
        assert_eq!(packet_samples(&[]), None);
        assert_eq!(packet_samples(&[0x03]), None);
        assert_eq!(
            packet_samples(&[[0x00].as_slice(), &[0; 1_276]].concat()),
            None
        );
    }

    #[test]
    fn only_stereo_20_ms_packets_are_passthrough_shaped() {
        assert!(is_stereo_20ms(&packet(Channels::Stereo, 960)));
        assert!(!is_stereo_20ms(&packet(Channels::Mono, 960)));
        assert!(!is_stereo_20ms(&packet(Channels::Stereo, 2_880)));
        assert!(!is_stereo_20ms(&packet(Channels::Stereo, 480)));
        assert!(!is_stereo_20ms(&[]));
    }

    #[test]
    fn encoder_settings_reach_libopus() {
        let settings = EncoderSettings {
            bitrate: 96_000,
            application: Application::Voip,
            fec: false,
            packet_loss: 15,
            complexity: 5,
        };
        let mut encoder = Encoder::new(settings).unwrap();

        assert_eq!(encoder.opus.get_bitrate().unwrap(), Bitrate::Bits(96_000));
        assert_eq!(
            encoder.opus.get_application().unwrap(),
            opus::Application::Voip
        );
        assert!(!encoder.opus.get_inband_fec().unwrap());
        assert_eq!(encoder.opus.get_packet_loss_perc().unwrap(), 15);
        assert_eq!(encoder.opus.get_complexity().unwrap(), 5);
    }

    #[test]
    fn changing_the_application_rebuilds_the_encoder() {
        let mut encoder = Encoder::new(settings()).unwrap();
        encoder.encode(&[0.1; FRAME_LEN]).unwrap();

        encoder
            .set(EncoderSettings {
                application: Application::LowDelay,
                bitrate: 128_000,
                ..settings()
            })
            .unwrap();

        assert_eq!(
            encoder.opus.get_application().unwrap(),
            opus::Application::LowDelay
        );
        assert_eq!(encoder.opus.get_bitrate().unwrap(), Bitrate::Bits(128_000));
        assert_eq!(encoder.settings().application, Application::LowDelay);
    }

    #[test]
    fn other_changes_keep_the_encoder_and_apply_in_place() {
        let mut encoder = Encoder::new(settings()).unwrap();

        encoder
            .set(EncoderSettings {
                bitrate: 32_000,
                complexity: 3,
                ..settings()
            })
            .unwrap();

        assert_eq!(encoder.opus.get_bitrate().unwrap(), Bitrate::Bits(32_000));
        assert_eq!(encoder.opus.get_complexity().unwrap(), 3);
        assert!(
            encoder
                .set(EncoderSettings {
                    bitrate: 1,
                    ..settings()
                })
                .is_err()
        );
        assert_eq!(encoder.settings().bitrate, 32_000);
    }

    #[test]
    fn frame_encoder_turns_24_khz_mono_into_20_ms_packets_and_pads_the_last_one() {
        let format = PcmFormat {
            rate: 24_000,
            channels: 1,
            sample: SampleFormat::S16Le,
        };
        let mut encoder = FrameEncoder::new(format, settings()).unwrap();
        let second: Vec<u8> = (0..24_000)
            .flat_map(|frame| {
                let sample = (TAU * 440.0 * frame as f32 / 24_000.0).sin() * 16_000.0;
                (sample as i16).to_le_bytes()
            })
            .collect();

        let mut packets: Vec<Bytes> = second
            .chunks(960)
            .flat_map(|chunk| encoder.encode(chunk))
            .collect();
        packets.extend(encoder.flush());

        assert_eq!(packets.len(), 50);
        assert!(
            packets
                .iter()
                .all(|packet| packet_samples(packet) == Some(960))
        );
        assert!(encoder.flush().is_empty());

        let mut tail = encoder.encode(&second[..480]);
        tail.extend(encoder.flush());
        let tail: Vec<Option<u32>> = tail.iter().map(|packet| packet_samples(packet)).collect();
        assert_eq!(tail, [Some(960)]);
    }

    #[test]
    fn frame_encoder_rejects_formats_a_sink_would_reject() {
        let format = PcmFormat {
            rate: 8_001,
            channels: 2,
            sample: SampleFormat::S16Le,
        };

        assert!(FrameEncoder::new(format, settings()).is_err());
    }

    fn speech(frames: usize) -> Vec<Vec<u8>> {
        let mut encoder =
            opus::Encoder::new(SAMPLE_RATE, Channels::Mono, opus::Application::Voip).unwrap();
        encoder.set_bitrate(Bitrate::Bits(24_000)).unwrap();
        encoder.set_inband_fec(true).unwrap();
        encoder.set_packet_loss_perc(30).unwrap();
        (0..frames)
            .map(|frame| {
                let pitch = 120.0 + 15.0 * frame as f32;
                let input: Vec<f32> = (0..FRAME_SAMPLES)
                    .map(|index| {
                        let time = (frame * FRAME_SAMPLES + index) as f32 / SAMPLE_RATE as f32;
                        (1..6)
                            .map(|harmonic| (TAU * pitch * harmonic as f32 * time).sin() * 0.1)
                            .sum()
                    })
                    .collect();
                encoder.encode_vec_float(&input, 4_000).unwrap()
            })
            .collect()
    }

    fn decoder_after(history: &[Vec<u8>]) -> Decoder {
        let mut decoder = Decoder::new(16_000, 1).unwrap();
        let mut scratch = Vec::new();
        for packet in history {
            decoder.decode(packet, &mut scratch).unwrap();
        }
        decoder
    }

    fn output(samples: Result<usize, DecodeError>, scratch: &[f32]) -> Vec<f32> {
        scratch[..samples.unwrap()].to_vec()
    }

    #[test]
    fn a_lost_frame_uses_fec_from_the_next_packet_else_plc_of_the_previous_duration() {
        let packets = speech(12);
        let lost = 8;
        let mut scratch = Vec::new();
        let mut reference = decoder_after(&packets[..lost]);
        let mut with_fec = decoder_after(&packets[..lost]);
        let mut without = decoder_after(&packets[..lost]);

        let samples = reference.decode(&packets[lost], &mut scratch);
        let expected = output(samples, &scratch);
        let samples = with_fec.conceal(Some(&packets[lost + 1]), 960, &mut scratch);
        let fec = output(samples, &scratch);
        let samples = without.conceal(None, 960, &mut scratch);
        let plc = output(samples, &scratch);
        let samples = without.conceal(None, 2_880, &mut scratch);
        let longer = output(samples, &scratch);

        let error = |output: &[f32]| -> f32 {
            output
                .iter()
                .zip(&expected)
                .map(|(sample, expected)| (sample - expected).powi(2))
                .sum()
        };
        assert_eq!((fec.len(), plc.len(), longer.len()), (320, 320, 960));
        assert!(
            error(&fec) < error(&plc) / 2.0,
            "{} {}",
            error(&fec),
            error(&plc)
        );
    }

    fn frames(samples: usize, rate: u32, channels: u8) -> Vec<(usize, usize)> {
        split(samples, rate, channels)
            .map(|range| (range.start, range.end))
            .collect()
    }

    #[test]
    fn the_decoder_outputs_the_requested_rate_and_channels() {
        let mut decoder = Decoder::new(16_000, 1).unwrap();
        let mut scratch = Vec::new();

        for (duration, ranges) in [
            (480, vec![(0, 160)]),
            (960, vec![(0, 320)]),
            (2_880, vec![(0, 320), (320, 640), (640, 960)]),
        ] {
            let samples = decoder
                .decode(&packet(Channels::Stereo, duration), &mut scratch)
                .unwrap();
            assert_eq!(frames(samples, 16_000, 1), ranges);
        }
        let longest = decoder
            .decode(&packet(Channels::Stereo, 5_760), &mut scratch)
            .unwrap();
        assert_eq!(longest, 1_920);

        let mut stereo = Decoder::new(24_000, 2).unwrap();
        let samples = stereo
            .decode(&packet(Channels::Mono, 960), &mut scratch)
            .unwrap();
        assert_eq!(frames(samples, 24_000, 2), [(0, 960)]);
    }

    #[test]
    fn invalid_and_all_zero_payloads_are_refused() {
        let zeros = [0; 10];
        let mut decoder = Decoder::new(48_000, 2).unwrap();
        let mut scratch = Vec::new();

        assert!(packet_samples(&zeros).is_some());
        assert_eq!(received_samples(&zeros), None);
        assert_eq!(received_samples(&[0x03]), None);
        assert_eq!(received_samples(&packet(Channels::Stereo, 960)), Some(960));
        assert!(decoder.decode(&[0x03], &mut scratch).is_err());
        assert_eq!(
            decoder
                .decode(&packet(Channels::Stereo, 960), &mut scratch)
                .unwrap(),
            960
        );
    }

    #[test]
    fn samples_are_written_as_rounded_clamped_s16le_or_as_f32le() {
        let edges = [1.5, -1.5, 0.5, -0.25, 1.0, -1.0, f32::NAN];
        let ties = (-32_768..32_768).map(|step| (step as f32 + 0.5) / 32_767.0);
        let pcm: Vec<f32> = edges
            .into_iter()
            .chain(ties.flat_map(|tie| [tie.next_down(), tie, tie.next_up()]))
            .collect();
        let mut s16 = BytesMut::new();
        let mut f32le = BytesMut::new();

        write_samples(&pcm, SampleFormat::S16Le, &mut s16);
        write_samples(&pcm, SampleFormat::F32Le, &mut f32le);

        let known: Vec<u8> = [32_767_i16, -32_767, 16_384, -8_192, 32_767, -32_767, 0]
            .iter()
            .flat_map(|sample| sample.to_le_bytes())
            .collect();
        assert_eq!(s16[..known.len()], known);
        let rounded: Vec<u8> = pcm
            .iter()
            .flat_map(|sample| ((sample.clamp(-1.0, 1.0) * 32_767.0).round() as i16).to_le_bytes())
            .collect();
        assert_eq!(s16.as_ref(), rounded);
        let floats: Vec<u8> = pcm.iter().flat_map(|sample| sample.to_le_bytes()).collect();
        assert_eq!(f32le.as_ref(), floats);
    }

    proptest! {
        #[test]
        fn packets_are_framed_as_libopus_parses_them(
            packet in prop_oneof![vec(any::<u8>(), 0..8), vec(any::<u8>(), 0..1_400)],
        ) {
            let parsed = opus::packet::parse(&packet)
                .ok()
                .and_then(|_| opus::packet::get_nb_samples(&packet, SAMPLE_RATE).ok())
                .and_then(|samples| u32::try_from(samples).ok())
                .filter(|&samples| samples > 0);
            prop_assert_eq!(packet_samples(&packet), parsed);
        }
    }
}
