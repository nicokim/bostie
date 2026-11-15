use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Duration;

use bytes::Bytes;
use futures_util::{SinkExt, StreamExt};
use parking_lot::Mutex;
use reqwest::Url;
use serde::Serialize;
use tokio::net::TcpStream;
use tokio::sync::{Notify, mpsc};
use tokio::time::{Instant, sleep, sleep_until, timeout};
use tokio_websockets::{
    ClientBuilder, CloseCode, Connector, Limits, MaybeTlsStream, Message, WebSocketStream,
};
use tracing::debug;

use super::close::{self, CloseAction};
use super::envelope::{self, Forward, Frame, command};
use super::inflate::Inflater;
use super::limiter::SendLimiter;
use super::{GatewayShared, SavedSession};
use crate::backoff::backoff;
use crate::error::describe;
use crate::ids::ShardId;
use crate::inbox::Delivery;
use crate::latency::Latency;
use crate::proxy::Proxy;
use crate::voice::tap::{VOICE_SERVER_UPDATE, VOICE_STATE_UPDATE};

const HEARTBEAT: u8 = 1;
const RESUME: u8 = 6;
const NORMAL_CLOSE: u16 = 1000;
const SESSION_KEEPING_CLOSE: u16 = 4000;
const GATEWAY_QUERY: &str = "v=10&encoding=json&compress=zstd-stream";
const STALE_URL_FAILURES: u32 = 2;

type Socket = WebSocketStream<MaybeTlsStream<TcpStream>>;

pub(super) fn gateway_url(text: &str) -> Option<Url> {
    let mut url = Url::parse(text).ok()?;
    if !matches!(url.scheme(), "ws" | "wss") || ClientBuilder::new().uri(url.as_str()).is_err() {
        return None;
    }
    url.set_query(Some(GATEWAY_QUERY));
    Some(url)
}

#[derive(Serialize)]
struct ResumeData<'a> {
    token: &'a str,
    session_id: &'a str,
    seq: u64,
}

pub(super) struct Signals {
    pub(super) identify: Mutex<Bytes>,
    pub(super) close: Notify,
    pub(super) reconnect: Notify,
    pub(super) approved: Notify,
    pub(super) approved_request: AtomicU64,
    pub(super) latency: Latency,
    pub(super) stopped: AtomicBool,
    pub(super) keep_session: AtomicBool,
}

impl Signals {
    pub(super) fn new(identify: Bytes) -> Self {
        Self {
            identify: Mutex::new(identify),
            close: Notify::new(),
            reconnect: Notify::new(),
            approved: Notify::new(),
            approved_request: AtomicU64::new(0),
            latency: Latency::default(),
            stopped: AtomicBool::new(false),
            keep_session: AtomicBool::new(false),
        }
    }
}

pub(super) struct ShardSession {
    id: String,
    resume_url: Option<Url>,
    seq: u64,
}

impl ShardSession {
    pub(super) fn restored(saved: &SavedSession) -> Option<Self> {
        Some(Self {
            id: saved.id.clone(),
            resume_url: Some(gateway_url(&saved.resume_url)?),
            seq: saved.seq,
        })
    }

    fn saved(&self, base_url: &Url) -> SavedSession {
        let mut resume_url = self.resume_url.as_ref().unwrap_or(base_url).clone();
        resume_url.set_query(None);
        SavedSession {
            id: self.id.clone(),
            seq: self.seq,
            resume_url: resume_url.into(),
        }
    }
}

#[derive(Debug)]
enum ShardEnding {
    Stop,
    Requested,
    Reconnect,
    InvalidSession { resumable: bool },
    NoIdentifyLeft,
    Zombie,
    Closed(u16),
    Lost(String),
    Unreachable(String),
    InvalidFrame(String),
}

struct Held {
    seq: u64,
    forward: Forward,
}

struct ShardHeartbeat {
    interval: Duration,
    next: Instant,
    sent_at: Option<Instant>,
    acked: bool,
    paused: bool,
}

impl ShardHeartbeat {
    fn after_hello(interval: Duration, hello_at: Instant, fraction: f64) -> Self {
        Self {
            interval,
            next: hello_at + interval.mul_f64(fraction),
            sent_at: None,
            acked: true,
            paused: false,
        }
    }

    fn beat(&mut self, paused: bool, now: Instant) {
        self.acked = false;
        self.paused = paused;
        self.next = (self.next + self.interval).max(now + self.interval / 2);
    }
}

