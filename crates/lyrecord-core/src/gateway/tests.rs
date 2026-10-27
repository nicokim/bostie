use std::collections::VecDeque;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use bytes::Bytes;
use futures_util::{SinkExt, StreamExt};
use sonic_rs::{JsonValueTrait, Value};
use tokio::net::{TcpListener, TcpStream};
use tokio::runtime::Handle;
use tokio::sync::mpsc;
use tokio::time::{Instant, sleep, timeout, timeout_at};
use tokio_websockets::{CloseCode, Connector, Message, ServerBuilder, WebSocketStream};
use zstd_safe::CCtx;

use super::close::{CLOSE_CODES, CloseAction};
use super::envelope::tests::fixture;
use super::inflate::tests::{compress, discord_compressor};
use super::{Gateway, IdentifyLimit};
use crate::bell::{self, BellReceiver};
use crate::error::CoreError;
use crate::ids::ShardId;
use crate::inbox::{DISPATCH_BYTES_LIMIT, Delivery, Inbox};

const TOKEN: &str = "test-token";
const SESSION_ID: &str = "70374e9c7263b9d5e16c4f418ff85dc1";
const IDENTIFY: &[u8] = br#"{"token": "test-token", "intents": 33281, "properties": {"os": "linux", "browser": "lyrecord", "device": "lyrecord"}}"#;
const WAIT: Duration = Duration::from_secs(5);
const SLOW_HEARTBEAT: u64 = 41_250;

const HELLO: &str = r#""op":10"#;
const READY: &str = r#""t":"READY""#;
const MESSAGE_CREATE: &str = r#""t":"MESSAGE_CREATE""#;
const TYPING_START: &str = r#""t":"TYPING_START""#;
const RESUMED: &str = r#""t":"RESUMED""#;
const HEARTBEAT_ACK: &str = r#""op":11"#;
const HEARTBEAT_REQUEST: &str = r#""op":1,"#;
const RECONNECT: &str = r#""op":7"#;
const INVALID_SESSION_RESUMABLE: &str = r#""op":9,"d":true"#;
const INVALID_SESSION: &str = r#""op":9,"d":false"#;

fn shard() -> ShardId {
    ShardId::from(0)
}

fn hello(interval_ms: u64) -> String {
    fixture(HELLO).replace("41250", &interval_ms.to_string())
}

fn message(seq: u64) -> String {
    format!(
        r#"{{"t":"MESSAGE_CREATE","s":{seq},"op":0,"d":{{"content":"{}"}}}}"#,
        "m".repeat(8000)
    )
}

fn raw_data(frame: &str) -> Bytes {
    let (_, data) = frame.split_once(r#""op":0,"d":"#).unwrap();
    Bytes::copy_from_slice(data.strip_suffix('}').unwrap().as_bytes())
}

fn voice_state(guild: u64) -> Bytes {
    Bytes::from(format!(
        r#"{{"op":4,"d":{{"guild_id":"{guild}","channel_id":null,"self_mute":false,"self_deaf":false}}}}"#
    ))
}

fn assert_resume(payload: &Value, seq: u64) {
    assert_eq!(payload["op"].as_u64(), Some(6), "{payload:?}");
    assert_eq!(payload["d"]["token"].as_str(), Some(TOKEN));
    assert_eq!(payload["d"]["session_id"].as_str(), Some(SESSION_ID));
    assert_eq!(payload["d"]["seq"].as_u64(), Some(seq));
}

fn assert_identify(payload: &Value) {
    assert_eq!(payload["op"].as_u64(), Some(2), "{payload:?}");
}

async fn unused_address() -> SocketAddr {
    TcpListener::bind("127.0.0.1:0")
        .await
        .unwrap()
        .local_addr()
        .unwrap()
}

struct FakeGateway {
    address: SocketAddr,
    connections: mpsc::UnboundedReceiver<TcpStream>,
    zstd: bool,
}

impl FakeGateway {
    async fn start(zstd: bool) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let (sender, connections) = mpsc::unbounded_channel();
        tokio::spawn(async move {
            while let Ok((stream, _)) = listener.accept().await {
                if sender.send(stream).is_err() {
                    return;
                }
            }
        });
        Self {
            address,
            connections,
            zstd,
        }
    }

    fn url(&self) -> String {
        format!("ws://{}/gateway", self.address)
    }

    fn ready(&self) -> String {
        fixture(READY).replace("{address}", &self.address.to_string())
    }

    async fn connection(&mut self) -> TcpStream {
        timeout(WAIT, self.connections.recv())
            .await
            .expect("no connection arrived")
            .unwrap()
    }

    async fn accept(&mut self) -> Peer {
        let stream = self.connection().await;
        let (request, socket) = ServerBuilder::new().accept(stream).await.unwrap();
        Peer {
            path: request.uri().path().to_owned(),
            query: request.uri().query().map(str::to_owned),
            socket,
            compressor: self.zstd.then(discord_compressor),
        }
    }
}

