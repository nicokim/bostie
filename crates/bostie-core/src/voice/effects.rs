use std::f32::consts::{SQRT_2, TAU};

use rubato::audioadapter_buffers::direct::InterleavedSlice;
use rubato::{
    Async, FixedAsync, Resampler, SincInterpolationParameters, SincInterpolationType,
    WindowFunction,
};

use crate::voice::types::{CHANNELS, FRAME_LEN, FRAME_SAMPLES, Filters};

const RATE: f32 = 48_000.0;
const BASS_HZ: f32 = 100.0;
const HOP: usize = FRAME_SAMPLES;
const WINDOW: usize = 2 * HOP;
const SEEK: usize = HOP / 2;
const OVERLAP: usize = HOP / 2;
const COARSE: usize = 4;
const SINC_LEN: usize = 64;
const OVERSAMPLING: usize = 64;
const SILENCE: [f32; FRAME_LEN] = [0.0; FRAME_LEN];

type Frame = [f32; CHANNELS];

pub(crate) struct Equalizer(Vec<Biquad>);

struct Biquad {
    b: [f32; 3],
    a: [f32; 2],
    state: [[f32; 2]; CHANNELS],
}

pub(crate) struct Rate {
    speed: f32,
    pitch: f32,
    shift: Option<Shift>,
    stretch: Stretch,
}

struct Shift {
    resampler: Async<f32>,
    block: Vec<f32>,
}

struct Stretch {
    input: Vec<Frame>,
    next: f64,
    last: Option<usize>,
    end: Option<usize>,
    tail: Vec<Frame>,
    window: Vec<f32>,
}

impl Equalizer {
    pub(crate) fn new(filters: &Filters, previous: Option<Self>) -> Option<Self> {
        let bass = (filters.bass != 0.0).then(|| Biquad::low_shelf(BASS_HZ, filters.bass));
        let bands = filters
            .bands
            .iter()
            .filter(|&&(_, gain)| gain != 0.0)
            .map(|&(frequency, gain)| Biquad::peak(frequency, gain));
        let mut biquads: Vec<Biquad> = bass.into_iter().chain(bands).collect();
        if biquads.is_empty() {
            return None;
        }
        for (biquad, old) in biquads
            .iter_mut()
            .zip(previous.into_iter().flat_map(|eq| eq.0))
        {
            biquad.state = old.state;
        }
        Some(Self(biquads))
    }

    pub(crate) fn apply(&mut self, frame: &mut [f32]) {
        let (frames, _) = frame.as_chunks_mut::<CHANNELS>();
        for biquad in &mut self.0 {
            biquad.apply(frames);
        }
    }
}

impl Biquad {
    fn peak(frequency: f32, gain: f32) -> Self {
        let a = 10_f32.powf(gain / 40.0);
        let (sin, cos) = (TAU * frequency / RATE).sin_cos();
        let alpha = sin / (2.0 * SQRT_2);
        Self::normalized(
            [1.0 + alpha * a, -2.0 * cos, 1.0 - alpha * a],
            [1.0 + alpha / a, -2.0 * cos, 1.0 - alpha / a],
        )
    }

    fn low_shelf(frequency: f32, gain: f32) -> Self {
        let a = 10_f32.powf(gain / 40.0);
        let (sin, cos) = (TAU * frequency / RATE).sin_cos();
        let slope = sin * a.sqrt() * SQRT_2;
        Self::normalized(
            [
                a * ((a + 1.0) - (a - 1.0) * cos + slope),
                2.0 * a * ((a - 1.0) - (a + 1.0) * cos),
                a * ((a + 1.0) - (a - 1.0) * cos - slope),
            ],
            [
                (a + 1.0) + (a - 1.0) * cos + slope,
                -2.0 * ((a - 1.0) + (a + 1.0) * cos),
                (a + 1.0) + (a - 1.0) * cos - slope,
            ],
        )
    }

    fn normalized(b: [f32; 3], [a0, a1, a2]: [f32; 3]) -> Self {
        Self {
            b: b.map(|b| b / a0),
            a: [a1 / a0, a2 / a0],
            state: [[0.0; 2]; CHANNELS],
        }
    }

    fn apply(&mut self, frames: &mut [Frame]) {
        let [b0, b1, b2] = self.b;
        let [a1, a2] = self.a;
        for frame in frames {
            for (sample, [z1, z2]) in frame.iter_mut().zip(&mut self.state) {
                let x = *sample;
                let y = b0 * x + *z1;
                *z1 = b1 * x - a1 * y + *z2;
                *z2 = b2 * x - a2 * y;
                *sample = y;
            }
        }
    }
}

