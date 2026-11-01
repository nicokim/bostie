mod voice;

use std::collections::HashMap;
use std::num::NonZeroU16;
use std::path::PathBuf;

use bostie_core::voice::Voice;
use bostie_core::voice::types::{ConnectRequest, DisconnectReason, ReceiveOptions, SourceSpec};
use bostie_core::{
    ChannelId, Core, CoreError, Delivery, FailureKind, GuildId, IdentifyLimit, RawBellHandle,
    RestRequest, ShardId, Ticket, UserId, describe,
};
use pyo3::IntoPyObjectExt;
use pyo3::exceptions::{PyOSError, PyRuntimeError, PyValueError};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyString};

use crate::voice::{NativeReceiver, NativeSink, NativeSource};

#[global_allocator]
static ALLOCATOR: mimalloc::MiMalloc = mimalloc::MiMalloc;

#[pyclass(frozen, name = "Core", module = "bostie._native")]
struct NativeCore(Core);

#[pymethods]
impl NativeCore {
    #[staticmethod]
    fn create(
        token: &str,
        api_url: &str,
        global_limit: NonZeroU16,
    ) -> Result<(Self, RawBellHandle)> {
        let (core, bell) = Core::new(token, api_url, global_limit)?;
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

    fn gateway_identify_limit(&self, remaining: u32, total: u32, reset_after: f64) -> Result<()> {
        let reset_after = voice::seconds("reset_after", reset_after)?;
        Ok(self.0.gateway_identify_limit(IdentifyLimit {
            remaining,
            total,
            reset_after,
        })?)
    }

    fn gateway_start(&self, shard_id: u32, url: &str, identify: Vec<u8>) -> Result<()> {
        Ok(self
            .0
            .gateway_start(ShardId::from(shard_id), url, identify.into())?)
    }

    fn gateway_send(&self, shard_id: u32, payload: Vec<u8>) -> Result<()> {
        Ok(self
            .0
            .gateway_send(ShardId::from(shard_id), payload.into())?)
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
        application: &str,
        fec: bool,
        packet_loss: u8,
        complexity: u8,
        speaking: u8,
    ) -> Result<()> {
        let request = ConnectRequest {
            guild: GuildId::from(guild_id),
            channel: ChannelId::from(channel_id),
            shard: ShardId::from(shard_id),
            user: UserId::from(user_id),
            self_mute,
            self_deaf,
            timeout: voice::seconds("timeout", timeout)?,
            server_change_delay: voice::seconds("server_change_delay", server_change_delay)?,
            encoder: voice::encoder_settings(bitrate, application, fec, packet_loss, complexity)?,
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

    #[pyo3(signature = (guild_id, *, bitrate, application, fec, packet_loss, complexity))]
    fn voice_encoder(
        &self,
        guild_id: u64,
        bitrate: u32,
        application: &str,
        fec: bool,
        packet_loss: u8,
        complexity: u8,
    ) -> Result<()> {
        let settings = voice::encoder_settings(bitrate, application, fec, packet_loss, complexity)?;
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

    fn voice_stats(&self, guild_id: u64) -> Result<HashMap<&'static str, u64>> {
        let stats = self.voice()?.stats(GuildId::from(guild_id))?;
        Ok(stats.into_iter().collect())
    }

    fn voice_diagnostics<'py>(&self, py: Python<'py>, guild_id: u64) -> Result<Bound<'py, PyDict>> {
        let diagnostics = self.voice()?.diagnostics(GuildId::from(guild_id))?;
        Ok(voice::diagnostics(py, diagnostics)?)
    }

    #[pyo3(signature = (guild_id, *, kind, rate, channels, sample, buffer, volume, stall))]
    #[expect(clippy::too_many_arguments)]
    fn voice_sink(
        &self,
        guild_id: u64,
        kind: &str,
        rate: u32,
        channels: u8,
        sample: &str,
        buffer: f64,
        volume: f32,
        stall: &str,
    ) -> Result<NativeSink> {
        let options = voice::sink_options(kind, rate, channels, sample, buffer, volume, stall)?;
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
        kind: &str,
        rate: u32,
        channels: u8,
        sample: &str,
        users: Option<Vec<u64>>,
        exclude: Vec<u64>,
        include_self: bool,
        buffer: f64,
        jitter: f64,
        markers: bool,
        mix: bool,
        overflow: &str,
        stall: &str,
    ) -> Result<NativeReceiver> {
        let options = ReceiveOptions {
            format: voice::receive_format(kind, rate, channels, sample)?,
            users: users.map(voice::users),
            exclude: voice::users(exclude),
            include_self,
            buffer: voice::seconds("buffer", buffer)?,
            jitter: voice::seconds("jitter", jitter)?,
            markers,
            mix,
            overflow: voice::overflow(overflow)?,
            stall: voice::receive_stall(stall)?,
        };
        Ok(self
            .voice()?
            .open_receiver(GuildId::from(guild_id), options)
            .map(NativeReceiver)?)
    }

    fn voice_speech_hangover(&self, guild_id: u64, seconds: f64) -> Result<()> {
        let hangover = voice::seconds("speech_hangover", seconds)?;
        Ok(self
            .voice()?
            .set_speech_hangover(GuildId::from(guild_id), hangover)?)
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

    fn voice_user_stats(&self, guild_id: u64) -> Result<HashMap<u64, HashMap<&'static str, u64>>> {
        let users = self.voice()?.user_stats(GuildId::from(guild_id))?;
        Ok(users
            .into_iter()
            .map(|(user, counters)| (u64::from(user), counters.into_iter().collect()))
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
        } => (intern!(py, "stopped"), u32::from(shard), code, reason).into_bound_py_any(py),
        Delivery::Backpressure {
            shard,
            queued_bytes,
        } => (intern!(py, "backpressure"), u32::from(shard), queued_bytes).into_bound_py_any(py),
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
            duration,
        } => (
            intern!(py, "voice_speech"),
            u64::from(guild),
            u64::from(user),
            ssrc,
            speaking,
            at.elapsed().as_secs_f64(),
            duration.as_secs_f64(),
        )
            .into_bound_py_any(py),
        Delivery::VoiceDeaf { guild, deaf } => {
            (intern!(py, "voice_deaf"), u64::from(guild), deaf).into_bound_py_any(py)
        }
    }
}

fn failure_kind(py: Python<'_>, kind: FailureKind) -> &Bound<'_, PyString> {
    match kind {
        FailureKind::Transport => intern!(py, FailureKind::Transport.as_str()),
        FailureKind::InvalidToken => intern!(py, FailureKind::InvalidToken.as_str()),
        FailureKind::RequestBudget => intern!(py, FailureKind::RequestBudget.as_str()),
        FailureKind::Bug => intern!(py, FailureKind::Bug.as_str()),
    }
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
            | CoreError::UnknownShard(_)
            | CoreError::ShardRunning(_)
            | CoreError::SendQueueFull(_)
            | CoreError::UnknownVoice(_)
            | CoreError::VoiceRunning(_)
            | CoreError::VoiceQueueFull(_)
            | CoreError::HandleClosed(_)
            | CoreError::Dave(_) => PyRuntimeError::new_err(message),
            CoreError::Runtime(_)
            | CoreError::Bell(_)
            | CoreError::HttpClient(_)
            | CoreError::Tls(_) => PyOSError::new_err(message),
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

#[pymodule]
mod _native {
    #[pymodule_export]
    use super::NativeCore;
    #[pymodule_export]
    use super::voice::{NativeOpusEncoder, NativeReceiver, NativeSink, NativeSource, versions};
}
