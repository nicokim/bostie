pub(crate) mod decode;
pub(crate) mod http;

use std::mem;
use std::panic::{self, AssertUnwindSafe};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

use bytes::Bytes;
use parking_lot::{Condvar, Mutex, MutexGuard};

use crate::error::CoreError;
use crate::ids::Handle;
use crate::inbox::{Delivery, Inbox};
use crate::runtime::Owner;
use crate::voice::panic_message;
use crate::voice::source::decode::{Decoder, Input, Step};
use crate::voice::source::http::{Http, HttpMedia};
use crate::voice::types::{SAMPLE_RATE, SourceSpec};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SourceKind {
    Pcm,
    Opus,
}

impl SourceKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Pcm => "pcm",
            Self::Opus => "opus",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SourceInfo {
    pub kind: SourceKind,
    pub duration: Option<Duration>,
    pub seekable: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SourceRead {
    Pending,
    Chunks(Vec<Bytes>),
    Ended(Option<String>),
}

pub struct Source {
    id: Handle,
    owner: Owner,
    shared: Arc<Shared>,
}

struct Shared {
    id: Handle,
    inbox: Arc<Inbox>,
    prefetch: u64,
    state: Mutex<State>,
    changed: Condvar,
}

struct State {
    info: Option<SourceInfo>,
    chunks: Vec<Bytes>,
    buffered: u64,
    progress: Progress,
    seek: Option<Duration>,
    generation: u64,
    wake_armed: bool,
    stopped: bool,
    closed: bool,
}

#[derive(PartialEq, Eq)]
enum Progress {
    Playing,
    Ended,
    Failed(String),
}

enum Command {
    Decode,
    Seek(Duration, u64),
    Close,
}

impl Source {
    pub fn open(
        id: Handle,
        spec: SourceSpec,
        inbox: Arc<Inbox>,
        http: &Http,
        prefetch: Duration,
    ) -> Result<Self, CoreError> {
        let input = match spec {
            SourceSpec::File(path) => Input::File(path),
            SourceSpec::Bytes { data, hint } => Input::Bytes { data, hint },
            SourceSpec::Url { url, headers } => {
                Input::Url(Box::new(HttpMedia::new(http, &url, &headers)?))
            }
        };
        Self::spawn(id, inbox, prefetch, move |shared| play(shared, input))
    }

    fn spawn(
        id: Handle,
        inbox: Arc<Inbox>,
        prefetch: Duration,
        body: impl FnOnce(&Shared) + Send + 'static,
    ) -> Result<Self, CoreError> {
        let shared = Arc::new(Shared {
            id,
            inbox,
            prefetch: frames_in(prefetch),
            state: Mutex::new(State {
                info: None,
                chunks: Vec::new(),
                buffered: 0,
                progress: Progress::Playing,
                seek: None,
                generation: 0,
                wake_armed: true,
                stopped: false,
                closed: false,
            }),
            changed: Condvar::new(),
        });
        let decoding = Arc::clone(&shared);
        thread::Builder::new()
            .name(format!("bostie-source-{id}"))
            .spawn(move || {
                let outcome = panic::catch_unwind(AssertUnwindSafe(|| body(&decoding)));
                let failure = outcome
                    .err()
                    .map(|payload| format!("decoder panicked: {}", panic_message(&*payload)));
                decoding.stop(failure);
            })
            .map_err(CoreError::Runtime)?;
        Ok(Self {
            id,
            owner: Owner::current(),
            shared,
        })
    }

    pub fn id(&self) -> Handle {
        self.id
    }

    pub fn info(&self) -> Result<Option<SourceInfo>, CoreError> {
        self.owner.check()?;
        Ok(self.shared.state.lock().info)
    }

    pub fn read(&self) -> Result<SourceRead, CoreError> {
        self.owner.check()?;
        let mut state = self.open_state()?;
        if !state.chunks.is_empty() {
            state.buffered = 0;
            let chunks = mem::take(&mut state.chunks);
            self.shared.changed.notify_one();
            return Ok(SourceRead::Chunks(chunks));
        }
        Ok(match &state.progress {
            Progress::Playing => {
                state.wake_armed = true;
                SourceRead::Pending
            }
            Progress::Ended => SourceRead::Ended(None),
            Progress::Failed(error) => SourceRead::Ended(Some(error.clone())),
        })
    }

    pub fn error(&self) -> Result<Option<String>, CoreError> {
        self.owner.check()?;
        Ok(match &self.shared.state.lock().progress {
            Progress::Failed(error) => Some(error.clone()),
            Progress::Playing | Progress::Ended => None,
        })
    }

    pub fn seek(&self, position: Duration) -> Result<(), CoreError> {
        self.owner.check()?;
        let mut state = self.open_state()?;
        if !state.info.is_some_and(|info| info.seekable) {
            return Err(CoreError::InvalidRequest(
                "this source is not seekable".to_owned(),
            ));
        }
        if state.stopped {
            return Ok(());
        }
        state.seek = Some(position);
        state.generation += 1;
        state.chunks.clear();
        state.buffered = 0;
        state.progress = Progress::Playing;
        self.shared.changed.notify_one();
        Ok(())
    }

    pub fn is_closed(&self) -> bool {
        self.shared.state.lock().closed
    }

    pub fn close(&self) {
        if !self.owner.is_current() {
            return;
        }
        let mut state = self.shared.state.lock();
        if mem::replace(&mut state.closed, true) {
            return;
        }
        state.chunks.clear();
        state.buffered = 0;
        self.shared.wake(&mut state);
        self.shared.changed.notify_one();
    }

    fn open_state(&self) -> Result<MutexGuard<'_, State>, CoreError> {
        let state = self.shared.state.lock();
        if state.closed {
            return Err(CoreError::HandleClosed(self.id));
        }
        Ok(state)
    }
}

