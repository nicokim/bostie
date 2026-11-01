use std::collections::HashSet;
use std::net::UdpSocket;

use bytes::BytesMut;
use futures_util::FutureExt;
use proptest::prelude::*;

use super::intake::SPARE_WINDOWS;
use super::output::wants;
use super::*;
use crate::ids::{ChannelId, Handle, ShardId};
use crate::voice::codec::{Decoder, FrameEncoder};
use crate::voice::crypto::{Cipher, Mode};
use crate::voice::dave::{Outbound, SendMode};
use crate::voice::media::Transport;
use crate::voice::receiver::{Ended, Item, ReceiveRead, Receiver};
use crate::voice::rtp;
use crate::voice::sink::tests::Fixture;
use crate::voice::testkit::mls::{Call, Signal};
use crate::voice::testkit::sfu::{self, Sealing};
use crate::voice::types::{
    Application, DaveOp, EncoderSettings, FRAME, Overflow, PcmFormat, ReceiveFormat,
    ReceiveOptions, ReceiveStall, SampleFormat,
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
                speaking: *speaking == Speaking::Started,
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
        let info = Arc::new(Info::new(GuildId::from(GUILD), ShardId::from(0)));
        let reception = Reception::new(Arc::clone(&media), Arc::clone(&info), user(BOT), tuning);
        let now = reception.core.epoch + Duration::from_secs(1);
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
            payload: (self.media.open_receivers() > 0).then(|| Bytes::copy_from_slice(payload)),
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

    fn panic(&mut self) {
        self.now += FRAME;
        assert!(self.reception.restart(self.now, &"boom"));
    }

    fn run_unguarded(&mut self, now: Instant, skipped: u32) -> bool {
        let Reception { core, state, .. } = &mut self.reception;
        state
            .get_or_insert_with(|| Box::new(State::new(core)))
            .run(now, skipped, now + FRAME)
    }

    fn undecryptable(&self) -> u64 {
        self.reception
            .state
            .as_ref()
            .map_or(0, |state| state.hold.undecryptable.total)
    }

    fn recovered(&self) -> bool {
        self.media.recover.notified().now_or_never().is_some()
    }

    fn speech(&self) -> Vec<(u64, Speaking)> {
        self.fixture
            .inbox
            .drain()
            .into_iter()
            .filter_map(|delivery| match delivery {
                Delivery::VoiceSpeech { user, speaking, .. } => Some((u64::from(user), speaking)),
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
    let packets = opus(4, 0.3);
    rig.send(BOB_SSRC, 0, &packets[0]);
    rig.step();
    for (sequence, packet) in (0..).zip(&packets) {
        rig.send(ALICE_SSRC, sequence, packet);
        rig.step();
    }
    rig.send(STRANGER_SSRC, 0, &packets[0]);
    rig.step();
    let before_op_5 = read(&receiver);

    rig.map(ALICE_SSRC, ALICE);
    rig.map(BOB_SSRC, BOB);
    rig.step();
    let after_op_5 = read(&receiver);
    rig.now += rig.reception.core.tuning.hold;
    rig.step();
    rig.map(STRANGER_SSRC, STRANGER);
    rig.step();

    assert!(before_op_5.is_empty());
    assert_eq!(senders(&after_op_5), [Some(ALICE); 3]);
    assert_eq!(data(&after_op_5), packets[1..]);
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
    let queued_before_the_receiver = read(&receiver);
    rig.send(BOB_SSRC, 0, &garbage(1));
    rig.step();
    let speech = rig.speech();
    rig.send(ALICE_SSRC, 1, &garbage(2));
    rig.send(ALICE_SSRC, 2, &call.speak(user(ALICE), &long(3)));
    rig.step();
    rig.now += rig.media.speech_hangover() * 2;
    rig.step();
    let mut stopped = rig.speech();
    stopped.sort_unstable_by_key(|(user, _)| *user);
    rig.send(BOB_SSRC, 1, &garbage(3));
    rig.step();
    let restarted = rig.speech();
    rig.media.set_speech_activity(false);
    rig.step();
    let switched_off = rig.speech();
    rig.send(BOB_SSRC, 2, &garbage(4));
    rig.step();
    rig.now += rig.reception.core.tuning.hold;
    rig.step();

    assert!(rig.speech().is_empty());
    assert_eq!(restarted, [(BOB, Speaking::Started)]);
    assert_eq!(switched_off, [(BOB, Speaking::Stopped(FRAME))]);
    assert!(queued_before_the_receiver.is_empty());
    assert_eq!(
        speech,
        [(ALICE, Speaking::Started), (BOB, Speaking::Started)]
    );
    let sixty_ms = Duration::from_millis(60);
    assert_eq!(
        stopped,
        [
            (ALICE, Speaking::Stopped(FRAME * 2 + sixty_ms)),
            (BOB, Speaking::Stopped(FRAME))
        ]
    );
    assert_eq!(rig.undecryptable(), 1);
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

    for (speaker, users, exclude, include_self, heard) in cases {
        let options = ReceiveOptions {
            users: users
                .as_ref()
                .map(|ids| ids.iter().copied().map(user).collect()),
            exclude: exclude.iter().copied().map(user).collect(),
            include_self,
            ..options(ReceiveFormat::Opus)
        };
        assert_eq!(
            wants(&options, user(speaker), speaker == BOT),
            heard,
            "{speaker} {users:?} {exclude:?} {include_self}"
        );
    }
}

#[test]
fn frames_failing_in_a_late_commit_retry_on_dave_changes() {
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
        let still_held = rig.undecryptable();
        rig.now += rig.reception.core.tuning.hold;
        rig.step();

        assert_eq!(before, 4, "{speaker}");
        assert_eq!((while_behind, without_a_change), (0, 0), "{speaker}");
        assert_eq!((first.len(), rest.len()), (10, 2), "{speaker}");
        assert_eq!(data(&[first, rest].concat()), spoken, "{speaker}");
        assert_eq!(still_held, 0, "{speaker}");
        assert_eq!(rig.undecryptable(), 1, "{speaker}");
        assert!(read(&receiver).is_empty(), "{speaker}");
        assert!(!rig.recovered(), "{speaker}");
    }
}

#[test]
fn a_busy_dave_lock_does_not_hold_plain_frames() {
    let plain_ending_in_the_marker = Bytes::from_static(&[0xFC, 0x01, 0x02, 0xFA, 0xFA]);
    let mut rig = Rig::new(tuning());
    rig.map(ALICE_SSRC, ALICE);
    let receiver = rig.open(options(ReceiveFormat::Opus));
    let media = Arc::clone(&rig.media);
    let busy = media.dave.lock();
    rig.send(ALICE_SSRC, 0, &plain_ending_in_the_marker);
    rig.step();
    drop(busy);

    assert_eq!(data(&read(&receiver)), [plain_ending_in_the_marker]);
}

#[test]
fn dave_frames_wait_in_user_order_and_decrypt_in_slices() {
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

    assert_eq!(started, [(ALICE, Speaking::Started)]);
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
}

#[test]
fn the_dave_hold_drops_its_oldest_frames_beyond_its_bounds() {
    let packets = opus(4, 0.3);
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
            false,
        ),
        (
            "in total",
            in_total,
            vec![vec![(BOB, 0), (ALICE, 0), (ALICE, 1)], vec![(ALICE, 2)]],
            vec![(ALICE, 0), (ALICE, 1), (ALICE, 2)],
            false,
        ),
        (
            "waiting behind a busy lock",
            in_total,
            (0..4).map(|sequence| vec![(ALICE, sequence)]).collect(),
            vec![(ALICE, 1), (ALICE, 2), (ALICE, 3)],
            true,
        ),
    ];
    for (bound, tuning, ticks, kept, busy) in rows {
        let mut rig = Rig::new(tuning);
        let mut call = rig.group(&[ALICE]);
        rig.map(ALICE_SSRC, ALICE);
        rig.map(BOB_SSRC, BOB);
        let receiver = rig.open(options(ReceiveFormat::Opus));
        join_behind_the_bot(&mut call, BOB);
        let media = Arc::clone(&rig.media);
        let lock = busy.then(|| media.dave.lock());
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
        drop(lock);
        let dropped = rig.undecryptable();
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
        assert_eq!(dropped, u64::from(!busy), "{bound}");
    }
}

