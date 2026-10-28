use std::mem;

use rubato::audioadapter_buffers::direct::InterleavedSlice;
use rubato::{Fft, FixedSync, Indexing, Resampler};

use crate::error::CoreError;
use crate::voice::types::{FRAME_SAMPLES, PcmFormat, SAMPLE_RATE, SampleFormat};

const S16_SCALE: f32 = 32_768.0;

pub struct Converter {
    format: PcmFormat,
    leftover: Vec<u8>,
    resampler: Option<Box<Resampling>>,
}

struct Resampling {
    fft: Fft<f32>,
    rate: u64,
    pending: Vec<f32>,
    block: Vec<f32>,
    received: u64,
    emitted: u64,
    delay_left: usize,
}

impl Converter {
    pub fn new(format: PcmFormat) -> Result<Self, CoreError> {
        format.validate()?;
        let resampler = if format.rate == SAMPLE_RATE {
            None
        } else {
            Some(Box::new(Resampling::new(format)?))
        };
        Ok(Self {
            format,
            leftover: Vec::new(),
            resampler,
        })
    }

    pub fn format(&self) -> PcmFormat {
        self.format
    }

    pub fn push(&mut self, data: &[u8], out: &mut Vec<f32>) {
        let frame_bytes = self.frame_bytes();
        let mut data = data;
        if !self.leftover.is_empty() {
            let missing = frame_bytes.saturating_sub(self.leftover.len());
            let split = missing.min(data.len());
            self.leftover
                .extend_from_slice(data.get(..split).unwrap_or_default());
            data = data.get(split..).unwrap_or_default();
            if self.leftover.len() < frame_bytes {
                return;
            }
            let frame = mem::take(&mut self.leftover);
            self.decode(&frame, out);
            self.leftover = frame;
            self.leftover.clear();
        }
        let whole = data.len() - data.len() % frame_bytes;
        self.decode(data.get(..whole).unwrap_or_default(), out);
        self.leftover
            .extend_from_slice(data.get(whole..).unwrap_or_default());
    }

    pub fn push_f32(&mut self, samples: &[f32], out: &mut Vec<f32>) {
        self.accept(samples.iter().copied().map(finite), out);
    }

    pub fn flush(&mut self, out: &mut Vec<f32>) {
        self.leftover.clear();
        let mono = self.is_mono();
        if let Some(resampler) = &mut self.resampler {
            resampler.flush(mono, out);
        }
    }

    pub fn reset(&mut self) {
        self.leftover.clear();
        if let Some(resampler) = &mut self.resampler {
            resampler.reset();
        }
    }

    fn frame_bytes(&self) -> usize {
        let sample_bytes = match self.format.sample {
            SampleFormat::S16Le => 2,
            SampleFormat::F32Le => 4,
        };
        sample_bytes * usize::from(self.format.channels)
    }

    fn is_mono(&self) -> bool {
        self.format.channels == 1
    }

    fn decode(&mut self, bytes: &[u8], out: &mut Vec<f32>) {
        match self.format.sample {
            SampleFormat::S16Le => {
                let (samples, _) = bytes.as_chunks::<2>();
                self.accept(
                    samples
                        .iter()
                        .map(|sample| f32::from(i16::from_le_bytes(*sample)) / S16_SCALE),
                    out,
                );
            }
            SampleFormat::F32Le => {
                let (samples, _) = bytes.as_chunks::<4>();
                self.accept(
                    samples
                        .iter()
                        .map(|sample| finite(f32::from_le_bytes(*sample))),
                    out,
                );
            }
        }
    }

    fn accept(&mut self, samples: impl Iterator<Item = f32>, out: &mut Vec<f32>) {
        let mono = self.is_mono();
        match &mut self.resampler {
            None => emit(mono, samples, out),
            Some(resampler) => {
                resampler.pending.extend(samples);
                resampler.run(mono, out);
            }
        }
    }
}

