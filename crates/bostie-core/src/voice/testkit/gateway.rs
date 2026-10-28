use std::net::SocketAddr;
use std::time::Duration;

use bytes::Bytes;
use futures_util::{SinkExt, StreamExt};
use sonic_rs::{JsonValueTrait, Value, json};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{mpsc, oneshot};
use tokio::time::timeout;
use tokio_websockets::{CloseCode, Message, ServerBuilder, WebSocketStream};

use crate::ids::{GuildId, UserId};
use crate::voice::proto::op;
use crate::voice::types::VOICE_GATEWAY_VERSION;

const WAIT: Duration = Duration::from_secs(5);
const FAILED_TO_DECODE: u16 = 4002;
const AUTHENTICATION_FAILED: u16 = 4004;
const SESSION_NO_LONGER_VALID: u16 = 4006;
const UNKNOWN_PROTOCOL: u16 = 4012;
const UNKNOWN_ENCRYPTION_MODE: u16 = 4016;
const DAVE_REQUIRED: u16 = 4017;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HelloAt {
    Connect,
    AfterReady,
}

#[derive(Debug, Clone)]
pub enum Outgoing {
    Json { op: u8, d: Value },
    Binary { op: u8, payload: Bytes },
}

#[derive(Debug, Clone)]
pub struct Config {
    pub server_id: GuildId,
    pub user_id: UserId,
    pub session_id: String,
    pub token: String,
    pub heartbeat_interval: Duration,
    pub hello: HelloAt,
    pub ssrc: u32,
    pub udp: SocketAddr,
    pub modes: Vec<String>,
    pub session_mode: Option<String>,
    pub secret_key: [u8; 32],
    pub dave_protocol_version: u16,
    pub early: Vec<Outgoing>,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            server_id: GuildId::from(1_335_172_792_320_131_073),
            user_id: UserId::from(1_329_027_298_099_331_073),
            session_id: "a9f3c2e1d0b84f6e9c7d5b3a1f0e2d4c".to_owned(),
            token: "voice-token".to_owned(),
            heartbeat_interval: Duration::from_millis(13_750),
            hello: HelloAt::Connect,
            ssrc: 3_456_789_012,
            udp: SocketAddr::from(([127, 0, 0, 1], 9)),
            modes: vec![
                "aead_aes256_gcm_rtpsize".to_owned(),
                "aead_xchacha20_poly1305_rtpsize".to_owned(),
            ],
            session_mode: None,
            secret_key: [7; 32],
            dave_protocol_version: 1,
            early: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum ClientMessage {
    Connected { query: Option<String> },
    Json { op: u64, d: Value },
    Binary { op: u8, payload: Bytes },
    Garbled(Bytes),
    Close(u16),
    Disconnected,
}

impl ClientMessage {
    pub fn op(&self) -> Option<u64> {
        match self {
            Self::Json { op, .. } => Some(*op),
            Self::Binary { op, .. } => Some(u64::from(*op)),
            _ => None,
        }
    }
}

enum Control {
    Send(Outgoing),
    SendRaw(Message),
    Close(u16),
    DropConnection,
    HoldAcks,
    InvalidateSession,
    Configure(Box<dyn FnOnce(&mut Config) + Send>),
    Applied(oneshot::Sender<()>),
}

pub struct FakeVoiceGateway {
    address: SocketAddr,
    controls: mpsc::UnboundedSender<Control>,
    messages: mpsc::UnboundedReceiver<ClientMessage>,
}

impl FakeVoiceGateway {
    pub async fn start(config: Config) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let (controls, control_receiver) = mpsc::unbounded_channel();
        let (recorder, messages) = mpsc::unbounded_channel();
        let server = Server {
            config,
            listener,
            recorder,
            connection: None,
            session: None,
            sent: Vec::new(),
            seq: 0,
        };
        tokio::spawn(server.run(control_receiver));
        Self {
            address,
            controls,
            messages,
        }
    }

    pub fn endpoint(&self) -> String {
        format!("ws://{}", self.address)
    }

    pub fn send(&self, op: u8, d: Value) {
        self.control(Control::Send(Outgoing::Json { op, d }));
    }

