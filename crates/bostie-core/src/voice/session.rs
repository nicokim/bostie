use std::future;
use std::mem;
use std::net::SocketAddr;
use std::pin::Pin;
use std::sync::Arc;
use std::sync::atomic::Ordering;
use std::time::Duration;

use bytes::Bytes;
use reqwest::Url;
use tokio::net::UdpSocket;
use tokio::sync::watch;
use tokio::task::{AbortHandle, spawn_blocking};
use tokio::time::Instant;
use tracing::{debug, warn};

use super::Throttle;
use super::connection::{Command, Connection, GatewayEvent, Step, until};
use super::crypto::{Cipher, Mode};
use super::dave::{Dave, Outbound, SendMode};
use super::media::Transport;
use super::proto::{self, Inbound, ProtoError, SessionDescription};
use super::receive::Intake;
use super::types::{DaveOp, DisconnectReason};
use super::udp::{self, Answer, DiscoveryError};
use super::ws::{self, Close, Heartbeat, Received, SeqAck, VoiceSocket};
use crate::error::describe;
use crate::inbox::Delivery;

pub(super) struct Credentials {
    pub session_id: Box<str>,
    pub token: Box<str>,
    pub endpoint: Box<str>,
}

pub(super) enum Attempt {
    Identify(Credentials),
    Resume,
}

#[derive(Debug)]
pub(super) enum SessionEnd {
    Closed(u16),
    Lost(String),
    Invalid(String),
    Failed(String),
    Zombie,
    Gateway(GatewayEvent),
    Disconnect,
    Reconnect,
    DaveFailed(String),
    NonceExhausted,
    Fatal(DisconnectReason, String),
    TimedOut,
    Stopped,
    MediaLost,
}

pub(super) struct VoiceSession {
    url: Url,
    session_id: Box<str>,
    token: Box<str>,
    seq_ack: SeqAck,
    udp: Option<Udp>,
    pub(super) resumable: bool,
}

struct Udp {
    socket: Arc<UdpSocket>,
    sender: Arc<std::net::UdpSocket>,
    answers: watch::Receiver<Option<Answer>>,
    ssrc: u32,
    receiver: AbortHandle,
}

impl Drop for Udp {
    fn drop(&mut self) {
        self.receiver.abort();
    }
}

pub(super) async fn run(conn: &mut Connection, attempt: Attempt) -> SessionEnd {
    conn.fresh = matches!(attempt, Attempt::Identify(_));
    let end = attempt_session(conn, attempt).await;
    conn.fresh = false;
    end
}

async fn attempt_session(conn: &mut Connection, attempt: Attempt) -> SessionEnd {
    let progress = match attempt {
        Attempt::Identify(credentials) => {
            let url = match ws::endpoint_url(&credentials.endpoint) {
                Ok(url) => url,
                Err(error) => return SessionEnd::Failed(error.to_string()),
            };
            conn.info.members.lock().clear();
            conn.info.ssrcs.lock().clear();
            conn.media.others.store(0, Ordering::Relaxed);
            conn.voice = Some(VoiceSession {
                url,
                session_id: credentials.session_id,
                token: credentials.token,
                seq_ack: SeqAck::default(),
                udp: None,
                resumable: false,
            });
            conn.set_step(Step::Websocket);
            Progress::Identifying
        }
        Attempt::Resume => Progress::Resuming,
    };
    let Some(url) = conn.voice.as_ref().map(|voice| voice.url.clone()) else {
        return SessionEnd::Failed("there is no voice session to resume".to_owned());
    };
    let shared = Arc::clone(&conn.shared);
    let timeouts = conn.tuning.ws;
    let socket = match conn
        .interruptible(VoiceSocket::dial(&url, &shared.connector, timeouts))
        .await
    {
        Ok(Ok(socket)) => socket,
        Ok(Err(error)) => return SessionEnd::Failed(format!("websocket: {error}")),
        Err(end) => return end,
    };
    let mut session = Session::new(conn, socket, progress);
    let end = match session.open().await {
        Ok(()) => session.serve().await,
        Err(end) => end,
    };
    session.close(&end).await;
    end
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Progress {
    Identifying,
    Discovering,
    Describing,
    Joining,
    Resuming,
    Ready,
}

type Discovery = Pin<Box<dyn Future<Output = Result<SocketAddr, DiscoveryError>> + Send>>;

#[derive(Debug)]
struct Coalescer {
    window: Duration,
    last: Option<Instant>,
    due: Option<Instant>,
}

impl Coalescer {
    const fn new(window: Duration) -> Self {
        Self {
            window,
            last: None,
            due: None,
        }
    }

    fn request(&mut self, now: Instant) -> bool {
        if self.due.is_some() {
            return false;
        }
        match self.last {
            Some(last) if now < last + self.window => {
                self.due = Some(last + self.window);
                false
            }
            _ => true,
        }
    }

    fn sent(&mut self, now: Instant) {
        self.last = Some(now);
        self.due = None;
    }
}

struct Keepalive {
    interval: Duration,
    next: Instant,
    sent_at: Option<std::time::Instant>,
    listening: bool,
}

impl Keepalive {
    fn new(interval: Duration) -> Self {
        Self {
            interval,
            next: Instant::now() + interval,
            sent_at: None,
            listening: true,
        }
    }
}

struct Session<'c> {
    conn: &'c mut Connection,
    socket: VoiceSocket,
    progress: Progress,
    heartbeat: Option<Heartbeat>,
    ready: Option<proto::Ready>,
    mode: Option<Mode>,
    early: Vec<Inbound>,
    discovery: Option<Discovery>,
    speaking: Coalescer,
    keepalive: Option<Keepalive>,
    stable: bool,
    unknown: Throttle,
    invalid: Throttle,
}

