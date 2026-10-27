use std::ops::RangeInclusive;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use bytes::Bytes;
use futures_util::{SinkExt, StreamExt};
use reqwest::Url;
use tokio::net::TcpStream;
use tokio::sync::{Notify, mpsc};
use tokio::time::{Instant, sleep, sleep_until, timeout};
use tokio_websockets::{
    ClientBuilder, CloseCode, Connector, Limits, MaybeTlsStream, Message, WebSocketStream,
};
use tracing::debug;

use super::Shared;
use super::close::{self, CloseAction};
use super::envelope::{self, Forward, Frame};
use super::inflate::Inflater;
use super::limiter::SendLimiter;
use crate::error::describe;
use crate::ids::ShardId;
use crate::inbox::{DISPATCH_BYTES_LIMIT, Delivery};

#[cfg(not(test))]
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
#[cfg(test)]
const CONNECT_TIMEOUT: Duration = Duration::from_secs(2);

#[cfg(not(test))]
const HELLO_TIMEOUT: Duration = Duration::from_secs(20);
#[cfg(test)]
const HELLO_TIMEOUT: Duration = Duration::from_secs(1);

#[cfg(not(test))]
const CLOSE_TIMEOUT: Duration = Duration::from_secs(2);
#[cfg(test)]
const CLOSE_TIMEOUT: Duration = Duration::from_millis(200);

#[cfg(not(test))]
const INVALID_SESSION_DELAY: RangeInclusive<Duration> =
    Duration::from_secs(1)..=Duration::from_secs(5);
#[cfg(test)]
const INVALID_SESSION_DELAY: RangeInclusive<Duration> =
    Duration::from_millis(10)..=Duration::from_millis(30);

#[cfg(not(test))]
const BACKOFF_BASE: Duration = Duration::from_secs(1);
#[cfg(test)]
const BACKOFF_BASE: Duration = Duration::from_millis(10);

#[cfg(not(test))]
const BACKOFF_MAX: Duration = Duration::from_secs(60);
#[cfg(test)]
const BACKOFF_MAX: Duration = Duration::from_millis(100);

const NORMAL_CLOSE: u16 = 1000;
const SESSION_KEEPING_CLOSE: u16 = 4000;
const GATEWAY_QUERY: &str = "v=10&encoding=json&compress=zstd-stream";
const IDENTIFY_PREFIX: &[u8] = br#"{"op":2,"d":"#;
pub(super) const UNKNOWN_LATENCY: u64 = u64::MAX;

type Socket = WebSocketStream<MaybeTlsStream<TcpStream>>;

pub(super) fn gateway_url(text: &str) -> Option<Url> {
    let mut url = Url::parse(text).ok()?;
    if !matches!(url.scheme(), "ws" | "wss") || ClientBuilder::new().uri(url.as_str()).is_err() {
        return None;
    }
    url.set_query(Some(GATEWAY_QUERY));
    Some(url)
}

pub(super) struct Signals {
    pub(super) close: Notify,
    pub(super) reconnect: Notify,
    pub(super) latency: AtomicU64,
}

impl Signals {
    pub(super) fn new() -> Self {
        Self {
            close: Notify::new(),
            reconnect: Notify::new(),
            latency: AtomicU64::new(UNKNOWN_LATENCY),
        }
    }
}

struct Session {
    id: String,
    resume_url: Option<Url>,
    seq: u64,
}

#[derive(Debug)]
enum Ending {
    Stop,
    Requested,
    Reconnect,
    InvalidSession { resumable: bool },
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

struct Heartbeat {
    interval: Duration,
    next: Instant,
    sent_at: Option<Instant>,
    received: bool,
    paused: bool,
}

struct Connection {
    socket: Socket,
    inflater: Inflater,
    hello_deadline: Instant,
    heartbeat: Option<Heartbeat>,
    limiter: Option<SendLimiter>,
    identify_at: Option<Instant>,
    running: bool,
    held: Option<Held>,
}

impl Connection {
    async fn send(&mut self, payload: Bytes) -> Result<(), Ending> {
        self.socket
            .send(Message::text(payload))
            .await
            .map_err(|error| Ending::Lost(describe(&error)))?;
        if let Some(limiter) = &mut self.limiter {
            limiter.record(Instant::now());
        }
        Ok(())
    }

