use std::collections::HashSet;
use std::error::Error;
use std::hint::black_box;
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use criterion::{Criterion, Throughput};
use lyrecord_core::gateway::bench::{Inflater, inbox, parse};
use lyrecord_core::inbox::{Delivery, Inbox};
use parking_lot::RwLock;
use sonic_rs::Value;
use zstd_safe::zstd_sys::ZSTD_EndDirective;
use zstd_safe::{CCtx, CParameter, DCtx, InBuffer, OutBuffer};

const SESSION: &str = include_str!("../src/gateway/testdata/session.jsonl");
const GUILD_CREATE: &str = include_str!("../../../tests/fixtures/models/events/GUILD_CREATE.json");
const LARGE_GUILD: [(&str, usize); 3] = [("members", 5000), ("channels", 500), ("roles", 200)];
const BURST: u64 = 100;
const DEFAULT_BOT_EVENTS: [&str; 16] = [
    "CHANNEL_CREATE",
    "CHANNEL_DELETE",
    "CHANNEL_UPDATE",
    "GUILD_CREATE",
    "GUILD_DELETE",
    "GUILD_ROLE_CREATE",
    "GUILD_ROLE_DELETE",
    "GUILD_ROLE_UPDATE",
    "GUILD_UPDATE",
    "MESSAGE_CREATE",
    "RATE_LIMITED",
    "READY",
    "THREAD_CREATE",
    "THREAD_DELETE",
    "THREAD_LIST_SYNC",
    "THREAD_UPDATE",
];

type Filter = RwLock<Option<HashSet<Box<str>>>>;

struct Dispatch {
    name: &'static str,
    frame: String,
    burst: u64,
}

