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
pub(crate) enum HelloAt {
    Connect,
    AfterReady,
}

#[derive(Debug, Clone)]
pub(crate) enum Outgoing {
    Json { op: u8, d: Value },
    Binary { op: u8, payload: Bytes },
}

#[derive(Debug, Clone)]
pub(crate) struct Config {
    pub(crate) server_id: GuildId,
    pub(crate) user_id: UserId,
    pub(crate) session_id: String,
    pub(crate) token: String,
    pub(crate) heartbeat_interval: Duration,
    pub(crate) hello: HelloAt,
    pub(crate) ssrc: u32,
    pub(crate) udp: SocketAddr,
    pub(crate) modes: Vec<String>,
    pub(crate) session_mode: Option<String>,
    pub(crate) secret_key: [u8; 32],
    pub(crate) dave_protocol_version: u16,
    pub(crate) early: Vec<Outgoing>,
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
pub(crate) enum ClientMessage {
    Connected { query: Option<String> },
    Json { op: u64, d: Value },
    Binary { op: u8, payload: Bytes },
    Garbled(Bytes),
    Close(u16),
    Disconnected,
}

impl ClientMessage {
    pub(crate) fn op(&self) -> Option<u64> {
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
    Configure(Box<dyn FnOnce(&mut Config) + Send>),
    Applied(oneshot::Sender<()>),
}

pub(crate) struct FakeVoiceGateway {
    address: SocketAddr,
    controls: mpsc::UnboundedSender<Control>,
    messages: mpsc::UnboundedReceiver<ClientMessage>,
}

impl FakeVoiceGateway {
    pub(crate) async fn start(config: Config) -> Self {
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

    pub(crate) fn endpoint(&self) -> String {
        format!("ws://{}", self.address)
    }

    pub(crate) fn send(&self, op: u8, d: Value) {
        self.control(Control::Send(Outgoing::Json { op, d }));
    }

    pub(crate) fn send_binary(&self, op: u8, payload: &[u8]) {
        self.control(Control::Send(Outgoing::Binary {
            op,
            payload: Bytes::copy_from_slice(payload),
        }));
    }

    pub(crate) fn send_raw(&self, message: Message) {
        self.control(Control::SendRaw(message));
    }

    pub(crate) fn close(&self, code: u16) {
        self.control(Control::Close(code));
    }

    pub(crate) fn drop_connection(&self) {
        self.control(Control::DropConnection);
    }

    pub(crate) async fn hold_acks(&self) {
        self.apply(Control::HoldAcks).await;
    }

    pub(crate) async fn configure(&self, change: impl FnOnce(&mut Config) + Send + 'static) {
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

    pub(crate) async fn next(&mut self) -> ClientMessage {
        timeout(WAIT, self.messages.recv())
            .await
            .expect("no client message within the wait")
            .expect("the fake voice gateway stopped")
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
            Control::Configure(change) => change(&mut self.config),
            Control::Applied(applied) => drop(applied.send(())),
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
            self.send_now(json_message(op::HEARTBEAT_ACK, &json!({"t": t}), None))
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
        self.send_now(json_message(op::RESUMED, &json!(null), None))
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
            &json!({"v": VOICE_GATEWAY_VERSION, "heartbeat_interval": millis}),
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
            Outgoing::Json { op, d } => json_message(op, &d, Some(seq)),
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
        drop(self.recorder.send(message));
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

fn json_message(op: u8, d: &Value, seq: Option<u16>) -> Message {
    let frame = if let Some(seq) = seq {
        json!({"op": op, "d": d, "seq": seq})
    } else {
        json!({"op": op, "d": d})
    };
    Message::text(frame.to_string())
}

#[cfg(test)]
mod tests {
    use tokio_websockets::Connector;

    use super::*;
    use crate::voice::proto;
    use crate::voice::ws::{Received, SocketTuning, VoiceSocket, endpoint_url};

    const PATIENCE: Duration = Duration::from_secs(1);

    async fn dial(gateway: &FakeVoiceGateway) -> VoiceSocket {
        let url = endpoint_url(&gateway.endpoint()).unwrap();
        VoiceSocket::dial(&url, &Connector::Plain, SocketTuning::default())
            .await
            .unwrap()
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
}
