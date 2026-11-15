use std::collections::HashMap;
use std::future;
use std::mem;
use std::pin::pin;
use std::sync::Arc;
use std::time::Duration;

use parking_lot::Mutex;
use tokio::sync::{mpsc, watch};
use tokio::time::{Instant, sleep, sleep_until};
use tracing::debug;

use super::Shared;
use super::close::{self, CloseAction};
use super::media::Media;
use super::proto;
use super::receiver::Ended;
use super::session::{self, Attempt, Credentials, SessionEnd, VoiceSession};
use super::tap::Snapshot;
use super::types::{ConnectRequest, DisconnectReason, ReceiveTuning, VoiceStatus};
use super::ws::Timeouts;
use crate::backoff::backoff;
use crate::ids::{ChannelId, GuildId, ShardId, UserId};
use crate::inbox::Delivery;
use crate::latency::Latency;

const REJOIN_LIMIT: u32 = 3;
const RESUME_ATTEMPTS: u32 = 2;

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
        backoff(self.resume_backoff, self.resume_backoff_max, failures)
    }
}

#[derive(Debug, Clone, Copy)]
pub(crate) enum Command {
    Update {
        channel: ChannelId,
        self_mute: bool,
        self_deaf: bool,
    },
    Speaking(u8),
    ServerChangeDelay(Duration),
    Disconnect,
    Reconnect,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Step {
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
    pub(crate) const fn as_str(self) -> &'static str {
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

pub(crate) struct Info {
    pub(crate) guild: GuildId,
    pub(crate) shard: ShardId,
    pub(crate) ssrcs: Mutex<HashMap<u32, (UserId, std::time::Instant)>>,
    pub(crate) ws_latency: Latency,
    pub(crate) udp_latency: Latency,
}

impl Info {
    pub(crate) fn new(guild: GuildId, shard: ShardId) -> Self {
        Self {
            guild,
            shard,
            ssrcs: Mutex::new(HashMap::new()),
            ws_latency: Latency::default(),
            udp_latency: Latency::default(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Ending {
    pub(crate) reason: DisconnectReason,
    pub(crate) code: Option<u16>,
    pub(crate) detail: String,
}

impl Ending {
    pub(crate) fn new(
        reason: DisconnectReason,
        code: Option<u16>,
        detail: impl Into<String>,
    ) -> Self {
        Self {
            reason,
            code,
            detail: detail.into(),
        }
    }

    pub(crate) fn leaves_the_channel(&self) -> bool {
        !matches!(
            self.reason,
            DisconnectReason::Removed | DisconnectReason::Closed
        )
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum GatewayEvent {
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

struct SelfState {
    shard: ShardId,
    self_mute: bool,
    self_deaf: bool,
    target: ChannelId,
    requested: Option<ChannelId>,
    reported: Option<[bool; 4]>,
    deaf: bool,
    joined: bool,
}

struct Servers {
    used: u64,
    settled: u64,
    moved: bool,
    change_delay: Duration,
}

#[derive(Default)]
struct Failures {
    rejoins: u32,
    resumes: u32,
    code: Option<u16>,
    last: Option<String>,
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
    me: SelfState,
    servers: Servers,
    failures: Failures,
    seen: Seen,
    status: VoiceStatus,
    step: Step,
    timeout: Duration,
    connected: bool,
}

pub(super) struct Inputs {
    pub(super) commands: mpsc::Receiver<Command>,
    pub(super) stop: watch::Receiver<bool>,
    pub(super) gateway: watch::Receiver<Snapshot>,
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
            me: SelfState {
                shard: request.shard,
                self_mute: request.self_mute,
                self_deaf: request.self_deaf,
                target: request.channel,
                requested: Some(request.channel),
                reported: None,
                deaf: false,
                joined: false,
            },
            servers: Servers {
                used: 0,
                settled: 0,
                moved: false,
                change_delay: request.server_change_delay,
            },
            failures: Failures::default(),
            seen,
            status: VoiceStatus::Connecting,
            step: Step::VoiceEvents,
            timeout: request.timeout,
            connected: false,
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
        self.failures.last = None;
        self.servers.settled = self.servers.used;
        if let Some(voice) = &mut self.voice {
            voice.resumable = true;
        }
        self.media.set_reconnecting(false);
        self.set_status(VoiceStatus::Ready, "");
    }

    pub(super) fn stable(&mut self) {
        self.failures = Failures::default();
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
            .unwrap_or(self.me.target)
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
            Some(Command::ServerChangeDelay(delay)) => {
                self.servers.change_delay = delay;
                None
            }
            Some(Command::Update {
                channel,
                self_mute,
                self_deaf,
            }) => {
                self.me.requested = Some(channel);
                self.me.self_mute = self_mute;
                self.me.self_deaf = self_deaf;
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
                .filter(|state| self.me.joined || state.channel.is_some())
            {
                self.me.joined = true;
                let flags = [state.self_mute, state.self_deaf, state.mute, state.suppress];
                if self.me.reported.replace(flags) != Some(flags) {
                    self.push(Delivery::VoiceSelf {
                        guild: self.guild,
                        self_mute: state.self_mute,
                        self_deaf: state.self_deaf,
                        mute: state.mute,
                        suppress: state.suppress,
                    });
                }
                if mem::replace(&mut self.me.deaf, state.deaf) != state.deaf {
                    self.push(Delivery::VoiceDeaf {
                        guild: self.guild,
                        deaf: state.deaf,
                    });
                }
                match state.channel {
                    None => return Some(GatewayEvent::Removed),
                    Some(channel) if channel != self.me.target => {
                        let from = mem::replace(&mut self.me.target, channel);
                        self.media.end_receivers(Ended::Moved);
                        self.push(Delivery::VoiceMoved {
                            guild: self.guild,
                            from,
                            to: channel,
                            by_us: self.me.requested == Some(channel),
                        });
                        if !(self.fresh && self.servers.used > self.servers.settled) {
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
                () = self.media.lost() => return Err(SessionEnd::MediaLost),
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
        if snapshot.server_seq <= self.servers.used {
            return None;
        }
        let server = snapshot.server.as_ref()?;
        let endpoint = server.endpoint.clone()?;
        let state = snapshot
            .state
            .as_ref()
            .filter(|state| state.channel == Some(self.me.target))?;
        let credentials = Credentials {
            session_id: state.session_id.clone(),
            token: server.token.clone(),
            endpoint,
        };
        Some((credentials, snapshot.server_seq))
    }

    fn take_server(&mut self) -> Option<Credentials> {
        let (credentials, seq) = self.offered_server()?;
        self.servers.used = seq;
        self.seen.server = self.seen.server.max(seq);
        Some(credentials)
    }

    async fn wait(&mut self) -> Next {
        loop {
            if self.servers.moved && self.offered_server().is_some() {
                self.servers.moved = false;
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
        let waited = self.interruptible(sleep(self.servers.change_delay)).await;
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
                self.failures.code = Some(code);
                let (action, text) = close::classify(code);
                self.failures.last = Some(format!("closed with {code}: {text}"));
                match action {
                    CloseAction::Resume => fallback,
                    CloseAction::Rejoin => Next::Rejoin,
                    CloseAction::WaitForGateway(reason) => Next::WaitForGateway(reason, code),
                    CloseAction::Fatal(reason) => Next::End(Ending::new(reason, Some(code), text)),
                }
            }
            SessionEnd::Lost(text) => {
                self.failures.last = Some(text);
                fallback
            }
            SessionEnd::Reconnect => fallback,
            SessionEnd::Failed(text) => {
                self.failures.last = Some(text);
                Next::Rejoin
            }
            SessionEnd::NonceExhausted => {
                self.failures.last = Some("the transport nonce is about to wrap".to_owned());
                Next::Rejoin
            }
            SessionEnd::Gateway(event) => Next::Event(event),
            SessionEnd::Disconnect => Next::End(Ending::new(DisconnectReason::Requested, None, "")),
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
        if self.failures.resumes >= RESUME_ATTEMPTS {
            return Next::Rejoin;
        }
        let delay = self.tuning.backoff(self.failures.resumes);
        self.failures.resumes += 1;
        let cause = self.failures.last.clone().unwrap_or_default();
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
            SessionEnd::Lost(_) | SessionEnd::Failed(_) => true,
            _ => false,
        };
        match self.after(end) {
            Next::Rejoin if failed => Next::Resume,
            next => next,
        }
    }

    async fn rejoin(&mut self) -> Next {
        let failures = &mut self.failures;
        if failures.rejoins >= REJOIN_LIMIT {
            let reason = close::rejoin_limit_reason(failures.code);
            let detail = format!(
                "the voice connection failed {} times in a row: {}",
                REJOIN_LIMIT + 1,
                failures.last.as_deref().unwrap_or("no reason given")
            );
            return Next::End(Ending::new(reason, failures.code, detail));
        }
        failures.rejoins += 1;
        self.drop_session();
        self.reconnecting(Step::Rejoining);
        match self.send_voice_state(Some(self.me.target)).await {
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
        self.servers.moved = self.connected
            && !self.servers.change_delay.is_zero()
            && matches!(
                event,
                GatewayEvent::ServerChanged | GatewayEvent::ServerUnavailable
            );
        self.drop_session();
        self.reconnecting(step);
        if event == GatewayEvent::SessionReplaced {
            self.servers.used = self.gateway.borrow().server_seq;
            if let Err(end) = self.send_voice_state(Some(self.me.target)).await {
                return self.after(end);
            }
        }
        Next::Wait
    }

    async fn send_voice_state(&mut self, channel: Option<ChannelId>) -> Result<(), SessionEnd> {
        let me = &self.me;
        let payload = proto::voice_state_update(self.guild, channel, me.self_mute, me.self_deaf);
        let mut failures = 0;
        loop {
            match (self.shared.send)(self.me.shard, payload.clone()) {
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
        let why = match &self.failures.last {
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
            .is_some_and(|state| state.channel == Some(self.me.target));
        let server = snapshot.server_seq > self.servers.used
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
