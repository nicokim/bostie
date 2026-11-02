mod bell;
mod error;
pub(crate) mod gateway;
mod ids;
pub(crate) mod inbox;
mod proxy;
pub(crate) mod rest;
mod runtime;
mod signature;
mod tasks;
pub mod voice;

use std::collections::HashMap;
use std::num::NonZeroU16;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Weak};
use std::time::Duration;

use bytes::Bytes;
use parking_lot::Mutex;
use tokio::runtime::Handle;

pub use crate::bell::{BellReceiver, RawBellHandle};
pub use crate::error::{CoreError, describe};
pub use crate::gateway::{IdentifyLimit, SavedSession};
pub use crate::ids::{ChannelId, GuildId, ShardId, Ticket, UserId};
pub use crate::inbox::{Delivery, FailureKind, LogSink};
pub use crate::rest::{Auth, HttpSettings, RestRequest};
pub use crate::signature::verify_signature;

use crate::gateway::Gateway;
use crate::inbox::Inbox;
use crate::proxy::Proxy;
use crate::rest::Rest;
use crate::runtime::Owner;
use crate::tasks::{PendingTicket, TaskTable};
use crate::voice::tap::GatewayTap;
use crate::voice::types::MAX_DAVE_PROTOCOL_VERSION;
use crate::voice::{GatewaySend, Voice};

pub fn versions() -> Vec<(&'static str, String)> {
    let libopus = opus::version();
    vec![
        ("bostie", env!("CARGO_PKG_VERSION").to_owned()),
        (
            "libopus",
            libopus
                .strip_prefix("libopus ")
                .unwrap_or(libopus)
                .to_owned(),
        ),
        ("davey", voice::dave::DAVEY_VERSION.to_owned()),
        ("dave_protocol", MAX_DAVE_PROTOCOL_VERSION.to_string()),
    ]
}

pub struct Core {
    owner: Owner,
    next_ticket: AtomicU64,
    runtime: Handle,
    inbox: Arc<Inbox>,
    tasks: Arc<Mutex<TaskTable>>,
    gateway: Arc<Gateway>,
    voice: Voice,
}

impl Core {
    pub fn new(
        auth: Auth<'_>,
        api_url: &str,
        global_limit: NonZeroU16,
        http: HttpSettings,
        proxy: Option<&str>,
    ) -> Result<(Self, BellReceiver), CoreError> {
        let proxy = proxy.map(Proxy::parse).transpose()?;
        let runtime = runtime::shared()?;
        let (bell, receiver) = bell::pair().map_err(CoreError::Bell)?;
        let inbox = Arc::new(Inbox::new(bell));
        let (rest, gateway, voice) = {
            let _context = runtime.enter();
            let rest = Arc::new(Rest::new(
                auth,
                api_url,
                global_limit,
                Arc::clone(&inbox),
                http,
                proxy.as_ref(),
            )?);
            let tap = Arc::new(GatewayTap::new());
            let gateway = Arc::new(Gateway::new(
                runtime.clone(),
                Arc::clone(&inbox),
                auth.bot_token().unwrap_or_default(),
                Arc::downgrade(&rest),
                Arc::clone(&tap),
                proxy,
            )?);
            let voice = Voice::new(
                runtime.clone(),
                Arc::clone(&inbox),
                tap,
                gateway_send(Arc::downgrade(&gateway)),
            )?;
            (rest, gateway, voice)
        };
        let core = Self {
            owner: Owner::current(),
            next_ticket: AtomicU64::new(1),
            runtime,
            inbox,
            tasks: Arc::new(Mutex::new(TaskTable {
                running: HashMap::new(),
                rest: Some(rest),
            })),
            gateway,
            voice,
        };
        Ok((core, receiver))
    }