fn main() -> Result<(), Box<dyn Error>> {
    let dispatches = [
        recorded("TYPING_START")?,
        recorded("MESSAGE_CREATE")?,
        large_guild_create()?,
    ];
    let heartbeat_ack = recorded_line(r#""op":11"#)?;
    let mut criterion = Criterion::default()
        .sample_size(30)
        .warm_up_time(Duration::from_secs(1))
        .measurement_time(Duration::from_secs(3))
        .configure_from_args();

    let mut group = criterion.benchmark_group("envelope");
    for dispatch in &dispatches {
        for (order, frame) in [
            ("d_last", reorder(&dispatch.frame, false)?),
            ("d_first", reorder(&dispatch.frame, true)?),
        ] {
            for forwarded in [false, true] {
                let filter = filter(dispatch.name, forwarded);
                let found = parse(frame.as_bytes(), |name| wants(&filter, name))?;
                if found.is_some() != forwarded {
                    return Err(format!("{} {order} parsed unexpectedly", dispatch.name).into());
                }
                group.throughput(Throughput::Bytes(len(&frame)?));
                group.bench_function(
                    format!("{}/{order}/{}", dispatch.name, mode(forwarded)),
                    |bencher| {
                        bencher.iter(|| {
                            parse(black_box(frame.as_bytes()), |name| wants(&filter, name))
                        })
                    },
                );
            }
        }
    }
    group.finish();

    let mut group = criterion.benchmark_group("inflate");
    for (name, frame) in dispatches
        .iter()
        .map(|dispatch| (dispatch.name, dispatch.frame.as_str()))
        .chain([("HEARTBEAT_ACK", heartbeat_ack)])
    {
        let message = compressed(frame)?;
        let mut inflater = Inflater::new().ok_or("zstd could not allocate a context")?;
        if inflater.inflate(&message)? != frame.as_bytes() {
            return Err(format!("{name} does not inflate to its frame").into());
        }
        eprintln!(
            "{name}: {} bytes, {} compressed",
            frame.len(),
            message.len()
        );
        group.throughput(Throughput::Bytes(len(frame)?));
        group.bench_function(name, |bencher| {
            bencher.iter(|| inflater.inflate(black_box(&message)).map(<[u8]>::len))
        });
    }
    group.finish();

    let mut group = criterion.benchmark_group("hot_path");
    let cases = dispatches
        .iter()
        .flat_map(|dispatch| {
            [false, true].map(|forwarded| {
                (
                    format!("{}/{}", dispatch.name, mode(forwarded)),
                    dispatch.name,
                    dispatch.frame.as_str(),
                    dispatch.burst,
                    forwarded,
                )
            })
        })
        .chain([(
            "HEARTBEAT_ACK".to_owned(),
            "HEARTBEAT_ACK",
            heartbeat_ack,
            BURST,
            false,
        )]);
    for (id, name, frame, burst, forwarded) in cases {
        let message = compressed(frame)?;
        let filter = filter(name, forwarded);
        let mut inflater = Inflater::new().ok_or("zstd could not allocate a context")?;
        let (inbox, _bell) = inbox()?;
        if receive_burst(&mut inflater, &inbox, &message, burst, &filter)?.len()
            != forwarded_count(burst, forwarded)
        {
            return Err(format!("{id} was not {}", mode(forwarded)).into());
        }
        group.throughput(Throughput::Elements(burst));
        group.bench_function(id, |bencher| {
            bencher
                .iter(|| receive_burst(&mut inflater, &inbox, black_box(&message), burst, &filter))
        });
    }
    group.finish();

    let (sender, receiver) = mpsc::sync_channel::<Vec<Delivery>>(1);
    let python_thread = thread::spawn(move || receiver.into_iter().for_each(drop));
    let mut group = criterion.benchmark_group("hot_path_dropped_on_another_thread");
    for dispatch in &dispatches {
        let message = compressed(&dispatch.frame)?;
        let filter = filter(dispatch.name, true);
        let mut inflater = Inflater::new().ok_or("zstd could not allocate a context")?;
        let (inbox, _bell) = inbox()?;
        group.throughput(Throughput::Elements(dispatch.burst));
        group.bench_function(format!("{}/forwarded", dispatch.name), |bencher| {
            bencher.iter(|| {
                let deliveries = receive_burst(
                    &mut inflater,
                    &inbox,
                    black_box(&message),
                    dispatch.burst,
                    &filter,
                )?;
                sender.send(deliveries).map_err(Box::<dyn Error>::from)
            })
        });
    }
    group.finish();
    drop(sender);
    python_thread
        .join()
        .map_err(|_| "the dropping thread panicked")?;

    criterion.final_summary();
    Ok(())
}

fn receive_burst(
    inflater: &mut Inflater,
    inbox: &Inbox,
    message: &[u8],
    burst: u64,
    filter: &Filter,
) -> Result<Vec<Delivery>, Box<dyn Error>> {
    for _ in 0..burst {
        let frame = inflater.inflate(message)?;
        if let Some(delivery) = parse(frame, |name| wants(filter, name))? {
            inbox.push(delivery);
        }
    }
    Ok(inbox.drain())
}

fn wants(filter: &Filter, name: &str) -> bool {
    matches!(name, "READY" | "RESUMED")
        || filter
            .read()
            .as_ref()
            .is_none_or(|names| names.contains(name))
}

fn filter(name: &str, forwarded: bool) -> Filter {
    let names = DEFAULT_BOT_EVENTS
        .into_iter()
        .filter(|&event| event != name)
        .chain(forwarded.then_some(name))
        .map(Box::from)
        .collect();
    RwLock::new(Some(names))
}

fn forwarded_count(burst: u64, forwarded: bool) -> usize {
    if forwarded {
        usize::try_from(burst).unwrap_or(usize::MAX)
    } else {
        0
    }
}

fn mode(forwarded: bool) -> &'static str {
    if forwarded { "forwarded" } else { "filtered" }
}

fn len(frame: &str) -> Result<u64, Box<dyn Error>> {
    Ok(u64::try_from(frame.len())?)
}

fn recorded_line(needle: &str) -> Result<&'static str, Box<dyn Error>> {
    SESSION
        .lines()
        .find(|line| line.contains(needle))
        .ok_or_else(|| format!("no recorded frame contains {needle}").into())
}

