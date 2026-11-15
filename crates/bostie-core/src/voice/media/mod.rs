use std::collections::{HashSet, VecDeque};
use std::mem;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU8, AtomicU64, AtomicUsize, Ordering};
use std::time::{Duration, Instant};

use bytes::Bytes;
use parking_lot::{Mutex, MutexGuard};
use tokio::sync::Notify;
use tokio::sync::futures::Notified;
use tracing::debug;

use crate::error::CoreError;
use crate::ids::{ChannelId, GuildId, UserId};
use crate::inbox::{Delivery, Inbox};
use crate::voice::crypto::Cipher;
use crate::voice::dave::{Dave, DaveInfo, SendMode};
use crate::voice::mixer::Mixer;
use crate::voice::nanos;
use crate::voice::pacer::Tick;
use crate::voice::receiver::{Ended, Receiver};
use crate::voice::rtp::{self, HEADER_LEN};
use crate::voice::sink::Sink;
use crate::voice::types::{
    Direction, EncoderSettings, FRAME_48K, ReceiveFormat, ReceiveStall, ReceiveTuning,
    SILENCE_FRAME,
};

pub(crate) const SILENCE_TAIL: u8 = 5;

const DEFAULT_SPEECH_HANGOVER: Duration = Duration::from_millis(200);

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Arrival {
    pub ssrc: u32,
    pub sequence: u16,
    pub timestamp: u32,
    pub at: Instant,
    pub payload: Option<Bytes>,
    pub silence: bool,
    pub extension: Option<(u16, Bytes)>,
    pub own: bool,
}

