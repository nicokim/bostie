use std::mem;
use std::net::SocketAddr;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use bytes::Bytes;
use futures_util::{SinkExt, StreamExt};
use parking_lot::Mutex as SyncMutex;
use sonic_rs::{JsonValueTrait, Value, json};
use tokio::net::TcpListener;
use tokio::time::{Instant, sleep, timeout};
use tokio_websockets::{ClientBuilder, CloseCode, Message, ServerBuilder};

use super::*;
use crate::bell::{self, BellReceiver};
use crate::voice::codec::FrameEncoder;
use crate::voice::connection::ConnectionTuning;
use crate::voice::crypto::Mode;
use crate::voice::dave::Recovery;
use crate::voice::dave::{Fault, MlsCall, Outbound, SendMode};
use crate::voice::proto::op;
use crate::voice::source::tests::fixture;
use crate::voice::tap::{VOICE_SERVER_UPDATE, VOICE_STATE_UPDATE};
use crate::voice::testkit::gateway::{ClientMessage, Config, FakeVoiceGateway, HelloAt, Outgoing};
use crate::voice::testkit::mls::{Call, Signal};
use crate::voice::testkit::sfu::{Packet, Sfu};
use crate::voice::types::{
    Application, DaveOp, FRAME, FRAME_SAMPLES, PcmFormat, ProposalsOp, ReceiveTuning, SampleFormat,
    SinkFormat, StallPolicy,
};
use crate::voice::ws::Timeouts;

const WAIT: Duration = Duration::from_secs(15);
const SHARD: u32 = 0;
const CHANNEL: u64 = 1_400_000_000_000_000_001;
const OTHER_CHANNEL: u64 = 1_400_000_000_000_000_002;
const SECOND_GUILD: u64 = 1_335_172_792_320_131_099;
const EARLY_PEER: u64 = 50;
const LATE_PEER: u64 = 2_000_000_000_000_000_001;
const THIRD_PEER: u64 = 2_000_000_000_000_000_002;
const HEARTBEAT: Duration = Duration::from_secs(30);
const QUICK_HEARTBEAT: Duration = Duration::from_millis(150);
const TIMEOUT: Duration = Duration::from_secs(30);
const AES: &str = "aead_aes256_gcm_rtpsize";
const XCHACHA: &str = "aead_xchacha20_poly1305_rtpsize";

fn tuning() -> Tuning {
    Tuning {
        connection: ConnectionTuning {
            move_window: Duration::from_millis(200),
            resume_backoff: Duration::from_millis(10),
            resume_backoff_max: Duration::from_millis(50),
            keepalive_interval: Duration::from_millis(100),
            early_ops: 8,
            speaking_coalesce: Duration::from_millis(20),
            discovery_interval: Duration::from_millis(50),
            stall_after: Duration::from_millis(100),
            ws: Timeouts {
                connect: Duration::from_secs(1),
                close: Duration::from_millis(200),
            },
            dave: Recovery {
                limit: 2,
                window: Duration::from_secs(1),
            },
            receive: ReceiveTuning::default(),
        },
        source_prefetch: Duration::from_millis(100),
        http_buffer: 4 * 1024,
    }
}

fn guild() -> GuildId {
    Config::default().server_id
}

fn bot() -> UserId {
    Config::default().user_id
}

fn user(id: u64) -> UserId {
    UserId::from(id)
}

fn encoder() -> EncoderSettings {
    EncoderSettings {
        bitrate: 64_000,
        application: Application::Audio,
        fec: true,
        packet_loss: 10,
        complexity: 10,
    }
}

fn pcm() -> SinkFormat {
    SinkFormat::Pcm(PcmFormat {
        rate: 48_000,
        channels: 2,
        sample: SampleFormat::S16Le,
    })
}

fn tone(frames: usize) -> Vec<u8> {
    (0..frames * 2)
        .flat_map(|index| {
            let sample = ((index / 2) as f32 * 0.05).sin() * 8_000.0;
            (sample as i16).to_le_bytes()
        })
        .collect()
}

fn opus(frames: usize) -> Vec<Bytes> {
    let format = PcmFormat {
        rate: 48_000,
        channels: 2,
        sample: SampleFormat::S16Le,
    };
    let packets = FrameEncoder::new(format, encoder())
        .unwrap()
        .encode(&tone(frames * FRAME_SAMPLES));
    assert_eq!(packets.len(), frames);
    packets
}

fn mode_of(config: &Config) -> Mode {
    if config.modes.iter().any(|mode| mode == AES) {
        Mode::Aes256GcmRtpSize
    } else {
        Mode::XChaCha20Poly1305RtpSize
    }
}

#[derive(Default)]
struct Recorder {
    sent: SyncMutex<Vec<Bytes>>,
    refusals: AtomicUsize,
}

impl Recorder {
    fn send(&self, payload: Bytes) -> Result<(), CoreError> {
        if self
            .refusals
            .try_update(Ordering::SeqCst, Ordering::SeqCst, |left| {
                left.checked_sub(1)
            })
            .is_ok()
        {
            return Err(CoreError::SendQueueFull(ShardId::from(SHARD)));
        }
        self.sent.lock().push(payload);
        Ok(())
    }

    fn voice_states(&self) -> Vec<Value> {
        self.sent
            .lock()
            .iter()
            .map(|payload| sonic_rs::from_slice::<Value>(payload).unwrap())
            .collect()
    }
}

struct Server {
    gateway: FakeVoiceGateway,
    sfu: Sfu,
    config: Config,
    messages: Vec<ClientMessage>,
    read: usize,
    forwarded: usize,
}

impl Server {
    async fn start(guild: GuildId, change: impl FnOnce(&mut Config)) -> Self {
        let sfu = Sfu::start();
        let mut config = Config {
            server_id: guild,
            udp: sfu.address(),
            heartbeat_interval: HEARTBEAT,
            dave_protocol_version: 0,
            ..Config::default()
        };
        change(&mut config);
        sfu.key(mode_of(&config), &config.secret_key);
        let gateway = FakeVoiceGateway::start(config.clone()).await;
        Self {
            gateway,
            sfu,
            config,
            messages: Vec::new(),
            read: 0,
            forwarded: 0,
        }
    }

    fn endpoint(&self) -> String {
        self.gateway.endpoint()
    }

    async fn collect(&mut self) {
        while let Ok(message) = timeout(Duration::from_millis(2), self.gateway.next()).await {
            self.messages.push(message);
        }
    }

    async fn expect(&mut self, wanted: impl Fn(&ClientMessage) -> bool) -> ClientMessage {
        let deadline = Instant::now() + WAIT;
        loop {
            if let Some(offset) = self.messages[self.read..].iter().position(&wanted) {
                let index = self.read + offset;
                self.read = index + 1;
                return self.messages[index].clone();
            }
            assert!(
                Instant::now() < deadline,
                "no matching client message; seen {:?}",
                self.messages
            );
            self.collect().await;
        }
    }

    async fn expect_op(&mut self, op: u8) -> Value {
        match self
            .expect(|message| message.op() == Some(u64::from(op)))
            .await
        {
            ClientMessage::Json { d, .. } => d,
            ClientMessage::Binary { payload, .. } => Value::from(payload.len() as u64),
            other => panic!("unexpected {other:?}"),
        }
    }

    async fn expect_close(&mut self) -> u16 {
        match self
            .expect(|message| matches!(message, ClientMessage::Close(_)))
            .await
        {
            ClientMessage::Close(code) => code,
            _ => unreachable!(),
        }
    }

    fn count(&self, op: u8) -> usize {
        self.messages
            .iter()
            .filter(|message| message.op() == Some(u64::from(op)))
            .count()
    }

    fn json(&self, op: u8) -> Vec<Value> {
        self.messages
            .iter()
            .filter_map(|message| match message {
                ClientMessage::Json { op: got, d } if *got == u64::from(op) => Some(d.clone()),
                _ => None,
            })
            .collect()
    }

    async fn token(&mut self, token: &str) {
        self.config.token = token.to_owned();
        let token = token.to_owned();
        self.gateway
            .configure(move |config| config.token = token)
            .await;
    }

    async fn rekey(&mut self, key: [u8; 32]) {
        self.config.secret_key = key;
        self.sfu.key(mode_of(&self.config), &key);
        self.gateway
            .configure(move |config| config.secret_key = key)
            .await;
    }

    fn send_signal(&self, signal: Signal) {
        let ids =
            |users: &[UserId]| -> Vec<String> { users.iter().map(ToString::to_string).collect() };
        match signal {
            Signal::ClientsConnect(users) => self
                .gateway
                .send(op::CLIENTS_CONNECT, json!({"user_ids": ids(&users)})),
            Signal::ClientDisconnect(user) => self
                .gateway
                .send(op::CLIENT_DISCONNECT, json!({"user_id": user.to_string()})),
            Signal::Dave(dave) => self.send_dave(dave),
        }
    }

    fn send_dave(&self, dave: DaveOp) {
        match dave {
            DaveOp::PrepareTransition {
                transition_id,
                protocol_version,
            } => self.gateway.send(
                op::DAVE_PREPARE_TRANSITION,
                json!({"transition_id": transition_id, "protocol_version": protocol_version}),
            ),
            DaveOp::ExecuteTransition { transition_id } => self.gateway.send(
                op::DAVE_EXECUTE_TRANSITION,
                json!({"transition_id": transition_id}),
            ),
            DaveOp::PrepareEpoch {
                transition_id,
                epoch,
                protocol_version,
            } => {
                let d = match transition_id {
                    Some(transition_id) => json!({
                        "transition_id": transition_id,
                        "epoch": epoch,
                        "protocol_version": protocol_version,
                    }),
                    None => json!({"epoch": epoch, "protocol_version": protocol_version}),
                };
                self.gateway.send(op::DAVE_PREPARE_EPOCH, d);
            }
            DaveOp::ExternalSender(bytes) => self
                .gateway
                .send_binary(op::DAVE_MLS_EXTERNAL_SENDER, &bytes),
            DaveOp::Proposals { operation, data } => {
                let operation = match operation {
                    ProposalsOp::Append => 0,
                    ProposalsOp::Revoke => 1,
                };
                self.gateway
                    .send_binary(op::DAVE_MLS_PROPOSALS, &[&[operation], &data[..]].concat());
            }
            DaveOp::AnnounceCommit {
                transition_id,
                commit,
            } => self.gateway.send_binary(
                op::DAVE_MLS_ANNOUNCE_COMMIT_TRANSITION,
                &[&transition_id.to_be_bytes()[..], &commit].concat(),
            ),
            DaveOp::Welcome {
                transition_id,
                welcome,
            } => self.gateway.send_binary(
                op::DAVE_MLS_WELCOME,
                &[&transition_id.to_be_bytes()[..], &welcome].concat(),
            ),
        }
    }