    pub fn send_binary(&self, op: u8, payload: &[u8]) {
        self.control(Control::Send(Outgoing::Binary {
            op,
            payload: Bytes::copy_from_slice(payload),
        }));
    }

    pub fn send_raw(&self, message: Message) {
        self.control(Control::SendRaw(message));
    }

    pub fn close(&self, code: u16) {
        self.control(Control::Close(code));
    }

    pub fn drop_connection(&self) {
        self.control(Control::DropConnection);
    }

    pub async fn hold_acks(&self) {
        self.apply(Control::HoldAcks).await;
    }

    pub async fn invalidate_session(&self) {
        self.apply(Control::InvalidateSession).await;
    }

    pub async fn configure(&self, change: impl FnOnce(&mut Config) + Send + 'static) {
        self.apply(Control::Configure(Box::new(change))).await;
    }

    async fn apply(&self, control: Control) {
        let (applied, done) = oneshot::channel();
        self.control(control);
        self.control(Control::Applied(applied));
        timeout(WAIT, done)
            .await
            .expect("the fake voice gateway did not apply a control")
            .expect("the fake voice gateway stopped");
    }

    pub async fn next(&mut self) -> ClientMessage {
        timeout(WAIT, self.messages.recv())
            .await
            .expect("no client message within the wait")
            .expect("the fake voice gateway stopped")
    }

    pub async fn next_op(&mut self, op: u8) -> ClientMessage {
        loop {
            let message = self.next().await;
            if message.op() == Some(u64::from(op)) {
                return message;
            }
        }
    }

    fn control(&self, control: Control) {
        assert!(
            self.controls.send(control).is_ok(),
            "the fake voice gateway stopped"
        );
    }
}

struct Session {
    session_id: String,
    token: String,
}

struct Connection {
    socket: WebSocketStream<TcpStream>,
    attached: bool,
    hold_acks: bool,
    closing: bool,
}

struct Server {
    config: Config,
    listener: TcpListener,
    recorder: mpsc::UnboundedSender<ClientMessage>,
    connection: Option<Connection>,
    session: Option<Session>,
    sent: Vec<(u16, Message)>,
    seq: u16,
}

impl Server {
    async fn run(mut self, mut controls: mpsc::UnboundedReceiver<Control>) {
        loop {
            tokio::select! {
                accepted = self.listener.accept() => {
                    if let Ok((stream, _)) = accepted {
                        self.open(stream).await;
                    }
                }
                control = controls.recv() => match control {
                    Some(control) => self.apply(control).await,
                    None => return,
                },
                message = next_message(&mut self.connection) => self.on_message(message).await,
            }
        }
    }

    async fn open(&mut self, stream: TcpStream) {
        let Ok((request, socket)) = ServerBuilder::new().accept(stream).await else {
            return;
        };
        self.end_connection();
        self.connection = Some(Connection {
            socket,
            attached: false,
            hold_acks: false,
            closing: false,
        });
        self.record(ClientMessage::Connected {
            query: request.uri().query().map(str::to_owned),
        });
        if self.config.hello == HelloAt::Connect {
            self.send_now(self.hello()).await;
        }
    }

    async fn apply(&mut self, control: Control) {
        match control {
            Control::Send(outgoing) => self.send_sequenced(outgoing).await,
            Control::SendRaw(message) => self.send_now(message).await,
            Control::Close(code) => self.close_connection(code).await,
            Control::DropConnection => self.end_connection(),
            Control::HoldAcks => {
                if let Some(connection) = &mut self.connection {
                    connection.hold_acks = true;
                }
            }
            Control::InvalidateSession => self.session = None,
            Control::Configure(change) => change(&mut self.config),
            Control::Applied(applied) => {
                let _ = applied.send(());
            }
        }
    }

