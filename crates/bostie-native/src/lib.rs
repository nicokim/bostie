#![expect(clippy::needless_pass_by_value)]

mod log;
mod voice;

use std::collections::HashMap;
use std::num::{NonZeroU16, NonZeroU32};
use std::path::PathBuf;
use std::time::Duration;

use bostie_core::voice::Voice;
use bostie_core::voice::types::{
    Application, Bandwidth, ConnectRequest, DisconnectReason, Handle, Overflow, ReceiveOptions,
    ReceiveStall, SampleFormat, SignalType, SourceSpec, StallPolicy,
};
use bostie_core::{
    Auth, ChannelId, Core, CoreError, Delivery, FailureKind, GuildId, IdentifyLimit, RawBellHandle,
    RestRequest, SavedSession, ShardId, Ticket, UserId, describe,
};
use pyo3::IntoPyObjectExt;
use pyo3::exceptions::PyValueError;
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList, PyString};

use crate::errors::{
    AlreadyConnected, DaveError, GatewayQueueFull, ReceiverClosed, ShardNotRunning, SinkClosed,
    StartupError, StateError, VoiceNotConnected, VoiceQueueFull,
};
use crate::voice::{
    Choice, NativeReceiver, NativeSink, NativeSource, ReceiveKind, SinkKind, rtcp_packet,
    speaking_parts,
};

#[global_allocator]
static ALLOCATOR: mimalloc::MiMalloc = mimalloc::MiMalloc;

#[pyclass(frozen, name = "Core", module = "bostie._native")]
struct NativeCore(Core);

#[pymethods]
impl NativeCore {
    #[staticmethod]
    #[pyo3(signature = (token, api_url, global_limit, bearer=false))]
    fn create(
        token: Option<&str>,
        api_url: &str,
        global_limit: NonZeroU16,
        bearer: bool,
    ) -> Result<(Self, RawBellHandle)> {
        let auth = match (token, bearer) {
            (None, _) => Auth::None,
            (Some(token), false) => Auth::Bot(token),
            (Some(token), true) => Auth::Bearer(token),
        };
        let (core, bell) = Core::new(auth, api_url, global_limit)?;
        log::register(core.log_sink());
        Ok((Self(core), bell.into_raw()))
    }

    #[pyo3(signature = (method, route, params, query, body=None, content_type=None, reason=None))]
    #[expect(clippy::too_many_arguments)]
    fn request(
        &self,
        method: &str,
        route: &str,
        params: HashMap<String, String>,
        query: Vec<(String, String)>,
        body: Option<Vec<u8>>,
        content_type: Option<String>,
        reason: Option<String>,
    ) -> Result<u64> {
        let request =
            RestRequest::parse(method, route, &params, query, body, content_type, reason)?;
        let ticket = self.0.request(request)?;
        Ok(ticket.into())
    }

    fn gateway_identify_limit(
        &self,
        remaining: u32,
        total: u32,
        reset_after: f64,
        max_concurrency: u32,
    ) -> Result<()> {
        let reset_after = seconds("reset_after", reset_after)?;
        let max_concurrency = NonZeroU32::new(max_concurrency)
            .ok_or_else(|| PyValueError::new_err("max_concurrency must be at least 1"))?;
        Ok(self.0.gateway_identify_limit(IdentifyLimit {
            remaining,
            total,
            reset_after,
            max_concurrency,
        })?)
    }

    fn gateway_gate_identifies(&self) -> Result<()> {
        Ok(self.0.gateway_gate_identifies()?)
    }

    fn gateway_approve_identify(&self, shard_id: u32, request: u64) -> Result<()> {
        Ok(self
            .0
            .gateway_approve_identify(ShardId::from(shard_id), request)?)
    }