    pub fn request(&self, request: RestRequest) -> Result<Ticket, CoreError> {
        self.owner.check()?;
        let ticket = Ticket::from(self.next_ticket.fetch_add(1, Ordering::Relaxed));

        let mut table = self.tasks.lock();
        let Some(rest) = table.rest.clone() else {
            return Err(CoreError::Closed);
        };
        let pending = PendingTicket::new(ticket, Arc::clone(&self.inbox), Arc::clone(&self.tasks));
        let task = self.runtime.spawn(async move {
            pending.resolve(rest.execute(ticket, request).await);
        });
        table.running.insert(ticket, task.abort_handle());
        Ok(ticket)
    }

    pub fn cancel(&self, ticket: Ticket) -> Result<(), CoreError> {
        self.owner.check()?;
        let task = self.tasks.lock().running.remove(&ticket);
        if let Some(task) = task {
            task.abort();
        }
        Ok(())
    }

    pub fn gateway_identify_limit(&self, limit: IdentifyLimit) -> Result<(), CoreError> {
        self.owner.check()?;
        self.gateway.set_identify_limit(limit);
        Ok(())
    }

    pub fn gateway_gate_identifies(&self) -> Result<(), CoreError> {
        self.owner.check()?;
        self.gateway.gate_identifies();
        Ok(())
    }

    pub fn gateway_approve_identify(&self, shard: ShardId, request: u64) -> Result<(), CoreError> {
        self.owner.check()?;
        self.gateway.approve_identify(shard, request)
    }

    pub fn gateway_start(
        &self,
        shard: ShardId,
        url: &str,
        identify: &[u8],
        saved: Option<SavedSession>,
    ) -> Result<(), CoreError> {
        self.owner.check()?;
        let table = self.tasks.lock();
        if table.rest.is_none() {
            return Err(CoreError::Closed);
        }
        self.gateway.start(shard, url, identify, saved)
    }

    pub fn gateway_send(&self, shard: ShardId, payload: Bytes) -> Result<(), CoreError> {
        self.owner.check()?;
        self.gateway.send(shard, payload)
    }

    pub fn gateway_identify(&self, shard: ShardId, identify: &[u8]) -> Result<(), CoreError> {
        self.owner.check()?;
        self.gateway.set_identify(shard, identify)
    }

    pub fn gateway_filter(&self, names: Option<Vec<String>>) -> Result<(), CoreError> {
        self.owner.check()?;
        self.gateway.set_filter(names);
        Ok(())
    }

    pub fn gateway_reconnect(&self, shard: ShardId) -> Result<(), CoreError> {
        self.owner.check()?;
        self.gateway.reconnect(shard)
    }

    pub fn gateway_close(&self, shard: ShardId) -> Result<(), CoreError> {
        self.owner.check()?;
        self.gateway.close(shard)
    }

    pub fn gateway_suspend(&self, shard: ShardId) -> Result<(), CoreError> {
        self.owner.check()?;
        self.gateway.suspend(shard)
    }

    pub fn gateway_latency(&self, shard: ShardId) -> Result<Option<Duration>, CoreError> {
        self.owner.check()?;
        self.gateway.latency(shard)
    }

    pub fn voice(&self) -> Result<&Voice, CoreError> {
        self.owner.check()?;
        Ok(&self.voice)
    }

    pub fn log_sink(&self) -> LogSink {
        LogSink::new(Arc::downgrade(&self.inbox))
    }

    pub fn drain(&self) -> Result<Vec<Delivery>, CoreError> {
        self.owner.check()?;
        Ok(self.inbox.drain())
    }

    pub fn close(&self) -> Result<(), CoreError> {
        self.owner.check()?;
        self.shut_down();
        Ok(())
    }

    fn shut_down(&self) {
        let mut table = self.tasks.lock();
        table.rest = None;
        for (_, task) in table.running.drain() {
            task.abort();
        }
        self.voice.shutdown();
        self.gateway.shutdown();
    }
}

fn gateway_send(gateway: Weak<Gateway>) -> GatewaySend {
    Arc::new(move |shard, payload| {
        gateway
            .upgrade()
            .ok_or(CoreError::Closed)?
            .send(shard, payload)
    })
}