    async fn on_message(&mut self, message: Option<Result<Message, tokio_websockets::Error>>) {
        let Some(Ok(message)) = message else {
            self.end_connection();
            return;
        };
        if let Some((code, _)) = message.as_close() {
            if !self
                .connection
                .as_ref()
                .is_some_and(|connection| connection.closing)
            {
                self.record(ClientMessage::Close(code.into()));
            }
            return;
        }
        let is_text = message.is_text();
        let payload = Bytes::from(message.into_payload());
        if !is_text {
            match payload.split_first() {
                Some((&op, _)) => self.record(ClientMessage::Binary {
                    op,
                    payload: payload.slice(1..),
                }),
                None => self.record(ClientMessage::Garbled(payload)),
            }
            return;
        }
        let parsed = sonic_rs::from_slice::<Value>(&payload)
            .ok()
            .and_then(|value| Some((value["op"].as_u64()?, value["d"].clone())));
        let Some((op, d)) = parsed else {
            self.record(ClientMessage::Garbled(payload));
            self.close_connection(FAILED_TO_DECODE).await;
            return;
        };
        self.record(ClientMessage::Json { op, d: d.clone() });
        match u8::try_from(op) {
            Ok(op::IDENTIFY) => self.identify(&d).await,
            Ok(op::SELECT_PROTOCOL) => self.select_protocol(&d).await,
            Ok(op::HEARTBEAT) => self.heartbeat(&d).await,
            Ok(op::RESUME) => self.resume(&d).await,
            _ => {}
        }
    }

    async fn identify(&mut self, d: &Value) {
        let text = |key: &str| d[key].as_str();
        let (Some(server_id), Some(user_id), Some(session_id), Some(token), Some(max_dave)) = (
            text("server_id"),
            text("user_id"),
            text("session_id"),
            text("token"),
            d["max_dave_protocol_version"].as_u64(),
        ) else {
            return self.close_connection(FAILED_TO_DECODE).await;
        };
        let refusal = if token != self.config.token {
            Some(AUTHENTICATION_FAILED)
        } else if server_id != self.config.server_id.to_string()
            || user_id != self.config.user_id.to_string()
            || session_id != self.config.session_id
        {
            Some(SESSION_NO_LONGER_VALID)
        } else if max_dave < 1 {
            Some(DAVE_REQUIRED)
        } else {
            None
        };
        if let Some(code) = refusal {
            return self.close_connection(code).await;
        }
        self.session = Some(Session {
            session_id: session_id.to_owned(),
            token: token.to_owned(),
        });
        self.sent.clear();
        self.seq = 0;
        self.attach();
        let ready = Outgoing::Json {
            op: op::READY,
            d: self.ready(),
        };
        self.send_sequenced(ready).await;
        if self.config.hello == HelloAt::AfterReady {
            self.send_now(self.hello()).await;
        }
        for early in self.config.early.clone() {
            self.send_sequenced(early).await;
        }
    }

    async fn select_protocol(&mut self, d: &Value) {
        if d["protocol"].as_str() != Some("udp") {
            return self.close_connection(UNKNOWN_PROTOCOL).await;
        }
        let data = &d["data"];
        let (Some(_), Some(_), Some(mode)) = (
            data["address"].as_str(),
            data["port"].as_u64(),
            data["mode"].as_str(),
        ) else {
            return self.close_connection(FAILED_TO_DECODE).await;
        };
        if !self.config.modes.iter().any(|offered| offered == mode) {
            return self.close_connection(UNKNOWN_ENCRYPTION_MODE).await;
        }
        let mode = self
            .config
            .session_mode
            .clone()
            .unwrap_or_else(|| mode.to_owned());
        let description = json!({
            "audio_codec": "opus",
            "media_session_id": "6f3c1b0d9a2e4c7f",
            "mode": mode,
            "secret_key": self.config.secret_key.to_vec(),
            "dave_protocol_version": self.config.dave_protocol_version,
        });
        self.send_sequenced(Outgoing::Json {
            op: op::SESSION_DESCRIPTION,
            d: description,
        })
        .await;
    }

    async fn heartbeat(&mut self, d: &Value) {
        let holding = self
            .connection
            .as_ref()
            .is_some_and(|connection| connection.hold_acks);
        let Some(t) = d["t"].as_u64() else {
            return;
        };
        if !holding {
            self.send_now(json_message(op::HEARTBEAT_ACK, json!({"t": t}), None))
                .await;
        }
    }

