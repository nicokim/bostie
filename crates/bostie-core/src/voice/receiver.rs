use std::collections::VecDeque;
use std::mem;
use std::sync::Arc;
use std::time::{Duration, Instant};

use bytes::Bytes;
use parking_lot::Mutex;

use crate::error::CoreError;
use crate::ids::{Handle, UserId};
use crate::inbox::{Delivery, Inbox};
use crate::runtime::Owner;
use crate::voice::peak::Peak;
use crate::voice::types::{Overflow, ReceiveOptions, Speaking};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Item {
    Frame {
        user: Option<UserId>,
        ssrc: Option<u32>,
        timestamp: Option<u32>,
        offset: u64,
        samples: u32,
        at: Instant,
        data: Bytes,
        lost: bool,
    },
    Packet {
        user: UserId,
        ssrc: u32,
        sequence: u16,
        timestamp: u32,
        at: Instant,
        opus: Bytes,
        extensions: Vec<(u8, Bytes)>,
    },
    Speech {
        user: UserId,
        ssrc: u32,
        speaking: Speaking,
        offset: Option<u64>,
        at: Instant,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Ended {
    Closed,
    Disconnected,
    Moved,
    Reconnecting,
}

impl Ended {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Closed => "closed",
            Self::Disconnected => "disconnected",
            Self::Moved => "moved",
            Self::Reconnecting => "reconnecting",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReceiveRead {
    Pending,
    Items(Vec<(Instant, Item)>),
    Ended(Ended),
}

pub struct Receiver {
    id: Handle,
    options: ReceiveOptions,
    owner: Owner,
    inbox: Arc<Inbox>,
    opened: Instant,
    queue: Mutex<Queue>,
}

#[derive(Default)]
struct Queue {
    stale_markers: Vec<(Instant, Item)>,
    items: VecDeque<(Instant, Item)>,
    peak: Peak,
    wake_armed: bool,
    ended: Option<Ended>,
    stats: ReceiverStats,
}

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct ReceiverStats {
    pub delivered: u64,
    pub dropped: u64,
    pub lost: u64,
}

impl Receiver {
    pub fn new(id: Handle, options: ReceiveOptions, inbox: Arc<Inbox>) -> Result<Self, CoreError> {
        options.validate()?;
        Ok(Self {
            id,
            options,
            owner: Owner::current(),
            inbox,
            opened: Instant::now(),
            queue: Mutex::default(),
        })
    }

    pub fn id(&self) -> Handle {
        self.id
    }

    pub fn options(&self) -> &ReceiveOptions {
        &self.options
    }

    pub fn opened(&self) -> Instant {
        self.opened
    }

    pub fn read(&self, limit: usize) -> Result<ReceiveRead, CoreError> {
        self.owner.check()?;
        let mut queue = self.queue.lock();
        if queue.ended == Some(Ended::Closed) {
            return Err(CoreError::HandleClosed(self.id));
        }
        let items = queue.take(limit.max(1));
        if !items.is_empty() {
            return Ok(ReceiveRead::Items(items));
        }
        Ok(match queue.ended {
            Some(why) => ReceiveRead::Ended(why),
            None => {
                queue.wake_armed = true;
                ReceiveRead::Pending
            }
        })
    }

    pub fn queued(&self) -> Result<Duration, CoreError> {
        self.owner.check()?;
        let queue = self.queue.lock();
        let oldest = queue.stale_markers.first().or(queue.items.front());
        Ok(oldest.map_or(Duration::ZERO, |(queued, _)| queued.elapsed()))
    }

    pub fn clear(&self) -> Result<(), CoreError> {
        self.owner.check()?;
        self.queue.lock().discard();
        Ok(())
    }

    pub fn stats(&self) -> Result<ReceiverStats, CoreError> {
        self.owner.check()?;
        Ok(self.queue.lock().stats)
    }

    pub fn ended(&self) -> Option<Ended> {
        self.queue.lock().ended
    }

    pub fn is_closed(&self) -> bool {
        self.ended().is_some()
    }

    pub fn close(&self) {
        if !self.owner.is_current() {
            return;
        }
        let mut queue = self.queue.lock();
        queue.discard();
        queue.ended = Some(Ended::Closed);
        self.wake(&mut queue);
    }

    pub fn end(&self, why: Ended) {
        let mut queue = self.queue.lock();
        if queue.ended.is_none() {
            queue.ended = Some(why);
            self.wake(&mut queue);
        }
    }

    pub fn push(&self, items: impl IntoIterator<Item = Item>, now: Instant) {
        let mut queue = self.queue.lock();
        if queue.ended.is_some() {
            return;
        }
        let before = queue.items.len();
        let buffer = self.options.buffer;
        let full = self.options.overflow == Overflow::DropNewest && queue.is_full(now, buffer);
        for item in items {
            if full && !matches!(item, Item::Speech { .. }) {
                queue.stats.dropped += 1;
            } else {
                queue.add(item, now);
            }
        }
        let added = queue.items.len() > before;
        if self.options.overflow == Overflow::DropOldest {
            queue.overflow(now, buffer);
        }
        queue.shrink();
        if added {
            self.wake(&mut queue);
        }
    }

    pub fn add_lost(&self, packets: u64) {
        self.queue.lock().stats.lost += packets;
    }

    pub fn add_dropped(&self, frames: u64) {
        self.queue.lock().stats.dropped += frames;
    }

    fn wake(&self, queue: &mut Queue) {
        if mem::take(&mut queue.wake_armed) {
            self.inbox.push(Delivery::VoiceWake { handle: self.id });
        }
    }
}

impl Queue {
    fn add(&mut self, item: Item, now: Instant) {
        match &item {
            Item::Frame { lost, .. } => {
                self.stats.delivered += 1;
                self.stats.lost += u64::from(*lost);
            }
            Item::Packet { .. } => self.stats.delivered += 1,
            Item::Speech { user, .. } => {
                let user = *user;
                self.stale_markers
                    .retain(|(_, marker)| !marker.is_speech_of(user));
            }
        }
        self.items.push_back((now, item));
    }

    fn is_full(&self, now: Instant, buffer: Duration) -> bool {
        self.items
            .front()
            .is_some_and(|(queued, _)| now.saturating_duration_since(*queued) > buffer)
    }

    fn overflow(&mut self, now: Instant, buffer: Duration) {
        while self.is_full(now, buffer)
            && let Some((queued, item)) = self.items.pop_front()
        {
            match item {
                Item::Speech { user, .. } => {
                    if !self.items.iter().any(|(_, later)| later.is_speech_of(user)) {
                        self.stale_markers.push((queued, item));
                    }
                }
                Item::Frame { .. } | Item::Packet { .. } => self.stats.dropped += 1,
            }
        }
    }

    fn shrink(&mut self) {
        self.peak.observe(self.items.len());
        self.peak
            .shrink(self.items.capacity(), |to| self.items.shrink_to(to));
        self.peak.advance();
    }

    fn take(&mut self, limit: usize) -> Vec<(Instant, Item)> {
        let stale = limit.min(self.stale_markers.len());
        let fresh = limit.saturating_sub(stale).min(self.items.len());
        let mut taken = Vec::with_capacity(stale + fresh);
        taken.extend(self.stale_markers.drain(..stale));
        taken.extend(self.items.drain(..fresh));
        taken
    }

    fn discard(&mut self) {
        self.stale_markers.clear();
        self.items.clear();
    }
}

impl Item {
    fn is_speech_of(&self, speaker: UserId) -> bool {
        matches!(self, Self::Speech { user, .. } if *user == speaker)
    }
}

#[cfg(test)]
mod tests {
    use std::collections::HashSet;

    use proptest::prelude::*;

    use super::*;
    use crate::voice::sink::tests::Fixture;
    use crate::voice::types::{Overflow, ReceiveFormat, ReceiveStall, SILENCE_FRAME};

    const ALICE: u64 = 1;
    const BOB: u64 = 2;

    fn open(fixture: &Fixture, buffer_ms: u64) -> Receiver {
        let options = ReceiveOptions {
            format: ReceiveFormat::Opus,
            users: None,
            exclude: HashSet::new(),
            include_self: false,
            buffer: Duration::from_millis(buffer_ms),
            jitter: Duration::ZERO,
            markers: true,
            overflow: Overflow::DropOldest,
            stall: ReceiveStall::Wait,
        };
        Receiver::new(Handle::from(1), options, Arc::clone(&fixture.inbox)).unwrap()
    }

    fn frame(user: u64, offset: u64, at: Instant) -> Item {
        Item::Frame {
            user: Some(UserId::from(user)),
            ssrc: Some(1),
            timestamp: Some(0),
            offset,
            samples: 960,
            at,
            data: Bytes::from_static(&SILENCE_FRAME),
            lost: false,
        }
    }

    fn speech(user: u64, speaking: bool, at: Instant) -> Item {
        Item::Speech {
            user: UserId::from(user),
            ssrc: 1,
            speaking: if speaking {
                Speaking::Started
            } else {
                Speaking::Stopped(Duration::ZERO)
            },
            offset: Some(0),
            at,
        }
    }

    fn wakes(fixture: &Fixture) -> usize {
        fixture
            .inbox
            .drain()
            .iter()
            .filter(|delivery| matches!(delivery, Delivery::VoiceWake { .. }))
            .count()
    }

    #[test]
    fn items_wait_for_read_and_one_wake_fires_per_waiting_read() {
        let fixture = Fixture::new();
        let receiver = open(&fixture, 60_000);
        let now = Instant::now();
        let early = now.checked_sub(Duration::from_secs(10)).unwrap();
        let later = now.checked_sub(Duration::from_secs(1)).unwrap();
        let gap = Item::Frame {
            user: Some(UserId::from(ALICE)),
            ssrc: Some(1),
            timestamp: Some(2_880),
            offset: 2_880,
            samples: 960,
            at: now,
            data: Bytes::new(),
            lost: true,
        };

        receiver.push([frame(ALICE, 0, now)], early);
        let unwaited = wakes(&fixture);
        let first = receiver.read(256).unwrap();
        let waits = [receiver.read(256).unwrap(), receiver.read(256).unwrap()];
        let batch = [
            frame(ALICE, 960, now),
            frame(ALICE, 1_920, now),
            gap.clone(),
        ];
        receiver.push(batch, early);
        receiver.push([frame(ALICE, 3_840, now)], later);
        let woken = wakes(&fixture);
        let oldest = receiver.queued().unwrap();
        let partial = receiver.read(2).unwrap();
        receiver.push([frame(ALICE, 4_800, now)], later);
        let while_more_remain = wakes(&fixture);
        let rest = receiver.read(0).unwrap();
        let next_oldest = receiver.queued().unwrap();
        receiver.add_lost(3);
        receiver.clear().unwrap();

        assert_eq!((unwaited, woken, while_more_remain), (0, 1, 0));
        assert_eq!(
            first,
            ReceiveRead::Items(vec![(early, frame(ALICE, 0, now))])
        );
        assert_eq!(waits, [ReceiveRead::Pending, ReceiveRead::Pending]);
        assert_eq!(
            partial,
            ReceiveRead::Items(vec![
                (early, frame(ALICE, 960, now)),
                (early, frame(ALICE, 1_920, now))
            ])
        );
        assert_eq!(rest, ReceiveRead::Items(vec![(early, gap)]));
        assert!(oldest >= Duration::from_secs(10), "{oldest:?}");
        assert!(
            (Duration::from_secs(1)..Duration::from_secs(10)).contains(&next_oldest),
            "{next_oldest:?}"
        );
        assert_eq!(receiver.queued().unwrap(), Duration::ZERO);
        assert_eq!(receiver.read(256).unwrap(), ReceiveRead::Pending);
        assert_eq!(
            receiver.stats().unwrap(),
            ReceiverStats {
                delivered: 6,
                dropped: 0,
                lost: 4
            }
        );
    }

    #[test]
    fn overflow_drops_the_oldest_audio_beyond_the_buffer_and_counts_it() {
        let fixture = Fixture::new();
        let receiver = open(&fixture, 100);
        let start = Instant::now();
        let at = |millis| start + Duration::from_millis(millis);

        let opening = [
            speech(ALICE, true, start),
            frame(ALICE, 0, start),
            frame(ALICE, 960, start),
        ];
        receiver.push(opening, at(0));
        let turn = [
            frame(ALICE, 1_920, start),
            speech(ALICE, false, start),
            speech(BOB, true, start),
        ];
        receiver.push(turn, at(60));
        receiver.push([frame(BOB, 0, start)], at(100));
        receiver.push([frame(BOB, 960, start)], at(180));
        receiver.push([speech(BOB, false, start)], at(200));
        receiver.add_dropped(2);

        assert_eq!(
            receiver.read(256).unwrap(),
            ReceiveRead::Items(vec![
                (at(60), speech(ALICE, false, start)),
                (at(100), frame(BOB, 0, start)),
                (at(180), frame(BOB, 960, start)),
                (at(200), speech(BOB, false, start)),
            ])
        );
        assert_eq!(
            receiver.stats().unwrap(),
            ReceiverStats {
                delivered: 5,
                dropped: 5,
                lost: 0
            }
        );
    }

    #[test]
    fn drop_newest_keeps_the_queued_audio_and_drops_what_arrives_while_full() {
        let fixture = Fixture::new();
        let options = ReceiveOptions {
            overflow: Overflow::DropNewest,
            ..open(&fixture, 100).options().clone()
        };
        let receiver = Receiver::new(Handle::from(1), options, Arc::clone(&fixture.inbox)).unwrap();
        let start = Instant::now();
        let at = |millis| start + Duration::from_millis(millis);

        receiver.push([frame(ALICE, 0, start)], at(0));
        receiver.push([frame(ALICE, 960, start)], at(60));
        receiver.push([frame(ALICE, 1_920, start)], at(100));
        let late = [frame(ALICE, 2_880, start), speech(ALICE, false, start)];
        receiver.push(late, at(120));
        let kept = receiver.read(256).unwrap();
        receiver.push([frame(ALICE, 3_840, start)], at(130));

        assert_eq!(
            kept,
            ReceiveRead::Items(vec![
                (at(0), frame(ALICE, 0, start)),
                (at(60), frame(ALICE, 960, start)),
                (at(100), frame(ALICE, 1_920, start)),
                (at(120), speech(ALICE, false, start)),
            ])
        );
        assert_eq!(
            receiver.read(256).unwrap(),
            ReceiveRead::Items(vec![(at(130), frame(ALICE, 3_840, start))])
        );
        assert_eq!(
            receiver.stats().unwrap(),
            ReceiverStats {
                delivered: 4,
                dropped: 1,
                lost: 0
            }
        );
    }

    #[test]
    fn close_discards_and_a_disconnect_keeps_queued_items_until_read() {
        let fixture = Fixture::new();
        let now = Instant::now();
        let closed = open(&fixture, 60_000);
        let waiting = open(&fixture, 60_000);
        closed.push([frame(ALICE, 0, now)], now);
        assert_eq!(waiting.read(256).unwrap(), ReceiveRead::Pending);

        closed.close();
        closed.close();
        waiting.close();
        closed.push([frame(ALICE, 960, now)], now);

        assert_eq!(wakes(&fixture), 1);
        assert_eq!(closed.ended(), Some(Ended::Closed));
        assert_eq!(closed.queued().unwrap(), Duration::ZERO);
        assert!(matches!(
            closed.read(256),
            Err(CoreError::HandleClosed(handle)) if handle == closed.id()
        ));

        let ended = open(&fixture, 60_000);
        let waiting = open(&fixture, 60_000);
        ended.push([frame(ALICE, 0, now)], now);
        assert_eq!(waiting.read(256).unwrap(), ReceiveRead::Pending);

        for receiver in [&ended, &waiting] {
            receiver.end(Ended::Disconnected);
            receiver.end(Ended::Moved);
        }
        ended.push([frame(ALICE, 960, now)], now);

        assert_eq!(wakes(&fixture), 1);
        assert_eq!(ended.ended(), Some(Ended::Disconnected));
        assert_eq!(
            ended.read(256).unwrap(),
            ReceiveRead::Items(vec![(now, frame(ALICE, 0, now))])
        );
        assert_eq!(
            ended.read(256).unwrap(),
            ReceiveRead::Ended(Ended::Disconnected)
        );
        assert_eq!(
            waiting.read(256).unwrap(),
            ReceiveRead::Ended(Ended::Disconnected)
        );
    }

    proptest! {
        #[test]
        fn a_queue_hands_out_frames_in_order_and_accounts_for_each_one(
            drop_newest in any::<bool>(),
            steps in proptest::collection::vec((0..3_u64, any::<bool>(), 0..80_u64, 0..3_usize), 1..60),
        ) {
            let fixture = Fixture::new();
            let overflow = if drop_newest { Overflow::DropNewest } else { Overflow::DropOldest };
            let options = ReceiveOptions { overflow, ..open(&fixture, 100).options().clone() };
            let receiver = Receiver::new(Handle::from(1), options, Arc::clone(&fixture.inbox)).unwrap();
            let start = Instant::now();
            let mut now = start;
            let (mut pushed, mut edges) = (0, 0);
            let mut heard = Vec::new();
            let collect = |read: ReceiveRead, heard: &mut Vec<Item>| match read {
                ReceiveRead::Items(items) => {
                    heard.extend(items.into_iter().map(|(_, item)| item));
                    true
                }
                _ => false,
            };

            for (frames, edge, advance, reads) in steps {
                now += Duration::from_millis(advance);
                let mut items: Vec<Item> = (pushed..pushed + frames)
                    .map(|index| frame(ALICE, index * 960, start))
                    .collect();
                pushed += frames;
                if edge {
                    edges += 1;
                    items.push(speech(ALICE, edges % 2 == 1, start));
                }
                receiver.push(items, now);
                if reads > 0 {
                    collect(receiver.read(reads).unwrap(), &mut heard);
                }
            }
            while collect(receiver.read(256).unwrap(), &mut heard) {}

            let offsets: Vec<u64> = heard
                .iter()
                .filter_map(|item| match item {
                    Item::Frame { offset, .. } => Some(*offset),
                    _ => None,
                })
                .collect();
            let spoken = heard.iter().filter(|item| matches!(item, Item::Speech { .. })).count();
            let dropped = receiver.stats().unwrap().dropped;
            prop_assert!(offsets.windows(2).all(|pair| pair[0] < pair[1]), "{offsets:?}");
            prop_assert_eq!(offsets.len() as u64 + dropped, pushed);
            if drop_newest {
                prop_assert_eq!(spoken, edges);
            }
        }
    }
}
