use futures_util::FutureExt;

use super::*;
use crate::bell;
use crate::ids::Handle;
use crate::voice::codec::{FrameEncoder, packet_samples};
use crate::voice::crypto::Mode;
use crate::voice::testkit::mls::Call;
use crate::voice::testkit::sfu::{Packet, Sfu};
use crate::voice::types::{
    Application, FRAME, FRAME_SAMPLES, PcmFormat, SampleFormat, SinkFormat, SinkOptions,
    StallPolicy,
};

const GUILD: u64 = 10;
const BOT: u64 = 100;
const PEER: u64 = 200;
const CHANNEL: u64 = 900;
const KEY: [u8; 32] = [3; 32];
const OTHER_KEY: [u8; 32] = [4; 32];
const SSRC: u32 = 77;
const WAIT: Duration = Duration::from_secs(5);
const STALL: Duration = Duration::from_millis(100);

fn settings() -> EncoderSettings {
    EncoderSettings {
        bitrate: 64_000,
        application: Application::Audio,
        fec: true,
        packet_loss: 10,
        complexity: 10,
    }
}

fn pcm_format() -> PcmFormat {
    PcmFormat {
        rate: 48_000,
        channels: 2,
        sample: SampleFormat::S16Le,
    }
}

#[expect(clippy::cast_precision_loss, clippy::cast_possible_truncation)]
pub(crate) fn tone(frames: usize) -> Vec<u8> {
    (0..frames * 2)
        .flat_map(|index| {
            let sample = ((index / 2) as f32 * 0.05).sin() * 8_000.0;
            (sample as i16).to_le_bytes()
        })
        .collect()
}

fn opus_packets(frames: usize) -> Vec<Bytes> {
    let mut encoder = FrameEncoder::new(pcm_format(), settings()).unwrap();
    let packets = encoder.encode(&tone(frames * FRAME_SAMPLES));
    assert_eq!(packets.len(), frames);
    packets
}

struct Rig {
    media: Arc<Media>,
    lane: Lane,
    sfu: Sfu,
    inbox: Arc<Inbox>,
    _bell: bell::BellReceiver,
    now: Instant,
    generation: u64,
    next_handle: u64,
}

impl Rig {
    fn new() -> Self {
        crate::init_test_tracing();
        let (bell, receiver) = bell::pair().unwrap();
        let inbox = Arc::new(Inbox::new(bell));
        let media = Arc::new(Media::new(
            GuildId::from(GUILD),
            UserId::from(BOT),
            ChannelId::from(CHANNEL),
            Arc::clone(&inbox),
            STALL,
            ReceiveTuning::default(),
        ));
        let lane = media.lane(settings()).unwrap();
        Self {
            media,
            lane,
            sfu: Sfu::start(),
            inbox,
            _bell: receiver,
            now: Instant::now(),
            generation: 0,
            next_handle: 1,
        }
    }

    fn connect(&mut self, key: &[u8; 32]) {
        self.sfu.key(Mode::Aes256GcmRtpSize, key);
        let socket = std::net::UdpSocket::bind("127.0.0.1:0").unwrap();
        socket.connect(self.sfu.address()).unwrap();
        socket.set_nonblocking(true).unwrap();
        self.generation += 1;
        self.media.install(Some(Arc::new(Transport {
            generation: self.generation,
            socket: Arc::new(socket),
            ssrc: SSRC,
            cipher: Cipher::new(Mode::Aes256GcmRtpSize, key),
        })));
    }

    fn nonce_at(&mut self, nonce: u32) {
        self.lane.rtp = Some(RtpState {
            generation: self.generation,
            sequence: 0,
            timestamp: 0,
            nonce,
            exhausted: false,
        });
    }

    fn plain(&mut self) {
        self.media.with_dave(|dave| {
            let mut out = Vec::new();
            dave.start(0, &mut out).unwrap();
        });
        assert_eq!(self.media.gate(), SendMode::Plain);
    }