    fn dave_outbound(&self) -> Vec<Outbound> {
        self.messages.iter().filter_map(outbound).collect()
    }
}

fn outbound(message: &ClientMessage) -> Option<Outbound> {
    match message {
        ClientMessage::Binary { op, payload } if *op == op::DAVE_MLS_KEY_PACKAGE => {
            Some(Outbound::KeyPackage(payload.to_vec()))
        }
        ClientMessage::Binary { op, payload } if *op == op::DAVE_MLS_COMMIT_WELCOME => {
            Some(Outbound::CommitWelcome(payload.to_vec()))
        }
        ClientMessage::Json { op, d } if *op == u64::from(op::DAVE_TRANSITION_READY) => Some(
            Outbound::TransitionReady(u16::try_from(d["transition_id"].as_u64().unwrap()).unwrap()),
        ),
        ClientMessage::Json { op, d } if *op == u64::from(op::DAVE_MLS_INVALID_COMMIT_WELCOME) => {
            Some(Outbound::InvalidCommitWelcome(
                u16::try_from(d["transition_id"].as_u64().unwrap()).unwrap(),
            ))
        }
        _ => None,
    }
}

type State = (VoiceStatus, Option<DisconnectReason>, Option<u16>, String);

struct Harness {
    voice: Voice,
    inbox: Arc<Inbox>,
    _bell: BellReceiver,
    tap: Arc<GatewayTap>,
    recorder: Arc<Recorder>,
    server: Server,
    deliveries: Vec<Delivery>,
    used: Vec<bool>,
    tokens: u32,
}

impl Harness {
    async fn start() -> Self {
        Self::with(|_| {}).await
    }

    async fn with(change: impl FnOnce(&mut Config)) -> Self {
        Self::build(change, PacerPool::manual(), tuning()).await
    }

    async fn tuned(change: impl FnOnce(&mut ConnectionTuning)) -> Self {
        let mut tuning = tuning();
        change(&mut tuning.connection);
        Self::build(|_| {}, PacerPool::manual(), tuning).await
    }

    async fn build(change: impl FnOnce(&mut Config), pacer: PacerPool, tuning: Tuning) -> Self {
        crate::init_test_tracing();
        let (bell, receiver) = bell::pair().unwrap();
        let inbox = Arc::new(Inbox::new(bell));
        let tap = Arc::new(GatewayTap::new());
        let recorder = Arc::new(Recorder::default());
        let send: GatewaySend = {
            let recorder = Arc::clone(&recorder);
            Arc::new(move |_, payload| recorder.send(payload))
        };
        let voice = Voice::build(
            runtime::Handle::current(),
            Arc::clone(&inbox),
            Arc::clone(&tap),
            send,
            pacer,
            tuning,
        )
        .unwrap();
        Self {
            voice,
            inbox,
            _bell: receiver,
            tap,
            recorder,
            server: Server::start(guild(), change).await,
            deliveries: Vec::new(),
            used: Vec::new(),
            tokens: 0,
        }
    }

    fn request(&self) -> ConnectRequest {
        ConnectRequest {
            guild: guild(),
            channel: ChannelId::from(CHANNEL),
            shard: ShardId::from(SHARD),
            user: bot(),
            self_mute: false,
            self_deaf: false,
            timeout: TIMEOUT,
            server_change_delay: Duration::ZERO,
            encoder: encoder(),
            speaking: 1,
        }
    }

    fn connect(&self) {
        self.voice.connect(self.request()).unwrap();
    }

