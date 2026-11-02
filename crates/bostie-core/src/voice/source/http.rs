use std::io::{self, ErrorKind, Read, Seek, SeekFrom};
use std::sync::OnceLock;
use std::thread;
use std::time::Duration;

use bytes::Bytes;
use reqwest::header::{ACCEPT_RANGES, CONTENT_TYPE, HeaderMap, HeaderName, HeaderValue, RANGE};
use reqwest::{RequestBuilder, Response, StatusCode, Url};
use symphonia::core::io::MediaSource;
use tokio::runtime::Handle;
use tokio::sync::{mpsc, oneshot};

use crate::error::{CoreError, describe};

const USER_AGENT: &str = concat!("bostie/", env!("CARGO_PKG_VERSION"));
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const READ_TIMEOUT: Duration = Duration::from_secs(30);
const HTTP_PIECES: usize = 16;
const RESUME_ATTEMPTS: u32 = 3;

pub struct Http {
    runtime: Handle,
    client: OnceLock<reqwest::Client>,
    piece: usize,
    retry: Duration,
}

impl Http {
    pub fn new(runtime: Handle, buffer: usize, retry: Duration) -> Self {
        Self {
            runtime,
            client: OnceLock::new(),
            piece: (buffer / HTTP_PIECES).max(1),
            retry,
        }
    }

    fn client(&self) -> Result<reqwest::Client, CoreError> {
        if let Some(client) = self.client.get() {
            return Ok(client.clone());
        }
        let client = {
            let _context = self.runtime.enter();
            reqwest::Client::builder()
                .user_agent(USER_AGENT)
                .connect_timeout(CONNECT_TIMEOUT)
                .read_timeout(READ_TIMEOUT)
                .build()
                .map_err(CoreError::HttpClient)?
        };
        Ok(self.client.get_or_init(|| client).clone())
    }
}

pub(crate) struct HttpMedia {
    runtime: Handle,
    client: reqwest::Client,
    piece: usize,
    retry: Duration,
    url: Url,
    headers: HeaderMap,
    length: Option<u64>,
    ranges: bool,
    content_type: Option<String>,
    position: u64,
    failures: u32,
    body: Option<Body>,
}

struct Body {
    pieces: mpsc::Receiver<io::Result<Bytes>>,
    current: Bytes,
}

pub(crate) struct Fetcher {
    runtime: Handle,
    client: reqwest::Client,
    headers: HeaderMap,
}

impl Fetcher {
    pub(crate) fn get(&self, url: &Url) -> io::Result<Bytes> {
        let host = url.host_str().unwrap_or_default().to_owned();
        let request = self.client.get(url.clone()).headers(self.headers.clone());
        self.runtime.block_on(async {
            let response = respond(request, false, &host).await?;
            response.bytes().await.map_err(failure)
        })
    }
}

struct Head {
    length: Option<u64>,
    ranges: bool,
    content_type: Option<String>,
}

impl HttpMedia {
    pub(crate) fn new(
        http: &Http,
        url: &str,
        headers: &[(String, String)],
    ) -> Result<Self, CoreError> {
        let url = Url::parse(url)
            .ok()
            .filter(|url| matches!(url.scheme(), "http" | "https") && url.has_host())
            .ok_or_else(|| {
                CoreError::InvalidRequest("a URL source needs an http or https URL".to_owned())
            })?;
        let mut map = HeaderMap::with_capacity(headers.len());
        for (name, value) in headers {
            let Ok(header) = HeaderName::from_bytes(name.as_bytes()) else {
                return Err(CoreError::InvalidRequest(format!(
                    "invalid header name {name:?}"
                )));
            };
            let Ok(mut value) = HeaderValue::from_str(value) else {
                return Err(CoreError::InvalidRequest(format!(
                    "the value of header {name:?} is invalid"
                )));
            };
            value.set_sensitive(true);
            map.append(header, value);
        }
        Ok(Self {
            runtime: http.runtime.clone(),
            client: http.client()?,
            piece: http.piece,
            retry: http.retry,
            url,
            headers: map,
            length: None,
            ranges: false,
            content_type: None,
            position: 0,
            failures: 0,
            body: None,
        })
    }

