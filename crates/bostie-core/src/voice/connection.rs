use std::collections::{HashMap, HashSet};
use std::future::{self, Future};
use std::mem;
use std::pin::pin;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use parking_lot::Mutex;
use tokio::sync::{mpsc, watch};
use tokio::time::{Instant, sleep, sleep_until};
use tracing::debug;

use super::close::{self, CloseAction};
use super::media::{Media, bump};
use super::proto;
use super::receiver::Ended;
use super::session::{self, Attempt, Credentials, SessionEnd, VoiceSession};
use super::tap::Snapshot;
use super::types::{ConnectRequest, DisconnectReason, ReceiveTuning, VoiceStatus};
use super::ws::Timeouts;
use super::{Shared, nanos};
use crate::ids::{ChannelId, GuildId, ShardId, UserId};
use crate::inbox::Delivery;

const REJOIN_LIMIT: u32 = 3;
const RESUME_ATTEMPTS: u32 = 2;
const UNKNOWN_LATENCY: u64 = u64::MAX;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct ConnectionTuning {
    pub(crate) move_window: Duration,
    pub(crate) resume_backoff: Duration,
    pub(crate) resume_backoff_max: Duration,
    pub(crate) keepalive_interval: Duration,
    pub(crate) early_ops: usize,
    pub(crate) speaking_coalesce: Duration,
    pub(crate) discovery_interval: Duration,
    pub(crate) stall_after: Duration,
    pub(crate) ws: Timeouts,
    pub(crate) receive: ReceiveTuning,
}

impl Default for ConnectionTuning {
    fn default() -> Self {
        Self {
            move_window: Duration::from_secs(5),
            resume_backoff: Duration::from_millis(500),
            resume_backoff_max: Duration::from_secs(5),
            keepalive_interval: Duration::from_secs(5),
            early_ops: 256,
            speaking_coalesce: Duration::from_millis(200),
            discovery_interval: Duration::from_secs(1),
            stall_after: Duration::from_secs(2),
            ws: Timeouts::default(),
            receive: ReceiveTuning::default(),
        }
    }
}

impl ConnectionTuning {
    fn backoff(&self, failures: u32) -> Duration {
        let base = self.resume_backoff.saturating_mul(1 << failures.min(16));
        base.mul_f64(0.5 + fastrand::f64() / 2.0)
            .min(self.resume_backoff_max)
    }
}

#[derive(Debug)]
pub enum Command {
    Update {
        channel: ChannelId,
        self_mute: bool,
        self_deaf: bool,
    },
    Speaking(u8),
    Disconnect,
    Reconnect,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Step {
    VoiceEvents,
    Websocket,
    Handshake,
    Udp,
    Session,
    Dave,
    Rejoining,
    WaitingForGateway,
    Moving,
    ServerChanged,
    ServerUnavailable,
    SessionReplaced,
}

impl Step {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::VoiceEvents => "voice_events",
            Self::Websocket => "websocket",
            Self::Handshake => "handshake",
            Self::Udp => "udp",
            Self::Session => "session",
            Self::Dave => "dave",
            Self::Rejoining => "rejoining",
            Self::WaitingForGateway => "waiting_for_gateway",
            Self::Moving => "moving",
            Self::ServerChanged => "server_changed",
            Self::ServerUnavailable => "server_unavailable",
            Self::SessionReplaced => "session_replaced",
        }
    }

    const fn waits_for_gateway(self) -> bool {
        matches!(
            self,
            Self::VoiceEvents
                | Self::Rejoining
                | Self::WaitingForGateway
                | Self::Moving
                | Self::ServerChanged
                | Self::ServerUnavailable
                | Self::SessionReplaced
        )
    }
}

pub struct Info {
    pub guild: GuildId,
    pub shard: ShardId,
    pub ssrcs: Mutex<HashMap<u32, (UserId, std::time::Instant)>>,
    pub members: Mutex<HashSet<UserId>>,
    latency_ws: AtomicU64,
    latency_udp: AtomicU64,
}

impl Info {
    pub fn new(guild: GuildId, shard: ShardId) -> Self {
        Self {
            guild,
            shard,
            ssrcs: Mutex::new(HashMap::new()),
            members: Mutex::new(HashSet::new()),
            latency_ws: AtomicU64::new(UNKNOWN_LATENCY),
            latency_udp: AtomicU64::new(UNKNOWN_LATENCY),
        }
    }