#[test]
fn a_failure_streak_asks_for_one_recovery_and_a_success_resets_it() {
    let tolerance = ReceiveTuning::default().decrypt_tolerance;
    let mut rig = Rig::dave();
    let mut call = rig.group(&[ALICE]);
    rig.map(ALICE_SSRC, ALICE);
    let _receiver = rig.open(options(ReceiveFormat::Opus));
    let decryptable = call.speak(user(ALICE), &opus(1, 0.3)[0]);
    let mut sequence = 0;
    let mut send = |rig: &mut Rig, frames: Vec<Vec<u8>>| {
        for frame in frames {
            rig.send(ALICE_SSRC, sequence, &frame);
            sequence += 1;
        }
        rig.step();
        rig.recovered()
    };
    let failing = |count: u32| (0..count).map(|index| garbage(index as u16)).collect();
    let refused = (0..tolerance)
        .flat_map(|_| [Vec::new(), vec![0x03]])
        .collect();

    let after_refused = send(&mut rig, refused);
    let dropped = rig.undecryptable();
    let short = send(&mut rig, failing(tolerance - 1));
    let after_a_success = send(&mut rig, vec![decryptable]);
    let short_again = send(&mut rig, failing(tolerance - 1));
    let reached = send(&mut rig, failing(1));
    let within_the_interval = send(&mut rig, failing(tolerance));

    assert_eq!(dropped, 0);
    assert_eq!(
        (after_refused, short, after_a_success, short_again),
        (false, false, false, false)
    );
    assert_eq!((reached, within_the_interval), (true, false));
}

