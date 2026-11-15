use std::time::Duration;

use bytes::Bytes;

use crate::error::CoreError;
use crate::voice::codec;
use crate::voice::source::SourceKind;
use crate::voice::source::decode::{DecodeStep, FileDecoder, Input};
use crate::voice::types::{CHANNELS, SAMPLE_RATE};

const WINDOW: u64 = SAMPLE_RATE as u64 / 10;
const MAX_POINTS: usize = 256;

pub fn voice_message(data: Bytes, hint: Option<String>) -> Result<(Duration, Vec<u8>), CoreError> {
    let unplayable = |error: &dyn std::fmt::Display| CoreError::InvalidRequest(error.to_string());
    let mut decoder =
        FileDecoder::open(Input::Bytes { data, hint }).map_err(|error| unplayable(&error))?;
    let info = decoder.info();
    let mut opus = match info.kind {
        SourceKind::Opus => Some(codec::OpusDecoder::new(SAMPLE_RATE, 2)?),
        SourceKind::Pcm => None,
    };
    let mut peaks = Peaks::default();
    let mut scratch = Vec::new();
    loop {
        match decoder.step().map_err(|error| unplayable(&error))? {
            DecodeStep::Chunk { data, .. } => match &mut opus {
                Some(opus) => {
                    let frames = opus
                        .decode(&data, &mut scratch)
                        .map_err(|error| unplayable(&error))?;
                    peaks.add(scratch.get(..frames * CHANNELS).unwrap_or_default());
                }
                None => peaks.add_f32le(&data),
            },
            DecodeStep::Skipped => {}
            DecodeStep::Ended => break,
        }
    }
    let (measured, waveform) = peaks.finish();
    Ok((info.duration.unwrap_or(measured), waveform))
}

#[derive(Default)]
struct Peaks {
    windows: Vec<f32>,
    peak: f32,
    filled: u64,
    frames: u64,
}

impl Peaks {
    fn add_f32le(&mut self, data: &[u8]) {
        let (words, _) = data.as_chunks::<4>();
        let samples: Vec<f32> = words.iter().copied().map(f32::from_le_bytes).collect();
        self.add(&samples);
    }

    fn add(&mut self, samples: &[f32]) {
        let (frames, _) = samples.as_chunks::<CHANNELS>();
        for frame in frames {
            self.peak = frame
                .iter()
                .fold(self.peak, |peak, sample| peak.max(sample.abs()));
            self.filled += 1;
            self.frames += 1;
            if self.filled == WINDOW {
                self.windows.push(self.peak);
                self.peak = 0.0;
                self.filled = 0;
            }
        }
    }

    fn finish(mut self) -> (Duration, Vec<u8>) {
        if self.filled > 0 || self.windows.is_empty() {
            self.windows.push(self.peak);
        }
        let count = self.windows.len();
        let points = count.min(MAX_POINTS);
        let waveform = (0..points)
            .map(|point| {
                let group = self
                    .windows
                    .get(point * count / points..(point + 1) * count / points)
                    .unwrap_or_default();
                let peak = group.iter().copied().fold(0.0_f32, f32::max).min(1.0);
                level(peak)
            })
            .collect();
        let duration = Duration::from_secs(self.frames) / SAMPLE_RATE;
        (duration, waveform)
    }
}

#[expect(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
fn level(peak: f32) -> u8 {
    (peak * 255.0).round() as u8
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::voice::source::tests::fixture;

    fn measured(name: &str) -> (Duration, Vec<u8>) {
        let data = std::fs::read(fixture(name)).unwrap();
        let hint = name
            .rsplit_once('.')
            .map(|(_, extension)| extension.to_owned());
        voice_message(data.into(), hint).unwrap()
    }

    fn wav(rate: u32, samples: &[i16]) -> Vec<u8> {
        let size = u32::try_from(samples.len() * 2).unwrap();
        let mut file = Vec::new();
        file.extend_from_slice(b"RIFF");
        file.extend_from_slice(&(36 + size).to_le_bytes());
        file.extend_from_slice(b"WAVEfmt ");
        file.extend_from_slice(&16_u32.to_le_bytes());
        file.extend_from_slice(&1_u16.to_le_bytes());
        file.extend_from_slice(&1_u16.to_le_bytes());
        file.extend_from_slice(&rate.to_le_bytes());
        file.extend_from_slice(&(rate * 2).to_le_bytes());
        file.extend_from_slice(&2_u16.to_le_bytes());
        file.extend_from_slice(&16_u16.to_le_bytes());
        file.extend_from_slice(b"data");
        file.extend_from_slice(&size.to_le_bytes());
        for sample in samples {
            file.extend_from_slice(&sample.to_le_bytes());
        }
        file
    }

    #[test]
    fn a_tone_gives_a_point_per_100_ms_at_its_level() {
        for name in ["tone.opus", "tone-s16.wav", "tone.mp3"] {
            let (duration, waveform) = measured(name);

            assert!(
                duration.abs_diff(Duration::from_secs(1)) < Duration::from_millis(30),
                "{name}: {duration:?}"
            );
            assert_eq!(
                waveform.len(),
                usize::try_from(duration.as_millis().div_ceil(100)).unwrap(),
                "{name}"
            );
            assert!(
                waveform[1..9].iter().all(|point| point.abs_diff(128) <= 8),
                "{name}: {waveform:?}"
            );
        }
    }

    #[test]
    fn a_long_recording_is_downsampled_to_256_points_keeping_peaks() {
        let mut samples = vec![0_i16; 8_000 * 60];
        samples[8_000 * 30 + 400] = i16::MAX;
        samples.extend([i16::MIN; 800]);

        let (duration, waveform) =
            voice_message(wav(8_000, &samples).into(), Some("wav".to_owned())).unwrap();

        assert_eq!(duration, Duration::from_millis(60_100));
        assert_eq!(waveform.len(), 256);
        let loud: Vec<_> = (0..)
            .zip(&waveform)
            .filter(|(_, point)| **point > 64)
            .collect();
        assert_eq!(
            loud.iter().map(|(index, _)| *index).collect::<Vec<_>>(),
            [128, 255]
        );
        assert_eq!(waveform.last(), Some(&255));
    }

    #[test]
    fn audio_that_does_not_decode_is_an_invalid_request() {
        let error = voice_message(Bytes::from_static(b"not audio"), None).unwrap_err();

        assert!(matches!(error, CoreError::InvalidRequest(_)), "{error}");
    }
}
