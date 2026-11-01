use std::collections::VecDeque;
use std::mem;
use std::sync::Arc;
use std::time::Duration;

use bytes::Bytes;
use parking_lot::{Mutex, MutexGuard};

use crate::error::CoreError;
use crate::ids::Handle;
use crate::inbox::{Delivery, Inbox};
use crate::runtime::Owner;
use crate::voice::codec;
use crate::voice::convert::Converter;
use crate::voice::types::{
    CHANNELS, FRAME_LEN, FRAME_SAMPLES, NANOS_PER_SECOND, SAMPLE_RATE, SinkFormat, SinkOptions,
    StallPolicy,
};

const FRAME_FRAMES: u64 = FRAME_SAMPLES as u64;

pub struct Sink {
    id: Handle,
    options: SinkOptions,
    owner: Owner,
    inbox: Arc<Inbox>,
    high_water: u64,
    low_water: u64,
    producer: Mutex<Producer>,
    queue: Mutex<Queue>,
}

enum Producer {
    Pcm {
        converter: Converter,
        scratch: Vec<f32>,
    },
    Opus,
}

#[derive(Default)]
struct Queue {
    pcm: VecDeque<f32>,
    opus: VecDeque<(Bytes, u32)>,
    queued: u64,
    written: u64,
    owed: u64,
    markers: VecDeque<Marker>,
    next_marker: u64,
    tainted: bool,
    wake_armed: bool,
    volume: f32,
    closed: bool,
    passthrough: bool,
    playing: bool,
    flushed: bool,
    stats: SinkStats,
}

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct SinkStats {
    pub sent: u64,
    pub consumed: u64,
    pub underruns: u64,
    pub held_ticks: u64,
    pub dropped: u64,
    pub passthrough_packets: u64,
}

#[derive(Clone, Copy)]
struct Marker {
    id: u64,
    position: u64,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Fate {
    Pulled,
    Stalled,
    Cleared,
}

impl Sink {
    pub fn new(id: Handle, options: SinkOptions, inbox: Arc<Inbox>) -> Result<Self, CoreError> {
        options.validate()?;
        let producer = match options.format {
            SinkFormat::Pcm(format) => Producer::Pcm {
                converter: Converter::new(format)?,
                scratch: Vec::new(),
            },
            SinkFormat::Opus => Producer::Opus,
        };
        let high_water = frames_in(options.buffer);
        Ok(Self {
            id,
            options,
            owner: Owner::current(),
            inbox,
            high_water,
            low_water: high_water / 2,
            producer: Mutex::new(producer),
            queue: Mutex::new(Queue {
                volume: options.volume,
                ..Queue::default()
            }),
        })
    }

    pub fn id(&self) -> Handle {
        self.id
    }

    pub fn options(&self) -> &SinkOptions {
        &self.options
    }

    pub fn write(&self, data: &[u8]) -> Result<bool, CoreError> {
        self.owner.check()?;
        let mut producer = self.producer.lock();
        {
            let mut queue = self.open_queue()?;
            if queue.queued >= self.high_water {
                queue.wake_armed = true;
                return Ok(false);
            }
        }
        match &mut *producer {
            Producer::Pcm { converter, scratch } => {
                scratch.clear();
                converter.push(data, scratch);
                let mut queue = self.open_queue()?;
                queue.pcm.extend(scratch.iter().copied());
                queue.add((scratch.len() / CHANNELS) as u64);
            }
            Producer::Opus => {
                let samples = codec::packet_samples(data).ok_or_else(|| {
                    CoreError::InvalidRequest("the data is not a valid Opus packet".to_owned())
                })?;
                let packet = Bytes::copy_from_slice(data);
                let mut queue = self.open_queue()?;
                queue.opus.push_back((packet, samples));
                queue.add(u64::from(samples));
            }
        }
        Ok(true)
    }

    pub fn mark(&self) -> Result<(u64, u64), CoreError> {
        self.owner.check()?;
        let mut queue = self.open_queue()?;
        Ok(self.place_marker(&mut queue))
    }