#[test]
fn the_playout_reorders_within_the_jitter_conceals_losses_and_skips_gaps() {
    let gaps: [&[u16]; 12] = [
        &[0],
        &[1],
        &[],
        &[3],
        &[],
        &[],
        &[],
        &[],
        &[],
        &[],
        &[10],
        &[11],
    ];
    let swapped: [&[u16]; 12] = [
        &[0],
        &[1],
        &[3],
        &[2],
        &[],
        &[],
        &[],
        &[],
        &[],
        &[],
        &[],
        &[],
    ];
    let skipped = [
        (0, false),
        (960, false),
        (1_920, true),
        (2_880, false),
        (9_600, false),
        (10_560, false),
    ];
    let in_order = [(0, false), (960, false), (1_920, false), (2_880, false)];
    let pcm16k = pcm(16_000, 1, SampleFormat::S16Le);
    let cases = [
        (ReceiveFormat::Opus, Duration::ZERO, gaps, &skipped[..], 7),
        (pcm16k, Duration::ZERO, gaps, &skipped[..], 7),
        (ReceiveFormat::Opus, FRAME * 2, swapped, &in_order[..], 0),
    ];
    for (format, jitter, schedule, expected, lost) in cases {
        let mut rig = Rig::new(tuning());
        rig.map(ALICE_SSRC, ALICE);
        let receiver = rig.open(ReceiveOptions {
            jitter,
            ..options(format)
        });
        let packets = opus(12, 0.3);
        for sequences in schedule {
            for &sequence in sequences {
                rig.send(ALICE_SSRC, sequence, &packets[usize::from(sequence)]);
            }
            rig.step();
        }

        let items = read(&receiver);
        let played: Vec<(u32, bool)> = heard(&items)
            .into_iter()
            .map(|item| match item {
                Heard::Frame {
                    timestamp: Some(timestamp),
                    lost,
                    ..
                } => (timestamp, lost),
                other => panic!("{other:?}"),
            })
            .collect();
        let lengths: Vec<usize> = data(&items).iter().map(Bytes::len).collect();
        assert_eq!(played, expected, "{format:?} {jitter:?}");
        assert_eq!(receiver.stats().unwrap()[2], ("lost", lost), "{format:?}");
        if format == pcm16k {
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
        } else {
            let silent: Vec<bool> = lengths.iter().map(|&length| length == 0).collect();
            let concealed: Vec<bool> = expected.iter().map(|&(_, lost)| lost).collect();
            assert_eq!(silent, concealed);
        }
    }
}

