#![deny(clippy::arithmetic_side_effects)]

use std::any::Any;
use std::collections::{HashMap, HashSet, VecDeque};
use std::mem;
use std::net::UdpSocket;
use std::num::NonZeroUsize;
use std::panic::{self, AssertUnwindSafe};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use bytes::{Bytes, BytesMut};
use opus::{Channels, SoftClip};
use parking_lot::MutexGuard;
use tracing::{debug, error, warn};

use crate::ids::{GuildId, Handle, UserId};
use crate::inbox::Delivery;
use crate::voice::codec::{self, Decoder};
use crate::voice::connection::Info;
use crate::voice::crypto::ReplayWindow;
use crate::voice::dave::{self, Dave, DecryptFailure};
use crate::voice::media::{Arrival, Media, Stall, Transport, bump};
use crate::voice::pacer::Tick;
use crate::voice::playout::{Check, Cursor, Release, Slot, Stream, is_before, to_samples};
use crate::voice::receiver::{Item, Receiver};
use crate::voice::rtp;
use crate::voice::speech::{self, Activity, Edge};
use crate::voice::types::{
    Direction, FRAME, FRAME_SAMPLES, PcmFormat, ReceiveFormat, ReceiveOptions, ReceiveTuning,
    SAMPLE_RATE, SILENCE_FRAME, Speaking,
};
use crate::voice::{Throttle, panic_message};

const FRAME_48K: u32 = FRAME_SAMPLES as u32;
const FRAME_OFFSETS: u64 = FRAME_SAMPLES as u64;
const FRAME_NANOS: u128 = FRAME.as_nanos();
const NANOS_PER_3_SAMPLES: u64 = 3_000_000_000 / SAMPLE_RATE as u64;
const PEAK_TICKS: u32 = 24;
const PAYLOAD_CHUNK: usize = 8 * 1024;
const MEDIA_RECENT: Duration = Duration::from_millis(100);
const MIX_SLACK: Duration = Duration::from_millis(100);
const RECOVERY_INTERVAL: Duration = Duration::from_secs(30);
const RECOVERIES_PER_USER: u8 = 2;
const SPARE_WINDOWS: usize = 8;

type Key = (u32, UserId);

pub struct Intake {
    media: Arc<Media>,
    info: Arc<Info>,
    socket: Arc<UdpSocket>,
    transport: Option<Arc<Transport>>,
    windows: HashMap<u32, ReplayWindow>,
    replay_window: u32,
    panicked: Throttle,
}

impl Intake {
    pub fn new(
        media: Arc<Media>,
        info: Arc<Info>,
        socket: Arc<UdpSocket>,
        replay_window: u32,
    ) -> Self {
        Self {
            media,
            info,
            socket,
            transport: None,
            windows: HashMap::new(),
            replay_window,
            panicked: Throttle::default(),
        }
    }

    pub fn rtp(&mut self, packet: &mut [u8]) {
        if let Err(payload) = panic::catch_unwind(AssertUnwindSafe(|| self.open(packet))) {
            bump(&self.media.receive.rx_panics);
            if self.panicked.allows(Instant::now()) {
                error!(
                    panic = panic_message(payload.as_ref()),
                    "opening a received voice packet panicked and it was dropped"
                );
            }
        }
    }

    fn open(&mut self, packet: &mut [u8]) {
        let media = &self.media;
        bump(&media.counters.packets_received);
        if !media.speech_activity() && media.open_receivers() == 0 {
            return;
        }
        let at = Instant::now();
        if self.transport.is_none() {
            let installed = media.transport.lock().clone();
            self.transport =
                installed.filter(|transport| Arc::ptr_eq(&transport.socket, &self.socket));
        }
        let Some(transport) = &self.transport else {
            bump(&media.receive.transport_failures);
            return;
        };
        let Ok(opened) = transport.cipher.open(packet) else {
            bump(&media.receive.transport_failures);
            return;
        };
        let header = opened.header;
        if !self.windows.contains_key(&header.ssrc) {
            let ssrcs = self.info.ssrcs.lock();
            if self.windows.len() >= ssrcs.len().saturating_add(SPARE_WINDOWS) {
                self.windows.retain(|ssrc, _| ssrcs.contains_key(ssrc));
            }
        }
        let replay_window = self.replay_window;
        let window = self
            .windows
            .entry(header.ssrc)
            .or_insert_with(|| ReplayWindow::new(replay_window));
        let counter = u32::from_le_bytes(opened.nonce);
        if !window.accept(counter) {
            bump(&media.receive.replays);
            return;
        }
        if header.ssrc == transport.ssrc {
            bump(&media.receive.own_echoes);
            return;
        }
        let payload = (media.open_receivers() > 0).then(|| Bytes::copy_from_slice(opened.payload));
        let extension = header
            .extension
            .filter(|_| media.packet_receivers() > 0)
            .map(|extension| (extension.profile, Bytes::copy_from_slice(opened.extension)));
        media.arrive(Arrival {
            ssrc: header.ssrc,
            sequence: header.sequence,
            timestamp: header.timestamp,
            at,
            payload,
            silence: opened.payload == SILENCE_FRAME,
            extension,
            own: false,
        });
    }
}

