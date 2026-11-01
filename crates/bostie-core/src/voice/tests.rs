use std::collections::HashSet;
use std::net::SocketAddr;
use std::sync::atomic::AtomicUsize;

use futures_util::{SinkExt, StreamExt};
use parking_lot::Mutex as SyncMutex;
use sonic_rs::{JsonValueTrait, Value, json};
use tokio::net::TcpListener;
use tokio::time::{Instant, sleep, timeout};
use tokio_websockets::{ClientBuilder, CloseCode, Message, ServerBuilder};

use super::*;
use crate::bell::{self, BellReceiver};
use crate::voice::crypto::Mode;
use crate::voice::dave::{Outbound, RECOVERY_LIMIT, SendMode};
use crate::voice::media::tests::{queued_frames, tone};
use crate::voice::proto::op;
use crate::voice::source::tests::fixture;
use crate::voice::tap::{VOICE_SERVER_UPDATE, VOICE_STATE_UPDATE};
use crate::voice::testkit::gateway::{ClientMessage, Config, FakeVoiceGateway, HelloAt, Outgoing};
use crate::voice::testkit::mls::{Call, Signal};
use crate::voice::testkit::pacer::ManualPacer;
use crate::voice::testkit::sfu::{Packet, Sealing, Sfu};
use crate::voice::types::{
    Application, DaveOp, FRAME, FRAME_SAMPLES, Overflow, PcmFormat, ProposalsOp, ReceiveFormat,
    ReceiveStall, ReceiveTuning, SampleFormat, SinkFormat, StallPolicy,
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
            move_window: WAIT,
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
            receive: ReceiveTuning {
                hold: WAIT,
                ..ReceiveTuning::default()
            },
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
            other => panic!("unexpected {other:?}"),
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
                let d = if let Some(transition_id) = transition_id {
                    json!({
                        "transition_id": transition_id,
                        "epoch": epoch,
                        "protocol_version": protocol_version,
                    })
                } else {
                    json!({"epoch": epoch, "protocol_version": protocol_version})
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
    pacer: Arc<ManualPacer>,
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
        Self::build(change, None, tuning()).await
    }

    async fn tuned(change: impl FnOnce(&mut ConnectionTuning)) -> Self {
        let mut tuning = tuning();
        change(&mut tuning.connection);
        Self::build(|_| {}, None, tuning).await
    }

    async fn build(
        change: impl FnOnce(&mut Config),
        pace: Option<Arc<dyn Pace>>,
        tuning: Tuning,
    ) -> Self {
        crate::init_test_tracing();
        let pacer = Arc::new(ManualPacer::new());
        let pace = pace.unwrap_or_else(|| Arc::<ManualPacer>::clone(&pacer));
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
            pace,
            tuning,
        )
        .unwrap();
        Self {
            voice,
            pacer,
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

    fn request() -> ConnectRequest {
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
        self.voice.connect(&Self::request()).unwrap();
    }

    fn offer_state(&self, guild: GuildId, channel: Option<u64>, extra: &str) {
        let channel = channel.map_or_else(|| "null".to_owned(), |id| format!(r#""{id}""#));
        let data = format!(
            r#"{{"guild_id":"{guild}","user_id":"{}","channel_id":{channel},"session_id":"{}"{extra}}}"#,
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
        self.pacer.tick(n);
    }

    fn media(&self) -> Arc<Media> {
        self.voice.media(guild()).unwrap()
    }

    fn gate(&self) -> SendMode {
        self.media().gate()
    }

    fn executed(&mut self) -> usize {
        self.count(|delivery| dave_states(delivery).is_some())
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

    fn ready(&self) -> bool {
        let latest = self
            .deliveries
            .iter()
            .rev()
            .find_map(|delivery| match delivery {
                Delivery::VoiceState {
                    guild: got, status, ..
                } if *got == guild() => Some(*status),
                _ => None,
            });
        latest == Some(VoiceStatus::Ready)
    }

    async fn encrypting(&mut self, call: &mut Call) {
        self.pump(call, |harness, _| {
            harness.ready() && harness.gate() == SendMode::Encrypt
        })
        .await;
    }
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
                        drop(client.send(close).await);
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
async fn a_plain_call_identifies_selects_aes_and_speaks_with_its_ssrc() {
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
    let mut harness = Harness::with(|config| {
        config.modes = vec![XCHACHA.to_owned()];
        config.hello = HelloAt::AfterReady;
    })
    .await;
    harness.connected().await;

    let select = harness.server.expect_op(op::SELECT_PROTOCOL).await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 2)).unwrap();
    harness.tick(2);

    assert_eq!(select["data"]["mode"].as_str(), Some(XCHACHA));
    assert_eq!(harness.server.sfu.wait_packets(2, WAIT).len(), 2);
    assert_eq!(harness.server.sfu.rejected(), 0);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn no_supported_mode_ends_with_a_protocol_error_and_leaves() {
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
    assert_eq!(sink.stats().unwrap().held_ticks, 0);
    assert_eq!(harness.server.count(op::RESUME), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_lost_transport_resumes_once_ready_and_rejoins_before() {
    for (ready, close) in [(true, Some(4015)), (false, Some(4015)), (false, None)] {
        let mut harness = Harness::tuned(|tuning| tuning.discovery_interval = WAIT).await;
        if !ready {
            harness.server.sfu.ignore_discoveries(usize::MAX);
        }
        harness.connect();
        harness.announce().await;
        harness.server.expect_op(op::IDENTIFY).await;
        if ready {
            harness.until(VoiceStatus::Ready).await;
        }

        match close {
            Some(code) => harness.server.gateway.close(code),
            None => harness.server.gateway.drop_connection(),
        }

        if ready {
            let (_, _, _, detail) = harness.until(VoiceStatus::Resuming).await;
            harness.server.expect_op(op::RESUME).await;
            harness.until(VoiceStatus::Ready).await;
            assert!(detail.contains("4015"), "{detail}");
        } else {
            harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
            assert_eq!(harness.server.count(op::RESUME), 0, "{close:?}");
        }
        assert_eq!(harness.server.count(op::IDENTIFY), 1, "{close:?}");
    }
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
async fn a_close_code_bostie_caused_ends_the_connection_without_retrying() {
    let mut harness = closed_with(4021).await;

    assert_eq!(harness.ended().await.0, DisconnectReason::RateLimited);
    let statuses = harness.statuses();
    let steps: Vec<VoiceStatus> = statuses.iter().map(|state| state.0).collect();
    assert_eq!(
        steps,
        [
            VoiceStatus::Connecting,
            VoiceStatus::Ready,
            VoiceStatus::Disconnected
        ]
    );
    assert_eq!(statuses.last().unwrap().2, Some(4021));
    assert_eq!(harness.joins(CHANNEL), 1);
    assert_eq!(harness.leaves(), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_dead_session_close_rejoins_with_a_new_voice_state() {
    let mut harness = closed_with(4006).await;

    let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;

    assert_eq!(detail, "rejoining");
    assert_eq!(harness.server.count(op::RESUME), 0);
    assert_eq!(harness.joins(CHANNEL), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn disconnected_and_call_terminated_wait_for_the_gateway_then_end() {
    for (code, reason, leaves) in [
        (4014, DisconnectReason::Removed, 0),
        (4022, DisconnectReason::CallTerminated, 1),
    ] {
        let mut harness =
            Harness::tuned(|tuning| tuning.move_window = Duration::from_millis(200)).await;
        harness.connected().await;
        harness.server.gateway.close(code);

        let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
        let (ended, ended_code, _) = harness.ended().await;

        assert_eq!(detail, "waiting_for_gateway", "{code}");
        assert_eq!((ended, ended_code), (reason, Some(code)));
        assert_eq!(harness.leaves(), leaves, "{code}");
    }
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
            .connect(&ConnectRequest {
                guild: second,
                ..Self::request()
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
async fn a_move_by_someone_else_reconnects_and_keeps_the_sinks() {
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
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_server_first_move_joins_the_new_channel_group() {
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
    let (reason, code, _) = harness.ended().await;

    assert_eq!((reason, code), (DisconnectReason::Removed, None));
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
        .connect(&ConnectRequest {
            timeout: Duration::from_secs(1),
            server_change_delay: delay,
            ..Harness::request()
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
        .connect(&ConnectRequest {
            server_change_delay: Duration::from_secs(3_600),
            ..Harness::request()
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
async fn a_changed_server_change_delay_applies_to_the_next_server_change() {
    let mut harness = Harness::start().await;
    harness
        .voice
        .connect(&ConnectRequest {
            server_change_delay: Duration::from_secs(3_600),
            ..Harness::request()
        })
        .unwrap();
    harness.announce().await;
    harness.until(VoiceStatus::Ready).await;

    harness
        .voice
        .set_server_change_delay(guild(), Duration::ZERO)
        .unwrap();
    harness.fresh_server().await;
    harness.until(VoiceStatus::Reconnecting).await;
    harness.until(VoiceStatus::Ready).await;

    harness.server.collect().await;
    assert_eq!(harness.server.count(op::IDENTIFY), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_session_replaced_during_the_change_delay_waits_for_new_server() {
    let mut harness = Harness::start().await;
    harness
        .voice
        .connect(&ConnectRequest {
            server_change_delay: Duration::from_secs(3_600),
            ..Harness::request()
        })
        .unwrap();
    harness.announce().await;
    harness.until(VoiceStatus::Ready).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Reconnecting).await;

    harness.tap.session_started(ShardId::from(SHARD));
    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
    harness.announce().await;
    harness.server.expect_op(op::IDENTIFY).await;
    let identify = harness.server.expect_op(op::IDENTIFY).await;

    assert_eq!(identify["token"].as_str(), Some("token-3"));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_endpointless_server_holds_media_until_the_next_one() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 20)).unwrap();
    harness.tick(2);
    assert_eq!(harness.server.sfu.wait_packets(2, WAIT).len(), 2);

    harness.offer_server(guild(), "gone", None);
    let code = harness.server.expect_close().await;
    let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
    harness.tick(2);
    let mut server = Server::start(guild(), |_| {}).await;
    harness.fresh_server_at(&mut server).await;
    harness.until(VoiceStatus::Ready).await;
    harness.tick(3);

    assert_eq!((code, detail.as_str()), (4000, "server_unavailable"));
    assert_eq!(server.sfu.wait_packets(3, WAIT).len(), 3);
    assert_eq!(harness.server.sfu.packets().len(), 2);
    assert_eq!(sink.stats().unwrap().held_ticks, 2);
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
async fn a_connect_timeout_names_what_is_missing() {
    let short = Duration::from_millis(300);
    let long = Duration::from_secs(1);
    for (offered, timeout, missing) in [
        (
            "nothing",
            short,
            "voice_events: no VOICE_STATE_UPDATE or VOICE_SERVER_UPDATE arrived for the channel",
        ),
        (
            "state",
            short,
            "voice_events: no VOICE_SERVER_UPDATE with an endpoint arrived",
        ),
        (
            "server",
            short,
            "voice_events: no VOICE_STATE_UPDATE arrived for the channel",
        ),
        (
            "a server that never answers discovery",
            short,
            "no progress within",
        ),
        (
            "a server whose discovery fails",
            long,
            "ip discovery: no answer from",
        ),
        (
            "an endpointless server after ready",
            long,
            "server_unavailable: no VOICE_SERVER_UPDATE with an endpoint arrived",
        ),
    ] {
        let slow = offered == "a server that never answers discovery";
        let mut harness = Harness::tuned(|tuning| {
            if slow {
                tuning.discovery_interval = WAIT;
            }
        })
        .await;
        harness
            .voice
            .connect(&ConnectRequest {
                timeout,
                ..Harness::request()
            })
            .unwrap();
        match offered {
            "state" => harness.state(Some(CHANNEL)),
            "server" => harness.fresh_server().await,
            "nothing" => {}
            "an endpointless server after ready" => {
                harness.announce().await;
                harness.until(VoiceStatus::Ready).await;
                harness.offer_server(guild(), "gone", None);
            }
            _ => {
                harness.server.sfu.ignore_discoveries(usize::MAX);
                harness.announce().await;
            }
        }

        let (reason, code, detail) = harness.ended().await;

        assert_eq!(
            (reason, code),
            (DisconnectReason::Timeout, None),
            "{offered}"
        );
        assert!(detail.contains(missing), "{offered}: {detail}");
        if offered == "nothing" {
            assert_eq!(harness.leaves(), 1);
        }
    }
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
async fn disconnect_closes_with_1000_leaves_and_closes_the_sinks_and_receivers() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 3)).unwrap();
    let (marker, _) = sink.mark().unwrap();
    let receiver = harness.receiver(receiving(ReceiveFormat::Opus));

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
    assert_eq!(
        receiver.read(256).unwrap(),
        ReceiveRead::Ended(Ended::Disconnected)
    );
    harness.voice.disconnect(guild()).unwrap();
    assert!(matches!(
        harness.voice.dave(guild()),
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
async fn across_a_move_a_hold_sink_keeps_its_audio_for_the_new_server() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let hold = harness.sink(pcm(), StallPolicy::Hold);
    hold.write(&tone(FRAME_SAMPLES * 20)).unwrap();

    harness.state(Some(OTHER_CHANNEL));
    harness.until(VoiceStatus::Reconnecting).await;
    harness.tick(5);
    let kept = queued_frames(&hold);
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;
    harness.tick(1);

    assert_eq!(kept, 20 * 960);
    assert_eq!(harness.server.sfu.wait_packets(1, WAIT).len(), 1);
    assert_eq!(queued_frames(&hold), 19 * 960);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn closing_the_core_ends_without_a_leave_and_drops_the_lane() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);

    harness.voice.shutdown();
    let (reason, _, _) = harness.ended().await;
    harness.tick(1);

    assert_eq!(reason, DisconnectReason::Closed);
    assert_eq!(harness.leaves(), 0);
    assert!(sink.is_closed());
    assert_eq!(harness.pacer.lanes(), 0);
    assert!(matches!(
        harness.voice.connect(&Harness::request()),
        Err(CoreError::Closed)
    ));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_unknown_opcode_is_ignored() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    harness.server.gateway.send(20, json!({"any": [1, 2]}));
    harness.server.gateway.send(
        op::SPEAKING,
        json!({"user_id": "9", "ssrc": 90, "speaking": 1}),
    );
    harness.speaking_from(90).await;

    assert_eq!(harness.statuses().len(), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_exhausted_nonce_rejoins_for_a_new_key_and_restarts_at_zero() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 10)).unwrap();
    harness.tick(2);

    harness.media().exhausted.notify_one();
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
    assert_eq!(nonces, [(0, 0), (0, 1), (1, 0), (1, 1)]);
    assert_eq!(harness.joins(CHANNEL), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_move_while_resuming_abandons_the_resume_for_the_new_server() {
    let mut harness = Harness::tuned(|tuning| tuning.ws.connect = WAIT).await;
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
async fn a_malformed_frame_after_ready_is_skipped_and_never_replayed() {
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
    let speaking = json!({"user_id": "9", "ssrc": 92, "speaking": 1});
    let unsequenced = json!({"op": op::SPEAKING, "d": speaking});
    harness
        .server
        .gateway
        .send_raw(Message::text(unsequenced.to_string()));
    harness.speaking_from(90).await;
    harness.speaking_from(92).await;
    harness.server.gateway.drop_connection();
    let resume = harness.server.expect_op(op::RESUME).await;
    harness.until(VoiceStatus::Ready).await;
    harness.server.gateway.send(
        op::SPEAKING,
        json!({"user_id": "9", "ssrc": 91, "speaking": 1}),
    );
    harness.speaking_from(91).await;

    assert_eq!(resume["seq_ack"].as_u64(), Some(4));
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
async fn resumes_closed_before_resumed_count_as_failures_then_rejoin() {
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
async fn a_resume_whose_dial_times_out_is_retried_before_any_rejoin() {
    let mut harness = Harness::tuned(|tuning| tuning.ws.connect = Duration::from_millis(200)).await;
    let proxy = harness.via_proxy().await;
    proxy.stalls.store(1, Ordering::SeqCst);

    harness.server.gateway.drop_connection();
    harness.until(VoiceStatus::Resuming).await;
    harness.until(VoiceStatus::Ready).await;
    harness.server.collect().await;

    assert_eq!(proxy.stalls.load(Ordering::SeqCst), 0);
    assert_eq!(harness.server.count(op::RESUME), 1);
    assert_eq!(harness.joins(CHANNEL), 1);
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
async fn a_stopped_lane_ends_its_connection_and_spares_the_others() {
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
    harness.connect();
    let (info, media) = harness
        .voice
        .with_connection(guild(), |handle| {
            (Arc::clone(&handle.info), Arc::clone(&handle.media))
        })
        .unwrap();
    let task: JoinHandle<Ending> = tokio::spawn(async { panic!("boom") });

    supervise(
        task,
        Arc::clone(&harness.voice.shared),
        info,
        Arc::clone(&media),
    )
    .await;
    let (reason, _, detail) = harness.ended().await;

    assert_eq!(reason, DisconnectReason::ProtocolError);
    assert_eq!(detail, "internal error: boom");
    assert_eq!(harness.leaves(), 1);
    assert!(media.closed.load(Ordering::Acquire));
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
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn each_change_of_the_mute_suppress_and_deaf_flags_is_reported_once() {
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
    let deafened = r#","mute":true,"suppress":true,"deaf":true"#;
    harness.offer_state(guild(), Some(CHANNEL), deafened);
    let deaf = harness
        .wait_for(|delivery| match delivery {
            Delivery::VoiceDeaf { deaf, .. } => Some(*deaf),
            _ => None,
        })
        .await;
    harness.offer_state(guild(), Some(CHANNEL), deafened);
    harness.server.gateway.send(
        op::SPEAKING,
        json!({"user_id": "9", "ssrc": 90, "speaking": 1}),
    );
    harness.speaking_from(90).await;

    assert!(deaf);
    assert_eq!(
        harness.count(|delivery| matches!(delivery, Delivery::VoiceSelf { .. })),
        3
    );
    assert_eq!(
        harness.count(|delivery| matches!(delivery, Delivery::VoiceDeaf { .. })),
        1
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

    async fn rejoined(&mut self, call: &mut Call, members: usize, executed: usize) {
        self.pump(call, |harness, call| {
            call.fake.members().len() == members
                && harness.executed() > executed
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
    let mut harness = Harness::dave().await;
    let mut call = dave_call(CHANNEL, &[]);
    harness.in_call(&mut call).await;
    let alone = harness.gate();
    call.fake.hold_transitions = true;

    call.join(user(LATE_PEER));
    harness
        .pump(&mut call, |harness, _| {
            transition_readies(&harness.server).len() == 1
        })
        .await;
    let forming = harness.gate();
    let sink = harness.opus_sink();
    let packets = opus(2);
    for packet in &packets {
        sink.write(packet).unwrap();
    }
    harness.tick(2);
    let released = call.fake.release_transitions();
    call.enqueue(released);
    harness.encrypting(&mut call).await;
    let executed = harness.wait_for(dave_states).await;
    harness.tick(3);
    let arrived = harness.server.sfu.wait_packets(3, WAIT);

    assert_eq!((alone, forming), (SendMode::Discard, SendMode::Hold));
    assert_eq!(arrived.len(), 3);
    for (packet, sent) in arrived.iter().zip(&packets) {
        assert_eq!(
            hear(&mut call, LATE_PEER, &packet.payload),
            Some(sent.to_vec())
        );
    }
    assert_eq!(executed.0, 1);
    assert_eq!(executed.1, Some(1));
    assert_ne!(executed.2, 0);
    assert!(harness.voice.dave(guild()).unwrap().privacy_code.is_some());
    assert_eq!(
        harness
            .voice
            .verification_code(guild(), user(LATE_PEER))
            .unwrap(),
        dave::verification_code(call.dave(user(LATE_PEER)).fingerprint_pair(bot()).unwrap())
            .unwrap()
    );
    assert_eq!(call.fake.leaves()[0].1, bot());
    let statuses: Vec<VoiceStatus> = harness.statuses().iter().map(|state| state.0).collect();
    assert_eq!(statuses, [VoiceStatus::Connecting, VoiceStatus::Ready]);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn too_many_dave_recoveries_end_the_connection() {
    let mut harness = Harness::dave().await;
    let mut call = dave_call(CHANNEL, &[]);
    harness.in_call(&mut call).await;

    for transition_id in 1..=u16::try_from(RECOVERY_LIMIT).unwrap() + 1 {
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
    let released = call.fake.release_transitions();
    call.enqueue(released);
    harness.encrypting(&mut call).await;
    harness.tick(3);
    let arrived = harness.server.sfu.wait_packets(before + 3, WAIT);

    assert_eq!(invalid_commit_welcomes(&harness.server), [9]);
    assert_eq!(rejoining, SendMode::Hold);
    assert_eq!(arrived.len(), before + 3);
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
        .pump(&mut call, |harness, _| harness.executed() == 2)
        .await;
    let executed = harness.wait_for(dave_states).await;
    harness.server.collect().await;

    assert_eq!(harness.server.count(op::RESUME), 2);
    assert_eq!(executed.1, Some(2));
    assert!(invalid_commit_welcomes(&harness.server).is_empty());
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
    let ssrcs = |harness: &Harness| harness.voice.ssrcs(guild()).unwrap();
    assert_eq!(ssrcs(&harness), [(55, user(LATE_PEER))]);
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

    let sink = harness.opus_sink();
    for packet in opus(3) {
        sink.write(&packet).unwrap();
    }
    harness.tick(3);
    let drained = queued_frames(&sink);
    sink.close();
    call.join(user(THIRD_PEER));
    harness.encrypting(&mut call).await;
    harness.assert_heard(&mut call, &[THIRD_PEER]);

    assert_eq!(joined, [user(LATE_PEER)]);
    assert_eq!(left, user(LATE_PEER));
    assert!(ssrcs(&harness).is_empty());
    assert_eq!(drained, 0);
    assert_eq!(harness.server.sfu.packets().len(), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn malformed_dave_messages_never_break_the_connection() {
    let mut harness = Harness::dave().await;
    let mut call = harness.talking_with(&[LATE_PEER]).await;
    let executed = harness.executed();
    let commits = harness.server.count(op::DAVE_MLS_COMMIT_WELCOME);

    let junk = Bytes::from_static(b"not mls at all");
    harness
        .server
        .send_dave(DaveOp::ExternalSender(junk.clone()));
    for operation in [ProposalsOp::Append, ProposalsOp::Revoke] {
        harness.server.send_dave(DaveOp::Proposals {
            operation,
            data: junk.clone(),
        });
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
        .pump(&mut call, |harness, _| {
            harness.server.count(op::DAVE_MLS_KEY_PACKAGE) == 2
        })
        .await;
    harness.rejoined(&mut call, 2, executed).await;

    assert_eq!(invalid_commit_welcomes(&harness.server), [4]);
    assert_eq!(harness.server.count(op::DAVE_MLS_COMMIT_WELCOME), commits);
    assert_eq!(harness.statuses().len(), 2);
    harness.assert_heard(&mut call, &[LATE_PEER]);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_busy_dave_lock_holds_only_its_own_lane_and_never_blocks_readers() {
    let mut harness = Harness::dave().await;
    let _call = harness.talking_with(&[LATE_PEER]).await;
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
    let media = harness.media();
    let busy = media.dave.lock();
    let pacer = Arc::clone(&harness.pacer);
    let (ticked, on_ticked) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        pacer.tick(5);
        ticked.send(()).unwrap();
    });
    on_ticked
        .recv_timeout(WAIT)
        .expect("the ticks waited for the DAVE lock");
    let dave = harness.voice.dave(guild()).unwrap();
    drop(busy);

    assert_eq!(own.stats().unwrap().held_ticks, 5);
    assert_eq!(dave.protocol_version, 1);
    assert_eq!(queued_frames(&own), 5 * 960);
    assert_eq!(second.sfu.wait_packets(5, WAIT).len(), 5);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn real_pacer_threads_send_on_a_20_ms_clock() {
    let mut harness = Harness::build(|_| {}, Some(Arc::new(PacerPool::new())), tuning()).await;
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
async fn an_acknowledged_heartbeat_restores_the_rejoin_and_resume_budgets() {
    for rejoins in [true, false] {
        let mut harness = Harness::with(|config| config.heartbeat_interval = QUICK_HEARTBEAT).await;
        harness.connected().await;

        for (round, ssrc) in (0..4).zip(90..) {
            harness.acknowledged(ssrc).await;
            if rejoins {
                harness.server.gateway.close(4006);
                harness
                    .settle(|harness| harness.joins(CHANNEL) == round + 2)
                    .await;
                harness.fresh_server().await;
            } else {
                harness.server.gateway.drop_connection();
                harness.until(VoiceStatus::Resuming).await;
            }
            harness.until(VoiceStatus::Ready).await;
        }
        harness
            .settle(|harness| {
                let (gateway, udp) = harness.voice.latency(guild()).unwrap();
                gateway.is_some() && udp.is_some()
            })
            .await;
        harness.server.collect().await;

        let expected = if rejoins { (5, 0) } else { (1, 4) };
        let budgets = (harness.joins(CHANNEL), harness.server.count(op::RESUME));
        assert_eq!(budgets, expected, "{rejoins}");
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_close_code_before_an_acknowledged_heartbeat_is_forgotten() {
    let mut harness = Harness::with(|config| config.heartbeat_interval = QUICK_HEARTBEAT).await;
    harness.connected().await;
    harness.server.gateway.close(4004);
    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;
    harness.acknowledged(90).await;

    for failure in 0..4 {
        harness.offer_server(guild(), "token", Some("http://unusable"));
        if failure < 3 {
            harness
                .settle(|harness| harness.joins(CHANNEL) == failure + 3)
                .await;
        }
    }
    let (reason, code, _) = harness.ended().await;

    assert_eq!((reason, code), (DisconnectReason::SessionInvalid, None));
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
    assert!(matches!(kept.read(), Err(CoreError::SourceClosed(_))));
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
    voice.owner = Owner::foreign();

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
    let harness = Harness::start().await;
    harness.connect();

    let first = harness.sink(pcm(), StallPolicy::Hold).id();
    let source = harness.voice.open_source(tone_file()).unwrap().id();
    let last = harness.sink(pcm(), StallPolicy::Hold).id();

    assert!(first != source && source != last && first != last);
}

fn receiving(format: ReceiveFormat) -> ReceiveOptions {
    ReceiveOptions {
        format,
        users: None,
        exclude: HashSet::new(),
        include_self: false,
        buffer: Duration::from_secs(10),
        jitter: Duration::ZERO,
        markers: false,
        overflow: Overflow::DropOldest,
        stall: ReceiveStall::Wait,
    }
}

fn frames(items: &[Item]) -> Vec<(Option<UserId>, Bytes)> {
    items
        .iter()
        .filter_map(|item| match item {
            Item::Frame { user, data, .. } => Some((*user, data.clone())),
            _ => None,
        })
        .collect()
}

fn spoken(peer: u64, packets: &[Bytes]) -> Vec<(Option<UserId>, Bytes)> {
    packets
        .iter()
        .map(|packet| (Some(user(peer)), packet.clone()))
        .collect()
}

impl Harness {
    fn receiver(&self, options: ReceiveOptions) -> Arc<Receiver> {
        self.voice.open_receiver(guild(), options).unwrap()
    }

    async fn speaking(&mut self, peer: u64, ssrc: u32) {
        self.server.gateway.send(
            op::SPEAKING,
            json!({"user_id": peer.to_string(), "ssrc": ssrc, "speaking": 1}),
        );
        self.speaking_from(ssrc).await;
    }

    fn say(&self, ssrc: u32, sequence: u16, payload: &[u8]) {
        self.server.sfu.send(
            ssrc,
            sequence,
            u32::from(sequence) * 960,
            payload,
            &Sealing::default(),
        );
    }

    fn talk(&self, call: &mut Call, peer: u64, ssrc: u32, sequence: u16, opus: &[u8]) {
        let frame = call.speak(user(peer), opus);
        self.say(ssrc, sequence, &frame);
    }

    async fn hear(&self, receiver: &Receiver, count: usize) -> Vec<Item> {
        let deadline = Instant::now() + WAIT;
        let mut items = Vec::new();
        while frames(&items).len() < count {
            assert!(
                Instant::now() < deadline,
                "only {} of {count} frames arrived: {items:?}",
                frames(&items).len()
            );
            self.tick(1);
            if let ReceiveRead::Items(batch) = receiver.read(256).unwrap() {
                items.extend(batch.into_iter().map(|(_, item)| item));
            }
            sleep(Duration::from_millis(2)).await;
        }
        items
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_peer_heard_before_its_op_5_is_received_under_its_user_id() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let receiver = harness.receiver(receiving(ReceiveFormat::Opus));
    let packets = opus(10);

    for (sequence, packet) in (0..).zip(&packets) {
        harness.say(70, sequence, packet);
    }
    harness
        .settle(|harness| harness.media().arrivals.lock().len() == 10)
        .await;
    harness.tick(2);
    harness.speaking(EARLY_PEER, 70).await;
    let heard = harness.hear(&receiver, 10).await;

    assert_eq!(frames(&heard), spoken(EARLY_PEER, &packets));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_reassigned_ssrc_keeps_its_new_owner_when_op_5_repeats() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let receiver = harness.receiver(receiving(ReceiveFormat::Opus));
    let packets = opus(1);
    harness.speaking(EARLY_PEER, 70).await;
    harness.tick(1);

    harness.speaking(LATE_PEER, 70).await;
    harness.say(70, 0, &packets[0]);
    harness
        .settle(|harness| harness.media().arrivals.lock().len() == 1)
        .await;
    harness.speaking(LATE_PEER, 70).await;
    let heard = harness.hear(&receiver, 1).await;

    assert_eq!(frames(&heard), spoken(LATE_PEER, &packets));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn dave_audio_from_a_peer_decrypts_across_a_member_joining() {
    let mut harness = Harness::dave().await;
    let mut call = harness.talking_with(&[LATE_PEER]).await;
    harness.speaking(LATE_PEER, 55).await;
    let receiver = harness.receiver(ReceiveOptions {
        users: Some(HashSet::from([user(LATE_PEER)])),
        ..receiving(ReceiveFormat::Opus)
    });
    let packets = opus(10);

    for (sequence, packet) in (0..5).zip(&packets) {
        harness.talk(&mut call, LATE_PEER, 55, sequence, packet);
    }
    let before = harness.hear(&receiver, 5).await;
    let executed = harness.executed();
    call.join(user(THIRD_PEER));
    harness.rejoined(&mut call, 3, executed).await;
    for (sequence, packet) in (5..).zip(&packets[5..]) {
        harness.talk(&mut call, LATE_PEER, 55, sequence, packet);
    }
    let after = harness.hear(&receiver, 5).await;
    harness.server.collect().await;

    assert_eq!(
        frames(&[before, after].concat()),
        spoken(LATE_PEER, &packets)
    );
    assert!(invalid_commit_welcomes(&harness.server).is_empty());
    assert_eq!(receiver.stats().unwrap().lost, 0);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_user_who_rejoins_with_a_new_ssrc_keeps_their_stream() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let receiver = harness.receiver(receiving(ReceiveFormat::Opus));
    let packets = opus(6);
    harness.speaking(LATE_PEER, 55).await;
    for (sequence, packet) in (0..3).zip(&packets) {
        harness.say(55, sequence, packet);
    }
    let first = harness.hear(&receiver, 3).await;

    harness.server.gateway.send(
        op::CLIENT_DISCONNECT,
        json!({"user_id": LATE_PEER.to_string()}),
    );
    harness
        .wait_for(|delivery| matches!(delivery, Delivery::VoiceClientLeft { .. }).then_some(()))
        .await;
    harness.server.gateway.send(
        op::CLIENTS_CONNECT,
        json!({"user_ids": [LATE_PEER.to_string()]}),
    );
    harness.speaking(LATE_PEER, 56).await;
    for (sequence, packet) in (40_000..).zip(&packets[3..]) {
        harness.say(56, sequence, packet);
    }
    let second = harness.hear(&receiver, 3).await;

    assert_eq!(
        frames(&[first, second].concat()),
        spoken(LATE_PEER, &packets)
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn receiving_continues_across_a_resume_and_a_server_change() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let receiver = harness.receiver(receiving(ReceiveFormat::Opus));
    let ending = harness.receiver(ReceiveOptions {
        stall: ReceiveStall::End,
        ..receiving(ReceiveFormat::Opus)
    });
    let packets = opus(6);
    harness.speaking(EARLY_PEER, 70).await;
    harness.say(70, 0, &packets[0]);
    harness.say(70, 1, &packets[1]);
    let mut heard = harness.hear(&receiver, 2).await;

    harness.server.gateway.drop_connection();
    harness.until(VoiceStatus::Resuming).await;
    harness.until(VoiceStatus::Ready).await;
    harness.say(70, 2, &packets[2]);
    harness.say(70, 3, &packets[3]);
    heard.extend(harness.hear(&receiver, 2).await);
    let open_after_the_resume = ending.ended();
    harness.fresh_server().await;
    harness.until(VoiceStatus::Reconnecting).await;
    harness.until(VoiceStatus::Ready).await;
    harness.speaking(EARLY_PEER, 70).await;
    harness.say(70, 4, &packets[4]);
    harness.say(70, 5, &packets[5]);
    heard.extend(harness.hear(&receiver, 2).await);
    harness.server.collect().await;

    assert_eq!(frames(&heard), spoken(EARLY_PEER, &packets));
    assert_eq!(harness.server.count(op::RESUME), 1);
    assert_eq!(receiver.ended(), None);
    assert_eq!(open_after_the_resume, None);
    assert_eq!(ending.ended(), Some(Ended::Reconnecting));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn include_self_hears_the_bots_own_audio() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let receiver = harness.receiver(ReceiveOptions {
        users: Some(HashSet::new()),
        include_self: true,
        ..receiving(ReceiveFormat::Opus)
    });
    let sink = harness.opus_sink();

    let sent = harness.play(&sink, 3);
    let heard = harness.hear(&receiver, 3).await;

    let open = harness.media().open_receivers();
    receiver.close();
    harness.tick(1);

    assert_eq!(frames(&heard)[..3], spoken(u64::from(bot()), &sent));
    assert_eq!(open, 1);
    assert_eq!(harness.media().open_receivers(), 0);
    assert_eq!(
        harness.count(|delivery| matches!(delivery, Delivery::VoiceSpeech { .. })),
        0
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_peer_that_never_decrypts_makes_the_bot_ask_for_a_new_group() {
    let mut tuning = tuning();
    tuning.connection.receive = ReceiveTuning {
        decrypt_tolerance: 3,
        dave_grace: Duration::ZERO,
        ..ReceiveTuning::default()
    };
    let mut harness = Harness::build(|config| config.dave_protocol_version = 1, None, tuning).await;
    let _call = harness.talking_with(&[LATE_PEER]).await;
    harness.speaking(LATE_PEER, 55).await;
    let _receiver = harness.receiver(receiving(ReceiveFormat::Opus));

    let deadline = Instant::now() + WAIT;
    let mut sequence = 0;
    while harness.server.count(op::DAVE_MLS_INVALID_COMMIT_WELCOME) == 0 {
        assert!(Instant::now() < deadline, "no recovery was asked for");
        harness.say(55, sequence, &[0x78, 0x01, 0x02, 0x03, 0x04, 0xFA, 0xFA]);
        sequence += 1;
        harness.tick(1);
        harness.server.collect().await;
    }

    assert_eq!(harness.server.count(op::DAVE_MLS_INVALID_COMMIT_WELCOME), 1);
    assert!(
        harness
            .statuses()
            .iter()
            .all(|(status, ..)| *status != VoiceStatus::Disconnected)
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_full_receiver_never_slows_sending() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    harness.speaking(EARLY_PEER, 70).await;
    let _receiver = harness.receiver(ReceiveOptions {
        buffer: FRAME,
        ..receiving(ReceiveFormat::Opus)
    });
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 40)).unwrap();
    let packets = opus(30);

    for (sequence, packet) in (0..).zip(&packets) {
        harness.say(70, sequence, packet);
        sleep(Duration::from_millis(2)).await;
        harness.tick(1);
    }

    assert_eq!(harness.server.sfu.wait_packets(30, WAIT).len(), 30);
    assert_eq!(sink.stats().unwrap().held_ticks, 0);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_move_to_another_channel_ends_the_receivers() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    harness.speaking(EARLY_PEER, 70).await;
    let receiver = harness.receiver(receiving(ReceiveFormat::Opus));
    let packets = opus(2);
    harness.say(70, 0, &packets[0]);
    harness.say(70, 1, &packets[1]);
    let delivered = || receiver.stats().unwrap().delivered;
    let deadline = Instant::now() + WAIT;
    while delivered() < 2 {
        assert!(Instant::now() < deadline, "the frames never arrived");
        harness.tick(1);
        sleep(Duration::from_millis(2)).await;
    }

    harness.state(Some(OTHER_CHANNEL));
    harness.wait_for(moved).await;
    harness.until(VoiceStatus::Reconnecting).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 2)).unwrap();
    harness.tick(2);

    let ReceiveRead::Items(items) = receiver.read(256).unwrap() else {
        panic!("the queued frames were lost");
    };
    let items: Vec<Item> = items.into_iter().map(|(_, item)| item).collect();
    assert_eq!(frames(&items), spoken(EARLY_PEER, &packets));
    assert_eq!(
        receiver.read(256).unwrap(),
        ReceiveRead::Ended(Ended::Moved)
    );
    assert_eq!(harness.server.sfu.wait_packets(2, WAIT).len(), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn the_speech_hangover_is_checked_against_its_bounds() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let hangover = |duration| harness.voice.set_speech_hangover(guild(), duration);

    for refused in [
        FRAME.saturating_sub(Duration::from_nanos(1)),
        Duration::from_secs(10) + FRAME,
    ] {
        assert!(
            matches!(hangover(refused), Err(CoreError::InvalidRequest(_))),
            "{refused:?}"
        );
    }
    hangover(Duration::from_millis(350)).unwrap();

    assert_eq!(
        harness.media().speech_hangover(),
        Duration::from_millis(350)
    );
}

#[test]
fn a_throttle_lets_one_message_through_per_second() {
    let start = std::time::Instant::now();
    let mut throttle = Throttle::default();

    assert!(throttle.allows(start));
    assert!(!throttle.allows(start + LOG_EVERY / 2));
    assert!(throttle.allows(start + LOG_EVERY));
}
