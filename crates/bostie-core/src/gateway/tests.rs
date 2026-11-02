use std::collections::VecDeque;
use std::net::SocketAddr;
use std::num::{NonZeroU16, NonZeroU32};
use std::pin::pin;
use std::sync::Arc;
use std::task::{Context, Wake, Waker};
use std::time::Duration;

use bytes::Bytes;
use futures_util::{SinkExt, StreamExt};
use sonic_rs::{JsonValueTrait, Value};
use tokio::net::{TcpSocket, TcpStream};
use tokio::runtime::Handle;
use tokio::sync::mpsc;
use tokio::time::{Instant, sleep, timeout, timeout_at};
use tokio_websockets::{CloseCode, Connector, Message, ServerBuilder, WebSocketStream};
use zstd_safe::CCtx;

use super::close::{CloseAction, classify};
use super::envelope::tests::fixture;
use super::inflate::tests::{compress, discord_compressor};
use super::limits::Limits;
use super::{Gateway, IdentifyLimit, SavedSession};
use crate::bell::{self, BellReceiver};
use crate::error::CoreError;
use crate::ids::{ChannelId, GuildId, ShardId, UserId};
use crate::inbox::{Delivery, Inbox};
use crate::rest::Rest;
use crate::rest::tests::{LIMITS as REST_LIMITS, NO_REPLY, Server, reply};
use crate::voice::tap::GatewayTap;

const TOKEN: &str = "test-token";
const SESSION_ID: &str = "70374e9c7263b9d5e16c4f418ff85dc1";
const IDENTIFY: &[u8] = br#"{"token": "test-token", "intents": 33281, "properties": {"os": "linux", "browser": "bostie", "device": "bostie"}}"#;
const WAIT: Duration = Duration::from_secs(5);
const NEVER: Duration = Duration::from_secs(3600);
const SLOW_HEARTBEAT: u64 = 41_250;
const QUIET_HEARTBEAT: u64 = 24 * 60 * 60 * 1000;
const INBOX_LIMIT: usize = 64 * 1024;
const LIMITS: Limits = Limits {
    connect_timeout: Duration::from_secs(2),
    hello_timeout: Duration::from_secs(1),
    close_timeout: Duration::from_millis(200),
    invalid_session_delay: Duration::from_millis(10)..=Duration::from_millis(30),
    backoff_base: Duration::from_millis(10),
    backoff_max: Duration::from_millis(100),
    identify_spacing: Duration::from_millis(20),
    max_decompressed: 1024 * 1024,
};
const GUILD: u64 = 1_335_172_792_320_131_073;
const BOT: u64 = 1_329_027_298_099_331_073;
const VOICE_CHANNEL: u64 = 1_335_173_295_636_611_074;

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

fn voice_server_update(seq: u64) -> String {
    format!(
        r#"{{"t":"VOICE_SERVER_UPDATE","s":{seq},"op":0,"d":{{"token":"voice-token","guild_id":"{GUILD}","endpoint":"voice.discord.media:443"}}}}"#
    )
}

