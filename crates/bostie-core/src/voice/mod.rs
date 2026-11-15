pub(crate) mod close;
pub(crate) mod codec;
pub(crate) mod connection;
pub(crate) mod convert;
pub(crate) mod crypto;
pub(crate) mod dave;
pub(crate) mod effects;
pub(crate) mod media;
pub(crate) mod mixer;
pub(crate) mod pacer;
pub(crate) mod peak;
pub(crate) mod playout;
pub(crate) mod proto;
pub(crate) mod receive;
pub(crate) mod receiver;
pub(crate) mod rtcp;
pub(crate) mod rtp;
pub(crate) mod session;
pub(crate) mod sink;
pub(crate) mod source;
pub(crate) mod speech;
pub(crate) mod tap;
#[cfg(test)]
pub(crate) mod testkit;
#[cfg(test)]
mod tests;
pub mod types;
pub(crate) mod udp;
pub(crate) mod ws;

use std::any::Any;
use std::collections::HashMap;
use std::mem;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Weak};
use std::time::{Duration, Instant};

use bytes::Bytes;
use parking_lot::Mutex;
use tokio::runtime;
use tokio::sync::{mpsc, watch};
use tokio::task::JoinHandle;
use tokio_websockets::Connector;
use tracing::{debug, error};

pub use self::codec::FrameEncoder;
use self::connection::{Command, Connection, ConnectionTuning, Ending, Info, Inputs};
pub use self::dave::DaveInfo;
use self::media::Media;
use self::pacer::{Pace, PacerPool};
use self::receive::Reception;
pub use self::receiver::{Ended, Item, ReceiveRead, Receiver, ReceiverStats};
pub use self::rtcp::{ReceptionReport, RtcpPacket};
pub use self::sink::{Sink, SinkStats};
use self::source::http::Http;
pub use self::source::waveform::voice_message;
pub use self::source::{Source, SourceKind, SourceRead};
use self::tap::GatewayTap;
use self::types::{
    ConnectRequest, DisconnectReason, EncoderSettings, FRAME, ReceiveOptions, SPEECH_HANGOVERS,
    SinkOptions, SourceSpec, VoiceStatus,
};
use crate::error::CoreError;
use crate::ids::{ChannelId, GuildId, Handle, ShardId, UserId};
use crate::inbox::{Delivery, Inbox};
use crate::proxy::Proxy;

pub type GatewaySend = Arc<dyn Fn(ShardId, Bytes) -> Result<(), CoreError> + Send + Sync>;

const COMMAND_QUEUE: usize = 32;
const LOG_EVERY: Duration = Duration::from_secs(1);

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Tuning {
    pub(crate) connection: ConnectionTuning,
    pub(crate) source_prefetch: Duration,
    pub(crate) http_buffer: usize,
    pub(crate) http_retry: Duration,
}

impl Default for Tuning {
    fn default() -> Self {
        Self {
            connection: ConnectionTuning::default(),
            source_prefetch: Duration::from_secs(1),
            http_buffer: 256 * 1024,
            http_retry: Duration::from_millis(500),
        }
    }
}

pub struct Voice {
    runtime: runtime::Handle,
    shared: Arc<Shared>,
}

struct Shared {
    inbox: Arc<Inbox>,
    tap: Arc<GatewayTap>,
    send: GatewaySend,
    connector: Connector,
    pacer: Arc<dyn Pace>,
    http: Http,
    tuning: Tuning,
    next_handle: AtomicU64,
    connections: Mutex<HashMap<GuildId, ConnectionHandle>>,
    sources: Mutex<Vec<Weak<Source>>>,
    closed: AtomicBool,
}

struct ConnectionHandle {
    commands: mpsc::Sender<Command>,
    stop: watch::Sender<bool>,
    info: Arc<Info>,
    media: Arc<Media>,
    supervisor: JoinHandle<()>,
}

impl Voice {
    pub fn new(
        runtime: runtime::Handle,
        inbox: Arc<Inbox>,
        tap: Arc<GatewayTap>,
        send: GatewaySend,
        proxy: Option<Proxy>,
    ) -> Result<Self, CoreError> {
        Self::build(
            runtime,
            inbox,
            tap,
            send,
            Arc::new(PacerPool::new()),
            Tuning::default(),
            proxy,
        )
    }