    pub(crate) fn connect(&mut self) -> io::Result<()> {
        let head = self.request()?;
        self.length = head.length;
        self.ranges = head.ranges;
        self.content_type = head.content_type;
        Ok(())
    }

    pub(crate) fn content_type(&self) -> Option<&str> {
        self.content_type.as_deref()
    }

    pub(crate) fn extension(&self) -> Option<&str> {
        let name = self.url.path_segments()?.next_back()?;
        name.rsplit_once('.').map(|(_, extension)| extension)
    }

    pub(crate) fn is_playlist(&self) -> bool {
        self.extension()
            .is_some_and(|extension| extension.eq_ignore_ascii_case("m3u8"))
            || self.content_type().is_some_and(|mime| {
                matches!(
                    mime,
                    "application/vnd.apple.mpegurl"
                        | "application/x-mpegurl"
                        | "audio/mpegurl"
                        | "audio/x-mpegurl"
                )
            })
    }

    pub(crate) fn into_fetcher(self) -> (Fetcher, Url) {
        let fetcher = Fetcher {
            runtime: self.runtime,
            client: self.client,
            headers: self.headers,
        };
        (fetcher, self.url)
    }

    fn request(&mut self) -> io::Result<Head> {
        let ranged = self.position > 0 && self.length.is_some();
        let mut request = self
            .client
            .get(self.url.clone())
            .headers(self.headers.clone());
        if ranged {
            request = request.header(RANGE, format!("bytes={}-", self.position));
        }
        let host = self.url.host_str().unwrap_or_default().to_owned();
        let (head_sender, head) = oneshot::channel();
        let (sender, pieces) = mpsc::channel(HTTP_PIECES);
        self.body = None;
        self.runtime.spawn(stream(
            request,
            ranged,
            host,
            head_sender,
            sender,
            self.piece,
        ));
        let Ok(head) = head.blocking_recv() else {
            return Err(io::Error::other("the HTTP request stopped"));
        };
        let head = head?;
        self.body = Some(Body {
            pieces,
            current: Bytes::new(),
        });
        Ok(head)
    }

    fn at_end(&self) -> bool {
        self.length.is_some_and(|length| self.position >= length)
    }

    fn resume(&mut self, mut error: io::Error) -> io::Result<()> {
        if self.length.is_some() && !self.is_seekable() {
            return Err(error);
        }
        while self.failures < RESUME_ATTEMPTS {
            tracing::debug!(%error, position = self.position, "resuming a dropped HTTP body");
            thread::sleep(self.retry * (1 << self.failures));
            self.failures += 1;
            match self.request() {
                Ok(_) => return Ok(()),
                Err(failed) => error = failed,
            }
        }
        Err(error)
    }
}

impl Read for HttpMedia {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        if buf.is_empty() || self.at_end() {
            return Ok(0);
        }
        if self.body.is_none() {
            self.request()?;
        }
        let body = loop {
            let Some(body) = self.body.as_mut() else {
                return Ok(0);
            };
            if !body.current.is_empty() {
                break body;
            }
            match body.pieces.blocking_recv() {
                Some(Ok(piece)) => body.current = piece,
                Some(Err(error)) => self.resume(error)?,
                None => return Ok(0),
            }
        };
        let count = buf.len().min(body.current.len());
        let piece = body.current.split_to(count);
        if let Some(target) = buf.get_mut(..count) {
            target.copy_from_slice(&piece);
        }
        self.position += count as u64;
        self.failures = 0;
        Ok(count)
    }
}

impl Seek for HttpMedia {
    fn seek(&mut self, position: SeekFrom) -> io::Result<u64> {
        let target = match position {
            SeekFrom::Start(offset) => Some(offset),
            SeekFrom::Current(delta) => self.position.checked_add_signed(delta),
            SeekFrom::End(delta) => self
                .length
                .and_then(|length| length.checked_add_signed(delta)),
        }
        .ok_or_else(|| io::Error::new(ErrorKind::InvalidInput, "seek outside the HTTP body"))?;
        if target != self.position {
            if !self.is_seekable() {
                return Err(io::Error::new(
                    ErrorKind::Unsupported,
                    "the server does not accept range requests",
                ));
            }
            self.body = None;
            self.position = target;
        }
        Ok(target)
    }
}

