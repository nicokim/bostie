mod bell;
mod error;
mod http;
mod inbox;
mod runtime;
mod tasks;
mod ticket;

use std::collections::HashMap;
use std::process;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use parking_lot::Mutex;
use tokio::runtime::Handle;

pub use crate::bell::{BellReceiver, RawBellHandle};
pub use crate::error::{CoreError, describe};
pub use crate::http::{HttpRequest, HttpResponse};
pub use crate::inbox::Message;
pub use crate::ticket::Ticket;

use crate::inbox::Inbox;
use crate::tasks::{PendingTicket, TaskTable};

const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const READ_TIMEOUT: Duration = Duration::from_secs(60);
const HTTP2_PING_INTERVAL: Duration = Duration::from_secs(30);

pub struct Core {
    owner: u32,
    next_ticket: AtomicU64,
    runtime: Handle,
    inbox: Arc<Inbox>,
    tasks: Arc<Mutex<TaskTable>>,
}

impl Core {
    pub fn new() -> Result<(Self, BellReceiver), CoreError> {
        let runtime = runtime::shared()?;
        let (bell, receiver) = bell::pair().map_err(CoreError::Bell)?;
        let http = {
            let _context = runtime.enter();
            reqwest::Client::builder()
                .connect_timeout(CONNECT_TIMEOUT)
                .read_timeout(READ_TIMEOUT)
                .http2_keep_alive_interval(HTTP2_PING_INTERVAL)
                .http2_keep_alive_while_idle(true)
                .build()
                .map_err(CoreError::HttpClient)?
        };
        let core = Self {
            owner: process::id(),
            next_ticket: AtomicU64::new(1),
            runtime,
            inbox: Arc::new(Inbox::new(bell)),
            tasks: Arc::new(Mutex::new(TaskTable {
                running: HashMap::new(),
                http: Some(http),
            })),
        };
        Ok((core, receiver))
    }

    pub fn submit_http(&self, request: HttpRequest) -> Result<Ticket, CoreError> {
        self.ensure_same_process()?;
        let ticket = Ticket::from(self.next_ticket.fetch_add(1, Ordering::Relaxed));

        let mut table = self.tasks.lock();
        let Some(client) = table.http.clone() else {
            return Err(CoreError::Closed);
        };
        let pending = PendingTicket::new(ticket, Arc::clone(&self.inbox), Arc::clone(&self.tasks));
        let task = self.runtime.spawn(async move {
            let message = match http::send(&client, request).await {
                Ok(response) => Message::Response { ticket, response },
                Err(error) => Message::Failure {
                    ticket,
                    detail: describe(&error.without_url()),
                },
            };
            pending.resolve(message);
        });
        table.running.insert(ticket, task.abort_handle());
        Ok(ticket)
    }

    pub fn cancel(&self, ticket: Ticket) -> Result<(), CoreError> {
        self.ensure_same_process()?;
        if let Some(task) = self.tasks.lock().running.remove(&ticket) {
            task.abort();
        }
        Ok(())
    }

    pub fn drain(&self) -> Result<Vec<Message>, CoreError> {
        self.ensure_same_process()?;
        Ok(self.inbox.drain())
    }

    pub fn inflight(&self) -> Result<usize, CoreError> {
        self.ensure_same_process()?;
        Ok(self.tasks.lock().running.len())
    }

    pub fn close(&self) -> Result<(), CoreError> {
        self.ensure_same_process()?;
        self.shut_down();
        Ok(())
    }

    fn shut_down(&self) {
        let mut table = self.tasks.lock();
        table.http = None;
        for (_, task) in table.running.drain() {
            task.abort();
        }
    }

    fn ensure_same_process(&self) -> Result<(), CoreError> {
        if process::id() == self.owner {
            Ok(())
        } else {
            Err(CoreError::Forked { owner: self.owner })
        }
    }
}

impl Drop for Core {
    fn drop(&mut self) {
        if process::id() == self.owner {
            self.shut_down();
        }
    }
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::sync::mpsc;
    use std::thread;
    use std::time::Duration;

    use super::*;

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
        format!("http://{address}/")
    }

    fn serve_silently() -> (String, mpsc::Receiver<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let (disconnected, on_disconnect) = mpsc::channel();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            read_request_head(&mut stream);
            let mut rest = Vec::new();
            let _ = stream.read_to_end(&mut rest);
            disconnected.send(()).unwrap();
        });
        (format!("http://{address}/"), on_disconnect)
    }

    fn get(url: &str) -> HttpRequest {
        HttpRequest::parse("GET", url, &[], None).unwrap()
    }

    fn wait_for_messages(core: &Core, receiver: &BellReceiver) -> Vec<Message> {
        let mut stream = receiver.stream();
        stream
            .set_read_timeout(Some(Duration::from_secs(10)))
            .unwrap();
        stream.read_exact(&mut [0; 1]).unwrap();
        core.drain().unwrap()
    }

    #[test]
    fn delivers_an_http_response() {
        let url =
            serve_once(b"HTTP/1.1 201 Created\r\nX-Test: yes\r\nContent-Length: 5\r\n\r\nhello");
        let (core, receiver) = Core::new().unwrap();

        let ticket = core.submit_http(get(&url)).unwrap();
        let messages = wait_for_messages(&core, &receiver);

        let [
            Message::Response {
                ticket: got,
                response,
            },
        ] = messages.as_slice()
        else {
            panic!("unexpected messages: {messages:?}");
        };
        assert_eq!(*got, ticket);
        assert_eq!(response.status, 201);
        assert!(
            response
                .headers
                .contains(&("x-test".to_owned(), "yes".to_owned()))
        );
        assert_eq!(&response.body[..], b"hello");
        assert_eq!(core.inflight().unwrap(), 0);
    }

    #[test]
    fn reports_connection_failures() {
        let unused_port = TcpListener::bind("127.0.0.1:0")
            .unwrap()
            .local_addr()
            .unwrap();
        let (core, receiver) = Core::new().unwrap();

        core.submit_http(get(&format!("http://{unused_port}/")))
            .unwrap();

        let messages = wait_for_messages(&core, &receiver);
        assert!(
            matches!(messages.as_slice(), [Message::Failure { .. }]),
            "{messages:?}"
        );
    }

    #[test]
    fn cancel_drops_the_connection_without_a_message() {
        let (url, on_disconnect) = serve_silently();
        let (core, receiver) = Core::new().unwrap();

        let ticket = core.submit_http(get(&url)).unwrap();
        thread::sleep(Duration::from_millis(100));
        core.cancel(ticket).unwrap();

        on_disconnect.recv_timeout(Duration::from_secs(10)).unwrap();
        assert_eq!(core.inflight().unwrap(), 0);
        receiver.stream().set_nonblocking(true).unwrap();
        assert!(receiver.stream().read(&mut [0; 1]).is_err());
        assert!(core.drain().unwrap().is_empty());
    }

    #[test]
    fn closed_core_rejects_work_and_aborts_running_tasks() {
        let (url, on_disconnect) = serve_silently();
        let (core, _receiver) = Core::new().unwrap();
        core.submit_http(get(&url)).unwrap();
        thread::sleep(Duration::from_millis(100));

        core.close().unwrap();

        on_disconnect.recv_timeout(Duration::from_secs(10)).unwrap();
        assert!(matches!(
            core.submit_http(get(&url)),
            Err(CoreError::Closed)
        ));
    }
}
