use std::future;
use std::mem;
use std::net::SocketAddr;
use std::pin::Pin;
use std::sync::Arc;
use std::time::Duration;

use bytes::Bytes;
use reqwest::Url;
use tokio::net::UdpSocket;
use tokio::sync::watch;
use tokio::task::{AbortHandle, spawn_blocking};
use tokio::time::Instant;
use tracing::{debug, warn};

use super::Throttle;
use super::connection::{ConnectStep, Connection, ConnectionCommand, GatewayEvent, until};
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
    pub(super) session_id: Box<str>,
    pub(super) token: Box<str>,
    pub(super) endpoint: Box<str>,
}

pub(super) enum Attempt {
    Identify(Credentials),
    Resume,
}

#[derive(Debug)]
pub(super) enum SessionEnd {
    Closed(u16),
    Lost(String),
    Failed(String),
    Gateway(GatewayEvent),
    Disconnect,
    Reconnect,
    NonceExhausted,
    Fatal(DisconnectReason, String),
    TimedOut,
    Stopped,
    MediaLost,
}

fn dave_failed(text: String) -> SessionEnd {
    SessionEnd::Fatal(DisconnectReason::DaveFailed, text)
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
            conn.info.ssrcs.lock().clear();
            conn.media.with_dave(Dave::clear_clients);
            conn.voice = Some(VoiceSession {
                url,
                session_id: credentials.session_id,
                token: credentials.token,
                seq_ack: SeqAck::default(),
                udp: None,
                resumable: false,
            });
            conn.set_step(ConnectStep::Websocket);
            SessionPhase::Identifying { ready: None }
        }
        Attempt::Resume => SessionPhase::Resuming,
    };
    let Some(url) = conn.voice.as_ref().map(|voice| voice.url.clone()) else {
        return SessionEnd::Failed("there is no voice session to resume".to_owned());
    };
    let shared = Arc::clone(&conn.shared);
    let socket_tuning = conn.tuning.ws;
    let socket = match conn
        .interruptible(VoiceSocket::dial(&url, &shared.connector, socket_tuning))
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

type Discovery = Pin<Box<dyn Future<Output = Result<SocketAddr, DiscoveryError>> + Send>>;

enum SessionPhase {
    Identifying { ready: Option<proto::Ready> },
    Discovering { mode: Mode, discovery: Discovery },
    Describing { mode: Mode },
    Joining,
    Resuming,
    Ready,
}

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
    sent_at: Option<Instant>,
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
    progress: SessionPhase,
    heartbeat: Option<Heartbeat>,
    early: Vec<Inbound>,
    speaking: Coalescer,
    keepalive: Option<Keepalive>,
    stable: bool,
    unknown: Throttle,
    invalid: Throttle,
}

impl<'c> Session<'c> {
    fn new(conn: &'c mut Connection, socket: VoiceSocket, progress: SessionPhase) -> Self {
        let speaking = Coalescer::new(conn.tuning.speaking_coalesce);
        Self {
            conn,
            socket,
            progress,
            heartbeat: None,
            early: Vec::new(),
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
        let resuming = matches!(self.progress, SessionPhase::Resuming);
        let payload = if resuming {
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
        if !resuming {
            self.conn.set_step(ConnectStep::Handshake);
        }
        self.send_text(payload).await
    }

    #[expect(clippy::cognitive_complexity)]
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
                result = discovered(&mut self.progress) => self.on_discovered(result).await,
                alive = answered(&mut self.conn.voice), if listening => {
                    self.on_answer(alive);
                    Ok(())
                }
                () = until(speak) => self.speak().await,
                () = self.conn.media.lost() => Err(SessionEnd::MediaLost),
                () = self.conn.media.rekey_requested() => Err(SessionEnd::NonceExhausted),
                () = self.conn.media.recovery_requested() => self.receive_recovery().await,
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
            SessionEnd::Lost(_) => {
                tokio::spawn(self.socket.close(Close::Resumable));
            }
            SessionEnd::Disconnect => self.socket.close(Close::Final).await,
            _ => self.socket.close(Close::Resumable).await,
        }
    }