    fn sink(&mut self, format: SinkFormat, stall: StallPolicy) -> Arc<Sink> {
        let options = SinkOptions {
            format,
            buffer: Duration::from_secs(10),
            volume: 1.0,
            stall,
        };
        let sink = Arc::new(
            Sink::new(
                Handle::from(self.next_handle),
                options,
                Arc::clone(&self.inbox),
            )
            .unwrap(),
        );
        self.next_handle += 1;
        self.media.sinks.lock().push(Arc::clone(&sink));
        sink
    }

    fn pcm_sink(&mut self, stall: StallPolicy) -> Arc<Sink> {
        self.sink(SinkFormat::Pcm(pcm_format()), stall)
    }

    fn tick(&mut self, n: u32) {
        self.tick_skipping(n, 0);
    }

    fn tick_skipping(&mut self, n: u32, skipped: u32) {
        for _ in 0..n {
            self.now += FRAME * (1 + skipped);
            assert!(self.lane.tick(Tick {
                now: self.now,
                skipped,
            }));
        }
    }

    fn deliveries(&self) -> Vec<Delivery> {
        self.inbox.drain()
    }

    fn packets(&self, count: usize) -> Vec<Packet> {
        let transport = self.media.transport.lock().clone().unwrap();
        let packets = self.sfu.flush(&transport.socket, WAIT);
        assert_eq!(packets.len(), count, "{packets:?}");
        packets
    }
}

pub(crate) fn queued_frames(sink: &Sink) -> u64 {
    u64::try_from(sink.queued().unwrap().as_micros()).unwrap() * 48 / 1000
}

fn markers(deliveries: &[Delivery]) -> Vec<(u64, bool)> {
    deliveries
        .iter()
        .filter_map(|delivery| match delivery {
            Delivery::VoiceMarker { marker, played, .. } => Some((*marker, *played)),
            _ => None,
        })
        .collect()
}

fn stalls(deliveries: &[Delivery]) -> Vec<(bool, Duration)> {
    deliveries
        .iter()
        .filter_map(|delivery| match delivery {
            Delivery::VoiceStalled {
                stalled, duration, ..
            } => Some((*stalled, *duration)),
            _ => None,
        })
        .collect()
}

fn encrypting(rig: &mut Rig) -> Call {
    let mut call = Call::new(ChannelId::from(CHANNEL), 1);
    call.join(UserId::from(BOT));
    call.run();
    call.join(UserId::from(PEER));
    call.run();
    let dave = mem::replace(
        call.dave_mut(UserId::from(BOT)),
        Dave::new(
            UserId::from(BOT),
            ChannelId::from(CHANNEL),
            &ReceiveTuning::default(),
        ),
    );
    assert_eq!(dave.send_mode(), SendMode::Encrypt);
    rig.media.with_dave(|current| *current = dave);
    rig.media.others.store(1, Ordering::Relaxed);
    assert_eq!(rig.media.gate(), SendMode::Encrypt);
    call
}

fn decrypt(call: &mut Call, payload: &[u8]) -> Vec<u8> {
    let mut frame = vec![0; payload.len()];
    let len = call
        .dave_mut(UserId::from(PEER))
        .decrypt(UserId::from(BOT), payload, &mut frame)
        .unwrap();
    frame.truncate(len);
    frame
}

#[test]
fn a_held_gate_keeps_hold_sinks_and_drains_drop_sinks() {
    let mut rig = Rig::new();
    let hold = rig.pcm_sink(StallPolicy::Hold);
    let drop_sink = rig.pcm_sink(StallPolicy::Drop);
    hold.write(&tone(FRAME_SAMPLES * 10)).unwrap();
    drop_sink.write(&tone(FRAME_SAMPLES * 10)).unwrap();

    rig.tick(4);

    assert_eq!(queued_frames(&hold), 9_600);
    assert_eq!(queued_frames(&drop_sink), 6 * 960);
    assert_eq!(hold.stats().unwrap().held_ticks, 4);
    rig.connect(&KEY);
    rig.plain();
    rig.tick(1);
    rig.packets(1);
}

