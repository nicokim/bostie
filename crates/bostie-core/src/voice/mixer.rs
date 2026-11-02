use std::collections::HashMap;
use std::sync::Arc;

use bytes::Bytes;
use opus::{Channels, Decoder, SoftClip};

use crate::error::CoreError;
use crate::ids::Handle;
use crate::voice::codec::{self, Encoder};
use crate::voice::sink::Sink;
use crate::voice::types::{
    CHANNELS, EncoderSettings, FRAME_LEN, FRAME_SAMPLES, SAMPLE_RATE, SinkFormat, StallPolicy,
};

pub(crate) struct Mixer {
    settings: EncoderSettings,
    encoder: Option<Encoder>,
    soft_clip: SoftClip,
    strips: HashMap<Handle, Strip>,
    sum: Vec<f32>,
    passing: Option<Handle>,
    passed: Bytes,
    stale: bool,
}

struct Strip {
    gain: f32,
    target: f32,
    live: bool,
    frame: Vec<f32>,
    packet: Option<(Bytes, u32)>,
    decoder: Option<Decoder>,
    carry: Vec<f32>,
    carried: usize,
}

impl Mixer {
    pub(crate) fn new(settings: EncoderSettings) -> Result<Self, CoreError> {
        settings.validate()?;
        Ok(Self {
            settings,
            encoder: None,
            soft_clip: SoftClip::new(Channels::Stereo),
            strips: HashMap::new(),
            sum: vec![0.0; FRAME_LEN],
            passing: None,
            passed: Bytes::new(),
            stale: false,
        })
    }

    pub(crate) fn set_encoder(&mut self, settings: EncoderSettings) -> Result<(), CoreError> {
        match &mut self.encoder {
            Some(encoder) => encoder.set(settings)?,
            None => settings.validate()?,
        }
        self.settings = settings;
        Ok(())
    }

    pub(crate) fn retain(&mut self, sinks: &[Arc<Sink>]) {
        let live = |id: Handle| sinks.iter().any(|sink| sink.id() == id);
        self.strips.retain(|&id, _| live(id));
        if self.passing.is_some_and(|id| !live(id)) {
            self.passing = None;
        }
    }

    pub(crate) fn hold(&mut self, sinks: &[Arc<Sink>]) {
        for sink in sinks {
            if sink.waits_for().is_some() {
                continue;
            }
            if sink.options().stall == StallPolicy::Drop
                && let Some(strip) = self.strips.get_mut(&sink.id())
            {
                strip.carry.clear();
                strip.carried = 0;
            }
            sink.hold();
        }
    }

    pub(crate) fn discard(&mut self, sinks: &[Arc<Sink>]) {
        for sink in sinks {
            if self.waiting(sink, sinks) {
                continue;
            }
            let strip = self.strip(sink);
            if strip.gather(sink) && sink.options().format == SinkFormat::Opus {
                strip.fill(sink, false);
            }
            strip.gain = strip.target;
        }
    }

    pub(crate) fn mix(&mut self, sinks: &[Arc<Sink>]) -> Option<&[u8]> {
        let mut live = 0;
        let mut solo = None;
        for sink in sinks {
            if self.waiting(sink, sinks) {
                continue;
            }
            if self.strip(sink).gather(sink) {
                live += 1;
                solo = Some(sink);
            }
        }
        if live == 0 {
            return None;
        }
        if live == 1
            && let Some(sink) = solo
            && let Some(strip) = self.strips.get_mut(&sink.id())
            && strip.passes()
            && let Some((packet, _)) = strip.packet.take()
        {
            self.pass(sink, sinks);
            self.passed = packet;
            return Some(&self.passed);
        }
        self.stop_passing(sinks);
        self.sum.fill(0.0);
        for sink in sinks {
            let Some(strip) = self.strips.get_mut(&sink.id()) else {
                continue;
            };
            if !strip.live {
                continue;
            }
            if sink.options().format == SinkFormat::Opus {
                strip.fill(sink, true);
            }
            strip.add_to(&mut self.sum);
        }
        self.soft_clip.apply(&mut self.sum);
        self.encode()
    }