    async fn resume(&mut self, d: &Value) {
        let server_id = self.config.server_id.to_string();
        let resumable = self.session.as_ref().is_some_and(|session| {
            d["server_id"].as_str() == Some(server_id.as_str())
                && d["session_id"].as_str() == Some(session.session_id.as_str())
                && d["token"].as_str() == Some(session.token.as_str())
        });
        if !resumable {
            return self.close_connection(SESSION_NO_LONGER_VALID).await;
        }
        let seq_ack = d["seq_ack"].as_i64().unwrap_or(-1);
        self.attach();
        self.send_now(json_message(op::RESUMED, json!(null), None))
            .await;
        if self.config.hello == HelloAt::AfterReady {
            self.send_now(self.hello()).await;
        }
        let missed: Vec<Message> = self
            .sent
            .iter()
            .filter(|(seq, _)| i64::from(*seq) > seq_ack)
            .map(|(_, message)| message.clone())
            .collect();
        for message in missed {
            self.send_now(message).await;
        }
    }

    fn ready(&self) -> Value {
        json!({
            "ssrc": self.config.ssrc,
            "ip": self.config.udp.ip().to_string(),
            "port": self.config.udp.port(),
            "modes": self.config.modes.clone(),
            "experiments": ["fixed_keyframe_interval"],
            "heartbeat_interval": 1,
        })
    }

    fn hello(&self) -> Message {
        let millis = self.config.heartbeat_interval.as_secs_f64() * 1000.0;
        json_message(
            op::HELLO,
            json!({"v": VOICE_GATEWAY_VERSION, "heartbeat_interval": millis}),
            None,
        )
    }

    fn attach(&mut self) {
        if let Some(connection) = &mut self.connection {
            connection.attached = true;
        }
    }

    async fn send_sequenced(&mut self, outgoing: Outgoing) {
        self.seq = self.seq.wrapping_add(1);
        let seq = self.seq;
        let message = match outgoing {
            Outgoing::Json { op, d } => json_message(op, d, Some(seq)),
            Outgoing::Binary { op, payload } => {
                Message::binary([&seq.to_be_bytes()[..], &[op], &payload].concat())
            }
        };
        self.sent.push((seq, message.clone()));
        if self
            .connection
            .as_ref()
            .is_some_and(|connection| connection.attached)
        {
            self.send_now(message).await;
        }
    }

    async fn send_now(&mut self, message: Message) {
        let Some(connection) = &mut self.connection else {
            return;
        };
        if connection.socket.send(message).await.is_err() {
            self.end_connection();
        }
    }

    async fn close_connection(&mut self, code: u16) {
        let Some(connection) = &mut self.connection else {
            return;
        };
        connection.closing = true;
        connection.attached = false;
        let frame = Message::close(Some(CloseCode::try_from(code).unwrap()), "");
        if connection.socket.send(frame).await.is_err() {
            self.end_connection();
        }
    }

    fn end_connection(&mut self) {
        if self.connection.take().is_some() {
            self.record(ClientMessage::Disconnected);
        }
    }

    fn record(&self, message: ClientMessage) {
        let _ = self.recorder.send(message);
    }
}

async fn next_message(
    connection: &mut Option<Connection>,
) -> Option<Result<Message, tokio_websockets::Error>> {
    match connection {
        Some(connection) => connection.socket.next().await,
        None => std::future::pending().await,
    }
}

fn json_message(op: u8, d: Value, seq: Option<u16>) -> Message {
    let frame = match seq {
        Some(seq) => json!({"op": op, "d": d, "seq": seq}),
        None => json!({"op": op, "d": d}),
    };
    Message::text(frame.to_string())
}

#[cfg(test)]
mod tests {
    use std::net::IpAddr;

    use tokio_websockets::Connector;

    use super::*;
    use crate::voice::proto::{self, Frame, Inbound, Ready};
    use crate::voice::types::{DaveOp, ProposalsOp};
    use crate::voice::ws::{Received, VoiceSocket, endpoint_url};