struct ShardConnection {
    socket: Socket,
    signals: Arc<Signals>,
    hello_timeout: Duration,
    close_timeout: Duration,
    inflater: Inflater,
    hello_deadline: Instant,
    heartbeat: Option<ShardHeartbeat>,
    limiter: Option<SendLimiter>,
    identify_at: Option<Instant>,
    awaiting_approval: Option<u64>,
    running: bool,
    held: Option<Held>,
}

impl ShardConnection {
    fn next_tick(&self) -> Instant {
        self.heartbeat
            .as_ref()
            .map_or(self.hello_deadline, |heartbeat| heartbeat.next)
    }

    fn can_read(&self) -> bool {
        self.held.is_none()
    }

    fn next_command_slot(&mut self, now: Instant) -> Option<Instant> {
        if !self.running {
            return None;
        }
        self.limiter.as_mut().map(|limiter| limiter.next_slot(now))
    }

    async fn send(&mut self, payload: Bytes) -> Result<(), ShardEnding> {
        let patience = self
            .heartbeat
            .as_ref()
            .map_or(self.hello_timeout, |heartbeat| heartbeat.interval);
        tokio::select! {
            biased;
            () = self.signals.close.notified() => return Err(ShardEnding::Stop),
            () = self.signals.reconnect.notified() => return Err(ShardEnding::Requested),
            sent = timeout(patience, self.socket.send(Message::text(payload))) => match sent {
                Ok(sent) => sent.map_err(|error| ShardEnding::Lost(describe(&error)))?,
                Err(_) => {
                    return Err(ShardEnding::Lost(format!(
                        "a write did not finish within {patience:?}"
                    )));
                }
            },
        }
        if let Some(limiter) = &mut self.limiter {
            limiter.record(Instant::now());
        }
        Ok(())
    }

    async fn close(&mut self, code: u16, queued: Option<&mut mpsc::Receiver<Bytes>>) {
        let patience = self.close_timeout;
        let closing = async {
            if let Some(queued) = queued {
                while self.running
                    && let Ok(payload) = queued.try_recv()
                {
                    if let Some(slot) = self.next_command_slot(Instant::now()) {
                        sleep_until(slot).await;
                    }
                    if self.send(payload).await.is_err() {
                        break;
                    }
                }
            }
            let frame = Message::close(CloseCode::try_from(code).ok(), "");
            if self.socket.send(frame).await.is_ok() {
                while self.socket.next().await.is_some() {}
            }
        };
        if timeout(patience, closing).await.is_err() {
            debug!(code, "the close handshake timed out");
        }
    }
}

pub(super) struct Shard {
    id: ShardId,
    url: Url,
    shared: Arc<GatewayShared>,
    commands: mpsc::Receiver<Bytes>,
    signals: Arc<Signals>,
    session: Option<ShardSession>,
    had_session: bool,
    identify_requests: u64,
    failures: u32,
    base_url_failures: u32,
}

impl Shard {
    pub(super) fn new(
        id: ShardId,
        url: Url,
        shared: Arc<GatewayShared>,
        commands: mpsc::Receiver<Bytes>,
        signals: Arc<Signals>,
        session: Option<ShardSession>,
    ) -> Self {
        Self {
            id,
            url,
            shared,
            commands,
            signals,
            had_session: session.is_some(),
            session,
            identify_requests: 0,
            failures: 0,
            base_url_failures: 0,
        }
    }

    pub(super) async fn run(mut self) {
        let mut delay = Duration::ZERO;
        loop {
            let limits = &self.shared.limits;
            let backoff = self
                .failures
                .checked_sub(1)
                .map_or(Duration::ZERO, |attempt| {
                    backoff(limits.backoff_base, limits.backoff_max, attempt)
                });
            if !self.idle(delay.saturating_add(backoff)).await
                || !self.await_identify_budget().await
                || !self.refresh_stale_url().await
            {
                self.closed();
                return;
            }
            self.failures = self.failures.saturating_add(1);
            let ending = self.connect_and_serve().await;
            debug!(shard = %self.id, ?ending, "connection ended");
            match self.conclude(ending) {
                Some(next_delay) => delay = next_delay,
                None => return,
            }
        }
    }

    async fn idle(&self, duration: Duration) -> bool {
        tokio::select! {
            biased;
            () = self.signals.close.notified() => false,
            () = self.signals.reconnect.notified() => true,
            () = sleep(duration) => true,
        }
    }