    pub fn flush(&self) -> Result<(u64, u64), CoreError> {
        self.owner.check()?;
        let mut producer = self.producer.lock();
        let mut queue = self.open_queue()?;
        if let Producer::Pcm { converter, scratch } = &mut *producer {
            scratch.clear();
            converter.flush(scratch);
            let before = queue.pcm.len();
            queue.pcm.extend(scratch.iter().copied());
            let partial = queue.pcm.len() % FRAME_LEN;
            if partial > 0 {
                let padded = queue.pcm.len() + FRAME_LEN - partial;
                queue.pcm.resize(padded, 0.0);
            }
            let added = (queue.pcm.len() - before) / CHANNELS;
            queue.add(added as u64);
        }
        queue.flushed = true;
        Ok(self.place_marker(&mut queue))
    }

    pub fn clear(&self, until: Option<u64>) -> Result<(), CoreError> {
        self.owner.check()?;
        let mut producer = self.producer.lock();
        let mut queue = self.open_queue()?;
        let end = match until {
            None => queue.written,
            Some(id) => match queue.markers.iter().find(|marker| marker.id == id) {
                Some(marker) => marker.position,
                None => return Ok(()),
            },
        };
        let frames = end.saturating_sub(queue.stats.consumed);
        let taken = queue.take(frames);
        self.consume(&mut queue, taken, Fate::Cleared);
        self.wake(&mut queue);
        if until.is_none()
            && let Producer::Pcm { converter, .. } = &mut *producer
        {
            converter.reset();
        }
        Ok(())
    }

    pub fn set_volume(&self, volume: f32) -> Result<(), CoreError> {
        self.owner.check()?;
        SinkOptions {
            volume,
            ..self.options
        }
        .validate()?;
        self.open_queue()?.volume = volume;
        Ok(())
    }

    pub fn queued(&self) -> Result<Duration, CoreError> {
        self.owner.check()?;
        let frames = self.queue.lock().queued;
        Ok(Duration::from_secs(frames) / SAMPLE_RATE)
    }

    pub fn passthrough(&self) -> Result<bool, CoreError> {
        self.owner.check()?;
        Ok(self.queue.lock().passthrough)
    }

    pub fn stats(&self) -> Result<SinkStats, CoreError> {
        self.owner.check()?;
        Ok(self.queue.lock().stats)
    }

    pub fn is_closed(&self) -> bool {
        self.queue.lock().closed
    }

    pub fn close(&self) {
        if !self.owner.is_current() {
            return;
        }
        let mut queue = self.queue.lock();
        if mem::replace(&mut queue.closed, true) {
            return;
        }
        queue.passthrough = false;
        let taken = queue.take(u64::MAX);
        self.consume(&mut queue, taken, Fate::Cleared);
        while let Some(marker) = queue.markers.pop_front() {
            self.resolve(marker.id, false);
        }
        self.wake(&mut queue);
    }

    pub fn volume(&self) -> f32 {
        self.queue.lock().volume
    }

    pub fn pull_pcm(&self, frame: &mut [f32]) -> bool {
        let mut queue = self.queue.lock();
        if queue.pcm.len() < FRAME_LEN {
            queue.starve();
            return false;
        }
        frame
            .iter_mut()
            .zip(queue.pcm.drain(..FRAME_LEN))
            .for_each(|(slot, sample)| *slot = sample);
        queue.playing = true;
        self.consume(&mut queue, FRAME_FRAMES, Fate::Pulled);
        true
    }

    pub fn pull_opus(&self) -> Option<(Bytes, u32)> {
        let mut queue = self.queue.lock();
        let Some((packet, samples)) = queue.opus.pop_front() else {
            queue.starve();
            return None;
        };
        queue.playing = true;
        self.consume(&mut queue, u64::from(samples), Fate::Pulled);
        Some((packet, samples))
    }