#[test]
fn alone_in_a_dave_call_sinks_drain_and_nothing_is_sent() {
    let mut rig = Rig::new();
    rig.connect(&KEY);
    rig.media.with_dave(|dave| {
        let mut out = Vec::new();
        dave.start(1, &mut out).unwrap();
    });
    assert_eq!(rig.media.gate(), SendMode::Discard);
    let pcm = rig.pcm_sink(StallPolicy::Hold);
    let opus = rig.sink(SinkFormat::Opus, StallPolicy::Hold);
    pcm.write(&tone(FRAME_SAMPLES * 3)).unwrap();
    let (pcm_marker, _) = pcm.mark().unwrap();
    for packet in opus_packets(3) {
        opus.write(&packet).unwrap();
    }
    let (opus_marker, _) = opus.mark().unwrap();

    rig.tick(2);
    assert_eq!(queued_frames(&pcm), 960);
    assert_eq!(queued_frames(&opus), 960);
    rig.tick(4);

    assert_eq!(queued_frames(&pcm), 0);
    let mut resolved = markers(&rig.deliveries());
    resolved.sort_unstable();
    assert_eq!(resolved, [(pcm_marker, true), (opus_marker, true)]);
    rig.packets(0);
}

#[test]
fn plain_audio_ends_with_exactly_five_silence_frames() {
    let mut rig = Rig::new();
    rig.connect(&KEY);
    rig.plain();
    let sink = rig.pcm_sink(StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 3)).unwrap();

    rig.tick(12);

    let packets = rig.packets(8);
    assert!(
        packets[..3]
            .iter()
            .all(|packet| packet.payload != SILENCE_FRAME
                && packet_samples(&packet.payload) == Some(960))
    );
    assert_eq!(rig.sfu.silence_runs(), [5]);
}

#[test]
fn new_audio_cancels_a_running_silence_tail() {
    let mut rig = Rig::new();
    rig.connect(&KEY);
    rig.plain();
    let sink = rig.pcm_sink(StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES)).unwrap();
    rig.tick(3);

    sink.write(&tone(FRAME_SAMPLES)).unwrap();
    rig.tick(8);

    rig.packets(9);
    assert_eq!(rig.sfu.silence_runs(), [2, 5]);
}

#[test]
fn an_encrypting_lane_sends_dave_frames_and_an_encoded_silence_tail() {
    let mut rig = Rig::new();
    rig.connect(&KEY);
    let mut call = encrypting(&mut rig);
    let sink = rig.sink(SinkFormat::Opus, StallPolicy::Hold);
    let sent = opus_packets(2);
    for packet in &sent {
        sink.write(packet).unwrap();
    }

    rig.tick(9);

    let packets = rig.packets(7);
    assert!(packets.iter().all(|packet| packet.payload != SILENCE_FRAME));
    let frames: Vec<Vec<u8>> = packets
        .iter()
        .map(|packet| decrypt(&mut call, &packet.payload))
        .collect();
    assert_eq!(
        &frames[..2],
        &sent.iter().map(|p| p.to_vec()).collect::<Vec<_>>()[..]
    );
    for silence in &frames[2..] {
        assert_ne!(silence.as_slice(), SILENCE_FRAME);
        assert_eq!(packet_samples(silence), Some(960));
    }
    assert_eq!(sink.stats().unwrap().passthrough_packets, 2);
}

#[test]
fn a_held_gate_sends_nothing_and_a_plain_gate_sends_opus() {
    let mut rig = Rig::new();
    rig.connect(&KEY);
    let sink = rig.pcm_sink(StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 4)).unwrap();
    assert_eq!(rig.media.gate(), SendMode::Plain);
    rig.media
        .gate
        .store(SendMode::Hold as u8, Ordering::Release);

    rig.tick(2);
    rig.packets(0);
    rig.plain();
    rig.tick(2);

    let packets = rig.packets(2);
    assert!(
        packets
            .iter()
            .all(|packet| packet_samples(&packet.payload) == Some(960))
    );
    assert_eq!(queued_frames(&sink), 1_920);
}