impl Rate {
    pub(crate) fn new() -> Self {
        Self {
            speed: 1.0,
            pitch: 1.0,
            shift: None,
            stretch: Stretch::new(),
        }
    }

    #[expect(clippy::float_cmp)]
    pub(crate) fn set(&mut self, speed: f32, pitch: f32) {
        if pitch != self.pitch {
            self.shift = (pitch != 1.0).then(|| Shift::new(pitch)).flatten();
        }
        self.speed = speed;
        self.pitch = pitch;
    }

    pub(crate) fn wants_input(&self) -> bool {
        !self.stretch.is_ready()
    }

    pub(crate) fn push(&mut self, frame: &[f32]) {
        if let Some(end) = self.stretch.end.take() {
            self.stretch.input.truncate(end);
        }
        self.feed(frame);
    }

    pub(crate) fn pad(&mut self) {
        if self.stretch.end.is_none() {
            let delay = self
                .shift
                .as_ref()
                .map_or(0, |shift| shift.resampler.output_delay());
            self.stretch.end = Some(self.stretch.input.len() + delay);
            self.feed(&SILENCE);
        }
        self.stretch.pad();
    }

    pub(crate) fn render(&mut self, out: &mut [f32]) -> bool {
        let rendered = self.stretch.step(self.speed / self.pitch, out);
        if self.stretch.is_drained() {
            self.reset();
        }
        rendered
    }

    pub(crate) fn held(&self) -> u64 {
        let end = self.stretch.end.unwrap_or(self.stretch.input.len());
        let ahead = (as_position(end) - self.stretch.next).max(0.0) * f64::from(self.pitch);
        #[expect(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
        let held = ahead.round() as u64;
        held
    }

    pub(crate) fn reset(&mut self) {
        self.stretch.reset();
        if let Some(shift) = &mut self.shift {
            shift.resampler.reset();
        }
    }

    fn feed(&mut self, frame: &[f32]) {
        if let Some(shift) = &mut self.shift {
            shift.process(frame, &mut self.stretch.input);
        } else {
            let (frames, _) = frame.as_chunks::<CHANNELS>();
            self.stretch.input.extend_from_slice(frames);
        }
    }
}

impl Shift {
    fn new(pitch: f32) -> Option<Self> {
        let parameters =
            SincInterpolationParameters::new(SINC_LEN, WindowFunction::BlackmanHarris2)
                .oversampling_factor(OVERSAMPLING)
                .interpolation(SincInterpolationType::Linear);
        let resampler = Async::new_sinc(
            1.0 / f64::from(pitch),
            1.0,
            &parameters,
            FRAME_SAMPLES,
            CHANNELS,
            FixedAsync::Input,
        )
        .inspect_err(|error| tracing::debug!(%error, "the pitch resampler could not start"))
        .ok()?;
        let block = vec![0.0; resampler.output_frames_max() * CHANNELS];
        Some(Self { resampler, block })
    }

    fn process(&mut self, frame: &[f32], out: &mut Vec<Frame>) {
        let frames = self.block.len() / CHANNELS;
        let input = InterleavedSlice::new(frame, CHANNELS, FRAME_SAMPLES);
        let output = InterleavedSlice::new_mut(&mut self.block, CHANNELS, frames);
        let produced = match (input, output) {
            (Ok(input), Ok(mut output)) => self
                .resampler
                .process_into_buffer(&input, &mut output, None)
                .map(|(_, produced)| produced)
                .map_err(|error| error.to_string()),
            (Err(error), _) | (_, Err(error)) => Err(error.to_string()),
        };
        match produced {
            Ok(produced) => {
                let (frames, _) = self.block.as_chunks::<CHANNELS>();
                out.extend_from_slice(frames.get(..produced).unwrap_or_default());
            }
            Err(error) => tracing::debug!(%error, "the pitch resampler rejected a frame"),
        }
    }
}

impl Stretch {
    fn new() -> Self {
        #[expect(clippy::cast_precision_loss)]
        let window = (0..WINDOW)
            .map(|index| 0.5 - 0.5 * (TAU * index as f32 / WINDOW as f32).cos())
            .collect();
        Self {
            input: Vec::new(),
            next: 0.0,
            last: None,
            end: None,
            tail: vec![[0.0; CHANNELS]; HOP],
            window,
        }
    }

    fn reset(&mut self) {
        self.input.clear();
        self.next = 0.0;
        self.last = None;
        self.end = None;
        self.tail.fill([0.0; CHANNELS]);
    }