    pub fn hold(&self) {
        let mut queue = self.queue.lock();
        queue.stats.held_ticks += 1;
        if self.options.stall == StallPolicy::Hold {
            return;
        }
        queue.owed += FRAME_FRAMES;
        let owed = queue.owed;
        let taken = queue.take(owed);
        queue.owed -= taken;
        self.consume(&mut queue, taken, Fate::Stalled);
        if queue.queued == 0 {
            queue.owed = 0;
        }
    }

    pub fn note_passthrough(&self, passing: bool) {
        let mut queue = self.queue.lock();
        queue.passthrough = passing;
        if passing {
            queue.stats.passthrough_packets += 1;
        }
    }

    fn open_queue(&self) -> Result<MutexGuard<'_, Queue>, CoreError> {
        let queue = self.queue.lock();
        if queue.closed {
            return Err(CoreError::SinkClosed(self.id));
        }
        Ok(queue)
    }

    fn place_marker(&self, queue: &mut Queue) -> (u64, u64) {
        let id = queue.next_marker;
        queue.next_marker += 1;
        let position = queue.written;
        if position <= queue.stats.consumed {
            let played = !mem::take(&mut queue.tainted);
            self.resolve(id, played);
        } else {
            queue.markers.push_back(Marker { id, position });
        }
        (id, position)
    }

    fn consume(&self, queue: &mut Queue, frames: u64, fate: Fate) {
        queue.queued = queue.queued.saturating_sub(frames);
        match fate {
            Fate::Pulled => queue.stats.sent += frames,
            Fate::Stalled => queue.stats.dropped += frames,
            Fate::Cleared => {}
        }
        let dropped = fate != Fate::Pulled && frames > 0;
        let end = queue.stats.consumed + frames;
        let mut cursor = queue.stats.consumed;
        while let Some(marker) = queue.markers.front().copied()
            && marker.position <= end
        {
            queue.markers.pop_front();
            let played = !(queue.tainted || (dropped && marker.position > cursor));
            queue.tainted = false;
            cursor = cursor.max(marker.position);
            self.resolve(marker.id, played);
        }
        queue.tainted |= dropped && end > cursor;
        queue.stats.consumed = end;
        if queue.queued <= self.low_water {
            self.wake(queue);
        }
    }

    fn resolve(&self, marker: u64, played: bool) {
        self.inbox.push(Delivery::VoiceMarker {
            handle: self.id,
            marker,
            played,
        });
    }

    fn wake(&self, queue: &mut Queue) {
        if mem::take(&mut queue.wake_armed) {
            self.inbox.push(Delivery::VoiceWake { handle: self.id });
        }
    }
}

impl Queue {
    fn add(&mut self, frames: u64) {
        self.queued += frames;
        self.written += frames;
        self.flushed = false;
    }

    fn take(&mut self, frames: u64) -> u64 {
        let samples = usize::try_from(frames)
            .unwrap_or(usize::MAX)
            .saturating_mul(CHANNELS)
            .min(self.pcm.len());
        self.pcm.drain(..samples);
        let mut taken = (samples / CHANNELS) as u64;
        while let Some(&(_, samples)) = self.opus.front()
            && taken + u64::from(samples) <= frames
        {
            taken += u64::from(samples);
            self.opus.pop_front();
        }
        taken
    }

    fn starve(&mut self) {
        if mem::take(&mut self.playing) && !self.flushed {
            self.stats.underruns += 1;
        }
    }
}

fn frames_in(duration: Duration) -> u64 {
    let frames = duration.as_nanos() * u128::from(SAMPLE_RATE) / NANOS_PER_SECOND;
    u64::try_from(frames).unwrap_or(u64::MAX)
}

#[cfg(test)]
pub(crate) mod tests {
    use opus::Channels;

    use super::*;
    use crate::bell::{self, BellReceiver};
    use crate::voice::codec::tests::packet;
    use crate::voice::types::{PcmFormat, SampleFormat};

    pub(crate) struct Fixture {
        pub(crate) inbox: Arc<Inbox>,
        _bell: BellReceiver,
        next: u64,
    }

