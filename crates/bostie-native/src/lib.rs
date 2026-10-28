use std::collections::HashMap;
use std::num::NonZeroU16;
use std::time::Duration;

use bostie_core::voice::types::DisconnectReason;
use bostie_core::{
    Core, CoreError, Delivery, FailureKind, IdentifyLimit, RawBellHandle, RestRequest, ShardId,
    Ticket, describe,
};
use pyo3::IntoPyObjectExt;
use pyo3::exceptions::{PyOSError, PyRuntimeError, PyValueError};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList, PyString};

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
    ) -> PyResult<(Self, RawBellHandle)> {
        let (core, bell) = Core::new(token, api_url, global_limit).map_err(to_python_error)?;
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
    ) -> PyResult<u64> {
        let request = RestRequest::parse(method, route, params, query, body, content_type, reason)
            .map_err(to_python_error)?;
        let ticket = self.0.request(request).map_err(to_python_error)?;
        Ok(ticket.into())
    }

    fn gateway_identify_limit(&self, remaining: u32, total: u32, reset_after: f64) -> PyResult<()> {
        let reset_after = Duration::try_from_secs_f64(reset_after).map_err(|error| {
            to_python_error(CoreError::InvalidRequest(format!("reset_after: {error}")))
        })?;
        self.0
            .gateway_identify_limit(IdentifyLimit {
                remaining,
                total,
                reset_after,
            })
            .map_err(to_python_error)
    }

    fn gateway_start(&self, shard_id: u32, url: &str, identify: Vec<u8>) -> PyResult<()> {
        self.0
            .gateway_start(ShardId::from(shard_id), url, identify.into())
            .map_err(to_python_error)
    }

    fn gateway_send(&self, shard_id: u32, payload: Vec<u8>) -> PyResult<()> {
        self.0
            .gateway_send(ShardId::from(shard_id), payload.into())
            .map_err(to_python_error)
    }

    fn gateway_filter(&self, names: Option<Vec<String>>) -> PyResult<()> {
        self.0.gateway_filter(names).map_err(to_python_error)
    }

    fn gateway_reconnect(&self, shard_id: u32) -> PyResult<()> {
        self.0
            .gateway_reconnect(ShardId::from(shard_id))
            .map_err(to_python_error)
    }

    fn gateway_close(&self, shard_id: u32) -> PyResult<()> {
        self.0
            .gateway_close(ShardId::from(shard_id))
            .map_err(to_python_error)
    }

    fn gateway_latency(&self, shard_id: u32) -> PyResult<Option<f64>> {
        let latency = self
            .0
            .gateway_latency(ShardId::from(shard_id))
            .map_err(to_python_error)?;
        Ok(latency.map(|latency| latency.as_secs_f64()))
    }

    fn cancel(&self, ticket: u64) -> PyResult<()> {
        self.0.cancel(Ticket::from(ticket)).map_err(to_python_error)
    }

    fn drain<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let deliveries = self.0.drain().map_err(to_python_error)?;
        let items = deliveries
            .into_iter()
            .map(|delivery| to_python_delivery(py, delivery))
            .collect::<PyResult<Vec<_>>>()?;
        PyList::new(py, items)
    }

    fn inflight(&self) -> PyResult<usize> {
        self.0.inflight().map_err(to_python_error)
    }

    fn close(&self) -> PyResult<()> {
        self.0.close().map_err(to_python_error)
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

fn to_python_error(error: CoreError) -> PyErr {
    let message = describe(&error);
    match error {
        CoreError::InvalidRequest(_) => PyValueError::new_err(message),
        CoreError::Forked { .. }
        | CoreError::Closed
        | CoreError::UnknownShard(_)
        | CoreError::ShardRunning(_)
        | CoreError::SendQueueFull(_)
        | CoreError::UnknownVoice(_)
        | CoreError::VoiceRunning(_)
        | CoreError::VoiceQueueFull(_)
        | CoreError::HandleClosed(_) => PyRuntimeError::new_err(message),
        CoreError::Runtime(_)
        | CoreError::Bell(_)
        | CoreError::HttpClient(_)
        | CoreError::Tls(_) => PyOSError::new_err(message),
    }
}

#[pymodule]
mod _native {
    #[pymodule_export]
    use super::NativeCore;
}