impl Resampling {
    fn new(format: PcmFormat) -> Result<Self, CoreError> {
        let channels = usize::from(format.channels);
        let fft = Fft::new(
            format.rate as usize,
            SAMPLE_RATE as usize,
            FRAME_SAMPLES,
            channels,
            FixedSync::Output,
        )
        .map_err(|error| {
            CoreError::InvalidRequest(format!("cannot resample {} Hz: {error}", format.rate))
        })?;
        let delay_left = fft.output_delay();
        Ok(Self {
            fft,
            rate: u64::from(format.rate),
            pending: Vec::new(),
            block: vec![0.0; FRAME_SAMPLES * channels],
            received: 0,
            emitted: 0,
            delay_left,
        })
    }

    fn channels(&self) -> usize {
        self.fft.nbr_channels()
    }

    fn run(&mut self, mono: bool, out: &mut Vec<f32>) {
        let channels = self.channels();
        let available = self.pending.len() / channels;
        let mut used = 0;
        loop {
            let needed = self.fft.input_frames_next();
            if available - used < needed
                || !self.process(Indexing::new().input_offset(used), u64::MAX, mono, out)
            {
                break;
            }
            used += needed;
        }
        self.pending.drain(..used * channels);
        self.received += used as u64;
    }

    fn flush(&mut self, mono: bool, out: &mut Vec<f32>) {
        let mut remaining = self.pending.len() / self.channels();
        self.received += remaining as u64;
        let target = (self.received * u64::from(SAMPLE_RATE)).div_ceil(self.rate);
        let mut offset = 0;
        while self.emitted < target {
            let take = remaining.min(self.fft.input_frames_next());
            let indexing = Indexing::new().input_offset(offset).partial_len(take);
            if !self.process(indexing, target - self.emitted, mono, out) {
                break;
            }
            offset += take;
            remaining -= take;
        }
        self.reset();
    }

    fn process(&mut self, indexing: Indexing, limit: u64, mono: bool, out: &mut Vec<f32>) -> bool {
        let channels = self.channels();
        let frames = self.pending.len() / channels;
        let input = InterleavedSlice::new(&self.pending, channels, frames);
        let output = InterleavedSlice::new_mut(&mut self.block, channels, FRAME_SAMPLES);
        let result = match (input, output) {
            (Ok(input), Ok(mut output)) => self
                .fft
                .process_into_buffer(&input, &mut output, Some(&indexing))
                .map_err(|error| error.to_string()),
            (Err(error), _) | (_, Err(error)) => Err(error.to_string()),
        };
        let produced = match result {
            Ok((_, produced)) => produced,
            Err(error) => {
                tracing::warn!(%error, "the resampler rejected a block");
                return false;
            }
        };
        let skipped = self.delay_left.min(produced);
        self.delay_left -= skipped;
        let kept = (produced - skipped).min(usize::try_from(limit).unwrap_or(usize::MAX));
        let samples = self
            .block
            .get(skipped * channels..(skipped + kept) * channels)
            .unwrap_or_default();
        emit(mono, samples.iter().copied(), out);
        self.emitted += kept as u64;
        true
    }

    fn reset(&mut self) {
        self.fft.reset();
        self.pending.clear();
        self.received = 0;
        self.emitted = 0;
        self.delay_left = self.fft.output_delay();
    }
}

fn emit(mono: bool, samples: impl Iterator<Item = f32>, out: &mut Vec<f32>) {
    if mono {
        out.extend(samples.flat_map(|sample| [sample, sample]));
    } else {
        out.extend(samples);
    }
}

fn finite(sample: f32) -> f32 {
    if sample.is_finite() { sample } else { 0.0 }
}

#[cfg(test)]
mod tests {
    use std::f32::consts::TAU;

    use super::*;

    fn format(rate: u32, channels: u8, sample: SampleFormat) -> PcmFormat {
        PcmFormat {
            rate,
            channels,
            sample,
        }
    }

    fn converter(rate: u32, channels: u8, sample: SampleFormat) -> Converter {
        Converter::new(format(rate, channels, sample)).unwrap()
    }

    fn s16_bytes(samples: &[f32]) -> Vec<u8> {
        samples
            .iter()
            .flat_map(|sample| ((sample * 32_767.0) as i16).to_le_bytes())
            .collect()
    }

    fn f32_bytes(samples: &[f32]) -> Vec<u8> {
        samples
            .iter()
            .flat_map(|sample| sample.to_le_bytes())
            .collect()
    }

