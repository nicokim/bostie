mod backoff;
mod bell;
mod error;
pub(crate) mod gateway;
mod ids;
pub(crate) mod inbox;
mod latency;
mod proxy;
pub(crate) mod rest;
mod runtime;
mod signature;
mod tasks;
#[cfg(test)]
mod testkit;
pub mod voice;

use std::collections::HashMap;
use std::num::NonZeroU16;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Weak};

use parking_lot::Mutex;
use reqwest::Url;

pub use crate::bell::{BellReceiver, RawBellHandle};
pub use crate::error::{CoreError, describe};
pub use crate::gateway::{Gateway, IdentifyLimit, SavedSession};
pub use crate::ids::{ChannelId, GuildId, Handle, ShardId, Ticket, UserId};
pub use crate::inbox::{Delivery, FailureKind, LogSink};
pub use crate::rest::{Auth, HttpSettings, RestRequest};
pub use crate::signature::verify_signature;

use crate::inbox::Inbox;
use crate::proxy::Proxy;
use crate::rest::Rest;
use crate::tasks::{PendingTicket, TaskTable};
use crate::voice::call::tap::GatewayTap;
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
    next_ticket: AtomicU64,
    runtime: tokio::runtime::Handle,
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
            let tap = Arc::new(GatewayTap::default());
            let gateway = Arc::new(Gateway::new(
                runtime.clone(),
                Arc::clone(&inbox),
                auth.bot_token().unwrap_or_default(),
                Arc::downgrade(&rest),
                Arc::clone(&tap),
                proxy.clone(),
            )?);
            let voice = Voice::new(
                runtime.clone(),
                Arc::clone(&inbox),
                tap,
                gateway_send(Arc::downgrade(&gateway)),
                proxy,
            )?;
            (rest, gateway, voice)
        };
        let core = Self {
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
        self.submit(move |rest, ticket| async move { rest.execute(ticket, request).await })
    }

    pub fn download(&self, url: &str) -> Result<Ticket, CoreError> {
        let url = Url::parse(url)
            .ok()
            .filter(|url| matches!(url.scheme(), "http" | "https"))
            .ok_or_else(|| {
                CoreError::InvalidRequest("a download needs an http or https URL".to_owned())
            })?;
        self.submit(move |rest, ticket| async move { rest.download(ticket, url).await })
    }

    fn submit<F, Fut>(&self, work: F) -> Result<Ticket, CoreError>
    where
        F: FnOnce(Arc<Rest>, Ticket) -> Fut,
        Fut: Future<Output = Delivery> + Send + 'static,
    {
        runtime::check_owner()?;
        let ticket = Ticket::from(self.next_ticket.fetch_add(1, Ordering::Relaxed));

        let mut table = self.tasks.lock();
        let Some(rest) = table.rest.clone() else {
            return Err(CoreError::Closed);
        };
        let pending = PendingTicket::new(ticket, Arc::clone(&self.inbox), Arc::clone(&self.tasks));
        let work = work(rest, ticket);
        let task = self.runtime.spawn(async move {
            pending.resolve(work.await);
        });
        table.running.insert(ticket, task.abort_handle());
        Ok(ticket)
    }

    pub fn cancel(&self, ticket: Ticket) -> Result<(), CoreError> {
        runtime::check_owner()?;
        let task = self.tasks.lock().running.remove(&ticket);
        if let Some(task) = task {
            task.abort();
        }
        Ok(())
    }

    pub fn gateway(&self) -> Result<&Gateway, CoreError> {
        runtime::check_owner()?;
        Ok(&self.gateway)
    }

    pub fn voice(&self) -> Result<&Voice, CoreError> {
        runtime::check_owner()?;
        Ok(&self.voice)
    }

    pub fn log_sink(&self) -> LogSink {
        LogSink::new(Arc::downgrade(&self.inbox))
    }

    pub fn drain(&self) -> Result<Vec<Delivery>, CoreError> {
        runtime::check_owner()?;
        Ok(self.inbox.drain())
    }

    pub fn close(&self) -> Result<(), CoreError> {
        runtime::check_owner()?;
        self.shut_down();
        Ok(())
    }

    fn shut_down(&self) {
        self.inbox.close_logs();
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
        if runtime::is_owner() {
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
mod tests {
    use std::io::{Read, Write};
    use std::sync::mpsc;
    use std::time::Duration;

    use bytes::Bytes;

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

    fn serve_once(response: &'static [u8]) -> String {
        let address = testkit::serve(move |_, stream| stream.write_all(response).unwrap());
        format!("http://{address}/api/v10")
    }

    struct Silent {
        url: String,
        requested: mpsc::Receiver<()>,
        disconnected: mpsc::Receiver<()>,
    }

    fn serve_silently() -> Silent {
        let (requested, on_request) = mpsc::channel();
        let (disconnected, on_disconnect) = mpsc::channel();
        let address = testkit::serve(move |_, stream| {
            requested.send(()).unwrap();
            drop(stream.read_to_end(&mut Vec::new()));
            disconnected.send(()).unwrap();
        });
        Silent {
            url: format!("http://{address}/api/v10"),
            requested: on_request,
            disconnected: on_disconnect,
        }
    }

    fn me() -> RestRequest {
        RestRequest::get("/users/@me").unwrap()
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
            core.gateway()
                .unwrap()
                .start(ShardId::from(0), "ws://127.0.0.1:1", b"{}", None),
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

        assert!(matches!(source.read(), Err(CoreError::SourceClosed(_))));
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
