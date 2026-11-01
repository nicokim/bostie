use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Instant;

use bostie_core::voice::types::{
    Application, EncoderSettings, Overflow, PcmFormat, ReceiveFormat, ReceiveStall, SampleFormat,
    SinkFormat, SinkOptions, Speaking, StallPolicy,
};
use bostie_core::voice::{
    Ended, FrameEncoder, Item, ReceiveRead, Receiver, ReceiverStats, Sink, SinkStats, Source,
    SourceRead,
};
use bostie_core::{CoreError, UserId};
use pyo3::IntoPyObjectExt;
use pyo3::buffer::PyBuffer;
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::{Error, Result, seconds};

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

    fn stats(&self) -> Result<(u64, u64, u64, u64, u64, u64)> {
        let SinkStats {
            sent,
            consumed,
            underruns,
            held_ticks,
            dropped,
            passthrough_packets,
        } = self.0.stats()?;
        Ok((
            sent,
            consumed,
            underruns,
            held_ticks,
            dropped,
            passthrough_packets,
        ))
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

    fn mark(&self) -> Result<u64> {
        Ok(self.0.mark()?)
    }

    fn stats(&self) -> Result<(u64, u64, u64)> {
        let ReceiverStats {
            delivered,
            dropped,
            lost,
        } = self.0.stats()?;
        Ok((delivered, dropped, lost))
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
        sample: Choice<SampleFormat>,
        bitrate: u32,
        application: Choice<Application>,
        fec: bool,
        packet_loss: u8,
        complexity: u8,
    ) -> Result<Self> {
        let format = PcmFormat {
            rate,
            channels,
            sample: sample.0,
        };
        let settings = encoder_settings(bitrate, application, fec, packet_loss, complexity);
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

pub(crate) struct Choice<T>(pub(crate) T);

pub(crate) trait Choices: Copy + 'static {
    const OPTION: &'static str;
    const NAMES: &'static [(&'static str, Self)];
}

impl<T: Choices> FromPyObject<'_, '_> for Choice<T> {
    type Error = PyErr;

    fn extract(obj: Borrowed<'_, '_, PyAny>) -> PyResult<Self> {
        let name = <&str>::extract(obj)?;
        T::NAMES
            .iter()
            .find(|(known, _)| *known == name)
            .map(|&(_, value)| Self(value))
            .ok_or_else(|| unknown(T::OPTION, name).into())
    }
}

pub(crate) fn names<T: Choices>() -> Vec<&'static str> {
    T::NAMES.iter().map(|&(name, _)| name).collect()
}

impl Choices for Application {
    const OPTION: &'static str = "application";
    const NAMES: &'static [(&'static str, Self)] = &[
        ("audio", Self::Audio),
        ("voip", Self::Voip),
        ("lowdelay", Self::LowDelay),
    ];
}

impl Choices for SampleFormat {
    const OPTION: &'static str = "sample";
    const NAMES: &'static [(&'static str, Self)] =
        &[("s16le", Self::S16Le), ("f32le", Self::F32Le)];
}

#[derive(Clone, Copy)]
pub(crate) enum SinkKind {
    Pcm,
    Opus,
}

impl Choices for SinkKind {
    const OPTION: &'static str = "kind";
    const NAMES: &'static [(&'static str, Self)] = &[("pcm", Self::Pcm), ("opus", Self::Opus)];
}

impl Choices for StallPolicy {
    const OPTION: &'static str = "stall";
    const NAMES: &'static [(&'static str, Self)] = &[("hold", Self::Hold), ("drop", Self::Drop)];
}

#[derive(Clone, Copy)]
pub(crate) enum ReceiveKind {
    Pcm,
    Opus,
    Packets,
}

impl Choices for ReceiveKind {
    const OPTION: &'static str = "kind";
    const NAMES: &'static [(&'static str, Self)] = &[
        ("pcm", Self::Pcm),
        ("opus", Self::Opus),
        ("packets", Self::Packets),
    ];
}

impl Choices for Overflow {
    const OPTION: &'static str = "overflow";
    const NAMES: &'static [(&'static str, Self)] = &[
        ("drop_oldest", Self::DropOldest),
        ("drop_newest", Self::DropNewest),
    ];
}

impl Choices for ReceiveStall {
    const OPTION: &'static str = "stall";
    const NAMES: &'static [(&'static str, Self)] = &[("wait", Self::Wait), ("end", Self::End)];
}

pub(crate) fn encoder_settings(
    bitrate: u32,
    application: Choice<Application>,
    fec: bool,
    packet_loss: u8,
    complexity: u8,
) -> EncoderSettings {
    EncoderSettings {
        bitrate,
        application: application.0,
        fec,
        packet_loss,
        complexity,
    }
}

pub(crate) fn sink_options(
    kind: Choice<SinkKind>,
    rate: u32,
    channels: u8,
    sample: Choice<SampleFormat>,
    buffer: f64,
    volume: f32,
    stall: Choice<StallPolicy>,
) -> Result<SinkOptions> {
    let format = match kind.0 {
        SinkKind::Pcm => SinkFormat::Pcm(PcmFormat {
            rate,
            channels,
            sample: sample.0,
        }),
        SinkKind::Opus => SinkFormat::Opus,
    };
    Ok(SinkOptions {
        format,
        buffer: seconds("buffer", buffer)?,
        volume,
        stall: stall.0,
    })
}

pub(crate) fn receive_format(
    kind: Choice<ReceiveKind>,
    rate: u32,
    channels: u8,
    sample: Choice<SampleFormat>,
    mix: bool,
) -> Result<ReceiveFormat> {
    let pcm = PcmFormat {
        rate,
        channels,
        sample: sample.0,
    };
    Ok(match (kind.0, mix) {
        (ReceiveKind::Pcm, false) => ReceiveFormat::Pcm(pcm),
        (ReceiveKind::Pcm, true) => ReceiveFormat::Mixed(pcm),
        (ReceiveKind::Opus, false) => ReceiveFormat::Opus,
        (ReceiveKind::Packets, false) => ReceiveFormat::Packets,
        (ReceiveKind::Opus | ReceiveKind::Packets, true) => {
            return Err(CoreError::InvalidRequest("mix=True needs a PCM format".to_owned()).into());
        }
    })
}

pub(crate) fn users(ids: Vec<u64>) -> HashSet<UserId> {
    ids.into_iter().map(UserId::from).collect()
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
        } => {
            let (speaking, duration) = speaking_parts(speaking);
            (
                intern!(py, "speech"),
                u64::from(user),
                ssrc,
                speaking,
                offset,
                age(now, at),
                duration,
                queued,
            )
                .into_bound_py_any(py)
        }
    }
}

pub(crate) fn speaking_parts(speaking: Speaking) -> (bool, f64) {
    match speaking {
        Speaking::Started => (true, 0.0),
        Speaking::Stopped(duration) => (false, duration.as_secs_f64()),
    }
}

fn age(now: Instant, at: Instant) -> f64 {
    now.saturating_duration_since(at).as_secs_f64()
}

fn unknown(name: &str, value: &str) -> Error {
    CoreError::InvalidRequest(format!("unknown {name} {value:?}")).into()
}