#[test]
fn the_intake_keeps_only_fresh_heard_packets_of_this_session() {
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

    let arrivals: Vec<Arrival> = rig.media.arrivals.lock().drain(..).collect();
    let shapes: Vec<(u16, bool, bool)> = arrivals
        .iter()
        .map(|arrival| (arrival.sequence, arrival.payload.is_some(), arrival.silence))
        .collect();
    let body = Bytes::from_static(&[0x10, 0xAA, 0, 0]);
    assert_eq!(shapes, [(1, false, true), (2, true, true)]);
    assert_eq!(arrivals[0].extension, None);
    assert_eq!(arrivals[1].extension, Some((rtp::ONE_BYTE_PROFILE, body)));
    assert_eq!(rig.media.transport_failures(), 2);
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
}

#[test]
fn the_intake_forgets_departed_ssrcs_once_unmapped_windows_pile_up() {
    let mut rig = Rig::new(tuning());
    let cipher = Cipher::new(Mode::Aes256GcmRtpSize, &KEY);
    let mut intake = rig.intake();
    let _packets = rig.open(options(ReceiveFormat::Packets));
    rig.map(ALICE_SSRC, ALICE);
    rig.map(BOB_SSRC, BOB);
    let unmapped: Vec<u32> = (2_000..).take(SPARE_WINDOWS - 1).collect();

    let mut send = |nonce, ssrc| {
        let sealing = Sealing::default();
        let mut packet = sfu::seal(&cipher, nonce, ssrc, 0, 0, &SILENCE_FRAME, &sealing);
        intake.rtp(&mut packet);
        let mut windows: Vec<u32> = intake.windows.keys().copied().collect();
        windows.sort_unstable();
        windows
    };
    send(0, ALICE_SSRC);
    send(1, BOB_SSRC);
    rig.info.ssrcs.lock().remove(&ALICE_SSRC);
    let mut piled = Vec::new();
    for (nonce, &ssrc) in (2..).zip(&unmapped) {
        piled = send(nonce, ssrc);
    }
    let swept = send(100, STRANGER_SSRC);

    assert_eq!(piled, [vec![ALICE_SSRC, BOB_SSRC], unmapped].concat());
    assert_eq!(swept, [BOB_SSRC, STRANGER_SSRC]);
}

#[test]
fn a_stall_end_receiver_opened_while_reconnecting_ends_at_once() {
    let mut rig = Rig::new(tuning());
    let ending = ReceiveOptions {
        stall: ReceiveStall::End,
        ..options(ReceiveFormat::Opus)
    };

    rig.media.set_reconnecting(true);
    let during = rig.open(ending.clone());
    let waiting = rig.open(options(ReceiveFormat::Opus));
    rig.media.set_reconnecting(false);
    let after = rig.open(ending);

    assert_eq!(during.ended(), Some(Ended::Reconnecting));
    assert_eq!(waiting.ended(), None);
    assert_eq!(after.ended(), None);
}