    pub fn latency(&self) -> (Option<Duration>, Option<Duration>) {
        let read = |latency: &AtomicU64| match latency.load(Ordering::Relaxed) {
            UNKNOWN_LATENCY => None,
            nanos => Some(Duration::from_nanos(nanos)),
        };
        (read(&self.latency_ws), read(&self.latency_udp))
    }

    pub fn set_latency_ws(&self, latency: Duration) {
        self.latency_ws
            .store(nanos(latency).min(UNKNOWN_LATENCY - 1), Ordering::Relaxed);
    }

    pub fn set_latency_udp(&self, latency: Duration) {
        self.latency_udp
            .store(nanos(latency).min(UNKNOWN_LATENCY - 1), Ordering::Relaxed);
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Ending {
    pub reason: DisconnectReason,
    pub code: Option<u16>,
    pub detail: String,
}

impl Ending {
    pub fn new(reason: DisconnectReason, code: Option<u16>, detail: impl Into<String>) -> Self {
        Self {
            reason,
            code,
            detail: detail.into(),
        }
    }

    pub fn leaves_the_channel(&self) -> bool {
        !matches!(
            self.reason,
            DisconnectReason::Removed | DisconnectReason::Closed
        )
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GatewayEvent {
    Removed,
    Moved,
    SessionReplaced,
    ServerChanged,
    ServerUnavailable,
}

enum Next {
    Wait,
    Identify(Credentials),
    Resume,
    Rejoin,
    WaitForGateway(DisconnectReason, u16),
    Event(GatewayEvent),
    End(Ending),
}

#[derive(Debug, Default)]
struct Seen {
    state: u64,
    server: u64,
    main: u64,
}

pub(super) struct Connection {
    pub(super) shared: Arc<Shared>,
    pub(super) guild: GuildId,
    pub(super) user: UserId,
    pub(super) info: Arc<Info>,
    pub(super) media: Arc<Media>,
    pub(super) commands: mpsc::Receiver<Command>,
    pub(super) stop: watch::Receiver<bool>,
    pub(super) gateway: watch::Receiver<Snapshot>,
    pub(super) speaking: u8,
    pub(super) voice: Option<VoiceSession>,
    pub(super) generation: u64,
    pub(super) deadline: Option<Instant>,
    pub(super) fresh: bool,
    pub(super) tuning: ConnectionTuning,
    shard: ShardId,
    self_mute: bool,
    self_deaf: bool,
    target: ChannelId,
    requested: Option<ChannelId>,
    reported: Option<[bool; 4]>,
    deaf: bool,
    status: VoiceStatus,
    step: Step,
    timeout: Duration,
    server_change_delay: Duration,
    server_moved: bool,
    seen: Seen,
    server_used: u64,
    server_settled: u64,
    rejoins: u32,
    resume_attempts: u32,
    last_code: Option<u16>,
    last_failure: Option<String>,
    connected: bool,
    joined: bool,
}

pub(super) struct Inputs {
    pub commands: mpsc::Receiver<Command>,
    pub stop: watch::Receiver<bool>,
    pub gateway: watch::Receiver<Snapshot>,
}

impl Connection {
    pub(super) fn new(
        shared: Arc<Shared>,
        request: &ConnectRequest,
        info: Arc<Info>,
        media: Arc<Media>,
        inputs: Inputs,
    ) -> Self {
        let seen = Seen {
            main: inputs.gateway.borrow().main_session,
            ..Seen::default()
        };
        let tuning = shared.tuning.connection;
        Self {
            shared,
            guild: request.guild,
            user: request.user,
            info,
            media,
            commands: inputs.commands,
            stop: inputs.stop,
            gateway: inputs.gateway,
            speaking: request.speaking,
            voice: None,
            generation: 0,
            deadline: Some(Instant::now() + request.timeout),
            fresh: false,
            tuning,
            shard: request.shard,
            self_mute: request.self_mute,
            self_deaf: request.self_deaf,
            target: request.channel,
            requested: Some(request.channel),
            reported: None,
            deaf: false,
            status: VoiceStatus::Connecting,
            step: Step::VoiceEvents,
            timeout: request.timeout,
            server_change_delay: request.server_change_delay,
            server_moved: false,
            seen,
            server_used: 0,
            server_settled: 0,
            rejoins: 0,
            resume_attempts: 0,
            last_code: None,
            last_failure: None,
            connected: false,
            joined: false,
        }
    }

    pub(super) async fn run(mut self) -> Ending {
        self.announce(VoiceStatus::Connecting, Step::VoiceEvents.as_str());
        let mut next = Next::Wait;
        loop {
            next = match next {
                Next::Wait => self.wait().await,
                Next::Identify(credentials) => {
                    let end = session::run(&mut self, Attempt::Identify(credentials)).await;
                    self.after(end)
                }
                Next::Resume => self.resume().await,
                Next::Rejoin => self.rejoin().await,
                Next::WaitForGateway(reason, code) => self.wait_for_gateway(reason, code).await,
                Next::Event(event) => self.on_event(event).await,
                Next::End(ending) => {
                    self.voice = None;
                    return ending;
                }
            };
        }
    }

    pub(super) fn set_step(&mut self, step: Step) {
        self.step = step;
    }

    pub(super) fn became_ready(&mut self) {
        self.deadline = None;
        self.connected = true;
        self.last_failure = None;
        self.server_settled = self.server_used;
        if let Some(voice) = &mut self.voice {
            voice.resumable = true;
        }
        self.media.set_reconnecting(false);
        self.set_status(VoiceStatus::Ready, "");
    }

    pub(super) fn stable(&mut self) {
        self.rejoins = 0;
        self.resume_attempts = 0;
    }

    pub(super) fn is_ready(&self) -> bool {
        self.status == VoiceStatus::Ready
    }

    pub(super) fn dave_channel(&self) -> ChannelId {
        self.gateway
            .borrow()
            .state
            .as_ref()
            .and_then(|state| state.channel)
            .unwrap_or(self.target)
    }

    pub(super) fn push(&self, delivery: Delivery) {
        self.shared.inbox.push(delivery);
    }

    pub(super) fn command(&mut self, command: Option<Command>) -> Option<SessionEnd> {
        match command {
            None => Some(SessionEnd::Stopped),
            Some(Command::Disconnect) => Some(SessionEnd::Disconnect),
            Some(Command::Reconnect) => Some(SessionEnd::Reconnect),
            Some(Command::Speaking(flags)) => {
                self.speaking = flags;
                None
            }
            Some(Command::Update {
                channel,
                self_mute,
                self_deaf,
            }) => {
                self.requested = Some(channel);
                self.self_mute = self_mute;
                self.self_deaf = self_deaf;
                None
            }
        }
    }

    pub(super) fn poll_gateway(&mut self) -> Option<GatewayEvent> {
        let snapshot = self.gateway.borrow_and_update().clone();
        if snapshot.state_seq > self.seen.state {
            self.seen.state = snapshot.state_seq;
            if let Some(state) = snapshot
                .state
                .as_ref()
                .filter(|state| self.joined || state.channel.is_some())
            {
                self.joined = true;
                let flags = [state.self_mute, state.self_deaf, state.mute, state.suppress];
                if self.reported.replace(flags) != Some(flags) {
                    self.push(Delivery::VoiceSelf {
                        guild: self.guild,
                        self_mute: state.self_mute,
                        self_deaf: state.self_deaf,
                        mute: state.mute,
                        suppress: state.suppress,
                    });
                }
                if mem::replace(&mut self.deaf, state.deaf) != state.deaf {
                    self.push(Delivery::VoiceDeaf {
                        guild: self.guild,
                        deaf: state.deaf,
                    });
                }
                match state.channel {
                    None => return Some(GatewayEvent::Removed),
                    Some(channel) if channel != self.target => {
                        let from = mem::replace(&mut self.target, channel);
                        self.media.end_receivers(Ended::Moved);
                        self.push(Delivery::VoiceMoved {
                            guild: self.guild,
                            from,
                            to: channel,
                            by_us: self.requested == Some(channel),
                        });
                        if !(self.fresh && self.server_used > self.server_settled) {
                            return Some(GatewayEvent::Moved);
                        }
                    }
                    Some(_) => {}
                }
            }
        }
        if snapshot.main_session > self.seen.main {
            self.seen.main = snapshot.main_session;
            return Some(GatewayEvent::SessionReplaced);
        }
        if snapshot.server_seq > self.seen.server {
            self.seen.server = snapshot.server_seq;
            let endpoint = snapshot
                .server
                .as_ref()
                .and_then(|server| server.endpoint.as_ref());
            return Some(match endpoint {
                Some(_) => GatewayEvent::ServerChanged,
                None => GatewayEvent::ServerUnavailable,
            });
        }
        None
    }

    pub(super) async fn interruptible<F: Future>(
        &mut self,
        future: F,
    ) -> Result<F::Output, SessionEnd> {
        let mut future = pin!(future);
        loop {
            if let Some(event) = self.poll_gateway() {
                return Err(SessionEnd::Gateway(event));
            }
            let deadline = self.deadline;
            tokio::select! {
                biased;
                command = self.commands.recv() => match self.command(command) {
                    None | Some(SessionEnd::Reconnect) => {}
                    Some(end) => return Err(end),
                },
                _ = self.stop.changed() => return Err(SessionEnd::Stopped),
                changed = self.gateway.changed() => {
                    if changed.is_err() {
                        return Err(SessionEnd::Stopped);
                    }
                }
                () = self.media.lost.notified() => return Err(SessionEnd::MediaLost),
                () = until(deadline) => return Err(SessionEnd::TimedOut),
                output = &mut future => return Ok(output),
            }
        }
    }

    fn announce(&mut self, status: VoiceStatus, detail: &str) {
        self.status = status;
        self.push(Delivery::VoiceState {
            guild: self.guild,
            status,
            reason: None,
            code: None,
            detail: detail.into(),
        });
    }

    fn set_status(&mut self, status: VoiceStatus, detail: &str) {
        if status != VoiceStatus::Ready && self.deadline.is_none() {
            self.deadline = Some(Instant::now() + self.timeout);
        }
        if status != self.status {
            self.announce(status, detail);
        }
    }

    fn reconnecting(&mut self, step: Step) {
        self.step = step;
        let status = if self.connected {
            self.media.set_reconnecting(true);
            VoiceStatus::Reconnecting
        } else {
            VoiceStatus::Connecting
        };
        self.set_status(status, step.as_str());
    }

    fn drop_session(&mut self) {
        self.voice = None;
        self.media.install(None);
    }

    fn offered_server(&self) -> Option<(Credentials, u64)> {
        let snapshot = self.gateway.borrow();
        if snapshot.server_seq <= self.server_used {
            return None;
        }
        let server = snapshot.server.as_ref()?;
        let endpoint = server.endpoint.clone()?;
        let state = snapshot
            .state
            .as_ref()
            .filter(|state| state.channel == Some(self.target))?;
        let credentials = Credentials {
            session_id: state.session_id.clone(),
            token: server.token.clone(),
            endpoint,
        };
        Some((credentials, snapshot.server_seq))
    }

    fn take_server(&mut self) -> Option<Credentials> {
        let (credentials, seq) = self.offered_server()?;
        self.server_used = seq;
        self.seen.server = self.seen.server.max(seq);
        Some(credentials)
    }

    async fn wait(&mut self) -> Next {
        loop {
            if self.server_moved && self.offered_server().is_some() {
                self.server_moved = false;
                match self.let_the_call_move().await {
                    Ok(()) => {}
                    Err(SessionEnd::Gateway(event)) => match self.on_event(event).await {
                        Next::Wait => continue,
                        next => return next,
                    },
                    Err(end) => return self.after(end),
                }
            }
            if let Some(credentials) = self.take_server() {
                return Next::Identify(credentials);
            }
            match self.interruptible(future::pending::<()>()).await {
                Ok(()) | Err(SessionEnd::Gateway(GatewayEvent::ServerChanged)) => {}
                Err(SessionEnd::Gateway(event)) => match self.on_event(event).await {
                    Next::Wait => {}
                    next => return next,
                },
                Err(end) => return self.after(end),
            }
        }
    }

    async fn let_the_call_move(&mut self) -> Result<(), SessionEnd> {
        let paused = self.deadline.take();
        let started = Instant::now();
        let waited = self.interruptible(sleep(self.server_change_delay)).await;
        self.deadline = paused.map(|deadline| deadline + started.elapsed());
        waited
    }

    fn after(&mut self, end: SessionEnd) -> Next {
        let resumable = self.voice.as_ref().is_some_and(|voice| voice.resumable);
        let fallback = if resumable {
            Next::Resume
        } else {
            Next::Rejoin
        };
        match end {
            SessionEnd::Closed(code) => {
                self.last_code = Some(code);
                let (action, text) = close::classify(code);
                self.last_failure = Some(format!("closed with {code}: {text}"));
                match action {
                    CloseAction::Resume => fallback,
                    CloseAction::Rejoin => Next::Rejoin,
                    CloseAction::WaitForGateway(reason) => Next::WaitForGateway(reason, code),
                    CloseAction::Fatal(reason) => Next::End(Ending::new(reason, Some(code), text)),
                }
            }
            SessionEnd::Lost(text) => {
                self.last_failure = Some(text);
                fallback
            }
            SessionEnd::Zombie => {
                self.last_failure = Some("no heartbeat ACK".to_owned());
                fallback
            }
            SessionEnd::Reconnect => fallback,
            SessionEnd::Invalid(text) | SessionEnd::Failed(text) => {
                self.last_failure = Some(text);
                Next::Rejoin
            }
            SessionEnd::NonceExhausted => {
                self.last_failure = Some("the transport nonce is about to wrap".to_owned());
                Next::Rejoin
            }
            SessionEnd::Gateway(event) => Next::Event(event),
            SessionEnd::Disconnect => Next::End(Ending::new(DisconnectReason::Requested, None, "")),
            SessionEnd::DaveFailed(text) => {
                Next::End(Ending::new(DisconnectReason::DaveFailed, None, text))
            }
            SessionEnd::Fatal(reason, text) => Next::End(Ending::new(reason, None, text)),
            SessionEnd::TimedOut => Next::End(self.timed_out()),
            SessionEnd::Stopped => Next::End(Ending::new(
                DisconnectReason::Closed,
                None,
                "the core was closed",
            )),
            SessionEnd::MediaLost => Next::End(Ending::new(
                DisconnectReason::ProtocolError,
                None,
                "internal error: the media lane ended",
            )),
        }
    }

    async fn resume(&mut self) -> Next {
        if self.resume_attempts >= RESUME_ATTEMPTS {
            return Next::Rejoin;
        }
        let delay = self.tuning.backoff(self.resume_attempts);
        self.resume_attempts += 1;
        let cause = self.last_failure.clone().unwrap_or_default();
        self.set_status(VoiceStatus::Resuming, &cause);
        if let Err(end) = self.interruptible(sleep(delay)).await {
            return self.after(end);
        }
        let end = session::run(self, Attempt::Resume).await;
        if self.is_ready() {
            return self.after(end);
        }
        let failed = match &end {
            SessionEnd::Closed(code) => close::classify(*code).0 == CloseAction::Resume,
            SessionEnd::Lost(_)
            | SessionEnd::Zombie
            | SessionEnd::Invalid(_)
            | SessionEnd::Failed(_) => true,
            _ => false,
        };
        match self.after(end) {
            Next::Rejoin if failed => Next::Resume,
            next => next,
        }
    }

    async fn rejoin(&mut self) -> Next {
        if self.rejoins >= REJOIN_LIMIT {
            let reason = close::rejoin_limit_reason(self.last_code);
            let detail = format!(
                "the voice connection failed {} times in a row: {}",
                REJOIN_LIMIT + 1,
                self.last_failure.as_deref().unwrap_or("no reason given")
            );
            return Next::End(Ending::new(reason, self.last_code, detail));
        }
        self.rejoins += 1;
        bump(&self.media.counters.rejoins);
        self.drop_session();
        self.reconnecting(Step::Rejoining);
        match self.send_voice_state(Some(self.target)).await {
            Ok(()) => Next::Wait,
            Err(end) => self.after(end),
        }
    }

    async fn wait_for_gateway(&mut self, reason: DisconnectReason, code: u16) -> Next {
        self.drop_session();
        self.reconnecting(Step::WaitingForGateway);
        let window = self.tuning.move_window;
        match self.interruptible(sleep(window)).await {
            Ok(()) => Next::End(Ending::new(
                reason,
                Some(code),
                format!("closed with {code} and no voice update followed within {window:?}"),
            )),
            Err(SessionEnd::Gateway(event)) => self.on_event(event).await,
            Err(end) => self.after(end),
        }
    }

    async fn on_event(&mut self, event: GatewayEvent) -> Next {
        let step = match event {
            GatewayEvent::Removed => {
                return Next::End(Ending::new(
                    DisconnectReason::Removed,
                    None,
                    "the bot left the voice channel or was removed from it",
                ));
            }
            GatewayEvent::Moved => Step::Moving,
            GatewayEvent::ServerChanged => Step::ServerChanged,
            GatewayEvent::ServerUnavailable => Step::ServerUnavailable,
            GatewayEvent::SessionReplaced => Step::SessionReplaced,
        };
        self.server_moved = self.connected
            && !self.server_change_delay.is_zero()
            && matches!(
                event,
                GatewayEvent::ServerChanged | GatewayEvent::ServerUnavailable
            );
        self.drop_session();
        self.reconnecting(step);
        if event == GatewayEvent::SessionReplaced
            && let Err(end) = self.send_voice_state(Some(self.target)).await
        {
            return self.after(end);
        }
        Next::Wait
    }

    async fn send_voice_state(&mut self, channel: Option<ChannelId>) -> Result<(), SessionEnd> {
        let payload =
            proto::voice_state_update(self.guild, channel, self.self_mute, self.self_deaf);
        let mut failures = 0;
        loop {
            match (self.shared.send)(self.shard, payload.clone()) {
                Ok(()) => return Ok(()),
                Err(error) => {
                    debug!(%error, guild = %self.guild, "the voice state update was not queued");
                    let delay = self.tuning.backoff(failures);
                    failures += 1;
                    self.interruptible(sleep(delay)).await?;
                }
            }
        }
    }

    fn timed_out(&self) -> Ending {
        let why = match &self.last_failure {
            Some(failure) => failure.clone(),
            None if self.step.waits_for_gateway() => self.missing_events().to_owned(),
            None => format!("no progress within {:?}", self.timeout),
        };
        Ending::new(
            DisconnectReason::Timeout,
            None,
            format!("{}: {why}", self.step.as_str()),
        )
    }

    fn missing_events(&self) -> &'static str {
        let snapshot = self.gateway.borrow();
        let state = snapshot
            .state
            .as_ref()
            .is_some_and(|state| state.channel == Some(self.target));
        let server = snapshot.server_seq > self.server_used
            && snapshot
                .server
                .as_ref()
                .is_some_and(|server| server.endpoint.is_some());
        match (state, server) {
            (false, false) => {
                "no VOICE_STATE_UPDATE or VOICE_SERVER_UPDATE arrived for the channel"
            }
            (true, false) => "no VOICE_SERVER_UPDATE with an endpoint arrived",
            (false, true) => "no VOICE_STATE_UPDATE arrived for the channel",
            (true, true) => "the voice events arrived too late",
        }
    }
}

pub(super) async fn until(deadline: Option<Instant>) {
    match deadline {
        Some(deadline) => sleep_until(deadline).await,
        None => future::pending().await,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backoff_doubles_with_jitter_and_stays_under_its_cap() {
        let tuning = ConnectionTuning::default();
        for failures in 0..40 {
            let delay = tuning.backoff(failures);
            let base = tuning.resume_backoff.saturating_mul(1 << failures.min(16));
            assert!(delay <= tuning.resume_backoff_max, "{failures}: {delay:?}");
            assert!(
                delay >= (base / 2).min(tuning.resume_backoff_max),
                "{failures}: {delay:?}"
            );
        }
    }

    #[test]
    fn latency_is_unknown_until_measured() {
        let info = Info::new(GuildId::from(1), ShardId::from(0));
        assert_eq!(info.latency(), (None, None));

        info.set_latency_ws(Duration::from_millis(12));
        info.set_latency_udp(Duration::from_millis(3));

        assert_eq!(
            info.latency(),
            (
                Some(Duration::from_millis(12)),
                Some(Duration::from_millis(3))
            )
        );
    }

    #[test]
    fn only_removed_and_closed_endings_stay_in_the_channel() {
        for reason in [
            DisconnectReason::Requested,
            DisconnectReason::CallTerminated,
            DisconnectReason::RateLimited,
            DisconnectReason::Timeout,
            DisconnectReason::SessionInvalid,
            DisconnectReason::AuthenticationFailed,
            DisconnectReason::DaveRequired,
            DisconnectReason::DaveFailed,
            DisconnectReason::ProtocolError,
        ] {
            assert!(
                Ending::new(reason, None, "").leaves_the_channel(),
                "{reason:?}"
            );
        }
        assert!(!Ending::new(DisconnectReason::Removed, None, "").leaves_the_channel());
        assert!(!Ending::new(DisconnectReason::Closed, None, "").leaves_the_channel());
    }
}
