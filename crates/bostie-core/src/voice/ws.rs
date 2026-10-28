use std::time::{Duration, SystemTime, UNIX_EPOCH};

use bytes::Bytes;
use futures_util::{SinkExt, StreamExt};
use reqwest::Url;
use tokio::net::TcpStream;
use tokio::time::{Instant, timeout};
use tokio_websockets::{
    ClientBuilder, CloseCode, Connector, Limits, MaybeTlsStream, Message, WebSocketStream,
};
use tracing::debug;

use super::proto::{self, Frame, ProtoError};
use super::types::VOICE_GATEWAY_VERSION;

pub const WS_MAX_MESSAGE: usize = 1024 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct Timeouts {
    pub(crate) connect: Duration,
    pub(crate) close: Duration,
}

impl Default for Timeouts {
    fn default() -> Self {
        Self {
            connect: Duration::from_secs(10),
            close: Duration::from_secs(2),
        }
    }
}

impl Timeouts {
    pub(crate) fn patience(self, heartbeat: Option<&Heartbeat>) -> Duration {
        heartbeat.map_or(self.connect, Heartbeat::interval)
    }
}

#[derive(Debug, thiserror::Error)]
pub enum WsError {
    #[error("invalid voice endpoint {0:?}")]
    Endpoint(Box<str>),
    #[error("no connection within {0:?}")]
    ConnectTimeout(Duration),
    #[error("a write did not finish within {0:?}")]
    WriteTimeout(Duration),
    #[error("the connection ended without a close frame")]
    Ended,
    #[error("the connection was closed after a message over {WS_MAX_MESSAGE} bytes")]
    Oversized,
    #[error(transparent)]
    Socket(#[from] tokio_websockets::Error),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Close {
    Final,
    Resumable,
}

impl Close {
    pub const fn code(self) -> u16 {
        match self {
            Self::Final => 1000,
            Self::Resumable => 4000,
        }
    }
}

#[derive(Debug)]
pub enum Received {
    Frame(Frame),
    Closed(u16),
    Lost(WsError),
}

pub fn endpoint_url(endpoint: &str) -> Result<Url, WsError> {
    let parsed = if endpoint.contains("://") {
        Url::parse(endpoint)
            .ok()
            .filter(|url| matches!(url.scheme(), "ws" | "wss"))
    } else {
        Url::parse(&format!("wss://{endpoint}")).ok()
    };
    let mut url = parsed.ok_or_else(|| WsError::Endpoint(endpoint.into()))?;
    url.set_query(Some(&format!("v={VOICE_GATEWAY_VERSION}")));
    Ok(url)
}

pub struct VoiceSocket {
    stream: WebSocketStream<MaybeTlsStream<TcpStream>>,
    close_timeout: Duration,
    oversized: bool,
}

impl VoiceSocket {
    pub(crate) async fn dial(
        url: &Url,
        connector: &Connector,
        timeouts: Timeouts,
    ) -> Result<Self, WsError> {
        let builder = ClientBuilder::new()
            .uri(url.as_str())
            .map_err(|_| WsError::Endpoint(url.as_str().into()))?
            .connector(connector)
            .limits(Limits::default().max_payload_len(Some(WS_MAX_MESSAGE)));
        let (stream, _) = timeout(timeouts.connect, builder.connect())
            .await
            .map_err(|_| WsError::ConnectTimeout(timeouts.connect))??;
        Ok(Self {
            stream,
            close_timeout: timeouts.close,
            oversized: false,
        })
    }

    pub async fn send_text(&mut self, payload: Bytes, patience: Duration) -> Result<(), WsError> {
        self.send(Message::text(payload), patience).await
    }

    pub async fn send_binary(&mut self, payload: Bytes, patience: Duration) -> Result<(), WsError> {
        self.send(Message::binary(payload), patience).await
    }

    async fn send(&mut self, message: Message, patience: Duration) -> Result<(), WsError> {
        timeout(patience, self.stream.send(message))
            .await
            .map_err(|_| WsError::WriteTimeout(patience))??;
        Ok(())
    }

