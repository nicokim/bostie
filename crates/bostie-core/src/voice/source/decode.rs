use std::ffi::OsStr;
use std::fs::File;
use std::io::{self, Cursor, ErrorKind, Seek, SeekFrom};
use std::path::PathBuf;
use std::time::Duration;

use bytes::Bytes;
use symphonia::core::audio::{Channels, Position};
use symphonia::core::codecs::CodecParameters;
use symphonia::core::codecs::audio::well_known::{
    CODEC_ID_AAC, CODEC_ID_AC3, CODEC_ID_ALAC, CODEC_ID_EAC3, CODEC_ID_OPUS,
};
use symphonia::core::codecs::audio::{AudioCodecId, AudioDecoder, AudioDecoderOptions};
use symphonia::core::errors::{Error as MediaError, SeekErrorKind};
use symphonia::core::formats::probe::Hint;
use symphonia::core::formats::{FormatOptions, FormatReader, SeekMode, SeekTo, Track, TrackType};
use symphonia::core::io::{MediaSource, MediaSourceStream, MediaSourceStreamOptions};
use symphonia::core::meta::MetadataOptions;
use symphonia::core::packet::Packet;
use symphonia::core::units::{Time, TimeBase, Timestamp};

use crate::voice::codec;
use crate::voice::convert::Converter;
use crate::voice::source::http::HttpMedia;
use crate::voice::source::{SourceInfo, SourceKind};
use crate::voice::types::{CHANNELS, NANOS_PER_SECOND, PcmFormat, SAMPLE_RATE, SampleFormat};

const MINUS_3_DB: f32 = std::f32::consts::FRAC_1_SQRT_2;

pub(crate) enum Input {
    File(PathBuf),
    Bytes { data: Bytes, hint: Option<String> },
    Url(Box<HttpMedia>),
}