    #[pyo3(signature = (shard_id, url, identify, session=None))]
    fn gateway_start(
        &self,
        shard_id: u32,
        url: &str,
        identify: Vec<u8>,
        session: Option<(String, u64, String)>,
    ) -> Result<()> {
        let saved = session.map(|(id, seq, resume_url)| SavedSession {
            id,
            seq,
            resume_url,
        });
        Ok(self
            .0
            .gateway_start(ShardId::from(shard_id), url, &identify, saved)?)
    }

    fn gateway_send(&self, shard_id: u32, payload: Vec<u8>) -> Result<()> {
        Ok(self
            .0
            .gateway_send(ShardId::from(shard_id), payload.into())?)
    }

    fn gateway_identify(&self, shard_id: u32, identify: Vec<u8>) -> Result<()> {
        Ok(self
            .0
            .gateway_identify(ShardId::from(shard_id), &identify)?)
    }

    fn gateway_filter(&self, names: Option<Vec<String>>) -> Result<()> {
        Ok(self.0.gateway_filter(names)?)
    }

    fn gateway_reconnect(&self, shard_id: u32) -> Result<()> {
        Ok(self.0.gateway_reconnect(ShardId::from(shard_id))?)
    }

    fn gateway_close(&self, shard_id: u32) -> Result<()> {
        Ok(self.0.gateway_close(ShardId::from(shard_id))?)
    }

    fn gateway_suspend(&self, shard_id: u32) -> Result<()> {
        Ok(self.0.gateway_suspend(ShardId::from(shard_id))?)
    }

    fn gateway_latency(&self, shard_id: u32) -> Result<Option<f64>> {
        let latency = self.0.gateway_latency(ShardId::from(shard_id))?;
        Ok(latency.map(|latency| latency.as_secs_f64()))
    }

    fn cancel(&self, ticket: u64) -> Result<()> {
        Ok(self.0.cancel(Ticket::from(ticket))?)
    }