    async fn on_command(&mut self, command: Option<ConnectionCommand>) -> Result<(), SessionEnd> {
        let speaking = matches!(command, Some(ConnectionCommand::Speaking(_)));
        match self.conn.command(command) {
            Some(SessionEnd::Reconnect) if !matches!(self.progress, SessionPhase::Ready) => Ok(()),
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
        if !matches!(self.progress, SessionPhase::Ready) {
            return Err(SessionEnd::Failed(format!("invalid frame: {error}")));
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
                if let SessionPhase::Identifying { ready: slot @ None } = &mut self.progress {
                    *slot = Some(ready);
                    return self.handshaken();
                }
                Ok(())
            }
            Inbound::SessionDescription(description) => match self.progress {
                SessionPhase::Describing { mode } => self.describe(mode, description).await,
                _ => Ok(()),
            },
            Inbound::Resumed => match self.progress {
                SessionPhase::Resuming => self.resumed().await,
                _ => Ok(()),
            },
            Inbound::HeartbeatAck { t } => {
                self.on_ack(t);
                Ok(())
            }
            Inbound::Speaking { user, ssrc, flags } => {
                {
                    let mut ssrcs = self.conn.info.ssrcs.lock();
                    if ssrcs.get(&ssrc).is_none_or(|&(owner, _)| owner != user) {
                        ssrcs.insert(ssrc, (user, Instant::now().into_std()));
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
                    SessionPhase::Identifying { .. }
                        | SessionPhase::Discovering { .. }
                        | SessionPhase::Describing { .. }
                ) {
                    let limit = self.conn.tuning.early_ops;
                    if self.early.len() >= limit {
                        return Err(SessionEnd::Failed(format!(
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
        let SessionPhase::Identifying { ready } = &mut self.progress else {
            return Ok(());
        };
        if self.heartbeat.is_none() {
            return Ok(());
        }
        let Some(ready) = ready.take() else {
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
        self.conn.set_step(ConnectStep::Udp);
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
                self.conn.user,
                self.conn.tuning.receive.replay_window,
            );
            let ssrc = ready.ssrc;
            tokio::spawn(async move {
                let received = udp::receive(&socket, ssrc, &answer_slot, |kind, packet| {
                    intake.receive(kind, packet);
                });
                if let Err(error) = received.await {
                    debug!(%error, "the voice UDP receiver stopped");
                }
            })
            .abort_handle()
        };
        let discovery: Discovery = {
            let socket = Arc::clone(&socket);
            let mut answers = answers.clone();
            let ssrc = ready.ssrc;
            let interval = self.conn.tuning.discovery_interval;
            Box::pin(async move { udp::discover(&socket, &mut answers, ssrc, interval).await })
        };
        if let Some(voice) = &mut self.conn.voice {
            voice.udp = Some(Udp {
                socket,
                sender,
                answers,
                ssrc: ready.ssrc,
                receiver,
            });
        }
        self.progress = SessionPhase::Discovering { mode, discovery };
        Ok(())
    }

    async fn on_discovered(
        &mut self,
        result: Result<SocketAddr, DiscoveryError>,
    ) -> Result<(), SessionEnd> {
        let SessionPhase::Discovering { mode, .. } = self.progress else {
            return Ok(());
        };
        self.progress = SessionPhase::Describing { mode };
        let address = result.map_err(|error| SessionEnd::Failed(error.to_string()))?;
        self.send_text(proto::select_protocol(
            address.ip(),
            address.port(),
            mode.as_str(),
        ))
        .await?;
        self.conn.set_step(ConnectStep::Session);
        self.keepalive = Some(Keepalive::new(self.conn.tuning.keepalive_interval));
        Ok(())
    }

    async fn describe(
        &mut self,
        mode: Mode,
        description: SessionDescription,
    ) -> Result<(), SessionEnd> {
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
            Ok(Err(error)) => return Err(dave_failed(error.to_string())),
            Err(error) => {
                return Err(dave_failed(format!(
                    "the DAVE session could not start: {error}"
                )));
            }
        };
        self.conn.media.begin(dave, transport);
        self.send_outbound(out).await?;
        self.progress = SessionPhase::Joining;
        self.conn.set_step(ConnectStep::Dave);
        for inbound in mem::take(&mut self.early) {
            self.on_call(inbound).await?;
        }
        self.joined();
        Ok(())
    }

    async fn resumed(&mut self) -> Result<(), SessionEnd> {
        self.progress = SessionPhase::Ready;
        self.keepalive = Some(Keepalive::new(self.conn.tuning.keepalive_interval));
        self.conn.became_ready();
        self.request_speaking().await
    }

    fn joined(&mut self) {
        if matches!(self.progress, SessionPhase::Joining)
            && self.conn.media.gate() != SendMode::Hold
        {
            self.progress = SessionPhase::Ready;
            self.conn.became_ready();
        }
    }

    async fn on_call(&mut self, inbound: Inbound) -> Result<(), SessionEnd> {
        let guild = self.conn.guild;
        match inbound {
            Inbound::ClientsConnect { users } => {
                self.conn
                    .media
                    .with_dave(|dave| dave.clients_connect(&users));
                self.conn.push(Delivery::VoiceClients { guild, users });
                self.request_speaking().await?;
            }
            Inbound::ClientDisconnect { user } => {
                self.conn
                    .info
                    .ssrcs
                    .lock()
                    .retain(|_, (owner, _)| *owner != user);
                self.conn
                    .media
                    .with_dave(|dave| dave.client_disconnect(user));
                self.conn.media.depart(user, Instant::now().into_std());
                self.conn.push(Delivery::VoiceClientLeft { guild, user });
            }
            Inbound::Dave(op) => self.dave(op).await?,
            _ => {}
        }
        self.joined();
        Ok(())
    }

    async fn dave(&mut self, op: DaveOp) -> Result<(), SessionEnd> {
        let executed = self
            .run_dave(|dave, now, out| dave.handle(op, now, out))
            .await?
            .map_err(|error| dave_failed(describe(&error)))?;
        if let Some(executed) = executed {
            self.conn.push(Delivery::VoiceDave {
                guild: self.conn.guild,
                protocol_version: executed.protocol_version,
                epoch: executed.epoch,
                transition_id: executed.transition_id,
            });
        }
        Ok(())
    }

    async fn receive_recovery(&mut self) -> Result<(), SessionEnd> {
        self.run_dave(Dave::receive_recovery)
            .await?
            .map(drop)
            .map_err(|error| dave_failed(error.to_string()))
    }

    async fn run_dave<T: Send + 'static>(
        &mut self,
        call: impl FnOnce(&mut Dave, std::time::Instant, &mut Vec<Outbound>) -> T + Send + 'static,
    ) -> Result<T, SessionEnd> {
        let media = Arc::clone(&self.conn.media);
        let (result, out) = spawn_blocking(move || {
            media.with_dave(|dave| {
                let mut out = Vec::new();
                let result = call(dave, Instant::now().into_std(), &mut out);
                (result, out)
            })
        })
        .await
        .map_err(|error| dave_failed(format!("a DAVE call failed: {error}")))?;
        self.send_outbound(out).await?;
        Ok(result)
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
        self.conn.info.ws_latency.set(latency);
        if matches!(self.progress, SessionPhase::Ready) && !mem::replace(&mut self.stable, true) {
            self.conn.stable();
        }
    }

    async fn on_beat(&mut self) -> Result<(), SessionEnd> {
        let Some(heartbeat) = &mut self.heartbeat else {
            return Ok(());
        };
        let t = heartbeat
            .beat(Instant::now())
            .map_err(|zombie| SessionEnd::Lost(zombie.to_string()))?;
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
                keepalive.sent_at = Some(Instant::now());
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
        let answered = self
            .conn
            .voice
            .as_mut()
            .and_then(|voice| voice.udp.as_mut())
            .and_then(|udp| *udp.answers.borrow_and_update())
            .map(|answer| Instant::from_std(answer.at));
        if let (Some(answered), Some(sent)) = (answered, keepalive.sent_at)
            && answered >= sent
        {
            keepalive.sent_at = None;
            self.conn.info.udp_latency.set(answered - sent);
        }
    }

    async fn request_speaking(&mut self) -> Result<(), SessionEnd> {
        if !matches!(self.progress, SessionPhase::Joining | SessionPhase::Ready) {
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

async fn discovered(progress: &mut SessionPhase) -> Result<SocketAddr, DiscoveryError> {
    match progress {
        SessionPhase::Discovering { discovery, .. } => discovery.as_mut().await,
        _ => future::pending().await,
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