impl MediaSource for HttpMedia {
    fn is_seekable(&self) -> bool {
        self.ranges && self.length.is_some()
    }

    fn byte_len(&self) -> Option<u64> {
        self.length
    }
}

async fn stream(
    request: RequestBuilder,
    ranged: bool,
    host: String,
    head: oneshot::Sender<io::Result<Head>>,
    pieces: mpsc::Sender<io::Result<Bytes>>,
    piece: usize,
) {
    let mut response = match respond(request, ranged, &host).await {
        Ok(response) => response,
        Err(error) => {
            drop(head.send(Err(error)));
            return;
        }
    };
    let opened = Head {
        length: response.content_length(),
        ranges: accepts_ranges(response.headers()),
        content_type: content_type(response.headers()),
    };
    if head.send(Ok(opened)).is_err() {
        return;
    }
    loop {
        let mut chunk = match response.chunk().await {
            Ok(Some(chunk)) => chunk,
            Ok(None) => return,
            Err(error) => {
                drop(pieces.send(Err(failure(error))).await);
                return;
            }
        };
        while !chunk.is_empty() {
            let next = chunk.split_to(chunk.len().min(piece));
            if pieces.send(Ok(next)).await.is_err() {
                return;
            }
        }
    }
}

async fn respond(request: RequestBuilder, ranged: bool, host: &str) -> io::Result<Response> {
    let response = request.send().await.map_err(failure)?;
    let status = response.status();
    if !status.is_success() {
        return Err(io::Error::other(format!(
            "HTTP {} from {host}",
            status.as_u16()
        )));
    }
    if ranged && status != StatusCode::PARTIAL_CONTENT {
        return Err(io::Error::other(format!("{host} ignored a range request")));
    }
    Ok(response)
}

fn failure(error: reqwest::Error) -> io::Error {
    io::Error::other(describe(&error.without_url()))
}

fn accepts_ranges(headers: &HeaderMap) -> bool {
    headers
        .get_all(ACCEPT_RANGES)
        .iter()
        .filter_map(|value| value.to_str().ok())
        .flat_map(|value| value.split(','))
        .any(|unit| unit.trim().eq_ignore_ascii_case("bytes"))
}

fn content_type(headers: &HeaderMap) -> Option<String> {
    let value = headers.get(CONTENT_TYPE)?.to_str().ok()?;
    let mime = value.split(';').next()?.trim();
    (!mime.is_empty()).then(|| mime.to_ascii_lowercase())
}

#[cfg(test)]
mod tests {
    use std::io::Write;
    use std::net::{TcpListener, TcpStream};
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::{Arc, mpsc};

    use super::*;
    use crate::voice::source::SourceKind;
    use crate::voice::source::tests::{
        Harness, fixture, frames, read_to_end, samples, wait_for_info,
    };
    use crate::voice::types::SourceSpec;

    struct Server {
        base: String,
        requests: mpsc::Receiver<String>,
    }

    impl Server {
        fn start(body: Vec<u8>, ranges: bool) -> Self {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let base = format!("http://{}", listener.local_addr().unwrap());
            let (sender, requests) = mpsc::channel();
            let body = Arc::new(body);
            let served = Arc::new(AtomicUsize::new(0));
            thread::spawn(move || {
                for stream in listener.incoming() {
                    let Ok(mut stream) = stream else { return };
                    let body = Arc::clone(&body);
                    let sender = sender.clone();
                    let attempt = served.fetch_add(1, Ordering::Relaxed);
                    thread::spawn(move || {
                        let head = read_head(&mut stream);
                        drop(sender.send(head.clone()));
                        answer(&mut stream, &head, &body, ranges, attempt);
                    });
                }
            });
            Self { base, requests }
        }

