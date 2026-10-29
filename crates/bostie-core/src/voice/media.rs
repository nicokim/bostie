use std::collections::{HashMap, VecDeque};
use std::mem;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU8, AtomicU64, AtomicUsize, Ordering};
use std::time::{Duration, Instant};

use bytes::Bytes;
use parking_lot::Mutex;
use tokio::sync::Notify;
use tracing::debug;

use crate::error::CoreError;
use crate::ids::{ChannelId, GuildId, UserId};
use crate::inbox::{Delivery, Inbox};
use crate::voice::crypto::Cipher;
use crate::voice::dave::{Dave, DaveCounters, DaveInfo, EncryptFailure, SendMode};
use crate::voice::mixer::{Mixed, Mixer};
use crate::voice::pacer::Tick;
use crate::voice::receiver::{Ended, Receiver};
use crate::voice::rtp::{self, HEADER_LEN};
use crate::voice::sink::Sink;
use crate::voice::types::{
    Direction, EncoderSettings, FRAME_SAMPLES, ReceiveFormat, ReceiveTuning, SILENCE_FRAME,
};

pub const SILENCE_TAIL: u8 = 5;

const SAMPLES_PER_TICK: u32 = FRAME_SAMPLES as u32;
const DEFAULT_SPEECH_HANGOVER: Duration = Duration::from_millis(200);
const SENDER_REPORT: u8 = 200;

macro_rules! counters {
    ($counters:ident { $($name:ident),+ $(,)? }) => {
        #[derive(Debug, Default)]
        pub struct $counters {
            $(pub $name: AtomicU64,)+
        }

        impl $counters {
            pub fn snapshot(&self) -> Vec<(&'static str, u64)> {
                vec![$((stringify!($name), self.$name.load(Ordering::Relaxed)),)+]
            }
        }
    };
}

counters!(SendCounters {
    packets_sent,
    bytes_sent,
    passthrough_packets,
    encoded_packets,
    silence_packets,
    held_ticks,
    alone_ticks,
    late_ticks,
    send_errors,
    dave_frames,
    dave_not_ready,
    encrypt_failures,
    dave_busy,
    encode_errors,
    invalid_frames,
    keepalives_sent,
    keepalives_answered,
    packets_received,
    resumes,
    rejoins,
});

counters!(ReceiveCounters {
    transport_failures,
    replays,
    own_echoes,
    unmapped,
    duplicates,
    late_packets,
    undecryptable,
    invalid_payloads,
    invalid_extensions,
    decrypt_busy,
    receive_deferred,
    arrivals_dropped,
    rx_panics,
    rtcp_sender_reports,
});

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct UserCounters {
    pub packets: u64,
    pub lost: u64,
    pub duplicates: u64,
    pub decrypt_failures: u64,
    pub invalid_payloads: u64,
}

