use std::collections::{HashMap, VecDeque};
use std::mem;
use std::num::NonZeroUsize;
use std::sync::Arc;
use std::time::{Duration, Instant};

use bytes::{Bytes, BytesMut};
use opus::{Channels, SoftClip};
use tracing::debug;

use super::{Core, FRAME_48K, Feed, Key, Moment, instant, samples_since, speaking};
use crate::ids::{Handle, UserId};
use crate::voice::codec::{self, Decoder};
use crate::voice::media::{Arrival, Media};
use crate::voice::peak::Peak;
use crate::voice::playout::{Cursor, Release, is_before, to_samples};
use crate::voice::receiver::{Item, Receiver};
use crate::voice::rtp;
use crate::voice::speech::{self, Activity, Edge};
use crate::voice::types::{
    FRAME, FRAME_SAMPLES, PcmFormat, ReceiveFormat, ReceiveOptions, SAMPLE_RATE, SampleFormat,
};

const FRAME_OFFSETS: u64 = FRAME_SAMPLES as u64;
const FRAME_NANOS: u128 = FRAME.as_nanos();
const MIX_SLACK: Duration = Duration::from_millis(100);

struct Track {
    feed: u64,
    cursor: Cursor,
    decoder: Option<Decoder>,
}

#[derive(Debug, Default)]
struct Marker {
    activity: Activity,
    end: u64,
    lag: i64,
}

struct Fifo {
    feed: u64,
    samples: VecDeque<f32>,
    lost: VecDeque<bool>,
    pending_lost: bool,
}

struct Mix {
    sample: SampleFormat,
    fifos: HashMap<Key, Fifo>,
    owed: u32,
    next: Option<u64>,
    clip: SoftClip,
    sum: Vec<f32>,
    frame: NonZeroUsize,
    limit: usize,
    dropped: u64,
}

struct Output {
    format: ReceiveFormat,
    markers: bool,
    opened: i64,
    jitter: i64,
    speaking: HashMap<UserId, Marker>,
    mix: Option<Mix>,
    items: Vec<Item>,
    peak: Peak,
    lost: u64,
}

struct Subscriber {
    receiver: Arc<Receiver>,
    tracks: HashMap<Key, Track>,
    out: Output,
}

struct Context<'a> {
    scratch: &'a mut Vec<f32>,
    epoch: Instant,
    now: Instant,
    now48: i64,
    hangover: i64,
    max_conceal: i64,
    heard: bool,
}

#[derive(Default)]
pub(super) struct Subscribers {
    receivers: Vec<Arc<Receiver>>,
    open: HashMap<Handle, Subscriber>,
    handles: Vec<Handle>,
    keys: Vec<Key>,
    scratch: Vec<f32>,
    turn: usize,
}

impl Subscribers {
    pub(super) fn sync(&mut self, media: &Media, epoch: Instant) {
        media.drop_ended_receivers(&mut self.receivers);
        let receivers = &self.receivers;
        self.open
            .retain(|handle, _| receivers.iter().any(|receiver| receiver.id() == *handle));
        for receiver in receivers {
            self.open
                .entry(receiver.id())
                .or_insert_with(|| Subscriber::new(Arc::clone(receiver), epoch));
        }
    }

    pub(super) fn is_empty(&self) -> bool {
        self.open.is_empty()
    }

    pub(super) fn wanted(&self, user: UserId, own: bool) -> bool {
        self.receivers
            .iter()
            .any(|receiver| wants(receiver.options(), user, own))
    }

    pub(super) fn plays(&self, user: UserId, own: bool) -> bool {
        self.open.values().any(|subscriber| {
            subscriber.out.format != ReceiveFormat::Packets
                && wants(subscriber.receiver.options(), user, own)
        })
    }

    pub(super) fn reset(&mut self) {
        for subscriber in self.open.values_mut() {
            subscriber.tracks.clear();
            if let Some(mix) = &mut subscriber.out.mix {
                mix.fifos.clear();
            }
        }
    }