    async fn await_identify_budget(&self) -> bool {
        if self.session.is_some() {
            return true;
        }
        let Some(reset) = self.shared.identify.exhausted_until(Instant::now()) else {
            return true;
        };
        let wait = reset.saturating_duration_since(Instant::now());
        self.disconnected(
            None,
            format!(
                "daily identify limit reached; waiting {:.0} s",
                wait.as_secs_f64().ceil()
            ),
        );
        tokio::select! {
            biased;
            () = self.signals.close.notified() => false,
            () = sleep_until(reset) => true,
        }
    }

    async fn refresh_stale_url(&mut self) -> bool {
        if self.base_url_failures < STALE_URL_FAILURES {
            return true;
        }
        self.base_url_failures = 0;
        let Some(rest) = self.shared.rest.upgrade() else {
            return true;
        };
        let fetched = tokio::select! {
            biased;
            () = self.signals.close.notified() => return false,
            fetched = timeout(self.shared.limits.connect_timeout, rest.fetch_gateway_url()) => fetched.ok().flatten(),
        };
        if let Some(url) = fetched.as_deref().and_then(gateway_url) {
            debug!(shard = %self.id, %url, "refreshed the gateway URL");
            self.url = url;
        } else {
            debug!(shard = %self.id, "could not refresh the gateway URL");
        }
        true
    }

    async fn connect_and_serve(&mut self) -> ShardEnding {
        let limits = &self.shared.limits;
        let Some(inflater) = Inflater::new(limits.max_decompressed) else {
            return ShardEnding::Lost("zstd could not allocate a decompression context".to_owned());
        };
        let url = self
            .session
            .as_ref()
            .and_then(|session| session.resume_url.as_ref())
            .unwrap_or(&self.url);
        debug!(shard = %self.id, %url, "connecting");
        let socket = tokio::select! {
            biased;
            () = self.signals.close.notified() => return ShardEnding::Stop,
            () = self.signals.reconnect.notified() => return ShardEnding::Requested,
            connected = timeout(limits.connect_timeout, connect(url, &self.shared.connector, self.shared.proxy.as_ref())) => match connected {
                Ok(Ok(socket)) => socket,
                Ok(Err(error)) => return ShardEnding::Unreachable(error),
                Err(_) => return ShardEnding::Unreachable(format!("no connection within {:?}", limits.connect_timeout)),
            },
        };
        let mut connection = ShardConnection {
            socket,
            signals: Arc::clone(&self.signals),
            hello_timeout: limits.hello_timeout,
            close_timeout: limits.close_timeout,
            inflater,
            hello_deadline: Instant::now() + limits.hello_timeout,
            heartbeat: None,
            limiter: None,
            identify_at: None,
            awaiting_approval: None,
            running: false,
            held: None,
        };
        let ending = self.serve(&mut connection).await;
        match ending {
            ShardEnding::Stop => {
                let code = if self.signals.keep_session.load(Ordering::Acquire) {
                    SESSION_KEEPING_CLOSE
                } else {
                    NORMAL_CLOSE
                };
                connection.close(code, Some(&mut self.commands)).await;
            }
            ShardEnding::Closed(_) | ShardEnding::Lost(_) => {}
            _ => connection.close(SESSION_KEEPING_CLOSE, None).await,
        }
        ending
    }

    async fn serve(&mut self, connection: &mut ShardConnection) -> ShardEnding {
        loop {
            let now = Instant::now();
            let next_tick = connection.next_tick();
            let command_slot = connection.next_command_slot(now);
            let commands_due = command_slot.is_some_and(|slot| slot <= now);
            let commands_wait_until = command_slot.filter(|&slot| slot > now);
            let step = tokio::select! {
                biased;
                () = self.signals.close.notified() => Err(ShardEnding::Stop),
                () = self.signals.reconnect.notified() => Err(ShardEnding::Requested),
                () = sleep_until(next_tick) => self.on_tick(connection).await,
                () = sleep_until(connection.identify_at.unwrap_or(now)), if connection.identify_at.is_some() => {
                    self.identify_due(connection).await
                }
                () = self.signals.approved.notified(), if connection.awaiting_approval.is_some() => {
                    self.on_approval(connection).await
                }
                message = connection.socket.next(), if connection.can_read() => {
                    self.on_message(connection, message).await
                }
                () = self.shared.inbox.wait_for_room(), if !connection.can_read() => {
                    self.deliver_held(connection);
                    Ok(())
                }
                command = self.commands.recv(), if commands_due => match command {
                    Some(payload) => connection.send(payload).await,
                    None => Err(ShardEnding::Stop),
                },
                () = sleep_until(commands_wait_until.unwrap_or(now)), if commands_wait_until.is_some() => Ok(()),
            };
            if let Err(ending) = step {
                return ending;
            }
        }
    }