#[test]
fn rtp_sequence_counts_packets_and_the_timestamp_counts_every_tick() {
    let mut rig = Rig::new();
    rig.connect(&KEY);
    rig.plain();
    let sink = rig.pcm_sink(StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES)).unwrap();
    rig.tick(1);
    rig.tick(6);
    sink.write(&tone(FRAME_SAMPLES)).unwrap();
    rig.tick_skipping(1, 4);
    sink.write(&tone(FRAME_SAMPLES)).unwrap();
    rig.tick(1);

    let packets = rig.packets(8);
    let first = packets[0].header;
    for (index, packet) in (0_u16..).zip(&packets[..6]) {
        assert_eq!(packet.header.sequence, first.sequence.wrapping_add(index));
        assert_eq!(
            packet.header.timestamp,
            first.timestamp.wrapping_add(960 * u32::from(index))
        );
    }
    assert_eq!(
        packets[6].header.timestamp,
        first.timestamp.wrapping_add(960 * 7)
    );
    assert_eq!(
        packets[7].header.timestamp,
        first.timestamp.wrapping_add(960 * (7 + 5))
    );
    assert_eq!(packets[7].header.sequence, first.sequence.wrapping_add(7));
}

#[test]
fn the_nonce_continues_on_one_transport_and_restarts_with_a_new_one() {
    let mut rig = Rig::new();
    rig.connect(&KEY);
    rig.plain();
    let sink = rig.pcm_sink(StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 6)).unwrap();
    rig.tick(3);
    rig.media.install(None);
    rig.tick(1);
    let current = rig.media.transport.lock().clone();
    assert!(current.is_none());
    rig.connect(&OTHER_KEY);
    rig.plain();
    rig.tick(2);

    let packets = rig.packets(5);
    let nonces: Vec<(usize, u32)> = packets.iter().map(|p| (p.key, p.nonce)).collect();
    assert_eq!(nonces, [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1)]);
    assert_eq!(
        packets[4].header.sequence,
        packets[3].header.sequence.wrapping_add(1)
    );
}

#[test]
fn a_nonce_about_to_wrap_stops_sending_and_asks_for_a_new_key_once() {
    let mut rig = Rig::new();
    rig.connect(&KEY);
    rig.nonce_at(u32::MAX - 1);
    rig.plain();
    let sink = rig.pcm_sink(StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 4)).unwrap();

    rig.tick(2);
    let asked = rig.media.exhausted.notified().now_or_never();
    rig.tick(2);

    let packets = rig.packets(1);
    assert_eq!(packets[0].nonce, u32::MAX - 1);
    assert!(asked.is_some());
    assert!(rig.media.exhausted.notified().now_or_never().is_none());
}

#[test]
fn a_frame_dave_cannot_encrypt_is_dropped_and_never_sent_plain() {
    let mut rig = Rig::new();
    rig.connect(&KEY);
    rig.nonce_at(0);
    let sink = rig.pcm_sink(StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 2)).unwrap();
    let mut dave = Dave::new(
        UserId::from(BOT),
        ChannelId::from(CHANNEL),
        &ReceiveTuning::default(),
    );
    dave.start(1, &mut Vec::new()).unwrap();
    let dave = Mutex::new(dave);
    let transport = rig.media.transport.lock().clone().unwrap();

    rig.lane.snapshot_sinks();
    rig.lane.send_frame(&transport, rig.now, Some(dave.lock()));

    rig.packets(0);
    assert_eq!(queued_frames(&sink), 960);
}

#[test]
fn a_busy_dave_lock_holds_the_tick_and_keeps_the_audio() {
    let mut rig = Rig::new();
    rig.connect(&KEY);
    let _call = encrypting(&mut rig);
    let sink = rig.pcm_sink(StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 3)).unwrap();

    let media = Arc::clone(&rig.media);
    let guard = media.dave.lock();
    rig.tick(2);
    drop(guard);

    assert_eq!(sink.stats().unwrap().held_ticks, 2);
    assert_eq!(queued_frames(&sink), 2_880);
    rig.packets(0);
    rig.tick(1);
    rig.packets(1);
}