#[derive(Debug, thiserror::Error)]
pub(crate) enum DecodeError {
    #[error("{0}")]
    Io(#[from] io::Error),
    #[error("{0}")]
    Media(#[from] MediaError),
    #[error("no decoder for {0}")]
    NoDecoder(String),
    #[error("the media has no audio track")]
    NoAudio,
    #[error("{0}")]
    Unplayable(String),
}

pub(crate) enum Step {
    Chunk { data: Bytes, frames: u64 },
    Skipped,
    Ended,
}

pub(crate) struct Decoder {
    format: Option<Box<dyn FormatReader>>,
    hint: Hint,
    track: u32,
    time_base: Option<TimeBase>,
    info: SourceInfo,
    output: Output,
    resume_at: Option<Timestamp>,
    finished: bool,
}

enum Output {
    Opus,
    Pcm(Box<Pcm>),
}

struct Pcm {
    decoder: Box<dyn AudioDecoder>,
    decoded: Vec<f32>,
    downmix: Downmix,
    stereo: Vec<f32>,
    converter: Option<Converter>,
    converted: Vec<f32>,
}

#[derive(Default)]
struct Downmix {
    channels: Channels,
    gains: Vec<[f32; 2]>,
}

impl Decoder {
    pub(crate) fn open(input: Input) -> Result<Self, DecodeError> {
        let mut hint = Hint::new();
        let mut described = None;
        let (media, seekable): (Box<dyn MediaSource>, bool) = match input {
            Input::File(path) => {
                if let Some(extension) = path.extension().and_then(OsStr::to_str) {
                    hint.with_extension(extension);
                    described = Some(extension.to_owned());
                }
                let file = File::open(&path)?;
                let seekable = file.is_seekable();
                (Box::new(file), seekable)
            }
            Input::Bytes { data, hint: given } => {
                if let Some(given) = given.as_deref().map(str::trim) {
                    if given.contains('/') {
                        hint.mime_type(given);
                    } else {
                        hint.with_extension(given.trim_start_matches('.'));
                    }
                    described = Some(given.to_owned());
                }
                (Box::new(Cursor::new(data)), true)
            }
            Input::Url(mut media) => {
                media.connect()?;
                if let Some(extension) = media.extension() {
                    hint.with_extension(extension);
                    described = Some(extension.to_owned());
                }
                if let Some(mime) = media.content_type() {
                    hint.mime_type(mime);
                    described = Some(mime.to_owned());
                }
                let seekable = media.is_seekable();
                (media, seekable)
            }
        };
        let stream = MediaSourceStream::new(media, MediaSourceStreamOptions::default());
        let format = probe(&hint, stream).map_err(|error| match error {
            MediaError::Unsupported(_) => no_decoder_for(described),
            MediaError::IoError(io) if io.kind() == ErrorKind::UnexpectedEof => {
                no_decoder_for(described)
            }
            other => DecodeError::Media(other),
        })?;
        let mut decoder = Self {
            format: Some(format),
            hint,
            track: 0,
            time_base: None,
            info: SourceInfo {
                kind: SourceKind::Pcm,
                duration: None,
                seekable,
            },
            output: Output::Opus,
            resume_at: None,
            finished: false,
        };
        decoder.select_track()?;
        Ok(decoder)
    }

    pub(crate) fn info(&self) -> SourceInfo {
        self.info
    }

    pub(crate) fn step(&mut self) -> Result<Step, DecodeError> {
        if self.finished {
            return Ok(Step::Ended);
        }
        let packet = match self.reader()?.next_packet() {
            Ok(Some(packet)) => packet,
            Ok(None) => return Ok(self.finish()),
            Err(MediaError::IoError(error)) if error.kind() == ErrorKind::UnexpectedEof => {
                return Ok(self.finish());
            }
            Err(MediaError::ResetRequired) => {
                let kind = self.info.kind;
                let tail = match &mut self.output {
                    Output::Pcm(pcm) => pcm.flush(),
                    Output::Opus => Step::Skipped,
                };
                self.select_track()?;
                if self.info.kind != kind {
                    return Err(DecodeError::Unplayable(
                        "the stream switched between Opus and PCM audio".to_owned(),
                    ));
                }
                return Ok(match tail {
                    Step::Ended => Step::Skipped,
                    step => step,
                });
            }
            Err(error) => return Err(error.into()),
        };
        if packet.track_id != self.track {
            return Ok(Step::Skipped);
        }
        let Output::Pcm(pcm) = &mut self.output else {
            return Ok(self.opus(packet));
        };
        let first_kept = packet.pts.saturating_add(packet.trim_start);
        let skip = self
            .resume_at
            .and_then(|resume| span(self.time_base, first_kept, resume));
        let step = pcm.decode(&packet, skip)?;
        if !matches!(step, Step::Skipped) {
            self.resume_at = None;
        }
        Ok(step)
    }

    pub(crate) fn seek(&mut self, position: Duration) -> Result<(), DecodeError> {
        let time = Time::try_from_nanos_u128(position.as_nanos())
            .ok_or_else(|| DecodeError::Unplayable(format!("cannot seek to {position:?}")))?;
        self.finished = false;
        self.resume_at = None;
        self.reopen()?;
        if position.is_zero() {
            return Ok(());
        }
        let target = SeekTo::Time {
            time,
            track_id: Some(self.track),
        };
        match self.reader()?.seek(SeekMode::Accurate, target) {
            Ok(seeked) => {
                self.resume_at = Some(seeked.required_ts);
                Ok(())
            }
            Err(MediaError::SeekError(SeekErrorKind::OutOfRange)) => {
                self.finished = true;
                Ok(())
            }
            Err(error) => Err(error.into()),
        }
    }

    fn reader(&mut self) -> Result<&mut dyn FormatReader, DecodeError> {
        match &mut self.format {
            Some(format) => Ok(format.as_mut()),
            None => Err(lost()),
        }
    }

    fn reopen(&mut self) -> Result<(), DecodeError> {
        let mut stream = self.format.take().ok_or_else(lost)?.into_inner();
        stream.seek(SeekFrom::Start(0))?;
        self.format = Some(probe(&self.hint, stream)?);
        self.select_track()
    }

    fn select_track(&mut self) -> Result<(), DecodeError> {
        let format = self.format.as_deref().ok_or_else(lost)?;
        let track = format
            .first_track(TrackType::Audio)
            .ok_or(DecodeError::NoAudio)?;
        let params = track
            .codec_params
            .as_ref()
            .and_then(CodecParameters::audio)
            .ok_or(DecodeError::NoAudio)?;
        let output = if params.codec == CODEC_ID_OPUS {
            let channels = params.channels.as_ref().map_or(0, Channels::count);
            if channels > CHANNELS {
                return Err(DecodeError::NoDecoder(format!(
                    "opus with {channels} channels"
                )));
            }
            Output::Opus
        } else {
            let decoder = symphonia::default::get_codecs()
                .make_audio_decoder(params, &AudioDecoderOptions::default())
                .map_err(|error| match error {
                    MediaError::Unsupported(_) => DecodeError::NoDecoder(format!(
                        "{} in {}",
                        codec_name(params.codec),
                        format.format_info().short_name
                    )),
                    other => DecodeError::Media(other),
                })?;
            Output::Pcm(Box::new(Pcm::new(decoder)))
        };
        self.info.kind = match output {
            Output::Opus => SourceKind::Opus,
            Output::Pcm(_) => SourceKind::Pcm,
        };
        self.info.duration = duration(track);
        self.track = track.id;
        self.time_base = track.time_base;
        self.output = output;
        Ok(())
    }

    fn opus(&mut self, packet: Packet) -> Step {
        let Some(samples) = codec::packet_samples(&packet.data) else {
            tracing::debug!(
                bytes = packet.data.len(),
                "skipped a packet libopus cannot parse"
            );
            return Step::Skipped;
        };
        if let Some(resume) = self.resume_at {
            let length = Duration::from_secs(u64::from(samples)) / SAMPLE_RATE;
            if span(self.time_base, packet.pts, resume).is_some_and(|ahead| ahead >= length) {
                return Step::Skipped;
            }
            self.resume_at = None;
        }
        Step::Chunk {
            data: Bytes::from(Vec::from(packet.data)),
            frames: u64::from(samples),
        }
    }

    fn finish(&mut self) -> Step {
        self.finished = true;
        match &mut self.output {
            Output::Opus => Step::Ended,
            Output::Pcm(pcm) => pcm.flush(),
        }
    }
}

impl Pcm {
    fn new(decoder: Box<dyn AudioDecoder>) -> Self {
        Self {
            decoder,
            decoded: Vec::new(),
            downmix: Downmix::default(),
            stereo: Vec::new(),
            converter: None,
            converted: Vec::new(),
        }
    }

    fn decode(&mut self, packet: &Packet, skip: Option<Duration>) -> Result<Step, DecodeError> {
        let buffer = match self.decoder.decode(packet) {
            Ok(buffer) => buffer,
            Err(MediaError::DecodeError(reason)) => {
                tracing::debug!(reason, "skipped a packet the decoder cannot read");
                return Ok(Step::Skipped);
            }
            Err(error) => return Err(error.into()),
        };
        let rate = buffer.spec().rate();
        let layout = buffer.spec().channels();
        let count = layout.count();
        if count == 0 || rate == 0 {
            return Ok(Step::Skipped);
        }
        buffer.copy_to_vec_interleaved(&mut self.decoded);
        let skipped = skip.map_or(0, |skip| frames_in(skip, rate));
        let samples = self
            .decoded
            .get(skipped.saturating_mul(count)..)
            .unwrap_or_default();
        if samples.is_empty() {
            return Ok(Step::Skipped);
        }
        self.converted.clear();
        let (samples, channels) = if count > CHANNELS {
            self.downmix.prepare(layout);
            self.downmix.apply(samples, &mut self.stereo);
            (self.stereo.as_slice(), CHANNELS)
        } else {
            (samples, count)
        };
        let converter = converter_for(&mut self.converter, rate, channels, &mut self.converted)?;
        converter.push_f32(samples, &mut self.converted);
        Ok(chunk(&self.converted))
    }

    fn flush(&mut self) -> Step {
        self.converted.clear();
        if let Some(converter) = &mut self.converter {
            converter.flush(&mut self.converted);
        }
        match chunk(&self.converted) {
            Step::Skipped => Step::Ended,
            step => step,
        }
    }
}

impl Downmix {
    fn prepare(&mut self, channels: &Channels) {
        if self.gains.is_empty() || self.channels != *channels {
            self.channels = channels.clone();
            self.gains = stereo_gains(channels);
        }
    }

    fn apply(&self, samples: &[f32], out: &mut Vec<f32>) {
        out.clear();
        for frame in samples.chunks_exact(self.gains.len()) {
            let (left, right) = frame.iter().zip(&self.gains).fold(
                (0.0, 0.0),
                |(left, right), (sample, [to_left, to_right])| {
                    (left + sample * to_left, right + sample * to_right)
                },
            );
            out.extend([left, right]);
        }
    }
}

fn stereo_gains(channels: &Channels) -> Vec<[f32; 2]> {
    let known = match channels {
        Channels::Positioned(positions) => positions
            .iter()
            .map(position_gains)
            .collect::<Option<Vec<_>>>(),
        _ => None,
    };
    known.unwrap_or_else(|| {
        (0..channels.count())
            .map(|index| match index {
                0 => [1.0, 0.0],
                1 => [0.0, 1.0],
                _ => [0.0, 0.0],
            })
            .collect()
    })
}

fn position_gains(position: Position) -> Option<[f32; 2]> {
    let left = Position::REAR_LEFT | Position::SIDE_LEFT;
    let right = Position::REAR_RIGHT | Position::SIDE_RIGHT;
    let lfe = Position::LFE1 | Position::LFE2;
    Some(if position == Position::FRONT_LEFT {
        [1.0, 0.0]
    } else if position == Position::FRONT_RIGHT {
        [0.0, 1.0]
    } else if position == Position::FRONT_CENTER || position == Position::REAR_CENTER {
        [MINUS_3_DB, MINUS_3_DB]
    } else if left.contains(position) {
        [MINUS_3_DB, 0.0]
    } else if right.contains(position) {
        [0.0, MINUS_3_DB]
    } else if lfe.contains(position) {
        [0.0, 0.0]
    } else {
        return None;
    })
}

fn converter_for<'a>(
    slot: &'a mut Option<Converter>,
    rate: u32,
    channels: usize,
    out: &mut Vec<f32>,
) -> Result<&'a mut Converter, DecodeError> {
    let format = PcmFormat {
        rate,
        channels: u8::try_from(channels).unwrap_or(u8::MAX),
        sample: SampleFormat::F32Le,
    };
    if let Some(current) = slot.as_mut()
        && current.format() != format
    {
        current.flush(out);
        *slot = None;
    }
    if let Some(converter) = slot {
        Ok(converter)
    } else {
        let converter =
            Converter::new(format).map_err(|error| DecodeError::Unplayable(error.to_string()))?;
        Ok(slot.insert(converter))
    }
}

