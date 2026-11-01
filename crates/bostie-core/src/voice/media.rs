use std::collections::VecDeque;
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
use crate::voice::dave::{Dave, DaveInfo, EncryptFailure, SendMode};
use crate::voice::mixer::{Mixed, Mixer};
use crate::voice::nanos;
use crate::voice::pacer::Tick;
use crate::voice::receiver::{Ended, Receiver};
use crate::voice::rtp::{self, HEADER_LEN};
use crate::voice::sink::Sink;
use crate::voice::types::{
    Direction, EncoderSettings, FRAME_SAMPLES, ReceiveFormat, ReceiveStall, ReceiveTuning,
    SILENCE_FRAME,
};

pub const SILENCE_TAIL: u8 = 5;

const SAMPLES_PER_TICK: u32 = FRAME_SAMPLES as u32;
const DEFAULT_SPEECH_HANGOVER: Duration = Duration::from_millis(200);

macro_rules! counters {
    ($counters:ident { $($name:ident),+ $(,)? }) => {
        #[derive(Debug, Default)]
        pub struct $counters {
            $(pub $name: AtomicU64,)+
        }

        impl $counters {
            #[cfg(test)]
            pub fn snapshot(&self) -> Vec<(&'static str, u64)> {
                vec![$((stringify!($name), self.$name.load(Ordering::Relaxed)),)+]
            }
        }
    };
}

counters!(SendCounters {
    packets_sent,
    passthrough_packets,
    encoded_packets,
    silence_packets,
    held_ticks,
    alone_ticks,
    late_ticks,
    dave_frames,
    dave_not_ready,
    encrypt_failures,
    dave_busy,
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
});

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
    pub session_built: bool,
}

impl DaveView {
    fn of(dave: &Dave) -> Self {
        Self {
            info: dave.info(),
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
    pub speech_activity: AtomicBool,
    pub receive: ReceiveCounters,
    #[cfg(test)]
    pub faults: Faults,
}

impl Media {
    pub(crate) fn new(
        guild: GuildId,
        user: UserId,
        channel: ChannelId,
        inbox: Arc<Inbox>,
        stall_after: Duration,
        receive: ReceiveTuning,
    ) -> Self {
        let dave = Dave::new(user, channel, &receive);
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
            speech_activity: AtomicBool::new(true),
            receive: ReceiveCounters::default(),
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

    pub fn reconnecting(&self) {
        for receiver in self.receivers.lock().iter() {
            if receiver.options().stall == ReceiveStall::End {
                receiver.end(Ended::Reconnecting);
            }
        }
    }

    pub fn speech_hangover(&self) -> Duration {
        Duration::from_nanos(self.speech_hangover.load(Ordering::Relaxed))
    }

    pub fn set_speech_hangover(&self, hangover: Duration) {
        self.speech_hangover
            .store(nanos(hangover), Ordering::Relaxed);
    }

    pub fn speech_activity(&self) -> bool {
        self.speech_activity.load(Ordering::Relaxed)
    }

    pub fn set_speech_activity(&self, enabled: bool) {
        self.speech_activity.store(enabled, Ordering::Relaxed);
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
    pub receive_panic: AtomicBool,
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
        return false;
    }
    rtp.nonce += 1;
    match transport.socket.send(packet) {
        Ok(_) => {
            rtp.sequence = rtp.sequence.wrapping_add(1);
            bump(&media.counters.packets_sent);
            true
        }
        Err(error) => {
            debug!(%error, "a voice packet was not sent");
            false
        }
    }
}

#[cfg(test)]
mod tests;