struct Peer {
    path: String,
    query: Option<String>,
    socket: WebSocketStream<TcpStream>,
    compressor: Option<CCtx<'static>>,
}

impl Peer {
    async fn send(&mut self, frame: &str) {
        let message = match &mut self.compressor {
            Some(compressor) => Message::binary(compress(compressor, frame.as_bytes())),
            None => Message::text(frame.to_owned()),
        };
        self.socket.send(message).await.unwrap();
    }

    async fn close(&mut self, code: u16) {
        let frame = Message::close(Some(CloseCode::try_from(code).unwrap()), "");
        self.socket.send(frame).await.unwrap();
    }

    async fn next_text(&mut self, within: Duration) -> Option<String> {
        let deadline = Instant::now() + within;
        loop {
            let message = timeout_at(deadline, self.socket.next()).await.ok()?;
            let message = message.expect("the client went away").unwrap();
            assert!(
                !message.is_close(),
                "unexpected close {:?}",
                message.as_close()
            );
            if let Some(text) = message.as_text() {
                return Some(text.to_owned());
            }
        }
    }

    async fn heartbeat(&mut self) -> Value {
        loop {
            let text = self.next_text(WAIT).await.expect("no heartbeat");
            if text.starts_with(r#"{"op":1,"#) {
                return sonic_rs::from_str(&text).unwrap();
            }
        }
    }

    async fn receive_text(&mut self) -> String {
        loop {
            let text = self.next_text(WAIT).await.expect("nothing received");
            if !text.starts_with(r#"{"op":1,"#) {
                return text;
            }
        }
    }

    async fn receive(&mut self) -> Value {
        sonic_rs::from_str(&self.receive_text().await).unwrap()
    }