#[test]
fn a_stale_encrypt_gate_is_rechecked_under_the_lock_and_held() {
    let mut rig = Rig::new();
    rig.connect(&KEY);
    rig.media.with_dave(|dave| {
        let mut out = Vec::new();
        dave.start(1, &mut out).unwrap();
        dave.clients_connect(&[UserId::from(PEER)]);
    });
    assert_eq!(rig.media.gate(), SendMode::Hold);
    rig.media
        .gate
        .store(SendMode::Encrypt as u8, Ordering::Release);
    let sink = rig.pcm_sink(StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 2)).unwrap();

    rig.tick(2);

    assert_eq!(sink.stats().unwrap().held_ticks, 2);
    assert_eq!(queued_frames(&sink), 1_920);
    rig.packets(0);
}

#[test]
fn the_watchdog_reports_a_stall_and_its_end() {
    for (others, version) in [(1, 1), (0, 0)] {
        let mut rig = Rig::new();
        let sink = rig.pcm_sink(StallPolicy::Hold);
        sink.write(&tone(FRAME_SAMPLES * 20)).unwrap();
        rig.media.others.store(others, Ordering::Relaxed);
        rig.media.with_dave(|dave| {
            let mut out = Vec::new();
            dave.start(version, &mut out).unwrap();
        });

        rig.tick(5);
        assert!(stalls(&rig.deliveries()).is_empty());
        rig.tick(1);
        assert_eq!(stalls(&rig.deliveries()), [(true, STALL)], "{others}");
        rig.tick(3);
        assert!(stalls(&rig.deliveries()).is_empty());
        rig.connect(&KEY);
        rig.plain();
        rig.tick(1);

        assert_eq!(stalls(&rig.deliveries()), [(false, FRAME * 9)]);
        rig.tick(1);
        assert!(stalls(&rig.deliveries()).is_empty());
    }
}

#[test]
fn a_reported_stall_stays_reported_while_it_eases_until_it_resumes() {
    let start = Instant::now();
    let mut stall = Stall::default();
    stall.press(start, STALL);
    stall.ease();
    let restarted = stall.press(start + STALL, STALL);

    let reported = stall.press(start + STALL * 2, STALL);
    stall.ease();

    assert_eq!((restarted, reported), (None, Some(STALL)));
    assert!(stall.reported());
    assert_eq!(stall.resume(start + STALL * 3), Some(STALL * 2));
    assert_eq!(stall.resume(start + STALL * 4), None);
}

#[test]
fn the_watchdog_stays_quiet_without_queued_audio_or_alone_in_a_dave_call() {
    let mut rig = Rig::new();
    rig.media.others.store(1, Ordering::Relaxed);
    rig.tick(20);
    assert!(stalls(&rig.deliveries()).is_empty());

    rig.media.with_dave(|dave| {
        let mut out = Vec::new();
        dave.start(1, &mut out).unwrap();
    });
    rig.media.others.store(0, Ordering::Relaxed);
    let sink = rig.pcm_sink(StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 40)).unwrap();
    rig.tick(20);
    assert!(stalls(&rig.deliveries()).is_empty());

    rig.connect(&KEY);
    assert_eq!(rig.media.gate(), SendMode::Discard);
    rig.tick(20);
    assert!(stalls(&rig.deliveries()).is_empty());
}

#[test]
fn dropping_the_lane_marks_the_media_closed_and_wakes_the_connection() {
    let rig = Rig::new();
    let media = Arc::clone(&rig.media);

    drop(rig);

    assert!(media.closed.load(Ordering::Acquire));
    assert!(media.lost.notified().now_or_never().is_some());
}

#[test]
fn a_closed_media_makes_the_lane_leave_and_closes_its_sinks() {
    let mut rig = Rig::new();
    let sink = rig.pcm_sink(StallPolicy::Hold);

    rig.media.close();

    assert!(sink.is_closed());
    assert!(!rig.lane.tick(Tick {
        now: rig.now,
        skipped: 0,
    }));
}