    pub(super) fn deliver(
        &mut self,
        epoch: Instant,
        user: UserId,
        arrival: &Arrival,
        opus: &Bytes,
        samples: u32,
        hangover: i64,
    ) -> bool {
        let taking = |subscriber: &Subscriber| {
            subscriber.out.format == ReceiveFormat::Packets
                && arrival.at >= subscriber.receiver.opened()
                && wants(subscriber.receiver.options(), user, arrival.own)
        };
        if !self.open.values().any(taking) {
            return false;
        }
        let at48 = samples_since(epoch, arrival.at);
        let extensions = extensions(arrival);
        let speaking = speech::is_speech(opus, false);
        let mut heard = false;
        for subscriber in self.open.values_mut() {
            if !taking(subscriber) {
                continue;
            }
            let out = &mut subscriber.out;
            if out.markers {
                let marker = out.speaking.entry(user).or_default();
                let stop = marker.activity.expire(at48, hangover);
                let start = marker
                    .activity
                    .packet(arrival.ssrc, at48, samples, speaking);
                for edge in [stop, start].into_iter().flatten() {
                    out.items.push(speech_item(epoch, user, edge, None));
                }
            }
            out.items.push(Item::Packet {
                user,
                ssrc: arrival.ssrc,
                sequence: arrival.sequence,
                timestamp: arrival.timestamp,
                at: arrival.at,
                opus: opus.clone(),
                extensions: extensions.clone(),
            });
            heard = true;
        }
        heard
    }

    pub(super) fn expire_packet_markers(&mut self, epoch: Instant, now48: i64, hangover: i64) {
        for subscriber in self.open.values_mut() {
            if subscriber.out.format == ReceiveFormat::Packets && subscriber.out.markers {
                subscriber.out.expire_markers(epoch, now48, hangover, false);
            }
        }
    }

    pub(super) fn release(
        &mut self,
        streams: &mut HashMap<Key, Feed>,
        core: &Core,
        at: Moment,
        skipped: u32,
        transport: bool,
    ) -> bool {
        for subscriber in self.open.values_mut() {
            subscriber.follow(streams);
            if let Some(mix) = &mut subscriber.out.mix {
                mix.owe(transport, skipped);
            }
        }
        self.handles.clear();
        self.handles.extend(self.open.keys().copied());
        self.keys.clear();
        self.keys.extend(streams.keys().copied());
        rotate(&mut self.handles, self.turn);
        rotate(&mut self.keys, self.turn);
        self.turn = self.turn.wrapping_add(1);
        let mut context = Context {
            scratch: &mut self.scratch,
            epoch: core.epoch,
            now: at.now,
            now48: at.now48,
            hangover: at.hangover,
            max_conceal: to_samples(core.tuning.max_conceal),
            heard: false,
        };
        let mut deferred = false;
        for handle in &self.handles {
            let Some(subscriber) = self.open.get_mut(handle) else {
                continue;
            };
            if deferred || subscriber.out.format == ReceiveFormat::Packets {
                continue;
            }
            deferred = !subscriber.play(streams, &self.keys, &mut context, at.deadline);
        }
        let heard = context.heard;
        for subscriber in self.open.values_mut() {
            subscriber.flush(at.now);
        }
        for (key, feed) in streams.iter_mut() {
            let mut oldest: Option<u16> = None;
            let mut waiting = false;
            for track in self
                .open
                .values()
                .filter_map(|subscriber| subscriber.tracks.get(key))
            {
                match track.cursor.position() {
                    None => waiting = true,
                    Some(next) => {
                        oldest = Some(oldest.map_or(next, |oldest| {
                            if is_before(next, oldest) {
                                next
                            } else {
                                oldest
                            }
                        }));
                    }
                }
            }
            if !waiting && let Some(oldest) = oldest {
                feed.stream.release_before(oldest);
            }
        }
        heard
    }
}