impl Drop for Source {
    fn drop(&mut self) {
        self.close();
    }
}

impl Shared {
    fn publish(&self, info: SourceInfo) -> u64 {
        let mut state = self.state.lock();
        state.info = Some(info);
        self.wake(&mut state);
        state.generation
    }

    fn push(&self, generation: u64, chunk: Bytes, frames: u64) {
        let mut state = self.state.lock();
        if state.generation != generation || state.closed {
            return;
        }
        state.chunks.push(chunk);
        state.buffered += frames;
        self.wake(&mut state);
    }

    fn finish(&self, generation: u64, error: Option<String>) {
        let mut state = self.state.lock();
        if state.generation != generation {
            return;
        }
        state.progress = error.map_or(Progress::Ended, Progress::Failed);
        self.wake(&mut state);
    }

    fn stop(&self, failure: Option<String>) {
        let mut state = self.state.lock();
        state.stopped = true;
        if let Some(failure) = failure {
            state.progress = Progress::Failed(failure);
        }
        self.wake(&mut state);
    }

    fn command(&self) -> Command {
        let mut state = self.state.lock();
        loop {
            if state.closed {
                return Command::Close;
            }
            if let Some(position) = state.seek.take() {
                return Command::Seek(position, state.generation);
            }
            if state.progress == Progress::Playing && state.buffered < self.prefetch {
                return Command::Decode;
            }
            self.changed.wait(&mut state);
        }
    }

    fn wake(&self, state: &mut State) {
        if mem::take(&mut state.wake_armed) {
            self.inbox.push(Delivery::VoiceWake { handle: self.id });
        }
    }
}

fn play(shared: &Shared, input: Input) {
    let mut decoder = match Decoder::open(input) {
        Ok(decoder) => decoder,
        Err(error) => return shared.finish(0, Some(error.to_string())),
    };
    let mut generation = shared.publish(decoder.info());
    loop {
        match shared.command() {
            Command::Close => return,
            Command::Seek(position, current) => {
                generation = current;
                if let Err(error) = decoder.seek(position) {
                    shared.finish(generation, Some(error.to_string()));
                }
            }
            Command::Decode => match decoder.step() {
                Ok(Step::Chunk { data, frames }) => shared.push(generation, data, frames),
                Ok(Step::Skipped) => {}
                Ok(Step::Ended) => shared.finish(generation, None),
                Err(error) => shared.finish(generation, Some(error.to_string())),
            },
        }
    }
}