        fn url(&self, path: &str) -> String {
            format!("{}{path}", self.base)
        }

        fn received(&self) -> Vec<String> {
            self.requests.try_iter().collect()
        }
    }

    fn read_head(stream: &mut TcpStream) -> String {
        let mut head = Vec::new();
        let mut byte = [0; 1];
        while !head.ends_with(b"\r\n\r\n") {
            if stream.read(&mut byte).unwrap_or(0) == 0 {
                break;
            }
            head.push(byte[0]);
        }
        String::from_utf8_lossy(&head).to_ascii_lowercase()
    }

    fn answer(stream: &mut TcpStream, head: &str, body: &[u8], ranges: bool, attempt: usize) {
        if head.starts_with("get /missing ") {
            drop(stream.write_all(
                b"HTTP/1.1 404 Not Found\r\ncontent-length: 0\r\nconnection: close\r\n\r\n",
            ));
            return;
        }
        if head.starts_with("get /live ") {
            return stream_live(stream, body, attempt == 0);
        }
        let cut = head.starts_with("get /cut-always ")
            || (head.starts_with("get /cut-once ") && attempt == 0);
        let start = head
            .lines()
            .find_map(|line| line.strip_prefix("range: bytes="))
            .and_then(|range| range.trim().trim_end_matches('-').parse::<usize>().ok())
            .filter(|_| ranges && !head.starts_with("get /liar "));
        let accept = if ranges {
            "accept-ranges: bytes\r\n"
        } else {
            ""
        };
        let response = match start {
            Some(start) => format!(
                "HTTP/1.1 206 Partial Content\r\n{accept}content-range: bytes {start}-{}/{}\r\n\
                 content-length: {}\r\nconnection: close\r\n\r\n",
                body.len() - 1,
                body.len(),
                body.len() - start
            ),
            None => format!(
                "HTTP/1.1 200 OK\r\n{accept}content-type: audio/ogg\r\ncontent-length: {}\r\n\
                 connection: close\r\n\r\n",
                body.len()
            ),
        };
        drop(stream.write_all(response.as_bytes()));
        let rest = &body[start.unwrap_or(0)..];
        let kept = match (cut, attempt) {
            (false, _) => rest.len(),
            (true, 0) => rest.len() / 2,
            (true, _) => 0,
        };
        for piece in rest[..kept].chunks(1_000) {
            if stream.write_all(piece).is_err() {
                return;
            }
        }
    }

    fn stream_live(stream: &mut TcpStream, body: &[u8], cut: bool) {
        let head = "HTTP/1.1 200 OK\r\ntransfer-encoding: chunked\r\nconnection: close\r\n\r\n";
        drop(stream.write_all(head.as_bytes()));
        let sent = if cut { &body[..body.len() / 2] } else { body };
        for piece in sent.chunks(1_000) {
            let chunk = [format!("{:x}\r\n", piece.len()).as_bytes(), piece, b"\r\n"].concat();
            if stream.write_all(&chunk).is_err() {
                return;
            }
        }
        if !cut {
            drop(stream.write_all(b"0\r\n\r\n"));
        }
    }

    fn body_of(size: u32) -> Vec<u8> {
        (0..size)
            .map(|index| {
                let [low, high, ..] = index.to_le_bytes();
                low ^ high
            })
            .collect()
    }

    fn url_source(harness: &mut Harness, url: String) -> crate::voice::source::Source {
        harness.open(SourceSpec::Url {
            url,
            headers: Vec::new(),
        })
    }

    #[test]
    fn a_url_without_range_support_streams_the_same_packets_as_the_file() {
        let mut harness = Harness::new();
        let bytes = std::fs::read(fixture("tone.opus")).unwrap();
        let server = Server::start(bytes, false);
        let file = harness.open(SourceSpec::File(fixture("tone.opus")));
        let source = url_source(&mut harness, server.url("/tone.opus"));

        let info = wait_for_info(&source);
        let (packets, error) = read_to_end(&source);

        assert_eq!(info.kind, SourceKind::Opus);
        assert!(!info.seekable);
        assert_eq!(error, None);
        assert_eq!(packets, read_to_end(&file).0);
        assert_eq!(server.received().len(), 1);
    }

