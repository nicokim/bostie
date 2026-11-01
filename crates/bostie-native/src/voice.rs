use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::{Duration, Instant};

use bostie_core::voice::types::{
    Application, EncoderSettings, Overflow, PcmFormat, ReceiveFormat, ReceiveStall, SampleFormat,
    SinkFormat, SinkOptions, StallPolicy,
};
use bostie_core::voice::{
    Ended, FrameEncoder, Item, ReceiveRead, Receiver, Sink, Source, SourceRead,
};
use bostie_core::{CoreError, UserId};
use pyo3::IntoPyObjectExt;
use pyo3::buffer::PyBuffer;
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::{Error, Result};

#[pyclass(frozen, name = "Sink", module = "bostie._native")]
pub(crate) struct NativeSink(pub(crate) Arc<Sink>);

#[pymethods]
impl NativeSink {
    #[getter]
    fn id(&self) -> u64 {
        self.0.id().into()
    }

    fn write(&self, py: Python<'_>, data: PyBuffer<u8>) -> Result<bool> {
        let data = data.to_vec(py)?;
        Ok(py.detach(|| self.0.write(&data))?)
    }

    fn mark(&self) -> Result<(u64, u64)> {
        Ok(self.0.mark()?)
    }

    fn flush(&self) -> Result<(u64, u64)> {
        Ok(self.0.flush()?)
    }

    #[pyo3(signature = (until=None))]
    fn clear(&self, until: Option<u64>) -> Result<()> {
        Ok(self.0.clear(until)?)
    }

    fn set_volume(&self, volume: f32) -> Result<()> {
        Ok(self.0.set_volume(volume)?)
    }

    fn queued(&self) -> Result<f64> {
        let queued = self.0.queued()?;
        Ok(queued.as_secs_f64())
    }

    fn passthrough(&self) -> Result<bool> {
        Ok(self.0.passthrough()?)
    }

    fn stats(&self) -> Result<HashMap<&'static str, u64>> {
        let stats = self.0.stats()?;
        Ok(stats.into_iter().collect())
    }

    fn close(&self) {
        self.0.close();
    }
}

#[pyclass(frozen, name = "Source", module = "bostie._native")]
pub(crate) struct NativeSource(pub(crate) Arc<Source>);

#[pymethods]
impl NativeSource {
    #[getter]
    fn id(&self) -> u64 {
        self.0.id().into()
    }

    fn info(&self) -> Result<Option<(&'static str, Option<f64>, bool)>> {
        let info = self.0.info()?;
        Ok(info.map(|info| {
            (
                info.kind.as_str(),
                info.duration.map(|duration| duration.as_secs_f64()),
                info.seekable,
            )
        }))
    }

    fn read<'py>(&self, py: Python<'py>) -> Result<Option<Vec<Bound<'py, PyBytes>>>> {
        Ok(match self.0.read()? {
            SourceRead::Pending => Some(Vec::new()),
            SourceRead::Chunks(chunks) => {
                Some(chunks.iter().map(|chunk| PyBytes::new(py, chunk)).collect())
            }
            SourceRead::Ended(_) => None,
        })
    }

    fn error(&self) -> Result<Option<String>> {
        Ok(self.0.error()?)
    }

    fn seek(&self, position: f64) -> Result<()> {
        let position = seconds("position", position)?;
        Ok(self.0.seek(position)?)
    }

    fn close(&self) {
        self.0.close();
    }
}

#[pyclass(frozen, name = "Receiver", module = "bostie._native")]
pub(crate) struct NativeReceiver(pub(crate) Arc<Receiver>);

#[pymethods]
impl NativeReceiver {
    #[getter]
    fn id(&self) -> u64 {
        self.0.id().into()
    }

    fn read<'py>(&self, py: Python<'py>, limit: usize) -> Result<Option<Vec<Bound<'py, PyAny>>>> {
        let now = Instant::now();
        Ok(match self.0.read(limit)? {
            ReceiveRead::Pending => Some(Vec::new()),
            ReceiveRead::Items(items) => Some(
                items
                    .into_iter()
                    .map(|(queued, item)| received_item(py, now, queued, item))
                    .collect::<PyResult<_>>()?,
            ),
            ReceiveRead::Ended(_) => None,
        })
    }

    fn queued(&self) -> Result<f64> {
        let queued = self.0.queued()?;
        Ok(queued.as_secs_f64())
    }

    fn clear(&self) -> Result<()> {
        Ok(self.0.clear()?)
    }

    fn stats(&self) -> Result<HashMap<&'static str, u64>> {
        let stats = self.0.stats()?;
        Ok(stats.into_iter().collect())
    }

    fn closed_reason(&self) -> Option<&'static str> {
        self.0.ended().map(Ended::as_str)
    }

    fn close(&self) {
        self.0.close();
    }
}

#[pyclass(frozen, name = "OpusEncoder", module = "bostie._native")]
pub(crate) struct NativeOpusEncoder(Mutex<FrameEncoder>);

#[pymethods]
impl NativeOpusEncoder {
    #[new]
    #[pyo3(signature = (*, rate, channels, sample, bitrate, application, fec, packet_loss, complexity))]
    #[expect(clippy::too_many_arguments)]
    fn new(
        rate: u32,
        channels: u8,
        sample: &str,
        bitrate: u32,
        application: &str,
        fec: bool,
        packet_loss: u8,
        complexity: u8,
    ) -> Result<Self> {
        let format = PcmFormat {
            rate,
            channels,
            sample: sample_format(sample)?,
        };
        let settings = encoder_settings(bitrate, application, fec, packet_loss, complexity)?;
        let encoder = FrameEncoder::new(format, settings)?;
        Ok(Self(Mutex::new(encoder)))
    }