    async fn on_tick(&self, connection: &mut ShardConnection) -> Result<(), ShardEnding> {
        let Some(heartbeat) = &mut connection.heartbeat else {
            return Err(ShardEnding::Unreachable(format!(
                "no HELLO within {:?}",
                connection.hello_timeout
            )));
        };
        if !heartbeat.acked && !heartbeat.paused {
            return Err(ShardEnding::Zombie);
        }
        heartbeat.beat(connection.held.is_some(), Instant::now());
        self.send_heartbeat(connection).await
    }

    async fn send_heartbeat(&self, connection: &mut ShardConnection) -> Result<(), ShardEnding> {
        let seq = self.session.as_ref().map(|session| session.seq);
        connection.send(command(HEARTBEAT, seq)).await?;
        if let Some(heartbeat) = &mut connection.heartbeat {
            heartbeat.sent_at = Some(Instant::now());
        }
        Ok(())
    }

    async fn identify_due(&mut self, connection: &mut ShardConnection) -> Result<(), ShardEnding> {
        connection.identify_at = None;
        if !self.shared.identify_gated.load(Ordering::Relaxed) {
            return self.send_identify(connection).await;
        }
        self.identify_requests = self.identify_requests.saturating_add(1);
        connection.awaiting_approval = Some(self.identify_requests);
        self.push(Delivery::IdentifyRequest {
            shard: self.id,
            request: self.identify_requests,
        });
        Ok(())
    }

    async fn on_approval(&mut self, connection: &mut ShardConnection) -> Result<(), ShardEnding> {
        let approved = self.signals.approved_request.load(Ordering::Acquire);
        if connection
            .awaiting_approval
            .is_none_or(|request| approved < request)
        {
            return Ok(());
        }
        connection.awaiting_approval = None;
        self.send_identify(connection).await
    }

    async fn send_identify(&mut self, connection: &mut ShardConnection) -> Result<(), ShardEnding> {
        if self.had_session {
            while self.commands.try_recv().is_ok() {}
        }
        debug!(shard = %self.id, "identifying");
        let identify = self.signals.identify.lock().clone();
        connection.send(identify).await
    }

    async fn on_message(
        &mut self,
        connection: &mut ShardConnection,
        message: Option<Result<Message, tokio_websockets::Error>>,
    ) -> Result<(), ShardEnding> {
        let message = match message {
            Some(Ok(message)) => message,
            Some(Err(error)) => return Err(ShardEnding::Lost(describe(&error))),
            None => {
                return Err(ShardEnding::Lost(
                    "the connection ended without a close frame".to_owned(),
                ));
            }
        };
        if let Some((code, _)) = message.as_close() {
            return Err(ShardEnding::Closed(code.into()));
        }
        let payload = if message.is_binary() {
            connection
                .inflater
                .inflate(message.as_payload())
                .map_err(|error| ShardEnding::InvalidFrame(error.to_string()))?
        } else if message.is_text() {
            message.as_payload()
        } else {
            return Ok(());
        };
        let frame = Frame::parse(payload, |name| self.wants(name))
            .map_err(|error| ShardEnding::InvalidFrame(error.to_string()))?;
        self.on_frame(connection, frame).await
    }

    fn wants(&self, name: &str) -> bool {
        self.forwards(name) || (is_voice_update(name) && self.shared.tap.has_guilds())
    }

    fn forwards(&self, name: &str) -> bool {
        ends_the_handshake(name)
            || self
                .shared
                .filter
                .read()
                .as_ref()
                .is_none_or(|names| names.contains(name))
    }

    async fn on_frame(
        &mut self,
        connection: &mut ShardConnection,
        frame: Frame,
    ) -> Result<(), ShardEnding> {
        match frame {
            Frame::Dispatch { seq, forward: None } => self.commit(seq),
            Frame::Dispatch {
                seq,
                forward: Some(forward),
            } => {
                if is_voice_update(&forward.name) {
                    self.shared.tap.offer(self.id, &forward.name, &forward.data);
                }
                if self.forwards(&forward.name) {
                    self.forward(connection, Held { seq, forward });
                } else {
                    self.commit(seq);
                }
            }
            Frame::Heartbeat => return self.send_heartbeat(connection).await,
            Frame::HeartbeatAck => self.on_heartbeat_ack(connection),
            Frame::Hello { heartbeat_interval } => {
                return self.on_hello(connection, heartbeat_interval).await;
            }
            Frame::Reconnect => return Err(ShardEnding::Reconnect),
            Frame::InvalidSession { resumable } => {
                return Err(ShardEnding::InvalidSession { resumable });
            }
            Frame::Other { op } => debug!(shard = %self.id, op, "ignoring an unknown opcode"),
        }
        Ok(())
    }