fn chunk(samples: &[f32]) -> Step {
    if samples.is_empty() {
        return Step::Skipped;
    }
    let data: Vec<u8> = samples
        .iter()
        .flat_map(|sample| sample.to_le_bytes())
        .collect();
    Step::Chunk {
        data: Bytes::from(data),
        frames: (samples.len() / CHANNELS) as u64,
    }
}

fn span(time_base: Option<TimeBase>, from: Timestamp, to: Timestamp) -> Option<Duration> {
    let time_base = time_base?;
    let start = time_base.calc_time(from)?.as_nanos();
    let end = time_base.calc_time(to)?.as_nanos();
    Some(nanos(end.saturating_sub(start)).unwrap_or_default())
}

fn frames_in(duration: Duration, rate: u32) -> usize {
    let frames = duration.as_nanos() * u128::from(rate) / NANOS_PER_SECOND;
    usize::try_from(frames).unwrap_or(usize::MAX)
}

fn duration(track: &Track) -> Option<Duration> {
    if let (Some(time_base), Some(duration)) = (track.time_base, track.duration) {
        return nanos(time_base.calc_duration(duration)?.as_nanos());
    }
    let rate = track
        .codec_params
        .as_ref()
        .and_then(CodecParameters::audio)
        .and_then(|params| params.sample_rate)
        .filter(|&rate| rate > 0)?;
    let frames = u128::from(track.num_frames?);
    nanos(i128::try_from(frames * NANOS_PER_SECOND / u128::from(rate)).ok()?)
}