#[derive(Debug, Clone, Copy)]
struct Span {
    user: UserId,
    from: Option<Instant>,
    until: Option<Instant>,
}

struct Work {
    user: UserId,
    arrival: Arrival,
    check: Check,
    plain: Option<Bytes>,
    fresh: bool,
}

struct Held {
    user: UserId,
    arrival: Arrival,
    check: Check,
    tried: u64,
}

struct Feed {
    id: u64,
    own: bool,
    stream: Stream,
    created: i64,
    trimmed: Option<u16>,
}

#[derive(Debug, Default)]
struct Streak {
    failures: u32,
    requested: u8,
    last_request: Option<Instant>,
}

#[derive(Debug, Default)]
struct Watchdog {
    failures: u64,
    media: Option<Instant>,
    stall: Stall,
}

#[derive(Debug, Default)]
pub(super) struct Peak {
    current: usize,
    previous: usize,
    ticks: u32,
}

impl Peak {
    pub(super) fn observe(&mut self, used: usize) {
        self.current = self.current.max(used);
    }

    pub(super) fn advance(&mut self) {
        self.ticks = self.ticks.saturating_add(1);
        if self.ticks >= PEAK_TICKS {
            self.previous = mem::take(&mut self.current);
            self.ticks = 0;
        }
    }

    pub(super) fn shrink(&self, capacity: usize, shrink_to: impl FnOnce(usize)) {
        let largest = self.current.max(self.previous);
        if capacity > largest.saturating_mul(4) {
            shrink_to(largest.saturating_mul(2));
        }
    }
}

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
    media: &'a Media,
    scratch: &'a mut Vec<f32>,
    epoch: Instant,
    now: Instant,
    now48: i64,
    hangover: i64,
    max_conceal: i64,
    heard: bool,
}

pub struct Reception {
    media: Arc<Media>,
    info: Arc<Info>,
    user: UserId,
    tuning: ReceiveTuning,
    epoch: Instant,
    state: Option<Box<State>>,
    panicked: Throttle,
}

struct State {
    media: Arc<Media>,
    info: Arc<Info>,
    user: UserId,
    tuning: ReceiveTuning,
    epoch: Instant,
    receivers: Vec<Arc<Receiver>>,
    subscribers: HashMap<Handle, Subscriber>,
    handles: Vec<Handle>,
    keys: Vec<Key>,
    arrivals: VecDeque<Arrival>,
    batch: Vec<(UserId, Arrival)>,
    work: Vec<Work>,
    retried: Vec<Work>,
    waiting: VecDeque<Work>,
    waits: HashSet<UserId>,
    peak: Peak,
    unmapped: HashMap<u32, VecDeque<Arrival>>,
    owners: HashMap<u32, Vec<Span>>,
    held: VecDeque<Held>,
    held_users: HashMap<UserId, usize>,
    admitted: HashSet<(Key, u16)>,
    streams: HashMap<Key, Feed>,
    streams_peak: Peak,
    speech: HashMap<UserId, Activity>,
    speech_activity: bool,
    streaks: HashMap<UserId, Streak>,
    last_request: Option<Instant>,
    generation: Option<u64>,
    watchdog: Watchdog,
    arrived: bool,
    heard: bool,
    scratch: Vec<f32>,
    plain: Vec<u8>,
    payloads: BytesMut,
    heard_at: HashMap<UserId, i64>,
    forget_at: Option<i64>,
    member_count: usize,
    undecryptable: u64,
    warned: Throttle,
    next_feed: u64,
    turn: usize,
}

impl Reception {
    pub(crate) fn new(
        media: Arc<Media>,
        info: Arc<Info>,
        user: UserId,
        tuning: ReceiveTuning,
    ) -> Self {
        Self {
            media,
            info,
            user,
            tuning,
            epoch: Instant::now(),
            state: None,
            panicked: Throttle::default(),
        }
    }

    pub fn tick(&mut self, tick: Tick, deadline: Instant) -> bool {
        self.run(Instant::now(), tick.skipped, deadline)
    }

    fn run(&mut self, now: Instant, skipped: u32, deadline: Instant) -> bool {
        if self.state.is_none()
            && self.media.open_receivers() == 0
            && self.media.arrivals.lock().is_empty()
        {
            return !self.media.closed.load(Ordering::Acquire);
        }
        let state = self.state.get_or_insert_with(|| {
            Box::new(State::new(
                Arc::clone(&self.media),
                Arc::clone(&self.info),
                self.user,
                self.tuning,
                self.epoch,
            ))
        });
        match panic::catch_unwind(AssertUnwindSafe(|| state.run(now, skipped, deadline))) {
            Ok(alive) => alive,
            Err(payload) => self.restart(now, payload.as_ref()),
        }
    }

    fn restart(&mut self, now: Instant, payload: &(dyn Any + Send)) -> bool {
        bump(&self.media.receive.rx_panics);
        if self.panicked.allows(now) {
            error!(
                panic = panic_message(payload),
                "the voice receive phase panicked and starts over"
            );
        }
        if let Some(mut state) = self.state.take() {
            state.expire_speech(i64::MAX, 0);
            state.resume(now);
        }
        !self.media.closed.load(Ordering::Acquire)
    }
}