    fn build(
        runtime: runtime::Handle,
        inbox: Arc<Inbox>,
        tap: Arc<GatewayTap>,
        send: GatewaySend,
        pacer: Arc<dyn Pace>,
        tuning: Tuning,
        proxy: Option<Proxy>,
    ) -> Result<Self, CoreError> {
        Ok(Self {
            shared: Arc::new(Shared {
                inbox,
                tap,
                send,
                connector: Connector::new().map_err(CoreError::Tls)?,
                pacer,
                http: Http::new(
                    runtime.clone(),
                    tuning.http_buffer,
                    tuning.http_retry,
                    proxy,
                ),
                tuning,
                next_handle: AtomicU64::new(1),
                connections: Mutex::new(HashMap::new()),
                sources: Mutex::new(Vec::new()),
                closed: AtomicBool::new(false),
            }),
            runtime,
        })
    }

    pub fn connect(&self, request: &ConnectRequest) -> Result<(), CoreError> {
        request.encoder.validate()?;
        check_speaking(request.speaking)?;
        if request.timeout.is_zero() {
            return Err(CoreError::InvalidRequest(
                "the voice connection timeout must be positive".to_owned(),
            ));
        }
        let guild = request.guild;
        let mut connections = self.shared.connections.lock();
        if self.shared.closed.load(Ordering::Acquire) {
            return Err(CoreError::Closed);
        }
        if connections
            .get(&guild)
            .is_some_and(|handle| !handle.supervisor.is_finished())
        {
            return Err(CoreError::VoiceRunning(guild));
        }
        let tuning = self.shared.tuning.connection;
        let media = Arc::new(Media::new(
            guild,
            request.user,
            request.channel,
            Arc::clone(&self.shared.inbox),
            tuning.stall_after,
            tuning.receive,
        ));
        let info = Arc::new(Info::new(guild, request.shard));
        let mut lane = media.lane(request.encoder)?;
        let mut reception = Reception::new(
            Arc::clone(&media),
            Arc::clone(&info),
            request.user,
            tuning.receive,
        );
        self.shared.pacer.assign_pair(
            Box::new(move |tick| lane.tick(tick)),
            Box::new(move |tick, deadline| reception.tick(tick, deadline)),
        )?;
        let gateway = self.shared.tap.register(guild, request.shard, request.user);
        let joined = (self.shared.send)(
            request.shard,
            proto::voice_state_update(
                guild,
                Some(request.channel),
                request.self_mute,
                request.self_deaf,
            ),
        );
        if let Err(error) = joined {
            media.close();
            self.shared.tap.unregister(guild);
            return Err(error);
        }
        let (commands, command_receiver) = mpsc::channel(COMMAND_QUEUE);
        let (stop, stopped) = watch::channel(false);
        let connection = Connection::new(
            Arc::clone(&self.shared),
            request,
            Arc::clone(&info),
            Arc::clone(&media),
            Inputs {
                commands: command_receiver,
                stop: stopped,
                gateway,
            },
        );
        let task = self.runtime.spawn(connection.run());
        let supervisor = self.runtime.spawn(supervise(
            task,
            Arc::clone(&self.shared),
            Arc::clone(&info),
            Arc::clone(&media),
        ));
        connections.insert(
            guild,
            ConnectionHandle {
                commands,
                stop,
                info,
                media,
                supervisor,
            },
        );
        Ok(())
    }

    pub fn update(
        &self,
        guild: GuildId,
        channel: ChannelId,
        self_mute: bool,
        self_deaf: bool,
    ) -> Result<(), CoreError> {
        let shard = self.with_connection(guild, |handle| handle.info.shard)?;
        self.command(
            guild,
            Command::Update {
                channel,
                self_mute,
                self_deaf,
            },
        )?;
        (self.shared.send)(
            shard,
            proto::voice_state_update(guild, Some(channel), self_mute, self_deaf),
        )
    }

    pub fn disconnect(&self, guild: GuildId) -> Result<(), CoreError> {
        match self.command(guild, Command::Disconnect) {
            Err(CoreError::UnknownVoice(_)) => Ok(()),
            result => result,
        }
    }

    pub fn reconnect(&self, guild: GuildId) -> Result<(), CoreError> {
        self.command(guild, Command::Reconnect)
    }