    async fn on_hello(
        &mut self,
        connection: &mut ShardConnection,
        interval: Duration,
    ) -> Result<(), ShardEnding> {
        self.base_url_failures = 0;
        let now = Instant::now();
        connection.heartbeat = Some(ShardHeartbeat::after_hello(interval, now, fastrand::f64()));
        connection.limiter = Some(SendLimiter::new(interval));
        let Some(session) = &self.session else {
            let slot = self.shared.identify.reserve(self.id, now);
            connection.identify_at = Some(slot.ok_or(ShardEnding::NoIdentifyLeft)?);
            return Ok(());
        };
        debug!(shard = %self.id, seq = session.seq, "resuming");
        let resume = ResumeData {
            token: &self.shared.token,
            session_id: &session.id,
            seq: session.seq,
        };
        connection.send(command(RESUME, resume)).await
    }

    fn on_heartbeat_ack(&self, connection: &mut ShardConnection) {
        let Some(heartbeat) = &mut connection.heartbeat else {
            return;
        };
        heartbeat.acked = true;
        let Some(sent_at) = heartbeat.sent_at else {
            return;
        };
        self.signals.latency.set(sent_at.elapsed());
    }

    fn forward(&mut self, connection: &mut ShardConnection, held: Held) {
        let backlog = self.shared.inbox.backlog();
        let Some(queued_bytes) = backlog.filter(|_| !ends_the_handshake(&held.forward.name)) else {
            self.deliver(connection, held);
            return;
        };
        debug!(shard = %self.id, seq = held.seq, "pausing until the inbox has room");
        self.push(Delivery::Backpressure {
            shard: self.id,
            queued_bytes,
        });
        if let Some(heartbeat) = &mut connection.heartbeat {
            heartbeat.paused = true;
        }
        connection.held = Some(held);
    }

    fn deliver_held(&mut self, connection: &mut ShardConnection) {
        if let Some(held) = connection.held.take() {
            self.deliver(connection, held);
        }
    }

    fn deliver(&mut self, connection: &mut ShardConnection, held: Held) {
        let Held {
            seq,
            forward: Forward { name, data },
        } = held;
        match &*name {
            "READY" => self.begin_session(connection, &data),
            "RESUMED" => self.start_running(connection),
            _ => {}
        }
        self.push(Delivery::Dispatch {
            shard: self.id,
            seq,
            name,
            data,
        });
        self.commit(seq);
    }

    fn begin_session(&mut self, connection: &mut ShardConnection, data: &[u8]) {
        self.shared.tap.session_started(self.id);
        self.had_session = true;
        self.session = envelope::ready_session(data).and_then(|ready| {
            Some(ShardSession {
                id: ready.id,
                resume_url: Some(gateway_url(&ready.resume_url)?),
                seq: 0,
            })
        });
        if self.session.is_none() {
            debug!(shard = %self.id, "READY has no usable session, so the next connection identifies");
        }
        self.start_running(connection);
    }

    fn start_running(&mut self, connection: &mut ShardConnection) {
        debug!(shard = %self.id, "running");
        connection.running = true;
        self.failures = 0;
    }

    fn commit(&mut self, seq: u64) {
        if let Some(session) = &mut self.session {
            session.seq = seq;
        }
    }

