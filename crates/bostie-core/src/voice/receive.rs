#![deny(clippy::arithmetic_side_effects)]

use std::collections::{HashMap, HashSet, VecDeque};
use std::mem;
use std::net::UdpSocket;
use std::panic::{self, AssertUnwindSafe};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use bytes::{Bytes, BytesMut};
use opus::{Channels, SoftClip};
use parking_lot::MutexGuard;
use tracing::{debug, warn};

use crate::ids::{Handle, UserId};
use crate::inbox::Delivery;
use crate::voice::codec::{self, Decoder};
use crate::voice::connection::Info;
use crate::voice::crypto::ReplayWindow;
use crate::voice::dave::{self, Dave, DecryptFailure};
use crate::voice::media::{Arrival, Media, Transport, UserCounters, bump};
use crate::voice::pacer::Tick;
use crate::voice::playout::{Check, Cursor, Release, Slot, Stream, to_samples};
use crate::voice::receiver::{Item, Receiver};
use crate::voice::rtp;
use crate::voice::speech::{self, Activity, Edge};
use crate::voice::types::{
    Direction, FRAME, FRAME_SAMPLES, PcmFormat, ReceiveFormat, ReceiveOptions, ReceiveTuning,
    SAMPLE_RATE, SILENCE_FRAME,
};

const FRAME_48K: u32 = FRAME_SAMPLES as u32;
const PEAK_TICKS: u32 = 24;
const PAYLOAD_CHUNK: usize = 8 * 1024;
const MEDIA_RECENT: Duration = Duration::from_millis(100);
const MIX_SLACK: Duration = Duration::from_millis(100);
const NANOS_PER_SECOND: u128 = 1_000_000_000;
const WARN_EVERY: Duration = Duration::from_secs(1);

type Key = (u32, UserId);