    const PATIENCE: Duration = Duration::from_secs(1);
    const XCHACHA: &str = "aead_xchacha20_poly1305_rtpsize";

    async fn dial(gateway: &FakeVoiceGateway) -> VoiceSocket {
        let url = endpoint_url(&gateway.endpoint()).unwrap();
        VoiceSocket::dial(&url, &Connector::Plain).await.unwrap()
    }

    async fn frame(socket: &mut VoiceSocket) -> Frame {
        match timeout(WAIT, socket.receive())
            .await
            .expect("nothing received")
        {
            Received::Frame(frame) => frame,
            other => panic!("expected a frame, got {other:?}"),
        }
    }

    async fn inbound(socket: &mut VoiceSocket) -> (Option<u64>, Inbound) {
        let frame = frame(socket).await;
        (frame.seq, frame.inbound.unwrap())
    }

    async fn closed(socket: &mut VoiceSocket) -> u16 {
        loop {
            match timeout(WAIT, socket.receive()).await.expect("no close") {
                Received::Closed(code) => return code,
                Received::Frame(_) => {}
                Received::Lost(error) => panic!("lost instead of closed: {error}"),
            }
        }
    }

    async fn send(socket: &mut VoiceSocket, payload: Bytes) {
        socket.send_text(payload, PATIENCE).await.unwrap();
    }

    fn identify(config: &Config) -> Bytes {
        proto::identify(
            config.server_id,
            config.user_id,
            &config.session_id,
            &config.token,
        )
    }

    fn resume(config: &Config, seq_ack: Option<u64>) -> Bytes {
        proto::resume(config.server_id, &config.session_id, &config.token, seq_ack)
    }

    async fn hello(socket: &mut VoiceSocket) -> Duration {
        match inbound(socket).await {
            (None, Inbound::Hello { interval }) => interval,
            other => panic!("expected Hello, got {other:?}"),
        }
    }

    async fn ready(socket: &mut VoiceSocket) -> Ready {
        match inbound(socket).await {
            (Some(1), Inbound::Ready(ready)) => ready,
            other => panic!("expected Ready with seq 1, got {other:?}"),
        }
    }

    async fn identified(gateway: &FakeVoiceGateway, config: &Config) -> VoiceSocket {
        let mut socket = dial(gateway).await;
        hello(&mut socket).await;
        send(&mut socket, identify(config)).await;
        ready(&mut socket).await;
        socket
    }

    async fn skip_to_disconnected(gateway: &mut FakeVoiceGateway) {
        while gateway.next().await != ClientMessage::Disconnected {}
    }

    #[tokio::test]
    async fn identify_gets_ready_and_select_protocol_gets_the_session_description() {
        let config = Config {
            udp: SocketAddr::from(([127, 0, 0, 1], 50_013)),
            ..Config::default()
        };
        let mut gateway = FakeVoiceGateway::start(config.clone()).await;
        let mut socket = dial(&gateway).await;

        assert_eq!(hello(&mut socket).await, config.heartbeat_interval);
        send(&mut socket, identify(&config)).await;
        let ready = ready(&mut socket).await;
        send(
            &mut socket,
            proto::select_protocol(ready.ip, ready.port, XCHACHA),
        )
        .await;
        let (seq, description) = inbound(&mut socket).await;

        assert_eq!(ready.ssrc, config.ssrc);
        assert_eq!(ready.ip, IpAddr::from([127, 0, 0, 1]));
        assert_eq!(ready.port, 50_013);
        assert_eq!(
            ready.modes,
            ["aead_aes256_gcm_rtpsize", XCHACHA].map(Box::<str>::from)
        );
        assert_eq!(seq, Some(2));
        let Inbound::SessionDescription(description) = description else {
            panic!("expected a session description, got {description:?}");
        };
        assert_eq!(&*description.mode, XCHACHA);
        assert_eq!(description.secret_key, config.secret_key);
        assert_eq!(description.dave_protocol_version, 1);
        assert_eq!(
            gateway.next().await,
            ClientMessage::Connected {
                query: Some("v=8".to_owned())
            }
        );
        let ClientMessage::Json { op: 0, d } = gateway.next().await else {
            panic!("expected the identify");
        };
        assert_eq!(d["server_id"].as_str(), Some("1335172792320131073"));
        assert_eq!(d["token"].as_str(), Some("voice-token"));
        let ClientMessage::Json { op: 1, d } = gateway.next().await else {
            panic!("expected the select protocol");
        };
        assert_eq!(d["data"]["port"].as_u64(), Some(50_013));
    }