impl Subscriber {
    fn new(receiver: Arc<Receiver>, epoch: Instant) -> Self {
        let options = receiver.options();
        let jitter = to_samples(options.jitter);
        let mix = match options.format {
            ReceiveFormat::Mixed(format) => Some(Mix::new(format, options.jitter)),
            _ => None,
        };
        let out = Output {
            format: options.format,
            markers: options.markers,
            opened: samples_since(epoch, receiver.opened()),
            jitter,
            speaking: HashMap::new(),
            mix,
            items: Vec::new(),
            peak: Peak::default(),
            lost: 0,
        };
        Self {
            receiver,
            tracks: HashMap::new(),
            out,
        }
    }

    fn follow(&mut self, streams: &HashMap<Key, Feed>) {
        if self.out.format == ReceiveFormat::Packets {
            return;
        }
        let current =
            |key: &Key, feed: u64| streams.get(key).is_some_and(|stream| stream.id == feed);
        self.tracks.retain(|key, track| current(key, track.feed));
        if let Some(mix) = &mut self.out.mix {
            mix.fifos.retain(|key, fifo| current(key, fifo.feed));
        }
        let options = self.receiver.options();
        for (&key, feed) in streams {
            if self.tracks.contains_key(&key) || !wants(options, feed.stream.user(), feed.own) {
                continue;
            }
            self.tracks.insert(
                key,
                Track {
                    feed: feed.id,
                    cursor: Cursor::new(self.out.opened),
                    decoder: None,
                },
            );
            if let Some(mix) = &mut self.out.mix {
                mix.fifos.insert(key, Fifo::new(feed.id));
            }
        }
    }

    fn flush(&mut self, now: Instant) {
        let out = &mut self.out;
        if out.lost > 0 {
            self.receiver.add_lost(mem::take(&mut out.lost));
        }
        if let Some(mix) = &mut out.mix
            && mix.dropped > 0
        {
            self.receiver.add_dropped(mem::take(&mut mix.dropped));
        }
        out.peak.observe(out.items.len());
        if !out.items.is_empty() {
            self.receiver.push(out.items.drain(..), now);
        }
        out.peak
            .shrink(out.items.capacity(), |to| out.items.shrink_to(to));
        out.peak.advance();
    }

    fn play(
        &mut self,
        streams: &mut HashMap<Key, Feed>,
        keys: &[Key],
        context: &mut Context<'_>,
        deadline: Instant,
    ) -> bool {
        for key in keys {
            let (Some(track), Some(feed)) = (self.tracks.get_mut(key), streams.get_mut(key)) else {
                continue;
            };
            loop {
                if Instant::now() >= deadline {
                    return false;
                }
                let Some(release) = track.cursor.next(
                    &feed.stream,
                    context.now48,
                    self.out.jitter,
                    context.max_conceal,
                ) else {
                    break;
                };
                self.out.play(release, key.0, track, feed, context);
            }
        }
        if !self.out.mix(context, deadline) {
            return false;
        }
        if self.out.markers {
            let end = context.now48.saturating_sub(self.out.jitter);
            self.out
                .expire_markers(context.epoch, end, context.hangover, true);
        }
        true
    }
}

impl Output {
    fn play(
        &mut self,
        release: Release,
        ssrc: u32,
        track: &mut Track,
        feed: &mut Feed,
        context: &mut Context<'_>,
    ) {
        let user = feed.stream.user();
        match release {
            Release::Packet { slot, due, samples } => {
                let Some(payload) = &slot.payload else {
                    return;
                };
                let header = FrameHeader {
                    user,
                    ssrc,
                    timestamp: slot.timestamp,
                    due,
                    at: instant(context.epoch, slot.at),
                    samples,
                    lost: false,
                };
                self.packet(header, payload, track, context);
            }
            Release::Conceal {
                count,
                samples,
                timestamp,
                due,
                next,
            } => {
                for index in 0..count {
                    let shift = samples.saturating_mul(u32::from(index));
                    let fec = (index.saturating_add(1) == count)
                        .then_some(next.payload.as_deref())
                        .flatten();
                    let header = FrameHeader {
                        user,
                        ssrc,
                        timestamp: timestamp.wrapping_add(shift),
                        due: due.saturating_add(i64::from(shift)),
                        at: context.now,
                        samples,
                        lost: true,
                    };
                    self.conceal(header, fec, track, context);
                }
            }
            Release::Skip { lost } => {
                self.lost = self.lost.saturating_add(u64::from(lost));
                if let Some(decoder) = &mut track.decoder {
                    decoder.reset();
                }
            }
        }
    }