    impl Fixture {
        pub(crate) fn new() -> Self {
            let (bell, receiver) = bell::pair().unwrap();
            Self {
                inbox: Arc::new(Inbox::new(bell)),
                _bell: receiver,
                next: 1,
            }
        }

        pub(crate) fn sink(
            &mut self,
            format: SinkFormat,
            buffer_ms: u64,
            stall: StallPolicy,
        ) -> Sink {
            let id = Handle::from(self.next);
            self.next += 1;
            let options = SinkOptions {
                format,
                buffer: Duration::from_millis(buffer_ms),
                volume: 1.0,
                stall,
            };
            Sink::new(id, options, Arc::clone(&self.inbox)).unwrap()
        }

        fn markers(&self) -> Vec<(u64, bool)> {
            self.inbox
                .drain()
                .into_iter()
                .filter_map(|delivery| match delivery {
                    Delivery::VoiceMarker { marker, played, .. } => Some((marker, played)),
                    _ => None,
                })
                .collect()
        }

        fn wakes(&self) -> usize {
            self.inbox
                .drain()
                .iter()
                .filter(|delivery| matches!(delivery, Delivery::VoiceWake { .. }))
                .count()
        }
    }

    pub(crate) fn stereo_48k() -> SinkFormat {
        SinkFormat::Pcm(PcmFormat {
            rate: 48_000,
            channels: 2,
            sample: SampleFormat::F32Le,
        })
    }

    pub(crate) fn frames_of(value: f32, frames: usize) -> Vec<u8> {
        std::iter::repeat_n(value.to_le_bytes(), frames * CHANNELS)
            .flatten()
            .collect()
    }

    fn pull(sink: &Sink) -> Option<f32> {
        let mut frame = vec![0.0; FRAME_LEN];
        sink.pull_pcm(&mut frame).then(|| frame[0])
    }

    #[test]
    fn writes_are_accepted_below_the_high_water_mark_and_then_arm_one_wake() {
        let mut fixture = Fixture::new();
        let sink = fixture.sink(stereo_48k(), 100, StallPolicy::Hold);

        assert!(sink.write(&frames_of(0.1, 2_880)).unwrap());
        assert!(sink.write(&frames_of(0.1, 2_880)).unwrap());
        assert!(!sink.write(&frames_of(0.1, 960)).unwrap());
        assert!(!sink.write(&frames_of(0.1, 960)).unwrap());

        assert_eq!(sink.queued().unwrap(), Duration::from_millis(120));
        assert_eq!(fixture.wakes(), 0);
    }

    #[test]
    fn the_wake_fires_once_the_queue_is_down_to_half_the_buffer() {
        let mut fixture = Fixture::new();
        let sink = fixture.sink(stereo_48k(), 100, StallPolicy::Hold);
        sink.write(&frames_of(0.1, 5_280)).unwrap();
        assert!(!sink.write(&frames_of(0.1, 960)).unwrap());

        let mut wakes = Vec::new();
        for _ in 0..5 {
            pull(&sink).unwrap();
            wakes.push(fixture.wakes());
        }

        assert_eq!(wakes, [0, 0, 1, 0, 0]);
    }

    #[test]
    fn a_marker_reports_its_position_and_resolves_once_its_audio_is_pulled() {
        let mut fixture = Fixture::new();
        let sink = fixture.sink(stereo_48k(), 1_000, StallPolicy::Hold);
        sink.write(&frames_of(0.1, 1_920)).unwrap();

        let (id, position) = sink.mark().unwrap();
        pull(&sink).unwrap();
        let after_one = fixture.markers();
        pull(&sink).unwrap();

        assert_eq!(position, 1_920);
        assert!(after_one.is_empty());
        assert_eq!(fixture.markers(), [(id, true)]);
    }

    #[test]
    fn a_marker_on_an_empty_sink_resolves_at_once() {
        let mut fixture = Fixture::new();
        let sink = fixture.sink(stereo_48k(), 1_000, StallPolicy::Hold);

        let (id, position) = sink.mark().unwrap();

        assert_eq!(position, 0);
        assert_eq!(fixture.markers(), [(id, true)]);
    }