    #[test]
    fn a_seekable_url_plays_from_the_seek_position() {
        let mut harness = Harness::new();
        let bytes = std::fs::read(fixture("tone-s16.wav")).unwrap();
        let server = Server::start(bytes, true);
        let source = url_source(&mut harness, server.url("/tone.wav"));
        assert!(wait_for_info(&source).seekable);

        source.seek(Duration::from_millis(500)).unwrap();
        let (chunks, error) = read_to_end(&source);

        assert_eq!(error, None);
        assert_eq!(samples(&chunks).len(), 24_000 * 2);
    }

    #[test]
    fn a_dropped_body_resumes_with_a_range_from_where_it_broke() {
        let harness = Harness::new();
        let body = body_of(100_000);
        let server = Server::start(body.clone(), true);
        let mut media = HttpMedia::new(&harness.http, &server.url("/cut-once"), &[]).unwrap();
        media.connect().unwrap();
        let mut read = Vec::new();

        media.read_to_end(&mut read).unwrap();

        assert_eq!(read, body);
        let requests = server.received();
        assert_eq!(requests.len(), 2);
        assert!(requests[1].contains("range: bytes=50000-\r\n"));
    }

    #[test]
    fn a_dropped_live_stream_reconnects_without_a_range() {
        let harness = Harness::new();
        let body = body_of(10_000);
        let server = Server::start(body.clone(), false);
        let mut media = HttpMedia::new(&harness.http, &server.url("/live"), &[]).unwrap();
        media.connect().unwrap();
        let mut read = Vec::new();

        media.read_to_end(&mut read).unwrap();

        assert_eq!(read, [&body[..5_000], &body].concat());
        let requests = server.received();
        assert_eq!(requests.len(), 2);
        assert!(!requests[1].contains("range:"));
    }

    #[test]
    fn a_body_that_keeps_dropping_fails_after_three_resumes_in_a_row() {
        let mut harness = Harness::new();
        let bytes = std::fs::read(fixture("tone-s16.wav")).unwrap();
        let server = Server::start(bytes, true);
        let source = url_source(&mut harness, server.url("/cut-always"));

        let (_, error) = read_to_end(&source);

        assert!(error.is_some());
        assert_eq!(server.received().len(), 4);
    }

    #[test]
    fn http_media_seeks_with_a_range_request_from_the_new_position() {
        let harness = Harness::new();
        let body = body_of(200_000);
        let server = Server::start(body.clone(), true);
        let mut media = HttpMedia::new(&harness.http, &server.url("/body"), &[]).unwrap();
        media.connect().unwrap();
        let mut head = [0; 100];
        media.read_exact(&mut head).unwrap();

        assert_eq!(media.stream_position().unwrap(), 100);
        assert_eq!(media.seek(SeekFrom::End(-50_000)).unwrap(), 150_000);
        let mut rest = Vec::new();
        media.read_to_end(&mut rest).unwrap();

        assert!(media.is_seekable());
        assert_eq!(media.byte_len(), Some(200_000));
        assert_eq!(head, body[..100]);
        assert_eq!(rest, body[150_000..]);
        let requests = server.received();
        assert_eq!(requests.len(), 2);
        assert!(!requests[0].contains("range:"));
        assert!(requests[1].contains("range: bytes=150000-\r\n"));
    }

    #[test]
    fn http_media_refuses_to_seek_when_the_server_has_no_ranges() {
        let harness = Harness::new();
        let server = Server::start(vec![7; 10_000], false);
        let mut media = HttpMedia::new(&harness.http, &server.url("/body"), &[]).unwrap();
        media.connect().unwrap();

        let error = media.seek(SeekFrom::Start(10)).unwrap_err();

        assert!(!media.is_seekable());
        assert_eq!(error.kind(), ErrorKind::Unsupported);
    }

