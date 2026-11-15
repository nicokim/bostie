#![deny(clippy::arithmetic_side_effects)]

mod decrypt;
mod intake;
mod output;
mod owners;

use std::any::Any;
use std::collections::{HashMap, VecDeque};
use std::mem;
use std::panic::{self, AssertUnwindSafe};
use std::sync::Arc;
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use bytes::Bytes;
use tracing::{debug, error};

use self::decrypt::Hold;
pub(crate) use self::intake::Intake;
use self::output::Subscribers;
use self::owners::Owners;
use crate::ids::{GuildId, UserId};
use crate::inbox::Delivery;
use crate::voice::codec;
use crate::voice::connection::Info;
use crate::voice::dave;
use crate::voice::media::{Arrival, Media, Stall};
use crate::voice::pacer::Tick;
use crate::voice::peak::Peak;
use crate::voice::playout::{Check, Slot, Stream, to_samples};
use crate::voice::speech::{self, Activity, Edge};
use crate::voice::types::{
    Direction, FRAME_48K, ReceiveTuning, SAMPLE_RATE, SILENCE_FRAME, Speaking, duration_of,
};
use crate::voice::{Throttle, panic_message};

const MEDIA_RECENT: Duration = Duration::from_millis(100);

type Key = (u32, UserId);

#[derive(Clone)]
struct Core {
    media: Arc<Media>,
    info: Arc<Info>,
    user: UserId,
    tuning: ReceiveTuning,
    epoch: Instant,
}

#[derive(Clone, Copy)]
struct Moment {
    now: Instant,
    now48: i64,
    hangover: i64,
    deadline: Instant,
    cut: Instant,
    skipped: u32,
}

struct Work {
    user: UserId,
    arrival: Arrival,
    check: Check,
    plain: Option<Bytes>,
    fresh: bool,
}

struct Feed {
    id: u64,
    own: bool,
    stream: Stream,
    created: i64,
}

#[derive(Default)]
struct Inflow {
    arrivals: VecDeque<Arrival>,
    batch: Vec<(UserId, Arrival)>,
    peak: Peak,
}

#[derive(Default)]
struct Streams {
    feeds: HashMap<Key, Feed>,
    next: u64,
    peak: Peak,
}

struct Speech {
    users: HashMap<UserId, Activity>,
    enabled: bool,
}

#[derive(Default)]
struct Presence {
    heard_at: HashMap<UserId, i64>,
    forget_at: Option<i64>,
    members: usize,
}

#[derive(Debug, Default)]
struct Watchdog {
    failures: u64,
    media: Option<Instant>,
    stall: Stall,
    arrived: bool,
    heard: bool,
}

pub(crate) struct Reception {
    core: Core,
    state: Option<Box<State>>,
    panicked: Throttle,
}

struct State {
    core: Core,
    inflow: Inflow,
    owners: Owners,
    hold: Hold,
    streams: Streams,
    subscribers: Subscribers,
    speech: Speech,
    presence: Presence,
    watchdog: Watchdog,
    departed: Vec<(UserId, Instant)>,
    generation: Option<u64>,
}

impl Reception {
    pub(crate) fn new(
        media: Arc<Media>,
        info: Arc<Info>,
        user: UserId,
        tuning: ReceiveTuning,
    ) -> Self {
        Self {
            core: Core {
                media,
                info,
                user,
                tuning,
                epoch: Instant::now(),
            },
            state: None,
            panicked: Throttle::default(),
        }
    }

    pub(crate) fn tick(&mut self, tick: Tick, deadline: Instant) -> bool {
        self.run(Instant::now(), tick.skipped, deadline)
    }

    fn run(&mut self, now: Instant, skipped: u32, deadline: Instant) -> bool {
        let media = &self.core.media;
        if self.state.is_none() && media.open_receivers() == 0 && media.arrivals.lock().is_empty() {
            return !media.closed.load(Ordering::Acquire);
        }
        let state = self
            .state
            .get_or_insert_with(|| Box::new(State::new(&self.core)));
        match panic::catch_unwind(AssertUnwindSafe(|| state.run(now, skipped, deadline))) {
            Ok(alive) => alive,
            Err(payload) => self.restart(now, payload.as_ref()),
        }
    }