#[test]
fn dropping_the_reception_closes_media_and_wakes_the_connection() {
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
fn a_receive_panic_restarts_the_phase_and_keeps_the_media() {
    let mut rig = Rig::new(tuning());
    rig.map(ALICE_SSRC, ALICE);
    let receiver = rig.open(options(ReceiveFormat::Opus));
    let packets = opus(2, 0.3);

    rig.send(ALICE_SSRC, 0, &packets[0]);
    rig.step();
    rig.panic();
    rig.send(ALICE_SSRC, 1, &packets[1]);
    rig.step();

    assert_eq!(
        rig.speech(),
        [
            (ALICE, Speaking::Started),
            (ALICE, Speaking::Stopped(FRAME)),
            (ALICE, Speaking::Started)
        ]
    );
    assert_eq!(data(&read(&receiver)), packets);
    assert!(!rig.media.closed.load(Ordering::Acquire));
}

#[test]
fn a_receive_panic_ends_the_stall_it_reported() {
    let mut rig = Rig::new(tuning());
    let _receiver = rig.open(options(ReceiveFormat::Opus));
    let stale = Cipher::new(Mode::Aes256GcmRtpSize, &[1; 32]);
    let mut intake = rig.intake();
    for sequence in 0..7 {
        let sealing = Sealing::default();
        let nonce = u32::from(sequence);
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
        rig.step();
    }
    let stalled = rig.stalls();
    rig.panic();

    assert_eq!(stalled, [(Direction::Receive, true, STALL)]);
    assert_eq!(
        rig.stalls(),
        [(Direction::Receive, false, STALL + FRAME * 2)]
    );
}

#[test]
fn each_packet_goes_to_the_owner_of_its_ssrc_at_arrival() {
    let mut rig = Rig::new(tuning());
    let receiver = rig.open(options(ReceiveFormat::Opus));
    let packets = opus(4, 0.3);
    let hold = rig.reception.core.tuning.hold;
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
    rig.map(ALICE_SSRC, ALICE);
    rig.step();

    assert_eq!(senders(&after_op_13), [Some(ALICE), Some(ALICE)]);
    let mut owners = senders(&after_a_new_owner);
    owners.sort_unstable();
    assert_eq!(owners, [Some(ALICE), Some(BOB)]);
    assert!(read(&receiver).is_empty());
}

#[test]
fn a_new_transport_resets_stream_state_and_keeps_receivers() {
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
    assert_eq!(receiver.ended(), None);
}

#[test]
fn streams_and_failure_streaks_unheard_for_the_stream_idle_are_forgotten() {
    let tolerance = ReceiveTuning::default().decrypt_tolerance;
    let tuning = ReceiveTuning {
        dave_grace: Duration::ZERO,
        stream_idle: Duration::from_secs(5),
        ..tuning()
    };
    let mut rig = Rig::new(tuning);
    let mut call = rig.group(&[ALICE]);
    rig.map(ALICE_SSRC, ALICE);

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

    assert_eq!(data(&read(&receiver)), packets);
    assert!(!recovered);
}

#[test]
fn a_mix_sums_users_soft_clips_and_continues_through_silence() {
    let mut rig = Rig::new(tuning());
    rig.map(ALICE_SSRC, ALICE);
    rig.map(BOB_SSRC, BOB);
    let receiver = rig.open(options(ReceiveFormat::Mixed(PcmFormat {
        rate: 48_000,
        channels: 2,
        sample: SampleFormat::F32Le,
    })));
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
    rig.run(0, rig.reception.core.epoch);
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
}

#[test]
fn in_band_markers_bracket_each_users_frames_on_the_receivers_timeline() {
    let formats = [
        pcm(16_000, 1, SampleFormat::S16Le),
        ReceiveFormat::Opus,
        ReceiveFormat::Packets,
    ];
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
        let mut packets = opus(9, 0.3);
        packets.push(Bytes::from_static(&SILENCE_FRAME));
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
        assert_eq!(heard.len(), 12, "{format:?} {slip:?}");
        if format == ReceiveFormat::Packets {
            assert_eq!(
                heard[0],
                Heard::Speech {
                    speaking: true,
                    offset: None
                }
            );
            assert_eq!(
                heard[11],
                Heard::Speech {
                    speaking: false,
                    offset: None
                }
            );
            assert!(
                heard[1..11]
                    .iter()
                    .all(|item| matches!(item, Heard::Packet { .. }))
            );
        } else {
            let offsets: Vec<u64> = heard[1..11]
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
                heard[11],
                Heard::Speech {
                    speaking: false,
                    offset: Some(offsets[8] + 960)
                }
            );
            assert!(offsets.windows(2).all(|pair| pair[1] == pair[0] + 960));
            if format != ReceiveFormat::Opus {
                assert!(data(&items).iter().all(|frame| frame.len() == 640));
            }
        }
    }
}

