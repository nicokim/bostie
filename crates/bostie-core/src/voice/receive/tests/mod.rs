use std::collections::HashSet;
use std::net::UdpSocket;

use futures_util::FutureExt;
use proptest::prelude::*;

use super::*;
use crate::ids::{ChannelId, Handle, ShardId};
use crate::voice::codec::FrameEncoder;
use crate::voice::dave::{Outbound, SendMode};
use crate::voice::media::Transport;
use crate::voice::receive::receiver::{Ended, Item, ReceiveRead, Receiver};
use crate::voice::send::sink::tests::Fixture;
use crate::voice::testkit::mls::{Call, Signal};
use crate::voice::testkit::sfu::{self, Sealing};
use crate::voice::transport::crypto::{Cipher, Mode};
use crate::voice::transport::rtp::{self, Kind};
use crate::voice::types::{
    Application, Bandwidth, DaveOp, EncoderSettings, FRAME, Overflow, PcmFormat, ReceiveFormat,
    ReceiveOptions, ReceiveStall, SampleFormat, SignalType,
};

mod decrypting;
mod marks;
mod timeline;

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
        bandwidth: Bandwidth::Auto,
        signal: SignalType::Auto,
    };
    #[expect(clippy::cast_precision_loss, clippy::cast_possible_truncation)]
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
        assert!(
            receiver.opened() <= self.now,
            "the rig took longer than its clock allows to open a receiver"
        );
        assert!(self.media.add_receiver(&receiver));
        receiver
    }

    fn intake(&self) -> Intake {
        Intake::new(
            Arc::clone(&self.media),
            Arc::clone(&self.info),
            Arc::clone(&self.socket),
            user(BOT),
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
        self.media.arrive(|_| self.arrival(ssrc, sequence, payload));
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
            .get_or_insert_with(|| Box::new(ReceptionState::new(core)))
            .run(now, skipped, now + FRAME)
    }

    fn undecryptable(&self) -> u64 {
        self.reception
            .state
            .as_ref()
            .map_or(0, |state| state.hold.undecryptable.total)
    }

    fn recovered(&self) -> bool {
        self.media.recovery_requested().now_or_never().is_some()
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

    fn marks(&self) -> Vec<(u64, bool)> {
        self.fixture
            .inbox
            .drain()
            .into_iter()
            .filter_map(|delivery| match delivery {
                Delivery::VoiceMarker { marker, played, .. } => Some((marker, played)),
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

    fn group(&self, peers: &[u64]) -> Call {
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
        assert_eq!(self.media.dave().send_mode(), SendMode::Encrypt);
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
fn the_intake_keeps_only_fresh_heard_packets_of_this_session() {
    let mut rig = Rig::new(tuning());
    let cipher = Cipher::new(Mode::Aes256GcmRtpSize, &KEY);
    let stranger = Cipher::new(Mode::Aes256GcmRtpSize, &[1; 32]);
    let other_socket = Arc::new(UdpSocket::bind("127.0.0.1:0").unwrap());
    let mut stale = Intake::new(
        Arc::clone(&rig.media),
        Arc::clone(&rig.info),
        other_socket,
        user(BOT),
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

    stale.receive(Kind::Rtp, &mut packet(&cipher, 0, ALICE_SSRC, 0, &tagged));
    intake.receive(Kind::Rtp, &mut packet(&cipher, 1, ALICE_SSRC, 1, &tagged));
    rig.media.set_speech_activity(false);
    intake.receive(Kind::Rtp, &mut packet(&cipher, 2, ALICE_SSRC, 2, &tagged));
    intake.receive(Kind::Rtp, &mut packet(&stranger, 5, ALICE_SSRC, 6, &tagged));
    let _packets = rig.open(options(ReceiveFormat::Packets));
    intake.receive(Kind::Rtp, &mut packet(&cipher, 2, ALICE_SSRC, 2, &tagged));
    intake.receive(Kind::Rtp, &mut packet(&cipher, 2, ALICE_SSRC, 3, &tagged));
    intake.receive(Kind::Rtp, &mut packet(&cipher, 3, BOT_SSRC, 4, &tagged));
    intake.receive(Kind::Rtp, &mut packet(&stranger, 4, ALICE_SSRC, 5, &tagged));

    let mut arrivals = VecDeque::new();
    rig.media.take_arrivals(&mut arrivals);
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
        intake.receive(Kind::Rtp, &mut packet);
    }

    let mut arrivals = VecDeque::new();
    rig.media.take_arrivals(&mut arrivals);
    assert_eq!(arrivals.len(), 2);
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

    assert!(media.is_closed());
    assert!(media.lost().now_or_never().is_some());
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
    assert!(!rig.media.is_closed());
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
        intake.receive(Kind::Rtp, &mut packet);
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
    let busy = media.dave();
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

fn mixed() -> ReceiveFormat {
    ReceiveFormat::Mixed(PcmFormat {
        rate: 48_000,
        channels: 2,
        sample: SampleFormat::F32Le,
    })
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
                intake.receive(Kind::Rtp, &mut packet);
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
    fn arrivals_with_arbitrary_fields_never_panic_the_receive_path(
        arrivals in proptest::collection::vec(
            (
                0..3_usize,
                any::<u16>(),
                any::<u32>(),
                proptest::option::of((
                    prop_oneof![Just(rtp::ONE_BYTE_PROFILE), Just(0x1000), any::<u16>()],
                    proptest::collection::vec(any::<u8>(), 0..12),
                )),
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
        let ssrcs = [ALICE_SSRC, BOB_SSRC, 0xFFFF_FFFF];
        for (index, (which, sequence, timestamp, extension, real, payload)) in (0_u32..).zip(arrivals) {
            let payload = if real { valid[usize::from(sequence) % valid.len()].to_vec() } else { payload };
            rig.media.arrive(|at| Arrival {
                timestamp,
                at,
                extension: extension.map(|(profile, body)| (profile, Bytes::from(body))),
                ..rig.arrival(ssrcs[which], sequence, &payload)
            });
            if index % 4 == 0 {
                prop_assert!(rig.run_unguarded(Instant::now(), index % 3));
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