    pub(crate) fn silence(&mut self) -> Option<&[u8]> {
        self.sum.fill(0.0);
        self.encode()
    }

    fn waiting(&self, sink: &Sink, sinks: &[Arc<Sink>]) -> bool {
        let Some(after) = sink.waits_for() else {
            return false;
        };
        let done = sinks
            .iter()
            .find(|previous| previous.id() == after)
            .is_none_or(|previous| {
                previous.played_out() && !self.strips.get(&after).is_some_and(|strip| strip.live)
            });
        if done {
            sink.release();
        }
        !done
    }

    fn strip(&mut self, sink: &Sink) -> &mut Strip {
        self.strips
            .entry(sink.id())
            .or_insert_with(|| Strip::new(sink.volume()))
    }

    fn pass(&mut self, sink: &Sink, sinks: &[Arc<Sink>]) {
        if self.passing != Some(sink.id()) {
            self.stop_passing(sinks);
            self.passing = Some(sink.id());
        }
        sink.note_passthrough(true);
        self.stale = true;
    }

    fn stop_passing(&mut self, sinks: &[Arc<Sink>]) {
        if let Some(id) = self.passing.take()
            && let Some(sink) = sinks.iter().find(|sink| sink.id() == id)
        {
            sink.note_passthrough(false);
        }
    }

    fn encode(&mut self) -> Option<&[u8]> {
        match &mut self.encoder {
            Some(encoder) if self.stale => encoder.reset(),
            Some(_) => {}
            None => match Encoder::new(self.settings) {
                Ok(encoder) => self.encoder = Some(encoder),
                Err(error) => {
                    tracing::debug!(%error, "opus could not build the encoder");
                    return None;
                }
            },
        }
        self.stale = false;
        self.encoder.as_mut()?.encode(&self.sum)
    }
}

impl Strip {
    fn new(volume: f32) -> Self {
        Self {
            gain: volume,
            target: volume,
            live: false,
            frame: vec![0.0; FRAME_LEN],
            packet: None,
            decoder: None,
            carry: Vec::new(),
            carried: 0,
        }
    }

    fn gather(&mut self, sink: &Sink) -> bool {
        self.target = sink.volume();
        self.packet = None;
        self.live = match sink.options().format {
            SinkFormat::Pcm(_) => sink.pull_pcm(&mut self.frame),
            SinkFormat::Opus => {
                self.has_carry() || {
                    self.packet = sink.pull_opus();
                    self.packet.is_some()
                }
            }
        };
        self.live
    }

    fn has_carry(&self) -> bool {
        self.carried < self.carry.len()
    }

    #[expect(clippy::float_cmp)]
    fn passes(&self) -> bool {
        self.gain == 1.0
            && self.target == 1.0
            && !self.has_carry()
            && self
                .packet
                .as_ref()
                .is_some_and(|(packet, _)| codec::is_stereo_20ms(packet))
    }

    fn fill(&mut self, sink: &Sink, decode: bool) {
        let mut filled = 0;
        while filled < FRAME_LEN {
            let source = self.carry.get(self.carried..).unwrap_or_default();
            let target = self.frame.get_mut(filled..).unwrap_or_default();
            let copied = source.len().min(target.len());
            target
                .iter_mut()
                .zip(source)
                .for_each(|(slot, sample)| *slot = *sample);
            filled += copied;
            self.carried += copied;
            if filled == FRAME_LEN {
                break;
            }
            let Some((packet, samples)) = self.packet.take().or_else(|| sink.pull_opus()) else {
                self.frame.get_mut(filled..).unwrap_or_default().fill(0.0);
                break;
            };
            self.unpack(&packet, samples, decode);
        }
    }