    fn is_drained(&self) -> bool {
        self.end.is_some_and(|end| self.next >= as_position(end))
    }

    fn ideal(&self) -> usize {
        #[expect(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
        let ideal = self.next.round().max(0.0) as usize;
        ideal
    }

    fn reach(&self) -> usize {
        (self.ideal() + SEEK).max(self.last.map_or(0, |last| last + HOP)) + WINDOW
    }

    fn is_ready(&self) -> bool {
        self.input.len() >= self.reach()
    }

    fn pad(&mut self) {
        let reach = self.reach().max(self.input.len());
        self.input.resize(reach, [0.0; CHANNELS]);
    }

    fn step(&mut self, tempo: f32, out: &mut [f32]) -> bool {
        if !self.is_ready() {
            return false;
        }
        let ideal = self.ideal();
        let source = match self.last {
            Some(last) if (tempo - 1.0).abs() < f32::EPSILON => last + HOP,
            Some(last) => self.seek(last + HOP, ideal),
            None => ideal,
        };
        let Some(frames) = self.input.get(source..source + WINDOW) else {
            return false;
        };
        let (head, rest) = frames.split_at(HOP);
        let (rising, falling) = self.window.split_at(HOP);
        let (out, _) = out.as_chunks_mut::<CHANNELS>();
        for (((out, tail), frame), weight) in out.iter_mut().zip(&self.tail).zip(head).zip(rising) {
            for ((out, tail), sample) in out.iter_mut().zip(tail).zip(frame) {
                *out = tail + sample * weight;
            }
        }
        for ((tail, frame), weight) in self.tail.iter_mut().zip(rest).zip(falling) {
            for (tail, sample) in tail.iter_mut().zip(frame) {
                *tail = sample * weight;
            }
        }
        self.last = Some(source);
        self.next = if (tempo - 1.0).abs() < f32::EPSILON {
            as_position(source + HOP)
        } else {
            self.next + as_position(HOP) * f64::from(tempo)
        };
        self.compact(source);
        true
    }

    fn seek(&self, target: usize, ideal: usize) -> usize {
        let low = ideal.saturating_sub(SEEK);
        let high = ideal + SEEK;
        let coarse = self.best(target, (low..=high).step_by(COARSE), COARSE);
        let fine = coarse.saturating_sub(COARSE).max(low)..=(coarse + COARSE).min(high);
        self.best(target, fine, 1)
    }

    fn best(&self, target: usize, candidates: impl Iterator<Item = usize>, stride: usize) -> usize {
        let mut best = (f32::NEG_INFINITY, target);
        for candidate in candidates {
            let score = self.similarity(candidate, target, stride);
            if score > best.0 {
                best = (score, candidate);
            }
        }
        best.1
    }

    fn similarity(&self, candidate: usize, target: usize, stride: usize) -> f32 {
        let (Some(candidate), Some(target)) = (
            self.input.get(candidate..candidate + OVERLAP),
            self.input.get(target..target + OVERLAP),
        ) else {
            return f32::NEG_INFINITY;
        };
        let (dot, energy) = candidate.iter().zip(target).step_by(stride).fold(
            (0.0, 0.0),
            |(dot, energy), ([left, right], [wanted_left, wanted_right])| {
                let mono = left + right;
                (
                    dot + mono * (wanted_left + wanted_right),
                    energy + mono * mono,
                )
            },
        );
        dot / (energy.sqrt() + f32::EPSILON)
    }

    fn compact(&mut self, source: usize) {
        let spent = source.min(self.ideal().saturating_sub(SEEK));
        if spent > WINDOW {
            self.input.drain(..spent);
            self.next -= as_position(spent);
            self.last = self.last.map(|last| last - spent);
            self.end = self.end.map(|end| end.saturating_sub(spent));
        }
    }
}