pub struct Intake {
    media: Arc<Media>,
    info: Arc<Info>,
    socket: Arc<UdpSocket>,
    transport: Option<Arc<Transport>>,
    windows: HashMap<u32, ReplayWindow>,
    replay_window: u32,
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
        }
    }

    pub fn rtp(&mut self, packet: &mut [u8]) {
        if panic::catch_unwind(AssertUnwindSafe(|| self.open(packet))).is_err() {
            bump(&self.media.receive.rx_panics);
        }
    }

    fn open(&mut self, packet: &mut [u8]) {
        let media = &self.media;
        bump(&media.counters.packets_received);
        if !media.speech_activity() && media.open_receivers.load(Ordering::Relaxed) == 0 {
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
            self.windows.retain(|ssrc, _| ssrcs.contains_key(ssrc));
        }
        let replay_window = self.replay_window;
        let window = self
            .windows
            .entry(header.ssrc)
            .or_insert_with(|| ReplayWindow::new(replay_window));
        let counter = u32::from_le_bytes(opened.nonce.to_be_bytes());
        if !window.accept(counter) {
            bump(&media.receive.replays);
            return;
        }
        if header.ssrc == transport.ssrc {
            bump(&media.receive.own_echoes);
            return;
        }
        let payload = (media.open_receivers.load(Ordering::Relaxed) > 0)
            .then(|| Bytes::copy_from_slice(opened.payload));
        let extension = header
            .extension
            .filter(|_| media.packet_receivers.load(Ordering::Relaxed) > 0)
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
    received: u64,
    reported_lost: u64,
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
    since: Option<Instant>,
    reported: bool,
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

    pub(super) fn excess(&self, capacity: usize) -> Option<usize> {
        let largest = self.current.max(self.previous);
        (capacity > largest.saturating_mul(4)).then(|| largest.saturating_mul(2))
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
    frame: usize,
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
    users: &'a mut HashMap<UserId, UserCounters>,
    scratch: &'a mut Vec<f32>,
    epoch: Instant,
    now: Instant,
    now48: i64,
    hangover: i64,
    max_conceal: i64,
    heard: bool,
    changed: bool,
}

pub struct Reception {
    media: Arc<Media>,
    info: Arc<Info>,
    user: UserId,
    tuning: ReceiveTuning,
    epoch: Instant,
    state: Option<Box<State>>,
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
    users: HashMap<UserId, UserCounters>,
    heard_at: HashMap<UserId, i64>,
    forget_at: Option<i64>,
    member_count: usize,
    users_changed: bool,
    undecryptable: u64,
    warned: Option<Instant>,
    next_feed: u64,
    turn: usize,
}

impl Reception {
    pub fn new(media: Arc<Media>, info: Arc<Info>, user: UserId, tuning: ReceiveTuning) -> Self {
        Self {
            media,
            info,
            user,
            tuning,
            epoch: Instant::now(),
            state: None,
        }
    }

    pub fn tick(&mut self, tick: Tick, deadline: Instant) -> bool {
        self.run(Instant::now(), tick.skipped, deadline)
    }

    fn run(&mut self, now: Instant, skipped: u32, deadline: Instant) -> bool {
        if self.state.is_none()
            && self.media.open_receivers.load(Ordering::Relaxed) == 0
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
        state.run(now, skipped, deadline)
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
            users: HashMap::new(),
            heard_at: HashMap::new(),
            forget_at: None,
            member_count: 0,
            users_changed: false,
            undecryptable: 0,
            warned: None,
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
        self.publish_users();
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
            && !self.watchdog.reported
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
            let counters = self.users.entry(user).or_default();
            counters.packets = counters.packets.saturating_add(1);
            self.users_changed = true;
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
            let counters = self.users.entry(user).or_default();
            counters.duplicates = counters.duplicates.saturating_add(1);
            return;
        }
        self.admitted.insert(admitted);
        let refused = payload.is_empty()
            || (!own
                && protocol_version > 0
                && !dave::has_marker(payload)
                && codec::packet_samples(payload).is_none());
        if refused {
            self.invalid(user);
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
        self.users_changed = true;
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
                    failed(
                        &mut self.streaks,
                        &mut self.users,
                        item.user,
                        failure,
                        counting,
                    );
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
        let interval = self.tuning.recovery_interval;
        let due = |last: Option<Instant>| {
            last.is_none_or(|last| now.saturating_duration_since(last) >= interval)
        };
        if !due(self.last_request) {
            return;
        }
        let asking = self.streaks.iter_mut().find(|(_, streak)| {
            streak.failures >= self.tuning.decrypt_tolerance
                && streak.requested < self.tuning.recoveries_per_user
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
            bump(&self.media.receive.undecryptable);
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
            self.invalid(user);
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
        if feed.stream.insert(slot, check, &self.tuning) {
            feed.received = feed.received.saturating_add(1);
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

    fn invalid(&mut self, user: UserId) {
        bump(&self.media.receive.invalid_payloads);
        let counters = self.users.entry(user).or_default();
        counters.invalid_payloads = counters.invalid_payloads.saturating_add(1);
        self.users_changed = true;
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
        if let Some(Edge::Start { ssrc, at }) = activity.packet(ssrc, at48, samples, speaking) {
            self.media.push(Delivery::VoiceSpeech {
                guild: self.media.guild(),
                user,
                ssrc,
                speaking: true,
                at: instant(self.epoch, at),
                duration: Duration::ZERO,
            });
        }
    }

    fn expire_speech(&mut self, now48: i64, hangover: i64) {
        for (&user, activity) in &mut self.speech {
            if let Some(Edge::Stop { ssrc, at, duration }) = activity.expire(now48, hangover) {
                self.media.push(Delivery::VoiceSpeech {
                    guild: self.media.guild(),
                    user,
                    ssrc,
                    speaking: false,
                    at: instant(self.epoch, at),
                    duration: duration_of(duration),
                });
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
            users: &mut self.users,
            scratch: &mut self.scratch,
            epoch: self.epoch,
            now,
            now48,
            hangover,
            max_conceal: to_samples(self.tuning.max_conceal),
            heard: false,
            changed: false,
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
        self.users_changed |= context.changed;
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
            if let Some(target) = out.peak.excess(out.items.capacity()) {
                out.items.shrink_to(target);
            }
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
                        oldest = Some(oldest.map_or(next, |oldest| earlier(next, oldest)));
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
        let watchdog = &mut self.watchdog;
        if self.subscribers.is_empty() {
            let since = watchdog.since.take();
            if mem::take(&mut watchdog.reported)
                && let Some(since) = since
            {
                self.media.stalled(
                    Direction::Receive,
                    false,
                    now.saturating_duration_since(since),
                );
            }
            return;
        }
        if self.arrived || failing {
            watchdog.media = Some(now);
        }
        if self.heard {
            if let Some(since) = watchdog.since.take()
                && mem::take(&mut watchdog.reported)
            {
                self.media.stalled(
                    Direction::Receive,
                    false,
                    now.saturating_duration_since(since),
                );
            }
            return;
        }
        let reaching = watchdog
            .media
            .is_some_and(|at| now.saturating_duration_since(at) <= MEDIA_RECENT);
        if reaching {
            let since = *watchdog.since.get_or_insert(now);
            let stalled = now.saturating_duration_since(since);
            if !watchdog.reported && stalled >= self.media.stall_after() {
                watchdog.reported = true;
                self.media.stalled(Direction::Receive, true, stalled);
            }
        } else if !watchdog.reported {
            watchdog.since = None;
        }
    }

    fn publish_users(&mut self) {
        for feed in self.streams.values_mut().filter(|feed| !feed.own) {
            let lost = feed.stream.expected().saturating_sub(feed.received);
            if lost != feed.reported_lost {
                let counters = self.users.entry(feed.stream.user()).or_default();
                counters.lost = counters
                    .lost
                    .saturating_add(lost)
                    .saturating_sub(feed.reported_lost);
                feed.reported_lost = lost;
                self.users_changed = true;
            }
        }
        if mem::take(&mut self.users_changed) {
            self.media.users.lock().clone_from(&self.users);
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
        let before = self.users.len();
        self.users.retain(|user, _| kept(user));
        self.streaks.retain(|user, _| kept(user));
        self.member_count = members.len();
        drop(members);
        if self.users.len() != before {
            self.users_changed = true;
        }
    }

    fn warn_undecryptable(&mut self, now: Instant) {
        let dropped = self.media.receive.undecryptable.load(Ordering::Relaxed);
        let quiet = self
            .warned
            .is_none_or(|at| now.saturating_duration_since(at) >= WARN_EVERY);
        if dropped > self.undecryptable && quiet {
            warn!(
                guild = %self.media.guild(),
                dropped = dropped.saturating_sub(self.undecryptable),
                "dropped received voice frames that could not be decrypted"
            );
            self.undecryptable = dropped;
            self.warned = Some(now);
        }
    }

    fn shrink_buffers(&mut self) {
        self.peak.observe(self.held.len());
        self.peak.observe(self.waiting.len());
        if let Some(target) = self.peak.excess(self.arrivals.capacity()) {
            self.arrivals.shrink_to(target);
        }
        if let Some(target) = self.peak.excess(self.batch.capacity()) {
            self.batch.shrink_to(target);
        }
        if let Some(target) = self.peak.excess(self.work.capacity()) {
            self.work.shrink_to(target);
        }
        if let Some(target) = self.peak.excess(self.retried.capacity()) {
            self.retried.shrink_to(target);
        }
        if let Some(target) = self.peak.excess(self.held.capacity()) {
            self.held.shrink_to(target);
        }
        if let Some(target) = self.peak.excess(self.waiting.capacity()) {
            self.waiting.shrink_to(target);
        }
        self.peak.advance();
        self.streams_peak.observe(self.streams.len());
        if let Some(target) = self.streams_peak.excess(self.streams.capacity()) {
            self.streams.shrink_to(target);
        }
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
            received: 0,
            reported_lost: 0,
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
                        .is_none_or(|trimmed| later(slot.sequence, trimmed))
                {
                    feed.trimmed = Some(slot.sequence);
                    context.invalid(user);
                }
                self.packet(&slot, due, samples, user, ssrc, track, context);
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
                    let lost = Lost {
                        timestamp: timestamp.wrapping_add(shift),
                        due: due.saturating_add(i64::from(shift)),
                        samples,
                    };
                    self.conceal(lost, fec, user, ssrc, track, context);
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

    #[expect(clippy::too_many_arguments)]
    fn packet(
        &mut self,
        slot: &Slot,
        due: i64,
        samples: u32,
        user: UserId,
        ssrc: u32,
        track: &mut Track,
        context: &mut Context<'_>,
    ) {
        let Some(payload) = &slot.payload else {
            return;
        };
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
        let at = instant(context.epoch, slot.at);
        let frames = Frames {
            user,
            ssrc,
            timestamp: slot.timestamp,
            due,
            at,
            lost: false,
        };
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
                        context.invalid(user);
                        let lost = Lost {
                            timestamp: slot.timestamp,
                            due,
                            samples,
                        };
                        self.conceal(lost, None, user, ssrc, track, context);
                        return;
                    }
                    None => return,
                };
                self.pcm(
                    format,
                    frames,
                    decoded,
                    samples,
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
                    timestamp: Some(slot.timestamp),
                    offset,
                    samples,
                    at,
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
        lost: Lost,
        fec: Option<&[u8]>,
        user: UserId,
        ssrc: u32,
        track: &mut Track,
        context: &mut Context<'_>,
    ) {
        let frames = Frames {
            user,
            ssrc,
            timestamp: lost.timestamp,
            due: lost.due,
            at: context.now,
            lost: true,
        };
        match self.format {
            ReceiveFormat::Pcm(format) => {
                let decoder = decoder(track, format);
                let Some(Ok(decoded)) = decoder
                    .as_mut()
                    .map(|decoder| decoder.conceal(fec, lost.samples, context.scratch))
                else {
                    return;
                };
                self.pcm(
                    format,
                    frames,
                    decoded,
                    lost.samples,
                    &mut track.cursor,
                    context,
                    &mut None,
                );
            }
            ReceiveFormat::Opus => {
                let offset = track.cursor.offset(lost.due, lost.samples);
                self.items.push(Item::Frame {
                    user: Some(user),
                    ssrc: Some(ssrc),
                    timestamp: Some(lost.timestamp),
                    offset,
                    samples: lost.samples,
                    at: context.now,
                    data: Bytes::new(),
                    lost: true,
                });
            }
            ReceiveFormat::Packets => {}
        }
    }

    #[expect(clippy::too_many_arguments)]
    fn pcm(
        &mut self,
        format: PcmFormat,
        frames: Frames,
        decoded: usize,
        keep48: u32,
        cursor: &mut Cursor,
        context: &mut Context<'_>,
        start: &mut Option<Edge>,
    ) -> Option<u64> {
        let ratio = SAMPLE_RATE.checked_div(format.rate).unwrap_or(1).max(1);
        let keep = usize::try_from(keep48.checked_div(ratio).unwrap_or(0)).unwrap_or(0);
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
            let timestamp = frames.timestamp.wrapping_add(consumed);
            let due = frames.due.saturating_add(i64::from(consumed));
            consumed = consumed.saturating_add(samples);
            if let Some(mix) = &mut self.mix {
                if let Some(fifo) = mix.fifos.get_mut(&(frames.ssrc, frames.user)) {
                    fifo.push(pcm, frames.lost, mix.frame);
                    mix.dropped = mix.dropped.saturating_add(fifo.trim(mix.frame, mix.limit));
                }
                continue;
            }
            let offset = cursor.offset(due, samples);
            if let Some(edge) = start.take() {
                self.items
                    .push(speech_item(context.epoch, frames.user, edge, Some(offset)));
            }
            let mut data = BytesMut::new();
            codec::write_samples(pcm, format.sample, &mut data);
            self.items.push(Item::Frame {
                user: Some(frames.user),
                ssrc: Some(frames.ssrc),
                timestamp: Some(timestamp),
                offset,
                samples,
                at: frames.at,
                data: data.freeze(),
                lost: frames.lost,
            });
            context.heard |= !frames.lost;
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
            let offset = *mix.next.get_or_insert(
                anchor.saturating_sub(anchor.checked_rem(u64::from(FRAME_48K)).unwrap_or(0)),
            );
            mix.sum.clear();
            mix.sum.resize(mix.frame, 0.0);
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
struct Frames {
    user: UserId,
    ssrc: u32,
    timestamp: u32,
    due: i64,
    at: Instant,
    lost: bool,
}

#[derive(Debug, Clone, Copy)]
struct Lost {
    timestamp: u32,
    due: i64,
    samples: u32,
}

impl Context<'_> {
    fn invalid(&mut self, user: UserId) {
        bump(&self.media.receive.invalid_payloads);
        let counters = self.users.entry(user).or_default();
        counters.invalid_payloads = counters.invalid_payloads.saturating_add(1);
        self.changed = true;
    }
}

impl Mix {
    fn new(format: PcmFormat, jitter: Duration) -> Self {
        let channels = if format.channels == 1 {
            Channels::Mono
        } else {
            Channels::Stereo
        };
        let per_channel = format.rate.checked_div(50).unwrap_or(0);
        let frame =
            usize::try_from(per_channel.saturating_mul(u32::from(format.channels))).unwrap_or(0);
        let window = jitter.saturating_add(MIX_SLACK).as_nanos();
        let limit = window.checked_div(FRAME.as_nanos()).unwrap_or(0).max(1);
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

    fn push(&mut self, pcm: &[f32], lost: bool, frame: usize) {
        self.samples.extend(pcm);
        self.pending_lost |= lost;
        while frame > 0 && self.samples.len().checked_div(frame).unwrap_or(0) > self.lost.len() {
            self.lost.push_back(mem::take(&mut self.pending_lost));
        }
    }

    fn trim(&mut self, frame: usize, limit: usize) -> u64 {
        let mut dropped: u64 = 0;
        while self.lost.len() > limit && self.samples.len() >= frame {
            self.samples.drain(..frame);
            self.lost.pop_front();
            dropped = dropped.saturating_add(1);
        }
        dropped
    }

    fn pop(&mut self, sum: &mut [f32], frame: usize) -> Option<bool> {
        if frame == 0 || self.samples.len() < frame {
            return None;
        }
        for (total, sample) in sum.iter_mut().zip(self.samples.drain(..frame)) {
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

fn failed(
    streaks: &mut HashMap<UserId, Streak>,
    users: &mut HashMap<UserId, UserCounters>,
    user: UserId,
    failure: DecryptFailure,
    counting: bool,
) {
    let counters = users.entry(user).or_default();
    counters.decrypt_failures = counters.decrypt_failures.saturating_add(1);
    if counting && failure != DecryptFailure::NoDecryptor {
        let streak = streaks.entry(user).or_default();
        streak.failures = streak.failures.saturating_add(1);
    }
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

fn earlier(a: u16, b: u16) -> u16 {
    if later(b, a) { a } else { b }
}

fn later(sequence: u16, other: u16) -> bool {
    sequence.wrapping_sub(other).cast_signed() > 0
}

fn samples_since(epoch: Instant, instant: Instant) -> i64 {
    to_samples(instant.saturating_duration_since(epoch))
}

fn duration_of(samples: i64) -> Duration {
    let samples = u128::from(u64::try_from(samples).unwrap_or(0));
    let nanos = samples
        .saturating_mul(NANOS_PER_SECOND)
        .checked_div(u128::from(SAMPLE_RATE))
        .unwrap_or(0);
    Duration::from_nanos(u64::try_from(nanos).unwrap_or(u64::MAX))
}

fn instant(epoch: Instant, samples: i64) -> Instant {
    epoch.checked_add(duration_of(samples)).unwrap_or(epoch)
}

fn offset_of(due: i64, opened: i64) -> u64 {
    due.saturating_sub(opened).max(0).unsigned_abs()
}

fn speech_item(epoch: Instant, user: UserId, edge: Edge, offset: Option<u64>) -> Item {
    match edge {
        Edge::Start { ssrc, at } => Item::Speech {
            user,
            ssrc,
            speaking: true,
            offset,
            at: instant(epoch, at),
            duration: Duration::ZERO,
        },
        Edge::Stop { ssrc, at, duration } => Item::Speech {
            user,
            ssrc,
            speaking: false,
            offset,
            at: instant(epoch, at),
            duration: duration_of(duration),
        },
    }
}

#[cfg(test)]
#[expect(clippy::arithmetic_side_effects)]
mod tests {
    use std::collections::HashSet;

    use futures_util::FutureExt;
    use proptest::prelude::*;

    use super::*;
    use crate::ids::{ChannelId, GuildId, ShardId};
    use crate::voice::codec::FrameEncoder;
    use crate::voice::crypto::{Cipher, Mode};
    use crate::voice::dave::{Outbound, SendMode};
    use crate::voice::receiver::ReceiveRead;
    use crate::voice::sink::tests::Fixture;
    use crate::voice::testkit::mls::{Call, Signal};
    use crate::voice::testkit::sfu::{self, Sealing};
    use crate::voice::types::{
        Application, DaveOp, EncoderSettings, Overflow, ReceiveStall, SampleFormat,
    };

    const GUILD: u64 = 10;
    const CHANNEL: u64 = 900;
    const BOT: u64 = 100;
    const ALICE: u64 = 1;
    const BOB: u64 = 2;
    const STRANGER: u64 = 3;
    const BOT_SSRC: u32 = 77;
    const ALICE_SSRC: u32 = 1_001;
    const BOB_SSRC: u32 = 1_002;
    const STRANGER_SSRC: u32 = 1_003;
    const KEY: [u8; 32] = [9; 32];
    const STALL: Duration = Duration::from_millis(100);
    const LATER: Duration = Duration::from_secs(3_600);

    fn user(id: u64) -> UserId {
        UserId::from(id)
    }

    fn tuning() -> ReceiveTuning {
        ReceiveTuning {
            dave_budget: LATER,
            ..ReceiveTuning::default()
        }
    }

    fn opus(count: usize, amplitude: f32) -> Vec<Bytes> {
        let format = PcmFormat {
            rate: 48_000,
            channels: 2,
            sample: SampleFormat::S16Le,
        };
        let settings = EncoderSettings {
            bitrate: 64_000,
            application: Application::Audio,
            fec: true,
            packet_loss: 10,
            complexity: 10,
        };
        let tone: Vec<u8> = (0..count * 960)
            .flat_map(|index| {
                let sample = ((index as f32) * 0.07).sin() * amplitude * 32_767.0;
                let sample = (sample as i16).to_le_bytes();
                [sample, sample].concat()
            })
            .collect();
        FrameEncoder::new(format, settings).unwrap().encode(&tone)
    }

    fn options(format: ReceiveFormat) -> ReceiveOptions {
        ReceiveOptions {
            format,
            users: None,
            exclude: HashSet::new(),
            include_self: false,
            buffer: Duration::from_secs(10),
            jitter: Duration::ZERO,
            markers: false,
            mix: false,
            overflow: Overflow::DropOldest,
            stall: ReceiveStall::Wait,
        }
    }

    fn only(ids: &[u64], format: ReceiveFormat) -> ReceiveOptions {
        ReceiveOptions {
            users: Some(ids.iter().copied().map(user).collect()),
            ..options(format)
        }
    }

    fn pcm(rate: u32, channels: u8, sample: SampleFormat) -> ReceiveFormat {
        ReceiveFormat::Pcm(PcmFormat {
            rate,
            channels,
            sample,
        })
    }

    fn garbage(index: u16) -> Vec<u8> {
        let [high, low] = index.to_be_bytes();
        vec![0x78, high, low, 0x13, 0x37, 0x42, 0x00, 0x01, 0xFA, 0xFA]
    }

    fn long(frames: u8) -> Vec<u8> {
        [0xFF, frames].into_iter().chain(1..=frames * 2).collect()
    }

    #[derive(Debug, Clone, PartialEq, Eq)]
    enum Heard {
        Frame {
            user: Option<u64>,
            timestamp: Option<u32>,
            offset: u64,
            lost: bool,
        },
        Packet {
            sequence: u16,
            opus: Bytes,
            extensions: Vec<(u8, Bytes)>,
        },
        Speech {
            speaking: bool,
            offset: Option<u64>,
        },
    }

    fn heard(items: &[Item]) -> Vec<Heard> {
        items
            .iter()
            .map(|item| match item {
                Item::Frame {
                    user,
                    timestamp,
                    offset,
                    lost,
                    ..
                } => Heard::Frame {
                    user: user.map(u64::from),
                    timestamp: *timestamp,
                    offset: *offset,
                    lost: *lost,
                },
                Item::Packet {
                    sequence,
                    opus,
                    extensions,
                    ..
                } => Heard::Packet {
                    sequence: *sequence,
                    opus: opus.clone(),
                    extensions: extensions.clone(),
                },
                Item::Speech {
                    speaking, offset, ..
                } => Heard::Speech {
                    speaking: *speaking,
                    offset: *offset,
                },
            })
            .collect()
    }

    fn data(items: &[Item]) -> Vec<Bytes> {
        items
            .iter()
            .filter_map(|item| match item {
                Item::Frame { data, .. } => Some(data.clone()),
                _ => None,
            })
            .collect()
    }

    fn senders(items: &[Item]) -> Vec<Option<u64>> {
        items
            .iter()
            .filter_map(|item| match item {
                Item::Frame { user, .. } => Some(user.map(u64::from)),
                _ => None,
            })
            .collect()
    }

    struct Rig {
        fixture: Fixture,
        media: Arc<Media>,
        info: Arc<Info>,
        reception: Reception,
        socket: Arc<UdpSocket>,
        now: Instant,
        next_handle: u64,
    }

    impl Rig {
        fn new(tuning: ReceiveTuning) -> Self {
            crate::init_test_tracing();
            let fixture = Fixture::new();
            let media = Arc::new(Media::new(
                GuildId::from(GUILD),
                user(BOT),
                ChannelId::from(CHANNEL),
                Arc::clone(&fixture.inbox),
                STALL,
                tuning,
            ));
            let info = Arc::new(Info::new(
                GuildId::from(GUILD),
                ShardId::from(0),
                ChannelId::from(CHANNEL),
            ));
            let reception =
                Reception::new(Arc::clone(&media), Arc::clone(&info), user(BOT), tuning);
            let now = reception.epoch + Duration::from_secs(1);
            let rig = Self {
                fixture,
                media,
                info,
                reception,
                socket: Arc::new(UdpSocket::bind("127.0.0.1:0").unwrap()),
                now,
                next_handle: 1,
            };
            rig.install(1);
            rig
        }

        fn dave() -> Self {
            let rig = Self::new(ReceiveTuning {
                dave_grace: Duration::ZERO,
                ..tuning()
            });
            rig.media.with_dave(|dave| {
                let mut out = Vec::new();
                dave.start(1, &mut out).unwrap();
            });
            rig
        }

        fn install(&self, generation: u64) {
            self.media.install(Some(Arc::new(Transport {
                generation,
                socket: Arc::clone(&self.socket),
                ssrc: BOT_SSRC,
                cipher: Cipher::new(Mode::Aes256GcmRtpSize, &KEY),
            })));
        }

        fn open(&mut self, options: ReceiveOptions) -> Arc<Receiver> {
            let receiver = Arc::new(
                Receiver::new(
                    Handle::from(self.next_handle),
                    options,
                    Arc::clone(&self.fixture.inbox),
                )
                .unwrap(),
            );
            self.next_handle += 1;
            assert!(self.media.add_receiver(&receiver));
            receiver
        }

        fn intake(&self) -> Intake {
            Intake::new(
                Arc::clone(&self.media),
                Arc::clone(&self.info),
                Arc::clone(&self.socket),
                64,
            )
        }

        fn map(&self, ssrc: u32, id: u64) {
            self.info.ssrcs.lock().insert(ssrc, (user(id), self.now));
        }

        fn arrival(&self, ssrc: u32, sequence: u16, payload: &[u8]) -> Arrival {
            Arrival {
                ssrc,
                sequence,
                timestamp: u32::from(sequence) * 960,
                at: self.now,
                payload: (self.media.open_receivers.load(Ordering::Relaxed) > 0)
                    .then(|| Bytes::copy_from_slice(payload)),
                silence: payload == SILENCE_FRAME,
                extension: None,
                own: false,
            }
        }

        fn send(&self, ssrc: u32, sequence: u16, payload: &[u8]) {
            self.media.arrive(self.arrival(ssrc, sequence, payload));
        }

        fn tick(&mut self) {
            self.run(0, self.now + LATER);
        }

        fn run(&mut self, skipped: u32, deadline: Instant) {
            assert!(self.reception.run(self.now, skipped, deadline));
        }

        fn step(&mut self) {
            self.now += FRAME;
            self.tick();
        }

        fn counter(&self, name: &str) -> u64 {
            let counters = self.media.receive.snapshot();
            counters
                .into_iter()
                .find(|(key, _)| *key == name)
                .unwrap()
                .1
        }

        fn user_counters(&self, id: u64) -> UserCounters {
            self.media
                .users
                .lock()
                .get(&user(id))
                .copied()
                .unwrap_or_default()
        }

        fn recovered(&self) -> bool {
            self.media.recover.notified().now_or_never().is_some()
        }

        fn speech(&self) -> Vec<(u64, bool, Duration)> {
            self.fixture
                .inbox
                .drain()
                .into_iter()
                .filter_map(|delivery| match delivery {
                    Delivery::VoiceSpeech {
                        user,
                        speaking,
                        duration,
                        ..
                    } => Some((u64::from(user), speaking, duration)),
                    _ => None,
                })
                .collect()
        }

        fn stalls(&self) -> Vec<(Direction, bool, Duration)> {
            self.fixture
                .inbox
                .drain()
                .into_iter()
                .filter_map(|delivery| match delivery {
                    Delivery::VoiceStalled {
                        direction,
                        stalled,
                        duration,
                        ..
                    } => Some((direction, stalled, duration)),
                    _ => None,
                })
                .collect()
        }

        fn group(&mut self, peers: &[u64]) -> Call {
            let mut call = Call::new(ChannelId::from(CHANNEL), 1);
            call.now = self.now;
            for &peer in peers {
                call.join(user(peer));
                call.run();
            }
            let mut out = Vec::new();
            self.media
                .with_dave(|dave| dave.start(1, &mut out))
                .unwrap();
            call.join_remote(user(BOT));
            for message in out {
                call.receive(user(BOT), message);
            }
            call.run();
            self.deliver(&mut call);
            assert_eq!(self.media.dave.lock().send_mode(), SendMode::Encrypt);
            call
        }

        fn deliver(&self, call: &mut Call) {
            while !call.remote.is_empty() {
                for (to, signal) in mem::take(&mut call.remote) {
                    if to != user(BOT) {
                        continue;
                    }
                    let mut out = Vec::new();
                    self.media.with_dave(|dave| match signal {
                        Signal::Dave(op) => {
                            dave.handle(op, self.now, &mut out).unwrap();
                        }
                        Signal::ClientsConnect(users) => dave.clients_connect(&users),
                        Signal::ClientDisconnect(left) => dave.client_disconnect(left),
                    });
                    for message in out {
                        call.receive(user(BOT), message);
                    }
                }
                call.run();
            }
        }
    }

    fn read(receiver: &Receiver) -> Vec<Item> {
        let mut items = Vec::new();
        while let ReceiveRead::Items(batch) = receiver.read(256).unwrap() {
            items.extend(batch.into_iter().map(|(_, item)| item));
        }
        items
    }

    fn join_behind_the_bot(call: &mut Call, peer: u64) {
        call.join(user(peer));
        call.run();
        let committed = call.fake.settle();
        call.enqueue(committed);
        call.run();
        let transition_id = call
            .remote
            .iter()
            .find_map(|(_, signal)| match signal {
                Signal::Dave(DaveOp::AnnounceCommit { transition_id, .. }) => Some(*transition_id),
                _ => None,
            })
            .unwrap();
        call.receive(user(BOT), Outbound::TransitionReady(transition_id));
        call.run();
        assert_eq!(call.dave(user(peer)).send_mode(), SendMode::Encrypt);
    }

    #[test]
    fn an_unmapped_ssrc_is_held_until_op_5_and_dropped_after_the_hold() {
        let mut rig = Rig::new(ReceiveTuning {
            hold_packets: 3,
            unmapped_ssrcs: 2,
            ..tuning()
        });
        let receiver = rig.open(options(ReceiveFormat::Opus));
        let packets = opus(3, 0.3);
        for sequence in 0..4 {
            rig.send(BOB_SSRC, sequence, &packets[0]);
        }
        rig.step();
        let over_the_packet_bound = rig.counter("unmapped");
        for (sequence, packet) in (0..).zip(&packets) {
            rig.send(ALICE_SSRC, sequence, packet);
            rig.step();
        }
        rig.send(STRANGER_SSRC, 0, &packets[0]);
        rig.step();
        let before_op_5 = read(&receiver);
        let over_the_ssrc_bound = rig.counter("unmapped");

        rig.map(ALICE_SSRC, ALICE);
        rig.step();
        let after_op_5 = read(&receiver);
        rig.now += rig.reception.tuning.hold;
        rig.step();

        assert!(before_op_5.is_empty());
        assert_eq!((over_the_packet_bound, over_the_ssrc_bound), (1, 4));
        assert_eq!(senders(&after_op_5), [Some(ALICE); 3]);
        assert_eq!(data(&after_op_5), packets);
        assert_eq!(rig.counter("unmapped"), 5);
        assert!(read(&receiver).is_empty());
    }

    #[test]
    fn users_nobody_receives_are_never_decrypted_but_still_speak() {
        let mut rig = Rig::dave();
        let mut call = rig.group(&[ALICE]);
        rig.map(ALICE_SSRC, ALICE);
        rig.map(BOB_SSRC, BOB);
        rig.send(ALICE_SSRC, 0, &garbage(0));
        let receiver = rig.open(only(&[ALICE], ReceiveFormat::Opus));
        rig.step();
        let queued_before_the_receiver = (read(&receiver), rig.user_counters(ALICE));
        rig.send(BOB_SSRC, 0, &garbage(1));
        rig.step();
        let speech = rig.speech();
        rig.send(ALICE_SSRC, 1, &garbage(2));
        rig.send(ALICE_SSRC, 2, &call.speak(user(ALICE), &long(3)));
        rig.step();
        rig.now += rig.media.speech_hangover() * 2;
        rig.step();
        let mut stopped = rig.speech();
        stopped.sort_unstable();
        rig.send(BOB_SSRC, 1, &garbage(3));
        rig.step();
        let restarted = rig.speech();
        rig.media.set_speech_activity(false);
        rig.step();
        let switched_off = rig.speech();
        rig.send(BOB_SSRC, 2, &garbage(4));
        rig.step();

        assert!(rig.speech().is_empty());
        assert_eq!(restarted, [(BOB, true, Duration::ZERO)]);
        assert_eq!(switched_off, [(BOB, false, FRAME)]);
        assert!(queued_before_the_receiver.0.is_empty());
        assert_eq!(queued_before_the_receiver.1.decrypt_failures, 0);
        assert_eq!(
            speech,
            [(ALICE, true, Duration::ZERO), (BOB, true, Duration::ZERO)]
        );
        let sixty_ms = Duration::from_millis(60);
        assert_eq!(
            stopped,
            [(ALICE, false, FRAME * 2 + sixty_ms), (BOB, false, FRAME)]
        );
        assert_eq!(rig.user_counters(BOB).decrypt_failures, 0);
        assert_eq!(rig.user_counters(BOB).packets, 3);
        assert_eq!(rig.user_counters(ALICE).decrypt_failures, 1);
    }

    #[test]
    fn frames_failing_during_a_late_commit_are_retried_when_the_dave_state_changes() {
        for speaker in [BOB, ALICE] {
            let mut rig = Rig::new(ReceiveTuning {
                dave_retries: 10,
                ..tuning()
            });
            let mut call = rig.group(&[ALICE]);
            rig.map(ALICE_SSRC, ALICE);
            rig.map(BOB_SSRC, BOB);
            rig.map(STRANGER_SSRC, STRANGER);
            let receiver = rig.open(options(ReceiveFormat::Opus));
            let packets = opus(16, 0.3);
            for (sequence, packet) in (0..4).zip(&packets) {
                let frame = call.speak(user(ALICE), packet);
                rig.send(ALICE_SSRC, sequence, &frame);
                rig.step();
            }
            let before = read(&receiver).len();
            let foreign = call.speak(user(ALICE), &packets[0]);

            join_behind_the_bot(&mut call, BOB);
            let ssrc = if speaker == ALICE {
                ALICE_SSRC
            } else {
                BOB_SSRC
            };
            let spoken = &packets[4..];
            let frames: Vec<Vec<u8>> = spoken
                .iter()
                .map(|packet| call.speak(user(speaker), packet))
                .collect();
            for (sequence, frame) in (4..).zip(&frames) {
                rig.send(ssrc, sequence, frame);
            }
            rig.send(STRANGER_SSRC, 0, &foreign);
            rig.step();
            let while_behind = read(&receiver).len();
            rig.send(ssrc, 4, &frames[0]);
            rig.step();
            let without_a_change = read(&receiver).len();
            rig.deliver(&mut call);
            rig.step();
            let first = read(&receiver);
            rig.step();
            let rest = read(&receiver);
            let still_held = rig.counter("undecryptable");
            rig.now += rig.reception.tuning.hold;
            rig.step();

            assert_eq!(before, 4, "{speaker}");
            assert_eq!((while_behind, without_a_change), (0, 0), "{speaker}");
            assert_eq!((first.len(), rest.len()), (10, 2), "{speaker}");
            assert_eq!(data(&[first, rest].concat()), spoken, "{speaker}");
            assert_eq!(still_held, 0, "{speaker}");
            assert_eq!(rig.counter("undecryptable"), 1, "{speaker}");
            assert_eq!(rig.counter("duplicates"), 1, "{speaker}");
            let failures = [speaker, STRANGER].map(|id| rig.user_counters(id).decrypt_failures);
            assert_eq!(failures, [12, 1], "{speaker}");
            assert!(read(&receiver).is_empty(), "{speaker}");
            assert!(!rig.recovered(), "{speaker}");
        }
    }

    #[test]
    fn dave_frames_wait_in_their_users_order_behind_a_busy_lock_or_past_the_deadline_and_decrypt_in_slices_before_it()
     {
        let plain_ending_in_the_marker = Bytes::from_static(&[0xFC, 0x01, 0x02, 0xFA, 0xFA]);
        let mut without_dave = Rig::new(tuning());
        without_dave.map(ALICE_SSRC, ALICE);
        let unencrypted = without_dave.open(options(ReceiveFormat::Opus));
        let media = Arc::clone(&without_dave.media);
        let busy = media.dave.lock();
        without_dave.send(ALICE_SSRC, 0, &plain_ending_in_the_marker);
        without_dave.step();
        drop(busy);
        let while_busy = data(&read(&unencrypted));

        let mut rig = Rig::new(ReceiveTuning {
            dave_budget: Duration::ZERO,
            ..tuning()
        });
        let mut call = rig.group(&[ALICE]);
        rig.map(ALICE_SSRC, ALICE);
        rig.map(BOB_SSRC, BOB);
        let receiver = rig.open(options(ReceiveFormat::Packets));
        let frames: Vec<Vec<u8>> = opus(5, 0.3)
            .iter()
            .map(|packet| call.speak(user(ALICE), packet))
            .collect();
        let order = |items: Vec<Item>| -> Vec<(u64, u16)> {
            items
                .into_iter()
                .filter_map(|item| match item {
                    Item::Packet { user, sequence, .. } => Some((u64::from(user), sequence)),
                    _ => None,
                })
                .collect()
        };
        let media = Arc::clone(&rig.media);
        let busy = media.dave.lock();
        for (ssrc, sequence, frame) in [
            (ALICE_SSRC, 0, &frames[0][..]),
            (ALICE_SSRC, 1, &frames[1][..]),
            (ALICE_SSRC, 2, &SILENCE_FRAME[..]),
            (BOB_SSRC, 0, &SILENCE_FRAME[..]),
        ] {
            rig.send(ssrc, sequence, frame);
        }
        rig.step();
        drop(busy);
        let started = rig.speech();
        let mut ticks = vec![order(read(&receiver))];
        rig.send(ALICE_SSRC, 3, &frames[2]);
        for _ in 0..3 {
            rig.now += FRAME;
            rig.run(0, Instant::now());
            ticks.push(order(read(&receiver)));
        }
        rig.send(ALICE_SSRC, 4, &frames[3]);
        rig.send(ALICE_SSRC, 5, &frames[4]);
        rig.step();
        ticks.push(order(read(&receiver)));

        assert_eq!(while_busy, [plain_ending_in_the_marker]);
        assert_eq!(started, [(ALICE, true, Duration::ZERO)]);
        assert_eq!(
            ticks,
            [
                vec![(BOB, 0)],
                vec![(ALICE, 0)],
                vec![(ALICE, 1), (ALICE, 2)],
                vec![(ALICE, 3)],
                vec![(ALICE, 4), (ALICE, 5)]
            ]
        );
        assert_eq!(
            ["decrypt_busy", "undecryptable"].map(|name| rig.counter(name)),
            [1, 0]
        );
    }

    #[test]
    fn the_dave_hold_drops_its_oldest_frames_beyond_its_bounds() {
        let packets = opus(3, 0.3);
        let per_user = ReceiveTuning {
            hold_packets: 2,
            ..tuning()
        };
        let in_total = ReceiveTuning {
            arrivals: 3,
            ..tuning()
        };
        let rows = [
            (
                "per user",
                per_user,
                vec![vec![(BOB, 0), (ALICE, 0), (ALICE, 1), (ALICE, 2)]],
                vec![(BOB, 0), (ALICE, 1), (ALICE, 2)],
            ),
            (
                "in total",
                in_total,
                vec![vec![(BOB, 0), (ALICE, 0), (ALICE, 1)], vec![(ALICE, 2)]],
                vec![(ALICE, 0), (ALICE, 1), (ALICE, 2)],
            ),
        ];
        for (bound, tuning, ticks, kept) in rows {
            let mut rig = Rig::new(tuning);
            let mut call = rig.group(&[ALICE]);
            rig.map(ALICE_SSRC, ALICE);
            rig.map(BOB_SSRC, BOB);
            let receiver = rig.open(options(ReceiveFormat::Opus));
            join_behind_the_bot(&mut call, BOB);
            for tick in ticks {
                for (speaker, sequence) in tick {
                    let ssrc = if speaker == ALICE {
                        ALICE_SSRC
                    } else {
                        BOB_SSRC
                    };
                    let frame = call.speak(user(speaker), &packets[usize::from(sequence)]);
                    rig.send(ssrc, sequence, &frame);
                }
                rig.step();
            }
            let dropped = rig.counter("undecryptable");
            rig.deliver(&mut call);
            rig.step();

            let items = read(&receiver);
            let mut heard: Vec<(Option<u64>, Bytes)> =
                senders(&items).into_iter().zip(data(&items)).collect();
            heard.sort();
            let mut expected: Vec<(Option<u64>, Bytes)> = kept
                .into_iter()
                .map(|(speaker, sequence): (u64, u16)| {
                    (Some(speaker), packets[usize::from(sequence)].clone())
                })
                .collect();
            expected.sort();
            assert_eq!(heard, expected, "{bound}");
            assert_eq!(dropped, 1, "{bound}");
        }
    }

    #[test]
    fn a_failure_streak_requests_recoveries_an_interval_apart_and_stops_after_two_per_user() {
        let tolerance = ReceiveTuning::default().decrypt_tolerance;
        let interval = ReceiveTuning::default().recovery_interval;
        for case in ["garbage", "plaintext", "unknown epoch"] {
            let mut rig = Rig::dave();
            let peers: &[u64] = if case == "garbage" {
                &[ALICE, BOB]
            } else {
                &[ALICE]
            };
            let mut call = rig.group(peers);
            rig.map(ALICE_SSRC, ALICE);
            rig.map(BOB_SSRC, BOB);
            let _receiver = rig.open(options(ReceiveFormat::Opus));
            let packet = opus(1, 0.3).remove(0);
            let first = call.speak(user(ALICE), &packet);
            rig.send(ALICE_SSRC, 0, &first);
            rig.step();
            if case == "unknown epoch" {
                join_behind_the_bot(&mut call, BOB);
            }
            let mut sequence = 1;
            let mut send = |rig: &mut Rig, speaker: u64, count: u32, decryptable: bool| {
                let ssrc = if speaker == ALICE {
                    ALICE_SSRC
                } else {
                    BOB_SSRC
                };
                for _ in 0..count {
                    let frame = if decryptable || case == "unknown epoch" {
                        call.speak(user(speaker), &packet)
                    } else if case == "plaintext" {
                        packet.to_vec()
                    } else {
                        garbage(sequence)
                    };
                    rig.send(ssrc, sequence, &frame);
                    sequence += 1;
                }
                rig.step();
                rig.recovered()
            };

            let short = send(&mut rig, ALICE, tolerance - 1, false);
            if case == "garbage" {
                assert!(!send(&mut rig, ALICE, 1, true));
                assert!(!send(&mut rig, ALICE, tolerance - 1, false));
            }
            let reached = send(&mut rig, ALICE, 1, false);
            if case == "plaintext" {
                assert_eq!(rig.counter("undecryptable"), u64::from(tolerance));
            }
            if case == "garbage" {
                assert!(!send(&mut rig, BOB, tolerance, false));
                assert!(!send(&mut rig, BOB, 1, true));
            }
            let within_the_interval = send(&mut rig, ALICE, tolerance, false);
            assert_eq!(
                (short, reached, within_the_interval),
                (false, true, false),
                "{case}"
            );
            if case == "garbage" {
                rig.now += interval;
                let transition = |rig: &Rig, op| {
                    rig.media
                        .with_dave(|dave| dave.handle(op, rig.now, &mut Vec::new()))
                        .unwrap();
                };
                transition(
                    &rig,
                    DaveOp::PrepareTransition {
                        transition_id: 9,
                        protocol_version: 1,
                    },
                );
                let while_settling = send(&mut rig, ALICE, 1, false);
                transition(&rig, DaveOp::ExecuteTransition { transition_id: 9 });
                let second = send(&mut rig, ALICE, 1, false);
                rig.now += interval;
                let third = send(&mut rig, ALICE, tolerance, false);
                let bob = send(&mut rig, BOB, tolerance, false);
                rig.now += interval;
                let after_bob_went_quiet = send(&mut rig, ALICE, 1, false);
                assert_eq!(
                    (while_settling, second, third, bob, after_bob_went_quiet),
                    (false, true, false, true, false)
                );
            }
        }

        for case in [
            "empty or unparseable",
            "no decryptor",
            "protocol version 0",
            "settling",
            "no group yet",
        ] {
            let mut rig = if case == "settling" {
                Rig::new(tuning())
            } else {
                Rig::dave()
            };
            let mut corrupted = Vec::new();
            if case != "no group yet" {
                let mut call = rig.group(&[ALICE]);
                let packet = &opus(1, 0.3)[0];
                corrupted = (0..tolerance * 2)
                    .map(|_| {
                        let mut frame = call.speak(user(ALICE), packet);
                        frame[1] ^= 0xFF;
                        frame
                    })
                    .collect();
                if case == "protocol version 0" {
                    let downgrade = call.fake.downgrade();
                    call.enqueue(downgrade);
                    call.run();
                    rig.deliver(&mut call);
                    assert_eq!(rig.media.protocol_version(), 0);
                }
            }
            let speaker = if case == "no decryptor" {
                STRANGER
            } else {
                ALICE
            };
            rig.map(ALICE_SSRC, speaker);
            let _receiver = rig.open(only(&[speaker], ReceiveFormat::Opus));
            for sequence in 0..u16::try_from(tolerance * 2).unwrap() {
                let frame = match case {
                    "empty or unparseable" if sequence % 2 == 0 => Vec::new(),
                    "empty or unparseable" => vec![0x03],
                    "no group yet" => garbage(sequence),
                    _ => corrupted[usize::from(sequence)].clone(),
                };
                rig.send(ALICE_SSRC, sequence, &frame);
            }
            rig.step();
            assert!(!rig.recovered(), "{case}");
            assert!(rig.user_counters(speaker).packets > 0, "{case}");
            let dropped_at_once = match case {
                "empty or unparseable" => ("invalid_payloads", tolerance * 2),
                "protocol version 0" => ("undecryptable", tolerance * 2),
                _ => ("invalid_payloads", 0),
            };
            assert_eq!(
                rig.counter(dropped_at_once.0),
                u64::from(dropped_at_once.1),
                "{case}"
            );
        }
    }

    #[test]
    fn lost_packets_become_lost_frames_and_longer_gaps_are_skipped() {
        for format in [ReceiveFormat::Opus, pcm(16_000, 1, SampleFormat::S16Le)] {
            let mut rig = Rig::new(tuning());
            rig.map(ALICE_SSRC, ALICE);
            let receiver = rig.open(options(format));
            let packets = opus(12, 0.3);
            for (sequence, packet) in (0..).zip(&packets) {
                if [0, 1, 3, 10, 11].contains(&sequence) {
                    rig.send(ALICE_SSRC, sequence, packet);
                }
                rig.step();
            }

            let items = read(&receiver);
            let played: Vec<(Option<u32>, bool)> = heard(&items)
                .into_iter()
                .map(|item| match item {
                    Heard::Frame {
                        timestamp, lost, ..
                    } => (timestamp, lost),
                    other => panic!("{other:?}"),
                })
                .collect();
            let lengths: Vec<usize> = data(&items).iter().map(Bytes::len).collect();
            assert_eq!(
                played,
                [
                    (Some(0), false),
                    (Some(960), false),
                    (Some(1_920), true),
                    (Some(2_880), false),
                    (Some(9_600), false),
                    (Some(10_560), false),
                ],
                "{format:?}"
            );
            assert_eq!(receiver.stats().unwrap()[2], ("lost", 7), "{format:?}");
            match format {
                ReceiveFormat::Opus => assert_eq!(lengths[2], 0),
                _ => {
                    assert!(lengths.iter().all(|&length| length == 640));
                    let mut fresh = Decoder::new(16_000, 1).unwrap();
                    let mut scratch = Vec::new();
                    let samples = fresh.decode(&packets[10], &mut scratch).unwrap();
                    let mut after_the_skip = BytesMut::new();
                    codec::write_samples(
                        &scratch[..samples],
                        SampleFormat::S16Le,
                        &mut after_the_skip,
                    );
                    assert_eq!(data(&items)[4], after_the_skip.freeze());
                }
            }
        }
    }

    #[test]
    fn each_user_counts_packets_losses_duplicates_and_invalid_payloads() {
        let mut rig = Rig::new(tuning());
        rig.map(ALICE_SSRC, ALICE);
        let _receiver = rig.open(options(ReceiveFormat::Opus));
        let packets = opus(4, 0.3);

        for (sequence, payload) in [
            (0, long(6)),
            (1, packets[1].to_vec()),
            (1, packets[1].to_vec()),
            (3, packets[3].to_vec()),
            (4, Vec::new()),
            (5, vec![0; 12]),
            (40_000, packets[2].to_vec()),
        ] {
            rig.send(ALICE_SSRC, sequence, &payload);
        }
        rig.step();

        assert_eq!(
            rig.user_counters(ALICE),
            UserCounters {
                packets: 7,
                lost: 1,
                duplicates: 1,
                decrypt_failures: 0,
                invalid_payloads: 3,
            }
        );
        assert_eq!(
            ["duplicates", "invalid_payloads", "late_packets"].map(|name| rig.counter(name)),
            [1, 3, 1]
        );
    }

    #[test]
    fn the_intake_opens_with_this_sessions_key_drops_replays_and_echoes_and_skips_the_unheard() {
        let mut rig = Rig::new(tuning());
        let cipher = Cipher::new(Mode::Aes256GcmRtpSize, &KEY);
        let stranger = Cipher::new(Mode::Aes256GcmRtpSize, &[1; 32]);
        let other_socket = Arc::new(UdpSocket::bind("127.0.0.1:0").unwrap());
        let mut stale = Intake::new(
            Arc::clone(&rig.media),
            Arc::clone(&rig.info),
            other_socket,
            64,
        );
        let mut intake = rig.intake();
        let tagged = Sealing {
            extension: Some(vec![0x10, 0xAA]),
            ..Sealing::default()
        };
        let packet = |cipher: &Cipher, nonce, ssrc, sequence, sealing: &Sealing| {
            sfu::seal(cipher, nonce, ssrc, sequence, 0, &SILENCE_FRAME, sealing)
        };

        stale.rtp(&mut packet(&cipher, 0, ALICE_SSRC, 0, &tagged));
        intake.rtp(&mut packet(&cipher, 1, ALICE_SSRC, 1, &tagged));
        rig.media.set_speech_activity(false);
        intake.rtp(&mut packet(&cipher, 2, ALICE_SSRC, 2, &tagged));
        intake.rtp(&mut packet(&stranger, 5, ALICE_SSRC, 6, &tagged));
        let _packets = rig.open(options(ReceiveFormat::Packets));
        intake.rtp(&mut packet(&cipher, 2, ALICE_SSRC, 2, &tagged));
        intake.rtp(&mut packet(&cipher, 2, ALICE_SSRC, 3, &tagged));
        intake.rtp(&mut packet(&cipher, 3, BOT_SSRC, 4, &tagged));
        intake.rtp(&mut packet(&stranger, 4, ALICE_SSRC, 5, &tagged));
        rig.media.rtcp(&[0x80, 200, 0, 6]);
        rig.media.rtcp(&[0x80, 201, 0, 1]);

        let arrivals: Vec<Arrival> = rig.media.arrivals.lock().drain(..).collect();
        let shapes: Vec<(u16, bool, bool)> = arrivals
            .iter()
            .map(|arrival| (arrival.sequence, arrival.payload.is_some(), arrival.silence))
            .collect();
        let body = Bytes::from_static(&[0x10, 0xAA, 0, 0]);
        assert_eq!(shapes, [(1, false, true), (2, true, true)]);
        assert_eq!(arrivals[0].extension, None);
        assert_eq!(arrivals[1].extension, Some((rtp::ONE_BYTE_PROFILE, body)));
        assert_eq!(
            [
                "transport_failures",
                "replays",
                "own_echoes",
                "rtcp_sender_reports"
            ]
            .map(|name| rig.counter(name)),
            [2, 1, 1, 1]
        );
        assert_eq!(
            rig.media.counters.packets_received.load(Ordering::Relaxed),
            8
        );
    }

    #[test]
    fn the_intake_reads_the_sfus_shared_nonce_counter_little_endian() {
        let mut rig = Rig::new(tuning());
        let cipher = Cipher::new(Mode::Aes256GcmRtpSize, &KEY);
        let mut intake = rig.intake();
        let _packets = rig.open(options(ReceiveFormat::Packets));

        for (nonce, sequence) in [(2, 1), (256, 2)] {
            let sealing = Sealing::default();
            let mut packet = sfu::seal(
                &cipher,
                nonce,
                ALICE_SSRC,
                sequence,
                0,
                &SILENCE_FRAME,
                &sealing,
            );
            intake.rtp(&mut packet);
        }

        assert_eq!(rig.media.arrivals.lock().len(), 2);
        assert_eq!(rig.counter("replays"), 0);
    }

    #[test]
    fn the_intake_forgets_the_replay_windows_of_ssrcs_that_left_when_a_new_one_arrives() {
        let mut rig = Rig::new(tuning());
        let cipher = Cipher::new(Mode::Aes256GcmRtpSize, &KEY);
        let mut intake = rig.intake();
        let _packets = rig.open(options(ReceiveFormat::Packets));
        rig.map(ALICE_SSRC, ALICE);
        rig.map(BOB_SSRC, BOB);

        let mut send = |nonce, ssrc| {
            let sealing = Sealing::default();
            let mut packet = sfu::seal(&cipher, nonce, ssrc, 0, 0, &SILENCE_FRAME, &sealing);
            intake.rtp(&mut packet);
        };
        send(0, ALICE_SSRC);
        send(1, BOB_SSRC);
        rig.info.ssrcs.lock().remove(&ALICE_SSRC);
        rig.map(STRANGER_SSRC, STRANGER);
        send(2, STRANGER_SSRC);

        let mut windows: Vec<u32> = intake.windows.keys().copied().collect();
        windows.sort_unstable();
        assert_eq!(windows, [BOB_SSRC, STRANGER_SSRC]);
    }

    #[test]
    fn dropping_the_reception_closes_the_media_to_new_receivers_and_wakes_the_connection() {
        let rig = Rig::new(tuning());
        let media = Arc::clone(&rig.media);
        let inbox = Arc::clone(&rig.fixture.inbox);

        drop(rig);
        let late = Receiver::new(Handle::from(1), options(ReceiveFormat::Opus), inbox).unwrap();

        assert!(media.closed.load(Ordering::Acquire));
        assert!(media.lost.notified().now_or_never().is_some());
        assert!(!media.add_receiver(&Arc::new(late)));
    }

    #[test]
    fn each_packet_goes_to_the_owner_of_its_ssrc_at_arrival() {
        let mut rig = Rig::new(tuning());
        let receiver = rig.open(options(ReceiveFormat::Opus));
        let packets = opus(4, 0.3);
        let hold = rig.reception.tuning.hold;
        rig.map(ALICE_SSRC, ALICE);
        rig.send(ALICE_SSRC, 0, &packets[0]);
        rig.step();
        rig.info.ssrcs.lock().remove(&ALICE_SSRC);
        rig.step();
        rig.now += hold / 2;
        rig.send(ALICE_SSRC, 1, &packets[1]);
        rig.step();
        rig.now += hold;
        rig.send(ALICE_SSRC, 2, &packets[2]);
        rig.step();
        let after_op_13 = read(&receiver);

        rig.map(BOB_SSRC, ALICE);
        rig.step();
        rig.send(BOB_SSRC, 0, &packets[0]);
        rig.now += FRAME;
        rig.map(BOB_SSRC, BOB);
        rig.now += FRAME;
        rig.send(BOB_SSRC, 1, &packets[1]);
        rig.step();
        let after_a_new_owner = read(&receiver);
        rig.now += hold;
        rig.step();

        assert_eq!(senders(&after_op_13), [Some(ALICE), Some(ALICE)]);
        let mut owners = senders(&after_a_new_owner);
        owners.sort_unstable();
        assert_eq!(owners, [Some(ALICE), Some(BOB)]);
        assert_eq!(rig.counter("unmapped"), 1);
    }

    #[test]
    fn a_new_transport_resets_the_streams_holds_and_streaks_and_keeps_the_receivers() {
        let tolerance = ReceiveTuning::default().decrypt_tolerance;
        let mut rig = Rig::dave();
        let mut call = rig.group(&[ALICE]);
        rig.map(ALICE_SSRC, ALICE);
        let receiver = rig.open(options(ReceiveFormat::Opus));
        let packets = opus(4, 0.3);
        for (sequence, packet) in (100..).zip(&packets[..2]) {
            let frame = call.speak(user(ALICE), packet);
            rig.send(ALICE_SSRC, sequence, &frame);
        }
        let failing = 102..102 + u16::try_from(tolerance).unwrap() - 1;
        for sequence in failing.clone() {
            rig.send(ALICE_SSRC, sequence, &garbage(sequence));
        }
        rig.step();
        let unmapped = call.speak(user(ALICE), &packets[2]);
        let held = call.speak(user(ALICE), &packets[3]);
        rig.send(BOB_SSRC, 0, &unmapped);
        let media = Arc::clone(&rig.media);
        let busy = media.dave.lock();
        rig.send(ALICE_SSRC, failing.end, &held);
        rig.step();
        drop(busy);

        rig.install(2);
        rig.map(BOB_SSRC, ALICE);
        rig.send(ALICE_SSRC, 9_000, &garbage(9_000));
        rig.step();
        let recovered = rig.recovered();
        for (sequence, packet) in (9_001..).zip(&packets[2..]) {
            let frame = call.speak(user(ALICE), packet);
            rig.send(ALICE_SSRC, sequence, &frame);
        }
        rig.step();

        assert!(!recovered);
        assert_eq!(data(&read(&receiver)), packets);
        assert_eq!(rig.counter("late_packets"), 0);
        assert_eq!(receiver.ended(), None);
    }

    #[test]
    fn streams_and_users_unheard_for_the_stream_idle_are_forgotten() {
        let tolerance = ReceiveTuning::default().decrypt_tolerance;
        let tuning = ReceiveTuning {
            dave_grace: Duration::ZERO,
            stream_idle: Duration::from_secs(5),
            ..tuning()
        };
        let mut rig = Rig::new(tuning);
        let mut call = rig.group(&[ALICE]);
        rig.map(ALICE_SSRC, ALICE);
        rig.map(BOB_SSRC, BOB);
        rig.info.members.lock().insert(user(BOB));
        rig.media.others.store(1, Ordering::Relaxed);
        let listed = |rig: &Rig| {
            let mut users: Vec<u64> = rig
                .media
                .users
                .lock()
                .keys()
                .map(|&id| u64::from(id))
                .collect();
            users.sort_unstable();
            users
        };
        rig.send(ALICE_SSRC, 0, &SILENCE_FRAME);
        rig.send(BOB_SSRC, 0, &SILENCE_FRAME);
        rig.step();
        let heard = listed(&rig);
        rig.now += tuning.stream_idle;
        rig.step();
        let after_the_idle = listed(&rig);
        rig.info.members.lock().remove(&user(BOB));
        rig.media.others.store(0, Ordering::Relaxed);
        rig.step();
        let after_bob_left = listed(&rig);

        let receiver = rig.open(options(ReceiveFormat::Opus));
        let packets = opus(2, 0.3);
        rig.send(ALICE_SSRC, 1, &call.speak(user(ALICE), &packets[0]));
        for sequence in 2..1 + u16::try_from(tolerance).unwrap() {
            rig.send(ALICE_SSRC, sequence, &garbage(sequence));
        }
        rig.step();
        rig.now += tuning.stream_idle;
        rig.step();
        rig.send(ALICE_SSRC, 40_000, &garbage(40_000));
        rig.step();
        let recovered = rig.recovered();
        rig.send(ALICE_SSRC, 40_001, &call.speak(user(ALICE), &packets[1]));
        rig.step();

        assert_eq!(heard, [ALICE, BOB]);
        assert_eq!(after_the_idle, [BOB]);
        assert!(after_bob_left.is_empty());
        assert_eq!(data(&read(&receiver)), packets);
        assert!(!recovered);
        assert_eq!(rig.counter("late_packets"), 0);
    }

    #[test]
    fn a_mix_sums_users_soft_clips_and_continues_through_silence() {
        let mut rig = Rig::new(tuning());
        rig.map(ALICE_SSRC, ALICE);
        rig.map(BOB_SSRC, BOB);
        let receiver = rig.open(ReceiveOptions {
            mix: true,
            ..options(pcm(48_000, 2, SampleFormat::F32Le))
        });
        let (quiet, loud) = (opus(6, 0.2), opus(2, 0.9));
        let decoded = |packets: &[Bytes]| -> Vec<f32> {
            let mut decoder = Decoder::new(48_000, 2).unwrap();
            let mut pcm = Vec::new();
            for packet in packets {
                let mut scratch = Vec::new();
                let samples = decoder.decode(packet, &mut scratch).unwrap();
                pcm.extend_from_slice(&scratch[..samples * 2]);
            }
            pcm
        };
        let floats = |items: &[Item]| -> Vec<f32> {
            data(items)
                .iter()
                .flat_map(|frame| {
                    frame
                        .as_chunks::<4>()
                        .0
                        .iter()
                        .map(|bytes| f32::from_le_bytes(*bytes))
                        .collect::<Vec<_>>()
                })
                .collect()
        };

        for (sequence, packet) in (0..3).zip(&quiet) {
            rig.send(ALICE_SSRC, sequence, packet);
            rig.send(BOB_SSRC, sequence, packet);
            rig.step();
        }
        let summed = read(&receiver);
        for (sequence, packet) in (3..6).zip(&quiet[3..]) {
            rig.send(ALICE_SSRC, sequence, packet);
            rig.now += FRAME;
        }
        rig.run(2, rig.now + LATER);
        let late = read(&receiver);
        rig.now += FRAME;
        rig.run(0, rig.reception.epoch);
        let deferred = read(&receiver);
        rig.step();
        let caught_up = read(&receiver);
        rig.step();
        let silent = read(&receiver);
        for (sequence, packet) in (6..).zip(&loud) {
            rig.send(ALICE_SSRC, sequence, packet);
            rig.send(BOB_SSRC, sequence, packet);
            rig.step();
        }
        let clipped = read(&receiver);
        for (sequence, packet) in (8..).zip(&opus(10, 0.2)) {
            rig.send(ALICE_SSRC, sequence, packet);
        }
        rig.step();
        let dropped = receiver.stats().unwrap()[1];
        read(&receiver);
        rig.media.install(None);
        rig.step();
        let reconnecting = read(&receiver);
        rig.now += FRAME * 5;
        rig.install(2);
        rig.step();
        let returned = read(&receiver);
        rig.send(ALICE_SSRC, 100, &quiet[0]);
        rig.send(ALICE_SSRC, 102, &quiet[2]);
        for _ in 0..3 {
            rig.step();
        }
        let concealed: Vec<bool> = heard(&read(&receiver))
            .into_iter()
            .map(|item| matches!(item, Heard::Frame { lost: true, .. }))
            .collect();

        let expected: Vec<f32> = decoded(&quiet[..3])
            .iter()
            .map(|sample| sample * 2.0)
            .collect();
        assert_eq!(floats(&summed), expected);
        assert_eq!(
            (late.len(), deferred.len(), caught_up.len(), silent.len()),
            (3, 0, 2, 1)
        );
        assert!(floats(&silent).iter().all(|&sample| sample == 0.0));
        let all = [summed, late, caught_up, silent, clipped.clone()].concat();
        let offsets: Vec<u64> = all
            .iter()
            .filter_map(|item| match item {
                Item::Frame { offset, user, .. } => {
                    assert!(user.is_none());
                    Some(*offset)
                }
                _ => None,
            })
            .collect();
        let contiguous: Vec<u64> = (0..offsets.len() as u64)
            .map(|index| offsets[0] + index * 960)
            .collect();
        assert_eq!(offsets, contiguous);
        let loud_sum = decoded(&loud)
            .iter()
            .map(|sample| sample * 2.0)
            .fold(0.0, f32::max);
        assert!(loud_sum > 1.0, "{loud_sum}");
        assert!(floats(&clipped).iter().all(|sample| sample.abs() <= 1.0));
        assert_eq!(dropped, ("dropped", 5));
        assert!(reconnecting.is_empty());
        let resumed = heard(&returned);
        assert_eq!(resumed.len(), 1);
        let Heard::Frame { offset, .. } = resumed[0] else {
            panic!("{resumed:?}");
        };
        let before_the_gap = offsets.last().unwrap() + 960 * (1 + 1);
        assert!(offset >= before_the_gap + 960 * 5, "{offset}");
        assert_eq!(offset % 960, 0);
        assert_eq!(concealed, [false, true, false]);
        assert_eq!(rig.counter("receive_deferred"), 1);
    }

    #[test]
    fn in_band_markers_bracket_each_users_frames_on_the_receivers_timeline() {
        let formats = [pcm(16_000, 1, SampleFormat::S16Le), ReceiveFormat::Packets];
        let slips = [Duration::ZERO, Duration::from_millis(50)];
        for (format, slip) in formats.into_iter().flat_map(|f| slips.map(|s| (f, s))) {
            let mut rig = Rig::new(tuning());
            rig.media.set_speech_hangover(Duration::from_millis(100));
            rig.media.set_speech_activity(false);
            rig.map(ALICE_SSRC, ALICE);
            let receiver = rig.open(ReceiveOptions {
                markers: true,
                jitter: Duration::from_millis(200),
                ..options(format)
            });
            let packets = opus(9, 0.3);
            for (sequence, packet) in (0..).zip(&packets) {
                rig.now += slip;
                rig.send(ALICE_SSRC, sequence, packet);
                rig.step();
            }
            for _ in 0..20 {
                rig.step();
            }

            let items = read(&receiver);
            let heard = heard(&items);
            assert!(rig.speech().is_empty(), "{format:?} {slip:?}");
            assert_eq!(heard.len(), 11, "{format:?} {slip:?}");
            if format == ReceiveFormat::Packets {
                assert_eq!(
                    heard[0],
                    Heard::Speech {
                        speaking: true,
                        offset: None
                    }
                );
                assert_eq!(
                    heard[10],
                    Heard::Speech {
                        speaking: false,
                        offset: None
                    }
                );
                assert!(
                    heard[1..10]
                        .iter()
                        .all(|item| matches!(item, Heard::Packet { .. }))
                );
            } else {
                let offsets: Vec<u64> = heard[1..10]
                    .iter()
                    .map(|item| match item {
                        Heard::Frame { offset, .. } => *offset,
                        other => panic!("{other:?}"),
                    })
                    .collect();
                assert_eq!(
                    heard[0],
                    Heard::Speech {
                        speaking: true,
                        offset: Some(offsets[0])
                    }
                );
                assert_eq!(
                    heard[10],
                    Heard::Speech {
                        speaking: false,
                        offset: Some(offsets[8] + 960)
                    }
                );
                assert!(offsets.windows(2).all(|pair| pair[1] == pair[0] + 960));
                assert!(data(&items).iter().all(|frame| frame.len() == 640));
            }
        }
    }

    #[test]
    fn a_packets_receiver_gets_decrypted_packets_in_arrival_order_with_extensions() {
        let mut rig = Rig::dave();
        let mut call = rig.group(&[ALICE]);
        rig.map(ALICE_SSRC, ALICE);
        let packets = opus(7, 0.3);
        let frames: Vec<Vec<u8>> = packets
            .iter()
            .map(|packet| call.speak(user(ALICE), packet))
            .collect();
        let _earlier = rig.open(options(ReceiveFormat::Opus));
        rig.media.arrive(Arrival {
            at: rig.reception.epoch,
            ..rig.arrival(ALICE_SSRC, 3, &frames[3])
        });
        let receiver = rig.open(only(&[ALICE], ReceiveFormat::Packets));
        let extension = |body: &[u8]| Some((rtp::ONE_BYTE_PROFILE, Bytes::copy_from_slice(body)));
        for (sequence, body) in [
            (2, extension(&[0x10, 0xAA, 0, 0])),
            (0, extension(&[0x13, 0xAA, 0, 0])),
            (1, None),
        ] {
            let arrival = Arrival {
                extension: body,
                ..rig.arrival(ALICE_SSRC, sequence, &frames[usize::from(sequence)])
            };
            rig.media.arrive(arrival);
        }
        rig.step();
        rig.send(ALICE_SSRC, 100, &frames[4]);
        rig.step();
        rig.send(ALICE_SSRC, 30, &frames[5]);
        rig.send(ALICE_SSRC, 20_000, &frames[6]);
        rig.step();

        let packet = |sequence: u16, index: usize, extensions: Vec<(u8, Bytes)>| Heard::Packet {
            sequence,
            opus: packets[index].clone(),
            extensions,
        };
        assert_eq!(
            heard(&read(&receiver)),
            [
                packet(2, 2, vec![(1, Bytes::from_static(&[0xAA]))]),
                packet(0, 0, Vec::new()),
                packet(1, 1, Vec::new()),
                packet(100, 4, Vec::new()),
                packet(30, 5, Vec::new()),
                packet(20_000, 6, Vec::new()),
            ]
        );
        assert_eq!(rig.counter("invalid_extensions"), 1);
        assert_eq!(rig.counter("late_packets"), 2);
    }

    #[test]
    fn release_and_decode_stop_at_the_deadline_and_resume_next_tick() {
        let mut rig = Rig::new(tuning());
        rig.map(ALICE_SSRC, ALICE);
        let receiver = rig.open(options(pcm(48_000, 2, SampleFormat::S16Le)));
        for (sequence, packet) in (0..).zip(&opus(3, 0.3)) {
            rig.send(ALICE_SSRC, sequence, packet);
        }

        rig.run(0, rig.reception.epoch);
        let deferred = read(&receiver);
        let counted = rig.counter("receive_deferred");
        rig.step();

        assert!(deferred.is_empty());
        assert_eq!(counted, 1);
        assert_eq!(senders(&read(&receiver)), [Some(ALICE); 3]);
        assert_eq!(rig.counter("receive_deferred"), 1);
    }

    #[test]
    fn the_watchdog_reports_media_that_arrives_but_never_plays() {
        for cause in ["undecryptable frames", "a stale key"] {
            let mut rig = Rig::dave();
            rig.map(ALICE_SSRC, ALICE);
            let receiver = rig.open(only(&[ALICE], ReceiveFormat::Opus));
            let stale = Cipher::new(Mode::Aes256GcmRtpSize, &[1; 32]);
            let mut intake = rig.intake();
            let mut stalls = Vec::new();
            for sequence in 0..7 {
                if cause == "a stale key" {
                    let nonce = u32::from(sequence);
                    let sealing = Sealing::default();
                    let mut packet = sfu::seal(
                        &stale,
                        nonce,
                        ALICE_SSRC,
                        sequence,
                        0,
                        &SILENCE_FRAME,
                        &sealing,
                    );
                    intake.rtp(&mut packet);
                } else {
                    rig.send(ALICE_SSRC, sequence, &garbage(sequence));
                }
                rig.step();
                stalls.extend(rig.stalls());
            }
            rig.send(ALICE_SSRC, 7, &SILENCE_FRAME);
            rig.step();

            assert_eq!(stalls, [(Direction::Receive, true, STALL)], "{cause}");
            assert_eq!(
                rig.stalls(),
                [(Direction::Receive, false, STALL + FRAME * 2)],
                "{cause}"
            );
            assert_eq!(read(&receiver).len(), 1, "{cause}");
        }
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(48))]
        #[test]
        fn sealed_packets_with_arbitrary_fields_never_panic_the_receive_path(
            packets in proptest::collection::vec(
                (
                    0..3_usize,
                    any::<u16>(),
                    any::<u32>(),
                    proptest::option::of(proptest::collection::vec(any::<u8>(), 0..12)),
                    0..4_u8,
                    any::<bool>(),
                    proptest::collection::vec(any::<u8>(), 0..48),
                ),
                1..48,
            ),
        ) {
            let mut rig = Rig::new(tuning());
            for (ssrc, id) in [(ALICE_SSRC, ALICE), (BOB_SSRC, BOB)] {
                rig.map(ssrc, id);
            }
            let markers = ReceiveOptions { markers: true, ..options(pcm(16_000, 1, SampleFormat::S16Le)) };
            let receivers = [
                rig.open(markers),
                rig.open(options(ReceiveFormat::Opus)),
                rig.open(ReceiveOptions { markers: true, ..options(ReceiveFormat::Packets) }),
                rig.open(ReceiveOptions { mix: true, ..options(pcm(48_000, 2, SampleFormat::F32Le)) }),
            ];
            let valid = opus(4, 0.5);
            let cipher = Cipher::new(Mode::Aes256GcmRtpSize, &KEY);
            let mut intake = rig.intake();
            let ssrcs = [ALICE_SSRC, BOB_SSRC, 0xFFFF_FFFF];
            for (nonce, (which, sequence, timestamp, extension, padding, real, payload)) in (0..).zip(packets) {
                let payload = if real { valid[usize::from(sequence) % valid.len()].to_vec() } else { payload };
                let sealing = Sealing { extension, padding, csrcs: padding, marker: real };
                let mut packet = sfu::seal(&cipher, nonce, ssrcs[which], sequence, timestamp, &payload, &sealing);
                intake.rtp(&mut packet);
                if nonce % 4 == 0 {
                    let now = Instant::now();
                    let tick = Tick { now, skipped: nonce % 3 };
                    prop_assert!(rig.reception.tick(tick, now + FRAME));
                }
            }
            for _ in 0..3 {
                let now = Instant::now();
                let tick = Tick { now, skipped: 0 };
                prop_assert!(rig.reception.tick(tick, now + FRAME));
            }
            prop_assert_eq!(rig.counter("rx_panics"), 0);
            for receiver in &receivers {
                prop_assert!(receiver.read(256).is_ok());
            }
        }
    }
}
