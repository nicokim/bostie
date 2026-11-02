use std::collections::HashSet;
use std::sync::atomic::AtomicUsize;

use parking_lot::Mutex as SyncMutex;
use sonic_rs::{JsonValueTrait, Value, json};
use tokio::time::{Instant, sleep, timeout};
use tokio_websockets::Message;

use super::*;
use crate::bell::{self, BellReceiver};
use crate::voice::crypto::Mode;
use crate::voice::dave::{Outbound, SendMode};
use crate::voice::media::tests::{queued_frames, tone};
use crate::voice::proto::op;
use crate::voice::tap::{VOICE_SERVER_UPDATE, VOICE_STATE_UPDATE};
use crate::voice::testkit::gateway::{ClientMessage, Config, FakeVoiceGateway, Outgoing};
use crate::voice::testkit::mls::{Call, Signal};
use crate::voice::testkit::pacer::ManualPacer;
use crate::voice::testkit::sfu::Sfu;
use crate::voice::types::{
    Application, DaveOp, FRAME, FRAME_SAMPLES, Overflow, PcmFormat, ProposalsOp, ReceiveFormat,
    ReceiveStall, ReceiveTuning, SampleFormat, SinkFormat, StallPolicy,
};
use crate::voice::ws::Timeouts;

mod e2ee;
mod lifecycle;
mod moves;
mod reception;
mod recovery;
mod signaling;

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
        http_retry: Duration::ZERO,
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

fn state_of(delivery: &Delivery) -> Option<State> {
    match delivery {
        Delivery::VoiceState {
            guild: got,
            status,
            reason,
            code,
            detail,
        } if *got == guild() => Some((*status, *reason, *code, detail.to_string())),
        _ => None,
    }
}

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
        self.wait_for(state_of).await
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
        self.deliveries.iter().filter_map(state_of).collect()
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
        let latest = self.deliveries.iter().rev().find_map(state_of);
        latest.is_some_and(|state| state.0 == VoiceStatus::Ready)
    }

    async fn encrypting(&mut self, call: &mut Call) {
        self.pump(call, |harness, _| {
            harness.ready() && harness.gate() == SendMode::Encrypt
        })
        .await;
    }

    async fn speaking_from(&mut self, ssrc: u32) {
        self.wait_for(|delivery| (speaking_ssrc(delivery) == Some(ssrc)).then_some(()))
            .await;
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

    fn receiver(&self, options: ReceiveOptions) -> Arc<Receiver> {
        self.voice.open_receiver(guild(), options).unwrap()
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

async fn closed_with(code: u16) -> Harness {
    let mut harness = Harness::start().await;
    harness.connected().await;
    harness.server.gateway.close(code);
    harness
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