    fn offer_state(&self, guild: GuildId, channel: Option<u64>, extra: &str) {
        let channel = channel.map_or_else(|| "null".to_owned(), |id| format!(r#""{id}""#));
        let data = format!(
            r#"{{"guild_id":"{guild}","user_id":"{}","channel_id":{channel},"session_id":"{}","deaf":false{extra}}}"#,
            bot(),
            Config::default().session_id,
        );
        self.tap
            .offer(ShardId::from(SHARD), VOICE_STATE_UPDATE, data.as_bytes());
    }

    fn state(&self, channel: Option<u64>) {
        self.offer_state(guild(), channel, "");
    }

    fn offer_server(&self, guild: GuildId, token: &str, endpoint: Option<&str>) {
        let endpoint = endpoint.map_or_else(|| "null".to_owned(), |text| format!(r#""{text}""#));
        let data = format!(r#"{{"token":"{token}","guild_id":"{guild}","endpoint":{endpoint}}}"#);
        self.tap
            .offer(ShardId::from(SHARD), VOICE_SERVER_UPDATE, data.as_bytes());
    }

    fn next_token(&mut self) -> String {
        self.tokens += 1;
        format!("token-{}", self.tokens)
    }

    async fn fresh_server(&mut self) {
        let token = self.next_token();
        self.server.token(&token).await;
        let endpoint = self.server.endpoint();
        self.offer_server(guild(), &token, Some(&endpoint));
    }

    async fn fresh_server_at(&mut self, server: &mut Server) {
        let token = self.next_token();
        server.token(&token).await;
        self.offer_server(guild(), &token, Some(&server.endpoint()));
    }

    async fn announce(&mut self) {
        self.state(Some(CHANNEL));
        self.fresh_server().await;
    }

    async fn connected(&mut self) {
        self.connect();
        self.announce().await;
        self.until(VoiceStatus::Ready).await;
    }

    fn drain(&mut self) {
        let fresh = self.inbox.drain();
        self.used.extend(fresh.iter().map(|_| false));
        self.deliveries.extend(fresh);
    }

    async fn wait_for<T>(&mut self, pick: impl Fn(&Delivery) -> Option<T>) -> T {
        let deadline = Instant::now() + WAIT;
        loop {
            self.drain();
            let found = self.deliveries.iter().zip(&self.used).enumerate().find_map(
                |(index, (delivery, used))| {
                    if *used {
                        return None;
                    }
                    pick(delivery).map(|picked| (index, picked))
                },
            );
            if let Some((index, picked)) = found {
                self.used[index] = true;
                return picked;
            }
            assert!(
                Instant::now() < deadline,
                "no matching delivery; seen {:?}",
                self.deliveries
            );
            sleep(Duration::from_millis(2)).await;
        }
    }

    async fn status(&mut self) -> State {
        self.wait_for(|delivery| match delivery {
            Delivery::VoiceState {
                guild: got,
                status,
                reason,
                code,
                detail,
            } if *got == guild() => Some((*status, *reason, *code, detail.to_string())),
            _ => None,
        })
        .await
    }

    async fn until(&mut self, wanted: VoiceStatus) -> State {
        loop {
            let state = self.status().await;
            if state.0 == wanted {
                return state;
            }
            assert_ne!(
                state.0,
                VoiceStatus::Disconnected,
                "the connection ended: {state:?}"
            );
        }
    }

    async fn ended(&mut self) -> (DisconnectReason, Option<u16>, String) {
        loop {
            let (status, reason, code, detail) = self.status().await;
            if status == VoiceStatus::Disconnected {
                return (reason.unwrap(), code, detail);
            }
        }
    }

    fn statuses(&mut self) -> Vec<State> {
        self.drain();
        self.deliveries
            .iter()
            .filter_map(|delivery| match delivery {
                Delivery::VoiceState {
                    guild: got,
                    status,
                    reason,
                    code,
                    detail,
                } if *got == guild() => Some((*status, *reason, *code, detail.to_string())),
                _ => None,
            })
            .collect()
    }

    fn count(&mut self, wanted: impl Fn(&Delivery) -> bool) -> usize {
        self.drain();
        self.deliveries
            .iter()
            .filter(|delivery| wanted(delivery))
            .count()
    }

    async fn settle(&mut self, done: impl Fn(&mut Self) -> bool) {
        let deadline = Instant::now() + WAIT;
        while !done(self) {
            assert!(Instant::now() < deadline, "the condition never held");
            self.drain();
            sleep(Duration::from_millis(2)).await;
        }
    }

    fn tick(&self, n: u32) {
        self.voice.shared.pacer.tick(n);
    }

    fn media(&self) -> Arc<Media> {
        self.voice.media(guild()).unwrap()
    }

    fn gate(&self) -> SendMode {
        self.media().gate()
    }

    fn counter(&self, name: &str) -> u64 {
        counter(&self.voice, guild(), name)
    }

    fn sink(&self, format: SinkFormat, stall: StallPolicy) -> Arc<Sink> {
        self.voice
            .open_sink(
                guild(),
                SinkOptions {
                    format,
                    buffer: Duration::from_secs(10),
                    volume: 1.0,
                    stall,
                },
            )
            .unwrap()
    }

    fn joins(&self, channel: u64) -> usize {
        let wanted = channel.to_string();
        self.recorder
            .voice_states()
            .iter()
            .filter(|payload| payload["d"]["channel_id"].as_str() == Some(wanted.as_str()))
            .count()
    }

    fn leaves(&self) -> usize {
        self.recorder
            .voice_states()
            .iter()
            .filter(|payload| payload["d"]["channel_id"].is_null())
            .count()
    }

    async fn pump(&mut self, call: &mut Call, done: impl Fn(&mut Self, &mut Call) -> bool) {
        let deadline = Instant::now() + WAIT;
        loop {
            self.server.collect().await;
            let fresh: Vec<Outbound> = self.server.messages[self.server.forwarded..]
                .iter()
                .filter_map(outbound)
                .collect();
            self.server.forwarded = self.server.messages.len();
            for message in fresh {
                call.receive(bot(), message);
            }
            call.run();
            for (to, signal) in mem::take(&mut call.remote) {
                if to == bot() {
                    self.server.send_signal(signal);
                }
            }
            self.drain();
            if done(self, call) {
                return;
            }
            assert!(Instant::now() < deadline, "the DAVE call never settled");
            sleep(Duration::from_millis(3)).await;
        }
    }

    async fn join_call(&mut self, call: &mut Call) {
        self.server.expect_op(op::IDENTIFY).await;
        call.join_remote(bot());
        self.pump(call, |harness, _| harness.ready()).await;
    }

    fn ready(&mut self) -> bool {
        self.voice
            .shared
            .connections
            .lock()
            .get(&guild())
            .is_some_and(|handle| handle.info.status() == VoiceStatus::Ready)
    }

    async fn encrypting(&mut self, call: &mut Call) {
        self.pump(call, |harness, _| {
            harness.ready() && harness.gate() == SendMode::Encrypt
        })
        .await;
    }
}

fn counter(voice: &Voice, guild: GuildId, name: &str) -> u64 {
    voice
        .stats(guild)
        .unwrap()
        .into_iter()
        .find(|(key, _)| *key == name)
        .unwrap_or_else(|| panic!("no counter {name}"))
        .1
}

fn hear(call: &mut Call, peer: u64, payload: &[u8]) -> Option<Vec<u8>> {
    let mut frame = vec![0; payload.len()];
    let len = call
        .dave_mut(user(peer))
        .decrypt(bot(), payload, &mut frame)
        .ok()?;
    frame.truncate(len);
    Some(frame)
}

fn dave_call(channel: u64, peers: &[u64]) -> Call {
    let mut call = Call::new(ChannelId::from(channel), 1);
    for &peer in peers {
        call.join(user(peer));
        call.run();
    }
    call
}

struct Proxy {
    address: SocketAddr,
    refusals: Arc<AtomicUsize>,
    stalls: Arc<AtomicUsize>,
}

impl Proxy {
    async fn start(target: String) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let refusals = Arc::new(AtomicUsize::new(0));
        let stalls = Arc::new(AtomicUsize::new(0));
        let (refused, stalled) = (Arc::clone(&refusals), Arc::clone(&stalls));
        tokio::spawn(async move {
            while let Ok((stream, _)) = listener.accept().await {
                let take = |counter: &AtomicUsize| {
                    counter
                        .try_update(Ordering::SeqCst, Ordering::SeqCst, |left| {
                            left.checked_sub(1)
                        })
                        .is_ok()
                };
                let (stall, refuse) = (take(&stalled), take(&refused));
                let target = target.clone();
                tokio::spawn(async move {
                    if stall {
                        sleep(WAIT).await;
                        drop(stream);
                        return;
                    }
                    let Ok((_, mut client)) = ServerBuilder::new().accept(stream).await else {
                        return;
                    };
                    if refuse {
                        let close = Message::close(CloseCode::try_from(1000).ok(), "");
                        let _ = client.send(close).await;
                        while client.next().await.is_some() {}
                        return;
                    }
                    let url = format!("{target}/?v=8");
                    let Ok((mut upstream, _)) =
                        ClientBuilder::new().uri(&url).unwrap().connect().await
                    else {
                        return;
                    };
                    loop {
                        tokio::select! {
                            message = client.next() => match message {
                                Some(Ok(message)) => {
                                    if upstream.send(message).await.is_err() {
                                        return;
                                    }
                                }
                                _ => return,
                            },
                            message = upstream.next() => match message {
                                Some(Ok(message)) => {
                                    if client.send(message).await.is_err() {
                                        return;
                                    }
                                }
                                _ => return,
                            },
                        }
                    }
                });
            }
        });
        Self {
            address,
            refusals,
            stalls,
        }
    }

    fn endpoint(&self) -> String {
        format!("ws://{}", self.address)
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_plain_call_identifies_selects_aes_and_speaks_before_any_packet() {
    let mut harness = Harness::start().await;
    harness.connect();
    harness.announce().await;

    let identify = harness.server.expect_op(op::IDENTIFY).await;
    let select = harness.server.expect_op(op::SELECT_PROTOCOL).await;
    let speaking = harness.server.expect_op(op::SPEAKING).await;
    let statuses = [harness.status().await, harness.status().await];

    let join = &harness.recorder.voice_states()[0];
    assert_eq!(join["op"].as_u64(), Some(4));
    assert_eq!(
        join["d"]["guild_id"].as_str(),
        Some(guild().to_string().as_str())
    );
    assert_eq!(
        join["d"]["channel_id"].as_str(),
        Some(CHANNEL.to_string().as_str())
    );
    assert_eq!(
        identify["server_id"].as_str(),
        Some(guild().to_string().as_str())
    );
    assert_eq!(
        identify["user_id"].as_str(),
        Some(bot().to_string().as_str())
    );
    assert_eq!(
        identify["session_id"].as_str(),
        Some(Config::default().session_id.as_str())
    );
    assert_eq!(identify["token"].as_str(), Some("token-1"));
    assert_eq!(identify["max_dave_protocol_version"].as_u64(), Some(1));
    assert_eq!(select["data"]["mode"].as_str(), Some(AES));
    assert_eq!(speaking["speaking"].as_u64(), Some(1));
    assert_eq!(
        speaking["ssrc"].as_u64(),
        Some(u64::from(Config::default().ssrc))
    );
    assert_eq!(statuses[0].0, VoiceStatus::Connecting);
    assert_eq!(statuses[0].3, "voice_events");
    assert_eq!(statuses[1].0, VoiceStatus::Ready);
    assert!(harness.server.sfu.packets().is_empty());

    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES)).unwrap();
    harness.tick(1);
    let packets = harness.server.sfu.wait_packets(1, WAIT);
    assert_eq!(packets.len(), 1);
    assert_eq!(packets[0].header.ssrc, Config::default().ssrc);
    assert_eq!(harness.voice.dave(guild()).unwrap().protocol_version, 0);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn only_xchacha_offered_selects_xchacha_and_its_packets_decrypt() {
    let mut harness = Harness::with(|config| config.modes = vec![XCHACHA.to_owned()]).await;
    harness.connected().await;

    let select = harness.server.expect_op(op::SELECT_PROTOCOL).await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 2)).unwrap();
    harness.tick(2);

    assert_eq!(select["data"]["mode"].as_str(), Some(XCHACHA));
    assert_eq!(harness.server.sfu.wait_packets(2, WAIT).len(), 2);
    assert_eq!(harness.server.sfu.rejected(), 0);
    let diagnostics = harness.voice.diagnostics(guild()).unwrap();
    assert_eq!(diagnostics.mode, Some(XCHACHA));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn no_supported_mode_ends_with_a_protocol_error_naming_the_offer_and_leaves() {
    let mut harness =
        Harness::with(|config| config.modes = vec!["xsalsa20_poly1305_lite".to_owned()]).await;
    harness.connect();
    harness.announce().await;

    let (reason, _, detail) = harness.ended().await;

    assert_eq!(reason, DisconnectReason::ProtocolError);
    assert!(detail.contains("no supported encryption mode"), "{detail}");
    assert!(detail.contains("xsalsa20_poly1305_lite"), "{detail}");
    assert_eq!(harness.leaves(), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_endpoint_keeps_its_scheme_and_port_and_gets_version_8() {
    let mut harness = Harness::start().await;
    harness.connect();
    harness.state(Some(CHANNEL));
    harness.server.token("token").await;
    let endpoint = format!("{}/?v=4", harness.server.endpoint());
    harness.offer_server(guild(), "token", Some(&endpoint));

    let connected = harness
        .server
        .expect(|message| matches!(message, ClientMessage::Connected { .. }))
        .await;
    harness.until(VoiceStatus::Ready).await;

    assert_eq!(
        connected,
        ClientMessage::Connected {
            query: Some("v=8".to_owned())
        }
    );
    let diagnostics = harness.voice.diagnostics(guild()).unwrap();
    assert_eq!(diagnostics.endpoint_host.as_deref(), Some("127.0.0.1"));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_endpoint_without_a_scheme_is_dialed_over_tls_on_its_own_port() {
    let harness = Harness::start().await;
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = listener.local_addr().unwrap().to_string();
    harness.connect();
    harness.state(Some(CHANNEL));
    harness.offer_server(guild(), "token", Some(&endpoint));

    let (stream, _) = timeout(WAIT, listener.accept()).await.unwrap().unwrap();
    let mut first = [0; 1];
    loop {
        timeout(WAIT, stream.readable()).await.unwrap().unwrap();
        match stream.try_read(&mut first) {
            Ok(_) => break,
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {}
            Err(error) => panic!("{error}"),
        }
    }

    assert_eq!(first[0], 0x16);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn ip_discovery_retries_until_an_answer_arrives() {
    let mut harness = Harness::start().await;
    harness.server.sfu.ignore_discoveries(2);

    harness.connected().await;

    assert!(harness.server.sfu.discoveries() >= 3);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn unanswered_ip_discovery_times_out_naming_it() {
    let mut harness = Harness::start().await;
    harness.server.sfu.ignore_discoveries(usize::MAX);
    harness
        .voice
        .connect(ConnectRequest {
            timeout: Duration::from_secs(1),
            ..harness.request()
        })
        .unwrap();
    harness.announce().await;

    let (reason, _, detail) = harness.ended().await;

    assert_eq!(reason, DisconnectReason::Timeout);
    assert!(detail.contains("ip discovery: no answer from"), "{detail}");
    assert!(harness.leaves() >= 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn hello_after_ready_still_completes_the_handshake() {
    let mut harness = Harness::with(|config| config.hello = HelloAt::AfterReady).await;

    harness.connected().await;
    harness.server.expect_op(op::IDENTIFY).await;

    assert_eq!(harness.server.count(op::IDENTIFY), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_dropped_socket_resumes_with_the_last_seq_and_keeps_sending() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 20)).unwrap();
    harness.tick(2);
    harness
        .server
        .gateway
        .send_binary(op::DAVE_MLS_EXTERNAL_SENDER, b"cached");
    harness.server.gateway.send(
        op::SPEAKING,
        json!({"user_id": "7", "ssrc": 70, "speaking": 1}),
    );
    harness
        .wait_for(|delivery| {
            matches!(delivery, Delivery::VoiceSpeaking { ssrc: 70, .. }).then_some(())
        })
        .await;

    harness.server.gateway.drop_connection();
    harness.server.gateway.send(
        op::SPEAKING,
        json!({"user_id": "8", "ssrc": 80, "speaking": 1}),
    );
    harness.server.gateway.send(
        op::DAVE_PREPARE_TRANSITION,
        json!({"transition_id": 6, "protocol_version": 0}),
    );
    harness.until(VoiceStatus::Resuming).await;
    harness.tick(3);
    let resume = harness.server.expect_op(op::RESUME).await;
    harness.until(VoiceStatus::Ready).await;
    harness
        .wait_for(|delivery| {
            matches!(delivery, Delivery::VoiceSpeaking { ssrc: 80, .. }).then_some(())
        })
        .await;
    let ready = harness.server.expect_op(op::DAVE_TRANSITION_READY).await;
    harness.tick(3);

    assert_eq!(resume["seq_ack"].as_u64(), Some(4));
    assert_eq!(ready["transition_id"].as_u64(), Some(6));
    assert_eq!(harness.server.count(op::IDENTIFY), 1);
    assert_eq!(harness.server.sfu.wait_packets(8, WAIT).len(), 8);
    assert_eq!(harness.counter("held_ticks"), 0);
    assert_eq!(harness.counter("resumes"), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_crashed_voice_server_close_resumes() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    harness.server.gateway.close(4015);

    let (_, _, _, detail) = harness.until(VoiceStatus::Resuming).await;
    harness.server.expect_op(op::RESUME).await;
    harness.until(VoiceStatus::Ready).await;
    assert!(detail.contains("4015"), "{detail}");
    assert_eq!(harness.server.count(op::IDENTIFY), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_missed_heartbeat_ack_resumes_as_a_zombie() {
    let mut harness = Harness::with(|config| config.heartbeat_interval = QUICK_HEARTBEAT).await;
    harness.connected().await;

    harness.server.gateway.hold_acks().await;
    harness
        .server
        .gateway
        .configure(|config| config.heartbeat_interval = HEARTBEAT)
        .await;

    let (_, _, _, detail) = harness.until(VoiceStatus::Resuming).await;
    harness.server.expect_op(op::RESUME).await;
    harness.until(VoiceStatus::Ready).await;
    assert!(detail.contains("heartbeat"), "{detail}");
}

async fn closed_with(code: u16) -> Harness {
    let mut harness = Harness::start().await;
    harness.connected().await;
    harness.server.gateway.close(code);
    harness
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn close_codes_bostie_caused_end_the_connection_without_retrying() {
    for (code, reason) in [
        (4001, DisconnectReason::ProtocolError),
        (4002, DisconnectReason::ProtocolError),
        (4003, DisconnectReason::ProtocolError),
        (4005, DisconnectReason::ProtocolError),
        (4012, DisconnectReason::ProtocolError),
        (4016, DisconnectReason::ProtocolError),
        (4017, DisconnectReason::DaveRequired),
        (4020, DisconnectReason::ProtocolError),
        (4021, DisconnectReason::RateLimited),
    ] {
        let mut harness = closed_with(code).await;

        assert_eq!(harness.ended().await.0, reason, "{code}");
        let statuses = harness.statuses();
        let steps: Vec<VoiceStatus> = statuses.iter().map(|state| state.0).collect();
        assert_eq!(
            steps,
            [
                VoiceStatus::Connecting,
                VoiceStatus::Ready,
                VoiceStatus::Disconnected
            ],
            "{code}"
        );
        assert_eq!(statuses.last().unwrap().2, Some(code), "{code}");
        assert_eq!(harness.joins(CHANNEL), 1, "{code}");
        assert_eq!(harness.leaves(), 1, "{code}");
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn close_codes_of_a_dead_session_rejoin_with_a_new_voice_state() {
    for code in [4004, 4006, 4009, 4011, 4099] {
        let mut harness = closed_with(code).await;

        let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
        assert_eq!(detail, "rejoining", "{code}");
        harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
        harness.fresh_server().await;
        harness.until(VoiceStatus::Ready).await;
        assert_eq!(harness.server.count(op::RESUME), 0, "{code}");
        assert_eq!(harness.counter("rejoins"), 1, "{code}");
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn transport_close_codes_resume() {
    for code in [1000, 1001, 3000, 4015] {
        let mut harness = closed_with(code).await;

        harness.until(VoiceStatus::Resuming).await;
        harness.server.expect_op(op::RESUME).await;
        harness.until(VoiceStatus::Ready).await;
        assert_eq!(harness.server.count(op::IDENTIFY), 1, "{code}");
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn disconnected_and_call_terminated_wait_for_the_gateway_then_end() {
    for (code, reason, leaves) in [
        (4014, DisconnectReason::Removed, 0),
        (4022, DisconnectReason::CallTerminated, 1),
    ] {
        let mut harness = closed_with(code).await;

        let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
        let (ended, ended_code, _) = harness.ended().await;

        assert_eq!(detail, "waiting_for_gateway", "{code}");
        assert_eq!((ended, ended_code), (reason, Some(code)));
        assert_eq!(harness.leaves(), leaves, "{code}");
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn repeated_invalid_sessions_end_after_three_rejoins() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    harness.server.expect_op(op::IDENTIFY).await;

    for rejoin in 0..4 {
        harness.server.gateway.close(4006);
        if rejoin == 3 {
            break;
        }
        harness
            .settle(|harness| harness.joins(CHANNEL) == rejoin + 2)
            .await;
        harness.fresh_server().await;
        harness
            .server
            .expect(|message| message.op() == Some(u64::from(op::IDENTIFY)))
            .await;
    }
    let (reason, code, _) = harness.ended().await;

    assert_eq!(
        (reason, code),
        (DisconnectReason::SessionInvalid, Some(4006))
    );
    assert_eq!(harness.joins(CHANNEL), 4);
    assert_eq!(harness.leaves(), 1);
}

fn moved(delivery: &Delivery) -> Option<(u64, u64, bool)> {
    match delivery {
        Delivery::VoiceMoved {
            from, to, by_us, ..
        } => Some((u64::from(*from), u64::from(*to), *by_us)),
        _ => None,
    }
}

fn speaking_ssrc(delivery: &Delivery) -> Option<u32> {
    match delivery {
        Delivery::VoiceSpeaking { ssrc, .. } => Some(*ssrc),
        _ => None,
    }
}

fn queued_frames(sink: &Sink) -> u64 {
    sink.queued().unwrap().as_micros() as u64 * 48 / 1000
}

fn sink_stat(sink: &Sink, name: &str) -> u64 {
    sink.stats()
        .unwrap()
        .into_iter()
        .find(|(key, _)| *key == name)
        .unwrap()
        .1
}

impl Harness {
    async fn speaking_from(&mut self, ssrc: u32) {
        self.wait_for(|delivery| (speaking_ssrc(delivery) == Some(ssrc)).then_some(()))
            .await;
    }

    async fn via_proxy(&mut self) -> Proxy {
        let proxy = Proxy::start(self.server.endpoint()).await;
        self.connect();
        self.state(Some(CHANNEL));
        let token = self.next_token();
        self.server.token(&token).await;
        self.offer_server(guild(), &token, Some(&proxy.endpoint()));
        self.until(VoiceStatus::Ready).await;
        proxy
    }

    async fn second_guild(&mut self) -> Server {
        let second = GuildId::from(SECOND_GUILD);
        let mut server = Server::start(second, |_| {}).await;
        self.voice
            .connect(ConnectRequest {
                guild: second,
                ..self.request()
            })
            .unwrap();
        self.offer_state(second, Some(CHANNEL), "");
        server.token("second").await;
        self.offer_server(second, "second", Some(&server.endpoint()));
        self.wait_for(|delivery| {
            matches!(
                delivery,
                Delivery::VoiceState {
                    guild: got,
                    status: VoiceStatus::Ready,
                    ..
                } if *got == second
            )
            .then_some(())
        })
        .await;
        server
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_move_by_someone_else_reconnects_in_the_new_channel_and_keeps_the_sinks() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 10)).unwrap();

    harness.state(Some(OTHER_CHANNEL));
    let moved = harness.wait_for(moved).await;
    let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;
    harness.tick(2);
    harness.server.collect().await;

    assert_eq!(moved, (CHANNEL, OTHER_CHANNEL, false));
    assert_eq!(detail, "moving");
    assert_eq!(harness.server.count(op::IDENTIFY), 2);
    assert_eq!(harness.server.count(op::RESUME), 0);
    assert!(!sink.is_closed());
    assert_eq!(harness.server.sfu.wait_packets(2, WAIT).len(), 2);
    assert_eq!(
        harness.voice.diagnostics(guild()).unwrap().channel,
        ChannelId::from(OTHER_CHANNEL)
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_move_announced_server_first_keeps_the_attempt_and_joins_the_new_channel_group() {
    let mut harness = Harness::with(|config| config.dave_protocol_version = 1).await;
    harness.connected().await;
    harness.server.expect_op(op::IDENTIFY).await;
    harness.server.sfu.ignore_discoveries(1);

    harness.fresh_server().await;
    harness.server.expect_op(op::IDENTIFY).await;
    harness.state(Some(OTHER_CHANNEL));
    let mut call = dave_call(OTHER_CHANNEL, &[LATE_PEER]);
    call.join_remote(bot());
    harness.encrypting(&mut call).await;
    let sink = harness.sink(SinkFormat::Opus, StallPolicy::Hold);
    let sent = opus(1);
    sink.write(&sent[0]).unwrap();
    harness.tick(1);

    assert_eq!(
        harness.wait_for(moved).await,
        (CHANNEL, OTHER_CHANNEL, false)
    );
    assert_eq!(harness.server.count(op::IDENTIFY), 2);
    let packets = harness.server.sfu.wait_packets(1, WAIT);
    assert_eq!(
        hear(&mut call, LATE_PEER, &packets[0].payload),
        Some(sent[0].to_vec())
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_disconnect_close_then_a_null_channel_is_a_removal_without_a_leave() {
    let mut harness = closed_with(4014).await;
    harness.until(VoiceStatus::Reconnecting).await;

    harness.state(None);
    let (reason, _, _) = harness.ended().await;

    assert_eq!(reason, DisconnectReason::Removed);
    assert_eq!(harness.leaves(), 0);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_disconnect_close_then_a_new_channel_is_a_move() {
    let mut harness = closed_with(4014).await;
    harness.until(VoiceStatus::Reconnecting).await;

    harness.state(Some(OTHER_CHANNEL));
    let moved = harness.wait_for(moved).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;

    assert_eq!(moved, (CHANNEL, OTHER_CHANNEL, false));
    harness.server.collect().await;
    assert_eq!(harness.server.count(op::IDENTIFY), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_terminated_call_then_a_new_server_connects_to_it() {
    let mut harness = closed_with(4022).await;
    harness.until(VoiceStatus::Reconnecting).await;
    let mut server = Server::start(guild(), |_| {}).await;

    harness.fresh_server_at(&mut server).await;
    harness.until(VoiceStatus::Ready).await;

    server.expect_op(op::IDENTIFY).await;
    assert_eq!(harness.joins(CHANNEL), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_terminated_call_waits_the_server_change_delay_even_past_the_timeout() {
    let delay = Duration::from_millis(1_500);
    let mut harness = Harness::start().await;
    harness
        .voice
        .connect(ConnectRequest {
            timeout: Duration::from_secs(1),
            server_change_delay: delay,
            ..harness.request()
        })
        .unwrap();
    harness.announce().await;
    harness.until(VoiceStatus::Ready).await;
    harness.server.gateway.close(4022);
    harness.until(VoiceStatus::Reconnecting).await;
    let mut server = Server::start(guild(), |_| {}).await;

    let offered = Instant::now();
    harness.fresh_server_at(&mut server).await;
    server.expect_op(op::IDENTIFY).await;
    let waited = offered.elapsed();
    harness.until(VoiceStatus::Ready).await;

    assert!(waited >= delay, "identified after {waited:?}");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_first_connect_and_a_move_skip_the_server_change_delay() {
    let mut harness = Harness::start().await;
    harness
        .voice
        .connect(ConnectRequest {
            server_change_delay: Duration::from_secs(3_600),
            ..harness.request()
        })
        .unwrap();
    harness.announce().await;
    harness.until(VoiceStatus::Ready).await;

    harness.fresh_server().await;
    harness.until(VoiceStatus::Reconnecting).await;
    harness.state(Some(OTHER_CHANNEL));
    harness.wait_for(moved).await;
    harness.until(VoiceStatus::Ready).await;

    harness.server.collect().await;
    assert_eq!(harness.server.count(op::IDENTIFY), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_server_without_an_endpoint_holds_media_until_the_next_server_takes_over() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 20)).unwrap();
    harness.tick(2);
    assert_eq!(harness.server.sfu.wait_packets(2, WAIT).len(), 2);

    harness.offer_server(guild(), "gone", None);
    let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
    harness.tick(2);
    let mut server = Server::start(guild(), |_| {}).await;
    harness.fresh_server_at(&mut server).await;
    harness.until(VoiceStatus::Ready).await;
    harness.tick(3);

    assert_eq!(detail, "server_unavailable");
    assert_eq!(server.sfu.wait_packets(3, WAIT).len(), 3);
    assert_eq!(harness.counter("packets_sent"), 5);
    assert_eq!(harness.server.sfu.packets().len(), 2);
    assert_eq!(harness.counter("held_ticks"), 2);
    assert_eq!(queued_frames(&sink), 15 * 960);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_new_token_on_the_same_endpoint_identifies_again_and_never_resumes() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    harness.fresh_server().await;
    let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
    harness.until(VoiceStatus::Ready).await;
    harness.server.collect().await;

    assert_eq!(detail, "server_changed");
    assert_eq!(harness.server.count(op::IDENTIFY), 2);
    assert_eq!(
        harness.server.json(op::IDENTIFY)[1]["token"].as_str(),
        Some("token-2")
    );
    assert_eq!(harness.server.count(op::RESUME), 0);
    assert_eq!(harness.joins(CHANNEL), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_replaced_main_session_sends_the_voice_state_again_and_starts_fresh() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    harness.tap.session_started(ShardId::from(SHARD));
    let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;
    harness.server.collect().await;

    assert_eq!(detail, "session_replaced");
    assert_eq!(harness.server.count(op::IDENTIFY), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_connect_after_the_main_session_started_joins_once() {
    let mut harness = Harness::start().await;
    harness.tap.session_started(ShardId::from(SHARD));

    harness.connected().await;
    harness.server.collect().await;

    assert_eq!(harness.joins(CHANNEL), 1);
    assert_eq!(harness.server.count(op::IDENTIFY), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn no_voice_events_time_out_naming_the_voice_server_update_and_leave() {
    let mut harness = Harness::start().await;
    harness
        .voice
        .connect(ConnectRequest {
            timeout: Duration::from_millis(300),
            ..harness.request()
        })
        .unwrap();

    let (reason, code, detail) = harness.ended().await;

    assert_eq!((reason, code), (DisconnectReason::Timeout, None));
    assert!(detail.starts_with("voice_events: "), "{detail}");
    assert!(detail.contains("VOICE_SERVER_UPDATE"), "{detail}");
    assert_eq!(harness.leaves(), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_update_to_another_channel_is_a_move_by_us() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    harness
        .voice
        .update(guild(), ChannelId::from(OTHER_CHANNEL), true, false)
        .unwrap();
    harness.state(Some(OTHER_CHANNEL));
    let moved = harness.wait_for(moved).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;

    assert_eq!(moved, (CHANNEL, OTHER_CHANNEL, true));
    let update = harness.recorder.voice_states().last().cloned().unwrap();
    assert_eq!(
        update["d"]["channel_id"].as_str(),
        Some(OTHER_CHANNEL.to_string().as_str())
    );
    assert_eq!(update["d"]["self_mute"].as_bool(), Some(true));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn the_last_leave_arriving_after_a_new_connect_is_not_a_removal() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    harness.voice.disconnect(guild()).unwrap();
    harness.ended().await;

    harness.connect();
    harness.state(None);
    harness.announce().await;
    harness.until(VoiceStatus::Ready).await;

    harness.state(None);
    let (reason, _, _) = harness.ended().await;
    assert_eq!(reason, DisconnectReason::Removed);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn disconnect_closes_with_1000_leaves_and_closes_the_sinks() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 3)).unwrap();
    let (marker, _) = sink.mark().unwrap();

    harness.voice.disconnect(guild()).unwrap();
    let (reason, code, _) = harness.ended().await;
    let played = harness
        .wait_for(|delivery| match delivery {
            Delivery::VoiceMarker {
                marker: got,
                played,
                ..
            } if *got == marker => Some(*played),
            _ => None,
        })
        .await;

    assert_eq!((reason, code), (DisconnectReason::Requested, None));
    assert_eq!(harness.server.expect_close().await, 1000);
    assert_eq!(harness.leaves(), 1);
    assert!(sink.is_closed());
    assert!(!played);
    assert!(harness.voice.disconnect(guild()).is_ok());
    assert!(matches!(
        harness.voice.stats(guild()),
        Err(CoreError::UnknownVoice(_))
    ));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn reconnect_closes_with_4000_and_resumes() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    harness.voice.reconnect(guild()).unwrap();

    assert_eq!(harness.server.expect_close().await, 4000);
    harness.server.expect_op(op::RESUME).await;
    harness.until(VoiceStatus::Resuming).await;
    harness.until(VoiceStatus::Ready).await;
    assert_eq!(harness.server.count(op::IDENTIFY), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn across_a_move_hold_sinks_keep_their_audio_and_drop_sinks_skip_ahead() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let hold = harness.sink(pcm(), StallPolicy::Hold);
    let skip = harness.sink(pcm(), StallPolicy::Drop);
    hold.write(&tone(FRAME_SAMPLES * 20)).unwrap();
    skip.write(&tone(FRAME_SAMPLES * 20)).unwrap();

    harness.state(Some(OTHER_CHANNEL));
    harness.until(VoiceStatus::Reconnecting).await;
    harness.tick(5);

    assert_eq!(queued_frames(&hold), 20 * 960);
    assert_eq!(queued_frames(&skip), 15 * 960);
    assert_eq!(
        sink_stat(&skip, "dropped_samples"),
        sink_stat(&skip, "held_ticks") * 960
    );
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;
    harness.tick(1);
    assert_eq!(harness.server.sfu.wait_packets(1, WAIT).len(), 1);
    assert_eq!(queued_frames(&hold), 19 * 960);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn closing_the_core_ends_the_connection_without_a_leave_and_drops_the_lane() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);

    harness.voice.shutdown();
    let (reason, _, _) = harness.ended().await;
    harness.tick(1);

    assert_eq!(reason, DisconnectReason::Closed);
    assert_eq!(harness.leaves(), 0);
    assert!(sink.is_closed());
    assert_eq!(harness.voice.shared.pacer.lanes(), 0);
    assert!(matches!(
        harness.voice.connect(harness.request()),
        Err(CoreError::Closed)
    ));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn unknown_opcodes_are_ignored() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    for unknown in [12, 15, 18, 20] {
        harness.server.gateway.send(unknown, json!({"any": [1, 2]}));
    }
    harness.server.gateway.send(
        op::SPEAKING,
        json!({"user_id": "9", "ssrc": 90, "speaking": 1}),
    );
    harness.speaking_from(90).await;

    assert_eq!(harness.counter("invalid_frames"), 0);
    assert_eq!(harness.statuses().len(), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_nonce_about_to_wrap_rejoins_for_a_new_key() {
    let mut harness = Harness::start().await;
    harness.connect();
    harness
        .media()
        .faults
        .nonce_start
        .store(u32::MAX - 2, Ordering::Relaxed);
    harness.announce().await;
    harness.until(VoiceStatus::Ready).await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 10)).unwrap();

    harness.tick(3);
    harness
        .media()
        .faults
        .nonce_start
        .store(0, Ordering::Relaxed);
    let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
    harness.server.rekey([9; 32]).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;
    harness.tick(2);

    assert_eq!(detail, "rejoining");
    let packets = harness.server.sfu.wait_packets(4, WAIT);
    let nonces: Vec<(usize, u32)> = packets
        .iter()
        .map(|packet| (packet.key, packet.nonce))
        .collect();
    assert_eq!(
        nonces,
        [(0, u32::MAX - 2), (0, u32::MAX - 1), (1, 0), (1, 1)]
    );
    assert_eq!(harness.counter("rejoins"), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_move_while_resuming_abandons_the_resume_for_the_new_server() {
    let mut harness = Harness::start().await;
    let proxy = harness.via_proxy().await;
    proxy.stalls.store(5, Ordering::SeqCst);

    harness.server.gateway.drop_connection();
    harness.until(VoiceStatus::Resuming).await;
    harness.state(Some(OTHER_CHANNEL));
    let moved = harness.wait_for(moved).await;
    let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
    let mut server = Server::start(guild(), |_| {}).await;
    harness.fresh_server_at(&mut server).await;
    harness.until(VoiceStatus::Ready).await;
    server.expect_op(op::IDENTIFY).await;
    harness.server.collect().await;

    assert_eq!(moved, (CHANNEL, OTHER_CHANNEL, false));
    assert_eq!(detail, "moving");
    assert_eq!(server.count(op::RESUME), 0);
    assert_eq!(harness.server.count(op::RESUME), 0);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_new_server_while_waiting_for_dave_restarts_the_first_connect_there() {
    let mut harness = Harness::with(|config| {
        config.dave_protocol_version = 1;
        config.early = vec![Outgoing::Json {
            op: op::CLIENTS_CONNECT,
            d: json!({"user_ids": ["77"]}),
        }];
    })
    .await;
    harness.connect();
    harness.announce().await;
    harness.server.expect_op(op::DAVE_MLS_KEY_PACKAGE).await;
    let mut server = Server::start(guild(), |_| {}).await;

    harness.fresh_server_at(&mut server).await;
    harness.until(VoiceStatus::Ready).await;

    server.expect_op(op::IDENTIFY).await;
    let statuses: Vec<VoiceStatus> = harness.statuses().iter().map(|state| state.0).collect();
    assert_eq!(statuses, [VoiceStatus::Connecting, VoiceStatus::Ready]);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_replaced_main_session_inside_the_move_window_is_not_a_removal() {
    let mut harness = closed_with(4014).await;
    harness.until(VoiceStatus::Reconnecting).await;

    harness.tap.session_started(ShardId::from(SHARD));
    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;

    assert_eq!(harness.leaves(), 0);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_malformed_frame_after_ready_is_skipped_counted_and_never_replayed() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    harness.server.gateway.send(
        op::SPEAKING,
        json!({"user_id": "9", "ssrc": 90, "speaking": 1}),
    );
    harness
        .server
        .gateway
        .send(op::SPEAKING, json!({"user_id": "not a snowflake"}));
    harness.speaking_from(90).await;
    harness
        .settle(|harness| harness.counter("invalid_frames") == 1)
        .await;
    harness.server.gateway.drop_connection();
    let resume = harness.server.expect_op(op::RESUME).await;
    harness.until(VoiceStatus::Ready).await;
    harness.server.gateway.send(
        op::SPEAKING,
        json!({"user_id": "9", "ssrc": 91, "speaking": 1}),
    );
    harness.speaking_from(91).await;

    assert_eq!(resume["seq_ack"].as_u64(), Some(4));
    assert_eq!(harness.counter("invalid_frames"), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_truncated_binary_frame_is_skipped_after_ready() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    harness
        .server
        .gateway
        .send_raw(Message::binary(vec![0_u8, 2]));
    harness
        .settle(|harness| harness.counter("invalid_frames") == 1)
        .await;
    harness.server.gateway.send(
        op::SPEAKING,
        json!({"user_id": "9", "ssrc": 90, "speaking": 1}),
    );
    harness.speaking_from(90).await;

    assert_eq!(harness.statuses().len(), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_truncated_binary_frame_before_ready_fails_the_attempt_and_rejoins() {
    let mut harness = Harness::start().await;
    harness.server.sfu.ignore_discoveries(1);
    harness.connect();
    harness.announce().await;
    harness.server.expect_op(op::IDENTIFY).await;

    harness
        .server
        .gateway
        .send_raw(Message::binary(vec![0_u8, 1]));
    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;

    let statuses: Vec<VoiceStatus> = harness.statuses().iter().map(|state| state.0).collect();
    assert_eq!(statuses, [VoiceStatus::Connecting, VoiceStatus::Ready]);
    assert_eq!(harness.counter("invalid_frames"), 0);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_session_description_with_another_mode_ends_with_a_protocol_error() {
    let mut harness = Harness::with(|config| config.session_mode = Some(XCHACHA.to_owned())).await;
    harness.connect();
    harness.announce().await;

    let (reason, _, detail) = harness.ended().await;

    assert_eq!(reason, DisconnectReason::ProtocolError);
    assert!(detail.contains(XCHACHA) && detail.contains(AES), "{detail}");
    assert_eq!(harness.leaves(), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn resume_sockets_closed_before_resumed_count_as_failed_attempts_then_rejoin() {
    let mut harness = Harness::start().await;
    let proxy = harness.via_proxy().await;
    proxy.refusals.store(2, Ordering::SeqCst);

    harness.server.gateway.drop_connection();
    harness.until(VoiceStatus::Resuming).await;
    let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
    let token = harness.next_token();
    harness.server.token(&token).await;
    harness.offer_server(guild(), &token, Some(&proxy.endpoint()));
    harness.until(VoiceStatus::Ready).await;
    harness.server.collect().await;

    assert_eq!(detail, "rejoining");
    assert_eq!(proxy.refusals.load(Ordering::SeqCst), 0);
    assert_eq!(harness.server.count(op::RESUME), 0);
    assert_eq!(harness.server.count(op::IDENTIFY), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn sessions_that_die_right_after_ready_end_after_three_rejoins() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    for rejoin in 0..4 {
        harness.server.gateway.close(4006);
        if rejoin == 3 {
            break;
        }
        harness
            .settle(|harness| harness.joins(CHANNEL) == rejoin + 2)
            .await;
        harness.fresh_server().await;
        harness.until(VoiceStatus::Ready).await;
    }
    let (reason, code, _) = harness.ended().await;

    assert_eq!(
        (reason, code),
        (DisconnectReason::SessionInvalid, Some(4006))
    );
    assert_eq!(harness.joins(CHANNEL), 4);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn resumes_that_drop_before_their_first_ack_fall_back_to_a_rejoin() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    for _ in 0..2 {
        harness.server.gateway.drop_connection();
        harness.server.expect_op(op::RESUME).await;
        harness.until(VoiceStatus::Ready).await;
    }
    harness.server.gateway.drop_connection();
    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;

    assert_eq!(harness.server.count(op::RESUME), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_lane_that_stops_ends_its_connection_while_the_other_lanes_keep_ticking() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let second = harness.second_guild().await;
    let other = harness
        .voice
        .open_sink(
            GuildId::from(SECOND_GUILD),
            SinkOptions {
                format: pcm(),
                buffer: Duration::from_secs(10),
                volume: 1.0,
                stall: StallPolicy::Hold,
            },
        )
        .unwrap();
    other.write(&tone(FRAME_SAMPLES * 5)).unwrap();

    harness.media().close();
    harness.tick(3);
    let (reason, _, detail) = harness.ended().await;

    assert_eq!(reason, DisconnectReason::ProtocolError);
    assert_eq!(detail, "internal error: the media lane ended");
    assert_eq!(harness.leaves(), 1);
    assert_eq!(second.sfu.wait_packets(3, WAIT).len(), 3);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_panicking_connection_task_is_ended_by_its_supervisor() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    harness.voice.command(guild(), Command::Panic).unwrap();
    let (reason, _, detail) = harness.ended().await;

    assert_eq!(reason, DisconnectReason::ProtocolError);
    assert_eq!(detail, "internal error: injected connection panic");
    assert_eq!(harness.leaves(), 1);
    assert!(matches!(
        harness.voice.dave(guild()),
        Err(CoreError::UnknownVoice(_))
    ));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_full_shard_queue_is_retried_until_the_voice_state_goes_out() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    harness.recorder.refusals.store(2, Ordering::SeqCst);

    harness.tap.session_started(ShardId::from(SHARD));
    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;

    assert_eq!(harness.recorder.refusals.load(Ordering::SeqCst), 0);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn speaking_updates_are_coalesced_and_the_last_flags_win() {
    let mut harness =
        Harness::tuned(|tuning| tuning.speaking_coalesce = Duration::from_millis(200)).await;
    harness.connected().await;
    harness.server.expect_op(op::SPEAKING).await;

    for index in 0..30_u8 {
        harness.voice.set_speaking(guild(), index % 3 + 1).unwrap();
    }
    harness
        .server
        .expect(|message| match message {
            ClientMessage::Json { op, d } if *op == u64::from(op::SPEAKING) => {
                d["speaking"].as_u64() == Some(3)
            }
            _ => false,
        })
        .await;

    let updates = harness.server.json(op::SPEAKING);
    assert!(updates.len() <= 3, "{updates:?}");
    assert!(harness.voice.set_speaking(guild(), 0).is_err());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_server_without_an_endpoint_closes_the_socket_and_stops_keepalives() {
    let keepalive = Duration::from_millis(20);
    let mut harness = Harness::tuned(|tuning| tuning.keepalive_interval = keepalive).await;
    harness.connected().await;
    harness
        .settle(|harness| harness.counter("keepalives_answered") >= 1)
        .await;

    harness.offer_server(guild(), "gone", None);
    let code = harness.server.expect_close().await;
    harness.until(VoiceStatus::Reconnecting).await;
    let sent = harness.counter("keepalives_sent");
    sleep(keepalive * 3).await;

    assert_eq!(code, 4000);
    assert_eq!(harness.counter("keepalives_sent"), sent);
    assert!(harness.media().transport.lock().is_none());
    assert_eq!(harness.gate(), SendMode::Hold);
    assert!(harness.voice.latency(guild()).unwrap().1.is_some());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn each_change_of_mute_or_suppress_is_reported_once_with_all_four_flags() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let flags = |delivery: &Delivery| match delivery {
        Delivery::VoiceSelf {
            self_mute,
            self_deaf,
            mute,
            suppress,
            ..
        } => Some([*self_mute, *self_deaf, *mute, *suppress]),
        _ => None,
    };
    assert_eq!(harness.wait_for(flags).await, [false; 4]);

    harness.offer_state(guild(), Some(CHANNEL), r#","mute":true"#);
    assert_eq!(harness.wait_for(flags).await, [false, false, true, false]);
    harness.offer_state(guild(), Some(CHANNEL), r#","mute":true,"suppress":true"#);
    assert_eq!(harness.wait_for(flags).await, [false, false, true, true]);
    harness.offer_state(guild(), Some(CHANNEL), r#","mute":true,"suppress":true"#);
    harness.server.gateway.send(
        op::SPEAKING,
        json!({"user_id": "9", "ssrc": 90, "speaking": 1}),
    );
    harness.speaking_from(90).await;

    assert_eq!(
        harness.count(|delivery| matches!(delivery, Delivery::VoiceSelf { .. })),
        3
    );
}

fn early_clients(count: usize) -> Vec<Outgoing> {
    (0..count)
        .map(|index| Outgoing::Json {
            op: op::CLIENTS_CONNECT,
            d: json!({"user_ids": [(100 + index).to_string()]}),
        })
        .collect()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn messages_before_the_session_description_are_kept_and_applied_in_order() {
    let mut harness = Harness::with(|config| config.early = early_clients(8)).await;
    harness.connected().await;

    let users: Vec<Vec<UserId>> = harness
        .deliveries
        .iter()
        .filter_map(|delivery| match delivery {
            Delivery::VoiceClients { users, .. } => Some(users.clone()),
            _ => None,
        })
        .collect();

    let expected: Vec<Vec<UserId>> = (100..108).map(|id| vec![user(id)]).collect();
    assert_eq!(users, expected);
    assert_eq!(harness.voice.diagnostics(guild()).unwrap().members.len(), 8);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn one_message_too_many_before_the_session_description_fails_the_attempt() {
    let mut harness = Harness::with(|config| config.early = early_clients(9)).await;
    harness.connect();
    harness.announce().await;

    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;

    assert_eq!(harness.statuses().len(), 1);
}

fn dave_states(delivery: &Delivery) -> Option<(u16, Option<u64>, u16)> {
    match delivery {
        Delivery::VoiceDave {
            protocol_version,
            epoch,
            transition_id,
            ..
        } => Some((*protocol_version, *epoch, *transition_id)),
        _ => None,
    }
}

fn transition_readies(server: &Server) -> Vec<u16> {
    server
        .dave_outbound()
        .into_iter()
        .filter_map(|message| match message {
            Outbound::TransitionReady(id) => Some(id),
            _ => None,
        })
        .collect()
}

fn invalid_commit_welcomes(server: &Server) -> Vec<u16> {
    server
        .dave_outbound()
        .into_iter()
        .filter_map(|message| match message {
            Outbound::InvalidCommitWelcome(id) => Some(id),
            _ => None,
        })
        .collect()
}

impl Harness {
    async fn dave() -> Self {
        Self::with(|config| config.dave_protocol_version = 1).await
    }

    async fn in_call(&mut self, call: &mut Call) {
        self.connect();
        self.announce().await;
        self.join_call(call).await;
    }

    async fn talking_with(&mut self, peers: &[u64]) -> Call {
        let mut call = dave_call(CHANNEL, peers);
        self.in_call(&mut call).await;
        self.encrypting(&mut call).await;
        call
    }

    async fn rejoined(&mut self, call: &mut Call, members: usize, executed: u64) {
        self.pump(call, |harness, call| {
            call.fake.members().len() == members
                && harness.counter("transitions_executed") > executed
                && harness.gate() == SendMode::Encrypt
        })
        .await;
    }

    fn opus_sink(&self) -> Arc<Sink> {
        self.sink(SinkFormat::Opus, StallPolicy::Hold)
    }

    fn play(&self, sink: &Sink, frames: usize) -> Vec<Bytes> {
        let packets = opus(frames);
        for packet in &packets {
            sink.write(packet).unwrap();
        }
        let before = self.server.sfu.packets().len();
        self.tick(u32::try_from(frames).unwrap());
        assert_eq!(
            self.server.sfu.wait_packets(before + frames, WAIT).len(),
            before + frames
        );
        packets
    }

    fn heard(&self, call: &mut Call, peer: u64, frames: usize) -> Vec<Option<Vec<u8>>> {
        let packets = self.server.sfu.packets();
        packets[packets.len() - frames..]
            .iter()
            .map(|packet| hear(call, peer, &packet.payload))
            .collect()
    }

    fn assert_heard(&self, call: &mut Call, peers: &[u64]) {
        let sink = self.opus_sink();
        let sent = self.play(&sink, 2);
        for &peer in peers {
            let heard = self.heard(call, peer, 2);
            let expected: Vec<Option<Vec<u8>>> =
                sent.iter().map(|packet| Some(packet.to_vec())).collect();
            assert_eq!(heard, expected, "{peer}");
        }
        sink.close();
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_peer_joining_a_lone_bot_forms_a_group_and_hears_the_exact_opus_bytes() {
    for peer in [LATE_PEER, EARLY_PEER] {
        let mut harness = Harness::dave().await;
        let mut call = dave_call(CHANNEL, &[]);
        harness.in_call(&mut call).await;

        call.join(user(peer));
        harness.encrypting(&mut call).await;
        let executed = harness.wait_for(dave_states).await;

        assert_eq!(executed.0, 1, "{peer}");
        assert_eq!(executed.1, Some(1), "{peer}");
        assert_ne!(executed.2, 0, "{peer}");
        assert!(harness.voice.dave(guild()).unwrap().privacy_code.is_some());
        assert_eq!(
            harness
                .voice
                .verification_code(guild(), user(peer))
                .unwrap(),
            dave::verification_code(call.dave(user(peer)).fingerprint_pair(bot()).unwrap())
                .unwrap()
        );
        assert_eq!(
            harness.counter("welcomes"),
            u64::from(peer == EARLY_PEER),
            "{peer}"
        );
        harness.assert_heard(&mut call, &[peer]);
        assert_eq!(harness.counter("dave_frames"), 2);
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn the_last_peer_leaving_resets_the_group_and_a_new_peer_gets_a_new_key() {
    let mut harness = Harness::dave().await;
    let mut call = harness.talking_with(&[LATE_PEER]).await;
    let readies = transition_readies(&harness.server).len();

    call.leave(user(LATE_PEER));
    harness
        .pump(&mut call, |harness, _| harness.counter("key_packages") == 2)
        .await;
    harness
        .pump(&mut call, |harness, _| harness.gate() == SendMode::Discard)
        .await;
    let sink = harness.opus_sink();
    for packet in opus(3) {
        sink.write(&packet).unwrap();
    }
    harness.tick(3);
    sink.close();

    assert_eq!(transition_readies(&harness.server).len(), readies);
    assert!(harness.server.sfu.packets().is_empty());
    assert_eq!(harness.counter("alone_ticks"), 3);
    call.join(user(THIRD_PEER));
    harness.encrypting(&mut call).await;
    harness.assert_heard(&mut call, &[THIRD_PEER]);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn too_many_dave_recoveries_end_the_connection() {
    let mut harness = Harness::dave().await;
    let mut call = dave_call(CHANNEL, &[]);
    harness.in_call(&mut call).await;

    for transition_id in 1..=3_u16 {
        harness.server.send_dave(DaveOp::Welcome {
            transition_id,
            welcome: Bytes::from_static(b"not a welcome"),
        });
    }
    let (reason, _, _) = harness.ended().await;

    assert_eq!(reason, DisconnectReason::DaveFailed);
    assert_eq!(harness.leaves(), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn after_a_recovery_media_waits_for_the_re_add_and_never_uses_the_old_key() {
    let mut harness = Harness::dave().await;
    let mut call = harness.talking_with(&[LATE_PEER]).await;
    let readies = transition_readies(&harness.server).len();
    call.fake.hold_transitions = true;
    let sink = harness.opus_sink();
    let before = harness.server.sfu.packets().len();

    harness.server.send_dave(DaveOp::AnnounceCommit {
        transition_id: 9,
        commit: Bytes::from_static(b"a commit"),
    });
    harness
        .pump(&mut call, |harness, _| {
            transition_readies(&harness.server).len() > readies
        })
        .await;
    let rejoining = harness.gate();
    let packets = opus(2);
    for packet in &packets {
        sink.write(packet).unwrap();
    }
    harness.tick(2);
    let sent_while_rejoining = harness.server.sfu.packets().len() - before;
    let released = call.fake.release_transitions();
    call.enqueue(released);
    harness.encrypting(&mut call).await;
    harness.tick(2);

    assert_eq!(harness.counter("recoveries"), 1);
    assert_eq!(rejoining, SendMode::Hold);
    assert_eq!(sent_while_rejoining, 0);
    let arrived = harness.server.sfu.wait_packets(before + 2, WAIT);
    assert_eq!(arrived.len(), before + 2);
    for (packet, sent) in arrived[before..].iter().zip(&packets) {
        assert_eq!(
            hear(&mut call, LATE_PEER, &packet.payload),
            Some(sent.to_vec())
        );
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_transition_split_by_two_resumes_completes_from_the_replays() {
    let mut harness = Harness::dave().await;
    let mut call = harness.talking_with(&[LATE_PEER]).await;
    harness.wait_for(dave_states).await;
    call.fake.hold_commits = true;
    call.fake.hold_transitions = true;
    call.join(user(THIRD_PEER));
    harness
        .pump(&mut call, |_, call| call.fake.awaiting().is_empty())
        .await;

    harness.server.gateway.drop_connection();
    call.fake.hold_commits = false;
    let settled = call.fake.settle();
    call.enqueue(settled);
    harness
        .pump(&mut call, |harness, _| {
            transition_readies(&harness.server).len() == 2
        })
        .await;
    harness.server.gateway.drop_connection();
    let held = call.fake.release_transitions();
    call.enqueue(held);
    harness
        .pump(&mut call, |harness, _| {
            harness.counter("transitions_executed") == 2
        })
        .await;
    let executed = harness.wait_for(dave_states).await;
    harness.server.collect().await;

    assert_eq!(harness.server.count(op::RESUME), 2);
    assert_eq!(executed.1, Some(2));
    assert_eq!(harness.counter("recoveries"), 0);
    harness.assert_heard(&mut call, &[LATE_PEER, THIRD_PEER]);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn media_sent_between_a_commit_and_its_execute_uses_the_old_key_and_still_decrypts() {
    let mut harness = Harness::dave().await;
    let mut call = harness.talking_with(&[LATE_PEER]).await;
    call.fake.hold_transitions = true;
    call.join(user(THIRD_PEER));
    harness
        .pump(&mut call, |harness, _| {
            transition_readies(&harness.server).len() == 2
        })
        .await;

    let sink = harness.opus_sink();
    let during = harness.play(&sink, 2);
    let captured: Vec<Vec<u8>> = harness.server.sfu.packets()[..2]
        .iter()
        .map(|packet| packet.payload.clone())
        .collect();
    let held = call.fake.release_transitions();
    call.enqueue(held);
    harness.encrypting(&mut call).await;
    harness
        .pump(&mut call, |harness, _| {
            harness.counter("transitions_executed") == 2
        })
        .await;

    for (payload, packet) in captured.iter().zip(&during) {
        assert_eq!(hear(&mut call, LATE_PEER, payload), Some(packet.to_vec()));
    }
    harness.assert_heard(&mut call, &[LATE_PEER, THIRD_PEER]);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_move_builds_a_new_dave_group_and_never_uses_the_old_one() {
    let mut harness = Harness::dave().await;
    let mut old = harness.talking_with(&[LATE_PEER]).await;

    harness.state(Some(OTHER_CHANNEL));
    harness.until(VoiceStatus::Reconnecting).await;
    harness.fresh_server().await;
    let mut call = dave_call(OTHER_CHANNEL, &[THIRD_PEER]);
    harness.server.expect_op(op::IDENTIFY).await;
    call.join_remote(bot());
    harness.encrypting(&mut call).await;

    harness.assert_heard(&mut call, &[THIRD_PEER]);
    assert_eq!(harness.heard(&mut old, LATE_PEER, 2), [None, None]);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn clients_joining_and_leaving_update_members_ssrcs_and_speaking() {
    let mut harness = Harness::dave().await;
    let mut call = dave_call(CHANNEL, &[]);
    harness.in_call(&mut call).await;
    let speaking = harness.server.count(op::SPEAKING);

    call.join(user(LATE_PEER));
    harness.encrypting(&mut call).await;
    harness.server.gateway.send(
        op::SPEAKING,
        json!({"user_id": LATE_PEER.to_string(), "ssrc": 55, "speaking": 1}),
    );
    harness.speaking_from(55).await;
    let joined = harness
        .wait_for(|delivery| match delivery {
            Delivery::VoiceClients { users, .. } => Some(users.clone()),
            _ => None,
        })
        .await;
    let ssrcs = |harness: &Harness| {
        harness.voice.shared.connections.lock()[&guild()]
            .info
            .ssrcs
            .lock()
            .clone()
    };
    assert_eq!(ssrcs(&harness).get(&55), Some(&user(LATE_PEER)));
    harness
        .pump(&mut call, |harness, _| {
            harness.server.count(op::SPEAKING) > speaking
        })
        .await;

    call.leave(user(LATE_PEER));
    harness
        .pump(&mut call, |harness, _| harness.gate() == SendMode::Discard)
        .await;
    let left = harness
        .wait_for(|delivery| match delivery {
            Delivery::VoiceClientLeft { user, .. } => Some(*user),
            _ => None,
        })
        .await;

    assert_eq!(joined, [user(LATE_PEER)]);
    assert_eq!(left, user(LATE_PEER));
    assert!(ssrcs(&harness).is_empty());
    assert!(
        harness
            .voice
            .diagnostics(guild())
            .unwrap()
            .members
            .is_empty()
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn malformed_dave_messages_never_break_the_connection() {
    let mut harness = Harness::dave().await;
    let mut call = harness.talking_with(&[LATE_PEER]).await;
    let executed = harness.counter("transitions_executed");

    for junk in [&b""[..], b"\x00", b"\xff\xff\xff\xff", b"not mls at all"] {
        harness
            .server
            .send_dave(DaveOp::ExternalSender(Bytes::copy_from_slice(junk)));
        for operation in [ProposalsOp::Append, ProposalsOp::Revoke] {
            harness.server.send_dave(DaveOp::Proposals {
                operation,
                data: Bytes::copy_from_slice(junk),
            });
        }
    }
    harness.server.send_dave(DaveOp::AnnounceCommit {
        transition_id: 4,
        commit: Bytes::from_static(b"\x00\x01"),
    });
    harness
        .pump(&mut call, |harness, _| {
            invalid_commit_welcomes(&harness.server) == [4]
        })
        .await;
    harness
        .pump(&mut call, |harness, _| harness.counter("key_packages") == 2)
        .await;
    harness.rejoined(&mut call, 2, executed).await;

    assert_eq!(harness.counter("recoveries"), 1);
    assert_eq!(harness.counter("rejected_proposals"), 6);
    assert_eq!(harness.statuses().len(), 2);
    harness.assert_heard(&mut call, &[LATE_PEER]);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_frame_dave_refuses_is_dropped_and_counted_never_sent_plain() {
    let mut harness = Harness::dave().await;
    let mut call = harness.talking_with(&[LATE_PEER]).await;
    *harness.media().faults.encrypt.lock() = Some(crate::voice::dave::EncryptFailure::NotReady);
    let sink = harness.opus_sink();
    let sent = opus(2);
    for packet in &sent {
        sink.write(packet).unwrap();
    }

    harness.tick(2);
    let packets = harness.server.sfu.wait_packets(1, WAIT);

    assert_eq!(harness.counter("packets_sent"), 1);
    assert_ne!(packets[0].payload, sent[1].to_vec());
    assert_eq!(
        hear(&mut call, LATE_PEER, &packets[0].payload),
        Some(sent[1].to_vec())
    );
    assert_eq!(harness.counter("dave_not_ready"), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_long_mls_call_holds_only_its_own_lane_and_never_blocks_readers() {
    let mut harness = Harness::dave().await;
    let mut call = harness.talking_with(&[LATE_PEER]).await;
    let second = harness.second_guild().await;
    let other = harness
        .voice
        .open_sink(
            GuildId::from(SECOND_GUILD),
            SinkOptions {
                format: pcm(),
                buffer: Duration::from_secs(10),
                volume: 1.0,
                stall: StallPolicy::Hold,
            },
        )
        .unwrap();
    other.write(&tone(FRAME_SAMPLES * 5)).unwrap();
    let own = harness.sink(pcm(), StallPolicy::Hold);
    own.write(&tone(FRAME_SAMPLES * 5)).unwrap();
    let (parked, on_parked) = std::sync::mpsc::channel();
    let (release, on_release) = std::sync::mpsc::channel();
    harness.media().dave.lock().inject(
        MlsCall::Proposals,
        Fault::Park {
            parked,
            release: on_release,
        },
    );

    call.join(user(THIRD_PEER));
    harness
        .pump(&mut call, |_, _| on_parked.try_recv().is_ok())
        .await;
    let shared = Arc::clone(&harness.voice.shared);
    let ticking = tokio::task::spawn_blocking(move || shared.pacer.tick(5));
    timeout(WAIT, ticking)
        .await
        .expect("the ticks waited for the DAVE call")
        .unwrap();
    let dave = harness.voice.dave(guild()).unwrap();
    let busy = harness.counter("dave_busy");
    release.send(()).unwrap();

    assert_eq!(busy, 5);
    assert_eq!(dave.protocol_version, 1);
    assert_eq!(queued_frames(&own), 5 * 960);
    assert_eq!(second.sfu.wait_packets(5, WAIT).len(), 5);
    harness.encrypting(&mut call).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn real_pacer_threads_send_on_a_20_ms_clock() {
    let mut harness = Harness::build(|_| {}, PacerPool::new(), tuning()).await;
    harness.connected().await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    let written = std::time::Instant::now();
    sink.write(&tone(FRAME_SAMPLES * 10)).unwrap();
    sink.flush().unwrap();

    let packets: Vec<Packet> = harness.server.sfu.wait_packets(15, WAIT);

    assert_eq!(packets.len(), 15);
    let last = packets[14].at.duration_since(written);
    assert!(last >= FRAME * 10, "{last:?}");
}

impl Harness {
    async fn acknowledged(&mut self, ssrc: u32) {
        self.server.collect().await;
        let from = self.server.messages.len();
        let deadline = Instant::now() + WAIT;
        while !self.server.messages[from..]
            .iter()
            .any(|message| message.op() == Some(u64::from(op::HEARTBEAT)))
        {
            assert!(Instant::now() < deadline, "no heartbeat after READY");
            self.server.collect().await;
        }
        self.server.gateway.send(
            op::SPEAKING,
            json!({"user_id": "9", "ssrc": ssrc, "speaking": 1}),
        );
        self.speaking_from(ssrc).await;
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_acknowledged_heartbeat_restores_the_rejoin_budget() {
    let mut harness = Harness::with(|config| config.heartbeat_interval = QUICK_HEARTBEAT).await;
    harness.connected().await;

    for rejoin in 0..4 {
        harness
            .acknowledged(90 + u32::try_from(rejoin).unwrap())
            .await;
        harness.server.gateway.close(4006);
        harness
            .settle(|harness| harness.joins(CHANNEL) == rejoin + 2)
            .await;
        harness.fresh_server().await;
        harness.until(VoiceStatus::Ready).await;
    }

    assert_eq!(harness.counter("rejoins"), 4);
    assert!(harness.voice.latency(guild()).unwrap().0.is_some());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn clients_announced_after_the_session_description_hold_media_until_the_group_forms() {
    let mut harness = Harness::dave().await;
    let mut call = dave_call(CHANNEL, &[]);
    harness.in_call(&mut call).await;
    let alone = harness.gate();
    call.fake.hold_transitions = true;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 4)).unwrap();

    call.join(user(LATE_PEER));
    harness
        .pump(&mut call, |harness, _| {
            transition_readies(&harness.server).len() == 1
        })
        .await;
    let forming = harness.gate();
    harness.tick(2);
    let held = harness.counter("held_ticks");
    let released = call.fake.release_transitions();
    call.enqueue(released);
    harness.encrypting(&mut call).await;
    harness.tick(2);

    assert_eq!(alone, SendMode::Discard);
    assert_eq!(forming, SendMode::Hold);
    assert_eq!(held, 2);
    assert_eq!(harness.server.sfu.wait_packets(2, WAIT).len(), 2);
    assert_eq!(
        harness.count(|delivery| matches!(delivery, Delivery::VoiceStalled { .. })),
        0
    );
    let statuses: Vec<VoiceStatus> = harness.statuses().iter().map(|state| state.0).collect();
    assert_eq!(statuses, [VoiceStatus::Connecting, VoiceStatus::Ready]);
}

fn tone_file() -> SourceSpec {
    SourceSpec::File(fixture("tone.flac"))
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn shutdown_closes_every_open_source_and_refuses_new_ones() {
    let harness = Harness::start().await;
    let kept = harness.voice.open_source(tone_file()).unwrap();
    drop(harness.voice.open_source(tone_file()).unwrap());

    harness.voice.shutdown();

    assert!(kept.is_closed());
    assert!(matches!(kept.read(), Err(CoreError::HandleClosed(_))));
    assert!(matches!(
        harness.voice.open_source(tone_file()),
        Err(CoreError::Closed)
    ));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn dropping_voice_in_another_process_leaves_its_sources_open() {
    let harness = Harness::start().await;
    let kept = harness.voice.open_source(tone_file()).unwrap();
    let mut voice = harness.voice;
    voice.owner = voice.owner.wrapping_add(1);

    drop(voice);

    assert!(!kept.is_closed());
    kept.close();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn dropped_sources_are_forgotten_when_the_next_one_opens() {
    let harness = Harness::start().await;
    for _ in 0..10 {
        drop(harness.voice.open_source(tone_file()).unwrap());
    }
    let kept = harness.voice.open_source(tone_file()).unwrap();

    assert_eq!(harness.voice.shared.sources.lock().len(), 1);
    assert!(!kept.is_closed());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn sinks_and_sources_never_share_an_id() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    let first = harness.sink(pcm(), StallPolicy::Hold).id();
    let source = harness.voice.open_source(tone_file()).unwrap().id();
    let last = harness.sink(pcm(), StallPolicy::Hold).id();

    assert!(first != source && source != last && first != last);
}