impl UserCounters {
    pub fn snapshot(&self) -> Vec<(&'static str, u64)> {
        vec![
            ("packets", self.packets),
            ("lost", self.lost),
            ("duplicates", self.duplicates),
            ("decrypt_failures", self.decrypt_failures),
            ("invalid_payloads", self.invalid_payloads),
        ]
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Arrival {
    pub ssrc: u32,
    pub sequence: u16,
    pub timestamp: u32,
    pub at: Instant,
    pub payload: Option<Bytes>,
    pub silence: bool,
    pub extension: Option<(u16, Bytes)>,
    pub own: bool,
}

pub fn bump(counter: &AtomicU64) {
    counter.fetch_add(1, Ordering::Relaxed);
}

#[derive(Debug)]
pub struct Transport {
    pub generation: u64,
    pub socket: Arc<std::net::UdpSocket>,
    pub ssrc: u32,
    pub cipher: Cipher,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DaveView {
    pub info: DaveInfo,
    pub counters: DaveCounters,
    pub session_built: bool,
}

impl DaveView {
    fn of(dave: &Dave) -> Self {
        Self {
            info: dave.info(),
            counters: dave.counters(),
            session_built: dave.generation() > 0,
        }
    }
}

pub struct Media {
    guild: GuildId,
    inbox: Arc<Inbox>,
    stall_after: Duration,
    arrival_limit: usize,
    pub transport: Mutex<Option<Arc<Transport>>>,
    pub gate: AtomicU8,
    pub dave: Mutex<Dave>,
    pub dave_view: Mutex<DaveView>,
    pub sinks: Mutex<Vec<Arc<Sink>>>,
    pub encoder: Mutex<Option<EncoderSettings>>,
    pub others: AtomicUsize,
    pub counters: SendCounters,
    pub closed: AtomicBool,
    pub lost: Notify,
    pub exhausted: Notify,
    pub recover: Notify,
    pub arrivals: Mutex<VecDeque<Arrival>>,
    pub receivers: Mutex<Vec<Arc<Receiver>>>,
    pub open_receivers: AtomicUsize,
    pub packet_receivers: AtomicUsize,
    pub self_receivers: AtomicUsize,
    pub speech_hangover: AtomicU64,
    pub receive: ReceiveCounters,
    pub users: Mutex<HashMap<UserId, UserCounters>>,
    #[cfg(test)]
    pub faults: Faults,
}

impl Media {
    pub fn new(
        guild: GuildId,
        user: UserId,
        channel: ChannelId,
        inbox: Arc<Inbox>,
        stall_after: Duration,
        receive: ReceiveTuning,
    ) -> Self {
        let dave = Dave::new(user, channel).with_receive(receive);
        let view = DaveView::of(&dave);
        Self {
            guild,
            inbox,
            stall_after,
            arrival_limit: receive.arrivals,
            transport: Mutex::new(None),
            gate: AtomicU8::new(SendMode::Hold as u8),
            dave: Mutex::new(dave),
            dave_view: Mutex::new(view),
            sinks: Mutex::new(Vec::new()),
            encoder: Mutex::new(None),
            others: AtomicUsize::new(0),
            counters: SendCounters::default(),
            closed: AtomicBool::new(false),
            lost: Notify::new(),
            exhausted: Notify::new(),
            recover: Notify::new(),
            arrivals: Mutex::new(VecDeque::new()),
            receivers: Mutex::new(Vec::new()),
            open_receivers: AtomicUsize::new(0),
            packet_receivers: AtomicUsize::new(0),
            self_receivers: AtomicUsize::new(0),
            speech_hangover: AtomicU64::new(nanos(DEFAULT_SPEECH_HANGOVER)),
            receive: ReceiveCounters::default(),
            users: Mutex::new(HashMap::new()),
            #[cfg(test)]
            faults: Faults::default(),
        }
    }

    pub fn guild(&self) -> GuildId {
        self.guild
    }

    pub fn stall_after(&self) -> Duration {
        self.stall_after
    }

    pub fn push(&self, delivery: Delivery) {
        self.inbox.push(delivery);
    }

    pub fn arrive(&self, arrival: Arrival) {
        let mut arrivals = self.arrivals.lock();
        if arrivals.len() >= self.arrival_limit && arrivals.pop_front().is_some() {
            bump(&self.receive.arrivals_dropped);
        }
        arrivals.push_back(arrival);
    }

    pub fn rtcp(&self, packet: &[u8]) {
        if packet.get(1) == Some(&SENDER_REPORT) {
            bump(&self.receive.rtcp_sender_reports);
        }
    }

    pub fn add_receiver(&self, receiver: &Arc<Receiver>) -> bool {
        let mut receivers = self.receivers.lock();
        if self.closed.load(Ordering::Acquire) {
            return false;
        }
        self.count_receiver(receiver, |count| {
            count.fetch_add(1, Ordering::Relaxed);
        });
        receivers.push(Arc::clone(receiver));
        true
    }

    pub fn drop_ended_receivers(&self, open: &mut Vec<Arc<Receiver>>) {
        let mut receivers = self.receivers.lock();
        receivers.retain(|receiver| {
            let ended = receiver.is_closed();
            if ended {
                self.count_receiver(receiver, |count| {
                    count.fetch_sub(1, Ordering::Relaxed);
                });
            }
            !ended
        });
        open.clear();
        open.extend(receivers.iter().cloned());
    }

    pub fn end_receivers(&self, why: Ended) {
        for receiver in self.receivers.lock().iter() {
            receiver.end(why);
        }
    }

    pub fn speech_hangover(&self) -> Duration {
        Duration::from_nanos(self.speech_hangover.load(Ordering::Relaxed))
    }

    pub fn set_speech_hangover(&self, hangover: Duration) {
        self.speech_hangover
            .store(nanos(hangover), Ordering::Relaxed);
    }

    fn count_receiver(&self, receiver: &Receiver, change: impl Fn(&AtomicUsize)) {
        let options = receiver.options();
        change(&self.open_receivers);
        if options.format == ReceiveFormat::Packets {
            change(&self.packet_receivers);
        }
        if options.include_self {
            change(&self.self_receivers);
        }
    }

    pub fn lane(self: &Arc<Self>, encoder: EncoderSettings) -> Result<Lane, CoreError> {
        Ok(Lane {
            media: Arc::clone(self),
            sinks: Vec::new(),
            mixer: Mixer::new(encoder)?,
            rtp: None,
            packet: Vec::new(),
            dave_out: Vec::new(),
            silence_left: 0,
            stall: Stall::default(),
        })
    }

    pub fn install(&self, transport: Option<Arc<Transport>>) {
        let dave = self.dave.lock();
        *self.transport.lock() = transport;
        self.refresh(&dave);
    }

    pub fn begin(&self, dave: Dave, transport: Arc<Transport>) {
        let mut current = self.dave.lock();
        *current = dave;
        *self.transport.lock() = Some(transport);
        self.refresh(&current);
    }

    pub fn with_dave<T>(&self, run: impl FnOnce(&mut Dave) -> T) -> T {
        let mut dave = self.dave.lock();
        let output = run(&mut dave);
        self.refresh(&dave);
        output
    }

    pub fn close(&self) {
        self.closed.store(true, Ordering::Release);
        let sinks = mem::take(&mut *self.sinks.lock());
        for sink in sinks {
            sink.close();
        }
        self.end_receivers(Ended::Disconnected);
    }

    pub fn gate(&self) -> SendMode {
        SendMode::from_byte(self.gate.load(Ordering::Acquire))
    }

    pub fn refresh(&self, dave: &Dave) {
        let mode = if self.transport.lock().is_some() {
            dave.send_mode()
        } else {
            SendMode::Hold
        };
        self.gate.store(mode as u8, Ordering::Release);
        *self.dave_view.lock() = DaveView::of(dave);
    }

    pub fn protocol_version(&self) -> u16 {
        self.dave_view.lock().info.protocol_version
    }

    pub fn receive_state(&self) -> (u16, bool) {
        let view = self.dave_view.lock();
        (view.info.protocol_version, view.session_built)
    }

    pub fn stalled(&self, direction: Direction, stalled: bool, duration: Duration) {
        self.inbox.push(Delivery::VoiceStalled {
            guild: self.guild,
            direction,
            stalled,
            duration,
        });
    }
}

fn nanos(duration: Duration) -> u64 {
    u64::try_from(duration.as_nanos()).unwrap_or(u64::MAX)
}

#[derive(Debug, Clone, Copy)]
struct RtpState {
    generation: u64,
    sequence: u16,
    timestamp: u32,
    nonce: u32,
    exhausted: bool,
}

#[derive(Debug, Default)]
struct Stall {
    since: Option<Instant>,
    reported: bool,
}

pub struct Lane {
    media: Arc<Media>,
    sinks: Vec<Arc<Sink>>,
    mixer: Mixer,
    rtp: Option<RtpState>,
    packet: Vec<u8>,
    dave_out: Vec<u8>,
    silence_left: u8,
    stall: Stall,
}

impl Lane {
    pub fn tick(&mut self, tick: Tick) -> bool {
        if self.media.closed.load(Ordering::Acquire) {
            return false;
        }
        self.media
            .counters
            .late_ticks
            .fetch_add(u64::from(tick.skipped), Ordering::Relaxed);
        if let Some(settings) = self.media.encoder.lock().take()
            && let Err(error) = self.mixer.set_encoder(settings)
        {
            debug!(%error, "the new encoder settings were refused");
        }
        self.snapshot_sinks();
        let transport = self.media.transport.lock().clone();
        match transport {
            None => self.hold(tick.now),
            Some(transport) => {
                self.follow(&transport);
                match self.media.gate() {
                    SendMode::Hold => self.hold(tick.now),
                    SendMode::Discard => self.discard(),
                    SendMode::Plain => self.plain(&transport, tick.now),
                    SendMode::Encrypt => self.encrypted(&transport, tick.now),
                }
                if let Some(rtp) = &mut self.rtp {
                    rtp.timestamp = rtp
                        .timestamp
                        .wrapping_add(SAMPLES_PER_TICK.wrapping_mul(tick.skipped.wrapping_add(1)));
                }
            }
        }
        true
    }

    fn snapshot_sinks(&mut self) {
        {
            let mut sinks = self.media.sinks.lock();
            sinks.retain(|sink| !sink.is_closed());
            self.sinks.clear();
            self.sinks.extend(sinks.iter().cloned());
        }
        self.mixer.retain(&self.sinks);
    }

    fn follow(&mut self, transport: &Transport) {
        if self
            .rtp
            .is_some_and(|rtp| rtp.generation == transport.generation)
        {
            return;
        }
        self.rtp = Some(RtpState {
            generation: transport.generation,
            sequence: fastrand::u16(..),
            timestamp: fastrand::u32(..),
            nonce: first_nonce(&self.media),
            exhausted: false,
        });
    }

    fn hold(&mut self, now: Instant) {
        self.mixer.hold(&self.sinks);
        bump(&self.media.counters.held_ticks);
        self.stalling(now);
    }

    fn discard(&mut self) {
        self.mixer.discard(&self.sinks);
        self.silence_left = 0;
        bump(&self.media.counters.alone_ticks);
    }

    fn plain(&mut self, transport: &Transport, now: Instant) {
        let counters = &self.media.counters;
        let payload: &[u8] = match self.mixer.mix(&self.sinks) {
            Some(mixed) => {
                self.silence_left = SILENCE_TAIL;
                count_mixed(counters, &mixed);
                mixed.packet
            }
            None if self.silence_left > 0 => {
                self.silence_left -= 1;
                bump(&counters.silence_packets);
                &SILENCE_FRAME
            }
            None => return,
        };
        let stamp = self.rtp.map(|rtp| (rtp.sequence, rtp.timestamp));
        let sent = self
            .rtp
            .as_mut()
            .is_some_and(|rtp| send(&self.media, transport, rtp, &mut self.packet, payload));
        if sent {
            tee(&self.media, transport, stamp, payload);
        }
        counters
            .encode_errors
            .store(self.mixer.encode_errors(), Ordering::Relaxed);
        self.after_send(sent, now);
    }

    fn encrypted(&mut self, transport: &Transport, now: Instant) {
        let media = Arc::clone(&self.media);
        let Some(mut dave) = media.dave.try_lock() else {
            bump(&media.counters.dave_busy);
            self.hold(now);
            return;
        };
        match dave.send_mode() {
            SendMode::Encrypt => {}
            SendMode::Plain => {
                drop(dave);
                self.plain(transport, now);
                return;
            }
            SendMode::Discard => {
                drop(dave);
                self.discard();
                return;
            }
            SendMode::Hold => {
                drop(dave);
                self.hold(now);
                return;
            }
        }
        let counters = &media.counters;
        let payload: &[u8] = match self.mixer.mix(&self.sinks) {
            Some(mixed) => {
                self.silence_left = SILENCE_TAIL;
                count_mixed(counters, &mixed);
                mixed.packet
            }
            None if self.silence_left > 0 => {
                self.silence_left -= 1;
                match self.mixer.silence() {
                    Some(silence) => {
                        bump(&counters.silence_packets);
                        silence
                    }
                    None => return,
                }
            }
            None => return,
        };
        let encrypted = forced_failure(&media).map_or_else(
            || {
                self.dave_out.clear();
                dave.encrypt(payload, &mut self.dave_out)
            },
            Err,
        );
        drop(dave);
        if let Err(failure) = encrypted {
            counters
                .encode_errors
                .store(self.mixer.encode_errors(), Ordering::Relaxed);
            match failure {
                EncryptFailure::NotReady => bump(&counters.dave_not_ready),
                EncryptFailure::Failed => bump(&counters.encrypt_failures),
            }
            return;
        }
        let stamp = self.rtp.map(|rtp| (rtp.sequence, rtp.timestamp));
        let sent = self
            .rtp
            .as_mut()
            .is_some_and(|rtp| send(&media, transport, rtp, &mut self.packet, &self.dave_out));
        if sent {
            bump(&counters.dave_frames);
            tee(&media, transport, stamp, payload);
        }
        counters
            .encode_errors
            .store(self.mixer.encode_errors(), Ordering::Relaxed);
        self.after_send(sent, now);
    }

    fn after_send(&mut self, sent: bool, now: Instant) {
        if !sent {
            self.stalling(now);
            return;
        }
        if let Some(since) = self.stall.since.take()
            && mem::take(&mut self.stall.reported)
        {
            self.media
                .stalled(Direction::Send, false, now.saturating_duration_since(since));
        }
    }

    fn stalling(&mut self, now: Instant) {
        let stalled = self
            .sinks
            .iter()
            .any(|sink| sink.queued().is_ok_and(|queued| !queued.is_zero()))
            && (self.media.others.load(Ordering::Relaxed) > 0
                || self.media.protocol_version() == 0);
        if !stalled {
            if !self.stall.reported {
                self.stall.since = None;
            }
            return;
        }
        let since = *self.stall.since.get_or_insert(now);
        let duration = now.saturating_duration_since(since);
        if !self.stall.reported && duration >= self.media.stall_after {
            self.stall.reported = true;
            self.media.stalled(Direction::Send, true, duration);
        }
    }
}

impl Drop for Lane {
    fn drop(&mut self) {
        self.media.closed.store(true, Ordering::Release);
        self.media.lost.notify_one();
    }
}

fn tee(media: &Media, transport: &Transport, stamp: Option<(u16, u32)>, payload: &[u8]) {
    let Some((sequence, timestamp)) = stamp else {
        return;
    };
    if media.self_receivers.load(Ordering::Relaxed) == 0 {
        return;
    }
    media.arrive(Arrival {
        ssrc: transport.ssrc,
        sequence,
        timestamp,
        at: Instant::now(),
        payload: Some(Bytes::copy_from_slice(payload)),
        silence: payload == SILENCE_FRAME,
        extension: None,
        own: true,
    });
}

fn count_mixed(counters: &SendCounters, mixed: &Mixed<'_>) {
    if mixed.passthrough {
        bump(&counters.passthrough_packets);
    } else {
        bump(&counters.encoded_packets);
    }
}

#[cfg(not(test))]
fn forced_failure(_: &Media) -> Option<EncryptFailure> {
    None
}

#[cfg(test)]
fn forced_failure(media: &Media) -> Option<EncryptFailure> {
    media.faults.encrypt.lock().take()
}

#[cfg(not(test))]
fn first_nonce(_: &Media) -> u32 {
    0
}

#[cfg(test)]
fn first_nonce(media: &Media) -> u32 {
    media.faults.nonce_start.load(Ordering::Relaxed)
}

#[cfg(test)]
#[derive(Default)]
pub struct Faults {
    pub nonce_start: std::sync::atomic::AtomicU32,
    pub encrypt: Mutex<Option<EncryptFailure>>,
}

fn send(
    media: &Media,
    transport: &Transport,
    rtp: &mut RtpState,
    packet: &mut Vec<u8>,
    payload: &[u8],
) -> bool {
    if rtp.nonce == u32::MAX {
        if !mem::replace(&mut rtp.exhausted, true) {
            media.exhausted.notify_one();
        }
        return false;
    }
    let mut header = [0; HEADER_LEN];
    rtp::write_header(&mut header, rtp.sequence, rtp.timestamp, transport.ssrc);
    if transport
        .cipher
        .seal(rtp.nonce, &header, payload, packet)
        .is_err()
    {
        bump(&media.counters.send_errors);
        return false;
    }
    rtp.nonce += 1;
    match transport.socket.send(packet) {
        Ok(sent) => {
            rtp.sequence = rtp.sequence.wrapping_add(1);
            bump(&media.counters.packets_sent);
            media
                .counters
                .bytes_sent
                .fetch_add(sent as u64, Ordering::Relaxed);
            true
        }
        Err(error) => {
            debug!(%error, "a voice packet was not sent");
            bump(&media.counters.send_errors);
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use bytes::Bytes;
    use futures_util::FutureExt;

    use super::*;
    use crate::bell;
    use crate::ids::Handle;
    use crate::voice::codec::{FrameEncoder, packet_samples};
    use crate::voice::crypto::Mode;
    use crate::voice::testkit::mls::Call;
    use crate::voice::testkit::sfu::Sfu;
    use crate::voice::types::{
        Application, FRAME, PcmFormat, SampleFormat, SinkFormat, SinkOptions, StallPolicy,
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

    fn tone(frames: usize) -> Vec<u8> {
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

        fn counter(&self, name: &str) -> u64 {
            self.media
                .counters
                .snapshot()
                .into_iter()
                .find(|(counter, _)| *counter == name)
                .unwrap()
                .1
        }

        fn deliveries(&self) -> Vec<Delivery> {
            self.inbox.drain()
        }

        fn packets(&self, count: usize) -> Vec<crate::voice::testkit::sfu::Packet> {
            let packets = self.sfu.wait_packets(count, WAIT);
            assert_eq!(packets.len(), count, "{packets:?}");
            packets
        }

        fn sent(&self, count: u64) {
            assert_eq!(self.counter("packets_sent"), count);
        }
    }

    fn queued_frames(sink: &Sink) -> u64 {
        sink.queued().unwrap().as_micros() as u64 * 48 / 1000
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
            Dave::new(UserId::from(BOT), ChannelId::from(CHANNEL)),
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
        assert_eq!(rig.counter("held_ticks"), 4);
        assert_eq!(rig.counter("packets_sent"), 0);
        rig.connect(&KEY);
        rig.plain();
        rig.tick(1);
        rig.packets(1);
    }

    #[test]
    fn alone_in_a_dave_call_every_sink_drains_one_frame_per_tick_and_nothing_is_sent() {
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
        assert_eq!(rig.counter("alone_ticks"), 6);
        assert_eq!(rig.counter("packets_sent"), 0);
        rig.sent(0);
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
        assert_eq!(rig.counter("silence_packets"), 5);
        assert_eq!(rig.counter("encoded_packets"), 3);
        rig.sent(8);
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
    fn an_encrypting_lane_sends_only_dave_frames_and_a_silence_tail_of_encoded_frames() {
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
        assert_eq!(rig.counter("dave_frames"), 7);
        assert_eq!(rig.counter("passthrough_packets"), 2);
        assert_eq!(rig.counter("silence_packets"), 5);
        rig.sent(7);
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
        rig.sent(0);
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
        for (index, packet) in packets[..6].iter().enumerate() {
            assert_eq!(
                packet.header.sequence,
                first.sequence.wrapping_add(index as u16)
            );
            assert_eq!(
                packet.header.timestamp,
                first.timestamp.wrapping_add(960 * index as u32)
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
        assert_eq!(rig.counter("late_ticks"), 4);
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
        rig.media
            .faults
            .nonce_start
            .store(u32::MAX - 1, Ordering::Relaxed);
        rig.connect(&KEY);
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
        rig.sent(1);
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

        assert_eq!(rig.counter("dave_busy"), 2);
        assert_eq!(rig.counter("held_ticks"), 2);
        assert_eq!(queued_frames(&sink), 2_880);
        rig.sent(0);
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

        assert_eq!(rig.counter("held_ticks"), 2);
        assert_eq!(queued_frames(&sink), 1_920);
        rig.sent(0);
    }

    #[test]
    fn a_frame_dave_cannot_encrypt_is_dropped_and_counted_never_sent_plain() {
        let mut rig = Rig::new();
        rig.connect(&KEY);
        let mut call = encrypting(&mut rig);
        let sink = rig.sink(SinkFormat::Opus, StallPolicy::Hold);
        let sent = opus_packets(3);
        for packet in &sent {
            sink.write(packet).unwrap();
        }

        *rig.media.faults.encrypt.lock() = Some(EncryptFailure::NotReady);
        rig.tick(1);
        *rig.media.faults.encrypt.lock() = Some(EncryptFailure::Failed);
        rig.tick(1);
        rig.tick(1);

        let packets = rig.packets(1);
        assert_eq!(decrypt(&mut call, &packets[0].payload), sent[2].to_vec());
        assert_eq!(rig.counter("dave_not_ready"), 1);
        assert_eq!(rig.counter("encrypt_failures"), 1);
        assert_eq!(queued_frames(&sink), 0);
    }

    #[test]
    fn the_watchdog_reports_a_stall_with_queued_audio_and_a_listener_then_its_end() {
        let mut rig = Rig::new();
        let sink = rig.pcm_sink(StallPolicy::Hold);
        sink.write(&tone(FRAME_SAMPLES * 20)).unwrap();
        rig.media.others.store(1, Ordering::Relaxed);

        rig.tick(5);
        assert!(stalls(&rig.deliveries()).is_empty());
        rig.tick(1);
        assert_eq!(stalls(&rig.deliveries()), [(true, STALL)]);
        rig.tick(3);
        assert!(stalls(&rig.deliveries()).is_empty());
        rig.connect(&KEY);
        rig.plain();
        rig.tick(1);

        assert_eq!(stalls(&rig.deliveries()), [(false, FRAME * 9)]);
        rig.tick(1);
        assert!(stalls(&rig.deliveries()).is_empty());
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
    fn closed_sinks_leave_the_lane_snapshot() {
        let mut rig = Rig::new();
        let first = rig.pcm_sink(StallPolicy::Hold);
        let _second = rig.pcm_sink(StallPolicy::Hold);
        first.close();

        rig.tick(1);

        assert_eq!(rig.media.sinks.lock().len(), 1);
        assert_eq!(rig.lane.sinks.len(), 1);
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
        rig.media
            .faults
            .nonce_start
            .store(u32::MAX - 3, Ordering::Relaxed);
        let sink = rig.pcm_sink(StallPolicy::Hold);
        sink.write(&tone(FRAME_SAMPLES * 2)).unwrap();
        rig.tick(2);
        let held = teed(&rig);
        rig.connect(&KEY);
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
        rig.sent(3);

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
        assert_eq!(media.receive.arrivals_dropped.load(Ordering::Relaxed), 1);
    }

    #[test]
    fn send_counters_name_every_key_of_the_stats() {
        let names: Vec<&str> = SendCounters::default()
            .snapshot()
            .into_iter()
            .map(|(name, _)| name)
            .collect();

        assert_eq!(
            names,
            [
                "packets_sent",
                "bytes_sent",
                "passthrough_packets",
                "encoded_packets",
                "silence_packets",
                "held_ticks",
                "alone_ticks",
                "late_ticks",
                "send_errors",
                "dave_frames",
                "dave_not_ready",
                "encrypt_failures",
                "dave_busy",
                "encode_errors",
                "invalid_frames",
                "keepalives_sent",
                "keepalives_answered",
                "packets_received",
                "resumes",
                "rejoins",
            ]
        );
    }
}