    fn restart(&mut self, now: Instant, payload: &(dyn Any + Send)) -> bool {
        if self.panicked.allows(now) {
            error!(
                panic = panic_message(payload),
                "the voice receive phase panicked and starts over"
            );
        }
        if let Some(mut state) = self.state.take() {
            state.abandon(now);
        }
        !self.core.media.closed.load(Ordering::Acquire)
    }
}

impl Drop for Reception {
    fn drop(&mut self) {
        self.core.media.closed.store(true, Ordering::Release);
        self.core.media.lost.notify_one();
    }
}

impl State {
    fn new(core: &Core) -> Self {
        Self {
            core: core.clone(),
            inflow: Inflow::default(),
            owners: Owners::default(),
            hold: Hold::default(),
            streams: Streams::default(),
            subscribers: Subscribers::default(),
            speech: Speech {
                users: HashMap::new(),
                enabled: true,
            },
            presence: Presence::default(),
            watchdog: Watchdog::default(),
            departed: Vec::new(),
            generation: None,
        }
    }

    fn run(&mut self, now: Instant, skipped: u32, deadline: Instant) -> bool {
        if self.core.media.closed.load(Ordering::Acquire) {
            return false;
        }
        self.subscribe();
        let now48 = samples_since(self.core.epoch, now);
        let cut = self.core.media.take_arrivals(&mut self.inflow.arrivals);
        self.inflow.peak.observe(self.inflow.arrivals.len());
        self.core.media.take_departures(&mut self.departed);
        for (user, at) in self.departed.drain(..) {
            self.subscribers
                .depart(user, samples_since(self.core.epoch, at));
        }
        if self.inflow.arrivals.is_empty() && self.is_idle(now48) {
            self.shrink_buffers();
            return true;
        }
        let at = Moment {
            now,
            now48,
            hangover: to_samples(self.core.media.speech_hangover()),
            deadline,
            cut,
            skipped,
        };
        self.speech.enabled = self.core.media.speech_activity();
        let transport = self.core.media.transport.lock().clone();
        if let Some(transport) = &transport {
            self.follow(transport.generation);
        }
        self.watchdog.arrived = false;
        self.watchdog.heard = false;
        let Inflow {
            arrivals,
            batch,
            peak,
        } = &mut self.inflow;
        self.owners.map(&self.core, now, arrivals, batch);
        peak.observe(batch.len());
        self.receive(at);
        if self.speech.enabled {
            self.expire_speech(now48, at.hangover);
        } else {
            self.expire_speech(i64::MAX, 0);
        }
        let heard = self.subscribers.release(
            &mut self.streams.feeds,
            &self.core,
            at,
            transport.is_some(),
            &self.owners,
            &self.hold,
        );
        self.watchdog.heard |= heard;
        let listening = !self.subscribers.is_empty();
        self.watchdog.watch(&self.core.media, now, listening);
        self.prune(now, now48);
        self.hold.warn_undecryptable(now, self.core.media.guild());
        self.shrink_buffers();
        true
    }

    fn abandon(&mut self, now: Instant) {
        self.expire_speech(i64::MAX, 0);
        self.watchdog.resume(&self.core.media, now);
    }

    fn is_idle(&self, now48: i64) -> bool {
        self.subscribers.is_empty()
            && self.speech.users.is_empty()
            && !self.owners.has_parked()
            && self.hold.is_empty()
            && self.streams.feeds.is_empty()
            && !self.watchdog.stall.reported()
            && self.presence.forget_at.is_none_or(|at| now48 < at)
            && self.core.media.others.load(Ordering::Relaxed) == self.presence.members
    }