impl<'c> Session<'c> {
    fn new(conn: &'c mut Connection, socket: VoiceSocket, progress: Progress) -> Self {
        let speaking = Coalescer::new(conn.tuning.speaking_coalesce);
        Self {
            conn,
            socket,
            progress,
            heartbeat: None,
            ready: None,
            mode: None,
            early: Vec::new(),
            discovery: None,
            speaking,
            keepalive: None,
            stable: false,
            unknown: Throttle::default(),
            invalid: Throttle::default(),
        }
    }

    async fn open(&mut self) -> Result<(), SessionEnd> {
        let Some(voice) = &self.conn.voice else {
            return Err(SessionEnd::Failed("the voice session is gone".to_owned()));
        };
        let payload = if self.progress == Progress::Resuming {
            proto::resume(
                self.conn.guild,
                &voice.session_id,
                &voice.token,
                voice.seq_ack.last(),
            )
        } else {
            proto::identify(
                self.conn.guild,
                self.conn.user,
                &voice.session_id,
                &voice.token,
            )
        };
        if self.progress != Progress::Resuming {
            self.conn.set_step(Step::Handshake);
        }
        self.send_text(payload).await
    }

    async fn serve(&mut self) -> SessionEnd {
        loop {
            let deadline = self.conn.deadline;
            let beat = self.heartbeat.as_ref().map(Heartbeat::deadline);
            let keepalive = self.keepalive.as_ref().map(|keepalive| keepalive.next);
            let listening = self
                .keepalive
                .as_ref()
                .is_some_and(|keepalive| keepalive.listening);
            let speak = self.speaking.due;
            let step = tokio::select! {
                biased;
                command = self.conn.commands.recv() => self.on_command(command).await,
                _ = self.conn.stop.changed() => Err(SessionEnd::Stopped),
                changed = self.conn.gateway.changed() => self.on_gateway(changed.is_ok()),
                received = self.socket.receive() => self.on_received(received).await,
                () = until(beat) => self.on_beat().await,
                () = until(keepalive) => self.on_keepalive().await,
                result = discovered(&mut self.discovery) => self.on_discovered(result).await,
                alive = answered(&mut self.conn.voice), if listening => {
                    self.on_answer(alive);
                    Ok(())
                }
                () = until(speak) => self.speak().await,
                () = self.conn.media.lost.notified() => Err(SessionEnd::MediaLost),
                () = self.conn.media.exhausted.notified() => Err(SessionEnd::NonceExhausted),
                () = self.conn.media.recover.notified() => self.receive_recovery().await,
                () = until(deadline) => Err(SessionEnd::TimedOut),
            };
            if let Err(end) = step {
                return end;
            }
        }
    }

    async fn close(self, end: &SessionEnd) {
        match end {
            SessionEnd::Closed(_) => {}
            SessionEnd::Lost(_) | SessionEnd::Zombie => {
                tokio::spawn(self.socket.close(Close::Resumable));
            }
            SessionEnd::Disconnect => self.socket.close(Close::Final).await,
            _ => self.socket.close(Close::Resumable).await,
        }
    }

