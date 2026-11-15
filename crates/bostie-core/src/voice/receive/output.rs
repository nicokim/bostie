use std::collections::{HashMap, VecDeque};
use std::mem;
use std::num::NonZeroUsize;
use std::sync::Arc;
use std::time::{Duration, Instant};

use bytes::{Bytes, BytesMut};
use opus::{Channels, SoftClip};
use tracing::debug;

use super::decrypt::Hold;
use super::owners::Owners;
use super::peak::Peak;
use super::playout::{Cursor, Release, is_before, to_samples};
use super::receiver::{Item, Receiver};
use super::speech::{self, Activity, Edge};
use super::{Feed, Key, Moment, ReceiveContext, instant, samples_since, speaking};
use crate::ids::{Handle, UserId};
use crate::voice::codec::{self, OpusDecoder};
use crate::voice::media::{Arrival, Media};
use crate::voice::transport::rtp;
use crate::voice::types::{
    FRAME, FRAME_48K, FRAME_SAMPLES, PcmFormat, ReceiveFormat, ReceiveOptions, SAMPLE_RATE,
};

const FRAME_OFFSETS: u64 = FRAME_SAMPLES as u64;
const FRAME_NANOS: u128 = FRAME.as_nanos();
const MIX_SLACK: Duration = Duration::from_millis(100);

struct Track {
    feed: u64,
    cursor: Cursor,
    decoder: Option<OpusDecoder>,
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
    frames: VecDeque<(bool, Instant)>,
    partial: Option<(bool, Instant)>,
    starved: bool,
}

struct Mix {
    format: PcmFormat,
    fifos: HashMap<Key, Fifo>,
    owed: u32,
    next: Option<u64>,
    clip: SoftClip,
    sum: Vec<f32>,
    frame: NonZeroUsize,
    limit: usize,
    dropped: u64,
}

enum Form {
    Pcm(PcmFormat),
    Mixed(Mix),
    Opus,
    Packets,
}