    pub fn set_encoder(&self, guild: GuildId, encoder: EncoderSettings) -> Result<(), CoreError> {
        encoder.validate()?;
        let media = self.media(guild)?;
        media.set_encoder(encoder);
        Ok(())
    }

    pub fn set_speaking(&self, guild: GuildId, flags: u8) -> Result<(), CoreError> {
        check_speaking(flags)?;
        self.command(guild, Command::Speaking(flags))
    }

    pub fn set_server_change_delay(
        &self,
        guild: GuildId,
        delay: Duration,
    ) -> Result<(), CoreError> {
        self.command(guild, Command::ServerChangeDelay(delay))
    }

    pub fn latency(
        &self,
        guild: GuildId,
    ) -> Result<(Option<Duration>, Option<Duration>), CoreError> {
        self.with_connection(guild, |handle| {
            (handle.info.ws_latency.get(), handle.info.udp_latency.get())
        })
    }

    pub fn dave(&self, guild: GuildId) -> Result<DaveInfo, CoreError> {
        let media = self.media(guild)?;
        Ok(media.dave_info())
    }

    pub fn verification_code(&self, guild: GuildId, user: UserId) -> Result<String, CoreError> {
        let media = self.media(guild)?;
        let fingerprints = media.dave().fingerprint_pair(user)?;
        Ok(dave::verification_code(fingerprints)?)
    }

    pub fn open_sink(&self, guild: GuildId, options: SinkOptions) -> Result<Arc<Sink>, CoreError> {
        let media = self.media(guild)?;
        let sink = Arc::new(Sink::new(
            self.next_handle(),
            options,
            Arc::clone(&self.shared.inbox),
        )?);
        if media.add_sink(&sink) {
            Ok(sink)
        } else {
            Err(CoreError::UnknownVoice(guild))
        }
    }

    pub fn open_receiver(
        &self,
        guild: GuildId,
        options: ReceiveOptions,
    ) -> Result<Arc<Receiver>, CoreError> {
        let media = self.media(guild)?;
        let receiver = Arc::new(Receiver::new(
            self.next_handle(),
            options,
            Arc::clone(&self.shared.inbox),
        )?);
        if media.add_receiver(&receiver) {
            Ok(receiver)
        } else {
            Err(CoreError::UnknownVoice(guild))
        }
    }

    pub fn set_speech_hangover(&self, guild: GuildId, hangover: Duration) -> Result<(), CoreError> {
        if !SPEECH_HANGOVERS.contains(&hangover) {
            return Err(CoreError::InvalidRequest(format!(
                "speech hangover must be from {:?} to {:?}, got {hangover:?}",
                SPEECH_HANGOVERS.start(),
                SPEECH_HANGOVERS.end(),
            )));
        }
        self.media(guild)?.set_speech_hangover(hangover);
        Ok(())
    }

    pub fn set_stall_after(&self, guild: GuildId, after: Duration) -> Result<(), CoreError> {
        if after < FRAME {
            return Err(CoreError::InvalidRequest(format!(
                "stall_after must be at least {FRAME:?}, got {after:?}"
            )));
        }
        self.media(guild)?.set_stall_after(after);
        Ok(())
    }

    pub fn set_speech_activity(&self, guild: GuildId, enabled: bool) -> Result<(), CoreError> {
        self.media(guild)?.set_speech_activity(enabled);
        Ok(())
    }

    pub fn ssrcs(&self, guild: GuildId) -> Result<Vec<(u32, UserId)>, CoreError> {
        let info = self.with_connection(guild, |handle| Arc::clone(&handle.info))?;
        let ssrcs = info.ssrcs.lock();
        Ok(ssrcs
            .iter()
            .map(|(&ssrc, &(user, _))| (ssrc, user))
            .collect())
    }

    pub fn open_source(&self, spec: SourceSpec) -> Result<Arc<Source>, CoreError> {
        let source = Source::open(
            self.next_handle(),
            spec,
            Arc::clone(&self.shared.inbox),
            &self.shared.http,
            self.shared.tuning.source_prefetch,
        )
        .map(Arc::new)?;
        let mut sources = self.shared.sources.lock();
        if self.shared.closed.load(Ordering::Acquire) {
            return Err(CoreError::Closed);
        }
        sources.retain(|source| source.strong_count() > 0);
        sources.push(Arc::downgrade(&source));
        Ok(source)
    }