    fn subscribe(&mut self) {
        self.subscribers.sync(&self.core.media, self.core.epoch);
        if self.subscribers.is_empty() {
            self.streams.feeds.clear();
            self.hold.unsubscribed();
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
            self.streams.feeds.clear();
            self.owners.clear();
            self.hold.new_session();
            self.subscribers.reset();
        }
    }

    fn receive(&mut self, at: Moment) {
        let (protocol_version, session_built) = self.core.media.receive_state();
        let all_plain = protocol_version == 0 && !session_built;
        let mut batch = mem::take(&mut self.inflow.batch);
        let mut work = self.hold.take_work();
        for (user, arrival) in batch.drain(..) {
            self.admit(
                user,
                arrival,
                protocol_version,
                all_plain,
                at.now48,
                &mut work,
            );
        }
        self.inflow.batch = batch;
        let cut = self
            .hold
            .decrypt(&mut work, &self.core, at.now, at.deadline);
        let mut retried = self.hold.take_retried();
        for item in retried.drain(..) {
            self.accept(item, at.hangover);
        }
        for (index, item) in work.drain(..).enumerate() {
            if self.hold.defers(&item, index >= cut) {
                self.wait(item);
            } else {
                self.accept(item, at.hangover);
            }
        }
        self.hold.settle(work, retried);
        self.subscribers
            .expire_packet_markers(self.core.epoch, at.now48, at.hangover);
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
            self.presence.heard_at.insert(user, now48);
        }
        let wanted = self.subscribers.wanted(user, own);
        let Some(payload) = arrival.payload.as_ref().filter(|_| wanted) else {
            if !own {
                let at48 = samples_since(self.core.epoch, arrival.at);
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
        self.watchdog.arrived = true;
        let key = (arrival.ssrc, user);
        let pending = self.hold.is_pending(key, arrival.sequence);
        let feed = self.streams.feed(key, own, now48);
        let check = if pending {
            Check::Duplicate
        } else {
            feed.stream.check(arrival.sequence)
        };
        if check == Check::Duplicate {
            return;
        }
        self.hold.admit(key, arrival.sequence);
        let refused = payload.is_empty()
            || (!own
                && protocol_version > 0
                && !dave::has_marker(payload)
                && codec::packet_samples(payload).is_none());
        if refused {
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

    fn wait(&mut self, item: Work) {
        let samples = item.plain.as_deref().and_then(codec::received_samples);
        self.hear(&item, samples);
        self.hold.wait(item, self.core.tuning.arrivals);
    }

    fn hear(&mut self, item: &Work, samples: Option<u32>) {
        if !item.fresh || item.arrival.own {
            return;
        }
        let at48 = samples_since(self.core.epoch, item.arrival.at);
        let ssrc = item.arrival.ssrc;
        if let Some(plain) = &item.plain {
            let duration = samples.unwrap_or(FRAME_48K);
            self.speak(item.user, ssrc, at48, plain, false, duration);
        } else {
            let payload = item.arrival.payload.as_deref().unwrap_or_default();
            self.speak(item.user, ssrc, at48, payload, false, FRAME_48K);
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
        let at48 = samples_since(self.core.epoch, arrival.at);
        let Some(plain) = plain else {
            return;
        };
        let Some(samples) = samples else {
            return;
        };
        let Some(feed) = self.streams.feeds.get_mut(&(arrival.ssrc, user)) else {
            return;
        };
        let slot = Slot {
            sequence: arrival.sequence,
            timestamp: arrival.timestamp,
            samples,
            at: at48,
            payload: Some(plain.clone()),
        };
        if feed.stream.insert(slot, check) && !self.subscribers.plays(user, feed.own) {
            feed.stream.release_before(arrival.sequence.wrapping_add(1));
        }
        self.watchdog.heard |=
            self.subscribers
                .deliver(self.core.epoch, user, &arrival, &plain, samples, hangover);
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
        if !self.speech.enabled {
            return;
        }
        let activity = self.speech.users.entry(user).or_default();
        let speaking = speech::is_speech(payload, silence);
        if let Some(edge) = activity.packet(ssrc, at48, samples, speaking) {
            let media = &self.core.media;
            media.push(speech_delivery(media.guild(), self.core.epoch, user, edge));
        }
    }

    fn expire_speech(&mut self, now48: i64, hangover: i64) {
        let media = &self.core.media;
        for (&user, activity) in &mut self.speech.users {
            if let Some(edge) = activity.expire(now48, hangover) {
                media.push(speech_delivery(media.guild(), self.core.epoch, user, edge));
            }
        }
        self.speech
            .users
            .retain(|_, activity| activity.is_speaking());
    }

    fn prune(&mut self, now: Instant, now48: i64) {
        let idle = to_samples(self.core.tuning.stream_idle);
        self.streams.feeds.retain(|_, feed| {
            let last = feed.stream.last_packet().unwrap_or(feed.created);
            now48.saturating_sub(last) < idle
        });
        let hold = self.core.tuning.hold;
        self.owners.prune(now, hold);
        self.hold.expire(now, hold);
        let presence = &mut self.presence;
        presence
            .heard_at
            .retain(|_, last| now48.saturating_sub(*last) < idle);
        presence.forget_at = presence
            .heard_at
            .values()
            .map(|last| last.saturating_add(idle))
            .min();
        let members = self.core.info.members.lock();
        let heard_at = &presence.heard_at;
        self.hold
            .forget_streaks(|user| heard_at.contains_key(user) || members.contains(user));
        presence.members = members.len();
    }

    fn shrink_buffers(&mut self) {
        let Inflow {
            arrivals,
            batch,
            peak,
        } = &mut self.inflow;
        peak.shrink(arrivals.capacity(), |to| arrivals.shrink_to(to));
        peak.shrink(batch.capacity(), |to| batch.shrink_to(to));
        peak.advance();
        self.hold.shrink();
        let Streams { feeds, peak, .. } = &mut self.streams;
        peak.observe(feeds.len());
        peak.shrink(feeds.capacity(), |to| feeds.shrink_to(to));
        peak.advance();
    }
}

impl Streams {
    fn feed(&mut self, key: Key, own: bool, now48: i64) -> &mut Feed {
        let next = &mut self.next;
        self.feeds.entry(key).or_insert_with(|| {
            let id = *next;
            *next = id.wrapping_add(1);
            Feed {
                id,
                own,
                stream: Stream::new(key.1),
                created: now48,
            }
        })
    }
}

impl Watchdog {
    fn watch(&mut self, media: &Media, now: Instant, listening: bool) {
        let failures = media.transport_failures();
        let failing = mem::replace(&mut self.failures, failures) != failures;
        if !listening {
            self.resume(media, now);
            return;
        }
        if self.arrived || failing {
            self.media = Some(now);
        }
        if self.heard {
            self.resume(media, now);
            return;
        }
        let reaching = self
            .media
            .is_some_and(|at| now.saturating_duration_since(at) <= MEDIA_RECENT);
        if !reaching {
            self.stall.ease();
            return;
        }
        if let Some(stalled) = self.stall.press(now, media.stall_after()) {
            media.stalled(Direction::Receive, true, stalled);
        }
    }

    fn resume(&mut self, media: &Media, now: Instant) {
        if let Some(duration) = self.stall.resume(now) {
            media.stalled(Direction::Receive, false, duration);
        }
    }
}

fn samples_since(epoch: Instant, instant: Instant) -> i64 {
    to_samples(instant.saturating_duration_since(epoch))
}

fn span(samples: i64) -> Duration {
    duration_of(u64::try_from(samples).unwrap_or(0), SAMPLE_RATE)
}

fn instant(epoch: Instant, samples: i64) -> Instant {
    epoch.checked_add(span(samples)).unwrap_or(epoch)
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
        Edge::Stop { ssrc, at, duration } => {
            (ssrc, instant(epoch, at), Speaking::Stopped(span(duration)))
        }
    }
}

#[cfg(test)]
#[expect(clippy::arithmetic_side_effects)]
mod tests;