    fn packet(
        &mut self,
        header: FrameHeader,
        payload: &Bytes,
        track: &mut Track,
        context: &mut Context<'_>,
    ) {
        let FrameHeader {
            user,
            ssrc,
            due,
            samples,
            ..
        } = header;
        let mut start = None;
        let speaking = speech::is_speech(payload, false);
        if self.markers {
            let marker = self.speaking.entry(user).or_default();
            if let Some(stop) = marker.activity.expire(due, context.hangover) {
                let end = marker.end;
                self.items
                    .push(speech_item(context.epoch, user, stop, Some(end)));
            }
            start = marker.activity.packet(ssrc, due, samples, speaking);
            marker.lag = context
                .now48
                .saturating_sub(self.jitter)
                .saturating_sub(due)
                .max(0);
        }
        let end = match self.format {
            ReceiveFormat::Pcm(format) | ReceiveFormat::Mixed(format) => {
                let decoder = decoder(track, format);
                let decoded = match decoder
                    .as_mut()
                    .map(|decoder| decoder.decode(payload, context.scratch))
                {
                    Some(Ok(decoded)) => decoded,
                    Some(Err(error)) => {
                        debug!(%error, %user, "a received packet did not decode");
                        let lost = FrameHeader {
                            at: context.now,
                            lost: true,
                            ..header
                        };
                        self.conceal(lost, None, track, context);
                        return;
                    }
                    None => return,
                };
                self.pcm(
                    format,
                    header,
                    decoded,
                    &mut track.cursor,
                    context,
                    &mut start,
                )
            }
            ReceiveFormat::Opus if samples > 0 => {
                let offset = track.cursor.offset(due, samples);
                if let Some(start) = start.take() {
                    self.items
                        .push(speech_item(context.epoch, user, start, Some(offset)));
                }
                self.items.push(Item::Frame {
                    user: Some(user),
                    ssrc: Some(ssrc),
                    timestamp: Some(header.timestamp),
                    offset,
                    samples,
                    at: header.at,
                    data: payload.clone(),
                    lost: false,
                });
                context.heard = true;
                Some(offset.saturating_add(u64::from(samples)))
            }
            ReceiveFormat::Opus | ReceiveFormat::Packets => None,
        };
        if let Some(start) = start {
            let offset = end.unwrap_or_else(|| offset_of(due, self.opened));
            self.items
                .push(speech_item(context.epoch, user, start, Some(offset)));
        }
        if speaking
            && let Some(end) = end
            && let Some(marker) = self.speaking.get_mut(&user)
        {
            marker.end = end;
        }
    }

    fn conceal(
        &mut self,
        header: FrameHeader,
        fec: Option<&[u8]>,
        track: &mut Track,
        context: &mut Context<'_>,
    ) {
        match self.format {
            ReceiveFormat::Pcm(format) | ReceiveFormat::Mixed(format) => {
                let decoder = decoder(track, format);
                let Some(Ok(decoded)) = decoder
                    .as_mut()
                    .map(|decoder| decoder.conceal(fec, header.samples, context.scratch))
                else {
                    return;
                };
                self.pcm(
                    format,
                    header,
                    decoded,
                    &mut track.cursor,
                    context,
                    &mut None,
                );
            }
            ReceiveFormat::Opus => {
                let offset = track.cursor.offset(header.due, header.samples);
                self.items.push(Item::Frame {
                    user: Some(header.user),
                    ssrc: Some(header.ssrc),
                    timestamp: Some(header.timestamp),
                    offset,
                    samples: header.samples,
                    at: header.at,
                    data: Bytes::new(),
                    lost: header.lost,
                });
            }
            ReceiveFormat::Packets => {}
        }
    }