    fn drain<'py>(&self, py: Python<'py>) -> Result<Bound<'py, PyList>> {
        let deliveries = self.0.drain()?;
        let items = deliveries
            .into_iter()
            .map(|delivery| to_python_delivery(py, delivery))
            .collect::<PyResult<Vec<_>>>()?;
        Ok(PyList::new(py, items)?)
    }

    fn close(&self) -> Result<()> {
        Ok(self.0.close()?)
    }

    #[pyo3(signature = (
        guild_id, channel_id, *, shard_id, user_id, self_mute, self_deaf, timeout,
        server_change_delay, bitrate, application, fec, packet_loss, complexity, speaking,
        bandwidth, signal,
    ))]
    #[expect(clippy::too_many_arguments)]
    fn voice_connect(
        &self,
        guild_id: u64,
        channel_id: u64,
        shard_id: u32,
        user_id: u64,
        self_mute: bool,
        self_deaf: bool,
        timeout: f64,
        server_change_delay: f64,
        bitrate: u32,
        application: Choice<Application>,
        fec: bool,
        packet_loss: u8,
        complexity: u8,
        speaking: u8,
        bandwidth: Choice<Bandwidth>,
        signal: Choice<SignalType>,
    ) -> Result<()> {
        let request = ConnectRequest {
            guild: GuildId::from(guild_id),
            channel: ChannelId::from(channel_id),
            shard: ShardId::from(shard_id),
            user: UserId::from(user_id),
            self_mute,
            self_deaf,
            timeout: seconds("timeout", timeout)?,
            server_change_delay: seconds("server_change_delay", server_change_delay)?,
            encoder: voice::encoder_settings(
                bitrate,
                application,
                fec,
                packet_loss,
                complexity,
                bandwidth,
                signal,
            ),
            speaking,
        };
        Ok(self.voice()?.connect(&request)?)
    }

    fn voice_update(
        &self,
        guild_id: u64,
        channel_id: u64,
        self_mute: bool,
        self_deaf: bool,
    ) -> Result<()> {
        Ok(self.voice()?.update(
            GuildId::from(guild_id),
            ChannelId::from(channel_id),
            self_mute,
            self_deaf,
        )?)
    }

    fn voice_disconnect(&self, guild_id: u64) -> Result<()> {
        Ok(self.voice()?.disconnect(GuildId::from(guild_id))?)
    }

    fn voice_reconnect(&self, guild_id: u64) -> Result<()> {
        Ok(self.voice()?.reconnect(GuildId::from(guild_id))?)
    }

    #[pyo3(signature = (
        guild_id, *, bitrate, application, fec, packet_loss, complexity,
        bandwidth, signal,
    ))]
    #[expect(clippy::too_many_arguments)]
    fn voice_encoder(
        &self,
        guild_id: u64,
        bitrate: u32,
        application: Choice<Application>,
        fec: bool,
        packet_loss: u8,
        complexity: u8,
        bandwidth: Choice<Bandwidth>,
        signal: Choice<SignalType>,
    ) -> Result<()> {
        let settings = voice::encoder_settings(
            bitrate,
            application,
            fec,
            packet_loss,
            complexity,
            bandwidth,
            signal,
        );
        Ok(self
            .voice()?
            .set_encoder(GuildId::from(guild_id), settings)?)
    }

    fn voice_speaking(&self, guild_id: u64, flags: u8) -> Result<()> {
        Ok(self.voice()?.set_speaking(GuildId::from(guild_id), flags)?)
    }

    fn voice_latency(&self, guild_id: u64) -> Result<(Option<f64>, Option<f64>)> {
        let (ws, udp) = self.voice()?.latency(GuildId::from(guild_id))?;
        Ok((
            ws.map(|latency| latency.as_secs_f64()),
            udp.map(|latency| latency.as_secs_f64()),
        ))
    }

    fn voice_dave(&self, guild_id: u64) -> Result<(u16, Option<u64>, Option<String>)> {
        let dave = self.voice()?.dave(GuildId::from(guild_id))?;
        Ok((dave.protocol_version, dave.epoch, dave.privacy_code))
    }

    fn voice_verification_code(
        &self,
        py: Python<'_>,
        guild_id: u64,
        user_id: u64,
    ) -> Result<String> {
        let voice = self.voice()?;
        Ok(py.detach(|| voice.verification_code(GuildId::from(guild_id), UserId::from(user_id)))?)
    }

    #[pyo3(signature = (
        guild_id, *, kind, rate, channels, sample, buffer, volume, stall, after=None,
        crossfade=0.0,
    ))]
    #[expect(clippy::too_many_arguments)]
    fn voice_sink(
        &self,
        guild_id: u64,
        kind: Choice<SinkKind>,
        rate: u32,
        channels: u8,
        sample: Choice<SampleFormat>,
        buffer: f64,
        volume: f32,
        stall: Choice<StallPolicy>,
        after: Option<u64>,
        crossfade: f64,
    ) -> Result<NativeSink> {
        let mut options = voice::sink_options(kind, rate, channels, sample, buffer, volume, stall)?;
        options.after = after.map(Handle::from);
        options.crossfade = seconds("crossfade", crossfade)?;
        Ok(self
            .voice()?
            .open_sink(GuildId::from(guild_id), options)
            .map(NativeSink)?)
    }

    fn voice_file(&self, path: PathBuf) -> Result<NativeSource> {
        self.open_source(SourceSpec::File(path))
    }

    fn voice_bytes(&self, data: &[u8], hint: Option<String>) -> Result<NativeSource> {
        self.open_source(SourceSpec::Bytes {
            data: data.to_vec().into(),
            hint,
        })
    }

    fn voice_url(&self, url: String, headers: Vec<(String, String)>) -> Result<NativeSource> {
        self.open_source(SourceSpec::Url { url, headers })
    }

    #[pyo3(signature = (
        guild_id, *, kind, rate, channels, sample, users, exclude, include_self, buffer, jitter,
        markers, mix, overflow, stall,
    ))]
    #[expect(clippy::too_many_arguments)]
    fn voice_receive(
        &self,
        guild_id: u64,
        kind: Choice<ReceiveKind>,
        rate: u32,
        channels: u8,
        sample: Choice<SampleFormat>,
        users: Option<Vec<u64>>,
        exclude: Vec<u64>,
        include_self: bool,
        buffer: f64,
        jitter: f64,
        markers: bool,
        mix: bool,
        overflow: Choice<Overflow>,
        stall: Choice<ReceiveStall>,
    ) -> Result<NativeReceiver> {
        let options = ReceiveOptions {
            format: voice::receive_format(kind, rate, channels, sample, mix)?,
            users: users.map(voice::users),
            exclude: voice::users(exclude),
            include_self,
            buffer: seconds("buffer", buffer)?,
            jitter: seconds("jitter", jitter)?,
            markers,
            overflow: overflow.0,
            stall: stall.0,
        };
        Ok(self
            .voice()?
            .open_receiver(GuildId::from(guild_id), options)
            .map(NativeReceiver)?)
    }

    fn voice_speech_hangover(&self, guild_id: u64, hangover: f64) -> Result<()> {
        let hangover = seconds("speech_hangover", hangover)?;
        Ok(self
            .voice()?
            .set_speech_hangover(GuildId::from(guild_id), hangover)?)
    }

    fn voice_server_change_delay(&self, guild_id: u64, delay: f64) -> Result<()> {
        let delay = seconds("server_change_delay", delay)?;
        Ok(self
            .voice()?
            .set_server_change_delay(GuildId::from(guild_id), delay)?)
    }

    fn voice_speech_activity(&self, guild_id: u64, enabled: bool) -> Result<()> {
        Ok(self
            .voice()?
            .set_speech_activity(GuildId::from(guild_id), enabled)?)
    }

    fn voice_ssrcs(&self, guild_id: u64) -> Result<HashMap<u32, u64>> {
        let ssrcs = self.voice()?.ssrcs(GuildId::from(guild_id))?;
        Ok(ssrcs
            .into_iter()
            .map(|(ssrc, user)| (ssrc, u64::from(user)))
            .collect())
    }
}

