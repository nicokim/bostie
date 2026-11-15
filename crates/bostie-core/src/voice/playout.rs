#![deny(clippy::arithmetic_side_effects)]

use std::time::Duration;

use bytes::Bytes;

use crate::ids::UserId;
use crate::voice::types::{SAMPLE_RATE, samples_in};

const RING: usize = OLDEST as usize + 1;
const MAX_DROPOUT: u16 = 3_000;
const MAX_MISORDER: u16 = 100;
const RESYNC: Duration = Duration::from_secs(1);
const ANCHOR_WINDOW: Duration = Duration::from_secs(10);

const OLDEST: u16 = 63;
const TIMESTAMP_SLACK: u32 = 960;

pub(crate) fn to_samples(duration: Duration) -> i64 {
    i64::try_from(samples_in(duration, SAMPLE_RATE)).unwrap_or(i64::MAX)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Slot {
    pub sequence: u16,
    pub timestamp: u32,
    pub samples: u32,
    pub at: i64,
    pub payload: Option<Bytes>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Check {
    Fresh,
    Duplicate,
    Late,
    Jump,
    Restart,
}

#[derive(Debug)]
pub(crate) struct Stream {
    user: UserId,
    ring: Box<[Option<Slot>; RING]>,
    highest: Option<u16>,
    probation: Option<u16>,
    anchor: Option<Anchor>,
    restarts: u64,
    last_packet: Option<i64>,
}

impl Stream {
    pub(crate) fn new(user: UserId) -> Self {
        Self {
            user,
            ring: Box::new([const { None }; RING]),
            highest: None,
            probation: None,
            anchor: None,
            restarts: 0,
            last_packet: None,
        }
    }

    pub(crate) fn user(&self) -> UserId {
        self.user
    }

    pub(crate) fn last_packet(&self) -> Option<i64> {
        self.last_packet
    }

    pub(crate) fn check(&mut self, sequence: u16) -> Check {
        match self.classify(sequence) {
            Check::Jump => {
                let restarted = self.probation == Some(sequence);
                self.probation = Some(sequence.wrapping_add(1));
                if restarted {
                    Check::Restart
                } else {
                    Check::Jump
                }
            }
            check => check,
        }
    }

    pub(crate) fn insert(&mut self, slot: Slot, check: Check) -> bool {
        if check == Check::Restart && self.classify(slot.sequence) == Check::Jump {
            self.restart();
        }
        if self.classify(slot.sequence) != Check::Fresh {
            return false;
        }
        self.advance(slot.sequence);
        let mark = Mark {
            local: slot.at,
            timestamp: slot.timestamp,
        };
        match &mut self.anchor {
            Some(anchor) => anchor.update(mark),
            None => self.anchor = Some(Anchor::new(mark)),
        }
        self.last_packet = Some(self.last_packet.map_or(slot.at, |last| last.max(slot.at)));
        if let Some(entry) = self.ring.get_mut(index(slot.sequence)) {
            *entry = Some(slot);
        }
        true
    }

    pub(crate) fn due(&self, timestamp: u32) -> Option<i64> {
        self.anchor
            .as_ref()
            .map(|anchor| anchor.mark().due(timestamp))
    }

    pub(crate) fn release_before(&mut self, sequence: u16) {
        for slot in self.ring.iter_mut().flatten() {
            if is_before(slot.sequence, sequence) {
                slot.payload = None;
            }
        }
    }

    fn classify(&self, sequence: u16) -> Check {
        let Some(highest) = self.highest else {
            return Check::Fresh;
        };
        if (1..=MAX_DROPOUT).contains(&sequence.wrapping_sub(highest)) {
            return Check::Fresh;
        }
        let behind = highest.wrapping_sub(sequence);
        if behind > MAX_MISORDER {
            Check::Jump
        } else if self.get(sequence).is_some() {
            Check::Duplicate
        } else if behind > OLDEST {
            Check::Late
        } else {
            Check::Fresh
        }
    }

    fn advance(&mut self, sequence: u16) {
        let ahead = self
            .highest
            .map_or(1, |highest| sequence.wrapping_sub(highest));
        if (1..=MAX_DROPOUT).contains(&ahead) {
            self.highest = Some(sequence);
        }
    }

    fn restart(&mut self) {
        self.ring.fill(None);
        self.highest = None;
        self.probation = None;
        self.anchor = None;
        self.restarts = self.restarts.wrapping_add(1);
    }

    fn get(&self, sequence: u16) -> Option<&Slot> {
        self.ring
            .get(index(sequence))?
            .as_ref()
            .filter(|slot| slot.sequence == sequence)
    }

    fn playable(&self, sequence: u16) -> Option<&Slot> {
        self.get(sequence).filter(|slot| slot.payload.is_some())
    }

    fn first_from(&self, next: u16) -> Option<(&Slot, u16)> {
        let behind = self.highest?.wrapping_sub(next);
        if behind == u16::MAX {
            return None;
        }
        (behind.saturating_sub(OLDEST)..=behind).find_map(|gap| {
            self.playable(next.wrapping_add(gap))
                .map(|slot| (slot, gap))
        })
    }

    fn first_since(&self, opened: i64) -> Option<&Slot> {
        let highest = self.highest?;
        (0..=OLDEST).rev().find_map(|behind| {
            self.playable(highest.wrapping_sub(behind))
                .filter(|slot| slot.at >= opened)
        })
    }

    fn duration(&self, slot: &Slot) -> u32 {
        let Some(successor) = self.get(slot.sequence.wrapping_add(1)) else {
            return slot.samples;
        };
        let step = successor.timestamp.wrapping_sub(slot.timestamp);
        if slot.samples > step.saturating_add(TIMESTAMP_SLACK) {
            step
        } else {
            slot.samples
        }
    }
}

fn index(sequence: u16) -> usize {
    usize::from(sequence) % RING
}

pub(crate) fn is_before(sequence: u16, other: u16) -> bool {
    other.wrapping_sub(sequence).cast_signed() > 0
}

#[derive(Debug, Clone, Copy)]
struct Mark {
    local: i64,
    timestamp: u32,
}

impl Mark {
    fn due(self, timestamp: u32) -> i64 {
        let delta = timestamp.wrapping_sub(self.timestamp).cast_signed();
        self.local.saturating_add(i64::from(delta))
    }

    fn is_earlier_than(self, other: Self) -> bool {
        self.local < other.due(self.timestamp)
    }
}

#[derive(Debug, Clone, Copy)]
struct Anchor {
    current: Mark,
    previous: Option<Mark>,
    started: i64,
}

impl Anchor {
    fn new(mark: Mark) -> Self {
        Self {
            current: mark,
            previous: None,
            started: mark.local,
        }
    }

    fn mark(&self) -> Mark {
        match self.previous {
            Some(previous) if previous.is_earlier_than(self.current) => previous,
            _ => self.current,
        }
    }

    fn update(&mut self, mark: Mark) {
        if mark.local.saturating_sub(self.mark().due(mark.timestamp)) > to_samples(RESYNC) {
            *self = Self::new(mark);
        } else if mark.local.saturating_sub(self.started) >= to_samples(ANCHOR_WINDOW) / 2 {
            self.previous = Some(self.current);
            self.current = mark;
            self.started = mark.local;
        } else if mark.is_earlier_than(self.current) {
            self.current = mark;
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum Release {
    Packet {
        slot: Slot,
        due: i64,
        samples: u32,
    },
    Conceal {
        count: u16,
        samples: u32,
        timestamp: u32,
        due: i64,
        next: Slot,
    },
    Skip {
        lost: u16,
    },
}

#[derive(Debug, Clone)]
pub(crate) struct Cursor {
    next: Option<u16>,
    last: Option<(u32, u32)>,
    restarts: u64,
    opened: i64,
    end: i64,
}

impl Cursor {
    pub(crate) fn new(opened: i64) -> Self {
        Self {
            next: None,
            last: None,
            restarts: 0,
            opened,
            end: 0,
        }
    }

    pub(crate) fn position(&self) -> Option<u16> {
        self.next
    }

    pub(crate) fn next(
        &mut self,
        stream: &Stream,
        now: i64,
        jitter: i64,
        max_conceal: i64,
    ) -> Option<Release> {
        if self.restarts != stream.restarts {
            self.restarts = stream.restarts;
            self.last = None;
            if self.next.take().is_some() {
                return Some(Release::Skip { lost: 0 });
            }
        }
        let (slot, gap) = match self.next {
            Some(next) => stream.first_from(next)?,
            None => (stream.first_since(self.opened)?, 0),
        };
        let due = stream.due(slot.timestamp)?.min(slot.at);
        if now < due.saturating_add(jitter) {
            return None;
        }
        if gap > 0 {
            return Some(self.bridge(slot, due, gap, max_conceal));
        }
        let samples = stream.duration(slot);
        self.next = Some(slot.sequence.wrapping_add(1));
        self.last = Some((slot.timestamp, samples));
        Some(Release::Packet {
            slot: slot.clone(),
            due,
            samples,
        })
    }

    pub(crate) fn backlog(&self, stream: &Stream) -> Option<i64> {
        let from = match self.next.filter(|_| self.restarts == stream.restarts) {
            Some(next) => next,
            None => stream.first_since(self.opened)?.sequence,
        };
        stream
            .ring
            .iter()
            .flatten()
            .filter(|slot| slot.payload.is_some() && !is_before(slot.sequence, from))
            .map(|slot| slot.at)
            .min()
    }

    pub(crate) fn offset(&mut self, due: i64, samples: u32) -> u64 {
        let offset = due.saturating_sub(self.opened).max(self.end);
        self.end = offset.saturating_add(i64::from(samples));
        offset.unsigned_abs()
    }

    fn bridge(&mut self, slot: &Slot, due: i64, gap: u16, max_conceal: i64) -> Release {
        self.next = Some(slot.sequence);
        let Some((timestamp, samples)) = self.last.take().filter(|&(timestamp, samples)| {
            is_concealable(timestamp, samples, slot.timestamp, gap, max_conceal)
        }) else {
            return Release::Skip { lost: gap };
        };
        let lost = samples.wrapping_mul(u32::from(gap));
        self.last = Some((timestamp.wrapping_add(lost), samples));
        Release::Conceal {
            count: gap,
            samples,
            timestamp: timestamp.wrapping_add(samples),
            due: due.saturating_sub(i64::from(lost)),
            next: slot.clone(),
        }
    }
}

fn is_concealable(last: u32, samples: u32, next: u32, gap: u16, max_conceal: i64) -> bool {
    let span = i64::from(gap).saturating_mul(i64::from(samples));
    let expected = last.wrapping_add(samples.wrapping_mul(u32::from(gap).wrapping_add(1)));
    let misalignment = next.wrapping_sub(expected).cast_signed().unsigned_abs();
    samples > 0 && span <= max_conceal && misalignment <= samples / 2
}

#[cfg(test)]
#[expect(clippy::arithmetic_side_effects)]
mod tests {
    use proptest::collection::vec;
    use proptest::prelude::*;

    use super::*;

    const FRAME: i64 = 960;
    const JITTER: i64 = 5 * FRAME;
    const MAX_CONCEAL: i64 = 5 * FRAME;

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    enum Out {
        Play(u16),
        Conceal(u16),
        Skip(u16),
    }

    fn out(release: &Release) -> Out {
        match release {
            Release::Packet { slot, .. } => Out::Play(slot.sequence),
            Release::Conceal { count, .. } => Out::Conceal(*count),
            Release::Skip { lost } => Out::Skip(*lost),
        }
    }

    fn plays(sequences: impl IntoIterator<Item = u16>) -> Vec<Out> {
        sequences.into_iter().map(Out::Play).collect()
    }

    fn packet(sequence: u16, timestamp: u32, at: i64) -> Slot {
        Slot {
            sequence,
            timestamp,
            samples: 960,
            at,
            payload: Some(Bytes::from_static(b"opus")),
        }
    }

    fn steady(sequence: u16) -> Slot {
        packet(
            sequence,
            960 * u32::from(sequence),
            FRAME * i64::from(sequence),
        )
    }

    fn stream() -> Stream {
        Stream::new(UserId::from(7))
    }

    struct Player {
        stream: Stream,
        cursor: Cursor,
        jitter: i64,
        now: i64,
        played: Vec<Out>,
        frames: Vec<(u64, u32)>,
    }

    impl Player {
        fn new(jitter: i64) -> Self {
            Self {
                stream: stream(),
                cursor: Cursor::new(0),
                jitter,
                now: 0,
                played: Vec::new(),
                frames: Vec::new(),
            }
        }

        fn arrive(&mut self, slot: Slot) -> Check {
            let check = self.stream.check(slot.sequence);
            self.stream.insert(slot, check);
            check
        }

        fn tick(&mut self, now: i64) {
            while let Some(release) = self
                .cursor
                .next(&self.stream, now, self.jitter, MAX_CONCEAL)
            {
                self.played.push(out(&release));
                match release {
                    Release::Packet { due, samples, .. } => {
                        let offset = self.cursor.offset(due, samples);
                        self.frames.push((offset, samples));
                    }
                    Release::Conceal {
                        count,
                        samples,
                        due,
                        ..
                    } => {
                        for lost in 0..i64::from(count) {
                            let due = due + lost * i64::from(samples);
                            let offset = self.cursor.offset(due, samples);
                            self.frames.push((offset, samples));
                        }
                    }
                    Release::Skip { .. } => {}
                }
            }
        }

        fn run(&mut self, arrivals: impl IntoIterator<Item = Slot>) {
            let mut arrivals: Vec<Slot> = arrivals.into_iter().collect();
            arrivals.sort_by_key(|slot| slot.at);
            let last = arrivals.last().map_or(self.now, |slot| slot.at);
            let mut arrivals = arrivals.into_iter().peekable();
            while self.now <= last + self.jitter + 4 * FRAME {
                while let Some(slot) = arrivals.next_if(|slot| slot.at <= self.now) {
                    self.arrive(slot);
                }
                self.tick(self.now);
                self.now += FRAME;
            }
        }

        fn offsets(&self) -> Vec<u64> {
            self.frames.iter().map(|&(offset, _)| offset).collect()
        }

        fn assert_offsets_never_overlap(&self) {
            let following = self.frames.iter().skip(1);
            for (&(offset, samples), &(next, _)) in self.frames.iter().zip(following) {
                assert!(next >= offset + u64::from(samples), "{:?}", self.frames);
            }
        }
    }

    #[test]
    fn packets_release_in_sequence_order_at_due_plus_jitter() {
        let mut player = Player::new(JITTER);
        let delay = 100;
        for sequence in 0..5 {
            let slot = steady(sequence);
            player.arrive(Slot {
                at: slot.at + delay,
                ..slot
            });
        }

        player.tick(delay + JITTER - 1);
        assert!(player.played.is_empty());
        player.tick(delay + JITTER);
        assert_eq!(player.played, plays([0]));
        player.tick(delay + JITTER + 4 * FRAME);
        assert_eq!(player.played, plays(0..5));
        assert_eq!(player.offsets(), [100, 1_060, 2_020, 2_980, 3_940]);
    }

    #[test]
    fn a_cursor_starts_at_the_first_packet_that_arrived_after_it_opened() {
        let mut player = Player::new(0);
        player.cursor = Cursor::new(5 * FRAME + 1);

        player.run((0..10).map(steady));

        assert_eq!(player.played, plays(6..10));
        assert_eq!(player.offsets(), [959, 1_919, 2_879, 3_839]);
    }

    #[test]
    fn a_swapped_pair_releases_in_order() {
        let mut player = Player::new(2 * FRAME);
        let late = Slot {
            at: 3 * FRAME + FRAME / 2,
            ..steady(2)
        };

        player.run(
            (0..6)
                .filter(|&sequence| sequence != 2)
                .map(steady)
                .chain([late]),
        );

        assert_eq!(player.played, plays(0..6));
    }

    #[test]
    fn losses_are_concealed_up_to_the_limit_and_skipped_beyond() {
        for (samples, lost, pause, bridge) in [
            (960, 1, 0, Out::Conceal(1)),
            (960, 2, 480, Out::Conceal(2)),
            (960, 2, 481, Out::Skip(2)),
            (960, 5, 0, Out::Conceal(5)),
            (960, 6, 0, Out::Skip(6)),
            (960, 2, 48_000, Out::Skip(2)),
            (2_880, 1, 0, Out::Conceal(1)),
            (2_880, 2, 0, Out::Skip(2)),
        ] {
            let mut player = Player::new(JITTER);
            let gone = 4..4 + lost;
            let kept = (0..12).filter(|sequence| !gone.contains(sequence));
            let start = |sequence: u16| {
                let paused = if sequence < 4 { 0 } else { pause };
                samples * u32::from(sequence) + paused
            };

            player.run(kept.clone().map(|sequence| Slot {
                samples,
                ..packet(sequence, start(sequence), i64::from(start(sequence)))
            }));

            let mut expected = plays(0..4);
            expected.push(bridge);
            expected.extend(plays(4 + lost..12));
            assert_eq!(player.played, expected, "{samples} {lost} {pause}");
            let framed: Vec<u16> = match bridge {
                Out::Conceal(_) => (0..12).collect(),
                _ => kept.collect(),
            };
            let offsets: Vec<u64> = framed
                .iter()
                .map(|&sequence| u64::from(start(sequence)))
                .collect();
            assert_eq!(player.offsets(), offsets, "{samples} {lost} {pause}");
        }
    }

    #[test]
    fn a_pause_releases_no_lost_frames_and_the_offset_jumps() {
        let mut player = Player::new(JITTER);
        let pause = 48_000;

        player.run((0..10).map(|sequence| {
            let shift = if sequence < 5 { 0 } else { pause };
            let slot = steady(sequence);
            Slot {
                timestamp: slot.timestamp + shift,
                at: slot.at + i64::from(shift),
                ..slot
            }
        }));

        assert_eq!(player.played, plays(0..10));
        let offsets = player.offsets();
        assert_eq!(offsets[5] - offsets[4], 960 + 48_000);
    }

    #[test]
    fn duplicates_and_packets_older_than_the_ring_are_refused() {
        let mut player = Player::new(0);
        for sequence in (0..=100).filter(|&sequence| sequence != 37) {
            player.arrive(steady(sequence));
        }
        let stream = &mut player.stream;

        for (sequence, check) in [
            (100, Check::Duplicate),
            (37, Check::Fresh),
            (36, Check::Late),
            (0, Check::Late),
            (65_535, Check::Jump),
            (101, Check::Fresh),
            (3_100, Check::Fresh),
            (3_101, Check::Jump),
        ] {
            assert_eq!(stream.check(sequence), check, "{sequence}");
        }
        assert!(!stream.insert(steady(30), Check::Fresh));
        assert!(!stream.insert(steady(60), Check::Fresh));
    }

    #[test]
    fn a_sequence_jump_restarts_the_stream_on_its_second_packet() {
        for first in [9_u16.wrapping_sub(5_000), 9 + 40_000] {
            let mut player = Player::new(0);
            player.run((0..10).map(steady));
            let alive = player.stream.last_packet();
            let restarted = |offset: u16| {
                packet(
                    first.wrapping_add(offset),
                    7_000_000 + 960 * u32::from(offset),
                    FRAME * (16 + i64::from(offset)),
                )
            };
            let restarted_run = |end: u16| {
                let mut expected = plays(0..10);
                expected.push(Out::Skip(0));
                expected.extend(plays((1..end).map(|offset| first.wrapping_add(offset))));
                expected
            };

            assert_eq!(player.arrive(restarted(0)), Check::Jump);
            assert_eq!(player.stream.last_packet(), alive);
            assert_eq!(player.stream.check(10), Check::Fresh);
            assert_eq!(player.arrive(restarted(1)), Check::Restart);
            player.run((2..4).map(restarted));

            assert_eq!(player.played, restarted_run(4), "{first}");
            player.assert_offsets_never_overlap();

            let mut held = Player::new(0);
            held.run((0..10).map(steady));
            let checks: Vec<Check> = (0..6)
                .map(|offset| held.stream.check(restarted(offset).sequence))
                .collect();
            assert_eq!(checks[..1], [Check::Jump], "{first}");
            assert_eq!(checks[1..], [Check::Restart; 5], "{first}");
            for offset in 1..6 {
                assert!(held.stream.insert(restarted(offset), Check::Restart));
            }
            assert!(!held.stream.insert(restarted(1), Check::Restart));
            held.tick(restarted(5).at);

            assert_eq!(held.played, restarted_run(6), "{first}");
        }
    }

    #[test]
    fn sequence_and_timestamp_wrap() {
        let mut player = Player::new(JITTER);
        let sequence = |index: u16| 65_530_u16.wrapping_add(index);

        player.run((0..20).map(|index| {
            packet(
                sequence(index),
                (u32::MAX - 1_999).wrapping_add(960 * u32::from(index)),
                FRAME * i64::from(index),
            )
        }));

        assert_eq!(player.played, plays((0..20).map(sequence)));
        let offsets: Vec<u64> = (0..20).map(|index| 960 * index).collect();
        assert_eq!(player.offsets(), offsets);
    }

    #[test]
    fn a_timestamp_sled_and_a_large_jump_resync() {
        let mut player = Player::new(2 * FRAME);
        let base = 5_000_000;
        let sled = (0..50).map(|sequence| packet(sequence, base, FRAME * i64::from(sequence)));
        let ahead = (50..60).map(|sequence| {
            let slot = steady(sequence);
            Slot {
                timestamp: base + 480_000 + slot.timestamp,
                ..slot
            }
        });
        let behind = (60..70).map(|sequence| {
            let slot = steady(sequence);
            let at = if sequence == 61 {
                62 * FRAME + FRAME / 2
            } else {
                slot.at
            };
            Slot {
                timestamp: base - 960_000 + slot.timestamp,
                at,
                ..slot
            }
        });

        player.run(sled.chain(ahead).chain(behind));

        assert_eq!(player.played, plays(0..70));
        player.assert_offsets_never_overlap();
    }

    #[test]
    fn a_slow_sender_clock_keeps_the_jitter_depth() {
        let mut player = Player::new(JITTER);
        let packets: u16 = 60_000;

        player.run((0..packets).map(|sequence| {
            let index = u32::from(sequence);
            let timestamp = 960 * index - 960 * index * 3 / 10_000;
            let reordered = if sequence % 2 == 0 { FRAME + 1 } else { 0 };
            packet(sequence, timestamp, FRAME * i64::from(index) + reordered)
        }));

        assert_eq!(player.played.len(), usize::from(packets));
        let missed = player
            .played
            .iter()
            .find(|out| !matches!(out, Out::Play(_)));
        assert_eq!(missed, None);
    }

    #[test]
    fn two_cursors_at_different_jitter_read_one_stream() {
        let mut stream = stream();
        let mut shallow = Cursor::new(0);
        let mut deep = Cursor::new(0);
        let late = Slot {
            at: 8 * FRAME,
            ..steady(5)
        };
        let arrivals: Vec<Slot> = (0..9)
            .filter(|&sequence| sequence != 5)
            .map(steady)
            .chain([late])
            .collect();
        let (mut shallow_heard, mut deep_heard) = (Vec::new(), Vec::new());

        for now in (0..=14).map(|tick| tick * FRAME) {
            for slot in arrivals.iter().filter(|slot| slot.at == now) {
                let check = stream.check(slot.sequence);
                stream.insert(slot.clone(), check);
            }
            while let Some(release) = shallow.next(&stream, now, FRAME, MAX_CONCEAL) {
                shallow_heard.push(out(&release));
            }
            while let Some(release) = deep.next(&stream, now, JITTER, MAX_CONCEAL) {
                deep_heard.push(out(&release));
            }
            stream.release_before(deep.position().unwrap_or_default());
            if now == 9 * FRAME {
                assert!(stream.get(5).unwrap().payload.is_some());
            }
        }

        let mut expected = plays(0..5);
        expected.push(Out::Conceal(1));
        expected.extend(plays(6..9));
        assert_eq!(shallow_heard, expected);
        assert_eq!(deep_heard, plays(0..9));
        assert_eq!(stream.get(5).unwrap().payload, None);
    }

    #[test]
    fn offsets_never_go_backwards_after_a_re_anchor() {
        let mut player = Player::new(2 * FRAME);
        let early = Slot {
            timestamp: 5 * 960 + 24_000,
            ..steady(5)
        };
        let late = Slot {
            at: 5 * FRAME + FRAME / 2,
            ..steady(4)
        };

        player.run((0..4).map(steady).chain([early, late]));

        assert_eq!(player.played, plays(0..6));
        assert_eq!(player.offsets(), [0, 960, 1_920, 2_880, 3_840, 4_800]);
    }

    #[test]
    fn long_packets_advance_by_their_own_duration() {
        let mut player = Player::new(JITTER);
        player.run((0..5).map(|sequence| {
            let start = 2_880 * u32::from(sequence);
            Slot {
                samples: 2_880,
                ..packet(sequence, start, i64::from(start))
            }
        }));
        let frames: Vec<(u64, u32)> = (0..5)
            .map(|index: u32| (u64::from(2_880 * index), 2_880))
            .collect();
        assert_eq!(player.frames, frames);

        for (samples, kept) in [(5_760, [960, 960, 960, 960, 5_760]), (1_920, [1_920; 5])] {
            let mut player = Player::new(JITTER);
            player.run((0..5).map(|sequence| Slot {
                samples,
                ..steady(sequence)
            }));
            let durations: Vec<u32> = player.frames.iter().map(|&(_, samples)| samples).collect();
            assert_eq!(durations, kept, "{samples}");
        }

        let mut player = Player::new(0);
        let trimmed = Slot {
            samples: 2_880,
            ..packet(0, 0, 0)
        };
        let unplayable = Slot {
            payload: None,
            ..packet(1, 0, 0)
        };
        player.run([trimmed, unplayable, packet(2, 0, FRAME)]);
        assert_eq!(player.played, [Out::Play(0), Out::Skip(1), Out::Play(2)]);
    }

    proptest! {
        #[test]
        fn any_arrival_order_within_the_jitter_plays_every_packet_in_order(
            delays in vec(0..JITTER, 2..40),
        ) {
            let count = u16::try_from(delays.len()).unwrap();
            let mut player = Player::new(JITTER);

            player.run((0..count).zip(&delays).map(|(sequence, delay)| {
                let slot = steady(sequence);
                Slot { at: slot.at + delay, ..slot }
            }));

            prop_assert_eq!(&player.played, &plays(0..count));
            player.assert_offsets_never_overlap();
        }

        #[test]
        fn offsets_never_overlap_or_go_back_for_any_arrivals(
            arrivals in vec((0..400_u16, 0..20 * FRAME), 1..60),
        ) {
            let mut player = Player::new(JITTER);

            player.run(arrivals.into_iter().map(|(sequence, delay)| {
                let slot = steady(sequence);
                Slot { at: slot.at + delay, ..slot }
            }));

            player.assert_offsets_never_overlap();
        }
    }
}