    fn pcm(
        &mut self,
        format: PcmFormat,
        header: FrameHeader,
        decoded: usize,
        cursor: &mut Cursor,
        context: &mut Context<'_>,
        start: &mut Option<Edge>,
    ) -> Option<u64> {
        let ratio = SAMPLE_RATE.checked_div(format.rate).unwrap_or(1).max(1);
        let keep = usize::try_from(header.samples.checked_div(ratio).unwrap_or(0)).unwrap_or(0);
        let taken = decoded.min(keep);
        let channels = usize::from(format.channels).max(1);
        let mut consumed: u32 = 0;
        let mut end = None;
        for range in codec::split(taken, format.rate, format.channels) {
            let Some(pcm) = context.scratch.get(range) else {
                continue;
            };
            let per_channel =
                u32::try_from(pcm.len().checked_div(channels).unwrap_or(0)).unwrap_or(0);
            let samples = per_channel.saturating_mul(ratio);
            let timestamp = header.timestamp.wrapping_add(consumed);
            let due = header.due.saturating_add(i64::from(consumed));
            consumed = consumed.saturating_add(samples);
            if let Some(mix) = &mut self.mix {
                if let Some(fifo) = mix.fifos.get_mut(&(header.ssrc, header.user)) {
                    fifo.push(pcm, header.lost, mix.frame);
                    mix.dropped = mix.dropped.saturating_add(fifo.trim(mix.frame, mix.limit));
                }
                continue;
            }
            let offset = cursor.offset(due, samples);
            if let Some(edge) = start.take() {
                self.items
                    .push(speech_item(context.epoch, header.user, edge, Some(offset)));
            }
            let mut data = BytesMut::new();
            codec::write_samples(pcm, format.sample, &mut data);
            self.items.push(Item::Frame {
                user: Some(header.user),
                ssrc: Some(header.ssrc),
                timestamp: Some(timestamp),
                offset,
                samples,
                at: header.at,
                data: data.freeze(),
                lost: header.lost,
            });
            context.heard |= !header.lost;
            end = Some(offset.saturating_add(u64::from(samples)));
        }
        end
    }

    fn mix(&mut self, context: &mut Context<'_>, deadline: Instant) -> bool {
        let Some(mix) = &mut self.mix else {
            return true;
        };
        while mix.owed > 0 {
            if Instant::now() >= deadline {
                return false;
            }
            let anchor = context
                .now48
                .saturating_sub(self.jitter)
                .saturating_sub(self.opened)
                .max(0)
                .unsigned_abs();
            let offset = *mix
                .next
                .get_or_insert(anchor.saturating_sub(anchor % FRAME_OFFSETS));
            mix.sum.clear();
            mix.sum.resize(mix.frame.get(), 0.0);
            let mut lost = false;
            for fifo in mix.fifos.values_mut() {
                if let Some(concealed) = fifo.pop(&mut mix.sum, mix.frame) {
                    lost |= concealed;
                    context.heard = true;
                }
            }
            mix.clip.apply(&mut mix.sum);
            let mut data = BytesMut::new();
            codec::write_samples(&mix.sum, mix.sample, &mut data);
            self.items.push(Item::Frame {
                user: None,
                ssrc: None,
                timestamp: None,
                offset,
                samples: FRAME_48K,
                at: context.now,
                data: data.freeze(),
                lost,
            });
            mix.next = Some(offset.saturating_add(u64::from(FRAME_48K)));
            mix.owed = mix.owed.saturating_sub(1);
        }
        true
    }

    fn expire_markers(&mut self, epoch: Instant, end: i64, hangover: i64, timeline: bool) {
        for (&user, marker) in &mut self.speaking {
            if let Some(stop) = marker
                .activity
                .expire(end.saturating_sub(marker.lag), hangover)
            {
                let offset = timeline.then_some(marker.end);
                self.items.push(speech_item(epoch, user, stop, offset));
            }
        }
        self.speaking
            .retain(|_, marker| marker.activity.is_speaking());
    }
}