#[expect(clippy::cast_precision_loss)]
fn as_position(frames: usize) -> f64 {
    frames as f64
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tone(hz: f32, frames: usize) -> Vec<f32> {
        #[expect(clippy::cast_precision_loss)]
        let samples = (0..frames * FRAME_SAMPLES)
            .map(|index| 0.5 * (TAU * hz * index as f32 / RATE).sin())
            .flat_map(|sample| [sample, sample])
            .collect();
        samples
    }

    fn rms(samples: &[f32]) -> f32 {
        #[expect(clippy::cast_precision_loss)]
        let mean = samples.iter().map(|sample| sample * sample).sum::<f32>() / samples.len() as f32;
        mean.sqrt()
    }

    fn crossings(samples: &[f32]) -> usize {
        let left: Vec<f32> = samples.iter().step_by(CHANNELS).copied().collect();
        left.windows(2)
            .filter(|pair| pair[0] < 0.0 && pair[1] >= 0.0)
            .count()
    }

    fn filters(bands: &[(f32, f32)], bass: f32, speed: f32, pitch: f32) -> Filters {
        Filters {
            bands: bands.to_vec(),
            bass,
            speed,
            pitch,
        }
    }

    #[test]
    fn bands_and_bass_change_the_gain_near_their_frequency_only() {
        let cases = [
            (
                filters(&[(1_000.0, 12.0)], 0.0, 1.0, 1.0),
                [1_000.0, 8_000.0],
                [12.0, 0.0],
            ),
            (filters(&[], -12.0, 1.0, 1.0), [40.0, 5_000.0], [-12.0, 0.0]),
        ];
        for (filters, frequencies, expected) in cases {
            for (hz, gain) in frequencies.into_iter().zip(expected) {
                let mut equalizer = Equalizer::new(&filters, None).unwrap();
                let mut audio = tone(hz, 50);
                for frame in audio.chunks_mut(FRAME_LEN) {
                    equalizer.apply(frame);
                }
                let settled = &audio[25 * FRAME_LEN..];
                let measured = 20.0 * (rms(settled) / rms(&tone(hz, 25))).log10();
                assert!((measured - gain).abs() < 1.0, "{hz} Hz: {measured} dB");
            }
        }
        assert!(Equalizer::new(&filters(&[(500.0, 0.0)], 0.0, 1.0, 1.0), None).is_none());
    }

    #[test]
    fn a_new_equalizer_carries_on_from_the_state_of_the_one_it_replaces() {
        let settings = filters(&[(1_000.0, 12.0)], 6.0, 1.0, 1.0);
        let input = tone(1_000.0, 2);
        let (first, second) = input.split_at(input.len() / 2);
        let mut whole = input.clone();
        Equalizer::new(&settings, None).unwrap().apply(&mut whole);

        let mut before = Equalizer::new(&settings, None).unwrap();
        let mut head = first.to_vec();
        before.apply(&mut head);
        let mut tail = second.to_vec();
        Equalizer::new(&settings, Some(before))
            .unwrap()
            .apply(&mut tail);

        assert_eq!([head, tail].concat(), whole);
    }

    #[test]
    fn speed_and_pitch_change_tempo_and_frequency_independently() {
        for (speed, pitch) in [(1.0, 1.0), (2.0, 1.0), (1.0, 2.0), (1.25, 1.25), (0.5, 1.0)] {
            let mut rate = Rate::new();
            rate.set(speed, pitch);
            let input = tone(500.0, 400);
            let mut frames = input.chunks(FRAME_LEN);
            let mut pulled = 0;
            let mut out = Vec::new();
            let mut frame = vec![0.0; FRAME_LEN];
            for _ in 0..100 {
                while rate.wants_input()
                    && let Some(next) = frames.next()
                {
                    rate.push(next);
                    pulled += 1;
                }
                assert!(rate.render(&mut frame));
                out.extend_from_slice(&frame);
            }
            let heard = &out[10 * FRAME_LEN..];
            #[expect(clippy::cast_precision_loss)]
            let hz = crossings(heard) as f32 * RATE / (heard.len() / CHANNELS) as f32;
            #[expect(clippy::cast_precision_loss)]
            let tempo = pulled as f32 / 100.0;
            assert!(
                (hz / 500.0 - pitch).abs() < 0.03,
                "{speed} {pitch}: {hz} Hz"
            );
            assert!((tempo - speed).abs() < 0.06, "{speed} {pitch}: {tempo}x");
        }
    }

    #[test]
    fn an_unchanged_rate_passes_audio_through_after_its_lookahead() {
        let mut rate = Rate::new();
        rate.set(2.0, 1.0);
        rate.set(1.0, 1.0);
        let input = tone(300.0, 20);
        let mut frames = input.chunks(FRAME_LEN);
        let mut out = Vec::new();
        let mut frame = vec![0.0; FRAME_LEN];
        for _ in 0..10 {
            while rate.wants_input()
                && let Some(next) = frames.next()
            {
                rate.push(next);
            }
            assert!(rate.render(&mut frame));
            out.extend_from_slice(&frame);
        }

        let tail = &out[FRAME_LEN..];
        assert!(
            tail.iter()
                .zip(&input[FRAME_LEN..])
                .all(|(out, input)| (out - input).abs() < 1e-5)
        );
    }
}