#[test]
fn a_pending_encoder_change_applies_on_the_next_tick() {
    let mut rig = Rig::new();
    rig.connect(&KEY);
    rig.plain();
    let sink = rig.pcm_sink(StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 2)).unwrap();
    rig.tick(1);
    *rig.media.encoder.lock() = Some(EncoderSettings {
        bitrate: 8_000,
        ..settings()
    });
    rig.tick(1);

    let packets = rig.packets(2);
    assert!(rig.media.encoder.lock().is_none());
    assert!(packets[1].payload.len() < packets[0].payload.len());
}

#[test]
fn include_self_tees_exactly_the_packets_sent() {
    let mut rig = Rig::new();
    let teed = |rig: &Rig| -> Vec<(u32, u16, u32, Vec<u8>, bool)> {
        rig.media
            .arrivals
            .lock()
            .drain(..)
            .map(|arrival| {
                let payload = arrival.payload.unwrap().to_vec();
                (
                    arrival.ssrc,
                    arrival.sequence,
                    arrival.timestamp,
                    payload,
                    arrival.own,
                )
            })
            .collect()
    };
    rig.media.self_receivers.store(1, Ordering::Relaxed);
    let sink = rig.pcm_sink(StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 2)).unwrap();
    rig.tick(2);
    let held = teed(&rig);
    rig.connect(&KEY);
    rig.nonce_at(u32::MAX - 3);
    rig.media.with_dave(|dave| {
        let mut out = Vec::new();
        dave.start(1, &mut out).unwrap();
    });
    rig.tick(2);
    let alone = teed(&rig);
    rig.plain();
    sink.write(&tone(FRAME_SAMPLES * 3)).unwrap();
    rig.tick(5);

    let sent: Vec<(u32, u16, u32, Vec<u8>, bool)> = rig
        .packets(3)
        .into_iter()
        .map(|packet| {
            let header = packet.header;
            (
                header.ssrc,
                header.sequence,
                header.timestamp,
                packet.payload,
                true,
            )
        })
        .collect();
    assert!(held.is_empty());
    assert!(alone.is_empty());
    assert_eq!(teed(&rig), sent);

    let mut rig = Rig::new();
    rig.connect(&KEY);
    let mut call = encrypting(&mut rig);
    rig.media.self_receivers.store(1, Ordering::Relaxed);
    let sink = rig.sink(SinkFormat::Opus, StallPolicy::Hold);
    let opus = opus_packets(2);
    for packet in &opus {
        sink.write(packet).unwrap();
    }
    rig.tick(2);

    let sent = rig.packets(2);
    let teed = teed(&rig);
    for ((packet, tee), opus) in sent.iter().zip(&teed).zip(&opus) {
        assert_eq!(decrypt(&mut call, &packet.payload), opus.to_vec());
        assert_eq!(tee.3, opus.to_vec());
        assert_eq!(
            (tee.1, tee.2),
            (packet.header.sequence, packet.header.timestamp)
        );
    }
    assert_eq!(teed.len(), 2);
}

#[test]
fn the_arrival_queue_drops_the_oldest_beyond_its_bound() {
    let (bell, _receiver) = bell::pair().unwrap();
    let media = Media::new(
        GuildId::from(GUILD),
        UserId::from(BOT),
        ChannelId::from(CHANNEL),
        Arc::new(Inbox::new(bell)),
        STALL,
        ReceiveTuning {
            arrivals: 2,
            ..ReceiveTuning::default()
        },
    );

    for sequence in 0..3 {
        media.arrive(Arrival {
            ssrc: SSRC,
            sequence,
            timestamp: 0,
            at: Instant::now(),
            payload: None,
            silence: false,
            extension: None,
            own: false,
        });
    }

    let kept: Vec<u16> = media
        .arrivals
        .lock()
        .iter()
        .map(|arrival| arrival.sequence)
        .collect();
    assert_eq!(kept, [1, 2]);
}