fn frames_in(duration: Duration) -> u64 {
    let frames = duration.as_millis() * u128::from(SAMPLE_RATE) / 1_000;
    u64::try_from(frames).unwrap_or(u64::MAX)
}

#[cfg(test)]
pub(crate) mod tests {
    use std::path::{Path, PathBuf};
    use std::time::Instant;

    use super::*;
    use crate::bell::{self, BellReceiver};
    use crate::voice::codec;

    const PATIENCE: Duration = Duration::from_secs(10);
    const MINUS_3_DB: f32 = std::f32::consts::FRAC_1_SQRT_2;
    const PREFETCH: Duration = Duration::from_millis(100);
    const HTTP_BUFFER: usize = 4 * 1024;

    pub(crate) struct Harness {
        pub(crate) inbox: Arc<Inbox>,
        _bell: BellReceiver,
        pub(crate) http: Http,
        next: u64,
    }

    impl Harness {
        pub(crate) fn new() -> Self {
            let (bell, receiver) = bell::pair().unwrap();
            Self {
                inbox: Arc::new(Inbox::new(bell)),
                _bell: receiver,
                http: Http::new(crate::runtime::shared().unwrap(), HTTP_BUFFER),
                next: 1,
            }
        }

        pub(crate) fn open(&mut self, spec: SourceSpec) -> Source {
            let id = self.handle();
            Source::open(id, spec, Arc::clone(&self.inbox), &self.http, PREFETCH).unwrap()
        }

        fn file(&mut self, name: &str) -> Source {
            self.open(SourceSpec::File(fixture(name)))
        }

        fn handle(&mut self) -> Handle {
            let id = Handle::from(self.next);
            self.next += 1;
            id
        }

        pub(crate) fn wakes(&self) -> usize {
            self.inbox
                .drain()
                .iter()
                .filter(|delivery| matches!(delivery, Delivery::VoiceWake { .. }))
                .count()
        }
    }