#[derive(Debug, Clone, Copy)]
struct FrameHeader {
    user: UserId,
    ssrc: u32,
    timestamp: u32,
    due: i64,
    at: Instant,
    samples: u32,
    lost: bool,
}

impl Mix {
    fn new(format: PcmFormat, jitter: Duration) -> Self {
        let channels = if format.channels == 1 {
            Channels::Mono
        } else {
            Channels::Stereo
        };
        let samples = (format.rate / 50).saturating_mul(u32::from(format.channels));
        let frame = usize::try_from(samples)
            .ok()
            .and_then(NonZeroUsize::new)
            .unwrap_or(NonZeroUsize::MIN);
        let limit = (jitter.saturating_add(MIX_SLACK).as_nanos() / FRAME_NANOS).max(1);
        Self {
            sample: format.sample,
            fifos: HashMap::new(),
            owed: 0,
            next: None,
            clip: SoftClip::new(channels),
            sum: Vec::new(),
            frame,
            limit: usize::try_from(limit).unwrap_or(usize::MAX),
            dropped: 0,
        }
    }

    fn owe(&mut self, transport: bool, skipped: u32) {
        if transport {
            self.owed = self.owed.saturating_add(skipped).saturating_add(1);
        } else {
            self.owed = 0;
            self.next = None;
        }
    }
}

impl Fifo {
    fn new(feed: u64) -> Self {
        Self {
            feed,
            samples: VecDeque::new(),
            lost: VecDeque::new(),
            pending_lost: false,
        }
    }

    fn push(&mut self, pcm: &[f32], lost: bool, frame: NonZeroUsize) {
        self.samples.extend(pcm);
        self.pending_lost |= lost;
        while self.samples.len() / frame > self.lost.len() {
            self.lost.push_back(mem::take(&mut self.pending_lost));
        }
    }

    fn trim(&mut self, frame: NonZeroUsize, limit: usize) -> u64 {
        let mut dropped: u64 = 0;
        while self.lost.len() > limit && self.samples.len() >= frame.get() {
            self.samples.drain(..frame.get());
            self.lost.pop_front();
            dropped = dropped.saturating_add(1);
        }
        dropped
    }

    fn pop(&mut self, sum: &mut [f32], frame: NonZeroUsize) -> Option<bool> {
        if self.samples.len() < frame.get() {
            return None;
        }
        for (total, sample) in sum.iter_mut().zip(self.samples.drain(..frame.get())) {
            *total += sample;
        }
        Some(self.lost.pop_front().unwrap_or(false))
    }
}

fn decoder(track: &mut Track, format: PcmFormat) -> &mut Option<Decoder> {
    if track.decoder.is_none() {
        track.decoder = Decoder::new(format.rate, format.channels).ok();
    }
    &mut track.decoder
}

pub(super) fn wants(options: &ReceiveOptions, user: UserId, own: bool) -> bool {
    if own {
        return options.include_self;
    }
    options
        .users
        .as_ref()
        .is_none_or(|users| users.contains(&user))
        && !options.exclude.contains(&user)
}

fn extensions(arrival: &Arrival) -> Vec<(u8, Bytes)> {
    let Some((profile, body)) = &arrival.extension else {
        return Vec::new();
    };
    match rtp::extensions(*profile, body) {
        Ok(elements) => elements
            .into_iter()
            .map(|(id, data)| (id, body.slice_ref(data)))
            .collect(),
        Err(_) => Vec::new(),
    }
}

fn rotate<T>(items: &mut [T], turn: usize) {
    if let Some(by) = turn.checked_rem(items.len()) {
        items.rotate_left(by);
    }
}

fn offset_of(due: i64, opened: i64) -> u64 {
    due.saturating_sub(opened).max(0).unsigned_abs()
}

fn speech_item(epoch: Instant, user: UserId, edge: Edge, offset: Option<u64>) -> Item {
    let (ssrc, at, speaking) = speaking(epoch, edge);
    Item::Speech {
        user,
        ssrc,
        speaking,
        offset,
        at,
    }
}