    #[test]
    fn markers_resolve_false_when_their_audio_is_dropped_cleared_or_closed() {
        let mut fixture = Fixture::new();
        let dropping = fixture.sink(stereo_48k(), 1_000, StallPolicy::Drop);
        let clearing = fixture.sink(stereo_48k(), 1_000, StallPolicy::Hold);
        let closing = fixture.sink(stereo_48k(), 1_000, StallPolicy::Hold);
        for sink in [&dropping, &clearing, &closing] {
            sink.write(&frames_of(0.1, 1_920)).unwrap();
            sink.mark().unwrap();
        }

        dropping.hold();
        dropping.hold();
        let dropped = fixture.markers();
        clearing.clear(None).unwrap();
        let cleared = fixture.markers();
        closing.close();
        let closed = fixture.markers();

        assert_eq!(dropped, [(0, false)]);
        assert_eq!(cleared, [(0, false)]);
        assert_eq!(closed, [(0, false)]);
    }

    #[test]
    fn a_marker_after_a_drop_starts_clean_once_the_dropped_range_resolved() {
        let mut fixture = Fixture::new();
        let sink = fixture.sink(stereo_48k(), 1_000, StallPolicy::Drop);
        sink.write(&frames_of(0.1, 960)).unwrap();
        let (first, _) = sink.mark().unwrap();
        sink.write(&frames_of(0.2, 960)).unwrap();
        let (second, _) = sink.mark().unwrap();

        sink.hold();
        pull(&sink).unwrap();

        assert_eq!(fixture.markers(), [(first, false), (second, true)]);
    }

    #[test]
    fn clear_until_a_marker_keeps_the_audio_after_it() {
        let mut fixture = Fixture::new();
        let sink = fixture.sink(stereo_48k(), 1_000, StallPolicy::Hold);
        sink.write(&frames_of(0.1, 1_920)).unwrap();
        let (first, _) = sink.mark().unwrap();
        sink.write(&frames_of(0.2, 960)).unwrap();
        let (second, _) = sink.mark().unwrap();

        sink.clear(Some(first)).unwrap();
        let cleared = fixture.markers();
        let queued = sink.queued().unwrap();
        let next = pull(&sink);

        assert_eq!(cleared, [(first, false)]);
        assert_eq!(queued, Duration::from_millis(20));
        assert_eq!(next, Some(0.2));
        assert_eq!(fixture.markers(), [(second, true)]);
    }

    #[test]
    fn clear_until_a_passed_marker_keeps_everything() {
        let mut fixture = Fixture::new();
        let sink = fixture.sink(stereo_48k(), 1_000, StallPolicy::Hold);
        let (passed, _) = sink.mark().unwrap();
        sink.write(&frames_of(0.1, 960)).unwrap();

        sink.clear(Some(passed)).unwrap();

        assert_eq!(sink.queued().unwrap(), Duration::from_millis(20));
    }

    #[test]
    fn clear_fires_an_armed_wake_and_resets_the_converter() {
        let mut fixture = Fixture::new();
        let format = SinkFormat::Pcm(PcmFormat {
            rate: 24_000,
            channels: 2,
            sample: SampleFormat::F32Le,
        });
        let sink = fixture.sink(format, 20, StallPolicy::Hold);
        sink.write(&frames_of(0.1, 1_000)).unwrap();
        assert!(!sink.write(&frames_of(0.1, 10)).unwrap());

        sink.clear(None).unwrap();
        let wakes = fixture.wakes();
        let (_, position) = sink.flush().unwrap();

        assert_eq!(wakes, 1);
        assert_eq!(sink.queued().unwrap(), Duration::ZERO);
        assert_eq!(position, sink.stats().unwrap().consumed);
    }