    async fn close(&mut self, code: u16) {
        let closing = async {
            let frame = Message::close(CloseCode::try_from(code).ok(), "");
            if self.socket.send(frame).await.is_ok() {
                while self.socket.next().await.is_some() {}
            }
        };
        if timeout(CLOSE_TIMEOUT, closing).await.is_err() {
            debug!(code, "the close handshake timed out");
        }
    }
}

pub(super) struct Shard {
    id: ShardId,
    url: Url,
    identify: Bytes,
    shared: Arc<Shared>,
    commands: mpsc::Receiver<Bytes>,
    signals: Arc<Signals>,
    session: Option<Session>,
    had_session: bool,
    failures: u32,
}

impl Shard {
    pub(super) fn new(
        id: ShardId,
        url: Url,
        identify: Bytes,
        shared: Arc<Shared>,
        commands: mpsc::Receiver<Bytes>,
        signals: Arc<Signals>,
    ) -> Self {
        Self {
            id,
            url,
            identify,
            shared,
            commands,
            signals,
            session: None,
            had_session: false,
            failures: 0,
        }
    }

    pub(super) async fn run(mut self) {
        let mut delay = Duration::ZERO;
        loop {
            if !self.idle(delay.saturating_add(self.backoff())).await
                || !self.await_identify_budget().await
            {
                self.stopped(None, "closed");
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

    fn backoff(&self) -> Duration {
        let Some(doublings) = self.failures.checked_sub(1) else {
            return Duration::ZERO;
        };
        let factor = 1_u32.checked_shl(doublings).unwrap_or(u32::MAX);
        BACKOFF_BASE
            .saturating_mul(factor)
            .min(BACKOFF_MAX)
            .mul_f64(0.5 + fastrand::f64() / 2.0)
    }

    async fn connect_and_serve(&mut self) -> Ending {
        let Some(inflater) = Inflater::new() else {
            return Ending::Lost("zstd could not allocate a decompression context".to_owned());
        };
        let url = self
            .session
            .as_ref()
            .and_then(|session| session.resume_url.as_ref())
            .unwrap_or(&self.url);
        debug!(shard = %self.id, %url, "connecting");
        let socket = tokio::select! {
            biased;
            () = self.signals.close.notified() => return Ending::Stop,
            () = self.signals.reconnect.notified() => return Ending::Requested,
            connected = timeout(CONNECT_TIMEOUT, connect(url, &self.shared.connector)) => match connected {
                Ok(Ok(socket)) => socket,
                Ok(Err(error)) => return Ending::Unreachable(error),
                Err(_) => return Ending::Unreachable(format!("no connection within {CONNECT_TIMEOUT:?}")),
            },
        };
        let mut connection = Connection {
            socket,
            inflater,
            hello_deadline: Instant::now() + HELLO_TIMEOUT,
            heartbeat: None,
            limiter: None,
            identify_at: None,
            running: false,
            held: None,
        };
        let ending = self.serve(&mut connection).await;
        match ending {
            Ending::Stop => connection.close(NORMAL_CLOSE).await,
            Ending::Closed(_) | Ending::Lost(_) => {}
            _ => connection.close(SESSION_KEEPING_CLOSE).await,
        }
        ending
    }

    async fn serve(&mut self, connection: &mut Connection) -> Ending {
        loop {
            let now = Instant::now();
            let tick = connection
                .heartbeat
                .as_ref()
                .map_or(connection.hello_deadline, |heartbeat| heartbeat.next);
            let commands_at = connection
                .limiter
                .as_mut()
                .filter(|_| connection.running)
                .map(|limiter| limiter.next_slot(now));
            let step = tokio::select! {
                biased;
                () = self.signals.close.notified() => Err(Ending::Stop),
                () = self.signals.reconnect.notified() => Err(Ending::Requested),
                () = sleep_until(tick) => self.on_tick(connection).await,
                () = sleep_until(connection.identify_at.unwrap_or(now)), if connection.identify_at.is_some() => {
                    self.send_identify(connection).await
                }
                message = connection.socket.next(), if connection.held.is_none() => {
                    self.on_message(connection, message).await
                }
                () = self.shared.inbox.wait_for_room(), if connection.held.is_some() => {
                    if let Some(held) = connection.held.take() {
                        self.deliver(connection, held);
                    }
                    Ok(())
                }
                command = self.commands.recv(), if commands_at.is_some_and(|at| at <= now) => match command {
                    Some(payload) => connection.send(payload).await,
                    None => Err(Ending::Stop),
                },
                () = sleep_until(commands_at.unwrap_or(now)), if commands_at.is_some_and(|at| at > now) => Ok(()),
            };
            if let Err(ending) = step {
                return ending;
            }
        }
    }

    async fn on_tick(&self, connection: &mut Connection) -> Result<(), Ending> {
        let Some(heartbeat) = &mut connection.heartbeat else {
            return Err(Ending::Unreachable(format!(
                "no HELLO within {HELLO_TIMEOUT:?}"
            )));
        };
        if !heartbeat.received && !heartbeat.paused {
            return Err(Ending::Zombie);
        }
        heartbeat.received = false;
        heartbeat.paused = connection.held.is_some();
        heartbeat.next += heartbeat.interval;
        self.send_heartbeat(connection).await
    }

    async fn send_heartbeat(&self, connection: &mut Connection) -> Result<(), Ending> {
        let payload = match &self.session {
            Some(session) => format!(r#"{{"op":1,"d":{}}}"#, session.seq),
            None => r#"{"op":1,"d":null}"#.to_owned(),
        };
        connection.send(Bytes::from(payload)).await?;
        if let Some(heartbeat) = &mut connection.heartbeat {
            heartbeat.sent_at = Some(Instant::now());
        }
        Ok(())
    }

    async fn send_identify(&self, connection: &mut Connection) -> Result<(), Ending> {
        connection.identify_at = None;
        let mut payload = Vec::with_capacity(IDENTIFY_PREFIX.len() + self.identify.len() + 1);
        payload.extend_from_slice(IDENTIFY_PREFIX);
        payload.extend_from_slice(&self.identify);
        payload.push(b'}');
        debug!(shard = %self.id, "identifying");
        connection.send(Bytes::from(payload)).await
    }

    async fn on_message(
        &mut self,
        connection: &mut Connection,
        message: Option<Result<Message, tokio_websockets::Error>>,
    ) -> Result<(), Ending> {
        let message = match message {
            Some(Ok(message)) => message,
            Some(Err(error)) => return Err(Ending::Lost(describe(&error))),
            None => {
                return Err(Ending::Lost(
                    "the connection ended without a close frame".to_owned(),
                ));
            }
        };
        if let Some((code, _)) = message.as_close() {
            return Err(Ending::Closed(code.into()));
        }
        if let Some(heartbeat) = &mut connection.heartbeat {
            heartbeat.received = true;
        }
        let payload = if message.is_binary() {
            connection
                .inflater
                .inflate(message.as_payload())
                .map_err(|error| Ending::InvalidFrame(error.to_string()))?
        } else if message.is_text() {
            message.as_payload()
        } else {
            return Ok(());
        };
        let frame = Frame::parse(payload, |name| self.wants(name))
            .map_err(|error| Ending::InvalidFrame(error.to_string()))?;
        self.on_frame(connection, frame).await
    }

    fn wants(&self, name: &str) -> bool {
        matches!(name, "READY" | "RESUMED")
            || self
                .shared
                .filter
                .read()
                .as_ref()
                .is_none_or(|names| names.contains(name))
    }

    async fn on_frame(&mut self, connection: &mut Connection, frame: Frame) -> Result<(), Ending> {
        match frame {
            Frame::Dispatch { seq, forward: None } => self.commit(seq),
            Frame::Dispatch {
                seq,
                forward: Some(forward),
            } => self.forward(connection, Held { seq, forward }),
            Frame::Heartbeat => return self.send_heartbeat(connection).await,
            Frame::HeartbeatAck => self.record_latency(connection),
            Frame::Hello { heartbeat_interval } => {
                return self.on_hello(connection, heartbeat_interval).await;
            }
            Frame::Reconnect => return Err(Ending::Reconnect),
            Frame::InvalidSession { resumable } => {
                return Err(Ending::InvalidSession { resumable });
            }
            Frame::Other { op } => debug!(shard = %self.id, op, "ignoring an unknown opcode"),
        }
        Ok(())
    }

    async fn on_hello(
        &self,
        connection: &mut Connection,
        interval: Duration,
    ) -> Result<(), Ending> {
        let now = Instant::now();
        connection.heartbeat = Some(Heartbeat {
            interval,
            next: now + interval.mul_f64(fastrand::f64()),
            sent_at: None,
            received: true,
            paused: false,
        });
        connection.limiter = Some(SendLimiter::new(interval));
        let Some(session) = &self.session else {
            connection.identify_at = Some(self.shared.identify.reserve(now));
            return Ok(());
        };
        debug!(shard = %self.id, seq = session.seq, "resuming");
        let resume = sonic_rs::json!({
            "op": 6,
            "d": {"token": &*self.shared.token, "session_id": &session.id, "seq": session.seq},
        });
        let payload = sonic_rs::to_vec(&resume).map_err(|error| Ending::Lost(error.to_string()))?;
        connection.send(Bytes::from(payload)).await
    }

    fn record_latency(&self, connection: &Connection) {
        let Some(sent_at) = connection.heartbeat.as_ref().and_then(|beat| beat.sent_at) else {
            return;
        };
        let nanos = u64::try_from(sent_at.elapsed().as_nanos()).unwrap_or(UNKNOWN_LATENCY - 1);
        self.signals.latency.store(nanos, Ordering::Relaxed);
    }

    fn forward(&mut self, connection: &mut Connection, held: Held) {
        let queued_bytes = self.shared.inbox.queued_bytes();
        if queued_bytes < DISPATCH_BYTES_LIMIT {
            self.deliver(connection, held);
            return;
        }
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

    fn deliver(&mut self, connection: &mut Connection, held: Held) {
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

    fn begin_session(&mut self, connection: &mut Connection, data: &[u8]) {
        if self.had_session {
            while self.commands.try_recv().is_ok() {}
        }
        self.had_session = true;
        self.session = envelope::ready_session(data).map(|ready| Session {
            id: ready.id,
            resume_url: ready.resume_url.as_deref().and_then(gateway_url),
            seq: 0,
        });
        self.start_running(connection);
    }

    fn start_running(&mut self, connection: &mut Connection) {
        debug!(shard = %self.id, "running");
        connection.running = true;
        self.failures = 0;
    }

    fn commit(&mut self, seq: u64) {
        if let Some(session) = &mut self.session {
            session.seq = seq;
        }
    }

    fn conclude(&mut self, ending: Ending) -> Option<Duration> {
        match ending {
            Ending::Stop => {
                self.stopped(None, "closed");
                return None;
            }
            Ending::Closed(code) => {
                let (action, text) = close::classify(code);
                match action {
                    CloseAction::Fatal => {
                        self.stopped(Some(code), text);
                        return None;
                    }
                    CloseAction::Reidentify => self.session = None,
                    CloseAction::Resume => {}
                }
                self.disconnected(Some(code), text.to_owned());
            }
            Ending::InvalidSession { resumable } => {
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
                    let (shortest, longest) =
                        (*INVALID_SESSION_DELAY.start(), *INVALID_SESSION_DELAY.end());
                    return Some(shortest + (longest - shortest).mul_f64(fastrand::f64()));
                }
            }
            Ending::Unreachable(error) => {
                if let Some(session) = &mut self.session {
                    session.resume_url = None;
                }
                self.disconnected(None, error);
            }
            Ending::Requested => self.disconnected(None, "reconnect requested".to_owned()),
            Ending::Reconnect => {
                self.disconnected(None, "reconnect requested by Discord".to_owned());
            }
            Ending::Zombie => {
                self.disconnected(None, "no heartbeat ACK (zombie connection)".to_owned());
            }
            Ending::Lost(error) => self.disconnected(None, error),
            Ending::InvalidFrame(error) => {
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

    fn stopped(&self, code: Option<u16>, reason: &str) {
        self.push(Delivery::Stopped {
            shard: self.id,
            code,
            reason: reason.to_owned(),
        });
    }

    fn push(&self, delivery: Delivery) {
        self.shared.inbox.push(delivery);
    }
}

async fn connect(url: &Url, connector: &Connector) -> Result<Socket, String> {
    let builder = ClientBuilder::new()
        .uri(url.as_str())
        .map_err(|error| error.to_string())?;
    let (socket, _) = builder
        .connector(connector)
        .limits(Limits::default())
        .connect()
        .await
        .map_err(|error| describe(&error))?;
    Ok(socket)
}