    pub(crate) fn fixture(name: &str) -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("src/voice/testdata/media")
            .join(name)
    }

    pub(crate) fn wait_for_info(source: &Source) -> SourceInfo {
        let deadline = Instant::now() + PATIENCE;
        loop {
            if let Some(info) = source.info().unwrap() {
                return info;
            }
            if let Some(error) = source.error().unwrap() {
                panic!("the source failed while probing: {error}");
            }
            assert!(Instant::now() < deadline, "the probe did not finish");
            thread::sleep(Duration::from_millis(1));
        }
    }

    pub(crate) fn read_to_end(source: &Source) -> (Vec<Bytes>, Option<String>) {
        let deadline = Instant::now() + PATIENCE;
        let mut chunks = Vec::new();
        loop {
            match source.read().unwrap() {
                SourceRead::Chunks(more) => chunks.extend(more),
                SourceRead::Pending => {
                    assert!(Instant::now() < deadline, "the source did not end");
                    thread::sleep(Duration::from_millis(1));
                }
                SourceRead::Ended(error) => return (chunks, error),
            }
        }
    }

    pub(crate) fn samples(chunks: &[Bytes]) -> Vec<f32> {
        chunks
            .iter()
            .inspect(|chunk| assert_eq!(chunk.len() % 8, 0, "a chunk split a stereo frame"))
            .flat_map(|chunk| chunk.as_chunks::<4>().0.iter().copied())
            .map(f32::from_le_bytes)
            .collect()
    }

    pub(crate) fn frames(kind: SourceKind, chunks: &[Bytes]) -> u64 {
        match kind {
            SourceKind::Opus => chunks
                .iter()
                .map(|packet| u64::from(codec::packet_samples(packet).unwrap()))
                .sum(),
            SourceKind::Pcm => (samples(chunks).len() / 2) as u64,
        }
    }

    fn left(stereo: &[f32]) -> Vec<f32> {
        stereo.iter().step_by(2).copied().collect()
    }

    #[expect(clippy::cast_precision_loss)]
    fn pitch(samples: &[f32]) -> f32 {
        let rises = samples
            .windows(2)
            .filter(|pair| pair[0] < 0.0 && pair[1] >= 0.0)
            .count();
        rises as f32 * SAMPLE_RATE as f32 / samples.len() as f32
    }

    fn wait_until(mut done: impl FnMut() -> bool, what: &str) {
        let deadline = Instant::now() + PATIENCE;
        while !done() {
            assert!(Instant::now() < deadline, "timed out waiting for {what}");
            thread::sleep(Duration::from_millis(1));
        }
    }

    fn seekable_pcm() -> SourceInfo {
        SourceInfo {
            kind: SourceKind::Pcm,
            duration: None,
            seekable: true,
        }
    }

    impl Harness {
        fn spawn(&mut self, body: impl FnOnce(&Shared) + Send + 'static) -> Source {
            let id = self.handle();
            Source::spawn(id, Arc::clone(&self.inbox), PREFETCH, body).unwrap()
        }
    }

    #[test]
    fn ogg_and_webm_opus_tracks_pass_their_packets_through_unchanged() {
        let mut harness = Harness::new();
        for name in ["tone.opus", "tone.webm"] {
            let source = harness.file(name);

            let info = wait_for_info(&source);
            let (packets, error) = read_to_end(&source);

            assert_eq!(info.kind, SourceKind::Opus, "{name}");
            assert_eq!(error, None, "{name}");
            assert_eq!(packets.len(), 51, "{name}");
            assert!(
                packets.iter().all(|packet| codec::is_stereo_20ms(packet)),
                "{name}"
            );
        }
    }

    #[test]
    fn every_pcm_source_yields_48_khz_stereo_f32_at_the_original_pitch() {
        let mut harness = Harness::new();
        for (name, stereo) in [
            ("tone.ogg", true),
            ("tone.flac", false),
            ("tone-s16.wav", false),
            ("tone-f32.wav", false),
            ("tone.mp3", false),
        ] {
            let source = harness.file(name);

            let info = wait_for_info(&source);
            let (chunks, error) = read_to_end(&source);
            let samples = samples(&chunks);

            assert_eq!(info.kind, SourceKind::Pcm, "{name}");
            assert_eq!(info.duration, Some(Duration::from_secs(1)), "{name}");
            assert_eq!(error, None, "{name}");
            assert_eq!(samples.len(), 48_000 * 2, "{name}");
            let (frames, _) = samples.as_chunks::<2>();
            let (left, right): (Vec<f32>, Vec<f32>) = frames[4_800..43_200]
                .iter()
                .map(|[left, right]| (left, right))
                .unzip();
            let hz = pitch(&left);
            assert!((hz - 1_000.0).abs() < 5.0, "{name}: {hz} Hz");
            if stereo {
                let hz = pitch(&right);
                assert!((hz - 50.0).abs() < 2.5, "{name}: {hz} Hz on the right");
            } else {
                assert_eq!(left, right, "{name}");
            }
        }
    }

    #[test]
    fn an_m4a_plays_at_the_original_pitch_with_its_aac_priming_left_in() {
        let mut harness = Harness::new();
        let source = harness.file("tone.m4a");

        let info = wait_for_info(&source);
        let (chunks, error) = read_to_end(&source);
        let samples = samples(&chunks);

        let priming = Duration::from_nanos(1_024 * 1_000_000_000 / 44_100);
        assert_eq!(info.kind, SourceKind::Pcm);
        assert_eq!(info.duration, Some(Duration::from_secs(1) + priming));
        assert_eq!(error, None);
        let hz = pitch(&left(&samples)[4_800..43_200]);
        assert!((hz - 1_000.0).abs() < 5.0, "{hz} Hz");
    }

    #[test]
    fn an_mp3_with_an_id3v2_tag_decodes_from_its_first_frame() {
        let mut harness = Harness::new();
        let bytes = std::fs::read(fixture("tone.mp3")).unwrap();
        let source = harness.open(SourceSpec::Bytes {
            data: Bytes::from(bytes.clone()),
            hint: Some("mp3".to_owned()),
        });

        let (chunks, error) = read_to_end(&source);

        assert!(bytes.starts_with(b"ID3\x03"));
        assert_eq!(error, None);
        assert_eq!(samples(&chunks).len(), 48_000 * 2);
    }

    #[test]
    fn five_point_one_is_downmixed_with_the_centre_at_minus_3_db_and_no_lfe() {
        let mut harness = Harness::new();
        let source = harness.file("tone-5.1.flac");

        let (chunks, error) = read_to_end(&source);
        let samples = samples(&chunks);

        assert_eq!(error, None);
        assert_eq!(samples.len(), 48_000 * 2);
        assert!(
            samples
                .as_chunks::<2>()
                .0
                .iter()
                .all(|[left, right]| left.to_bits() == right.to_bits())
        );
        let peak = samples
            .iter()
            .fold(0.0_f32, |peak, sample| peak.max(sample.abs()));
        assert!((peak - 0.5 * MINUS_3_DB).abs() < 0.02, "peak {peak}");
        let hz = pitch(&left(&samples)[4_800..43_200]);
        assert!((hz - 1_000.0).abs() < 5.0, "{hz} Hz");
    }

    #[test]
    fn unknown_formats_end_the_source_naming_what_has_no_decoder() {
        let mut harness = Harness::new();
        for (hint, expected) in [
            (None, "no decoder for this media"),
            (Some("xyz"), "no decoder for xyz"),
        ] {
            let source = harness.open(SourceSpec::Bytes {
                data: Bytes::from(vec![0x5a; 4_096]),
                hint: hint.map(str::to_owned),
            });

            let (chunks, error) = read_to_end(&source);

            assert!(chunks.is_empty());
            assert_eq!(error.as_deref(), Some(expected));
            assert_eq!(source.error().unwrap().as_deref(), Some(expected));
            assert_eq!(source.info().unwrap(), None);
        }
    }

    #[test]
    fn codecs_without_a_decoder_end_the_source_naming_the_codec() {
        let mut harness = Harness::new();
        for (name, expected) in [
            ("tone-ac3.mka", "no decoder for ac3 in matroska"),
            ("tone-5.1.opus", "no decoder for opus with 6 channels"),
        ] {
            let source = harness.file(name);

            let (chunks, error) = read_to_end(&source);

            assert!(chunks.is_empty(), "{name}");
            assert_eq!(error.as_deref(), Some(expected), "{name}");
        }
    }

    fn chained(first: &str, second: &str) -> SourceSpec {
        let mut bytes = std::fs::read(fixture(first)).unwrap();
        bytes.extend(std::fs::read(fixture(second)).unwrap());
        SourceSpec::Bytes {
            data: Bytes::from(bytes),
            hint: None,
        }
    }

    #[test]
    fn a_chained_ogg_stream_plays_every_link_without_a_gap() {
        let mut harness = Harness::new();
        for (name, expected) in [("tone.ogg", 96_000), ("tone.opus", 2 * 51 * 960)] {
            let source = harness.open(chained(name, name));

            let kind = wait_for_info(&source).kind;
            let (chunks, error) = read_to_end(&source);

            assert_eq!(error, None, "{name}");
            assert_eq!(frames(kind, &chunks), expected, "{name}");
        }
    }

    #[test]
    fn a_chained_ogg_stream_that_switches_codec_kind_ends_with_an_error() {
        let mut harness = Harness::new();
        let source = harness.open(chained("tone.ogg", "tone.opus"));

        let (_, error) = read_to_end(&source);

        assert_eq!(
            error.as_deref(),
            Some("the stream switched between Opus and PCM audio")
        );
    }

    #[test]
    fn a_missing_file_ends_the_source_with_the_io_error_text() {
        let mut harness = Harness::new();
        let path = fixture("missing.ogg");
        let expected = std::fs::File::open(&path).unwrap_err().to_string();
        let source = harness.open(SourceSpec::File(path));

        let (_, error) = read_to_end(&source);

        assert_eq!(error, Some(expected));
    }

    #[test]
    fn a_seek_after_the_end_plays_again_from_the_new_position() {
        let mut harness = Harness::new();
        let source = harness.file("tone.flac");
        let (first, _) = read_to_end(&source);

        source.seek(Duration::ZERO).unwrap();
        let (again, error) = read_to_end(&source);

        assert_eq!(error, None);
        let again = samples(&again);
        let first = samples(&first);
        let differ = again
            .iter()
            .zip(&first)
            .position(|(a, b)| a.to_bits() != b.to_bits());
        assert_eq!((again.len(), differ), (first.len(), None));
    }

    #[test]
    fn the_decoder_stops_one_chunk_past_the_prefetch_bound_until_it_is_read() {
        let mut harness = Harness::new();
        let source = harness.file("tone-s16.wav");
        wait_for_info(&source);

        let prefetch = frames_in(PREFETCH);
        wait_until(
            || source.shared.state.lock().buffered >= prefetch,
            "the prefetch",
        );
        thread::sleep(Duration::from_millis(20));
        {
            let state = source.shared.state.lock();
            let last = state.chunks.last().map_or(0, |chunk| chunk.len() / 8) as u64;
            assert!(state.buffered - last < prefetch, "{}", state.buffered);
            assert!(state.progress == Progress::Playing);
        }
        let (chunks, error) = read_to_end(&source);

        assert_eq!(error, None);
        assert_eq!(samples(&chunks).len(), 48_000 * 2);
    }

    #[test]
    fn a_panicking_decoder_ends_the_source_with_its_message_and_a_wake() {
        let mut harness = Harness::new();
        let source = harness.spawn(|_| panic!("corrupt stream"));

        wait_until(|| harness.wakes() == 1, "the wake");

        assert_eq!(
            source.read().unwrap(),
            SourceRead::Ended(Some("decoder panicked: corrupt stream".to_owned()))
        );
        assert_eq!(
            source.error().unwrap().as_deref(),
            Some("decoder panicked: corrupt stream")
        );
    }

    #[test]
    fn a_seek_after_the_decoder_panicked_still_reports_the_panic() {
        let mut harness = Harness::new();
        let source = harness.spawn(|shared| {
            shared.publish(seekable_pcm());
            panic!("bad frame");
        });
        wait_until(|| source.error().unwrap().is_some(), "the panic");

        source.seek(Duration::ZERO).unwrap();

        assert_eq!(
            source.read().unwrap(),
            SourceRead::Ended(Some("decoder panicked: bad frame".to_owned()))
        );
    }

    #[test]
    fn a_pending_read_arms_one_wake_that_the_next_chunk_fires() {
        let mut harness = Harness::new();
        let (step, steps) = std::sync::mpsc::channel::<()>();
        let source = harness.spawn(move |shared| {
            let generation = shared.publish(seekable_pcm());
            steps.recv().unwrap();
            shared.push(generation, Bytes::from_static(&[0; 8]), 1);
            steps.recv().unwrap();
            shared.push(generation, Bytes::from_static(&[1; 8]), 1);
            shared.finish(generation, None);
        });
        wait_for_info(&source);
        assert_eq!(harness.wakes(), 1);

        assert_eq!(source.read().unwrap(), SourceRead::Pending);
        assert_eq!(source.read().unwrap(), SourceRead::Pending);
        step.send(()).unwrap();
        wait_until(|| harness.wakes() == 1, "the data wake");
        assert_eq!(
            source.read().unwrap(),
            SourceRead::Chunks(vec![Bytes::from_static(&[0; 8])])
        );
        step.send(()).unwrap();
        wait_until(
            || source.shared.state.lock().progress == Progress::Ended,
            "the end",
        );

        assert_eq!(
            source.read().unwrap(),
            SourceRead::Chunks(vec![Bytes::from_static(&[1; 8])])
        );
        assert_eq!(source.read().unwrap(), SourceRead::Ended(None));
        assert_eq!(harness.wakes(), 0);
    }

    #[test]
    fn a_seek_drops_buffered_audio_and_everything_decoded_before_it() {
        let mut harness = Harness::new();
        let (step, steps) = std::sync::mpsc::channel::<()>();
        let (seeked, seeks) = std::sync::mpsc::channel();
        let source = harness.spawn(move |shared| {
            shared.push(0, Bytes::from_static(b"buffered"), 1);
            let generation = shared.publish(seekable_pcm());
            steps.recv().unwrap();
            shared.push(generation, Bytes::from_static(b"stale..."), 1);
            shared.finish(generation, Some("stale end".to_owned()));
            let Command::Seek(position, current) = shared.command() else {
                panic!("expected the seek");
            };
            seeked.send(position).unwrap();
            shared.push(current, Bytes::from_static(b"fresh..."), 1);
            shared.finish(current, None);
        });
        wait_for_info(&source);

        source.seek(Duration::from_millis(250)).unwrap();
        step.send(()).unwrap();
        let (chunks, error) = read_to_end(&source);

        assert_eq!(seeks.recv().unwrap(), Duration::from_millis(250));
        assert_eq!(chunks, [Bytes::from_static(b"fresh...")]);
        assert_eq!(error, None);
    }

    #[test]
    fn sources_that_cannot_seek_refuse_with_an_invalid_request() {
        let mut harness = Harness::new();
        let (_hold, held) = std::sync::mpsc::channel::<()>();
        let probing = harness.spawn(move |_| {
            held.recv().ok();
        });
        let live = harness.spawn(|shared| {
            shared.publish(SourceInfo {
                seekable: false,
                ..seekable_pcm()
            });
        });
        wait_for_info(&live);

        for source in [&probing, &live] {
            assert!(matches!(
                source.seek(Duration::ZERO),
                Err(CoreError::InvalidRequest(message)) if message.contains("not seekable")
            ));
        }
    }

    #[test]
    fn close_wakes_an_armed_reader_and_later_calls_see_a_closed_handle() {
        let mut harness = Harness::new();
        let (hold, held) = std::sync::mpsc::channel::<()>();
        let source = harness.spawn(move |shared| {
            shared.publish(seekable_pcm());
            held.recv().ok();
        });
        wait_for_info(&source);
        harness.wakes();
        assert_eq!(source.read().unwrap(), SourceRead::Pending);

        source.close();
        source.close();

        assert_eq!(harness.wakes(), 1);
        assert!(source.is_closed());
        assert!(matches!(source.read(), Err(CoreError::HandleClosed(_))));
        assert!(matches!(
            source.seek(Duration::ZERO),
            Err(CoreError::HandleClosed(_))
        ));
        assert_eq!(source.info().unwrap(), Some(seekable_pcm()));
        drop(hold);
    }

    #[test]
    fn closing_or_dropping_a_source_ends_its_decoder_thread() {
        let mut harness = Harness::new();
        let closed = harness.file("tone.flac");
        let dropped = harness.file("tone.flac");
        wait_for_info(&closed);
        wait_for_info(&dropped);
        let closed_thread = Arc::downgrade(&closed.shared);
        let dropped_thread = Arc::downgrade(&dropped.shared);

        closed.close();
        drop(dropped);

        wait_until(|| closed_thread.strong_count() == 1, "the closed thread");
        wait_until(|| dropped_thread.strong_count() == 0, "the dropped thread");
    }
}