impl Drop for Core {
    fn drop(&mut self) {
        if self.owner.is_current() {
            self.shut_down();
        }
    }
}

#[cfg(test)]
fn init_test_tracing() {
    drop(
        tracing_subscriber::fmt()
            .with_test_writer()
            .with_max_level(tracing::Level::DEBUG)
            .try_init(),
    );
}

#[cfg(test)]
fn refusing_socket() -> tokio::net::TcpSocket {
    let socket = tokio::net::TcpSocket::new_v4().unwrap();
    socket
        .bind(std::net::SocketAddr::from(([127, 0, 0, 1], 0)))
        .unwrap();
    socket
}

#[cfg(test)]
fn strict_timing() -> bool {
    std::env::var_os("BOSTIE_STRICT_TIMING").is_some_and(|value| !value.is_empty())
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::sync::mpsc;
    use std::thread;

    use super::*;
    use crate::voice::types::{
        Application, Bandwidth, ConnectRequest, EncoderSettings, SignalType, SourceSpec,
    };

    const TOKEN: &str = "token";
    const GLOBAL_LIMIT: NonZeroU16 = NonZeroU16::new(50).unwrap();
    const WAIT: Duration = Duration::from_secs(10);

    fn core(api_url: &str) -> Result<(Core, BellReceiver), CoreError> {
        Core::new(
            Auth::Bot(TOKEN),
            api_url,
            GLOBAL_LIMIT,
            HttpSettings::default(),
            None,
        )
    }

    fn read_request_head(stream: &mut TcpStream) {
        let mut head = Vec::new();
        let mut byte = [0; 1];
        while !head.ends_with(b"\r\n\r\n") {
            stream.read_exact(&mut byte).unwrap();
            head.push(byte[0]);
        }
    }

    fn serve_once(response: &'static [u8]) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            read_request_head(&mut stream);
            stream.write_all(response).unwrap();
        });
        format!("http://{address}/api/v10")
    }

    struct Silent {
        url: String,
        requested: mpsc::Receiver<()>,
        disconnected: mpsc::Receiver<()>,
    }

    fn serve_silently() -> Silent {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let (requested, on_request) = mpsc::channel();
        let (disconnected, on_disconnect) = mpsc::channel();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            read_request_head(&mut stream);
            requested.send(()).unwrap();
            let mut rest = Vec::new();
            drop(stream.read_to_end(&mut rest));
            disconnected.send(()).unwrap();
        });
        Silent {
            url: format!("http://{address}/api/v10"),
            requested: on_request,
            disconnected: on_disconnect,
        }
    }

    fn me() -> RestRequest {
        RestRequest::parse(
            "GET",
            "/users/@me",
            &HashMap::new(),
            Vec::new(),
            None,
            None,
            None,
        )
        .unwrap()
    }

    fn wait_for_deliveries(core: &Core, receiver: &BellReceiver) -> Vec<Delivery> {
        let mut stream = receiver.stream();
        stream.set_read_timeout(Some(WAIT)).unwrap();
        stream.read_exact(&mut [0; 1]).unwrap();
        core.drain().unwrap()
    }

    #[test]
    fn delivers_an_http_response() {
        init_test_tracing();
        let url = serve_once(b"HTTP/1.1 201 Created\r\nContent-Length: 5\r\n\r\nhello");
        let (core, receiver) = core(&url).unwrap();

        let ticket = core.request(me()).unwrap();
        let deliveries = wait_for_deliveries(&core, &receiver);

        let [
            Delivery::Response {
                ticket: got,
                status,
                body,
            },
        ] = deliveries.as_slice()
        else {
            panic!("unexpected deliveries: {deliveries:?}");
        };
        assert_eq!(*got, ticket);
        assert_eq!(*status, 201);
        assert_eq!(&body[..], b"hello");
        assert_eq!(core.tasks.lock().running.len(), 0);
    }

    #[test]
    fn rejects_an_api_url_that_is_not_http() {
        let error = core("ws://127.0.0.1:1").err();

        assert!(
            matches!(error, Some(CoreError::InvalidRequest(_))),
            "{error:?}"
        );
    }

    #[test]
    fn cancel_drops_the_connection_without_a_message() {
        let server = serve_silently();
        let (core, receiver) = core(&server.url).unwrap();

        let ticket = core.request(me()).unwrap();
        server.requested.recv_timeout(WAIT).unwrap();
        core.cancel(ticket).unwrap();

        server.disconnected.recv_timeout(WAIT).unwrap();
        assert_eq!(core.tasks.lock().running.len(), 0);
        receiver.stream().set_nonblocking(true).unwrap();
        receiver.stream().read(&mut [0; 1]).unwrap_err();
        assert!(core.drain().unwrap().is_empty());
    }

    #[test]
    fn closed_core_rejects_work_and_aborts_running_tasks() {
        let server = serve_silently();
        let (core, _receiver) = core(&server.url).unwrap();
        core.request(me()).unwrap();
        server.requested.recv_timeout(WAIT).unwrap();

        core.close().unwrap();

        server.disconnected.recv_timeout(WAIT).unwrap();
        assert!(matches!(core.request(me()), Err(CoreError::Closed)));
        assert!(matches!(
            core.gateway_start(ShardId::from(0), "ws://127.0.0.1:1", b"{}", None),
            Err(CoreError::Closed)
        ));
    }

    fn voice_request() -> ConnectRequest {
        ConnectRequest {
            guild: GuildId::from(1),
            channel: ChannelId::from(2),
            shard: ShardId::from(0),
            user: UserId::from(3),
            self_mute: false,
            self_deaf: false,
            timeout: Duration::from_secs(5),
            server_change_delay: Duration::ZERO,
            encoder: EncoderSettings {
                bitrate: 64_000,
                application: Application::Audio,
                fec: true,
                packet_loss: 10,
                complexity: 10,
                bandwidth: Bandwidth::Auto,
                signal: SignalType::Auto,
            },
            speaking: 1,
        }
    }

    fn some_bytes() -> SourceSpec {
        SourceSpec::Bytes {
            data: Bytes::from_static(b"not audio"),
            hint: None,
        }
    }

    #[test]
    fn voice_connect_fails_without_a_running_shard() {
        let (core, _receiver) = core("http://127.0.0.1:1").unwrap();

        let joined = core.voice().unwrap().connect(&voice_request());

        assert!(
            matches!(joined, Err(CoreError::UnknownShard(shard)) if shard == ShardId::from(0)),
            "{joined:?}"
        );
    }

    #[test]
    fn closing_the_core_shuts_voice_down() {
        let (core, _receiver) = core("http://127.0.0.1:1").unwrap();
        let source = core.voice().unwrap().open_source(some_bytes()).unwrap();

        core.close().unwrap();

        assert!(source.is_closed());
        let voice = core.voice().unwrap();
        assert!(matches!(
            voice.open_source(some_bytes()),
            Err(CoreError::Closed)
        ));
        assert!(matches!(
            voice.connect(&voice_request()),
            Err(CoreError::Closed)
        ));
    }

    #[test]
    fn versions_name_bostie_libopus_davey_and_the_dave_protocol() {
        let versions: HashMap<_, _> = versions().into_iter().collect();

        assert_eq!(versions["bostie"], env!("CARGO_PKG_VERSION"));
        assert!(versions["libopus"].starts_with("1."), "{versions:?}");
        let pin = format!("davey = \"={}\"", versions["davey"]);
        assert!(include_str!("../../../Cargo.toml").contains(&pin), "{pin}");
        assert_eq!(versions["dave_protocol"], "1");
        assert_eq!(versions.len(), 4);
    }
}