    fn unpack(&mut self, packet: &[u8], samples: u32, decode: bool) {
        self.carry.clear();
        self.carry.resize(samples as usize * CHANNELS, 0.0);
        self.carried = 0;
        if !decode {
            return;
        }
        if self.decoder.is_none() {
            self.decoder = Decoder::new(SAMPLE_RATE, Channels::Stereo)
                .inspect_err(|error| tracing::debug!(%error, "opus could not create a decoder"))
                .ok();
        }
        if let Some(decoder) = &mut self.decoder {
            match decoder.decode_float(packet, &mut self.carry, false) {
                Ok(frames) => self.carry.truncate(frames * CHANNELS),
                Err(error) => tracing::debug!(%error, "opus could not decode a packet"),
            }
        }
    }

    fn add_to(&mut self, sum: &mut [f32]) {
        let start = self.gain;
        let change = self.target - start;
        let (sum, _) = sum.as_chunks_mut::<CHANNELS>();
        let (frame, _) = self.frame.as_chunks::<CHANNELS>();
        for (index, (mixed, frame)) in sum.iter_mut().zip(frame).enumerate() {
            #[expect(clippy::cast_precision_loss)]
            let gain = start + change * ((index + 1) as f32 / FRAME_SAMPLES as f32);
            mixed
                .iter_mut()
                .zip(frame)
                .for_each(|(mixed, sample)| *mixed += sample * gain);
        }
        self.gain = self.target;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::voice::codec::tests::{packet, settings};
    use crate::voice::sink::tests::{Fixture, frames_of, stereo_48k};
    use std::time::Duration;

    use crate::inbox::Delivery;
    use crate::voice::types::{SILENCE_FRAME, SinkOptions};

    fn opus_sink(fixture: &mut Fixture) -> Arc<Sink> {
        Arc::new(fixture.sink(SinkFormat::Opus, 1_000, StallPolicy::Hold))
    }

    fn pcm_sink(fixture: &mut Fixture, value: f32, frames: usize) -> Arc<Sink> {
        let sink = Arc::new(fixture.sink(stereo_48k(), 1_000, StallPolicy::Hold));
        sink.write(&frames_of(value, frames)).unwrap();
        sink
    }

    fn pcm_after(fixture: &mut Fixture, previous: &Sink) -> Arc<Sink> {
        Arc::new(fixture.sink_with(SinkOptions {
            format: stereo_48k(),
            buffer: Duration::from_secs(1),
            volume: 1.0,
            stall: StallPolicy::Hold,
            after: Some(previous.id()),
        }))
    }

    fn consumed(sink: &Sink) -> u64 {
        sink.stats().unwrap().consumed
    }

    fn mixed(mixer: &mut Mixer, sinks: &[Arc<Sink>]) -> Option<Vec<u8>> {
        mixer.mix(sinks).map(<[u8]>::to_vec)
    }

    #[test]
    fn a_lone_stereo_20_ms_opus_input_passes_through_once_its_ramp_ends() {
        let mut fixture = Fixture::new();
        let sink = opus_sink(&mut fixture);
        let original = packet(Channels::Stereo, 960);
        let mut mixer = Mixer::new(settings()).unwrap();

        let passed: Vec<(bool, bool)> = [0.5, 1.0, 1.0]
            .into_iter()
            .map(|volume| {
                sink.set_volume(volume).unwrap();
                sink.write(&original).unwrap();
                let result = mixed(&mut mixer, std::slice::from_ref(&sink)).unwrap();
                (result == original, sink.passthrough().unwrap())
            })
            .collect();

        assert_eq!(passed, [(false, false), (false, false), (true, true)]);
    }

    #[test]
    fn other_opus_inputs_are_decoded_and_encoded() {
        let mut fixture = Fixture::new();
        let quiet = opus_sink(&mut fixture);
        quiet.set_volume(0.5).unwrap();
        let mono = opus_sink(&mut fixture);
        let long = opus_sink(&mut fixture);
        let accompanied = opus_sink(&mut fixture);
        quiet.write(&packet(Channels::Stereo, 960)).unwrap();
        mono.write(&packet(Channels::Mono, 960)).unwrap();
        long.write(&packet(Channels::Stereo, 2_880)).unwrap();
        accompanied.write(&packet(Channels::Stereo, 960)).unwrap();
        let pcm = pcm_sink(&mut fixture, 0.1, 960);

        for sinks in [vec![quiet], vec![mono], vec![long], vec![accompanied, pcm]] {
            let mut mixer = Mixer::new(settings()).unwrap();
            let packet = mixed(&mut mixer, &sinks).unwrap();
            assert!(sinks.iter().all(|sink| !sink.passthrough().unwrap()));
            assert_eq!(codec::packet_samples(&packet), Some(960));
        }
    }

    #[test]
    fn a_passing_sink_loses_its_flag_when_mixing_resumes() {
        let mut fixture = Fixture::new();
        let opus = opus_sink(&mut fixture);
        opus.write(&packet(Channels::Stereo, 960)).unwrap();
        opus.write(&packet(Channels::Stereo, 960)).unwrap();
        let mut mixer = Mixer::new(settings()).unwrap();
        mixer.mix(std::slice::from_ref(&opus)).unwrap();
        let pcm = pcm_sink(&mut fixture, 0.1, 960);

        mixer.mix(&[Arc::clone(&opus), pcm]).unwrap();

        assert!(!opus.passthrough().unwrap());
    }

    #[test]
    fn long_and_mono_packets_decode_with_a_carry_across_ticks() {
        let mut fixture = Fixture::new();
        let long = opus_sink(&mut fixture);
        long.write(&packet(Channels::Stereo, 2_880)).unwrap();
        let mono = opus_sink(&mut fixture);
        mono.write(&packet(Channels::Mono, 2_880)).unwrap();
        let mut mixer = Mixer::new(settings()).unwrap();

        let mut ticks = Vec::new();
        for _ in 0..4 {
            ticks.push(mixer.mix(&[Arc::clone(&long), Arc::clone(&mono)]).is_some());
            if ticks.len() == 1 {
                assert_eq!((consumed(&long), consumed(&mono)), (2_880, 2_880));
            }
            if ticks.len() == 2 {
                let frame = &mixer.strips[&mono.id()].frame;
                assert!(
                    frame
                        .as_chunks::<2>()
                        .0
                        .iter()
                        .all(|[left, right]| left.to_bits() == right.to_bits())
                );
                assert!(frame.iter().any(|sample| *sample != 0.0));
            }
        }

        assert_eq!(ticks, [true, true, true, false]);
    }

    #[test]
    fn short_packets_are_pulled_until_the_frame_is_full() {
        let mut fixture = Fixture::new();
        let sink = opus_sink(&mut fixture);
        for _ in 0..4 {
            sink.write(&packet(Channels::Stereo, 480)).unwrap();
        }
        let mut mixer = Mixer::new(settings()).unwrap();

        let ticks: Vec<bool> = (0..3)
            .map(|_| mixer.mix(std::slice::from_ref(&sink)).is_some())
            .collect();

        assert_eq!(ticks, [true, true, false]);
        assert_eq!(consumed(&sink), 1_920);
        assert!(!sink.passthrough().unwrap());
    }

    #[test]
    fn two_inputs_sum() {
        let mut fixture = Fixture::new();
        let sinks = [
            pcm_sink(&mut fixture, 0.25, 960),
            pcm_sink(&mut fixture, 0.125, 960),
        ];
        let mut mixer = Mixer::new(settings()).unwrap();

        mixer.mix(&sinks).unwrap();

        assert!(mixer.sum.iter().all(|sample| (sample - 0.375).abs() < 1e-6));
    }

    #[test]
    fn a_sink_after_another_starts_on_the_tick_after_it_played_out() {
        let mut fixture = Fixture::new();
        let first = pcm_sink(&mut fixture, 0.25, 1_920);
        first.flush().unwrap();
        let second = pcm_after(&mut fixture, &first);
        let (start, _) = second.mark().unwrap();
        second.write(&frames_of(0.5, 1_920)).unwrap();
        let sinks = [Arc::clone(&first), Arc::clone(&second)];
        let mut mixer = Mixer::new(settings()).unwrap();

        let mut heard = Vec::new();
        let mut started = Vec::new();
        for _ in 0..4 {
            mixer.mix(&sinks).unwrap();
            heard.push(mixer.sum[0]);
            started.push(fixture.inbox.drain().iter().any(|delivery| {
                matches!(delivery, Delivery::VoiceMarker { handle, marker, played: true }
                    if *handle == second.id() && *marker == start)
            }));
        }

        assert_eq!(heard, [0.25, 0.25, 0.5, 0.5]);
        assert_eq!(started, [false, false, true, false]);
    }

    #[test]
    fn a_sink_after_an_open_unflushed_one_waits_until_it_closes() {
        let mut fixture = Fixture::new();
        let first = pcm_sink(&mut fixture, 0.25, 960);
        let second = pcm_after(&mut fixture, &first);
        second.write(&frames_of(0.5, 960)).unwrap();
        let mut mixer = Mixer::new(settings()).unwrap();
        mixer
            .mix(&[Arc::clone(&first), Arc::clone(&second)])
            .unwrap();

        let dry = mixer
            .mix(&[Arc::clone(&first), Arc::clone(&second)])
            .is_none();
        first.close();
        mixer.mix(std::slice::from_ref(&second)).unwrap();

        assert!(dry);
        assert_eq!(mixer.sum[0], 0.5);
    }

    #[test]
    fn a_volume_change_ramps_linearly_over_one_frame() {
        let mut fixture = Fixture::new();
        let sinks = [pcm_sink(&mut fixture, 0.5, 1_920)];
        let mut mixer = Mixer::new(settings()).unwrap();
        mixer.mix(&sinks).unwrap();

        sinks[0].set_volume(0.0).unwrap();
        mixer.mix(&sinks).unwrap();

        let left: Vec<f32> = mixer.sum.iter().step_by(2).copied().collect();
        for (step, sample) in (1_u16..).zip(&left) {
            let expected = 0.5 * (1.0 - f32::from(step) / 960.0);
            assert!((sample - expected).abs() < 1e-6, "sample {step}: {sample}");
        }
        assert_eq!(left[959], 0.0);
    }

    #[test]
    fn soft_clip_keeps_a_loud_mix_within_full_scale() {
        let mut fixture = Fixture::new();
        let sinks = [
            pcm_sink(&mut fixture, 0.9, 960),
            pcm_sink(&mut fixture, 0.9, 960),
        ];
        let mut mixer = Mixer::new(settings()).unwrap();

        mixer.mix(&sinks).unwrap();

        assert!(mixer.sum.iter().all(|sample| sample.abs() <= 1.0));
    }

    #[test]
    fn the_encoder_restarts_from_a_clean_state_after_passthrough() {
        let mut fixture = Fixture::new();
        let mut fresh = Mixer::new(settings()).unwrap();
        let mut resumed = Mixer::new(settings()).unwrap();
        let mut continued = Mixer::new(settings()).unwrap();
        for mixer in [&mut resumed, &mut continued] {
            let warmup = pcm_sink(&mut fixture, 0.3, 1_920);
            for _ in 0..2 {
                mixer.mix(std::slice::from_ref(&warmup)).unwrap();
            }
        }
        let opus = opus_sink(&mut fixture);
        opus.write(&packet(Channels::Stereo, 960)).unwrap();
        resumed.mix(std::slice::from_ref(&opus)).unwrap();
        assert!(opus.passthrough().unwrap());

        let encoded: Vec<Vec<u8>> = [&mut fresh, &mut resumed, &mut continued]
            .into_iter()
            .map(|mixer| {
                let sink = pcm_sink(&mut fixture, 0.2, 960);
                mixed(mixer, &[sink]).unwrap()
            })
            .collect();

        assert_eq!(encoded[1], encoded[0]);
        assert_ne!(encoded[2], encoded[0]);
    }

    #[test]
    fn retain_keeps_state_only_for_open_sinks() {
        let mut fixture = Fixture::new();
        let mut mixer = Mixer::new(settings()).unwrap();
        let mut open = Vec::new();
        let stereo = packet(Channels::Stereo, 960);
        let mono = packet(Channels::Mono, 960);

        for index in 0..3 {
            let sink = opus_sink(&mut fixture);
            sink.write(if index % 2 == 0 { &stereo } else { &mono })
                .unwrap();
            open.push(sink);
            mixer.mix(&open).unwrap();
            if index % 3 != 0 {
                open.remove(0).close();
            }
            open.retain(|sink| !sink.is_closed());
            mixer.retain(&open);
            assert!(mixer.strips.len() <= open.len());
        }
    }

    #[test]
    fn discard_pulls_at_the_clock_rate_without_decoding() {
        let mut fixture = Fixture::new();
        let opus = opus_sink(&mut fixture);
        opus.write(&packet(Channels::Stereo, 2_880)).unwrap();
        let (marker, _) = opus.mark().unwrap();
        let pcm = pcm_sink(&mut fixture, 0.1, 1_920);
        let sinks = [Arc::clone(&opus), Arc::clone(&pcm)];
        let mut mixer = Mixer::new(settings()).unwrap();

        mixer.discard(&sinks);
        let after_one = (consumed(&opus), consumed(&pcm));
        mixer.discard(&sinks);
        mixer.discard(&sinks);

        assert_eq!(after_one, (2_880, 960));
        assert_eq!(consumed(&pcm), 1_920);
        assert!(fixture.inbox.drain().iter().any(|delivery| matches!(
            delivery,
            Delivery::VoiceMarker { marker: id, played: true, .. } if *id == marker
        )));
        assert!(mixer.mix(&sinks).is_none());
    }

    #[test]
    fn hold_drops_the_carry_of_drop_sinks_only() {
        let mut fixture = Fixture::new();
        let dropping = Arc::new(fixture.sink(SinkFormat::Opus, 1_000, StallPolicy::Drop));
        let holding = opus_sink(&mut fixture);
        let mut mixer = Mixer::new(settings()).unwrap();
        for sink in [&dropping, &holding] {
            sink.write(&packet(Channels::Stereo, 2_880)).unwrap();
            mixer.mix(std::slice::from_ref(sink)).unwrap();
        }

        mixer.hold(&[Arc::clone(&dropping), Arc::clone(&holding)]);

        assert!(mixer.mix(&[dropping]).is_none());
        assert!(mixer.mix(&[holding]).is_some());
    }

    #[test]
    fn nothing_to_mix_returns_nothing() {
        let mut fixture = Fixture::new();
        let sinks = [opus_sink(&mut fixture), pcm_sink(&mut fixture, 0.1, 100)];
        let mut mixer = Mixer::new(settings()).unwrap();

        assert!(mixer.mix(&[]).is_none());
        assert!(mixer.mix(&sinks).is_none());
    }

    #[test]
    fn silence_is_an_encoded_stereo_frame_and_not_the_plain_silence_packet() {
        let mut mixer = Mixer::new(settings()).unwrap();

        let silence = mixer.silence().unwrap().to_vec();

        assert_ne!(silence, SILENCE_FRAME);
        assert_eq!(codec::packet_samples(&silence), Some(960));
        assert!(matches!(
            opus::packet::get_nb_channels(&silence),
            Ok(Channels::Stereo)
        ));
    }

    #[test]
    fn encoder_changes_are_validated_and_reach_the_encoder_built_on_first_use() {
        let mut mixer = Mixer::new(settings()).unwrap();
        let faster = EncoderSettings {
            bitrate: 128_000,
            ..settings()
        };

        assert!(
            mixer
                .set_encoder(EncoderSettings {
                    complexity: 11,
                    ..settings()
                })
                .is_err()
        );
        mixer.set_encoder(faster).unwrap();
        let silence = mixer.silence().unwrap().to_vec();

        let silence_with = |settings| {
            let mut encoder = Encoder::new(settings).unwrap();
            encoder.encode(&[0.0; FRAME_LEN]).unwrap().to_vec()
        };
        assert_eq!(silence, silence_with(faster));
        assert_ne!(silence, silence_with(settings()));
    }
}