struct Output {
    form: Form,
    markers: bool,
    opened: i64,
    jitter: i64,
    speaking: HashMap<UserId, Marker>,
    leaving: Vec<(UserId, i64)>,
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
            !subscriber.out.is_packets() && wants(subscriber.receiver.options(), user, own)
        })
    }

    pub(super) fn reset(&mut self) {
        for subscriber in self.open.values_mut() {
            subscriber.tracks.clear();
            if let Form::Mixed(mix) = &mut subscriber.out.form {
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
            subscriber.out.is_packets()
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
            if subscriber.out.is_packets() && subscriber.out.markers {
                subscriber.out.expire_markers(epoch, now48, hangover, false);
                subscriber.out.leave(epoch, now48, false);
            }
        }
    }

    pub(super) fn depart(&mut self, user: UserId, due: i64) {
        for subscriber in self.open.values_mut() {
            let out = &mut subscriber.out;
            if out.markers && due >= out.opened && wants(subscriber.receiver.options(), user, false)
            {
                out.leaving.push((user, due));
            }
        }
    }

    pub(super) fn release(
        &mut self,
        streams: &mut HashMap<Key, Feed>,
        core: &ReceiveContext,
        at: Moment,
        transport: bool,
        owners: &Owners,
        hold: &Hold,
    ) -> bool {
        for subscriber in self.open.values_mut() {
            subscriber.follow(streams);
            if let Form::Mixed(mix) = &mut subscriber.out.form {
                mix.owe(transport, at.skipped);
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
            if !subscriber.out.is_packets() && !deferred {
                deferred = !subscriber.play(streams, &self.keys, &mut context, at.deadline);
            }
            subscriber.flush(at.now);
            subscriber.pass_marks(streams, core, at, owners, hold);
        }
        let heard = context.heard;
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
        let form = match options.format {
            ReceiveFormat::Pcm(format) => Form::Pcm(format),
            ReceiveFormat::Mixed(format) => Form::Mixed(Mix::new(format, options.jitter)),
            ReceiveFormat::Opus => Form::Opus,
            ReceiveFormat::Packets => Form::Packets,
        };
        let out = Output {
            form,
            markers: options.markers,
            opened: samples_since(epoch, receiver.opened()),
            jitter,
            speaking: HashMap::new(),
            leaving: Vec::new(),
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
        if self.out.is_packets() {
            return;
        }
        let current =
            |key: &Key, feed: u64| streams.get(key).is_some_and(|stream| stream.id == feed);
        self.tracks.retain(|key, track| current(key, track.feed));
        if let Form::Mixed(mix) = &mut self.out.form {
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
            if let Form::Mixed(mix) = &mut self.out.form {
                mix.fifos.insert(key, Fifo::new(feed.id));
            }
        }
    }

    fn flush(&mut self, now: Instant) {
        let out = &mut self.out;
        if out.lost > 0 {
            self.receiver.add_lost(mem::take(&mut out.lost));
        }
        if let Form::Mixed(mix) = &mut out.form
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

    fn pass_marks(
        &self,
        streams: &HashMap<Key, Feed>,
        core: &ReceiveContext,
        at: Moment,
        owners: &Owners,
        hold: &Hold,
    ) {
        if !self.receiver.is_marked() {
            return;
        }
        let options = self.receiver.options();
        let playing = self.tracks.iter().filter_map(|(key, track)| {
            let feed = streams.get(key)?;
            let heard = track.cursor.backlog(&feed.stream)?;
            Some(instant(core.epoch, heard))
        });
        let mixing = match &self.out.form {
            Form::Mixed(mix) => mix.oldest(),
            _ => None,
        };
        let oldest = owners
            .oldest()
            .into_iter()
            .chain(hold.oldest(|user, own| wants(options, user, own)))
            .chain(playing)
            .chain(mixing)
            .min();
        let slack = options.jitter.saturating_add(MIX_SLACK).saturating_mul(2);
        let bound = core.tuning.hold.saturating_add(slack);
        self.receiver
            .pass(at.cut, oldest, at.now.checked_sub(bound));
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
            self.out.leave(context.epoch, end, true);
        }
        true
    }
}

impl Output {
    fn is_packets(&self) -> bool {
        matches!(self.form, Form::Packets)
    }

    fn play(
        &mut self,
        release: Release,
        ssrc: u32,
        track: &mut Track,
        feed: &Feed,
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
        let end = match self.form {
            Form::Pcm(format) | Form::Mixed(Mix { format, .. }) => {
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
            Form::Opus if samples > 0 => {
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
                    level: None,
                });
                context.heard = true;
                Some(offset.saturating_add(u64::from(samples)))
            }
            Form::Opus | Form::Packets => None,
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
        match self.form {
            Form::Pcm(format) | Form::Mixed(Mix { format, .. }) => {
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
            Form::Opus => {
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
                    level: None,
                });
            }
            Form::Packets => {}
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
            if let Form::Mixed(mix) = &mut self.form {
                if let Some(fifo) = mix.fifos.get_mut(&(header.ssrc, header.user)) {
                    fifo.push(pcm, header.lost, header.at, mix.frame);
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
                level: Some(level(pcm)),
            });
            context.heard |= !header.lost;
            end = Some(offset.saturating_add(u64::from(samples)));
        }
        end
    }

    fn mix(&mut self, context: &mut Context<'_>, deadline: Instant) -> bool {
        let Form::Mixed(mix) = &mut self.form else {
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
                .get_or_insert_with(|| anchor.saturating_sub(anchor % FRAME_OFFSETS));
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
            codec::write_samples(&mix.sum, mix.format.sample, &mut data);
            self.items.push(Item::Frame {
                user: None,
                ssrc: None,
                timestamp: None,
                offset,
                samples: FRAME_48K,
                at: context.now,
                data: data.freeze(),
                lost,
                level: Some(level(&mix.sum)),
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

    fn leave(&mut self, epoch: Instant, end: i64, timeline: bool) {
        let Self {
            leaving,
            speaking,
            items,
            opened,
            ..
        } = self;
        leaving.retain(|&(user, due)| {
            let lag = speaking.get(&user).map_or(0, |marker| marker.lag);
            if due > end.saturating_sub(lag) {
                return true;
            }
            let mut last = offset_of(due, *opened);
            if let Some(mut marker) = speaking.remove(&user) {
                last = last.max(marker.end);
                if let Some(stop) = marker.activity.expire(i64::MAX, 0) {
                    let offset = timeline.then_some(marker.end);
                    items.push(speech_item(epoch, user, stop, offset));
                }
            }
            items.push(Item::Leave {
                user,
                offset: timeline.then_some(last),
            });
            false
        });
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
            format,
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

    fn oldest(&self) -> Option<Instant> {
        self.fifos
            .values()
            .flat_map(|fifo| fifo.frames.iter().chain(&fifo.partial))
            .map(|&(_, heard)| heard)
            .min()
    }

    fn owe(&mut self, transport: bool, skipped: u32) {
        if transport {
            self.owed = self.owed.saturating_add(skipped).saturating_add(1);
            return;
        }
        self.owed = 0;
        self.next = None;
        for fifo in self.fifos.values_mut() {
            fifo.samples.clear();
            fifo.frames.clear();
            fifo.partial = None;
        }
    }
}

impl Fifo {
    fn new(feed: u64) -> Self {
        Self {
            feed,
            samples: VecDeque::new(),
            frames: VecDeque::new(),
            partial: None,
            starved: false,
        }
    }

    fn push(&mut self, pcm: &[f32], lost: bool, heard: Instant, frame: NonZeroUsize) {
        self.starved = false;
        self.samples.extend(pcm);
        let (lost_before, first) = self.partial.take().unwrap_or((false, heard));
        let mut next = (lost_before || lost, first.min(heard));
        while self.samples.len() / frame > self.frames.len() {
            self.frames
                .push_back(mem::replace(&mut next, (false, heard)));
        }
        if self.samples.len() % frame > 0 {
            self.partial = Some(next);
        }
    }

    fn trim(&mut self, frame: NonZeroUsize, limit: usize) -> u64 {
        let mut dropped: u64 = 0;
        while self.frames.len() > limit && self.samples.len() >= frame.get() {
            self.samples.drain(..frame.get());
            self.frames.pop_front();
            dropped = dropped.saturating_add(1);
        }
        dropped
    }

    fn pop(&mut self, sum: &mut [f32], frame: NonZeroUsize) -> Option<bool> {
        if self.samples.len() < frame.get() && !mem::replace(&mut self.starved, true) {
            return None;
        }
        let (lost, _) = self.frames.pop_front().or_else(|| self.partial.take())?;
        let taken = self.samples.len().min(frame.get());
        for (total, sample) in sum.iter_mut().zip(self.samples.drain(..taken)) {
            *total += sample;
        }
        Some(lost)
    }
}

fn decoder(track: &mut Track, format: PcmFormat) -> &mut Option<OpusDecoder> {
    if track.decoder.is_none() {
        track.decoder = OpusDecoder::new(format.rate, format.channels).ok();
    }
    &mut track.decoder
}

fn wants(options: &ReceiveOptions, user: UserId, own: bool) -> bool {
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

#[expect(clippy::cast_precision_loss)]
fn level(samples: &[f32]) -> f32 {
    let energy: f32 = samples.iter().map(|sample| sample * sample).sum();
    10.0 * (energy / samples.len().max(1) as f32).log10()
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

#[cfg(test)]
mod tests {
    use std::collections::HashSet;

    use super::*;
    use crate::voice::types::{Overflow, ReceiveStall, SampleFormat};

    const ALICE: u64 = 1;
    const BOB: u64 = 2;
    const BOT: u64 = 100;

    fn users(ids: &[u64]) -> HashSet<UserId> {
        ids.iter().copied().map(UserId::from).collect()
    }

    #[test]
    fn a_mix_reports_the_oldest_stamp_of_any_pcm_it_still_holds() {
        let format = PcmFormat {
            rate: 48_000,
            channels: 2,
            sample: SampleFormat::F32Le,
        };
        let mut mix = Mix::new(format, Duration::ZERO);
        let half = vec![0.0; mix.frame.get() / 2];
        let earliest = Instant::now();
        let earlier = earliest + Duration::from_millis(5);
        let later = earlier + Duration::from_millis(5);
        let mut reordered = Fifo::new(0);
        reordered.push(&half, false, later, mix.frame);
        reordered.push(&half, false, earlier, mix.frame);
        let mut partial = Fifo::new(1);
        partial.push(&half, false, earliest, mix.frame);

        mix.fifos.insert((1, UserId::from(ALICE)), reordered);
        let complete = mix.oldest();
        mix.fifos.insert((2, UserId::from(BOB)), partial);

        assert_eq!((complete, mix.oldest()), (Some(earlier), Some(earliest)));
    }

    #[test]
    fn a_level_is_the_rms_in_dbfs_and_minus_infinity_for_silence() {
        let square = [0.5, -0.5, 0.5, -0.5];

        assert!((level(&square) - 20.0 * 0.5_f32.log10()).abs() < 1e-5);
        assert_eq!(level(&[0.0; 4]), f32::NEG_INFINITY);
    }

    #[test]
    fn a_fifo_mixes_its_partial_frame_once_a_frame_passes_with_nothing_new() {
        let frame = NonZeroUsize::new(4).unwrap();
        let mut fifo = Fifo::new(0);
        let mut sums = [[0.0; 4]; 3];

        fifo.push(&[1.0; 6], false, Instant::now(), frame);
        let popped = sums.each_mut().map(|sum| fifo.pop(sum, frame));

        assert_eq!(popped, [Some(false), None, Some(false)]);
        assert_eq!(sums, [[1.0; 4], [0.0; 4], [1.0, 1.0, 0.0, 0.0]]);
    }

    #[test]
    fn the_user_filters_decide_whose_audio_a_receiver_gets() {
        let cases = [
            (ALICE, None, vec![], false, true),
            (ALICE, None, vec![ALICE], false, false),
            (ALICE, Some(vec![ALICE]), vec![], false, true),
            (ALICE, Some(vec![ALICE]), vec![ALICE], false, false),
            (ALICE, Some(vec![BOB]), vec![], true, false),
            (ALICE, Some(vec![]), vec![], false, false),
            (BOT, Some(vec![BOB]), vec![BOT], true, true),
            (BOT, None, vec![], false, false),
        ];

        for (speaker, only, exclude, include_self, heard) in cases {
            let options = ReceiveOptions {
                format: ReceiveFormat::Opus,
                users: only.as_deref().map(users),
                exclude: users(&exclude),
                include_self,
                buffer: Duration::ZERO,
                jitter: Duration::ZERO,
                markers: false,
                overflow: Overflow::DropOldest,
                stall: ReceiveStall::Wait,
            };
            assert_eq!(
                wants(&options, UserId::from(speaker), speaker == BOT),
                heard,
                "{speaker} {only:?} {exclude:?} {include_self}"
            );
        }
    }
}