fn voice_state_update(seq: u64) -> String {
    format!(
        r#"{{"t":"VOICE_STATE_UPDATE","s":{seq},"op":0,"d":{{"guild_id":"{GUILD}","user_id":"{BOT}","channel_id":"{VOICE_CHANNEL}","session_id":"voice-session","self_mute":false,"self_deaf":true,"mute":false,"suppress":false,"deaf":false}}}}"#
    )
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

fn address_of(socket: &TcpSocket) -> SocketAddr {
    socket.local_addr().unwrap()
}

struct FakeGateway {
    address: SocketAddr,
    connections: mpsc::UnboundedReceiver<TcpStream>,
    zstd: bool,
}

impl FakeGateway {
    fn start(zstd: bool) -> Self {
        let socket = TcpSocket::new_v4().unwrap();
        socket.set_recv_buffer_size(4096).unwrap();
        socket.bind(SocketAddr::from(([127, 0, 0, 1], 0))).unwrap();
        let listener = socket.listen(1024).unwrap();
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

    async fn identifies(&mut self) -> bool {
        loop {
            let message = timeout(WAIT, self.socket.next())
                .await
                .expect("nothing received")
                .expect("the client went away")
                .unwrap();
            if message.is_close() {
                return false;
            }
            if let Some(text) = message.as_text()
                && !text.starts_with(r#"{"op":1,"#)
            {
                assert_identify(&sonic_rs::from_str(text).unwrap());
                return true;
            }
        }
    }

    async fn acknowledge_heartbeats(mut self) {
        while let Some(Ok(message)) = self.socket.next().await {
            let heartbeat = message
                .as_text()
                .is_some_and(|text| text.starts_with(r#"{"op":1,"#));
            let ack = Message::text(fixture(HEARTBEAT_ACK).to_owned());
            if heartbeat && self.socket.send(ack).await.is_err() {
                return;
            }
        }
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
    gateway: Arc<Gateway>,
    tap: Arc<GatewayTap>,
    inbox: Arc<Inbox>,
    _rest: Arc<Rest>,
    _bell: BellReceiver,
    fake: FakeGateway,
    received: VecDeque<Delivery>,
    _refusing_api: Option<TcpSocket>,
}

impl Drop for Harness {
    fn drop(&mut self) {
        self.gateway.shutdown();
    }
}

impl Harness {
    fn new(zstd: bool) -> Self {
        Self::limited(zstd, LIMITS)
    }

    fn limited(zstd: bool, limits: Limits) -> Self {
        let refusing = crate::refusing_socket();
        let api_url = format!("http://{}/api/v10", address_of(&refusing));
        Self::with(FakeGateway::start(zstd), &api_url, limits, Some(refusing))
    }

    fn with(
        fake: FakeGateway,
        api_url: &str,
        limits: Limits,
        refusing_api: Option<TcpSocket>,
    ) -> Self {
        crate::init_test_tracing();
        let (bell, receiver) = bell::pair().unwrap();
        let inbox = Arc::new(Inbox::with_dispatch_limit(bell, INBOX_LIMIT));
        let global_limit = NonZeroU16::new(50).unwrap();
        let rest = Rest::with_limits(
            TOKEN,
            api_url,
            global_limit,
            Arc::clone(&inbox),
            REST_LIMITS,
        );
        let rest = Arc::new(rest.unwrap());
        let tap = Arc::new(GatewayTap::new());
        let gateway = Gateway::with_limits(
            Handle::current(),
            Arc::clone(&inbox),
            TOKEN,
            Arc::downgrade(&rest),
            Arc::clone(&tap),
            limits,
        )
        .unwrap();
        Self {
            gateway: Arc::new(gateway),
            tap,
            inbox,
            _rest: rest,
            _bell: receiver,
            fake,
            received: VecDeque::new(),
            _refusing_api: refusing_api,
        }
    }

    fn start(&self) {
        self.gateway
            .start(shard(), &self.fake.url(), IDENTIFY, None)
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

    async fn until_holding(&self) {
        let deadline = Instant::now() + WAIT;
        loop {
            let deliveries = self.inbox.drain();
            let holding = deliveries
                .iter()
                .any(|delivery| matches!(delivery, Delivery::Backpressure { .. }));
            for delivery in deliveries {
                self.inbox.push(delivery);
            }
            if holding {
                return;
            }
            assert!(Instant::now() < deadline, "the shard never held an event");
            sleep(Duration::from_millis(2)).await;
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

    async fn stopped(&mut self) -> (Option<u16>, String, Option<SavedSession>) {
        match self.delivery().await {
            Delivery::Stopped {
                code,
                reason,
                session,
                ..
            } => (code, reason, session),
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
    let mut harness = Harness::new(false);
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
    let mut harness = Harness::new(zstd);
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
    let mut harness = Harness::new(false);
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
async fn heartbeat_requests_get_the_last_seq_and_acks_set_latency() {
    let mut harness = Harness::new(false);
    harness.start();
    let mut peer = harness.fake.accept().await;
    peer.send(&hello(QUIET_HEARTBEAT)).await;
    assert_identify(&peer.receive().await);

    peer.send(fixture(HEARTBEAT_REQUEST)).await;
    assert!(peer.heartbeat().await["d"].is_null());
    peer.send(&harness.fake.ready()).await;
    harness.until_dispatch("READY").await;
    peer.send(fixture(HEARTBEAT_REQUEST)).await;
    assert_eq!(peer.heartbeat().await["d"].as_u64(), Some(1));

    assert_eq!(harness.gateway.latency(shard()).unwrap(), None);
    peer.send(fixture(HEARTBEAT_ACK)).await;
    let deadline = Instant::now() + WAIT;
    while harness.gateway.latency(shard()).unwrap().is_none() {
        assert!(Instant::now() < deadline, "the ACK never set the latency");
        sleep(Duration::from_millis(2)).await;
    }
}

#[tokio::test]
async fn frames_without_a_heartbeat_ack_still_make_a_zombie() {
    let mut harness = Harness::new(false);
    let mut peer = harness.ready(200).await;
    let request = || Message::text(fixture(HEARTBEAT_REQUEST).to_owned());

    let started = Instant::now();
    let zombie = loop {
        assert!(started.elapsed() < WAIT, "frames kept the connection alive");
        drop(peer.socket.send(request()).await);
        harness.received.extend(harness.inbox.drain());
        if let Some(delivery) = harness.received.pop_front() {
            break delivery;
        }
        sleep(Duration::from_millis(20)).await;
    };

    let Delivery::Disconnected {
        code,
        reason,
        resuming,
        ..
    } = zombie
    else {
        panic!("expected a disconnect, got {zombie:?}");
    };
    assert_eq!(
        (code, reason.as_str(), resuming),
        (None, "no heartbeat ACK (zombie connection)", true)
    );
}

#[tokio::test]
async fn a_ready_without_resume_gateway_url_identifies_after_a_disconnect() {
    let mut harness = Harness::new(false);
    harness.start();
    let mut peer = harness.fake.accept().await;
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    peer.receive().await;
    let ready = fixture(READY).replace(
        r#""resume_gateway_url":"ws://{address}/gateway-resume","#,
        "",
    );
    assert!(!ready.contains("resume_gateway_url"));
    peer.send(&ready).await;
    harness.dispatch().await;

    peer.close(4000).await;

    assert_eq!(
        harness.disconnected().await,
        (Some(4000), "unknown error".to_owned(), false)
    );
    let mut peer = harness.fake.accept().await;
    assert_eq!(peer.path, "/gateway");
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    assert_identify(&peer.receive().await);
}

#[tokio::test]
async fn a_refusing_base_url_falls_back_to_get_gateway() {
    let fake = FakeGateway::start(false);
    let gateway = format!(r#"{{"url":"{}"}}"#, fake.url());
    let mut api = Server::start(vec![NO_REPLY, reply("200 OK", &[], &gateway)]).await;
    let limits = Limits {
        connect_timeout: Duration::from_millis(500),
        ..LIMITS
    };
    let mut harness = Harness::with(fake, &api.url, limits, None);
    let closed = crate::refusing_socket();
    let refusing = format!("ws://{}", address_of(&closed));

    harness
        .gateway
        .start(shard(), &refusing, IDENTIFY, None)
        .unwrap();

    for _ in 0..4 {
        let (code, _, resuming) = harness.disconnected().await;
        assert_eq!((code, resuming), (None, false));
    }
    let mut peer = harness.fake.accept().await;
    assert_eq!(peer.path, "/gateway");
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    assert_identify(&peer.receive().await);
    for _ in 0..2 {
        let fetched = api.next().await;
        assert_eq!(fetched.path, "/api/v10/gateway");
        assert!(!fetched.headers.contains_key("authorization"));
    }
    assert_eq!(api.received(), 0);
    harness.received.extend(harness.inbox.drain());
    assert!(harness.received.is_empty(), "{:?}", harness.received);
}

#[tokio::test]
async fn a_failed_reconnect_resumes_on_the_base_url_without_refreshing_it() {
    let mut api = Server::start(Vec::new()).await;
    let mut harness = Harness::with(FakeGateway::start(false), &api.url, LIMITS, None);
    let closed = crate::refusing_socket();
    let dead = address_of(&closed).to_string();
    harness.start();

    let _without_hello = harness.fake.accept().await;
    assert_eq!(harness.disconnected().await.0, None);
    let mut peer = harness.fake.accept().await;
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    peer.receive().await;
    peer.send(&fixture(READY).replace("{address}", &dead)).await;
    harness.dispatch().await;
    peer.close(4000).await;
    assert_eq!(harness.disconnected().await.0, Some(4000));
    let (code, _, resuming) = harness.disconnected().await;
    assert_eq!((code, resuming), (None, true));
    let again_without_hello = harness.fake.accept().await;
    assert_eq!(again_without_hello.path, "/gateway");
    assert_eq!(
        harness.disconnected().await,
        (None, "no HELLO within 1s".to_owned(), true)
    );

    let mut peer = harness.fake.accept().await;
    assert_eq!(peer.path, "/gateway");
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    assert_resume(&peer.receive().await, 1);
    assert_eq!(api.received(), 0);
}

#[tokio::test]
async fn closing_during_a_get_gateway_that_never_answers_stops_at_once() {
    let mut api = Server::start(vec![NO_REPLY]).await;
    let limits = Limits {
        connect_timeout: NEVER,
        ..LIMITS
    };
    let mut harness = Harness::with(FakeGateway::start(false), &api.url, limits, None);
    let closed = crate::refusing_socket();
    let refusing = format!("ws://{}", address_of(&closed));
    harness
        .gateway
        .start(shard(), &refusing, IDENTIFY, None)
        .unwrap();
    for _ in 0..2 {
        harness.disconnected().await;
    }
    assert_eq!(api.next().await.path, "/api/v10/gateway");

    harness.gateway.close(shard()).unwrap();

    assert_eq!(harness.stopped().await, (None, "closed".to_owned(), None));
}

#[tokio::test]
async fn reconnect_and_invalid_session_requests_resume_or_identify() {
    let delay = Duration::from_millis(100);
    let limits = Limits {
        close_timeout: Duration::ZERO,
        invalid_session_delay: delay..=delay,
        ..LIMITS
    };
    let mut harness = Harness::limited(false, limits);
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
    assert!(invalidated.elapsed() >= delay);
    assert_eq!(peer.path, "/gateway");
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    assert_identify(&peer.receive().await);
}

#[tokio::test]
async fn frames_before_ready_reconnect_without_a_session() {
    for (frame, reason) in [
        (RECONNECT, "reconnect requested by Discord"),
        (INVALID_SESSION_RESUMABLE, "invalid session (resumable)"),
    ] {
        let limits = Limits {
            hello_timeout: NEVER,
            ..LIMITS
        };
        let mut harness = Harness::limited(false, limits);
        harness.start();
        let mut peer = harness.fake.accept().await;

        peer.send(fixture(frame)).await;

        assert_eq!(
            harness.disconnected().await,
            (None, reason.to_owned(), false)
        );
        let mut peer = harness.fake.accept().await;
        assert_eq!(peer.path, "/gateway");
        peer.send(&hello(SLOW_HEARTBEAT)).await;
        assert_identify(&peer.receive().await);
    }
}

#[tokio::test]
async fn close_codes_resume_reidentify_or_stop_as_the_table_says() {
    for code in [4000, 4007, 4004, 4999] {
        let (action, text) = classify(code);
        let mut harness = Harness::new(false);
        let mut peer = harness.ready(SLOW_HEARTBEAT).await;

        peer.close(code).await;

        if action == CloseAction::Fatal {
            assert_eq!(harness.stopped().await, (Some(code), text.to_owned(), None));
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
async fn a_dropped_connection_resumes_without_backing_off() {
    for zstd in [false, true] {
        let limits = Limits {
            backoff_base: NEVER,
            backoff_max: NEVER,
            ..LIMITS
        };
        let mut harness = Harness::limited(zstd, limits);
        let peer = harness.ready(SLOW_HEARTBEAT).await;

        drop(peer);

        let (code, _, resuming) = harness.disconnected().await;
        assert_eq!((code, resuming), (None, true), "zstd {zstd}");
        harness.resume(SLOW_HEARTBEAT, 1).await;
    }
}

#[tokio::test]
async fn refused_connections_back_off_longer_each_time() {
    let mut harness = Harness::new(false);
    harness.start();

    let mut arrivals = Vec::new();
    for _ in 0..6 {
        drop(harness.fake.connection().await);
        arrivals.push(Instant::now());
    }

    let gaps: Vec<Duration> = arrivals.windows(2).map(|pair| pair[1] - pair[0]).collect();
    assert!(gaps[4] >= LIMITS.backoff_base * 4, "{gaps:?}");
}

#[tokio::test]
async fn an_empty_identify_budget_opens_no_connection_until_the_reset() {
    let mut harness = Harness::new(false);
    let reset_after = Duration::from_millis(300);
    let seeded = Instant::now();
    harness.gateway.set_identify_limit(IdentifyLimit {
        remaining: 0,
        total: 1000,
        reset_after,
        max_concurrency: NonZeroU32::MIN,
    });

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
    assert!(seeded.elapsed() >= reset_after.saturating_sub(Duration::from_millis(5)));
}

#[tokio::test]
async fn shards_sharing_the_last_identify_send_one_and_wait_for_the_reset() {
    let mut harness = Harness::new(false);
    harness.gateway.set_identify_limit(IdentifyLimit {
        remaining: 1,
        total: 1000,
        reset_after: Duration::from_secs(3600),
        max_concurrency: NonZeroU32::MIN,
    });
    for id in 0..2 {
        harness
            .gateway
            .start(ShardId::from(id), &harness.fake.url(), IDENTIFY, None)
            .unwrap();
    }
    let mut peers = [harness.fake.accept().await, harness.fake.accept().await];

    for peer in &mut peers {
        peer.send(&hello(SLOW_HEARTBEAT)).await;
    }

    let mut identified = 0;
    for peer in &mut peers {
        identified += usize::from(peer.identifies().await);
    }
    assert_eq!(identified, 1);
    assert_eq!(
        harness.disconnected().await,
        (
            None,
            "daily identify limit reached; waiting 3600 s".to_owned(),
            false
        )
    );
}

#[tokio::test]
async fn a_gated_identify_waits_for_the_approval_of_its_own_request() {
    let mut harness = Harness::new(false);
    harness.gateway.gate_identifies();
    harness.start();
    let mut peer = harness.fake.accept().await;
    peer.send(&hello(SLOW_HEARTBEAT)).await;

    let delivery = harness.delivery().await;
    let Delivery::IdentifyRequest { request, .. } = delivery else {
        panic!("expected an identify request, got {delivery:?}");
    };
    harness
        .gateway
        .approve_identify(shard(), request - 1)
        .unwrap();
    peer.assert_quiet(Duration::from_millis(100)).await;
    harness.gateway.approve_identify(shard(), request).unwrap();

    assert_identify(&peer.receive().await);
}

#[tokio::test]
async fn a_ready_that_meets_a_full_inbox_still_starts_the_session() {
    let mut harness = Harness::new(false);
    harness.inbox.push(Delivery::Dispatch {
        shard: shard(),
        seq: 0,
        name: "MESSAGE_CREATE".into(),
        data: Bytes::from(vec![b'0'; INBOX_LIMIT]),
    });
    harness.start();
    let mut peer = harness.fake.accept().await;
    peer.send(&hello(100)).await;
    assert_identify(&peer.receive().await);
    peer.send(&harness.fake.ready()).await;

    drop(peer);

    harness.resume(SLOW_HEARTBEAT, 1).await;
}

#[tokio::test]
async fn a_stuck_write_ends_the_connection_without_holding_back_close() {
    let mut harness = Harness::new(false);
    harness.start();
    let mut peer = harness.fake.accept().await;
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    assert_identify(&peer.receive().await);
    let unsendable = "s".repeat(8 << 20);
    peer.send(&harness.fake.ready().replace(SESSION_ID, &unsendable))
        .await;
    harness.until_dispatch("READY").await;
    drop(peer);
    assert!(harness.disconnected().await.2);

    let mut unread = harness.fake.accept().await;
    unread.send(&hello(300)).await;
    assert_eq!(
        harness.disconnected().await,
        (None, "a write did not finish within 300ms".to_owned(), true)
    );

    let mut unread_again = harness.fake.accept().await;
    unread_again.send(&hello(SLOW_HEARTBEAT)).await;
    timeout(WAIT, unread_again.socket.get_ref().readable())
        .await
        .unwrap()
        .unwrap();
    harness.gateway.close(shard()).unwrap();
    assert_eq!(harness.stopped().await, (None, "closed".to_owned(), None));
}

#[tokio::test]
async fn a_pause_that_starts_after_an_unanswered_heartbeat_is_no_zombie() {
    let mut harness = Harness::new(false);
    let mut peer = harness.ready(500).await;
    peer.send(fixture(HEARTBEAT_ACK)).await;
    peer.heartbeat().await;
    peer.send(fixture(HEARTBEAT_ACK)).await;
    peer.heartbeat().await;

    for seq in 2..=14 {
        peer.send(&message(seq)).await;
    }
    peer.send(fixture(HEARTBEAT_ACK)).await;
    for _ in 0..3 {
        peer.heartbeat().await;
    }
    tokio::spawn(peer.acknowledge_heartbeats());

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
async fn a_slow_consumer_pauses_reading_without_losing_events() {
    let mut harness = Harness::new(false);
    let mut peer = harness.ready(150).await;

    for seq in 2..=14 {
        peer.send(&message(seq)).await;
    }
    harness.until_holding().await;
    for _ in 0..3 {
        peer.heartbeat().await;
    }
    tokio::spawn(peer.acknowledge_heartbeats());

    let first = harness.inbox.drain();
    let backpressure: Vec<_> = first
        .iter()
        .filter_map(|delivery| match delivery {
            Delivery::Backpressure { queued_bytes, .. } => Some(*queued_bytes),
            _ => None,
        })
        .collect();
    assert!(
        matches!(backpressure.as_slice(), [queued] if *queued >= INBOX_LIMIT),
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
    let mut harness = Harness::new(false);
    let mut peer = harness.ready(SLOW_HEARTBEAT).await;
    for seq in 2..=14 {
        peer.send(&message(seq)).await;
    }
    harness.until_holding().await;

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
    loop {
        match harness.delivery().await {
            Delivery::Dispatch { seq, name, .. } if &*name == "RESUMED" => {
                assert_eq!(seq, 15);
                break;
            }
            Delivery::Dispatch { seq, .. } => seqs.push(seq),
            Delivery::Backpressure { .. } => {}
            Delivery::Disconnected { reason, .. } => assert_eq!(reason, "reconnect requested"),
            other => panic!("unexpected {other:?}"),
        }
    }
    assert_eq!(seqs, (2..=14).collect::<Vec<_>>());
}

#[tokio::test]
async fn queued_commands_wait_for_ready_or_resumed() {
    let mut harness = Harness::new(false);
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
async fn a_reidentify_sends_the_latest_payload_and_keeps_later_commands() {
    let mut harness = Harness::new(false);
    let mut peer = harness.ready(SLOW_HEARTBEAT).await;
    let idle = br#"{"token":"test-token","intents":1,"presence":{"status":"idle"}}"#;

    peer.send(fixture(INVALID_SESSION)).await;
    harness.disconnected().await;
    harness.gateway.send(shard(), voice_state(1)).unwrap();
    let mut peer = harness.fake.accept().await;
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    assert_eq!(
        peer.receive_text().await.as_bytes(),
        [br#"{"op":2,"d":"#.as_slice(), IDENTIFY, b"}"].concat()
    );
    harness.gateway.set_identify(shard(), idle).unwrap();
    harness.gateway.send(shard(), voice_state(2)).unwrap();
    peer.send(&harness.fake.ready()).await;
    assert_eq!(peer.receive().await["d"]["guild_id"].as_str(), Some("2"));

    peer.send(fixture(INVALID_SESSION)).await;
    let mut peer = harness.fake.accept().await;
    peer.send(&hello(SLOW_HEARTBEAT)).await;
    assert_eq!(
        peer.receive_text().await.as_bytes(),
        [br#"{"op":2,"d":"#.as_slice(), idle, b"}"].concat()
    );
}

#[tokio::test]
async fn the_api_rejects_bad_input_and_full_queues() {
    let harness = Harness::new(false);
    let gateway = &harness.gateway;
    let command = || Bytes::from_static(br#"{"op":3,"d":{}}"#);

    assert!(matches!(
        gateway.send(shard(), command()),
        Err(CoreError::UnknownShard(_))
    ));
    assert!(matches!(
        gateway.set_identify(shard(), IDENTIFY),
        Err(CoreError::UnknownShard(_))
    ));
    assert!(matches!(
        gateway.start(shard(), "https://gateway.discord.gg", IDENTIFY, None),
        Err(CoreError::InvalidRequest(_))
    ));
    assert!(matches!(
        gateway.start(shard(), &harness.fake.url(), b"[]", None),
        Err(CoreError::InvalidRequest(_))
    ));
    let saved = SavedSession {
        id: SESSION_ID.to_owned(),
        seq: 1,
        resume_url: "https://gateway.discord.gg".to_owned(),
    };
    assert!(matches!(
        gateway.start(shard(), &harness.fake.url(), IDENTIFY, Some(saved)),
        Err(CoreError::InvalidRequest(_))
    ));
    let oversized = format!(r#"{{"token":"{}"}}"#, "x".repeat(4096));
    assert!(matches!(
        gateway.start(shard(), &harness.fake.url(), oversized.as_bytes(), None),
        Err(CoreError::InvalidRequest(_))
    ));
    harness.start();
    assert!(matches!(
        gateway.send(shard(), Bytes::from_static(br#"{"op":2,"d":{}}"#)),
        Err(CoreError::InvalidRequest(_))
    ));
    assert!(matches!(
        gateway.set_identify(shard(), b"[]"),
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
        gateway.start(shard(), &harness.fake.url(), IDENTIFY, None),
        Err(CoreError::ShardRunning(_))
    ));
}

struct DropWaitsForRelease {
    payload: &'static [u8],
    release: std::sync::mpsc::Receiver<()>,
}

impl AsRef<[u8]> for DropWaitsForRelease {
    fn as_ref(&self) -> &[u8] {
        self.payload
    }
}

impl Drop for DropWaitsForRelease {
    fn drop(&mut self) {
        tokio::task::block_in_place(|| self.release.recv()).unwrap_err();
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_shard_that_reported_its_stop_is_stopped_while_its_task_still_unwinds() {
    let mut harness = Harness::new(false);
    harness.start();
    let _peer = harness.fake.accept().await;
    let (release, held) = std::sync::mpsc::channel();
    let queued = Bytes::from_owner(DropWaitsForRelease {
        payload: br#"{"op":3,"d":{}}"#,
        release: held,
    });
    harness.gateway.send(shard(), queued).unwrap();

    harness.gateway.close(shard()).unwrap();
    assert_eq!(harness.stopped().await, (None, "closed".to_owned(), None));
    let latency = harness.gateway.latency(shard());
    let restarted = harness
        .gateway
        .start(shard(), &harness.fake.url(), IDENTIFY, None);
    drop(release);

    assert!(
        matches!(latency, Err(CoreError::UnknownShard(_))),
        "{latency:?}"
    );
    assert!(restarted.is_ok(), "{restarted:?}");
}

#[tokio::test]
async fn reconnect_resumes_and_close_stops() {
    let mut harness = Harness::new(false);
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
    assert_eq!(harness.stopped().await, (None, "closed".to_owned(), None));
    harness.start();
}

#[tokio::test]
async fn a_saved_session_resumes_and_a_suspend_saves_where_it_stopped() {
    let mut harness = Harness::new(false);
    let saved = SavedSession {
        id: SESSION_ID.to_owned(),
        seq: 3,
        resume_url: format!("ws://{}/gateway-resume", harness.fake.address),
    };
    harness
        .gateway
        .start(shard(), &harness.fake.url(), IDENTIFY, Some(saved.clone()))
        .unwrap();

    let mut peer = harness.resume(SLOW_HEARTBEAT, 3).await;
    peer.send(fixture(RESUMED)).await;
    let resumed = harness.until_dispatch("RESUMED").await;
    harness.gateway.suspend(shard()).unwrap();

    assert_eq!(peer.close_code().await, Some(4000));
    assert_eq!(
        harness.stopped().await,
        (
            None,
            "closed".to_owned(),
            Some(SavedSession {
                seq: resumed,
                ..saved
            })
        )
    );
}

#[tokio::test]
async fn invalid_frames_reconnect_instead_of_panicking() {
    let mut harness = Harness::new(false);
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

#[tokio::test]
async fn filtered_voice_updates_still_reach_the_voice_tap() {
    let mut harness = Harness::new(false);
    harness
        .gateway
        .set_filter(Some(vec!["MESSAGE_CREATE".to_owned()]));
    let mut slot = harness
        .tap
        .register(GuildId::from(GUILD), shard(), UserId::from(BOT));
    let mut peer = harness.ready(SLOW_HEARTBEAT).await;

    peer.send(&voice_server_update(2)).await;
    peer.send(&voice_state_update(3)).await;
    peer.send(&message(4)).await;

    let (seq, name, _) = harness.dispatch().await;
    assert_eq!((seq, &*name), (4, "MESSAGE_CREATE"));
    let snapshot = slot.borrow_and_update().clone();
    let server = snapshot.server.unwrap();
    assert_eq!(&*server.token, "voice-token");
    assert_eq!(server.endpoint.as_deref(), Some("voice.discord.media:443"));
    let state = snapshot.state.unwrap();
    assert_eq!(state.channel, Some(ChannelId::from(VOICE_CHANNEL)));
    assert!(state.self_deaf);
    assert_eq!((snapshot.server_seq, snapshot.state_seq), (1, 1));
}

#[tokio::test]
async fn voice_updates_the_filter_wants_reach_both_python_and_the_voice_tap() {
    let mut harness = Harness::new(false);
    let mut slot = harness
        .tap
        .register(GuildId::from(GUILD), shard(), UserId::from(BOT));
    let mut peer = harness.ready(SLOW_HEARTBEAT).await;

    peer.send(&voice_server_update(2)).await;

    let (seq, name, data) = harness.dispatch().await;
    assert_eq!((seq, &*name), (2, "VOICE_SERVER_UPDATE"));
    assert_eq!(data, raw_data(&voice_server_update(2)));
    assert_eq!(slot.borrow_and_update().server_seq, 1);
}

struct SendOnWake {
    gateway: Arc<Gateway>,
    sent: std::sync::Mutex<Option<Result<(), CoreError>>>,
}

impl Wake for SendOnWake {
    fn wake(self: Arc<Self>) {
        self.wake_by_ref();
    }

    fn wake_by_ref(self: &Arc<Self>) {
        let mut sent = self.sent.lock().unwrap();
        if sent.is_none() {
            *sent = Some(self.gateway.send(shard(), voice_state(5)));
        }
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_later_ready_reaches_the_voice_tap_after_stale_commands() {
    let mut harness = Harness::new(false);
    let mut peer = harness.ready(SLOW_HEARTBEAT).await;
    let mut slot = harness
        .tap
        .register(GuildId::from(GUILD), shard(), UserId::from(BOT));
    let first_session = slot.borrow().main_session;
    let on_wake = Arc::new(SendOnWake {
        gateway: Arc::clone(&harness.gateway),
        sent: std::sync::Mutex::new(None),
    });

    peer.send(fixture(INVALID_SESSION)).await;
    harness.disconnected().await;
    harness.gateway.send(shard(), voice_state(4)).unwrap();
    {
        let waker = Waker::from(Arc::clone(&on_wake));
        let mut changed = pin!(slot.changed());
        let polled = changed.as_mut().poll(&mut Context::from_waker(&waker));
        assert!(polled.is_pending());
        let mut peer = harness.fake.accept().await;
        peer.send(&hello(SLOW_HEARTBEAT)).await;
        assert_identify(&peer.receive().await);
        peer.send(&harness.fake.ready()).await;
        harness.until_dispatch("READY").await;

        on_wake.sent.lock().unwrap().take().unwrap().unwrap();
        assert_eq!(peer.receive().await["d"]["guild_id"].as_str(), Some("5"));
    }
    assert_eq!(slot.borrow().main_session, first_session + 1);
}