    pub async fn receive(&mut self) -> Received {
        if self.oversized {
            return Received::Lost(WsError::Oversized);
        }
        loop {
            let message = match self.stream.next().await {
                Some(Ok(message)) => message,
                Some(Err(tokio_websockets::Error::PayloadTooLong { len, .. })) => {
                    self.oversized = true;
                    return Received::Frame(Frame {
                        seq: None,
                        inbound: Err(ProtoError::TooLarge { len }),
                    });
                }
                Some(Err(error)) => return Received::Lost(error.into()),
                None => return Received::Lost(WsError::Ended),
            };
            if let Some((code, _)) = message.as_close() {
                let code = code.into();
                if timeout(self.close_timeout, self.stream.flush())
                    .await
                    .is_err()
                {
                    debug!(code, "the voice close echo timed out");
                }
                return Received::Closed(code);
            }
            if message.is_text() {
                return Received::Frame(proto::parse_text(message.as_payload()));
            }
            if message.is_binary() {
                return Received::Frame(proto::parse_binary(message.into_payload().into()));
            }
        }
    }

    pub async fn close(mut self, close: Close) {
        let code = close.code();
        let patience = self.close_timeout;
        let closing = async {
            let frame = Message::close(CloseCode::try_from(code).ok(), "");
            if self.stream.send(frame).await.is_ok() {
                while self.stream.next().await.is_some() {}
            }
        };
        if timeout(patience, closing).await.is_err() {
            debug!(code, "the voice close handshake timed out");
        }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct SeqAck {
    last: Option<u64>,
}

impl SeqAck {
    pub fn record(&mut self, seq: Option<u64>) {
        self.last = seq.or(self.last);
    }

    pub const fn last(self) -> Option<u64> {
        self.last
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
#[error("no heartbeat ACK within {interval:?}")]
pub struct Zombie {
    pub interval: Duration,
}

#[derive(Debug, Clone, Copy)]
struct Unanswered {
    t: u64,
    sent_at: Instant,
}

#[derive(Debug, Clone)]
pub struct Heartbeat {
    interval: Duration,
    deadline: Instant,
    unanswered: Option<Unanswered>,
}

impl Heartbeat {
    pub fn new(interval: Duration, hello_at: Instant) -> Self {
        Self {
            interval,
            deadline: hello_at + interval,
            unanswered: None,
        }
    }

    pub const fn interval(&self) -> Duration {
        self.interval
    }

    pub const fn deadline(&self) -> Instant {
        self.deadline
    }

    pub fn beat(&mut self, now: Instant) -> Result<u64, Zombie> {
        if self.unanswered.is_some() {
            return Err(Zombie {
                interval: self.interval,
            });
        }
        let t = unix_millis();
        self.unanswered = Some(Unanswered { t, sent_at: now });
        self.deadline += self.interval;
        Ok(t)
    }

    pub fn ack(&mut self, t: u64, now: Instant) -> Option<Duration> {
        let answered = self.unanswered.take_if(|beat| beat.t == t)?;
        Some(now.saturating_duration_since(answered.sent_at))
    }
}

fn unix_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |since| {
            u64::try_from(since.as_millis()).unwrap_or(u64::MAX)
        })
}

#[cfg(test)]
mod tests {
    use std::net::SocketAddr;

    use sonic_rs::JsonValueTrait;
    use tokio::net::{TcpListener, TcpSocket};
    use tokio_websockets::ServerBuilder;

    use super::*;
    use crate::voice::proto::Inbound;
    use crate::voice::testkit::gateway::{ClientMessage, Config, FakeVoiceGateway};
    use crate::voice::types::DaveOp;

    const INTERVAL: Duration = Duration::from_millis(13_750);
    const WAIT: Duration = Duration::from_secs(5);
    const TIMEOUTS: Timeouts = Timeouts {
        connect: Duration::from_millis(100),
        close: Duration::from_millis(200),
    };

    fn url(endpoint: &str) -> String {
        endpoint_url(endpoint).unwrap().to_string()
    }

    async fn dial(endpoint: &str) -> VoiceSocket {
        let url = endpoint_url(endpoint).unwrap();
        VoiceSocket::dial(&url, &Connector::Plain, TIMEOUTS)
            .await
            .unwrap()
    }

    async fn receive(socket: &mut VoiceSocket) -> Received {
        timeout(WAIT, socket.receive())
            .await
            .expect("nothing received")
    }

    async fn identified(gateway: &mut FakeVoiceGateway) -> VoiceSocket {
        let config = Config::default();
        let mut socket = dial(&gateway.endpoint()).await;
        let identify = proto::identify(
            config.server_id,
            config.user_id,
            &config.session_id,
            &config.token,
        );
        socket.send_text(identify, INTERVAL).await.unwrap();

        assert!(matches!(
            gateway.next().await,
            ClientMessage::Connected { .. }
        ));
        assert!(matches!(
            gateway.next().await,
            ClientMessage::Json { op: 0, .. }
        ));
        assert!(matches!(
            receive(&mut socket).await,
            Received::Frame(Frame {
                inbound: Ok(Inbound::Hello { .. }),
                ..
            })
        ));
        assert!(matches!(
            receive(&mut socket).await,
            Received::Frame(Frame {
                seq: Some(1),
                inbound: Ok(Inbound::Ready(_)),
            })
        ));
        socket
    }

    #[test]
    fn endpoints_without_a_scheme_get_wss_and_version_8() {
        assert_eq!(
            url("sweetwater-12345.discord.media:2048"),
            "wss://sweetwater-12345.discord.media:2048/?v=8"
        );
        assert_eq!(
            url("c-ams13-1a2b.discord.media"),
            "wss://c-ams13-1a2b.discord.media/?v=8"
        );
    }

    #[test]
    fn endpoints_keep_their_scheme_host_port_and_path() {
        assert_eq!(url("ws://127.0.0.1:5000"), "ws://127.0.0.1:5000/?v=8");
        assert_eq!(
            url("wss://voice.example:8443/gateway?v=4&x=1"),
            "wss://voice.example:8443/gateway?v=8"
        );
        assert_eq!(url("127.0.0.1:2048"), "wss://127.0.0.1:2048/?v=8");
        assert_eq!(url("[2001:db8::1]:2048"), "wss://[2001:db8::1]:2048/?v=8");
    }

    #[test]
    fn unusable_endpoints_are_rejected() {
        for endpoint in ["", "host:99999", "wss://", "a b:1", "https://voice.example"] {
            assert!(
                matches!(endpoint_url(endpoint), Err(WsError::Endpoint(_))),
                "{endpoint:?}"
            );
        }
    }

    #[test]
    fn the_first_beat_is_due_one_interval_after_hello() {
        let hello_at = Instant::now();

        let heartbeat = Heartbeat::new(INTERVAL, hello_at);

        assert_eq!(heartbeat.deadline(), hello_at + INTERVAL);
        assert_eq!(heartbeat.interval(), INTERVAL);
    }

    #[test]
    fn beats_follow_the_previous_deadline() {
        let hello_at = Instant::now();
        let mut heartbeat = Heartbeat::new(INTERVAL, hello_at);
        let late = hello_at + INTERVAL + Duration::from_millis(40);

        let t = heartbeat.beat(late).unwrap();

        assert_eq!(heartbeat.deadline(), hello_at + INTERVAL * 2);
        assert!(t > 1_700_000_000_000, "{t} is not a unix time in ms");
    }

    #[test]
    fn an_ack_with_the_beats_t_measures_the_latency() {
        let hello_at = Instant::now();
        let mut heartbeat = Heartbeat::new(INTERVAL, hello_at);
        let sent_at = heartbeat.deadline();
        let t = heartbeat.beat(sent_at).unwrap();

        assert_eq!(
            heartbeat.ack(t + 1, sent_at + Duration::from_millis(5)),
            None
        );
        assert_eq!(
            heartbeat.ack(t, sent_at + Duration::from_millis(30)),
            Some(Duration::from_millis(30))
        );
        assert_eq!(heartbeat.ack(t, sent_at + Duration::from_millis(31)), None);
        assert!(heartbeat.beat(heartbeat.deadline()).is_ok());
    }

    #[test]
    fn a_deadline_that_finds_the_beat_unanswered_is_a_zombie() {
        let hello_at = Instant::now();
        let mut heartbeat = Heartbeat::new(INTERVAL, hello_at);
        let t = heartbeat.beat(heartbeat.deadline()).unwrap();
        heartbeat.ack(t + 1, heartbeat.deadline());

        assert_eq!(
            heartbeat.beat(heartbeat.deadline()),
            Err(Zombie { interval: INTERVAL })
        );
    }

    #[test]
    fn writes_wait_one_heartbeat_interval_and_the_connect_timeout_before_hello() {
        let heartbeat = Heartbeat::new(INTERVAL, Instant::now());

        assert_eq!(TIMEOUTS.patience(None), TIMEOUTS.connect);
        assert_eq!(TIMEOUTS.patience(Some(&heartbeat)), INTERVAL);
    }

    #[test]
    fn seq_ack_is_minus_one_until_a_sequenced_message_arrives() {
        let mut seq_ack = SeqAck::default();
        seq_ack.record(proto::parse_text(br#"{"op":6,"d":{"t":1}}"#).seq);

        assert_eq!(seq_ack.last(), None);
        assert_eq!(
            proto::heartbeat(1, seq_ack.last()),
            r#"{"op":3,"d":{"t":1,"seq_ack":-1}}"#
        );
    }

    #[test]
    fn seq_ack_keeps_the_last_seq_from_json_and_binary_messages() {
        let mut seq_ack = SeqAck::default();

        seq_ack.record(proto::parse_text(br#"{"op":5,"d":{},"seq":7}"#).seq);
        assert_eq!(seq_ack.last(), Some(7));
        seq_ack.record(proto::parse_binary(Bytes::from_static(&[0x01, 0x02, 25])).seq);
        assert_eq!(seq_ack.last(), Some(258));
        seq_ack.record(proto::parse_text(br#"{"op":6,"d":{"t":1}}"#).seq);
        assert_eq!(seq_ack.last(), Some(258));
        seq_ack.record(proto::parse_binary(Bytes::from_static(&[0x00, 0x03])).seq);
        assert_eq!(seq_ack.last(), Some(3));
    }

    #[tokio::test]
    async fn dial_asks_for_voice_gateway_version_8() {
        let mut gateway = FakeVoiceGateway::start(Config::default()).await;

        let _socket = dial(&gateway.endpoint()).await;

        assert_eq!(
            gateway.next().await,
            ClientMessage::Connected {
                query: Some("v=8".to_owned())
            }
        );
    }

    #[tokio::test]
    async fn text_and_binary_messages_arrive_as_parsed_frames() {
        let mut gateway = FakeVoiceGateway::start(Config::default()).await;
        let mut socket = identified(&mut gateway).await;

        gateway.send(
            5,
            sonic_rs::json!({"user_id": "9", "ssrc": 4, "speaking": 1}),
        );
        gateway.send_binary(25, &[0xAB]);

        let Received::Frame(speaking) = receive(&mut socket).await else {
            panic!("expected a frame");
        };
        assert_eq!(speaking.seq, Some(2));
        assert!(matches!(
            speaking.inbound,
            Ok(Inbound::Speaking { ssrc: 4, .. })
        ));
        let Received::Frame(external_sender) = receive(&mut socket).await else {
            panic!("expected a frame");
        };
        assert_eq!(external_sender.seq, Some(3));
        assert_eq!(
            external_sender.inbound,
            Ok(Inbound::Dave(DaveOp::ExternalSender(Bytes::from_static(
                &[0xAB]
            ))))
        );
    }

    #[tokio::test]
    async fn sends_reach_the_server_as_text_and_binary() {
        let mut gateway = FakeVoiceGateway::start(Config::default()).await;
        let mut socket = identified(&mut gateway).await;

        socket
            .send_text(proto::speaking(1, 77), INTERVAL)
            .await
            .unwrap();
        socket
            .send_binary(proto::key_package(&[1, 2]), INTERVAL)
            .await
            .unwrap();

        let ClientMessage::Json { op: 5, d } = gateway.next().await else {
            panic!("expected op 5");
        };
        assert_eq!(d["ssrc"].as_u64(), Some(77));
        assert_eq!(
            gateway.next().await,
            ClientMessage::Binary {
                op: 26,
                payload: Bytes::from_static(&[1, 2])
            }
        );
    }

    #[tokio::test]
    async fn a_close_frame_reports_its_code_and_is_echoed() {
        let mut gateway = FakeVoiceGateway::start(Config::default()).await;
        let mut socket = identified(&mut gateway).await;

        gateway.close(4014);

        assert!(matches!(receive(&mut socket).await, Received::Closed(4014)));
        assert_eq!(gateway.next().await, ClientMessage::Disconnected);
    }

    #[tokio::test]
    async fn a_connection_dropped_without_a_close_frame_is_lost() {
        let mut gateway = FakeVoiceGateway::start(Config::default()).await;
        let mut socket = identified(&mut gateway).await;

        gateway.drop_connection();

        assert!(matches!(receive(&mut socket).await, Received::Lost(_)));
    }

    #[tokio::test]
    async fn close_sends_4000_to_keep_the_session_and_1000_to_end_it() {
        let mut gateway = FakeVoiceGateway::start(Config::default()).await;

        for (close, code) in [(Close::Resumable, 4000), (Close::Final, 1000)] {
            let socket = identified(&mut gateway).await;

            socket.close(close).await;

            assert_eq!(gateway.next().await, ClientMessage::Close(code));
            assert_eq!(gateway.next().await, ClientMessage::Disconnected);
        }
    }

    #[tokio::test]
    async fn a_message_over_the_limit_is_an_invalid_frame_and_then_a_lost_connection() {
        let mut gateway = FakeVoiceGateway::start(Config::default()).await;
        let mut socket = identified(&mut gateway).await;

        gateway.send_binary(25, &vec![0x81; WS_MAX_MESSAGE]);
        gateway.send(13, sonic_rs::json!({"user_id": "6"}));

        let Received::Frame(frame) = receive(&mut socket).await else {
            panic!("expected an invalid frame");
        };
        assert_eq!(
            frame.inbound,
            Err(ProtoError::TooLarge {
                len: WS_MAX_MESSAGE + 3
            })
        );
        assert!(matches!(
            receive(&mut socket).await,
            Received::Lost(WsError::Oversized)
        ));
    }

    #[tokio::test]
    async fn a_write_to_a_peer_that_stops_reading_gives_up_after_the_patience() {
        let socket = TcpSocket::new_v4().unwrap();
        socket.set_recv_buffer_size(4096).unwrap();
        socket.bind(SocketAddr::from(([127, 0, 0, 1], 0))).unwrap();
        let listener = socket.listen(1).unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let (_, socket) = ServerBuilder::new().accept(stream).await.unwrap();
            tokio::time::sleep(WAIT).await;
            drop(socket);
        });
        let mut client = dial(&format!("ws://{address}")).await;
        let patience = Duration::from_millis(50);
        let payload = Bytes::from(vec![b'x'; 64 * 1024]);

        let mut outcome = Ok(());
        for _ in 0..1000 {
            outcome = client.send_text(payload.clone(), patience).await;
            if outcome.is_err() {
                break;
            }
        }

        assert!(
            matches!(outcome, Err(WsError::WriteTimeout(waited)) if waited == patience),
            "{outcome:?}"
        );
        server.abort();
    }

    #[tokio::test]
    async fn a_dial_that_gets_no_upgrade_answer_times_out() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (_stream, _) = listener.accept().await.unwrap();
            tokio::time::sleep(WAIT).await;
        });
        let url = endpoint_url(&format!("ws://{address}")).unwrap();

        let dialed = VoiceSocket::dial(&url, &Connector::Plain, TIMEOUTS).await;

        assert!(matches!(
            dialed,
            Err(WsError::ConnectTimeout(waited)) if waited == TIMEOUTS.connect
        ));
        server.abort();
    }

    #[tokio::test]
    async fn a_refused_dial_is_a_socket_error() {
        let refusing = crate::refusing_socket();
        let address = refusing.local_addr().unwrap();
        let url = endpoint_url(&format!("ws://{address}")).unwrap();

        let dialed = VoiceSocket::dial(&url, &Connector::Plain, TIMEOUTS).await;

        assert!(matches!(dialed, Err(WsError::Socket(_))));
    }
}