impl State {
    fn new(
        media: Arc<Media>,
        info: Arc<Info>,
        user: UserId,
        tuning: ReceiveTuning,
        epoch: Instant,
    ) -> Self {
        Self {
            media,
            info,
            user,
            tuning,
            epoch,
            receivers: Vec::new(),
            subscribers: HashMap::new(),
            handles: Vec::new(),
            keys: Vec::new(),
            arrivals: VecDeque::new(),
            batch: Vec::new(),
            work: Vec::new(),
            retried: Vec::new(),
            waiting: VecDeque::new(),
            waits: HashSet::new(),
            peak: Peak::default(),
            unmapped: HashMap::new(),
            owners: HashMap::new(),
            held: VecDeque::new(),
            held_users: HashMap::new(),
            admitted: HashSet::new(),
            streams: HashMap::new(),
            streams_peak: Peak::default(),
            speech: HashMap::new(),
            speech_activity: true,
            streaks: HashMap::new(),
            last_request: None,
            generation: None,
            watchdog: Watchdog::default(),
            arrived: false,
            heard: false,
            scratch: Vec::new(),
            plain: Vec::new(),
            payloads: BytesMut::new(),
            heard_at: HashMap::new(),
            forget_at: None,
            member_count: 0,
            undecryptable: 0,
            warned: Throttle::default(),
            next_feed: 0,
            turn: 0,
        }
    }

    fn run(&mut self, now: Instant, skipped: u32, deadline: Instant) -> bool {
        if self.media.closed.load(Ordering::Acquire) {
            return false;
        }
        self.subscribe();
        let now48 = samples_since(self.epoch, now);
        mem::swap(&mut *self.media.arrivals.lock(), &mut self.arrivals);
        self.peak.observe(self.arrivals.len());
        if self.arrivals.is_empty() && self.is_idle(now48) {
            self.shrink_buffers();
            return true;
        }
        let hangover = to_samples(self.media.speech_hangover());
        self.speech_activity = self.media.speech_activity();
        let transport = self.media.transport.lock().clone();
        if let Some(transport) = &transport {
            self.follow(transport.generation);
        }
        self.arrived = false;
        self.heard = false;
        self.map(now);
        self.peak.observe(self.batch.len());
        self.receive(now, now48, hangover, deadline);
        if self.speech_activity {
            self.expire_speech(now48, hangover);
        } else {
            self.expire_speech(i64::MAX, 0);
        }
        self.release(now, now48, skipped, deadline, hangover, transport.is_some());
        self.watch(now);
        self.prune(now, now48);
        self.warn_undecryptable(now);
        self.shrink_buffers();
        true
    }

    fn is_idle(&self, now48: i64) -> bool {
        self.subscribers.is_empty()
            && self.speech.is_empty()
            && self.unmapped.is_empty()
            && self.held.is_empty()
            && self.streams.is_empty()
            && !self.watchdog.stall.reported()
            && self.forget_at.is_none_or(|at| now48 < at)
            && self.media.others.load(Ordering::Relaxed) == self.member_count
    }

    fn subscribe(&mut self) {
        self.media.drop_ended_receivers(&mut self.receivers);
        let receivers = &self.receivers;
        self.subscribers
            .retain(|handle, _| receivers.iter().any(|receiver| receiver.id() == *handle));
        for receiver in receivers {
            if !self.subscribers.contains_key(&receiver.id()) {
                let subscriber = Subscriber::new(Arc::clone(receiver), self.epoch);
                self.subscribers.insert(receiver.id(), subscriber);
            }
        }
        if self.subscribers.is_empty() {
            self.streams.clear();
            self.held.clear();
            self.held_users.clear();
            self.waiting.clear();
            self.payloads = BytesMut::new();
        }
    }

    fn follow(&mut self, generation: u64) {
        if self
            .generation
            .replace(generation)
            .is_some_and(|previous| previous != generation)
        {
            debug!(
                generation,
                "a new voice session resets the received streams"
            );
            self.streams.clear();
            self.unmapped.clear();
            self.held.clear();
            self.held_users.clear();
            self.waiting.clear();
            self.streaks.clear();
            self.owners.clear();
            for subscriber in self.subscribers.values_mut() {
                subscriber.tracks.clear();
                if let Some(mix) = &mut subscriber.out.mix {
                    mix.fifos.clear();
                }
            }
        }
    }