    #[tokio::test]
    async fn hello_can_follow_ready() {
        let config = Config {
            hello: HelloAt::AfterReady,
            ..Config::default()
        };
        let gateway = FakeVoiceGateway::start(config.clone()).await;
        let mut socket = dial(&gateway).await;

        send(&mut socket, identify(&config)).await;

        ready(&mut socket).await;
        assert_eq!(hello(&mut socket).await, config.heartbeat_interval);
    }

    #[tokio::test]
    async fn early_messages_arrive_between_ready_and_the_session_description() {
        let config = Config {
            early: vec![
                Outgoing::Binary {
                    op: 25,
                    payload: Bytes::from_static(&[0xEE]),
                },
                Outgoing::Json {
                    op: 11,
                    d: json!({"user_ids": ["5"]}),
                },
            ],
            ..Config::default()
        };
        let gateway = FakeVoiceGateway::start(config.clone()).await;
        let mut socket = identified(&gateway, &config).await;
        send(
            &mut socket,
            proto::select_protocol(IpAddr::from([127, 0, 0, 1]), 1, XCHACHA),
        )
        .await;

        assert_eq!(
            inbound(&mut socket).await,
            (
                Some(2),
                Inbound::Dave(DaveOp::ExternalSender(Bytes::from_static(&[0xEE])))
            )
        );
        assert_eq!(
            inbound(&mut socket).await,
            (
                Some(3),
                Inbound::ClientsConnect {
                    users: vec![UserId::from(5)]
                }
            )
        );
        assert!(matches!(
            inbound(&mut socket).await,
            (Some(4), Inbound::SessionDescription(_))
        ));
    }

    #[tokio::test]
    async fn scripted_sends_carry_the_next_seq_in_either_framing() {
        let config = Config::default();
        let gateway = FakeVoiceGateway::start(config.clone()).await;
        let mut socket = identified(&gateway, &config).await;

        gateway.send(13, json!({"user_id": "6"}));
        gateway.send_binary(27, &[1, 0xAA]);
        gateway.send_binary(29, &[0x01, 0x02, 0xBB]);

        assert_eq!(
            inbound(&mut socket).await,
            (
                Some(2),
                Inbound::ClientDisconnect {
                    user: UserId::from(6)
                }
            )
        );
        assert_eq!(
            inbound(&mut socket).await,
            (
                Some(3),
                Inbound::Dave(DaveOp::Proposals {
                    operation: ProposalsOp::Revoke,
                    data: Bytes::from_static(&[0xAA])
                })
            )
        );
        assert_eq!(
            inbound(&mut socket).await,
            (
                Some(4),
                Inbound::Dave(DaveOp::AnnounceCommit {
                    transition_id: 258,
                    commit: Bytes::from_static(&[0xBB])
                })
            )
        );
    }

    #[tokio::test]
    async fn raw_sends_go_out_as_they_are() {
        let config = Config::default();
        let gateway = FakeVoiceGateway::start(config.clone()).await;
        let mut socket = identified(&gateway, &config).await;

        gateway.send_raw(Message::binary(&[0x00][..]));

        assert_eq!(
            frame(&mut socket).await,
            Frame {
                seq: None,
                inbound: Err(proto::ProtoError::NoOpcode { len: 1 })
            }
        );
    }