    #[test]
    fn close_discards_the_queue_fires_the_wake_and_refuses_writes() {
        let mut fixture = Fixture::new();
        let sink = fixture.sink(stereo_48k(), 20, StallPolicy::Hold);
        sink.write(&frames_of(0.1, 960)).unwrap();
        assert!(!sink.write(&frames_of(0.1, 960)).unwrap());

        sink.close();
        sink.close();

        assert!(sink.is_closed());
        assert_eq!(fixture.wakes(), 1);
        assert_eq!(sink.queued().unwrap(), Duration::ZERO);
        assert!(pull(&sink).is_none());
        assert!(matches!(
            sink.write(&frames_of(0.1, 960)),
            Err(CoreError::SinkClosed(handle)) if handle == sink.id()
        ));
        assert!(matches!(sink.mark(), Err(CoreError::SinkClosed(_))));
        assert!(matches!(sink.flush(), Err(CoreError::SinkClosed(_))));
        assert!(matches!(sink.clear(None), Err(CoreError::SinkClosed(_))));
        assert!(matches!(
            sink.set_volume(0.5),
            Err(CoreError::SinkClosed(_))
        ));
    }

    #[test]
    fn a_hold_sink_keeps_its_audio_and_a_drop_sink_drops_20_ms_per_held_tick() {
        let mut fixture = Fixture::new();
        let holding = fixture.sink(stereo_48k(), 1_000, StallPolicy::Hold);
        let dropping = fixture.sink(stereo_48k(), 1_000, StallPolicy::Drop);
        for sink in [&holding, &dropping] {
            sink.write(&frames_of(0.1, 4_800)).unwrap();
            for _ in 0..3 {
                sink.hold();
            }
        }

        assert_eq!(holding.queued().unwrap(), Duration::from_millis(100));
        assert_eq!(holding.stats().unwrap().held_ticks, 3);
        assert_eq!(holding.stats().unwrap().dropped, 0);
        assert_eq!(dropping.queued().unwrap(), Duration::from_millis(40));
        assert_eq!(dropping.stats().unwrap().held_ticks, 3);
        assert_eq!(dropping.stats().unwrap().dropped, 2_880);
    }

    #[test]
    fn a_drop_sink_drops_long_opus_packets_at_the_rate_of_the_clock() {
        let mut fixture = Fixture::new();
        let sink = fixture.sink(SinkFormat::Opus, 1_000, StallPolicy::Drop);
        for _ in 0..2 {
            sink.write(&packet(Channels::Stereo, 2_880)).unwrap();
        }

        let queued: Vec<Duration> = (0..4)
            .map(|_| {
                sink.hold();
                sink.queued().unwrap()
            })
            .collect();

        assert_eq!(queued, [120, 120, 60, 60].map(Duration::from_millis),);
    }

    #[test]
    fn an_underrun_is_counted_once_per_starve() {
        let mut fixture = Fixture::new();
        let sink = fixture.sink(stereo_48k(), 1_000, StallPolicy::Hold);
        assert!(pull(&sink).is_none());
        let before_playing = sink.stats().unwrap().underruns;

        for _ in 0..2 {
            sink.write(&frames_of(0.1, 960)).unwrap();
            pull(&sink).unwrap();
            pull(&sink);
            pull(&sink);
        }

        assert_eq!(before_playing, 0);
        assert_eq!(sink.stats().unwrap().underruns, 2);
    }

    #[test]
    fn running_dry_after_a_flush_is_no_underrun_until_the_next_write() {
        let mut fixture = Fixture::new();
        let sink = fixture.sink(stereo_48k(), 1_000, StallPolicy::Hold);
        sink.write(&frames_of(0.1, 960)).unwrap();
        sink.flush().unwrap();

        pull(&sink).unwrap();
        assert!(pull(&sink).is_none());
        let after_the_flush = sink.stats().unwrap().underruns;
        sink.write(&frames_of(0.1, 960)).unwrap();
        pull(&sink).unwrap();
        assert!(pull(&sink).is_none());

        assert_eq!(after_the_flush, 0);
        assert_eq!(sink.stats().unwrap().underruns, 1);
    }