    fn map(&mut self, now: Instant) {
        {
            let ssrcs = self.info.ssrcs.lock();
            for (&ssrc, &(user, since)) in ssrcs.iter() {
                let spans = self
                    .owners
                    .entry(ssrc)
                    .or_insert_with(|| Vec::with_capacity(1));
                match spans.last_mut() {
                    Some(last) if last.until.is_none() && last.user == user => {}
                    Some(last) => {
                        if last.until.is_none() {
                            last.until = Some(since);
                        }
                        spans.push(Span {
                            user,
                            from: Some(since),
                            until: None,
                        });
                    }
                    None => spans.push(Span {
                        user,
                        from: None,
                        until: None,
                    }),
                }
            }
            for (ssrc, spans) in &mut self.owners {
                if !ssrcs.contains_key(ssrc)
                    && let Some(last) = spans.last_mut()
                    && last.until.is_none()
                {
                    last.until = Some(now);
                }
            }
        }
        let hold = self.tuning.hold;
        self.batch.clear();
        for (ssrc, queue) in &mut self.unmapped {
            let Some(spans) = self.owners.get(ssrc) else {
                continue;
            };
            for arrival in mem::take(queue) {
                match owner(spans, arrival.at, hold) {
                    Some(user) => self.batch.push((user, arrival)),
                    None => queue.push_back(arrival),
                }
            }
        }
        self.unmapped.retain(|_, queue| !queue.is_empty());
        self.batch.sort_by_key(|(_, arrival)| arrival.at);
        let mut arrivals = mem::take(&mut self.arrivals);
        for arrival in arrivals.drain(..) {
            if arrival.own {
                self.batch.push((self.user, arrival));
                continue;
            }
            let user = self
                .owners
                .get(&arrival.ssrc)
                .and_then(|spans| owner(spans, arrival.at, hold));
            match user {
                Some(user) => self.batch.push((user, arrival)),
                None => self.hold_unmapped(arrival),
            }
        }
        self.arrivals = arrivals;
    }

    fn hold_unmapped(&mut self, arrival: Arrival) {
        let counter = &self.media.receive.unmapped;
        if self.tuning.hold_packets == 0 || self.tuning.unmapped_ssrcs == 0 {
            bump(counter);
            return;
        }
        if !self.unmapped.contains_key(&arrival.ssrc)
            && self.unmapped.len() >= self.tuning.unmapped_ssrcs
            && let Some(oldest) = self
                .unmapped
                .iter()
                .min_by_key(|(_, queue)| queue.front().map(|arrival| arrival.at))
                .map(|(&ssrc, _)| ssrc)
            && let Some(dropped) = self.unmapped.remove(&oldest)
        {
            add(counter, dropped.len());
        }
        let queue = self.unmapped.entry(arrival.ssrc).or_default();
        if queue.len() >= self.tuning.hold_packets && queue.pop_front().is_some() {
            bump(counter);
        }
        queue.push_back(arrival);
    }

    fn receive(&mut self, now: Instant, now48: i64, hangover: i64, deadline: Instant) {
        let (protocol_version, session_built) = self.media.receive_state();
        let all_plain = protocol_version == 0 && !session_built;
        let mut batch = mem::take(&mut self.batch);
        let mut work = mem::take(&mut self.work);
        work.extend(self.waiting.drain(..));
        for (user, arrival) in batch.drain(..) {
            self.admit(user, arrival, protocol_version, all_plain, now48, &mut work);
        }
        self.batch = batch;
        let cut = if work.iter().any(|item| item.plain.is_none()) || !self.held.is_empty() {
            self.decrypt(&mut work, now, deadline)
        } else {
            work.len()
        };
        let mut retried = mem::take(&mut self.retried);
        self.peak.observe(retried.len());
        for item in retried.drain(..) {
            self.accept(item, hangover);
        }
        for (index, item) in work.drain(..).enumerate() {
            if self.waits.contains(&item.user) || (index >= cut && item.plain.is_none()) {
                self.wait(item);
            } else {
                self.accept(item, hangover);
            }
        }
        self.waits.clear();
        self.retried = retried;
        self.work = work;
        self.admitted.clear();
        let held = self
            .held
            .iter()
            .map(|held| ((held.arrival.ssrc, held.user), held.arrival.sequence));
        let waiting = self
            .waiting
            .iter()
            .map(|item| ((item.arrival.ssrc, item.user), item.arrival.sequence));
        self.admitted.extend(held.chain(waiting));
        for subscriber in self.subscribers.values_mut() {
            if subscriber.out.format == ReceiveFormat::Packets && subscriber.out.markers {
                subscriber
                    .out
                    .expire_markers(self.epoch, now48, hangover, false);
            }
        }
    }

    fn admit(
        &mut self,
        user: UserId,
        arrival: Arrival,
        protocol_version: u16,
        all_plain: bool,
        now48: i64,
        work: &mut Vec<Work>,
    ) {
        let own = arrival.own;
        if !own {
            self.heard_at.insert(user, now48);
        }
        let wanted = self.wanted(user, own);
        let Some(payload) = arrival.payload.as_ref().filter(|_| wanted) else {
            if !own {
                let at48 = samples_since(self.epoch, arrival.at);
                let payload = arrival.payload.as_deref().unwrap_or_default();
                self.speak(
                    user,
                    arrival.ssrc,
                    at48,
                    payload,
                    arrival.silence,
                    FRAME_48K,
                );
            }
            return;
        };
        self.arrived = true;
        let admitted = ((arrival.ssrc, user), arrival.sequence);
        let pending = self.admitted.contains(&admitted);
        let feed = self.feed(arrival.ssrc, user, own, now48);
        let check = if pending {
            Check::Duplicate
        } else {
            feed.stream.check(arrival.sequence)
        };
        if check == Check::Duplicate {
            bump(&self.media.receive.duplicates);
            return;
        }
        self.admitted.insert(admitted);
        let refused = payload.is_empty()
            || (!own
                && protocol_version > 0
                && !dave::has_marker(payload)
                && codec::packet_samples(payload).is_none());
        if refused {
            bump(&self.media.receive.invalid_payloads);
            return;
        }
        let plain = (own
            || all_plain
            || payload.as_ref() == SILENCE_FRAME
            || (protocol_version == 0 && !dave::has_marker(payload)))
        .then(|| payload.clone());
        work.push(Work {
            user,
            arrival,
            check,
            plain,
            fresh: true,
        });
    }

