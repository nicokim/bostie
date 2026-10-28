use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::{Duration, Instant};

use bostie_core::CoreError;
use bostie_core::voice::codec::FrameEncoder;
use bostie_core::voice::pacer::ThreadLoad;
use bostie_core::voice::sink::Sink;
use bostie_core::voice::source::{Source, SourceRead};
use bostie_core::voice::types::{
    Application, DisconnectReason, EncoderSettings, PcmFormat, SampleFormat, SinkFormat,
    SinkOptions, SourceSpec, StallPolicy,
};
use bostie_core::voice::{Diagnostics, HistoryEntry};
use pyo3::buffer::PyBuffer;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};

use crate::to_python_error;

#[pyclass(frozen, name = "Sink", module = "bostie._native")]
pub(crate) struct NativeSink(pub(crate) Arc<Sink>);

#[pymethods]
impl NativeSink {
    #[getter]
    fn id(&self) -> u64 {
        self.0.id().into()
    }

    fn write(&self, py: Python<'_>, data: PyBuffer<u8>) -> PyResult<bool> {
        let data = data.to_vec(py)?;
        self.0.write(&data).map_err(to_python_error)
    }

    fn mark(&self) -> PyResult<(u64, u64)> {
        self.0.mark().map_err(to_python_error)
    }

    fn flush(&self) -> PyResult<(u64, u64)> {
        self.0.flush().map_err(to_python_error)
    }

    #[pyo3(signature = (until=None))]
    fn clear(&self, until: Option<u64>) -> PyResult<()> {
        self.0.clear(until).map_err(to_python_error)
    }

    fn set_volume(&self, volume: f32) -> PyResult<()> {
        self.0.set_volume(volume).map_err(to_python_error)
    }

    fn queued(&self) -> PyResult<f64> {
        let queued = self.0.queued().map_err(to_python_error)?;
        Ok(queued.as_secs_f64())
    }

    fn passthrough(&self) -> PyResult<bool> {
        self.0.passthrough().map_err(to_python_error)
    }

    fn stats(&self) -> PyResult<HashMap<&'static str, u64>> {
        let stats = self.0.stats().map_err(to_python_error)?;
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

    fn info(&self) -> PyResult<Option<(&'static str, Option<f64>, bool)>> {
        let info = self.0.info().map_err(to_python_error)?;
        Ok(info.map(|info| {
            (
                info.kind.as_str(),
                info.duration.map(|duration| duration.as_secs_f64()),
                info.seekable,
            )
        }))
    }

    fn read<'py>(&self, py: Python<'py>) -> PyResult<Option<Vec<Bound<'py, PyBytes>>>> {
        Ok(match self.0.read().map_err(to_python_error)? {
            SourceRead::Pending => Some(Vec::new()),
            SourceRead::Chunks(chunks) => {
                Some(chunks.iter().map(|chunk| PyBytes::new(py, chunk)).collect())
            }
            SourceRead::Ended(_) => None,
        })
    }

    fn error(&self) -> PyResult<Option<String>> {
        self.0.error().map_err(to_python_error)
    }

    fn seek(&self, position: f64) -> PyResult<()> {
        let position = seconds("position", position)?;
        self.0.seek(position).map_err(to_python_error)
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
    ) -> PyResult<Self> {
        let format = PcmFormat {
            rate,
            channels,
            sample: sample_format(sample)?,
        };
        let settings = encoder_settings(bitrate, application, fec, packet_loss, complexity)?;
        let encoder = FrameEncoder::new(format, settings).map_err(to_python_error)?;
        Ok(Self(Mutex::new(encoder)))
    }

    fn encode<'py>(
        &self,
        py: Python<'py>,
        data: PyBuffer<u8>,
    ) -> PyResult<Vec<Bound<'py, PyBytes>>> {
        let data = data.to_vec(py)?;
        let packets = self.encoder().encode(&data);
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
) -> PyResult<EncoderSettings> {
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
) -> PyResult<SinkOptions> {
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

pub(crate) fn file(path: &str) -> SourceSpec {
    SourceSpec::File(PathBuf::from(path))
}

pub(crate) fn seconds(name: &str, value: f64) -> PyResult<Duration> {
    Duration::try_from_secs_f64(value)
        .map_err(|error| to_python_error(CoreError::InvalidRequest(format!("{name}: {error}"))))
}

pub(crate) fn diagnostics(py: Python<'_>, diagnostics: Diagnostics) -> PyResult<Bound<'_, PyDict>> {
    let now = Instant::now();
    let dave = PyDict::new(py);
    dave.set_item("protocol_version", diagnostics.dave.protocol_version)?;
    dave.set_item("epoch", diagnostics.dave.epoch)?;
    let history: Vec<_> = diagnostics
        .history
        .iter()
        .map(|entry| history_entry(now, entry))
        .collect();
    let dict = PyDict::new(py);
    dict.set_item("versions", versions())?;
    dict.set_item("guild_id", u64::from(diagnostics.guild))?;
    dict.set_item("channel_id", u64::from(diagnostics.channel))?;
    dict.set_item("shard_id", u32::from(diagnostics.shard))?;
    dict.set_item("status", diagnostics.status.as_str())?;
    dict.set_item("endpoint_host", diagnostics.endpoint_host.as_deref())?;
    dict.set_item("mode", diagnostics.mode)?;
    dict.set_item("ssrc", diagnostics.ssrc)?;
    dict.set_item("dave", dave)?;
    dict.set_item(
        "members",
        diagnostics
            .members
            .into_iter()
            .map(u64::from)
            .collect::<Vec<_>>(),
    )?;
    dict.set_item("last_close_code", diagnostics.last_close_code)?;
    dict.set_item("history", history)?;
    dict.set_item("pacer", diagnostics.pacer.map(pacer))?;
    dict.set_item(
        "counters",
        diagnostics.counters.into_iter().collect::<HashMap<_, _>>(),
    )?;
    Ok(dict)
}

fn history_entry(
    now: Instant,
    entry: &HistoryEntry,
) -> (f64, &'static str, Option<&'static str>, Option<u16>, &str) {
    (
        now.saturating_duration_since(entry.at).as_secs_f64(),
        entry.status.as_str(),
        entry.reason.map(DisconnectReason::as_str),
        entry.code,
        &entry.detail,
    )
}

fn pacer(load: ThreadLoad) -> HashMap<&'static str, u64> {
    HashMap::from([
        ("lanes", u64::try_from(load.lanes).unwrap_or(u64::MAX)),
        ("work_ewma_us", micros(load.work_ewma)),
        ("work_max_us", micros(load.work_max)),
        ("late_ticks", load.late_ticks),
        ("lane_panics", load.lane_panics),
    ])
}

fn micros(duration: Duration) -> u64 {
    u64::try_from(duration.as_micros()).unwrap_or(u64::MAX)
}

fn sample_format(sample: &str) -> PyResult<SampleFormat> {
    match sample {
        "s16le" => Ok(SampleFormat::S16Le),
        "f32le" => Ok(SampleFormat::F32Le),
        other => Err(unknown("sample", other)),
    }
}

fn unknown(name: &str, value: &str) -> PyErr {
    to_python_error(CoreError::InvalidRequest(format!(
        "unknown {name} {value:?}"
    )))
}