fn nanos(nanos: i128) -> Option<Duration> {
    u64::try_from(nanos).ok().map(Duration::from_nanos)
}

fn probe(
    hint: &Hint,
    stream: MediaSourceStream<'static>,
) -> Result<Box<dyn FormatReader>, MediaError> {
    symphonia::default::get_probe().probe(
        hint,
        stream,
        FormatOptions::default(),
        MetadataOptions::default(),
    )
}

fn lost() -> DecodeError {
    DecodeError::Unplayable("the media could not be read again after a seek".to_owned())
}

fn no_decoder_for(described: Option<String>) -> DecodeError {
    DecodeError::NoDecoder(described.unwrap_or_else(|| "this media".to_owned()))
}

fn codec_name(codec: AudioCodecId) -> String {
    let name = match codec {
        CODEC_ID_AAC => "aac",
        CODEC_ID_AC3 => "ac3",
        CODEC_ID_EAC3 => "eac3",
        CODEC_ID_ALAC => "alac",
        other => return format!("codec {other}"),
    };
    name.to_owned()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::voice::source::tests::fixture;

    const H: f32 = MINUS_3_DB;

    fn decode_rest(decoder: &mut Decoder) -> Vec<Bytes> {
        let mut chunks = Vec::new();
        loop {
            match decoder.step().unwrap() {
                Step::Chunk { data, .. } => chunks.push(data),
                Step::Skipped => {}
                Step::Ended => return chunks,
            }
        }
    }

    fn open(name: &str) -> Decoder {
        Decoder::open(Input::File(fixture(name))).unwrap()
    }

    #[test]
    fn five_point_one_gains_follow_itu_r_bs_775() {
        let layout = Channels::Positioned(
            Position::FRONT_LEFT
                | Position::FRONT_RIGHT
                | Position::FRONT_CENTER
                | Position::LFE1
                | Position::REAR_LEFT
                | Position::REAR_RIGHT,
        );

        assert_eq!(
            stereo_gains(&layout),
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [H, H],
                [0.0, 0.0],
                [H, 0.0],
                [0.0, H]
            ]
        );
    }

    #[test]
    fn side_and_rear_centre_channels_fold_in_at_minus_3_db() {
        let layout = Channels::Positioned(
            Position::FRONT_LEFT
                | Position::FRONT_RIGHT
                | Position::FRONT_CENTER
                | Position::LFE1
                | Position::REAR_CENTER
                | Position::SIDE_LEFT
                | Position::SIDE_RIGHT,
        );

        assert_eq!(
            stereo_gains(&layout),
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [H, H],
                [0.0, 0.0],
                [H, H],
                [H, 0.0],
                [0.0, H]
            ]
        );
    }

    #[test]
    fn unknown_layouts_keep_their_first_two_channels() {
        let first_two = [[1.0, 0.0], [0.0, 1.0], [0.0, 0.0], [0.0, 0.0]];
        let with_height = Channels::Positioned(
            Position::FRONT_LEFT
                | Position::FRONT_RIGHT
                | Position::FRONT_CENTER
                | Position::TOP_CENTER,
        );

        assert_eq!(stereo_gains(&Channels::Discrete(4)), first_two);
        assert_eq!(stereo_gains(&with_height), first_two);
    }

    #[test]
    fn a_downmix_sums_every_channel_of_a_frame_with_its_gains() {
        let mut downmix = Downmix::default();
        downmix.prepare(&Channels::Positioned(
            Position::FRONT_LEFT | Position::FRONT_RIGHT | Position::FRONT_CENTER,
        ));
        let mut out = Vec::new();

        downmix.apply(&[0.1, 0.2, 0.5, -0.1, 0.0, 1.0], &mut out);

        assert_eq!(out, [0.1 + 0.5 * H, 0.2 + 0.5 * H, -0.1 + H, H]);
    }

    #[test]
    fn seeking_back_to_the_start_replays_exactly_the_same_audio() {
        for name in [
            "tone.opus",
            "tone.webm",
            "tone.ogg",
            "tone.flac",
            "tone-s16.wav",
            "tone-f32.wav",
            "tone.mp3",
            "tone.m4a",
            "tone-5.1.flac",
        ] {
            let reference = decode_rest(&mut open(name));
            let mut midway = open(name);
            for _ in 0..3 {
                midway.step().unwrap();
            }
            let mut ended = open(name);
            decode_rest(&mut ended);

            midway.seek(Duration::ZERO).unwrap();
            ended.seek(Duration::ZERO).unwrap();

            assert!(decode_rest(&mut midway) == reference, "{name} from midway");
            assert!(decode_rest(&mut ended) == reference, "{name} from the end");
        }
    }

    #[test]
    fn pcm_seeks_land_within_a_few_samples_of_the_position() {
        let frames = |chunks: &[Bytes]| chunks.iter().map(|chunk| chunk.len() / 8).sum::<usize>();
        for name in [
            "tone.ogg",
            "tone.flac",
            "tone-s16.wav",
            "tone-f32.wav",
            "tone.mp3",
            "tone.m4a",
            "tone-5.1.flac",
        ] {
            let total = frames(&decode_rest(&mut open(name)));
            for millis in [1, 7, 333, 500] {
                let mut decoder = open(name);

                decoder.seek(Duration::from_millis(millis)).unwrap();
                let skipped = total - frames(&decode_rest(&mut decoder));

                let expected = usize::try_from(millis * 48).unwrap();
                assert!(
                    skipped.abs_diff(expected) <= 4,
                    "{name} at {millis} ms: skipped {skipped}"
                );
            }
        }
    }

    #[test]
    fn a_seek_to_the_first_sample_of_an_opus_packet_keeps_that_packet() {
        for (name, millis, packet) in [("tone.webm", 35, 2), ("tone.opus", 20, 1)] {
            let reference = decode_rest(&mut open(name));
            let mut decoder = open(name);

            decoder.seek(Duration::from_millis(millis)).unwrap();
            let rest = decode_rest(&mut decoder);

            assert!(rest.as_slice() == &reference[packet..], "{name}");
        }
    }

    #[test]
    fn a_seek_past_the_end_ends_the_stream_without_an_error() {
        let mut decoder = open("tone.flac");

        decoder.seek(Duration::from_secs(5)).unwrap();

        assert!(matches!(decoder.step().unwrap(), Step::Ended));
    }
}