impl NativeCore {
    fn voice(&self) -> Result<&Voice> {
        Ok(self.0.voice()?)
    }

    fn open_source(&self, spec: SourceSpec) -> Result<NativeSource> {
        Ok(self.voice()?.open_source(spec).map(NativeSource)?)
    }
}

#[expect(clippy::too_many_lines)]
fn to_python_delivery(py: Python<'_>, delivery: Delivery) -> PyResult<Bound<'_, PyAny>> {
    match delivery {
        Delivery::Response {
            ticket,
            status,
            body,
        } => (
            intern!(py, "response"),
            u64::from(ticket),
            status,
            PyBytes::new(py, &body),
        )
            .into_bound_py_any(py),
        Delivery::Failure {
            ticket,
            kind,
            detail,
        } => (
            intern!(py, "failure"),
            u64::from(ticket),
            failure_kind(py, kind),
            detail,
        )
            .into_bound_py_any(py),
        Delivery::Dispatch {
            shard,
            seq,
            name,
            data,
        } => (
            intern!(py, "dispatch"),
            u32::from(shard),
            seq,
            &*name,
            PyBytes::new(py, &data),
        )
            .into_bound_py_any(py),
        Delivery::Disconnected {
            shard,
            code,
            reason,
            resuming,
        } => (
            intern!(py, "disconnected"),
            u32::from(shard),
            code,
            reason,
            resuming,
        )
            .into_bound_py_any(py),
        Delivery::Stopped {
            shard,
            code,
            reason,
            session,
        } => (
            intern!(py, "stopped"),
            u32::from(shard),
            code,
            reason,
            session.map(|session| (session.id, session.seq, session.resume_url)),
        )
            .into_bound_py_any(py),
        Delivery::Backpressure {
            shard,
            queued_bytes,
        } => (intern!(py, "backpressure"), u32::from(shard), queued_bytes).into_bound_py_any(py),
        Delivery::IdentifyRequest { shard, request } => {
            (intern!(py, "identify"), u32::from(shard), request).into_bound_py_any(py)
        }
        Delivery::InvalidRequests { count } => {
            (intern!(py, "invalid_requests"), count).into_bound_py_any(py)
        }
        Delivery::VoiceState {
            guild,
            status,
            reason,
            code,
            detail,
        } => (
            intern!(py, "voice_state"),
            u64::from(guild),
            status.as_str(),
            reason.map(DisconnectReason::as_str),
            code,
            &*detail,
        )
            .into_bound_py_any(py),
        Delivery::VoiceMoved {
            guild,
            from,
            to,
            by_us,
        } => (
            intern!(py, "voice_moved"),
            u64::from(guild),
            u64::from(from),
            u64::from(to),
            by_us,
        )
            .into_bound_py_any(py),
        Delivery::VoiceDave {
            guild,
            protocol_version,
            epoch,
            transition_id,
        } => (
            intern!(py, "voice_dave"),
            u64::from(guild),
            protocol_version,
            epoch,
            transition_id,
        )
            .into_bound_py_any(py),
        Delivery::VoiceSelf {
            guild,
            self_mute,
            self_deaf,
            mute,
            suppress,
        } => (
            intern!(py, "voice_self"),
            u64::from(guild),
            self_mute,
            self_deaf,
            mute,
            suppress,
        )
            .into_bound_py_any(py),
        Delivery::VoiceSpeaking {
            guild,
            user,
            ssrc,
            flags,
        } => (
            intern!(py, "voice_speaking"),
            u64::from(guild),
            u64::from(user),
            ssrc,
            flags,
        )
            .into_bound_py_any(py),
        Delivery::VoiceClients { guild, users } => (
            intern!(py, "voice_clients"),
            u64::from(guild),
            users.into_iter().map(u64::from).collect::<Vec<_>>(),
        )
            .into_bound_py_any(py),
        Delivery::VoiceClientLeft { guild, user } => (
            intern!(py, "voice_client_left"),
            u64::from(guild),
            u64::from(user),
        )
            .into_bound_py_any(py),
        Delivery::VoiceStalled {
            guild,
            direction,
            stalled,
            duration,
        } => (
            intern!(py, "voice_stalled"),
            u64::from(guild),
            direction.as_str(),
            stalled,
            duration.as_secs_f64(),
        )
            .into_bound_py_any(py),
        Delivery::VoiceWake { handle } => {
            (intern!(py, "voice_wake"), u64::from(handle)).into_bound_py_any(py)
        }
        Delivery::VoiceMarker {
            handle,
            marker,
            played,
        } => (
            intern!(py, "voice_marker"),
            u64::from(handle),
            marker,
            played,
        )
            .into_bound_py_any(py),
        Delivery::VoiceSpeech {
            guild,
            user,
            ssrc,
            speaking,
            at,
        } => {
            let (speaking, duration) = speaking_parts(speaking);
            (
                intern!(py, "voice_speech"),
                u64::from(guild),
                u64::from(user),
                ssrc,
                speaking,
                at.elapsed().as_secs_f64(),
                duration,
            )
                .into_bound_py_any(py)
        }
        Delivery::VoiceDeaf { guild, deaf } => {
            (intern!(py, "voice_deaf"), u64::from(guild), deaf).into_bound_py_any(py)
        }
        Delivery::VoiceRtcp { guild, packets } => (
            intern!(py, "voice_rtcp"),
            u64::from(guild),
            packets
                .into_iter()
                .map(|packet| rtcp_packet(py, packet))
                .collect::<PyResult<Vec<_>>>()?,
        )
            .into_bound_py_any(py),
        Delivery::Log {
            level,
            logger,
            message,
        } => (
            intern!(py, "log"),
            log::python_level(level),
            logger,
            message,
        )
            .into_bound_py_any(py),
    }
}

fn seconds(name: &str, value: f64) -> Result<Duration> {
    Duration::try_from_secs_f64(value)
        .map_err(|error| CoreError::InvalidRequest(format!("{name}: {error}")).into())
}

fn failure_kind(py: Python<'_>, kind: FailureKind) -> &Bound<'_, PyString> {
    match kind {
        FailureKind::Transport => intern!(py, FailureKind::Transport.as_str()),
        FailureKind::InvalidToken => intern!(py, FailureKind::InvalidToken.as_str()),
        FailureKind::NoToken => intern!(py, FailureKind::NoToken.as_str()),
        FailureKind::RequestBudget => intern!(py, FailureKind::RequestBudget.as_str()),
        FailureKind::Bug => intern!(py, FailureKind::Bug.as_str()),
    }
}

#[expect(clippy::same_name_method)]
mod errors {
    pyo3::import_exception!(bostie.errors, StateError);
    pyo3::import_exception!(bostie.errors, ShardNotRunning);
    pyo3::import_exception!(bostie.errors, GatewayQueueFull);
    pyo3::import_exception!(bostie.errors, VoiceNotConnected);
    pyo3::import_exception!(bostie.errors, AlreadyConnected);
    pyo3::import_exception!(bostie.errors, VoiceQueueFull);
    pyo3::import_exception!(bostie.errors, SinkClosed);
    pyo3::import_exception!(bostie.errors, ReceiverClosed);
    pyo3::import_exception!(bostie.errors, DaveError);
    pyo3::import_exception!(bostie.errors, StartupError);
}

pub(crate) struct Error(PyErr);

pub(crate) type Result<T, E = Error> = std::result::Result<T, E>;

impl From<CoreError> for Error {
    fn from(error: CoreError) -> Self {
        let message = describe(&error);
        Self(match error {
            CoreError::InvalidRequest(_) => PyValueError::new_err(message),
            CoreError::Forked { .. }
            | CoreError::Closed
            | CoreError::ShardRunning(_)
            | CoreError::SourceClosed(_) => StateError::new_err(message),
            CoreError::UnknownShard(_) => ShardNotRunning::new_err(message),
            CoreError::SendQueueFull(_) => GatewayQueueFull::new_err(message),
            CoreError::UnknownVoice(_) => VoiceNotConnected::new_err(message),
            CoreError::VoiceRunning(_) => AlreadyConnected::new_err(message),
            CoreError::VoiceQueueFull(_) => VoiceQueueFull::new_err(message),
            CoreError::SinkClosed(_) => SinkClosed::new_err(message),
            CoreError::ReceiverClosed(_) => ReceiverClosed::new_err(message),
            CoreError::Dave(_) => DaveError::new_err(message),
            CoreError::Runtime(_)
            | CoreError::Bell(_)
            | CoreError::HttpClient(_)
            | CoreError::Tls(_) => StartupError::new_err(message),
        })
    }
}

impl From<PyErr> for Error {
    fn from(error: PyErr) -> Self {
        Self(error)
    }
}

impl From<Error> for PyErr {
    fn from(error: Error) -> Self {
        error.0
    }
}

macro_rules! variant_names {
    ($kind:ident { $($variant:ident),+ $(,)? }) => {{
        let name = |value: $kind| match value {
            $($kind::$variant)|+ => value.as_str(),
        };
        [$(name($kind::$variant)),+]
    }};
}

#[pymodule]
mod _native {
    use bostie_core::voice::Ended;
    use bostie_core::voice::types::{
        self, Application, Bandwidth, Direction, DisconnectReason, Overflow, ReceiveStall,
        SampleFormat, SignalType, StallPolicy, VoiceStatus,
    };
    use pyo3::prelude::*;

    #[pymodule_export]
    use super::NativeCore;
    #[pymodule_export]
    use super::voice::{
        NativeOpusEncoder, NativeReceiver, NativeSink, NativeSource, versions, voice_message,
    };
    #[pymodule_export]
    const LOGGERS: [&str; 4] = super::log::LOGGERS;

    #[pyfunction]
    fn log_levels(levels: [i32; 4]) {
        super::log::set_levels(levels);
    }
    use super::voice::{ReceiveKind, SinkKind, names};

    #[pymodule_init]
    fn init(module: &Bound<'_, PyModule>) -> PyResult<()> {
        module.add("OPUS_APPLICATIONS", names::<Application>())?;
        module.add("OPUS_BANDWIDTHS", names::<Bandwidth>())?;
        module.add("OPUS_SIGNALS", names::<SignalType>())?;
        module.add("SAMPLES", names::<SampleFormat>())?;
        module.add("SINK_KINDS", names::<SinkKind>())?;
        module.add("RECEIVE_KINDS", names::<ReceiveKind>())?;
        module.add("SINK_STALLS", names::<StallPolicy>())?;
        module.add("RECEIVE_STALLS", names::<ReceiveStall>())?;
        module.add("OVERFLOWS", names::<Overflow>())?;
        module.add(
            "VOICE_STATES",
            variant_names!(VoiceStatus {
                Connecting,
                Ready,
                Resuming,
                Reconnecting,
                Disconnected,
            }),
        )?;
        module.add(
            "DISCONNECT_REASONS",
            variant_names!(DisconnectReason {
                Requested,
                Removed,
                CallTerminated,
                RateLimited,
                Timeout,
                SessionInvalid,
                AuthenticationFailed,
                DaveRequired,
                DaveFailed,
                ProtocolError,
                Closed,
            }),
        )?;
        module.add("DIRECTIONS", variant_names!(Direction { Send, Receive }))?;
        module.add(
            "CLOSED_REASONS",
            variant_names!(Ended {
                Closed,
                Disconnected,
                Moved,
                Reconnecting,
            }),
        )?;
        Ok(())
    }

    #[pymodule_export]
    const SAMPLE_RATE: u32 = types::SAMPLE_RATE;
    #[pymodule_export]
    const MIN_RATE: u32 = *types::RATES.start();
    #[pymodule_export]
    const MAX_RATE: u32 = *types::RATES.end();
    #[pymodule_export]
    const MAX_RESAMPLER_BLOCK: u32 = types::MAX_RESAMPLER_BLOCK;
    #[pymodule_export]
    const DECODE_RATES: [u32; 5] = types::DECODE_RATES;
    #[pymodule_export]
    const MIN_BITRATE: u32 = *types::BITRATES.start();
    #[pymodule_export]
    const MAX_BITRATE: u32 = *types::BITRATES.end();
    #[pymodule_export]
    const MAX_COMPLEXITY: u8 = types::MAX_COMPLEXITY;
    #[pymodule_export]
    const MIN_BUFFER: f64 = types::BUFFERS.start().as_secs_f64();
    #[pymodule_export]
    const MAX_BUFFER: f64 = types::BUFFERS.end().as_secs_f64();
    #[pymodule_export]
    const MIN_SPEECH_HANGOVER: f64 = types::SPEECH_HANGOVERS.start().as_secs_f64();
    #[pymodule_export]
    const MAX_SPEECH_HANGOVER: f64 = types::SPEECH_HANGOVERS.end().as_secs_f64();
    #[pymodule_export]
    const MAX_JITTER: f64 = types::MAX_JITTER.as_secs_f64();
    #[pymodule_export]
    const MAX_BANDS: usize = types::MAX_BANDS;
    #[pymodule_export]
    const MIN_BAND_FREQUENCY: f32 = *types::BAND_FREQUENCIES.start();
    #[pymodule_export]
    const MAX_BAND_FREQUENCY: f32 = *types::BAND_FREQUENCIES.end();
    #[pymodule_export]
    const MAX_GAIN: f32 = types::MAX_GAIN;
    #[pymodule_export]
    const MIN_SPEED: f32 = *types::SPEEDS.start();
    #[pymodule_export]
    const MAX_SPEED: f32 = *types::SPEEDS.end();
}
