use std::io::Read;
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::sync::Arc;
use std::thread;

use tokio::io::{AsyncRead, AsyncReadExt};

const HEAD_END: &[u8] = b"\r\n\r\n";

pub(crate) struct Request {
    pub(crate) head: String,
    pub(crate) attempt: usize,
}

impl Request {
    pub(crate) fn path(&self) -> &str {
        self.head.split(' ').nth(1).unwrap_or_default()
    }
}

pub(crate) fn serve(
    answer: impl Fn(&Request, &mut TcpStream) + Send + Sync + 'static,
) -> SocketAddr {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let answer = Arc::new(answer);
    thread::spawn(move || {
        for (attempt, stream) in listener.incoming().enumerate() {
            let Ok(mut stream) = stream else { return };
            let answer = Arc::clone(&answer);
            thread::spawn(move || {
                let head = read_head(&mut stream);
                answer(&Request { head, attempt }, &mut stream);
            });
        }
    });
    address
}

pub(crate) fn read_head(stream: &mut impl Read) -> String {
    let mut head = Vec::new();
    let mut byte = [0; 1];
    while !head.ends_with(HEAD_END) && stream.read(&mut byte).unwrap_or(0) == 1 {
        head.push(byte[0]);
    }
    String::from_utf8_lossy(&head).into_owned()
}

pub(crate) async fn read_head_async(stream: &mut (impl AsyncRead + Unpin)) -> Option<String> {
    let mut head = Vec::new();
    while !head.ends_with(HEAD_END) {
        head.push(stream.read_u8().await.ok()?);
    }
    String::from_utf8(head).ok()
}