    async fn on_command(&mut self, command: Option<Command>) -> Result<(), SessionEnd> {
        let speaking = matches!(command, Some(Command::Speaking(_)));
        match self.conn.command(command) {
            Some(SessionEnd::Reconnect) if self.progress != Progress::Ready => Ok(()),
            Some(end) => Err(end),
            None if speaking => self.request_speaking().await,
            None => Ok(()),
        }
    }

    fn on_gateway(&mut self, alive: bool) -> Result<(), SessionEnd> {
        if !alive {
            return Err(SessionEnd::Stopped);
        }
        match self.conn.poll_gateway() {
            Some(event) => Err(SessionEnd::Gateway(event)),
            None => Ok(()),
        }
    }

    async fn on_received(&mut self, received: Received) -> Result<(), SessionEnd> {
        let frame = match received {
            Received::Closed(code) => return Err(SessionEnd::Closed(code)),
            Received::Lost(error) => return Err(SessionEnd::Lost(error.to_string())),
            Received::Frame(frame) => frame,
        };
        let handled = match frame.inbound {
            Ok(inbound) => self.on_inbound(inbound).await,
            Err(error) => self.on_invalid(&error),
        };
        if let Some(voice) = &mut self.conn.voice {
            voice.seq_ack.record(frame.seq);
        }
        handled
    }

    fn on_invalid(&mut self, error: &ProtoError) -> Result<(), SessionEnd> {
        if self.progress != Progress::Ready {
            return Err(SessionEnd::Invalid(format!("invalid frame: {error}")));
        }
        if self.invalid.allows(Instant::now().into_std()) {
            warn!(%error, guild = %self.conn.guild, "skipping an invalid voice frame");
        }
        Ok(())
    }

    async fn on_inbound(&mut self, inbound: Inbound) -> Result<(), SessionEnd> {
        match inbound {
            Inbound::Hello { interval } => {
                self.heartbeat = Some(Heartbeat::new(interval, Instant::now()));
                self.handshaken()
            }
            Inbound::Ready(ready) => {
                if self.progress == Progress::Identifying && self.ready.is_none() {
                    self.ready = Some(ready);
                    return self.handshaken();
                }
                Ok(())
            }
            Inbound::SessionDescription(description) => {
                if self.progress == Progress::Describing {
                    return self.describe(description).await;
                }
                Ok(())
            }
            Inbound::Resumed => {
                if self.progress == Progress::Resuming {
                    return self.resumed().await;
                }
                Ok(())
            }
            Inbound::HeartbeatAck { t } => {
                self.on_ack(t);
                Ok(())
            }
            Inbound::Speaking { user, ssrc, flags } => {
                {
                    let mut ssrcs = self.conn.info.ssrcs.lock();
                    if ssrcs.get(&ssrc).is_none_or(|&(owner, _)| owner != user) {
                        ssrcs.insert(ssrc, (user, std::time::Instant::now()));
                    }
                }
                self.conn.push(Delivery::VoiceSpeaking {
                    guild: self.conn.guild,
                    user,
                    ssrc,
                    flags,
                });
                Ok(())
            }
            inbound @ (Inbound::ClientsConnect { .. }
            | Inbound::ClientDisconnect { .. }
            | Inbound::Dave(_)) => {
                if matches!(
                    self.progress,
                    Progress::Identifying | Progress::Discovering | Progress::Describing
                ) {
                    let limit = self.conn.tuning.early_ops;
                    if self.early.len() >= limit {
                        return Err(SessionEnd::Invalid(format!(
                            "invalid frame: more than {limit} messages arrived before the \
                             session description"
                        )));
                    }
                    self.early.push(inbound);
                    return Ok(());
                }
                self.on_call(inbound).await
            }
            Inbound::Other { op } => {
                if self.unknown.allows(Instant::now().into_std()) {
                    debug!(op, guild = %self.conn.guild, "ignoring an unknown voice opcode");
                }
                Ok(())
            }
        }
    }