    fn feed(&mut self, ssrc: u32, user: UserId, own: bool, now48: i64) -> &mut Feed {
        let next = &mut self.next_feed;
        self.streams
            .entry((ssrc, user))
            .or_insert_with(|| Feed::new(next, user, own, now48))
    }

    fn decrypt(&mut self, work: &mut [Work], now: Instant, deadline: Instant) -> usize {
        let media = Arc::clone(&self.media);
        let Some(mut dave) = media.dave.try_lock() else {
            bump(&media.receive.decrypt_busy);
            return 0;
        };
        let settling = dave.settling(now);
        let mut retries = self.tuning.dave_retries;
        let mut cut = 0;
        loop {
            let spent;
            (cut, spent) = self.slice(&mut dave, work, cut, &mut retries, now);
            if !spent || Instant::now() >= deadline {
                drop(dave);
                break;
            }
            MutexGuard::unlock_fair(dave);
            match media.dave.try_lock() {
                Some(next) => dave = next,
                None => break,
            }
        }
        if !settling {
            self.request_recovery(now);
        }
        cut
    }

    fn slice(
        &mut self,
        dave: &mut Dave,
        work: &mut [Work],
        from: usize,
        retries: &mut usize,
        now: Instant,
    ) -> (usize, bool) {
        let started = Instant::now();
        let before = dave.counters();
        let generation = dave.generation();
        let protocol_version = self.media.protocol_version();
        let counting = protocol_version > 0 && !dave.settling(now);
        let budget = self.tuning.dave_budget;
        let mut spent = false;
        {
            let Self {
                media,
                held,
                held_users,
                plain,
                payloads,
                retried,
                streaks,
                ..
            } = self;
            held.retain_mut(|held| {
                if spent || *retries == 0 || held.tried == generation {
                    return true;
                }
                *retries = retries.saturating_sub(1);
                let payload = held.arrival.payload.as_deref().unwrap_or_default();
                let decrypted = dave.receive(held.user, payload, plain);
                spent = started.elapsed() >= budget;
                match decrypted {
                    Ok(()) => {
                        streaks.remove(&held.user);
                        retried.push(Work {
                            user: held.user,
                            arrival: held.arrival.clone(),
                            check: held.check,
                            plain: Some(share(payloads, plain)),
                            fresh: false,
                        });
                        unhold(held_users, held.user);
                        false
                    }
                    Err(failure) => {
                        held.tried = generation;
                        let kept = failure != DecryptFailure::Unencrypted && protocol_version > 0;
                        if !kept {
                            bump(&media.receive.undecryptable);
                            unhold(held_users, held.user);
                        }
                        kept
                    }
                }
            });
        }
        let mut cut = work.len();
        for (index, item) in work.iter_mut().enumerate().skip(from) {
            if item.plain.is_some() {
                continue;
            }
            if spent {
                cut = index;
                break;
            }
            let payload = item.arrival.payload.as_deref().unwrap_or_default();
            let decrypted = dave.receive(item.user, payload, &mut self.plain);
            spent = started.elapsed() >= budget;
            match decrypted {
                Ok(()) => {
                    self.streaks.remove(&item.user);
                    item.plain = Some(share(&mut self.payloads, &self.plain));
                }
                Err(failure) => {
                    if counting && failure != DecryptFailure::NoDecryptor {
                        let streak = self.streaks.entry(item.user).or_default();
                        streak.failures = streak.failures.saturating_add(1);
                    }
                    if failure == DecryptFailure::Unencrypted || protocol_version == 0 {
                        bump(&self.media.receive.undecryptable);
                    } else {
                        self.hold(item, generation);
                    }
                }
            }
        }
        if dave.counters() != before {
            self.media.refresh(dave);
        }
        (cut, spent)
    }

    fn hold(&mut self, item: &Work, tried: u64) {
        let undecryptable = &self.media.receive.undecryptable;
        if self.tuning.hold_packets == 0 || self.tuning.arrivals == 0 {
            bump(undecryptable);
            return;
        }
        let count = self.held_users.entry(item.user).or_default();
        if *count >= self.tuning.hold_packets {
            if let Some(index) = self.held.iter().position(|held| held.user == item.user)
                && self.held.remove(index).is_some()
            {
                bump(undecryptable);
            }
        } else {
            *count = count.saturating_add(1);
        }
        if self.held.len() >= self.tuning.arrivals
            && let Some(oldest) = self.held.pop_front()
        {
            unhold(&mut self.held_users, oldest.user);
            bump(undecryptable);
        }
        self.held.push_back(Held {
            user: item.user,
            arrival: item.arrival.clone(),
            check: item.check,
            tried,
        });
    }