    fn tone(rate: u32, channels: u8, seconds: f32) -> Vec<f32> {
        let frames = (rate as f32 * seconds) as usize;
        (0..frames)
            .flat_map(|frame| {
                let sample = (TAU * 1_000.0 * frame as f32 / rate as f32).sin() * 0.5;
                std::iter::repeat_n(sample, usize::from(channels))
            })
            .collect()
    }

    fn left(stereo: &[f32]) -> Vec<f32> {
        stereo.iter().step_by(2).copied().collect()
    }

    fn frequency(samples: &[f32]) -> f32 {
        let crossings: Vec<f32> = samples
            .windows(2)
            .enumerate()
            .filter(|(_, pair)| pair[0] < 0.0 && pair[1] >= 0.0)
            .map(|(index, pair)| index as f32 + pair[0] / (pair[0] - pair[1]))
            .collect();
        let span = crossings.last().unwrap() - crossings.first().unwrap();
        (crossings.len() - 1) as f32 / (span / SAMPLE_RATE as f32)
    }

    fn convert_in_chunks(converter: &mut Converter, bytes: &[u8], chunk: usize) -> Vec<f32> {
        let mut out = Vec::new();
        for piece in bytes.chunks(chunk) {
            converter.push(piece, &mut out);
        }
        out
    }

    #[test]
    fn s16_and_f32_stereo_at_48_khz_become_the_same_floats() {
        let mut s16 = converter(48_000, 2, SampleFormat::S16Le);
        let mut f32le = converter(48_000, 2, SampleFormat::F32Le);
        let mut from_s16 = Vec::new();
        let mut from_f32 = Vec::new();

        s16.push(
            &[16_384_i16, -16_384, 0, i16::MIN]
                .iter()
                .flat_map(|sample| sample.to_le_bytes())
                .collect::<Vec<_>>(),
            &mut from_s16,
        );
        f32le.push(&f32_bytes(&[0.5, -0.5, 0.0, -1.0]), &mut from_f32);

        assert_eq!(from_s16, [0.5, -0.5, 0.0, -1.0]);
        assert_eq!(from_f32, from_s16);
    }

    #[test]
    fn mono_at_48_khz_is_duplicated_into_both_channels() {
        let mut converter = converter(48_000, 1, SampleFormat::F32Le);
        let mut out = Vec::new();

        converter.push(&f32_bytes(&[0.25, -0.75]), &mut out);

        assert_eq!(out, [0.25, 0.25, -0.75, -0.75]);
    }

    #[test]
    fn odd_byte_splits_across_writes_decode_like_one_write() {
        let bytes = s16_bytes(&tone(48_000, 2, 0.01));
        let mut whole = converter(48_000, 2, SampleFormat::S16Le);
        let expected = convert_in_chunks(&mut whole, &bytes, bytes.len());

        for chunk in [1, 3, 5, 7, 1_001] {
            let mut split = converter(48_000, 2, SampleFormat::S16Le);
            let out = convert_in_chunks(&mut split, &bytes, chunk);
            assert_eq!(out, expected, "chunks of {chunk} bytes");
        }
    }

    #[test]
    fn an_incomplete_frame_waits_for_the_rest_of_its_bytes() {
        let mut converter = converter(48_000, 2, SampleFormat::F32Le);
        let bytes = f32_bytes(&[0.5, -0.5]);
        let mut out = Vec::new();

        converter.push(&bytes[..5], &mut out);
        assert!(out.is_empty());
        converter.push(&bytes[5..], &mut out);

        assert_eq!(out, [0.5, -0.5]);
    }

    #[test]
    fn common_rates_resample_one_second_to_48_000_frames_at_the_same_pitch() {
        for rate in [
            8_000, 16_000, 22_050, 24_000, 44_100, 48_000, 96_000, 192_000,
        ] {
            for channels in [1, 2] {
                let mut converter = converter(rate, channels, SampleFormat::S16Le);
                let bytes = s16_bytes(&tone(rate, channels, 1.0));
                let chunk = (rate / 50) as usize * usize::from(channels) * 2;

                let mut out = convert_in_chunks(&mut converter, &bytes, chunk);
                let before_flush = out.len() / 2;
                converter.flush(&mut out);

                assert!(
                    before_flush + FRAME_SAMPLES * 2 >= 48_000,
                    "{rate} Hz x{channels}: only {before_flush} frames before the flush"
                );
                assert_eq!(out.len(), 48_000 * 2, "{rate} Hz x{channels}");
                let pitch = frequency(&left(&out)[960..47_040]);
                assert!(
                    (pitch - 1_000.0).abs() < 1.0,
                    "{rate} Hz x{channels}: {pitch} Hz"
                );
            }
        }
    }