    pub fn shutdown(&self) {
        self.shared.closed.store(true, Ordering::Release);
        for handle in self.shared.connections.lock().values() {
            handle.stop.send_replace(true);
            handle.media.close();
        }
        let sources = mem::take(&mut *self.shared.sources.lock());
        for source in sources.iter().filter_map(Weak::upgrade) {
            source.close();
        }
    }

    fn next_handle(&self) -> Handle {
        Handle::from(self.shared.next_handle.fetch_add(1, Ordering::Relaxed))
    }

    fn with_connection<T>(
        &self,
        guild: GuildId,
        read: impl FnOnce(&ConnectionHandle) -> T,
    ) -> Result<T, CoreError> {
        self.shared
            .connections
            .lock()
            .get(&guild)
            .map(read)
            .ok_or(CoreError::UnknownVoice(guild))
    }

    fn media(&self, guild: GuildId) -> Result<Arc<Media>, CoreError> {
        self.with_connection(guild, |handle| Arc::clone(&handle.media))
    }

    fn command(&self, guild: GuildId, command: Command) -> Result<(), CoreError> {
        let commands = self.with_connection(guild, |handle| handle.commands.clone())?;
        commands.try_send(command).map_err(|error| match error {
            mpsc::error::TrySendError::Full(_) => CoreError::VoiceQueueFull(guild),
            mpsc::error::TrySendError::Closed(_) => CoreError::UnknownVoice(guild),
        })
    }
}

impl Drop for Voice {
    fn drop(&mut self) {
        if crate::runtime::is_owner() {
            self.shutdown();
        }
    }
}

fn check_speaking(flags: u8) -> Result<(), CoreError> {
    if flags == 0 {
        return Err(CoreError::InvalidRequest(
            "speaking flags must not be 0; Discord needs a speaking mode to play audio".to_owned(),
        ));
    }
    Ok(())
}

async fn supervise(
    task: JoinHandle<Ending>,
    shared: Arc<Shared>,
    info: Arc<Info>,
    media: Arc<Media>,
) {
    let ending = match task.await {
        Ok(ending) => ending,
        Err(failure) => {
            let message = if failure.is_panic() {
                panic_message(failure.into_panic().as_ref()).to_owned()
            } else {
                "the connection task was cancelled".to_owned()
            };
            error!(guild = %info.guild, %message, "a voice connection task failed");
            Ending::new(
                DisconnectReason::ProtocolError,
                None,
                format!("internal error: {message}"),
            )
        }
    };
    finish(&shared, &info, &media, ending);
}

fn finish(shared: &Shared, info: &Arc<Info>, media: &Media, ending: Ending) {
    media.close();
    shared.tap.unregister(info.guild);
    {
        let mut connections = shared.connections.lock();
        if connections
            .get(&info.guild)
            .is_some_and(|handle| Arc::ptr_eq(&handle.info, info))
        {
            connections.remove(&info.guild);
        }
    }
    if ending.leaves_the_channel()
        && let Err(error) = (shared.send)(
            info.shard,
            proto::voice_state_update(info.guild, None, false, false),
        )
    {
        debug!(%error, guild = %info.guild, "the voice leave was not queued");
    }
    debug!(guild = %info.guild, reason = ?ending.reason, "voice connection ended");
    shared.inbox.push(Delivery::VoiceState {
        guild: info.guild,
        status: VoiceStatus::Disconnected,
        reason: Some(ending.reason),
        code: ending.code,
        detail: ending.detail.into(),
    });
}

#[derive(Debug, Default)]
pub(crate) struct Throttle(Option<Instant>);

impl Throttle {
    pub(crate) fn allows(&mut self, now: Instant) -> bool {
        if self
            .0
            .is_some_and(|last| now.saturating_duration_since(last) < LOG_EVERY)
        {
            return false;
        }
        self.0 = Some(now);
        true
    }
}

pub(crate) fn nanos(duration: Duration) -> u64 {
    u64::try_from(duration.as_nanos()).unwrap_or(u64::MAX)
}

pub(crate) fn panic_message(payload: &(dyn Any + Send)) -> &str {
    payload
        .downcast_ref::<&str>()
        .copied()
        .or_else(|| payload.downcast_ref::<String>().map(String::as_str))
        .unwrap_or("unknown panic")
}