    fn request_recovery(&mut self, now: Instant) {
        let due = |last: Option<Instant>| {
            last.is_none_or(|last| now.saturating_duration_since(last) >= RECOVERY_INTERVAL)
        };
        if !due(self.last_request) {
            return;
        }
        let asking = self.streaks.iter_mut().find(|(_, streak)| {
            streak.failures >= self.tuning.decrypt_tolerance
                && streak.requested < RECOVERIES_PER_USER
                && due(streak.last_request)
        });
        if let Some((user, streak)) = asking {
            debug!(%user, failures = streak.failures, "asking for a DAVE recovery after repeated decrypt failures");
            streak.failures = 0;
            streak.requested = streak.requested.saturating_add(1);
            streak.last_request = Some(now);
            self.last_request = Some(now);
            self.media.recover.notify_one();
        }
    }

    fn wait(&mut self, mut item: Work) {
        self.waits.insert(item.user);
        let samples = item.plain.as_deref().and_then(codec::received_samples);
        self.hear(&item, samples);
        item.fresh = false;
        if self.waiting.len() >= self.tuning.arrivals && self.waiting.pop_front().is_some() {
            bump(&self.media.receive.arrivals_dropped);
        }
        self.waiting.push_back(item);
    }

    fn hear(&mut self, item: &Work, samples: Option<u32>) {
        if !item.fresh || item.arrival.own {
            return;
        }
        let at48 = samples_since(self.epoch, item.arrival.at);
        let ssrc = item.arrival.ssrc;
        match &item.plain {
            Some(plain) => {
                let duration = samples.unwrap_or(FRAME_48K);
                self.speak(item.user, ssrc, at48, plain, false, duration);
            }
            None => {
                let payload = item.arrival.payload.as_deref().unwrap_or_default();
                self.speak(item.user, ssrc, at48, payload, false, FRAME_48K);
            }
        }
    }

    fn accept(&mut self, item: Work, hangover: i64) {
        let samples = item.plain.as_deref().and_then(codec::received_samples);
        self.hear(&item, samples);
        let Work {
            user,
            arrival,
            check,
            plain,
            ..
        } = item;
        let at48 = samples_since(self.epoch, arrival.at);
        let Some(plain) = plain else {
            return;
        };
        let Some(samples) = samples else {
            bump(&self.media.receive.invalid_payloads);
            return;
        };
        let Some(feed) = self.streams.get_mut(&(arrival.ssrc, user)) else {
            return;
        };
        let slot = Slot {
            sequence: arrival.sequence,
            timestamp: arrival.timestamp,
            samples,
            at: at48,
            payload: Some(plain.clone()),
        };
        if feed.stream.insert(slot, check) {
            let own = feed.own;
            let played = self.subscribers.values().any(|subscriber| {
                subscriber.out.format != ReceiveFormat::Packets
                    && wants(subscriber.receiver.options(), user, own)
            });
            if !played {
                feed.stream.release_before(arrival.sequence.wrapping_add(1));
            }
        } else {
            bump(&self.media.receive.late_packets);
        }
        self.deliver(user, &arrival, &plain, samples, at48, hangover);
    }

    fn deliver(
        &mut self,
        user: UserId,
        arrival: &Arrival,
        opus: &Bytes,
        samples: u32,
        at48: i64,
        hangover: i64,
    ) {
        let taking = |subscriber: &Subscriber| {
            subscriber.out.format == ReceiveFormat::Packets
                && arrival.at >= subscriber.receiver.opened()
                && wants(subscriber.receiver.options(), user, arrival.own)
        };
        if !self.subscribers.values().any(taking) {
            return;
        }
        let extensions = self.extensions(arrival);
        let speaking = speech::is_speech(opus, false);
        for subscriber in self.subscribers.values_mut() {
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
                    out.items.push(speech_item(self.epoch, user, edge, None));
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
            self.heard = true;
        }
    }

    fn extensions(&self, arrival: &Arrival) -> Vec<(u8, Bytes)> {
        let Some((profile, body)) = &arrival.extension else {
            return Vec::new();
        };
        match rtp::extensions(*profile, body) {
            Ok(elements) => elements
                .into_iter()
                .map(|(id, data)| (id, body.slice_ref(data)))
                .collect(),
            Err(_) => {
                bump(&self.media.receive.invalid_extensions);
                Vec::new()
            }
        }
    }

    fn wanted(&self, user: UserId, own: bool) -> bool {
        self.receivers
            .iter()
            .any(|receiver| wants(receiver.options(), user, own))
    }

    fn speak(
        &mut self,
        user: UserId,
        ssrc: u32,
        at48: i64,
        payload: &[u8],
        silence: bool,
        samples: u32,
    ) {
        if !self.speech_activity {
            return;
        }
        let activity = self.speech.entry(user).or_default();
        let speaking = speech::is_speech(payload, silence);
        if let Some(edge) = activity.packet(ssrc, at48, samples, speaking) {
            self.media
                .push(speech_delivery(self.media.guild(), self.epoch, user, edge));
        }
    }

    fn expire_speech(&mut self, now48: i64, hangover: i64) {
        for (&user, activity) in &mut self.speech {
            if let Some(edge) = activity.expire(now48, hangover) {
                self.media
                    .push(speech_delivery(self.media.guild(), self.epoch, user, edge));
            }
        }
        self.speech.retain(|_, activity| activity.is_speaking());
    }