    #[test]
    fn mono_resamples_as_one_channel_and_is_duplicated_after() {
        let mut converter = converter(24_000, 1, SampleFormat::F32Le);
        let mut out = Vec::new();

        converter.push(&f32_bytes(&tone(24_000, 1, 0.1)), &mut out);
        converter.flush(&mut out);

        assert_eq!(converter.resampler.as_ref().unwrap().channels(), 1);
        assert_eq!(out.len(), 4_800 * 2);
        assert!(
            out.as_chunks::<2>()
                .0
                .iter()
                .all(|[left, right]| left == right)
        );
    }

    #[test]
    fn the_resampler_delay_is_trimmed_so_audio_keeps_its_position() {
        let mut converter = converter(24_000, 1, SampleFormat::F32Le);
        let mut input = vec![0.0; 2_400];
        input[1_000] = 1.0;
        let mut out = Vec::new();

        converter.push_f32(&input, &mut out);
        converter.flush(&mut out);

        let peak = left(&out)
            .iter()
            .enumerate()
            .max_by(|a, b| a.1.total_cmp(b.1))
            .map(|(frame, _)| frame)
            .unwrap();
        assert!(peak.abs_diff(2_000) <= 1, "peak at {peak}");
    }

    #[test]
    fn push_f32_takes_interleaved_samples_at_the_input_rate() {
        let mut from_bytes = converter(44_100, 2, SampleFormat::F32Le);
        let mut from_floats = converter(44_100, 2, SampleFormat::F32Le);
        let samples = tone(44_100, 2, 0.2);
        let mut expected = Vec::new();
        let mut out = Vec::new();

        from_bytes.push(&f32_bytes(&samples), &mut expected);
        from_floats.push_f32(&samples, &mut out);

        assert!(!out.is_empty());
        assert_eq!(out, expected);
    }

    #[test]
    fn non_finite_samples_become_silence() {
        let samples = [f32::NAN, f32::INFINITY, f32::NEG_INFINITY, 0.5];
        let mut from_bytes = Vec::new();
        let mut from_floats = Vec::new();

        converter(48_000, 2, SampleFormat::F32Le).push(&f32_bytes(&samples), &mut from_bytes);
        converter(48_000, 2, SampleFormat::F32Le).push_f32(&samples, &mut from_floats);

        assert_eq!(from_bytes, [0.0, 0.0, 0.0, 0.5]);
        assert_eq!(from_floats, from_bytes);
    }

    #[test]
    fn rates_that_cannot_resample_in_20_ms_blocks_are_rejected_with_the_rule() {
        for rate in [8_001, 44_101] {
            match Converter::new(format(rate, 2, SampleFormat::S16Le)) {
                Err(CoreError::InvalidRequest(message)) => {
                    assert!(message.contains("gcd(rate, 48000)"), "{message}");
                }
                Err(other) => panic!("{rate} Hz: {other}"),
                Ok(_) => panic!("{rate} Hz was accepted"),
            }
        }
    }

    #[test]
    fn flush_pushes_the_tail_still_inside_the_resampler() {
        let mut converter = converter(16_000, 2, SampleFormat::S16Le);
        let mut out = Vec::new();

        converter.push(&s16_bytes(&tone(16_000, 2, 0.01)), &mut out);
        let before = out.len() / 2;
        converter.flush(&mut out);

        assert!(before < 480, "{before} frames before the flush");
        assert_eq!(out.len() / 2, 480);
    }

    #[test]
    fn reset_forgets_pending_bytes_and_samples() {
        let mut converter = converter(24_000, 2, SampleFormat::S16Le);
        let mut out = Vec::new();

        converter.push(&s16_bytes(&tone(24_000, 2, 0.01))[..479], &mut out);
        converter.reset();
        converter.flush(&mut out);

        assert!(out.is_empty());
    }
}