    #[test]
    fn consumed_samples_counts_pulled_dropped_and_cleared_frames() {
        let mut fixture = Fixture::new();
        let sink = fixture.sink(stereo_48k(), 1_000, StallPolicy::Drop);
        sink.write(&frames_of(0.1, 4_800)).unwrap();

        pull(&sink).unwrap();
        sink.hold();
        sink.clear(None).unwrap();

        assert_eq!(sink.stats().unwrap().sent, 960);
        assert_eq!(sink.stats().unwrap().dropped, 960);
        assert_eq!(sink.stats().unwrap().consumed, 4_800);
    }

    #[test]
    fn flush_pushes_the_resampler_tail_and_pads_to_a_whole_frame() {
        let mut fixture = Fixture::new();
        let format = SinkFormat::Pcm(PcmFormat {
            rate: 24_000,
            channels: 1,
            sample: SampleFormat::S16Le,
        });
        let sink = fixture.sink(format, 1_000, StallPolicy::Hold);
        sink.write(&vec![0x10; 480]).unwrap();

        let (id, position) = sink.flush().unwrap();
        pull(&sink).unwrap();

        assert_eq!(position, 960);
        assert_eq!(fixture.markers(), [(id, true)]);
        assert!(pull(&sink).is_none());
    }

    #[test]
    fn opus_sinks_validate_and_queue_whole_packets() {
        let mut fixture = Fixture::new();
        let sink = fixture.sink(SinkFormat::Opus, 1_000, StallPolicy::Hold);
        let long = packet(Channels::Stereo, 2_880);

        assert!(matches!(
            sink.write(&[0x03]),
            Err(CoreError::InvalidRequest(_))
        ));
        assert!(sink.write(&long).unwrap());
        let (_, position) = sink.mark().unwrap();

        assert_eq!(position, 2_880);
        assert_eq!(sink.queued().unwrap(), Duration::from_millis(60));
        assert_eq!(sink.pull_opus(), Some((Bytes::from(long), 2_880)));
        assert_eq!(fixture.markers(), [(0, true)]);
    }

    #[test]
    fn volume_changes_are_validated() {
        let mut fixture = Fixture::new();
        let sink = fixture.sink(stereo_48k(), 1_000, StallPolicy::Hold);

        sink.set_volume(0.25).unwrap();

        assert_eq!(sink.volume(), 0.25);
        for volume in [-1.0, f32::NAN, f32::INFINITY] {
            assert!(matches!(
                sink.set_volume(volume),
                Err(CoreError::InvalidRequest(_))
            ));
        }
        assert_eq!(sink.volume(), 0.25);
    }

    #[test]
    fn passthrough_is_reported_and_counted() {
        let mut fixture = Fixture::new();
        let sink = fixture.sink(SinkFormat::Opus, 1_000, StallPolicy::Hold);

        sink.note_passthrough(true);
        sink.note_passthrough(true);
        let passing = sink.passthrough().unwrap();
        sink.note_passthrough(false);

        assert!(passing);
        assert!(!sink.passthrough().unwrap());
        assert_eq!(sink.stats().unwrap().passthrough_packets, 2);
    }

    #[test]
    fn a_forked_child_gets_an_error_from_every_method() {
        let mut fixture = Fixture::new();
        let mut sink = fixture.sink(stereo_48k(), 1_000, StallPolicy::Hold);
        sink.owner = Owner::foreign();
        let forked =
            |result: Result<(), CoreError>| matches!(result, Err(CoreError::Forked { .. }));

        assert!(forked(sink.write(&frames_of(0.1, 960)).map(drop)));
        assert!(forked(sink.mark().map(drop)));
        assert!(forked(sink.flush().map(drop)));
        assert!(forked(sink.clear(None)));
        assert!(forked(sink.set_volume(0.5)));
        assert!(forked(sink.queued().map(drop)));
        assert!(forked(sink.passthrough().map(drop)));
        assert!(forked(sink.stats().map(drop)));
        sink.close();
        assert!(!sink.is_closed());
    }
}