#[test]
fn a_packets_receiver_gets_every_decrypted_packet_in_arrival_order() {
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
        at: rig.reception.core.epoch,
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
}

#[test]
fn release_and_decode_stop_at_the_deadline_and_resume_next_tick() {
    let mut rig = Rig::new(tuning());
    rig.map(ALICE_SSRC, ALICE);
    let receiver = rig.open(options(pcm(48_000, 2, SampleFormat::S16Le)));
    for (sequence, packet) in (0..).zip(&opus(3, 0.3)) {
        rig.send(ALICE_SSRC, sequence, packet);
    }

    rig.run(0, rig.reception.core.epoch);
    let deferred = read(&receiver);
    rig.step();

    assert!(deferred.is_empty());
    assert_eq!(senders(&read(&receiver)), [Some(ALICE); 3]);
}

#[test]
fn the_receive_watchdog_reports_media_that_never_plays_and_its_end() {
    for (cause, closes) in [
        ("undecryptable frames", false),
        ("a stale key", false),
        ("undecryptable frames", true),
    ] {
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
        if closes {
            receiver.close();
        } else {
            rig.send(ALICE_SSRC, 7, &SILENCE_FRAME);
        }
        rig.step();

        assert_eq!(stalls, [(Direction::Receive, true, STALL)], "{cause}");
        assert_eq!(
            rig.stalls(),
            [(Direction::Receive, false, STALL + FRAME * 2)],
            "{cause} {closes}"
        );
        assert!(closes || read(&receiver).len() == 1, "{cause}");
    }
}

#[test]
fn a_speaker_who_goes_quiet_is_not_a_receive_stall() {
    let mut rig = Rig::dave();
    rig.map(ALICE_SSRC, ALICE);
    let receiver = rig.open(only(&[ALICE], ReceiveFormat::Opus));

    rig.send(ALICE_SSRC, 0, &SILENCE_FRAME);
    for _ in 0..10 {
        rig.step();
    }

    assert!(rig.stalls().is_empty());
    assert_eq!(read(&receiver).len(), 1);
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
            rig.open(options(ReceiveFormat::Mixed(PcmFormat { rate: 48_000, channels: 2, sample: SampleFormat::F32Le }))),
        ];
        let valid = opus(4, 0.5);
        let cipher = Cipher::new(Mode::Aes256GcmRtpSize, &KEY);
        let mut intake = rig.intake();
        let ssrcs = [ALICE_SSRC, BOB_SSRC, 0xFFFF_FFFF];
        for (nonce, (which, sequence, timestamp, extension, padding, real, payload)) in (0..).zip(packets) {
            let payload = if real { valid[usize::from(sequence) % valid.len()].to_vec() } else { payload };
            let sealing = Sealing { extension, padding, csrcs: padding, marker: real };
            let mut packet = sfu::seal(&cipher, nonce, ssrcs[which], sequence, timestamp, &payload, &sealing);
            intake.open(&mut packet);
            if nonce % 4 == 0 {
                prop_assert!(rig.run_unguarded(Instant::now(), nonce % 3));
            }
        }
        for _ in 0..3 {
            prop_assert!(rig.run_unguarded(Instant::now(), 0));
        }
        for receiver in &receivers {
            prop_assert!(receiver.read(256).is_ok());
        }
    }
}