    fn encode<'py>(&self, py: Python<'py>, data: PyBuffer<u8>) -> Result<Vec<Bound<'py, PyBytes>>> {
        let data = data.to_vec(py)?;
        let packets = py.detach(|| self.encoder().encode(&data));
        Ok(packets
            .iter()
            .map(|packet| PyBytes::new(py, packet))
            .collect())
    }

    fn flush<'py>(&self, py: Python<'py>) -> Vec<Bound<'py, PyBytes>> {
        let packets = self.encoder().flush();
        packets
            .iter()
            .map(|packet| PyBytes::new(py, packet))
            .collect()
    }
}

impl NativeOpusEncoder {
    fn encoder(&self) -> MutexGuard<'_, FrameEncoder> {
        self.0.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

#[pyfunction]
pub(crate) fn versions() -> HashMap<&'static str, String> {
    bostie_core::versions().into_iter().collect()
}

pub(crate) fn encoder_settings(
    bitrate: u32,
    application: &str,
    fec: bool,
    packet_loss: u8,
    complexity: u8,
) -> Result<EncoderSettings> {
    let application = match application {
        "audio" => Application::Audio,
        "voip" => Application::Voip,
        "lowdelay" => Application::LowDelay,
        other => return Err(unknown("application", other)),
    };
    Ok(EncoderSettings {
        bitrate,
        application,
        fec,
        packet_loss,
        complexity,
    })
}

pub(crate) fn sink_options(
    kind: &str,
    rate: u32,
    channels: u8,
    sample: &str,
    buffer: f64,
    volume: f32,
    stall: &str,
) -> Result<SinkOptions> {
    let format = match kind {
        "pcm" => SinkFormat::Pcm(PcmFormat {
            rate,
            channels,
            sample: sample_format(sample)?,
        }),
        "opus" => SinkFormat::Opus,
        other => return Err(unknown("kind", other)),
    };
    let stall = match stall {
        "hold" => StallPolicy::Hold,
        "drop" => StallPolicy::Drop,
        other => return Err(unknown("stall", other)),
    };
    Ok(SinkOptions {
        format,
        buffer: seconds("buffer", buffer)?,
        volume,
        stall,
    })
}

pub(crate) fn receive_format(
    kind: &str,
    rate: u32,
    channels: u8,
    sample: &str,
) -> Result<ReceiveFormat> {
    Ok(match kind {
        "pcm" => ReceiveFormat::Pcm(PcmFormat {
            rate,
            channels,
            sample: sample_format(sample)?,
        }),
        "opus" => ReceiveFormat::Opus,
        "packets" => ReceiveFormat::Packets,
        other => return Err(unknown("kind", other)),
    })
}

pub(crate) fn overflow(overflow: &str) -> Result<Overflow> {
    match overflow {
        "drop_oldest" => Ok(Overflow::DropOldest),
        other => Err(unknown("overflow", other)),
    }
}

pub(crate) fn receive_stall(stall: &str) -> Result<ReceiveStall> {
    match stall {
        "wait" => Ok(ReceiveStall::Wait),
        other => Err(unknown("stall", other)),
    }
}

pub(crate) fn users(ids: Vec<u64>) -> HashSet<UserId> {
    ids.into_iter().map(UserId::from).collect()
}

pub(crate) fn seconds(name: &str, value: f64) -> Result<Duration> {
    Duration::try_from_secs_f64(value)
        .map_err(|error| CoreError::InvalidRequest(format!("{name}: {error}")).into())
}

fn received_item(
    py: Python<'_>,
    now: Instant,
    queued: Instant,
    item: Item,
) -> PyResult<Bound<'_, PyAny>> {
    let queued = age(now, queued);
    match item {
        Item::Frame {
            user,
            ssrc,
            timestamp,
            offset,
            samples,
            at,
            data,
            lost,
        } => (
            intern!(py, "frame"),
            user.map(u64::from),
            ssrc,
            timestamp,
            offset,
            samples,
            age(now, at),
            PyBytes::new(py, &data),
            lost,
            queued,
        )
            .into_bound_py_any(py),
        Item::Packet {
            user,
            ssrc,
            sequence,
            timestamp,
            at,
            opus,
            extensions,
        } => (
            intern!(py, "packet"),
            u64::from(user),
            ssrc,
            sequence,
            timestamp,
            age(now, at),
            PyBytes::new(py, &opus),
            extensions
                .iter()
                .map(|(id, body)| (*id, PyBytes::new(py, body)))
                .collect::<Vec<_>>(),
            queued,
        )
            .into_bound_py_any(py),
        Item::Speech {
            user,
            ssrc,
            speaking,
            offset,
            at,
            duration,
        } => (
            intern!(py, "speech"),
            u64::from(user),
            ssrc,
            speaking,
            offset,
            age(now, at),
            duration.as_secs_f64(),
            queued,
        )
            .into_bound_py_any(py),
    }
}

fn age(now: Instant, at: Instant) -> f64 {
    now.saturating_duration_since(at).as_secs_f64()
}

fn sample_format(sample: &str) -> Result<SampleFormat> {
    match sample {
        "s16le" => Ok(SampleFormat::S16Le),
        "f32le" => Ok(SampleFormat::F32Le),
        other => Err(unknown("sample", other)),
    }
}

fn unknown(name: &str, value: &str) -> Error {
    CoreError::InvalidRequest(format!("unknown {name} {value:?}")).into()
}