    fn handshaken(&mut self) -> Result<(), SessionEnd> {
        if self.progress != Progress::Identifying || self.heartbeat.is_none() {
            return Ok(());
        }
        let Some(ready) = self.ready.take() else {
            return Ok(());
        };
        let Some(mode) = Mode::choose(&ready.modes) else {
            return Err(SessionEnd::Fatal(
                DisconnectReason::ProtocolError,
                format!(
                    "no supported encryption mode; the server offered {:?}",
                    ready.modes
                ),
            ));
        };
        self.mode = Some(mode);
        self.conn.set_step(Step::Udp);
        let (socket, sender) = udp::bind(SocketAddr::new(ready.ip, ready.port))
            .map_err(|error| SessionEnd::Failed(format!("udp: {error}")))?;
        let socket = Arc::new(socket);
        let (answer_slot, answers) = watch::channel(None);
        let receiver = {
            let socket = Arc::clone(&socket);
            let mut intake = Intake::new(
                Arc::clone(&self.conn.media),
                Arc::clone(&self.conn.info),
                Arc::clone(&sender),
                self.conn.tuning.receive.replay_window,
            );
            let ssrc = ready.ssrc;
            tokio::spawn(async move {
                let received =
                    udp::receive(&socket, ssrc, &answer_slot, |packet| intake.rtp(packet));
                if let Err(error) = received.await {
                    debug!(%error, "the voice UDP receiver stopped");
                }
            })
            .abort_handle()
        };
        self.discovery = Some({
            let socket = Arc::clone(&socket);
            let mut answers = answers.clone();
            let ssrc = ready.ssrc;
            let interval = self.conn.tuning.discovery_interval;
            Box::pin(async move { udp::discover(&socket, &mut answers, ssrc, interval).await })
        });
        if let Some(voice) = &mut self.conn.voice {
            voice.udp = Some(Udp {
                socket,
                sender,
                answers,
                ssrc: ready.ssrc,
                receiver,
            });
        }
        self.progress = Progress::Discovering;
        Ok(())
    }

    async fn on_discovered(
        &mut self,
        result: Result<SocketAddr, DiscoveryError>,
    ) -> Result<(), SessionEnd> {
        self.discovery = None;
        let address = result.map_err(|error| SessionEnd::Failed(error.to_string()))?;
        let Some(mode) = self.mode else {
            return Ok(());
        };
        self.send_text(proto::select_protocol(
            address.ip(),
            address.port(),
            mode.as_str(),
        ))
        .await?;
        self.progress = Progress::Describing;
        self.conn.set_step(Step::Session);
        self.keepalive = Some(Keepalive::new(self.conn.tuning.keepalive_interval));
        Ok(())
    }

    async fn describe(&mut self, description: SessionDescription) -> Result<(), SessionEnd> {
        let Some(mode) = self.mode else {
            return Ok(());
        };
        if &*description.mode != mode.as_str() {
            return Err(SessionEnd::Fatal(
                DisconnectReason::ProtocolError,
                format!(
                    "the session description uses {} but {} was selected",
                    description.mode,
                    mode.as_str()
                ),
            ));
        }
        let Some((sender, ssrc)) = self.udp().map(|udp| (Arc::clone(&udp.sender), udp.ssrc)) else {
            return Ok(());
        };
        self.conn.generation += 1;
        let transport = Arc::new(Transport {
            generation: self.conn.generation,
            socket: sender,
            ssrc,
            cipher: Cipher::new(mode, &description.secret_key),
        });
        self.speaking.sent(Instant::now());
        self.send_text(proto::speaking(self.conn.speaking, ssrc))
            .await?;
        let (user, channel) = (self.conn.user, self.conn.dave_channel());
        let protocol_version = description.dave_protocol_version;
        let receive = self.conn.tuning.receive;
        let start = move || {
            let mut dave = Dave::new(user, channel, &receive);
            let mut out = Vec::new();
            dave.start(protocol_version, &mut out).map(|()| (dave, out))
        };
        let started = if protocol_version == 0 {
            Ok(start())
        } else {
            spawn_blocking(start).await
        };
        let (dave, out) = match started {
            Ok(Ok(started)) => started,
            Ok(Err(error)) => return Err(SessionEnd::DaveFailed(error.to_string())),
            Err(error) => {
                return Err(SessionEnd::DaveFailed(format!(
                    "the DAVE session could not start: {error}"
                )));
            }
        };
        self.conn.media.begin(dave, transport);
        self.send_outbound(out).await?;
        self.progress = Progress::Joining;
        self.conn.set_step(Step::Dave);
        for inbound in mem::take(&mut self.early) {
            self.on_call(inbound).await?;
        }
        self.joined();
        Ok(())
    }

