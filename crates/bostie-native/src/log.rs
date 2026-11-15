use std::cell::Cell;
use std::fmt;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Mutex, Once, PoisonError};

use bostie_core::LogSink;
use tracing::field::{Field, Visit};
use tracing::level_filters::LevelFilter;
use tracing::span::{Attributes, Id, Record};
use tracing::subscriber::Interest;
use tracing::{Event, Level, Metadata, Subscriber};

pub(crate) const LOGGERS: [&str; 4] = ["bostie.voice", "bostie.gateway", "bostie.rest", "bostie"];
const ROUTES: [(&str, usize); 5] = [
    ("bostie_core::voice", 0),
    ("davey", 0),
    ("bostie_core::gateway", 1),
    ("bostie_core::rest", 2),
    ("twilight_http_ratelimiting", 2),
];
const ROOT: usize = 3;
const OFF: u8 = 0;
const WARN: u8 = 2;
const TRACE: u8 = 5;

static LEVELS: [AtomicU8; 4] = [const { AtomicU8::new(WARN) }; 4];
static SINKS: Mutex<Vec<LogSink>> = Mutex::new(Vec::new());
static INSTALL: Once = Once::new();

thread_local! {
    static FORWARDING: Cell<bool> = const { Cell::new(false) };
}

pub(crate) fn register(sink: LogSink) {
    let mut sinks = SINKS.lock().unwrap_or_else(PoisonError::into_inner);
    sinks.retain(LogSink::is_open);
    sinks.push(sink);
    drop(sinks);
    INSTALL.call_once(|| drop(tracing::subscriber::set_global_default(Forward)));
}

pub(crate) fn set_levels(python_levels: [i32; 4]) {
    for (level, python) in LEVELS.iter().zip(python_levels) {
        level.store(rank_for_python(python), Ordering::Relaxed);
    }
    tracing::callsite::rebuild_interest_cache();
}

pub(crate) fn python_level(level: Level) -> u8 {
    match level {
        Level::TRACE => 5,
        Level::DEBUG => 10,
        Level::INFO => 20,
        Level::WARN => 30,
        Level::ERROR => 40,
    }
}

fn rank_for_python(python: i32) -> u8 {
    match python {
        ..=5 => TRACE,
        6..=10 => 4,
        11..=20 => 3,
        21..=30 => WARN,
        31..=40 => 1,
        _ => OFF,
    }
}

fn rank(level: Level) -> u8 {
    match level {
        Level::ERROR => 1,
        Level::WARN => WARN,
        Level::INFO => 3,
        Level::DEBUG => 4,
        Level::TRACE => TRACE,
    }
}

fn route(target: &str) -> usize {
    ROUTES
        .iter()
        .find(|(prefix, _)| {
            target
                .strip_prefix(prefix)
                .is_some_and(|rest| rest.is_empty() || rest.starts_with("::"))
        })
        .map_or(ROOT, |&(_, logger)| logger)
}

fn allowed(level: Level, target: &str) -> bool {
    let logger = route(target);
    let mut most = LEVELS
        .get(logger)
        .map_or(OFF, |level| level.load(Ordering::Relaxed));
    if logger == ROOT && !target.starts_with("bostie_core") {
        most = most.min(WARN);
    }
    rank(level) <= most
}

fn deliver(level: Level, logger: &'static str, mut message: String) {
    let mut sinks = SINKS.lock().unwrap_or_else(PoisonError::into_inner);
    while let Some(sink) = sinks.last() {
        match sink.push(level, logger, message) {
            Ok(()) => return,
            Err(returned) => {
                message = returned;
                sinks.pop();
            }
        }
    }
}

struct Forward;

impl Subscriber for Forward {
    fn register_callsite(&self, metadata: &'static Metadata<'static>) -> Interest {
        if self.enabled(metadata) {
            Interest::always()
        } else {
            Interest::never()
        }
    }

    fn enabled(&self, metadata: &Metadata<'_>) -> bool {
        metadata.is_event() && allowed(*metadata.level(), metadata.target())
    }

    fn max_level_hint(&self) -> Option<LevelFilter> {
        let most = LEVELS
            .iter()
            .map(|level| level.load(Ordering::Relaxed))
            .max()
            .unwrap_or(OFF);
        Some(match most {
            OFF => LevelFilter::OFF,
            1 => LevelFilter::ERROR,
            WARN => LevelFilter::WARN,
            3 => LevelFilter::INFO,
            4 => LevelFilter::DEBUG,
            _ => LevelFilter::TRACE,
        })
    }

    fn new_span(&self, _: &Attributes<'_>) -> Id {
        Id::from_u64(1)
    }