    fn release(
        &mut self,
        now: Instant,
        now48: i64,
        skipped: u32,
        deadline: Instant,
        hangover: i64,
        transport: bool,
    ) {
        for subscriber in self.subscribers.values_mut() {
            subscriber.follow(&self.streams);
            if let Some(mix) = &mut subscriber.out.mix {
                mix.owe(transport, skipped);
            }
        }
        self.handles.clear();
        self.handles.extend(self.subscribers.keys().copied());
        self.keys.clear();
        self.keys.extend(self.streams.keys().copied());
        rotate(&mut self.handles, self.turn);
        rotate(&mut self.keys, self.turn);
        self.turn = self.turn.wrapping_add(1);
        let mut context = Context {
            media: &self.media,
            scratch: &mut self.scratch,
            epoch: self.epoch,
            now,
            now48,
            hangover,
            max_conceal: to_samples(self.tuning.max_conceal),
            heard: false,
        };
        let mut deferred = false;
        for handle in &self.handles {
            let Some(subscriber) = self.subscribers.get_mut(handle) else {
                continue;
            };
            if deferred || subscriber.out.format == ReceiveFormat::Packets {
                continue;
            }
            deferred = !subscriber.play(&mut self.streams, &self.keys, &mut context, deadline);
        }
        self.heard |= context.heard;
        if deferred {
            bump(&self.media.receive.receive_deferred);
        }
        for subscriber in self.subscribers.values_mut() {
            let out = &mut subscriber.out;
            if out.lost > 0 {
                subscriber.receiver.add_lost(mem::take(&mut out.lost));
            }
            if let Some(mix) = &mut out.mix
                && mix.dropped > 0
            {
                subscriber.receiver.add_dropped(mem::take(&mut mix.dropped));
            }
            out.peak.observe(out.items.len());
            if !out.items.is_empty() {
                subscriber.receiver.push(out.items.drain(..), now);
            }
            out.peak
                .shrink(out.items.capacity(), |to| out.items.shrink_to(to));
            out.peak.advance();
        }
        for (key, feed) in &mut self.streams {
            let mut oldest: Option<u16> = None;
            let mut waiting = false;
            for track in self
                .subscribers
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
    }

    fn watch(&mut self, now: Instant) {
        let failures = self
            .media
            .receive
            .transport_failures
            .load(Ordering::Relaxed);
        let failing = mem::replace(&mut self.watchdog.failures, failures) != failures;
        if self.subscribers.is_empty() {
            self.resume(now);
            return;
        }
        if self.arrived || failing {
            self.watchdog.media = Some(now);
        }
        if self.heard {
            self.resume(now);
            return;
        }
        let reaching = self
            .watchdog
            .media
            .is_some_and(|at| now.saturating_duration_since(at) <= MEDIA_RECENT);
        if !reaching {
            self.watchdog.stall.ease();
            return;
        }
        if let Some(stalled) = self.watchdog.stall.press(now, self.media.stall_after()) {
            self.media.stalled(Direction::Receive, true, stalled);
        }
    }

    fn resume(&mut self, now: Instant) {
        if let Some(duration) = self.watchdog.stall.resume(now) {
            self.media.stalled(Direction::Receive, false, duration);
        }
    }

    fn prune(&mut self, now: Instant, now48: i64) {
        let idle = to_samples(self.tuning.stream_idle);
        self.streams.retain(|_, feed| {
            let last = feed.stream.last_packet().unwrap_or(feed.created);
            now48.saturating_sub(last) < idle
        });
        let hold = self.tuning.hold;
        let expired = |at: Instant| now.saturating_duration_since(at) > hold;
        let unmapped = &self.media.receive.unmapped;
        self.unmapped.retain(|_, queue| {
            while queue.front().is_some_and(|arrival| expired(arrival.at)) {
                queue.pop_front();
                bump(unmapped);
            }
            !queue.is_empty()
        });
        while self
            .held
            .front()
            .is_some_and(|held| expired(held.arrival.at))
        {
            if let Some(held) = self.held.pop_front() {
                unhold(&mut self.held_users, held.user);
                bump(&self.media.receive.undecryptable);
            }
        }
        self.owners.retain(|_, spans| {
            spans.retain(|span| span.until.is_none_or(|until| !expired(until)));
            !spans.is_empty()
        });
        self.heard_at
            .retain(|_, last| now48.saturating_sub(*last) < idle);
        self.forget_at = self
            .heard_at
            .values()
            .map(|last| last.saturating_add(idle))
            .min();
        let members = self.info.members.lock();
        let heard_at = &self.heard_at;
        let kept = |user: &UserId| heard_at.contains_key(user) || members.contains(user);
        self.streaks.retain(|user, _| kept(user));
        self.member_count = members.len();
    }

    fn warn_undecryptable(&mut self, now: Instant) {
        let dropped = self.media.receive.undecryptable.load(Ordering::Relaxed);
        if dropped > self.undecryptable && self.warned.allows(now) {
            warn!(
                guild = %self.media.guild(),
                dropped = dropped.saturating_sub(self.undecryptable),
                "dropped received voice frames that could not be decrypted"
            );
            self.undecryptable = dropped;
        }
    }

    fn shrink_buffers(&mut self) {
        self.peak.observe(self.held.len());
        self.peak.observe(self.waiting.len());
        let peak = &self.peak;
        peak.shrink(self.arrivals.capacity(), |to| self.arrivals.shrink_to(to));
        peak.shrink(self.batch.capacity(), |to| self.batch.shrink_to(to));
        peak.shrink(self.work.capacity(), |to| self.work.shrink_to(to));
        peak.shrink(self.retried.capacity(), |to| self.retried.shrink_to(to));
        peak.shrink(self.held.capacity(), |to| self.held.shrink_to(to));
        peak.shrink(self.waiting.capacity(), |to| self.waiting.shrink_to(to));
        self.peak.advance();
        self.streams_peak.observe(self.streams.len());
        self.streams_peak
            .shrink(self.streams.capacity(), |to| self.streams.shrink_to(to));
        self.streams_peak.advance();
    }
}

impl Drop for Reception {
    fn drop(&mut self) {
        self.media.closed.store(true, Ordering::Release);
        self.media.lost.notify_one();
    }
}

impl Feed {
    fn new(next: &mut u64, user: UserId, own: bool, now48: i64) -> Self {
        let id = *next;
        *next = id.wrapping_add(1);
        Self {
            id,
            own,
            stream: Stream::new(user),
            created: now48,
            trimmed: None,
        }
    }
}

impl Subscriber {
    fn new(receiver: Arc<Receiver>, epoch: Instant) -> Self {
        let options = receiver.options();
        let jitter = to_samples(options.jitter);
        let mix = match options.format {
            ReceiveFormat::Pcm(format) if options.mix => Some(Mix::new(format, options.jitter)),
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
                if samples < slot.samples
                    && feed
                        .trimmed
                        .is_none_or(|trimmed| is_before(trimmed, slot.sequence))
                {
                    feed.trimmed = Some(slot.sequence);
                    bump(&context.media.receive.invalid_payloads);
                }
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
            ReceiveFormat::Pcm(format) => {
                let decoder = decoder(track, format);
                let decoded = match decoder
                    .as_mut()
                    .map(|decoder| decoder.decode(payload, context.scratch))
                {
                    Some(Ok(decoded)) => decoded,
                    Some(Err(error)) => {
                        debug!(%error, %user, "a received packet did not decode");
                        bump(&context.media.receive.invalid_payloads);
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
            ReceiveFormat::Pcm(format) => {
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
        let ReceiveFormat::Pcm(format) = self.format else {
            return true;
        };
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
            codec::write_samples(&mix.sum, format.sample, &mut data);
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

fn share(payloads: &mut BytesMut, payload: &[u8]) -> Bytes {
    if payloads.capacity() < payload.len() {
        *payloads = BytesMut::with_capacity(PAYLOAD_CHUNK.max(payload.len()));
    }
    payloads.extend_from_slice(payload);
    payloads.split().freeze()
}

fn decoder(track: &mut Track, format: PcmFormat) -> &mut Option<Decoder> {
    if track.decoder.is_none() {
        track.decoder = Decoder::new(format.rate, format.channels).ok();
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

fn owner(spans: &[Span], at: Instant, hold: Duration) -> Option<UserId> {
    spans
        .iter()
        .rev()
        .find(|span| {
            span.from.is_none_or(|from| from <= at)
                && span
                    .until
                    .is_none_or(|until| until.checked_add(hold).is_none_or(|end| at <= end))
        })
        .map(|span| span.user)
}

fn unhold(held_users: &mut HashMap<UserId, usize>, user: UserId) {
    if let Some(count) = held_users.get_mut(&user) {
        *count = count.saturating_sub(1);
        if *count == 0 {
            held_users.remove(&user);
        }
    }
}

fn add(counter: &AtomicU64, count: usize) {
    counter.fetch_add(u64::try_from(count).unwrap_or(u64::MAX), Ordering::Relaxed);
}

fn rotate<T>(items: &mut [T], turn: usize) {
    if let Some(by) = turn.checked_rem(items.len()) {
        items.rotate_left(by);
    }
}

fn samples_since(epoch: Instant, instant: Instant) -> i64 {
    to_samples(instant.saturating_duration_since(epoch))
}

fn duration_of(samples: i64) -> Duration {
    let samples = u64::try_from(samples).unwrap_or(0);
    Duration::from_nanos(samples.saturating_mul(NANOS_PER_3_SAMPLES) / 3)
}

fn instant(epoch: Instant, samples: i64) -> Instant {
    epoch.checked_add(duration_of(samples)).unwrap_or(epoch)
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

fn speech_delivery(guild: GuildId, epoch: Instant, user: UserId, edge: Edge) -> Delivery {
    let (ssrc, at, speaking) = speaking(epoch, edge);
    Delivery::VoiceSpeech {
        guild,
        user,
        ssrc,
        speaking,
        at,
    }
}

fn speaking(epoch: Instant, edge: Edge) -> (u32, Instant, Speaking) {
    match edge {
        Edge::Start { ssrc, at } => (ssrc, instant(epoch, at), Speaking::Started),
        Edge::Stop { ssrc, at, duration } => (
            ssrc,
            instant(epoch, at),
            Speaking::Stopped(duration_of(duration)),
        ),
    }
}

#[cfg(test)]
#[expect(clippy::arithmetic_side_effects)]
mod tests;