    async fn resumed(&mut self) -> Result<(), SessionEnd> {
        self.progress = Progress::Ready;
        self.keepalive = Some(Keepalive::new(self.conn.tuning.keepalive_interval));
        self.conn.became_ready();
        self.request_speaking().await
    }

    fn joined(&mut self) {
        if self.progress == Progress::Joining && self.conn.media.gate() != SendMode::Hold {
            self.progress = Progress::Ready;
            self.conn.became_ready();
        }
    }

    async fn on_call(&mut self, inbound: Inbound) -> Result<(), SessionEnd> {
        let guild = self.conn.guild;
        match inbound {
            Inbound::ClientsConnect { users } => {
                let own = self.conn.user;
                let others = {
                    let mut members = self.conn.info.members.lock();
                    members.extend(users.iter().copied().filter(|&user| user != own));
                    members.len()
                };
                self.conn.media.others.store(others, Ordering::Relaxed);
                self.conn
                    .media
                    .with_dave(|dave| dave.clients_connect(&users));
                self.conn.push(Delivery::VoiceClients { guild, users });
                self.request_speaking().await?;
            }
            Inbound::ClientDisconnect { user } => {
                let others = {
                    let mut members = self.conn.info.members.lock();
                    members.remove(&user);
                    members.len()
                };
                self.conn
                    .info
                    .ssrcs
                    .lock()
                    .retain(|_, (owner, _)| *owner != user);
                self.conn.media.others.store(others, Ordering::Relaxed);
                self.conn
                    .media
                    .with_dave(|dave| dave.client_disconnect(user));
                self.conn.push(Delivery::VoiceClientLeft { guild, user });
            }
            Inbound::Dave(op) => self.dave(op).await?,
            _ => {}
        }
        self.joined();
        Ok(())
    }

    async fn dave(&mut self, op: DaveOp) -> Result<(), SessionEnd> {
        let media = Arc::clone(&self.conn.media);
        let handled = spawn_blocking(move || {
            media.with_dave(|dave| {
                let mut out = Vec::new();
                let result = dave.handle(op, std::time::Instant::now(), &mut out);
                (result, out)
            })
        })
        .await;
        let (result, out) = handled
            .map_err(|error| SessionEnd::DaveFailed(format!("a DAVE call failed: {error}")))?;
        self.send_outbound(out).await?;
        match result {
            Ok(Some(executed)) => self.conn.push(Delivery::VoiceDave {
                guild: self.conn.guild,
                protocol_version: executed.protocol_version,
                epoch: executed.epoch,
                transition_id: executed.transition_id,
            }),
            Ok(None) => {}
            Err(error) => return Err(SessionEnd::DaveFailed(describe(&error))),
        }
        Ok(())
    }

    async fn receive_recovery(&mut self) -> Result<(), SessionEnd> {
        let media = Arc::clone(&self.conn.media);
        let handled = spawn_blocking(move || {
            media.with_dave(|dave| {
                let mut out = Vec::new();
                let result = dave.receive_recovery(std::time::Instant::now(), &mut out);
                (result, out)
            })
        })
        .await;
        let (result, out) = handled
            .map_err(|error| SessionEnd::DaveFailed(format!("a DAVE call failed: {error}")))?;
        self.send_outbound(out).await?;
        result
            .map(|_| ())
            .map_err(|error| SessionEnd::DaveFailed(error.to_string()))
    }

    async fn send_outbound(&mut self, out: Vec<Outbound>) -> Result<(), SessionEnd> {
        for message in out {
            match message {
                Outbound::KeyPackage(bytes) => {
                    self.send_binary(proto::key_package(&bytes)).await?;
                }
                Outbound::CommitWelcome(bytes) => {
                    self.send_binary(proto::commit_welcome(&bytes)).await?;
                }
                Outbound::TransitionReady(transition_id) => {
                    self.send_text(proto::transition_ready(transition_id))
                        .await?;
                }
                Outbound::InvalidCommitWelcome(transition_id) => {
                    self.send_text(proto::invalid_commit_welcome(transition_id))
                        .await?;
                }
            }
        }
        Ok(())
    }

    fn on_ack(&mut self, t: u64) {
        let Some(latency) = self
            .heartbeat
            .as_mut()
            .and_then(|heartbeat| heartbeat.ack(t, Instant::now()))
        else {
            return;
        };
        self.conn.info.set_latency_ws(latency);
        if self.progress == Progress::Ready && !mem::replace(&mut self.stable, true) {
            self.conn.stable();
        }
    }