    #[tokio::test]
    async fn heartbeats_are_acknowledged_with_their_t_until_acks_are_held() {
        let config = Config::default();
        let mut gateway = FakeVoiceGateway::start(config.clone()).await;
        let mut socket = identified(&gateway, &config).await;

        send(&mut socket, proto::heartbeat(42, Some(1))).await;

        assert_eq!(
            inbound(&mut socket).await,
            (None, Inbound::HeartbeatAck { t: 42 })
        );
        let ClientMessage::Json { d, .. } = gateway.next_op(3).await else {
            panic!("expected the heartbeat");
        };
        assert_eq!(d["seq_ack"].as_i64(), Some(1));

        gateway.hold_acks().await;
        send(&mut socket, proto::heartbeat(43, Some(1))).await;
        gateway.next_op(3).await;
        gateway.send(13, json!({"user_id": "6"}));

        assert!(matches!(
            inbound(&mut socket).await,
            (Some(2), Inbound::ClientDisconnect { .. })
        ));
    }

    #[tokio::test]
    async fn a_resume_replays_what_came_after_seq_ack() {
        let config = Config::default();
        let mut gateway = FakeVoiceGateway::start(config.clone()).await;
        let mut socket = identified(&gateway, &config).await;
        gateway.send(11, json!({"user_ids": ["5"]}));
        let (seq_ack, _) = inbound(&mut socket).await;

        gateway.drop_connection();
        assert!(matches!(
            timeout(WAIT, socket.receive()).await.unwrap(),
            Received::Lost(_)
        ));
        skip_to_disconnected(&mut gateway).await;
        gateway.send(13, json!({"user_id": "5"}));
        gateway.send_binary(25, &[0xCC]);
        let mut resumed = dial(&gateway).await;
        hello(&mut resumed).await;
        send(&mut resumed, resume(&config, seq_ack)).await;

        assert_eq!(seq_ack, Some(2));
        assert_eq!(inbound(&mut resumed).await, (None, Inbound::Resumed));
        assert_eq!(
            inbound(&mut resumed).await,
            (
                Some(3),
                Inbound::ClientDisconnect {
                    user: UserId::from(5)
                }
            )
        );
        assert_eq!(
            inbound(&mut resumed).await,
            (
                Some(4),
                Inbound::Dave(DaveOp::ExternalSender(Bytes::from_static(&[0xCC])))
            )
        );
        gateway.send(5, json!({"user_id": "5", "ssrc": 9, "speaking": 1}));
        assert!(matches!(
            inbound(&mut resumed).await,
            (Some(5), Inbound::Speaking { ssrc: 9, .. })
        ));
    }

    #[tokio::test]
    async fn a_resume_without_seq_ack_replays_the_whole_session() {
        let config = Config::default();
        let mut gateway = FakeVoiceGateway::start(config.clone()).await;
        let socket = identified(&gateway, &config).await;
        socket.close(crate::voice::ws::Close::Resumable).await;
        skip_to_disconnected(&mut gateway).await;

        let mut resumed = dial(&gateway).await;
        hello(&mut resumed).await;
        send(&mut resumed, resume(&config, None)).await;

        assert_eq!(inbound(&mut resumed).await, (None, Inbound::Resumed));
        ready(&mut resumed).await;
    }

    #[tokio::test]
    async fn a_resume_of_an_unknown_session_closes_with_4006() {
        let config = Config::default();
        let mut gateway = FakeVoiceGateway::start(config.clone()).await;
        let socket = identified(&gateway, &config).await;
        socket.close(crate::voice::ws::Close::Resumable).await;
        skip_to_disconnected(&mut gateway).await;
        let wrong_token = Config {
            token: "stale".to_owned(),
            ..config.clone()
        };

        let mut wrong = dial(&gateway).await;
        send(&mut wrong, resume(&wrong_token, Some(1))).await;
        assert_eq!(closed(&mut wrong).await, 4006);

        gateway.invalidate_session().await;
        let mut forgotten = dial(&gateway).await;
        send(&mut forgotten, resume(&config, Some(1))).await;
        assert_eq!(closed(&mut forgotten).await, 4006);
    }