fn recorded(name: &'static str) -> Result<Dispatch, Box<dyn Error>> {
    Ok(Dispatch {
        name,
        frame: recorded_line(&format!(r#""t":"{name}""#))?.to_owned(),
        burst: BURST,
    })
}

fn large_guild_create() -> Result<Dispatch, Box<dyn Error>> {
    let mut data = sonic_rs::to_string(&sonic_rs::from_str::<Value>(GUILD_CREATE)?)?;
    let mut rng = fastrand::Rng::with_seed(0x6c79);
    for (key, count) in LARGE_GUILD {
        let array = sonic_rs::get(&data, [key])?.as_raw_str().to_owned();
        let originals = sonic_rs::to_array_iter(array.as_str())
            .map(|item| item.map(|item| item.as_raw_str().to_owned()))
            .collect::<Result<Vec<_>, _>>()?;
        let mut copies = originals.clone();
        for item in originals
            .iter()
            .cycle()
            .take(count.saturating_sub(originals.len()))
        {
            copies.push(scrambled(item, &mut rng)?);
        }
        data = data.replacen(&array, &format!("[{}]", copies.join(",")), 1);
    }
    Ok(Dispatch {
        name: "GUILD_CREATE",
        frame: format!(r#"{{"t":"GUILD_CREATE","s":2,"op":0,"d":{data}}}"#),
        burst: 1,
    })
}

fn scrambled(json: &str, rng: &mut fastrand::Rng) -> Result<String, Box<dyn Error>> {
    let mut output = String::with_capacity(json.len());
    let mut rest = json;
    while let Some((before, string)) = rest.split_once('"') {
        let (text, after) = string_end(string)
            .and_then(|end| string.split_at_checked(end))
            .ok_or("unterminated string")?;
        let after = after.strip_prefix('"').ok_or("unterminated string")?;
        output.push_str(before);
        output.push('"');
        if after.starts_with(':') || text.contains('\\') {
            output.push_str(text);
        } else {
            output.extend(text.chars().map(|character| match character {
                '0'..='9' => rng.digit(10),
                'a'..='z' => rng.lowercase(),
                'A'..='Z' => rng.uppercase(),
                other => other,
            }));
        }
        output.push('"');
        rest = after;
    }
    output.push_str(rest);
    Ok(output)
}

fn string_end(string: &str) -> Option<usize> {
    let mut escaped = false;
    string.char_indices().find_map(|(index, character)| {
        let end = !escaped && character == '"';
        escaped = !escaped && character == '\\';
        end.then_some(index)
    })
}

fn reorder(frame: &str, data_first: bool) -> Result<String, Box<dyn Error>> {
    let field = |key: &str| sonic_rs::get(frame, [key]).map(|value| value.as_raw_str().to_owned());
    let (name, seq, op, data) = (field("t")?, field("s")?, field("op")?, field("d")?);
    Ok(if data_first {
        format!(r#"{{"d":{data},"op":{op},"s":{seq},"t":{name}}}"#)
    } else {
        format!(r#"{{"t":{name},"s":{seq},"op":{op},"d":{data}}}"#)
    })
}

fn compressed(frame: &str) -> Result<Vec<u8>, Box<dyn Error>> {
    let mut context = CCtx::try_create().ok_or("zstd could not allocate a context")?;
    for parameter in [
        CParameter::CompressionLevel(6),
        CParameter::WindowLog(18),
        CParameter::ChainLog(16),
        CParameter::HashLog(16),
    ] {
        context
            .set_parameter(parameter)
            .map_err(zstd_safe::get_error_name)?;
    }
    let mut message = Vec::with_capacity(zstd_safe::compress_bound(frame.len()));
    let mut input = InBuffer::around(frame.as_bytes());
    loop {
        let written = message.len();
        let mut output = OutBuffer::around_pos(&mut message, written);
        let remaining = context
            .compress_stream2(&mut output, &mut input, ZSTD_EndDirective::ZSTD_e_end)
            .map_err(zstd_safe::get_error_name)?;
        if remaining == 0 {
            return Ok(message);
        }
        message.reserve(DCtx::out_size());
    }
}