    async fn on_beat(&mut self) -> Result<(), SessionEnd> {
        let Some(heartbeat) = &mut self.heartbeat else {
            return Ok(());
        };
        let t = heartbeat
            .beat(Instant::now())
            .map_err(|_| SessionEnd::Zombie)?;
        let seq_ack = self
            .conn
            .voice
            .as_ref()
            .and_then(|voice| voice.seq_ack.last());
        self.send_text(proto::heartbeat(t, seq_ack)).await
    }

    async fn on_keepalive(&mut self) -> Result<(), SessionEnd> {
        let Some(keepalive) = &mut self.keepalive else {
            return Ok(());
        };
        keepalive.next += keepalive.interval;
        let Some(udp) = self
            .conn
            .voice
            .as_ref()
            .and_then(|voice| voice.udp.as_ref())
        else {
            return Ok(());
        };
        match udp.socket.send(&udp::discovery_request(udp.ssrc)).await {
            Ok(_) => {
                keepalive.sent_at = Some(std::time::Instant::now());
            }
            Err(error) => debug!(%error, "a voice keepalive was not sent"),
        }
        Ok(())
    }

    fn on_answer(&mut self, alive: bool) {
        let Some(keepalive) = &mut self.keepalive else {
            return;
        };
        if !alive {
            keepalive.listening = false;
            return;
        }
        let answer = self
            .conn
            .voice
            .as_mut()
            .and_then(|voice| voice.udp.as_mut())
            .and_then(|udp| *udp.answers.borrow_and_update());
        if let (Some(answer), Some(sent)) = (answer, keepalive.sent_at)
            && answer.at >= sent
        {
            keepalive.sent_at = None;
            self.conn.info.set_latency_udp(answer.at - sent);
        }
    }

    async fn request_speaking(&mut self) -> Result<(), SessionEnd> {
        if !matches!(self.progress, Progress::Joining | Progress::Ready) {
            return Ok(());
        }
        if self.speaking.request(Instant::now()) {
            return self.speak().await;
        }
        Ok(())
    }

    async fn speak(&mut self) -> Result<(), SessionEnd> {
        let Some(ssrc) = self.udp().map(|udp| udp.ssrc) else {
            self.speaking.due = None;
            return Ok(());
        };
        self.speaking.sent(Instant::now());
        self.send_text(proto::speaking(self.conn.speaking, ssrc))
            .await
    }

    fn udp(&self) -> Option<&Udp> {
        self.conn
            .voice
            .as_ref()
            .and_then(|voice| voice.udp.as_ref())
    }

    async fn send_text(&mut self, payload: Bytes) -> Result<(), SessionEnd> {
        let patience = self.conn.tuning.ws.patience(self.heartbeat.as_ref());
        self.socket
            .send_text(payload, patience)
            .await
            .map_err(|error| SessionEnd::Lost(error.to_string()))
    }

    async fn send_binary(&mut self, payload: Bytes) -> Result<(), SessionEnd> {
        let patience = self.conn.tuning.ws.patience(self.heartbeat.as_ref());
        self.socket
            .send_binary(payload, patience)
            .await
            .map_err(|error| SessionEnd::Lost(error.to_string()))
    }
}

async fn discovered(discovery: &mut Option<Discovery>) -> Result<SocketAddr, DiscoveryError> {
    match discovery {
        Some(discovery) => discovery.as_mut().await,
        None => future::pending().await,
    }
}

async fn answered(voice: &mut Option<VoiceSession>) -> bool {
    match voice.as_mut().and_then(|voice| voice.udp.as_mut()) {
        Some(udp) => udp.answers.changed().await.is_ok(),
        None => future::pending().await,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn speaking_goes_out_at_once_and_then_once_per_window() {
        let window = Duration::from_millis(200);
        let start = Instant::now();
        let mut coalescer = Coalescer::new(window);

        assert!(coalescer.request(start));
        coalescer.sent(start);
        assert!(!coalescer.request(start + window / 4));
        assert!(!coalescer.request(start + window / 2));
        assert_eq!(coalescer.due, Some(start + window));

        coalescer.sent(start + window);
        assert_eq!(coalescer.due, None);
        assert!(coalescer.request(start + window * 3));
    }
}