    async fn assert_quiet(&mut self, duration: Duration) {
        let until = Instant::now() + duration;
        while let Some(text) = self.next_text(until - Instant::now()).await {
            assert!(text.starts_with(r#"{"op":1,"#), "unexpected {text}");
        }
    }

    async fn close_code(&mut self) -> Option<u16> {
        loop {
            let message = timeout(WAIT, self.socket.next())
                .await
                .expect("no close frame");
            match message {
                Some(Ok(message)) if message.is_close() => {
                    return message.as_close().map(|(code, _)| code.into());
                }
                Some(Ok(_)) => {}
                _ => return None,
            }
        }
    }
}

struct Harness {
    gateway: Gateway,
    inbox: Arc<Inbox>,
    _bell: BellReceiver,
    fake: FakeGateway,
    received: VecDeque<Delivery>,
}

impl Drop for Harness {
    fn drop(&mut self) {
        self.gateway.shutdown();
    }
}

impl Harness {
    async fn new(zstd: bool) -> Self {
        crate::init_test_tracing();
        let (bell, receiver) = bell::pair().unwrap();
        let inbox = Arc::new(Inbox::new(bell));
        Self {
            gateway: Gateway::new(Handle::current(), Arc::clone(&inbox), TOKEN).unwrap(),
            inbox,
            _bell: receiver,
            fake: FakeGateway::start(zstd).await,
            received: VecDeque::new(),
        }
    }

    fn start(&self) {
        self.gateway
            .start(shard(), &self.fake.url(), Bytes::from_static(IDENTIFY))
            .unwrap();
    }

    async fn delivery(&mut self) -> Delivery {
        let deadline = Instant::now() + WAIT;
        loop {
            if let Some(delivery) = self.received.pop_front() {
                return delivery;
            }
            self.received.extend(self.inbox.drain());
            if self.received.is_empty() {
                assert!(Instant::now() < deadline, "no delivery arrived");
                sleep(Duration::from_millis(2)).await;
            }
        }
    }

    async fn dispatch(&mut self) -> (u64, Box<str>, Bytes) {
        match self.delivery().await {
            Delivery::Dispatch {
                seq, name, data, ..
            } => (seq, name, data),
            other => panic!("expected a dispatch, got {other:?}"),
        }
    }

    async fn until_dispatch(&mut self, wanted: &str) -> u64 {
        loop {
            if let Delivery::Dispatch { seq, name, .. } = self.delivery().await
                && &*name == wanted
            {
                return seq;
            }
        }
    }

    async fn disconnected(&mut self) -> (Option<u16>, String, bool) {
        match self.delivery().await {
            Delivery::Disconnected {
                code,
                reason,
                resuming,
                ..
            } => (code, reason, resuming),
            other => panic!("expected a disconnect, got {other:?}"),
        }
    }

    async fn stopped(&mut self) -> (Option<u16>, String) {
        match self.delivery().await {
            Delivery::Stopped { code, reason, .. } => (code, reason),
            other => panic!("expected a stop, got {other:?}"),
        }
    }

    async fn ready(&mut self, interval_ms: u64) -> Peer {
        self.start();
        let mut peer = self.fake.accept().await;
        peer.send(&hello(interval_ms)).await;
        assert_identify(&peer.receive().await);
        peer.send(&self.fake.ready()).await;
        let (seq, name, _) = self.dispatch().await;
        assert_eq!((seq, &*name), (1, "READY"));
        peer
    }

    async fn resume(&mut self, interval_ms: u64, seq: u64) -> Peer {
        let mut peer = self.fake.accept().await;
        assert_eq!(peer.path, "/gateway-resume");
        peer.send(&hello(interval_ms)).await;
        assert_resume(&peer.receive().await, seq);
        peer
    }
}

#[tokio::test]
async fn identify_wraps_the_python_payload_byte_for_byte() {
    let mut harness = Harness::new(false).await;
    harness.start();

    let mut peer = harness.fake.accept().await;
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    let identify = peer.receive_text().await;

    assert_eq!(peer.path, "/gateway");
    assert_eq!(
        peer.query.as_deref(),
        Some("v=10&encoding=json&compress=zstd-stream")
    );
    assert_eq!(
        identify.as_bytes(),
        [br#"{"op":2,"d":"#.as_slice(), IDENTIFY, b"}"].concat()
    );
}

async fn ready_and_message(zstd: bool) -> Vec<(u64, Box<str>, Bytes)> {
    let mut harness = Harness::new(zstd).await;
    harness.start();
    let mut peer = harness.fake.accept().await;
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    peer.receive().await;
    peer.send(fixture(READY)).await;
    peer.send(fixture(MESSAGE_CREATE)).await;
    vec![harness.dispatch().await, harness.dispatch().await]
}

#[tokio::test]
async fn dispatches_carry_the_exact_payload_over_text_and_zstd() {
    let expected = vec![
        (1, "READY".into(), raw_data(fixture(READY))),
        (
            2,
            "MESSAGE_CREATE".into(),
            raw_data(fixture(MESSAGE_CREATE)),
        ),
    ];

    assert_eq!(ready_and_message(false).await, expected);
    assert_eq!(ready_and_message(true).await, expected);
}

#[tokio::test]
async fn filtered_dispatches_are_not_delivered_but_count_for_resume() {
    let mut harness = Harness::new(false).await;
    harness
        .gateway
        .set_filter(Some(vec!["MESSAGE_CREATE".to_owned()]));
    let mut peer = harness.ready(SLOW_HEARTBEAT).await;

    peer.send(fixture(TYPING_START)).await;
    peer.close(4000).await;

    assert_eq!(
        harness.disconnected().await,
        (Some(4000), "unknown error".to_owned(), true)
    );
    harness.resume(SLOW_HEARTBEAT, 3).await;
}

#[tokio::test]
async fn heartbeats_follow_the_schedule_requests_and_acks() {
    let interval = Duration::from_millis(600);
    let mut harness = Harness::new(false).await;
    harness.start();
    let mut peer = harness.fake.accept().await;

    let hello_sent = Instant::now();
    peer.send(&hello(600)).await;
    let first = peer.heartbeat().await;
    assert!(hello_sent.elapsed() < interval + Duration::from_millis(50));
    assert!(first["d"].is_null());

    assert_eq!(harness.gateway.latency(shard()).unwrap(), None);
    peer.send(fixture(HEARTBEAT_ACK)).await;
    let deadline = Instant::now() + WAIT;
    while harness.gateway.latency(shard()).unwrap().is_none() {
        assert!(Instant::now() < deadline, "the ACK never set the latency");
        sleep(Duration::from_millis(2)).await;
    }

    let requested = Instant::now();
    peer.send(fixture(HEARTBEAT_REQUEST)).await;
    peer.heartbeat().await;
    assert!(requested.elapsed() < interval / 4);

    peer.heartbeat().await;
    sleep(interval * 3 / 4).await;
    peer.send(fixture(HEARTBEAT_REQUEST)).await;
    peer.heartbeat().await;
    peer.heartbeat().await;
    assert_eq!(peer.close_code().await, Some(4000));
}

#[tokio::test]
async fn a_silent_connection_is_closed_and_resumed() {
    let mut harness = Harness::new(true).await;
    let mut peer = harness.ready(200).await;

    assert_eq!(peer.close_code().await, Some(4000));
    assert_eq!(
        harness.disconnected().await,
        (
            None,
            "no heartbeat ACK (zombie connection)".to_owned(),
            true
        )
    );
    harness.resume(200, 1).await;
}

#[tokio::test]
async fn an_unreachable_resume_url_falls_back_to_the_base_url() {
    let mut harness = Harness::new(false).await;
    let dead = unused_address().await.to_string();
    harness.start();
    let mut peer = harness.fake.accept().await;
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    peer.receive().await;
    peer.send(&fixture(READY).replace("{address}", &dead)).await;
    harness.dispatch().await;

    peer.close(4000).await;
    assert_eq!(harness.disconnected().await.0, Some(4000));
    let (code, _, resuming) = harness.disconnected().await;
    assert_eq!((code, resuming), (None, true));

    let mut peer = harness.fake.accept().await;
    assert_eq!(peer.path, "/gateway");
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    assert_resume(&peer.receive().await, 1);

    peer.send(fixture(INVALID_SESSION)).await;
    assert_eq!(
        harness.disconnected().await,
        (None, "invalid session".to_owned(), false)
    );
    let mut peer = harness.fake.accept().await;
    assert_eq!(peer.path, "/gateway");
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    assert_identify(&peer.receive().await);
}

#[tokio::test]
async fn reconnect_and_invalid_session_requests_resume_or_identify() {
    let mut harness = Harness::new(false).await;
    let mut peer = harness.ready(SLOW_HEARTBEAT).await;

    peer.send(fixture(RECONNECT)).await;
    assert_eq!(
        harness.disconnected().await,
        (None, "reconnect requested by Discord".to_owned(), true)
    );
    let mut peer = harness.resume(SLOW_HEARTBEAT, 1).await;
    peer.send(fixture(RESUMED)).await;
    assert_eq!(harness.dispatch().await.0, 4);

    peer.send(fixture(INVALID_SESSION_RESUMABLE)).await;
    assert_eq!(
        harness.disconnected().await,
        (None, "invalid session (resumable)".to_owned(), true)
    );
    let mut peer = harness.resume(SLOW_HEARTBEAT, 4).await;

    let invalidated = Instant::now();
    peer.send(fixture(INVALID_SESSION)).await;
    assert_eq!(
        harness.disconnected().await,
        (None, "invalid session".to_owned(), false)
    );
    let mut peer = harness.fake.accept().await;
    assert!(invalidated.elapsed() >= Duration::from_millis(10));
    assert_eq!(peer.path, "/gateway");
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    assert_identify(&peer.receive().await);
}

#[tokio::test]
async fn reconnect_before_hello_does_not_wait_for_hello() {
    let mut harness = Harness::new(false).await;
    harness.start();
    let mut peer = harness.fake.accept().await;

    let requested = Instant::now();
    peer.send(fixture(RECONNECT)).await;

    assert_eq!(
        harness.disconnected().await,
        (None, "reconnect requested by Discord".to_owned(), false)
    );
    harness.fake.connection().await;
    assert!(requested.elapsed() < Duration::from_millis(500));
}

#[tokio::test]
async fn an_invalid_session_after_the_first_identify_identifies_again() {
    let mut harness = Harness::new(false).await;
    harness.start();
    let mut peer = harness.fake.accept().await;
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    assert_identify(&peer.receive().await);

    peer.send(fixture(INVALID_SESSION)).await;

    assert_eq!(
        harness.disconnected().await,
        (None, "invalid session".to_owned(), false)
    );
    let mut peer = harness.fake.accept().await;
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    assert_identify(&peer.receive().await);
}

#[tokio::test]
async fn close_codes_resume_reidentify_or_stop_as_the_table_says() {
    let rows =
        CLOSE_CODES
            .iter()
            .copied()
            .chain([(4999, CloseAction::Resume, "closed by Discord")]);
    for (code, action, text) in rows {
        let mut harness = Harness::new(false).await;
        let mut peer = harness.ready(SLOW_HEARTBEAT).await;

        peer.close(code).await;

        if action == CloseAction::Fatal {
            assert_eq!(harness.stopped().await, (Some(code), text.to_owned()));
            continue;
        }
        let resuming = action == CloseAction::Resume;
        assert_eq!(
            harness.disconnected().await,
            (Some(code), text.to_owned(), resuming)
        );
        if resuming {
            harness.resume(SLOW_HEARTBEAT, 1).await;
        } else {
            let mut peer = harness.fake.accept().await;
            assert_eq!(peer.path, "/gateway", "close code {code}");
            peer.send(&hello(SLOW_HEARTBEAT)).await;
            assert_identify(&peer.receive().await);
        }
    }
}

#[tokio::test]
async fn a_dropped_connection_resumes() {
    let mut harness = Harness::new(false).await;
    let peer = harness.ready(SLOW_HEARTBEAT).await;

    drop(peer);

    let (code, _, resuming) = harness.disconnected().await;
    assert_eq!((code, resuming), (None, true));
    harness.resume(SLOW_HEARTBEAT, 1).await;
}

#[tokio::test]
async fn refused_connections_back_off_until_ready() {
    let mut harness = Harness::new(false).await;
    harness.start();

    let mut arrivals = Vec::new();
    for _ in 0..7 {
        drop(harness.fake.connection().await);
        arrivals.push(Instant::now());
    }
    let gaps: Vec<Duration> = arrivals.windows(2).map(|pair| pair[1] - pair[0]).collect();
    assert!(gaps[0] < Duration::from_millis(30), "{gaps:?}");
    assert!(gaps[4] >= Duration::from_millis(40), "{gaps:?}");

    let mut peer = harness.fake.accept().await;
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    peer.receive().await;
    peer.send(&harness.fake.ready()).await;
    harness.until_dispatch("READY").await;
    drop(peer);

    let dropped = Instant::now();
    harness.fake.connection().await;
    assert!(dropped.elapsed() < Duration::from_millis(30));
}

#[tokio::test]
async fn an_empty_identify_budget_opens_no_connection_until_the_reset() {
    let mut harness = Harness::new(false).await;
    let reset_after = Duration::from_millis(300);
    harness.gateway.set_identify_limit(IdentifyLimit {
        remaining: 0,
        total: 1000,
        reset_after,
    });
    let seeded = Instant::now();

    harness.start();

    assert_eq!(
        harness.disconnected().await,
        (
            None,
            "daily identify limit reached; waiting 1 s".to_owned(),
            false
        )
    );
    harness.fake.connection().await;
    assert!(seeded.elapsed() >= reset_after - Duration::from_millis(5));
}

#[tokio::test]
async fn a_slow_consumer_pauses_reading_without_losing_events() {
    let mut harness = Harness::new(false).await;
    let mut peer = harness.ready(150).await;

    for seq in 2..=14 {
        peer.send(&message(seq)).await;
    }
    let paused_since = Instant::now();
    let mut heartbeats = 0;
    while paused_since.elapsed() < Duration::from_millis(700) {
        peer.heartbeat().await;
        heartbeats += 1;
    }
    assert!(heartbeats >= 3, "{heartbeats} heartbeats while paused");

    let first = harness.inbox.drain();
    let backpressure: Vec<_> = first
        .iter()
        .filter_map(|delivery| match delivery {
            Delivery::Backpressure { queued_bytes, .. } => Some(*queued_bytes),
            _ => None,
        })
        .collect();
    assert!(
        matches!(backpressure.as_slice(), [queued] if *queued >= DISPATCH_BYTES_LIMIT),
        "{backpressure:?}"
    );
    harness.received.extend(first);
    let mut seqs = Vec::new();
    while seqs.len() < 13 {
        match harness.delivery().await {
            Delivery::Dispatch { seq, .. } => seqs.push(seq),
            Delivery::Backpressure { .. } => {}
            other => panic!("unexpected {other:?}"),
        }
    }
    assert_eq!(seqs, (2..=14).collect::<Vec<_>>());
}

#[tokio::test]
async fn reconnecting_while_paused_delivers_the_held_event_once() {
    let mut harness = Harness::new(false).await;
    let mut peer = harness.ready(SLOW_HEARTBEAT).await;
    for seq in 2..=14 {
        peer.send(&message(seq)).await;
    }
    sleep(Duration::from_millis(300)).await;

    harness.gateway.reconnect(shard()).unwrap();

    assert_eq!(peer.close_code().await, Some(4000));
    let mut peer = harness.fake.accept().await;
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    let resume = peer.receive().await;
    let committed = resume["d"]["seq"].as_u64().unwrap();
    assert!(committed < 14, "nothing was held back");
    for seq in committed + 1..=14 {
        peer.send(&message(seq)).await;
    }
    peer.send(&fixture(RESUMED).replace(r#""s":4"#, r#""s":15"#))
        .await;

    let mut seqs = Vec::new();
    let mut backpressure = 0;
    loop {
        match harness.delivery().await {
            Delivery::Dispatch { seq, name, .. } if &*name == "RESUMED" => {
                assert_eq!(seq, 15);
                break;
            }
            Delivery::Dispatch { seq, .. } => seqs.push(seq),
            Delivery::Backpressure { .. } => backpressure += 1,
            Delivery::Disconnected { reason, .. } => assert_eq!(reason, "reconnect requested"),
            other => panic!("unexpected {other:?}"),
        }
    }
    assert!(backpressure >= 1);
    assert_eq!(seqs, (2..=14).collect::<Vec<_>>());
}

#[tokio::test]
async fn queued_commands_wait_for_ready_or_resumed() {
    let mut harness = Harness::new(false).await;
    harness.start();
    harness.gateway.send(shard(), voice_state(1)).unwrap();
    harness.gateway.send(shard(), voice_state(2)).unwrap();
    let mut peer = harness.fake.accept().await;
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    assert_identify(&peer.receive().await);
    peer.assert_quiet(Duration::from_millis(100)).await;

    peer.send(&harness.fake.ready()).await;
    assert_eq!(peer.receive().await["d"]["guild_id"].as_str(), Some("1"));
    assert_eq!(peer.receive().await["d"]["guild_id"].as_str(), Some("2"));

    drop(peer);
    harness.until_dispatch("READY").await;
    harness.disconnected().await;
    harness.gateway.send(shard(), voice_state(3)).unwrap();
    let mut peer = harness.resume(SLOW_HEARTBEAT, 1).await;
    peer.assert_quiet(Duration::from_millis(100)).await;
    peer.send(fixture(RESUMED)).await;
    assert_eq!(peer.receive().await["d"]["guild_id"].as_str(), Some("3"));
}

#[tokio::test]
async fn a_ready_that_replaces_a_session_drops_older_commands() {
    let mut harness = Harness::new(false).await;
    let mut peer = harness.ready(SLOW_HEARTBEAT).await;

    peer.send(fixture(INVALID_SESSION)).await;
    harness.disconnected().await;
    harness.gateway.send(shard(), voice_state(4)).unwrap();
    let mut peer = harness.fake.accept().await;
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    assert_identify(&peer.receive().await);
    peer.send(&harness.fake.ready()).await;
    harness.until_dispatch("READY").await;
    harness.gateway.send(shard(), voice_state(5)).unwrap();

    assert_eq!(peer.receive().await["d"]["guild_id"].as_str(), Some("5"));
}

#[tokio::test]
async fn the_api_rejects_bad_input_and_full_queues() {
    let harness = Harness::new(false).await;
    let gateway = &harness.gateway;
    let command = || Bytes::from_static(br#"{"op":3,"d":{}}"#);

    assert!(matches!(
        gateway.send(shard(), command()),
        Err(CoreError::UnknownShard(_))
    ));
    assert!(matches!(
        gateway.start(
            shard(),
            "https://gateway.discord.gg",
            Bytes::from_static(IDENTIFY)
        ),
        Err(CoreError::InvalidRequest(_))
    ));
    assert!(matches!(
        gateway.start(shard(), &harness.fake.url(), Bytes::from_static(b"[]")),
        Err(CoreError::InvalidRequest(_))
    ));
    harness.start();
    assert!(matches!(
        gateway.send(shard(), Bytes::from_static(br#"{"op":2,"d":{}}"#)),
        Err(CoreError::InvalidRequest(_))
    ));
    for _ in 0..128 {
        gateway.send(shard(), command()).unwrap();
    }
    assert!(matches!(
        gateway.send(shard(), command()),
        Err(CoreError::SendQueueFull(_))
    ));
    assert!(matches!(
        gateway.start(shard(), &harness.fake.url(), Bytes::from_static(IDENTIFY)),
        Err(CoreError::ShardRunning(_))
    ));
}

#[tokio::test]
async fn reconnect_resumes_and_close_stops() {
    let mut harness = Harness::new(false).await;
    let mut peer = harness.ready(SLOW_HEARTBEAT).await;

    harness.gateway.reconnect(shard()).unwrap();
    assert_eq!(peer.close_code().await, Some(4000));
    assert_eq!(
        harness.disconnected().await,
        (None, "reconnect requested".to_owned(), true)
    );
    let mut peer = harness.resume(SLOW_HEARTBEAT, 1).await;

    harness.gateway.close(shard()).unwrap();
    assert_eq!(peer.close_code().await, Some(1000));
    assert_eq!(harness.stopped().await, (None, "closed".to_owned()));
    let deadline = Instant::now() + WAIT;
    while harness.gateway.latency(shard()).is_ok() {
        assert!(Instant::now() < deadline, "the shard never finished");
        sleep(Duration::from_millis(2)).await;
    }
    harness.start();
}

#[tokio::test]
async fn invalid_frames_reconnect_instead_of_panicking() {
    let mut harness = Harness::new(false).await;
    let huge = format!(
        r#"{{"t":"X","s":1,"op":0,"d":"{}"}}"#,
        "h".repeat(1100 * 1024)
    );
    let zstd = |frame: &str| Message::binary(compress(&mut discord_compressor(), frame.as_bytes()));
    let frames = [
        zstd(&hello(0)),
        zstd(r#"{"t":null,"op":"#),
        zstd(&huge),
        Message::binary(&b"definitely not zstd"[..]),
    ];
    harness.start();

    for frame in frames {
        let mut peer = harness.fake.accept().await;
        peer.socket.send(frame).await.unwrap();

        assert_eq!(peer.close_code().await, Some(4000));
        let (code, reason, resuming) = harness.disconnected().await;
        assert!(reason.starts_with("invalid frame: "), "{reason}");
        assert_eq!((code, resuming), (None, false));
    }
    harness.fake.accept().await;
}

#[test]
fn the_tls_connector_builds_with_one_rustls_provider() {
    Connector::new().unwrap();
}