    #[tokio::test]
    async fn identify_refusals_close_with_their_code() {
        let config = Config::default();
        let gateway = FakeVoiceGateway::start(config.clone()).await;
        let refusals = [
            (
                identify(&Config {
                    token: "wrong".to_owned(),
                    ..config.clone()
                }),
                4004,
            ),
            (
                identify(&Config {
                    session_id: "other".to_owned(),
                    ..config.clone()
                }),
                4006,
            ),
            (
                Bytes::from(
                    json!({"op": 0, "d": {
                        "server_id": config.server_id.to_string(),
                        "user_id": config.user_id.to_string(),
                        "session_id": config.session_id,
                        "token": config.token,
                        "max_dave_protocol_version": 0,
                    }})
                    .to_string(),
                ),
                4017,
            ),
            (
                Bytes::from(
                    json!({"op": 0, "d": {
                        "server_id": 1,
                        "user_id": 2,
                        "session_id": "s",
                        "token": "t",
                        "max_dave_protocol_version": 1,
                    }})
                    .to_string(),
                ),
                4002,
            ),
        ];

        for (payload, code) in refusals {
            let mut socket = dial(&gateway).await;
            send(&mut socket, payload).await;

            assert_eq!(closed(&mut socket).await, code);
        }
    }

    #[tokio::test]
    async fn unreadable_text_is_recorded_and_closed_with_4002() {
        let mut gateway = FakeVoiceGateway::start(Config::default()).await;
        let mut socket = dial(&gateway).await;

        send(&mut socket, Bytes::from_static(b"not json")).await;

        assert_eq!(closed(&mut socket).await, 4002);
        assert!(matches!(
            gateway.next().await,
            ClientMessage::Connected { .. }
        ));
        assert_eq!(
            gateway.next().await,
            ClientMessage::Garbled(Bytes::from_static(b"not json"))
        );
    }

    #[tokio::test]
    async fn select_protocol_refusals_close_with_their_code() {
        let config = Config::default();
        let gateway = FakeVoiceGateway::start(config.clone()).await;
        let unoffered =
            proto::select_protocol(IpAddr::from([127, 0, 0, 1]), 1, "xsalsa20_poly1305");
        let tcp = Bytes::from(
            json!({"op": 1, "d": {"protocol": "tcp", "data": {"address": "127.0.0.1", "port": 1, "mode": XCHACHA}}})
                .to_string(),
        );

        for (payload, code) in [(unoffered, 4016), (tcp, 4012)] {
            let mut socket = identified(&gateway, &config).await;
            send(&mut socket, payload).await;

            assert_eq!(closed(&mut socket).await, code);
        }
    }

    #[tokio::test]
    async fn the_session_description_can_announce_another_mode_than_the_selected_one() {
        let config = Config {
            session_mode: Some("aead_aes256_gcm_rtpsize".to_owned()),
            dave_protocol_version: 0,
            ..Config::default()
        };
        let gateway = FakeVoiceGateway::start(config.clone()).await;
        let mut socket = identified(&gateway, &config).await;

        send(
            &mut socket,
            proto::select_protocol(IpAddr::from([127, 0, 0, 1]), 1, XCHACHA),
        )
        .await;

        let (_, Inbound::SessionDescription(description)) = inbound(&mut socket).await else {
            panic!("expected a session description");
        };
        assert_eq!(&*description.mode, "aead_aes256_gcm_rtpsize");
        assert_eq!(description.dave_protocol_version, 0);
    }

    #[tokio::test]
    async fn configure_changes_what_the_next_identify_needs() {
        let config = Config::default();
        let gateway = FakeVoiceGateway::start(config.clone()).await;
        let fresh = Config {
            token: "fresh-token".to_owned(),
            ..config.clone()
        };

        gateway
            .configure(|config| config.token = "fresh-token".to_owned())
            .await;

        let mut stale = dial(&gateway).await;
        send(&mut stale, identify(&config)).await;
        assert_eq!(closed(&mut stale).await, 4004);
        identified(&gateway, &fresh).await;
    }

    #[tokio::test]
    async fn a_server_close_is_not_recorded_back_as_a_client_close() {
        let config = Config::default();
        let mut gateway = FakeVoiceGateway::start(config.clone()).await;
        let mut socket = identified(&gateway, &config).await;
        gateway.next_op(0).await;

        gateway.close(4014);

        assert_eq!(closed(&mut socket).await, 4014);
        assert_eq!(gateway.next().await, ClientMessage::Disconnected);
    }
}