    fn conclude(&mut self, ending: ShardEnding) -> Option<Duration> {
        match ending {
            ShardEnding::Stop => {
                self.closed();
                return None;
            }
            ShardEnding::Closed(code) => {
                let (action, text) = close::classify(code);
                match action {
                    CloseAction::Fatal => {
                        self.stopped(Some(code), text, None);
                        return None;
                    }
                    CloseAction::Reidentify => self.session = None,
                    CloseAction::Resume => {}
                }
                self.disconnected(Some(code), text.to_owned());
            }
            ShardEnding::InvalidSession { resumable } => {
                if !resumable {
                    self.session = None;
                }
                let reason = if resumable {
                    "invalid session (resumable)"
                } else {
                    "invalid session"
                };
                self.disconnected(None, reason.to_owned());
                if self.session.is_none() {
                    let delay = &self.shared.limits.invalid_session_delay;
                    let (shortest, longest) = (*delay.start(), *delay.end());
                    return Some(
                        shortest + longest.saturating_sub(shortest).mul_f64(fastrand::f64()),
                    );
                }
            }
            ShardEnding::Unreachable(error) => {
                match &mut self.session {
                    Some(ShardSession {
                        resume_url: resume_url @ Some(_),
                        ..
                    }) => *resume_url = None,
                    _ => self.base_url_failures = self.base_url_failures.saturating_add(1),
                }
                self.disconnected(None, error);
            }
            ShardEnding::NoIdentifyLeft => {}
            ShardEnding::Requested => self.disconnected(None, "reconnect requested".to_owned()),
            ShardEnding::Reconnect => {
                self.disconnected(None, "reconnect requested by Discord".to_owned());
            }
            ShardEnding::Zombie => {
                self.disconnected(None, "no heartbeat ACK (zombie connection)".to_owned());
            }
            ShardEnding::Lost(error) => self.disconnected(None, error),
            ShardEnding::InvalidFrame(error) => {
                self.disconnected(None, format!("invalid frame: {error}"));
            }
        }
        Some(Duration::ZERO)
    }

    fn disconnected(&self, code: Option<u16>, reason: String) {
        self.push(Delivery::Disconnected {
            shard: self.id,
            code,
            reason,
            resuming: self.session.is_some(),
        });
    }

    fn closed(&self) {
        let session = self
            .session
            .as_ref()
            .filter(|_| self.signals.keep_session.load(Ordering::Acquire))
            .map(|session| session.saved(&self.url));
        self.stopped(None, "closed", session);
    }

    fn stopped(&self, code: Option<u16>, reason: &str, session: Option<SavedSession>) {
        self.signals.stopped.store(true, Ordering::Release);
        self.push(Delivery::Stopped {
            shard: self.id,
            code,
            reason: reason.to_owned(),
            session,
        });
    }

    fn push(&self, delivery: Delivery) {
        self.shared.inbox.push(delivery);
    }
}

fn ends_the_handshake(name: &str) -> bool {
    matches!(name, "READY" | "RESUMED")
}

fn is_voice_update(name: &str) -> bool {
    matches!(name, VOICE_STATE_UPDATE | VOICE_SERVER_UPDATE)
}

async fn connect(
    url: &Url,
    connector: &Connector,
    proxy: Option<&Proxy>,
) -> Result<Socket, String> {
    let builder = ClientBuilder::new()
        .uri(url.as_str())
        .map_err(|error| error.to_string())?
        .connector(connector)
        .limits(Limits::default());
    let Some(proxy) = proxy else {
        let (socket, _) = builder.connect().await.map_err(|error| describe(&error))?;
        return Ok(socket);
    };
    let host = url.host_str().unwrap_or_default();
    let port = url.port_or_known_default().unwrap_or(443);
    let tunnel = proxy.tunnel(host, port).await?;
    let domain = host.trim_start_matches('[').trim_end_matches(']');
    let stream = if url.scheme() == "wss" {
        connector.wrap(domain, tunnel).await
    } else {
        Connector::Plain.wrap(domain, tunnel).await
    }
    .map_err(|error| describe(&error))?;
    let (socket, _) = builder
        .connect_on(stream)
        .await
        .map_err(|error| describe(&error))?;
    Ok(socket)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn heartbeats_start_within_one_interval_then_follow_it() {
        let interval = Duration::from_millis(41_250);
        let hello_at = Instant::now();
        for fraction in [0.0, 0.5, 0.999] {
            let mut heartbeat = ShardHeartbeat::after_hello(interval, hello_at, fraction);
            let first = heartbeat.next;

            heartbeat.beat(false, first);
            heartbeat.beat(true, first + interval);

            assert!(
                first >= hello_at && first < hello_at + interval,
                "{fraction}"
            );
            assert_eq!(heartbeat.next, first + interval * 2, "{fraction}");
        }
    }

    #[test]
    fn a_beat_after_a_stall_waits_half_an_interval_before_the_next() {
        let interval = Duration::from_millis(41_250);
        let hello_at = Instant::now();
        let mut heartbeat = ShardHeartbeat::after_hello(interval, hello_at, 0.0);
        let woke = hello_at + interval * 3;

        heartbeat.beat(false, woke);

        assert_eq!(heartbeat.next, woke + interval / 2);
    }
}