    fn record(&self, _: &Id, _: &Record<'_>) {}

    fn record_follows_from(&self, _: &Id, _: &Id) {}

    fn event(&self, event: &Event<'_>) {
        if FORWARDING.with(|forwarding| forwarding.replace(true)) {
            return;
        }
        let metadata = event.metadata();
        let logger = LOGGERS
            .get(route(metadata.target()))
            .copied()
            .unwrap_or("bostie");
        deliver(*metadata.level(), logger, render(event));
        FORWARDING.with(|forwarding| forwarding.set(false));
    }

    fn enter(&self, _: &Id) {}

    fn exit(&self, _: &Id) {}
}

fn render(event: &Event<'_>) -> String {
    let mut text = Text::default();
    event.record(&mut text);
    if text.fields.is_empty() {
        return text.message;
    }
    if text.message.is_empty() {
        return text.fields;
    }
    text.message.push(' ');
    text.message.push_str(&text.fields);
    text.message
}

#[derive(Default)]
struct Text {
    message: String,
    fields: String,
}

impl Text {
    fn add(&mut self, field: &Field, value: fmt::Arguments<'_>) {
        if field.name() == "message" {
            self.message.push_str(&value.to_string());
            return;
        }
        if !self.fields.is_empty() {
            self.fields.push(' ');
        }
        self.fields.push_str(field.name());
        self.fields.push('=');
        self.fields.push_str(&value.to_string());
    }
}

impl Visit for Text {
    fn record_str(&mut self, field: &Field, value: &str) {
        self.add(field, format_args!("{value}"));
    }

    fn record_debug(&mut self, field: &Field, value: &dyn fmt::Debug) {
        self.add(field, format_args!("{value:?}"));
    }
}

#[cfg(test)]
mod tests {
    use std::num::NonZeroU16;

    use bostie_core::{Auth, Core, Delivery, HttpSettings};

    use super::*;

    #[test]
    fn each_target_follows_its_own_logger_level() {
        set_levels([10, 30, 50, 10]);

        assert!(allowed(Level::DEBUG, "bostie_core::voice::dave"));
        assert!(allowed(Level::DEBUG, "davey::session"));
        assert!(!allowed(Level::DEBUG, "bostie_core::gateway::shard"));
        assert!(allowed(Level::WARN, "bostie_core::gateway::shard"));
        assert!(!allowed(Level::ERROR, "bostie_core::rest"));
        assert!(allowed(Level::DEBUG, "bostie_core::bell"));
        assert!(!allowed(Level::INFO, "h2::proto"));
        assert!(allowed(Level::WARN, "h2::proto"));
        assert_eq!(Forward.max_level_hint(), Some(LevelFilter::DEBUG));
    }

    #[test]
    fn a_log_goes_to_the_newest_open_sink_and_falls_back_once_it_closes() {
        let core = || {
            Core::new(
                Auth::Bot("token"),
                "http://127.0.0.1:1/api/v10",
                NonZeroU16::MIN,
                HttpSettings::default(),
                None,
            )
            .unwrap()
        };
        let (older, _older_bell) = core();
        let (newer, _newer_bell) = core();
        register(older.log_sink());
        register(newer.log_sink());

        deliver(Level::WARN, "bostie", "first".to_owned());
        newer.close().unwrap();
        deliver(Level::WARN, "bostie", "second".to_owned());

        let sent = |core: &Core| -> Vec<String> {
            core.drain()
                .unwrap()
                .into_iter()
                .filter_map(|delivery| match delivery {
                    Delivery::Log { message, .. } if message == "first" || message == "second" => {
                        Some(message)
                    }
                    _ => None,
                })
                .collect()
        };
        assert_eq!(sent(&newer), ["first"]);
        assert_eq!(sent(&older), ["second"]);
    }

    #[test]
    fn fields_follow_the_message_as_key_value_pairs() {
        let dispatch = tracing::Dispatch::new(Capture::default());
        tracing::dispatcher::with_default(&dispatch, || {
            let guild = 42;
            tracing::warn!(%guild, reason = "stale", "skipping a frame");
            tracing::warn!(count = 3);
        });
        let capture = dispatch.downcast_ref::<Capture>().unwrap();

        assert_eq!(
            *capture.0.lock().unwrap(),
            ["skipping a frame guild=42 reason=stale", "count=3"]
        );
    }

    #[derive(Default)]
    struct Capture(Mutex<Vec<String>>);

    impl Subscriber for Capture {
        fn enabled(&self, _: &Metadata<'_>) -> bool {
            true
        }

        fn new_span(&self, _: &Attributes<'_>) -> Id {
            Id::from_u64(1)
        }

        fn record(&self, _: &Id, _: &Record<'_>) {}

        fn record_follows_from(&self, _: &Id, _: &Id) {}

        fn event(&self, event: &Event<'_>) {
            self.0.lock().unwrap().push(render(event));
        }

        fn enter(&self, _: &Id) {}

        fn exit(&self, _: &Id) {}
    }
}