    #[test]
    fn a_server_that_ignores_a_range_request_fails_the_read() {
        let harness = Harness::new();
        let server = Server::start(vec![7; 10_000], true);
        let mut media = HttpMedia::new(&harness.http, &server.url("/liar"), &[]).unwrap();
        media.connect().unwrap();
        media.seek(SeekFrom::Start(10)).unwrap();

        let error = media.read(&mut [0; 16]).unwrap_err();

        assert_eq!(error.to_string(), "127.0.0.1 ignored a range request");
    }

    #[test]
    fn a_seekable_url_plays_a_seek_back_to_the_start_in_full() {
        let mut harness = Harness::new();
        let bytes = std::fs::read(fixture("tone.flac")).unwrap();
        let server = Server::start(bytes, true);
        let source = url_source(&mut harness, server.url("/tone.flac"));
        let (first, _) = read_to_end(&source);

        source.seek(Duration::ZERO).unwrap();
        let (again, error) = read_to_end(&source);

        assert_eq!(error, None);
        assert_eq!(frames(SourceKind::Pcm, &again), 48_000);
        assert_eq!(again, first);
    }

    #[test]
    fn an_http_error_status_ends_the_source_naming_the_status_and_host() {
        let mut harness = Harness::new();
        let server = Server::start(Vec::new(), false);
        let source = url_source(&mut harness, server.url("/missing"));

        let (chunks, error) = read_to_end(&source);

        assert!(chunks.is_empty());
        assert_eq!(error.as_deref(), Some("HTTP 404 from 127.0.0.1"));
        assert_eq!(source.info().unwrap(), None);
    }

    #[test]
    fn a_failed_connection_reports_the_error_without_the_url() {
        let mut harness = Harness::new();
        let refusing = crate::refusing_socket();
        let port = refusing.local_addr().unwrap().port();
        let source = url_source(
            &mut harness,
            format!("http://127.0.0.1:{port}/tone.ogg?token=hunter2"),
        );

        let (_, error) = read_to_end(&source);

        let error = error.unwrap();
        assert!(!error.is_empty());
        assert!(!error.contains("hunter2"), "{error}");
    }

    #[test]
    fn custom_headers_reach_the_server_and_stay_out_of_debug_output() {
        let mut harness = Harness::new();
        let bytes = std::fs::read(fixture("tone.opus")).unwrap();
        let server = Server::start(bytes, false);
        let headers = vec![("X-Token".to_owned(), "hunter2".to_owned())];
        let media = HttpMedia::new(&harness.http, &server.url("/tone.opus"), &headers).unwrap();
        let source = harness.open(SourceSpec::Url {
            url: server.url("/tone.opus"),
            headers,
        });

        read_to_end(&source);

        let request = &server.received()[0];
        assert!(!format!("{:?}", media.headers).contains("hunter2"));
        assert!(request.contains("x-token: hunter2\r\n"));
        assert!(request.contains(&format!(
            "user-agent: bostie/{}\r\n",
            env!("CARGO_PKG_VERSION")
        )));
        assert!(!request.contains("authorization"));
    }

    #[test]
    fn invalid_urls_and_headers_are_refused_without_echoing_header_values() {
        let harness = Harness::new();
        let refused = |url: &str, headers: &[(String, String)]| match HttpMedia::new(
            &harness.http,
            url,
            headers,
        ) {
            Err(CoreError::InvalidRequest(message)) => message,
            Err(other) => panic!("{other}"),
            Ok(_) => panic!("{url} was accepted"),
        };
        let header = |name: &str, value: &str| vec![(name.to_owned(), value.to_owned())];

        assert!(refused("ftp://example.com/a.ogg", &[]).contains("http"));
        assert!(refused("not a url", &[]).contains("http"));
        assert!(refused("http://example.com/a.ogg", &header("bad name", "x")).contains("bad name"));
        let message = refused("http://example.com/a.ogg", &header("X-Key", "line\nbreak"));
        assert!(
            message.contains("X-Key") && !message.contains("break"),
            "{message}"
        );
    }
}