#[derive(Debug)]
pub(crate) struct Transport {
    pub generation: u64,
    pub socket: Arc<std::net::UdpSocket>,
    pub ssrc: u32,
    pub cipher: Cipher,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct DaveView {
    info: DaveInfo,
    session_built: bool,
    send_mode: SendMode,
    members: Arc<HashSet<UserId>>,
}

impl DaveView {
    fn of(dave: &Dave) -> Self {
        Self {
            info: dave.info(),
            session_built: dave.generation() > 0,
            send_mode: dave.send_mode(),
            members: Arc::clone(dave.members()),
        }
    }
}

#[derive(Default)]
struct Roster {
    open: Vec<Arc<Receiver>>,
    reconnecting: bool,
}

struct Sending {
    transport: Mutex<Option<Arc<Transport>>>,
    gate: AtomicU8,
    sinks: Mutex<Vec<Arc<Sink>>>,
    encoder: Mutex<Option<EncoderSettings>>,
}

struct Receiving {
    arrival_limit: usize,
    arrivals: Mutex<VecDeque<Arrival>>,
    departures: Mutex<Vec<(UserId, Instant)>>,
    receivers: Mutex<Roster>,
    open_receivers: AtomicUsize,
    packet_receivers: AtomicUsize,
    self_receivers: AtomicUsize,
    speech_hangover: AtomicU64,
    speech_activity: AtomicBool,
    transport_failures: AtomicU64,
}

#[derive(Default)]
struct Lifecycle {
    closed: AtomicBool,
    lost: Notify,
    rekey: Notify,
    recover: Notify,
}

pub(crate) struct Media {
    guild: GuildId,
    inbox: Arc<Inbox>,
    stall_after: AtomicU64,
    dave: Mutex<Dave>,
    dave_view: Mutex<DaveView>,
    sending: Sending,
    receiving: Receiving,
    lifecycle: Lifecycle,
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
            stall_after: AtomicU64::new(nanos(stall_after)),
            dave: Mutex::new(dave),
            dave_view: Mutex::new(view),
            sending: Sending {
                transport: Mutex::new(None),
                gate: AtomicU8::new(SendMode::Hold as u8),
                sinks: Mutex::new(Vec::new()),
                encoder: Mutex::new(None),
            },
            receiving: Receiving {
                arrival_limit: receive.arrivals,
                arrivals: Mutex::new(VecDeque::new()),
                departures: Mutex::new(Vec::new()),
                receivers: Mutex::new(Roster::default()),
                open_receivers: AtomicUsize::new(0),
                packet_receivers: AtomicUsize::new(0),
                self_receivers: AtomicUsize::new(0),
                speech_hangover: AtomicU64::new(nanos(DEFAULT_SPEECH_HANGOVER)),
                speech_activity: AtomicBool::new(true),
                transport_failures: AtomicU64::new(0),
            },
            lifecycle: Lifecycle::default(),
        }
    }

    pub(crate) fn guild(&self) -> GuildId {
        self.guild
    }

    pub(crate) fn stall_after(&self) -> Duration {
        Duration::from_nanos(self.stall_after.load(Ordering::Relaxed))
    }

    pub(crate) fn set_stall_after(&self, after: Duration) {
        self.stall_after.store(nanos(after), Ordering::Relaxed);
    }

    pub(crate) fn push(&self, delivery: Delivery) {
        self.inbox.push(delivery);
    }

    pub(crate) fn is_closed(&self) -> bool {
        self.lifecycle.closed.load(Ordering::Acquire)
    }

    pub(crate) fn lose(&self) {
        self.lifecycle.closed.store(true, Ordering::Release);
        self.lifecycle.lost.notify_one();
    }

    pub(crate) fn lost(&self) -> Notified<'_> {
        self.lifecycle.lost.notified()
    }

    pub(crate) fn rekey_requested(&self) -> Notified<'_> {
        self.lifecycle.rekey.notified()
    }

    pub(crate) fn request_rekey(&self) {
        self.lifecycle.rekey.notify_one();
    }

    pub(crate) fn recovery_requested(&self) -> Notified<'_> {
        self.lifecycle.recover.notified()
    }

    pub(crate) fn request_recovery(&self) {
        self.lifecycle.recover.notify_one();
    }

    pub(crate) fn close(&self) {
        self.lifecycle.closed.store(true, Ordering::Release);
        let sinks = mem::take(&mut *self.sending.sinks.lock());
        for sink in sinks {
            sink.close();
        }
        self.end_receivers(Ended::Disconnected);
    }

    pub(crate) fn transport(&self) -> Option<Arc<Transport>> {
        self.sending.transport.lock().clone()
    }

    pub(crate) fn add_sink(&self, sink: &Arc<Sink>) -> bool {
        let mut sinks = self.sending.sinks.lock();
        if self.is_closed() {
            return false;
        }
        sinks.push(Arc::clone(sink));
        true
    }

    pub(crate) fn set_encoder(&self, encoder: EncoderSettings) {
        *self.sending.encoder.lock() = Some(encoder);
    }

    pub(crate) fn has_arrivals(&self) -> bool {
        !self.receiving.arrivals.lock().is_empty()
    }

    pub(crate) fn arrive(&self, stamped: impl FnOnce(Instant) -> Arrival) {
        let mut arrivals = self.receiving.arrivals.lock();
        if arrivals.len() >= self.receiving.arrival_limit {
            arrivals.pop_front();
        }
        arrivals.push_back(stamped(Instant::now()));
    }

    pub(crate) fn take_arrivals(&self, into: &mut VecDeque<Arrival>) -> Instant {
        let mut arrivals = self.receiving.arrivals.lock();
        mem::swap(&mut *arrivals, into);
        Instant::now()
    }

    pub(crate) fn depart(&self, user: UserId, at: Instant) {
        if self.open_receivers() > 0 {
            self.receiving.departures.lock().push((user, at));
        }
    }

    pub(crate) fn take_departures(&self, into: &mut Vec<(UserId, Instant)>) {
        mem::swap(&mut *self.receiving.departures.lock(), into);
    }

    pub(crate) fn add_receiver(&self, receiver: &Arc<Receiver>) -> bool {
        let mut roster = self.receiving.receivers.lock();
        if self.is_closed() {
            return false;
        }
        if roster.reconnecting && receiver.options().stall == ReceiveStall::End {
            receiver.end(Ended::Reconnecting);
            return true;
        }
        self.count_receiver(receiver, |count| {
            count.fetch_add(1, Ordering::Relaxed);
        });
        roster.open.push(Arc::clone(receiver));
        true
    }

    pub(crate) fn drop_ended_receivers(&self, open: &mut Vec<Arc<Receiver>>) {
        let mut roster = self.receiving.receivers.lock();
        roster.open.retain(|receiver| {
            let ended = receiver.is_closed();
            if ended {
                self.count_receiver(receiver, |count| {
                    count.fetch_sub(1, Ordering::Relaxed);
                });
            }
            !ended
        });
        open.clear();
        open.extend(roster.open.iter().cloned());
    }

    pub(crate) fn end_receivers(&self, why: Ended) {
        for receiver in &self.receiving.receivers.lock().open {
            receiver.end(why);
        }
    }

    pub(crate) fn set_reconnecting(&self, reconnecting: bool) {
        let mut roster = self.receiving.receivers.lock();
        roster.reconnecting = reconnecting;
        if reconnecting {
            for receiver in &roster.open {
                if receiver.options().stall == ReceiveStall::End {
                    receiver.end(Ended::Reconnecting);
                }
            }
        }
    }

    pub(crate) fn open_receivers(&self) -> usize {
        self.receiving.open_receivers.load(Ordering::Relaxed)
    }

    pub(crate) fn packet_receivers(&self) -> usize {
        self.receiving.packet_receivers.load(Ordering::Relaxed)
    }

    pub(crate) fn speech_hangover(&self) -> Duration {
        Duration::from_nanos(self.receiving.speech_hangover.load(Ordering::Relaxed))
    }

    pub(crate) fn set_speech_hangover(&self, hangover: Duration) {
        self.receiving
            .speech_hangover
            .store(nanos(hangover), Ordering::Relaxed);
    }

    pub(crate) fn speech_activity(&self) -> bool {
        self.receiving.speech_activity.load(Ordering::Relaxed)
    }

    pub(crate) fn set_speech_activity(&self, enabled: bool) {
        self.receiving
            .speech_activity
            .store(enabled, Ordering::Relaxed);
    }

    pub(crate) fn transport_failed(&self) {
        self.receiving
            .transport_failures
            .fetch_add(1, Ordering::Relaxed);
    }

    pub(crate) fn transport_failures(&self) -> u64 {
        self.receiving.transport_failures.load(Ordering::Relaxed)
    }

    fn count_receiver(&self, receiver: &Receiver, change: impl Fn(&AtomicUsize)) {
        let options = receiver.options();
        change(&self.receiving.open_receivers);
        if options.format == ReceiveFormat::Packets {
            change(&self.receiving.packet_receivers);
        }
        if options.include_self {
            change(&self.receiving.self_receivers);
        }
    }

    pub(crate) fn lane(self: &Arc<Self>, encoder: EncoderSettings) -> Result<Lane, CoreError> {
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

    pub(crate) fn install(&self, transport: Option<Arc<Transport>>) {
        let dave = self.dave.lock();
        *self.sending.transport.lock() = transport;
        self.refresh(&dave);
    }

    pub(crate) fn begin(&self, dave: Dave, transport: Arc<Transport>) {
        let mut current = self.dave.lock();
        *current = dave;
        *self.sending.transport.lock() = Some(transport);
        self.refresh(&current);
    }

    pub(crate) fn dave(&self) -> MutexGuard<'_, Dave> {
        self.dave.lock()
    }

    pub(crate) fn try_dave(&self) -> Option<MutexGuard<'_, Dave>> {
        self.dave.try_lock()
    }

    pub(crate) fn with_dave<T>(&self, run: impl FnOnce(&mut Dave) -> T) -> T {
        let mut dave = self.dave.lock();
        let output = run(&mut dave);
        self.refresh(&dave);
        output
    }

    pub(crate) fn gate(&self) -> SendMode {
        SendMode::from_byte(self.sending.gate.load(Ordering::Acquire))
    }

    fn refresh(&self, dave: &Dave) {
        let mode = if self.sending.transport.lock().is_some() {
            dave.send_mode()
        } else {
            SendMode::Hold
        };
        self.sending.gate.store(mode as u8, Ordering::Release);
        *self.dave_view.lock() = DaveView::of(dave);
    }

    pub(crate) fn dave_info(&self) -> DaveInfo {
        self.dave_view.lock().info.clone()
    }

    pub(crate) fn protocol_version(&self) -> u16 {
        self.dave_view.lock().info.protocol_version
    }

    pub(crate) fn receive_state(&self) -> (u16, bool) {
        let view = self.dave_view.lock();
        (view.info.protocol_version, view.session_built)
    }

    pub(crate) fn members(&self) -> Arc<HashSet<UserId>> {
        Arc::clone(&self.dave_view.lock().members)
    }

    pub(crate) fn others(&self) -> usize {
        self.dave_view.lock().members.len()
    }

    fn discards(&self) -> bool {
        self.dave_view.lock().send_mode == SendMode::Discard
    }

    pub(crate) fn stalled(&self, direction: Direction, stalled: bool, duration: Duration) {
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

#[derive(Debug, Default, Clone, Copy)]
pub(crate) enum Stall {
    #[default]
    Clear,
    Pending(Instant),
    Reported(Instant),
}

impl Stall {
    pub(crate) fn press(&mut self, now: Instant, after: Duration) -> Option<Duration> {
        let since = match *self {
            Self::Clear => now,
            Self::Pending(since) => since,
            Self::Reported(_) => return None,
        };
        let stalled = now.saturating_duration_since(since);
        if stalled < after {
            *self = Self::Pending(since);
            return None;
        }
        *self = Self::Reported(since);
        Some(stalled)
    }

    pub(crate) fn ease(&mut self) {
        if let Self::Pending(_) = self {
            *self = Self::Clear;
        }
    }

    pub(crate) fn resume(&mut self, now: Instant) -> Option<Duration> {
        match mem::take(self) {
            Self::Reported(since) => Some(now.saturating_duration_since(since)),
            Self::Clear | Self::Pending(_) => None,
        }
    }

    pub(crate) fn reported(self) -> bool {
        matches!(self, Self::Reported(_))
    }
}

pub(crate) struct Lane {
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
    pub(crate) fn tick(&mut self, tick: Tick) -> bool {
        if self.media.is_closed() {
            return false;
        }
        if let Some(settings) = self.media.sending.encoder.lock().take()
            && let Err(error) = self.mixer.set_encoder(settings)
        {
            debug!(%error, "the new encoder settings were refused");
        }
        self.snapshot_sinks();
        match self.media.transport() {
            None => self.hold(tick.now),
            Some(transport) => {
                self.follow(&transport);
                self.send(&transport, tick.now);
                if let Some(rtp) = &mut self.rtp {
                    rtp.timestamp = rtp
                        .timestamp
                        .wrapping_add(FRAME_48K.wrapping_mul(tick.skipped.wrapping_add(1)));
                }
            }
        }
        true
    }

    fn snapshot_sinks(&mut self) {
        {
            let mut sinks = self.media.sending.sinks.lock();
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
            nonce: 0,
            exhausted: false,
        });
    }

    fn hold(&mut self, now: Instant) {
        for sink in self.sinks.iter().filter(|sink| sink.waits_for().is_none()) {
            sink.hold();
        }
        self.stalling(now);
    }

    fn discard(&mut self) {
        self.mixer.discard(&self.sinks);
        self.silence_left = 0;
    }

    fn send(&mut self, transport: &Transport, now: Instant) {
        let media;
        let mut mode = self.media.gate();
        let dave = if mode == SendMode::Encrypt {
            media = Arc::clone(&self.media);
            let Some(guard) = media.dave.try_lock() else {
                self.hold(now);
                return;
            };
            mode = guard.send_mode();
            (mode == SendMode::Encrypt).then_some(guard)
        } else {
            None
        };
        match mode {
            SendMode::Hold => self.hold(now),
            SendMode::Discard => self.discard(),
            SendMode::Plain | SendMode::Encrypt => self.send_frame(transport, now, dave),
        }
    }

    fn send_frame(
        &mut self,
        transport: &Transport,
        now: Instant,
        mut dave: Option<MutexGuard<'_, Dave>>,
    ) {
        let payload: &[u8] = match self.mixer.mix(&self.sinks) {
            Some(packet) => {
                self.silence_left = SILENCE_TAIL;
                packet
            }
            None if self.silence_left > 0 => {
                self.silence_left -= 1;
                let silence = if dave.is_some() {
                    self.mixer.silence()
                } else {
                    Some(&SILENCE_FRAME[..])
                };
                let Some(silence) = silence else {
                    return;
                };
                silence
            }
            None => return,
        };
        let wire: &[u8] = match &mut dave {
            Some(dave) => {
                self.dave_out.clear();
                if dave.encrypt(payload, &mut self.dave_out).is_err() {
                    return;
                }
                &self.dave_out
            }
            None => payload,
        };
        drop(dave);
        let stamp = self.rtp.map(|rtp| (rtp.sequence, rtp.timestamp));
        let sent = self
            .rtp
            .as_mut()
            .is_some_and(|rtp| seal_and_send(&self.media, transport, rtp, &mut self.packet, wire));
        if sent {
            tee(&self.media, transport, stamp, payload);
        }
        self.after_send(sent, now);
    }

    fn after_send(&mut self, sent: bool, now: Instant) {
        if !sent {
            self.stalling(now);
            return;
        }
        if let Some(duration) = self.stall.resume(now) {
            self.media.stalled(Direction::Send, false, duration);
        }
    }

    fn stalling(&mut self, now: Instant) {
        let stalled = self
            .sinks
            .iter()
            .any(|sink| sink.queued().is_ok_and(|queued| !queued.is_zero()))
            && !self.media.discards();
        if !stalled {
            self.stall.ease();
            return;
        }
        if let Some(duration) = self.stall.press(now, self.media.stall_after()) {
            self.media.stalled(Direction::Send, true, duration);
        }
    }
}

impl Drop for Lane {
    fn drop(&mut self) {
        self.media.lose();
    }
}

fn tee(media: &Media, transport: &Transport, stamp: Option<(u16, u32)>, payload: &[u8]) {
    let Some((sequence, timestamp)) = stamp else {
        return;
    };
    if media.receiving.self_receivers.load(Ordering::Relaxed) == 0 {
        return;
    }
    let silence = payload == SILENCE_FRAME;
    let payload = Some(Bytes::copy_from_slice(payload));
    media.arrive(|at| Arrival {
        ssrc: transport.ssrc,
        sequence,
        timestamp,
        at,
        payload,
        silence,
        extension: None,
        own: true,
    });
}

fn seal_and_send(
    media: &Media,
    transport: &Transport,
    rtp: &mut RtpState,
    packet: &mut Vec<u8>,
    payload: &[u8],
) -> bool {
    if rtp.nonce == u32::MAX {
        if !mem::replace(&mut rtp.exhausted, true) {
            media.request_rekey();
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
            true
        }
        Err(error) => {
            debug!(%error, "a voice packet was not sent");
            false
        }
    }
}

#[cfg(test)]
pub(crate) mod tests;
