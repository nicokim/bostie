use std::collections::{HashMap, VecDeque};
use std::iter;

use tokio::io::{AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::mpsc;
use tokio::time::timeout;
use twilight_http_ratelimiting::{Endpoint, Method as LimiterMethod};

use super::*;
use crate::bell::{self, BellReceiver};
use crate::testkit;

const PATIENCE: Duration = Duration::from_secs(5);
pub(crate) const SETTINGS: HttpSettings = HttpSettings {
    max_rate_limit_wait: Duration::from_secs(1),
    retries: 2,
    backoff_base: Duration::from_millis(10),
    connect_timeout: Duration::from_secs(10),
    read_timeout: Duration::from_secs(60),
    rate_limiter: true,
};
pub(crate) const NO_REPLY: String = String::new();

pub(crate) struct Captured {
    pub(crate) path: String,
    pub(crate) headers: HashMap<String, String>,
    body: Vec<u8>,
    at: Instant,
}

pub(crate) struct Server {
    pub(crate) url: String,
    requests: mpsc::UnboundedReceiver<Captured>,
}

impl Server {
    pub(crate) async fn start(script: Vec<String>) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}/api/v10", listener.local_addr().unwrap());
        let (captured, requests) = mpsc::unbounded_channel();
        let script = Arc::new(Mutex::new(VecDeque::from(script)));
        tokio::spawn(async move {
            while let Ok((stream, _)) = listener.accept().await {
                tokio::spawn(answer(stream, Arc::clone(&script), captured.clone()));
            }
        });
        Self { url, requests }
    }

    pub(crate) async fn next(&mut self) -> Captured {
        timeout(PATIENCE, self.requests.recv())
            .await
            .unwrap()
            .unwrap()
    }

    pub(crate) fn received(&mut self) -> usize {
        iter::from_fn(|| self.requests.try_recv().ok()).count()
    }
}

async fn answer(
    stream: TcpStream,
    script: Arc<Mutex<VecDeque<String>>>,
    captured: mpsc::UnboundedSender<Captured>,
) {
    let mut stream = BufReader::new(stream);
    while let Some(request) = read_request(&mut stream).await {
        let scripted = script
            .lock()
            .pop_front()
            .unwrap_or_else(|| reply("200 OK", &[], "{}"));
        drop(captured.send(request));
        if scripted.is_empty() {
            drop(stream.read(&mut [0; 1]).await);
            return;
        }
        if stream
            .get_mut()
            .write_all(scripted.as_bytes())
            .await
            .is_err()
            || scripted.contains("Connection: close")
        {
            return;
        }
    }
}

async fn read_request(stream: &mut BufReader<TcpStream>) -> Option<Captured> {
    let head = testkit::read_head_async(stream).await?;
    let mut lines = head.lines();
    let path = lines.next()?.split(' ').nth(1)?.to_owned();
    let headers: HashMap<String, String> = lines
        .filter_map(|line| line.split_once(':'))
        .map(|(name, value)| (name.to_ascii_lowercase(), value.trim().to_owned()))
        .collect();
    let length = headers
        .get("content-length")
        .map_or(0, |length| length.parse().unwrap());
    let mut body = vec![0; length];
    stream.read_exact(&mut body).await.ok()?;
    Some(Captured {
        path,
        headers,
        body,
        at: Instant::now(),
    })
}

pub(crate) fn reply(status: &str, headers: &[(&str, &str)], body: &str) -> String {
    let headers: String = headers
        .iter()
        .flat_map(|&(name, value)| [name, ": ", value, "\r\n"])
        .collect();
    format!(
        "HTTP/1.1 {status}\r\n{headers}Content-Length: {}\r\n\r\n{body}",
        body.len()
    )
}

fn too_many(headers: &[(&str, &str)]) -> String {
    reply(
        "429 Too Many Requests",
        headers,
        r#"{"message":"You are being rate limited.","retry_after":0.1,"global":false}"#,
    )
}

fn rest(url: &str) -> (Rest, BellReceiver) {
    limited_rest(url, 50, SETTINGS)
}

fn limited_rest(url: &str, global_limit: u16, settings: HttpSettings) -> (Rest, BellReceiver) {
    rest_with(Auth::Bot("token"), url, global_limit, settings)
}

fn rest_with(
    auth: Auth<'_>,
    url: &str,
    global_limit: u16,
    settings: HttpSettings,
) -> (Rest, BellReceiver) {
    let (bell, receiver) = bell::pair().unwrap();
    let global_limit = NonZeroU16::new(global_limit).unwrap();
    let inbox = Arc::new(Inbox::new(bell));
    let rest = Rest::new(auth, url, global_limit, inbox, settings, None).unwrap();
    (rest, receiver)
}

fn request(method: &str, route: &str, params: &[(&str, &str)]) -> RestRequest {
    let params = params
        .iter()
        .map(|(name, value)| ((*name).to_owned(), (*value).to_owned()))
        .collect();
    RestRequest::parse(method, route, &params, Vec::new(), None, None, None).unwrap()
}

fn message(channel_id: &str, message_id: &str) -> RestRequest {
    request(
        "GET",
        "/channels/{channel_id}/messages/{message_id}",
        &[("channel_id", channel_id), ("message_id", message_id)],
    )
}

fn me() -> RestRequest {
    request("GET", "/users/@me", &[])
}

fn callback() -> RestRequest {
    request(
        "POST",
        "/interactions/{interaction_id}/{interaction_token}/callback",
        &[("interaction_id", "1"), ("interaction_token", "abc")],
    )
}

fn followup(token: &str) -> RestRequest {
    request(
        "POST",
        "/webhooks/{application_id}/{interaction_token}",
        &[("application_id", "1"), ("interaction_token", token)],
    )
}

fn webhook() -> RestRequest {
    request(
        "POST",
        "/webhooks/{webhook_id}/{webhook_token}",
        &[("webhook_id", "2"), ("webhook_token", "secret")],
    )
}

async fn arrivals(server: &mut Server, count: usize) -> HashMap<String, Instant> {
    let mut arrivals = HashMap::new();
    for _ in 0..count {
        let captured = server.next().await;
        arrivals.insert(captured.path, captured.at);
    }
    arrivals
}

fn status(delivery: Delivery) -> (u16, Bytes) {
    match delivery {
        Delivery::Response { status, body, .. } => (status, body),
        other => panic!("expected a response, got {other:?}"),
    }
}

fn primed_to_warn(rest: &Rest) {
    let now = Instant::now();
    let mut invalid = rest.invalid.lock();
    for _ in 0..999 {
        invalid.record(now);
    }
}

fn warned(rest: &Rest) -> bool {
    rest.inbox
        .drain()
        .iter()
        .any(|delivery| matches!(delivery, Delivery::InvalidRequests { count: 1_000 }))
}

#[test]
fn rate_limits_are_retried_three_times_when_the_wait_is_short() {
    let short = Some(Limited {
        scope: Scope::Route,
        retry_after: SETTINGS.max_rate_limit_wait,
    });
    let long = Some(Limited {
        scope: Scope::Shared,
        retry_after: SETTINGS.max_rate_limit_wait + Duration::from_millis(1),
    });
    let mut retries = Retries::new(SETTINGS);

    for _ in 0..RATE_LIMIT_RETRIES {
        assert_eq!(
            retries.after_response(&Method::POST, StatusCode::TOO_MANY_REQUESTS, short),
            Some(SETTINGS.max_rate_limit_wait)
        );
    }
    assert_eq!(
        retries.after_response(&Method::POST, StatusCode::TOO_MANY_REQUESTS, short),
        None
    );
    assert!(
        retries
            .after_response(&Method::GET, StatusCode::BAD_GATEWAY, None)
            .is_some()
    );
    assert_eq!(
        Retries::new(SETTINGS).after_response(&Method::GET, StatusCode::TOO_MANY_REQUESTS, long),
        None
    );
}

#[test]
fn backoff_stops_doubling_at_the_longest_wait_however_many_retries() {
    let mut retries = Retries::new(HttpSettings {
        retries: 64,
        ..SETTINGS
    });

    let waits: Vec<_> = iter::from_fn(|| retries.backoff()).collect();

    assert_eq!(waits.len(), 64);
    assert!(
        waits
            .iter()
            .all(|wait| *wait <= SETTINGS.max_rate_limit_wait)
    );
}

#[test]
fn server_errors_retry_twice_with_jittered_backoff_unless_posted() {
    let mut retries = Retries::new(SETTINGS);

    let first = retries.after_response(&Method::GET, StatusCode::BAD_GATEWAY, None);
    let second = retries.after_response(&Method::DELETE, StatusCode::SERVICE_UNAVAILABLE, None);
    let third = retries.after_response(&Method::GET, StatusCode::BAD_GATEWAY, None);

    let base = SETTINGS.backoff_base;
    assert!(first.is_some_and(|wait| wait >= base / 2 && wait <= base));
    assert!(second.is_some_and(|wait| wait >= base && wait <= base * 2));
    assert_eq!(third, None);
    let short = Some(Limited {
        scope: Scope::Route,
        retry_after: base,
    });
    assert_eq!(
        retries.after_response(&Method::GET, StatusCode::TOO_MANY_REQUESTS, short),
        Some(base)
    );
    let cloudflare_timeout = StatusCode::from_u16(CLOUDFLARE_TIMEOUT).unwrap();
    for (method, status, retried) in [
        (Method::GET, StatusCode::INTERNAL_SERVER_ERROR, true),
        (Method::PUT, StatusCode::GATEWAY_TIMEOUT, true),
        (Method::DELETE, cloudflare_timeout, true),
        (Method::POST, cloudflare_timeout, false),
        (Method::POST, StatusCode::INTERNAL_SERVER_ERROR, false),
        (Method::GET, StatusCode::NOT_IMPLEMENTED, false),
        (Method::GET, StatusCode::NOT_FOUND, false),
        (Method::PUT, StatusCode::NO_CONTENT, false),
    ] {
        assert_eq!(
            Retries::new(SETTINGS)
                .after_response(&method, status, None)
                .is_some(),
            retried,
            "{method} {status}"
        );
    }
}

#[test]
fn transport_errors_retry_when_unsent_or_idempotent() {
    for (method, before_sending, retried) in [
        (Method::POST, true, true),
        (Method::POST, false, false),
        (Method::PATCH, false, true),
    ] {
        assert_eq!(
            Retries::new(SETTINGS)
                .after_error(&method, before_sending)
                .is_some(),
            retried,
            "{method} {before_sending}"
        );
    }
}

#[tokio::test]
async fn requests_go_through_the_proxy_in_absolute_form() {
    let mut server = Server::start(vec![reply("200 OK", &[], "{}")]).await;
    let proxy = Proxy::parse(server.url.trim_end_matches("/api/v10")).unwrap();
    let (bell, _receiver) = bell::pair().unwrap();
    let global_limit = NonZeroU16::new(50).unwrap();
    let inbox = Arc::new(Inbox::new(bell));
    let api_url = "http://discord.invalid/api/v10";
    let auth = Auth::Bot("token");
    let rest = Rest::new(auth, api_url, global_limit, inbox, SETTINGS, Some(&proxy));

    let (got, _) = status(rest.unwrap().execute(Ticket::from(1), me()).await);

    assert_eq!(got, 200);
    assert_eq!(server.next().await.path, format!("{api_url}/users/@me"));
}

#[tokio::test]
async fn downloads_skip_the_token_go_through_the_proxy_and_retry_server_errors() {
    let mut server = Server::start(vec![
        reply("503 Service Unavailable", &[], ""),
        reply("200 OK", &[], "PNG"),
    ])
    .await;
    let proxy = Proxy::parse(server.url.trim_end_matches("/api/v10")).unwrap();
    let (bell, _receiver) = bell::pair().unwrap();
    let global_limit = NonZeroU16::new(50).unwrap();
    let inbox = Arc::new(Inbox::new(bell));
    let api_url = "http://discord.invalid/api/v10";
    let rest = Rest::new(
        Auth::Bot("token"),
        api_url,
        global_limit,
        inbox,
        SETTINGS,
        Some(&proxy),
    )
    .unwrap();
    let file = "http://cdn.invalid/attachments/1/2/a.png?ex=1";

    let got = status(
        rest.download(Ticket::from(1), Url::parse(file).unwrap())
            .await,
    );

    assert_eq!(got, (200, Bytes::from_static(b"PNG")));
    for _ in 0..2 {
        let sent = server.next().await;
        assert_eq!(sent.path, file);
        assert!(!sent.headers.contains_key("authorization"));
    }
}

#[tokio::test]
async fn each_auth_sends_its_token_only_on_bot_routes() {
    crate::init_test_tracing();
    for (auth, authorization) in [
        (Auth::Bot("token"), Some("Bot token")),
        (Auth::Bearer("token"), Some("Bearer token")),
        (Auth::None, None),
    ] {
        let mut server = Server::start(vec![reply("200 OK", &[], "hello")]).await;
        let (rest, _receiver) = rest_with(auth, &server.url, 50, SETTINGS);
        let webhook = RestRequest::parse(
            "POST",
            "/webhooks/{webhook_id}/{webhook_token}",
            &HashMap::from([
                ("webhook_id".to_owned(), "1".to_owned()),
                ("webhook_token".to_owned(), "secret".to_owned()),
            ]),
            Vec::new(),
            Some(b"{}".to_vec()),
            None,
            None,
        )
        .unwrap();
        let token = request("POST", "/oauth2/token", &[]);

        let bot = rest.execute(Ticket::from(1), me()).await;
        for unauthenticated in [callback(), webhook, token] {
            assert_eq!(
                status(rest.execute(Ticket::from(2), unauthenticated).await).0,
                200
            );
        }

        if let Some(authorization) = authorization {
            assert_eq!(status(bot), (200, Bytes::from_static(b"hello")));
            let bot = server.next().await;
            assert_eq!(bot.path, "/api/v10/users/@me");
            assert_eq!(bot.headers["authorization"], authorization);
        } else {
            assert!(
                matches!(
                    bot,
                    Delivery::Failure {
                        kind: FailureKind::NoToken,
                        ..
                    }
                ),
                "{bot:?}"
            );
        }
        let sent = [
            server.next().await,
            server.next().await,
            server.next().await,
        ];
        let paths = sent.each_ref().map(|captured| captured.path.as_str());
        assert_eq!(
            paths,
            [
                "/api/v10/interactions/1/abc/callback",
                "/api/v10/webhooks/1/secret",
                "/api/v10/oauth2/token"
            ]
        );
        let [_, webhook, _] = &sent;
        assert_eq!(webhook.headers["content-type"], "application/json");
        for captured in &sent {
            assert!(!captured.headers.contains_key("authorization"));
            assert!(
                captured.headers["user-agent"]
                    .starts_with("DiscordBot (https://pypi.org/project/bostie, ")
            );
        }
    }
}

#[tokio::test]
async fn passes_the_audit_log_reason_and_multipart_bodies_through() {
    crate::init_test_tracing();
    let mut server = Server::start(Vec::new()).await;
    let (rest, _receiver) = rest(&server.url);
    let upload = RestRequest::parse(
        "POST",
        "/channels/{channel_id}/messages",
        &HashMap::from([("channel_id".to_owned(), "1".to_owned())]),
        Vec::new(),
        Some(b"--x\r\npart\r\n--x--".to_vec()),
        Some("multipart/form-data; boundary=x".to_owned()),
        Some("spam bot: ü/✓".to_owned()),
    )
    .unwrap();

    status(rest.execute(Ticket::from(1), upload).await);

    let captured = server.next().await;
    assert_eq!(
        captured.headers["x-audit-log-reason"],
        "spam%20bot%3A%20%C3%BC%2F%E2%9C%93"
    );
    assert_eq!(
        captured.headers["content-type"],
        "multipart/form-data; boundary=x"
    );
    assert_eq!(captured.body, b"--x\r\npart\r\n--x--");
}

#[tokio::test]
async fn without_the_rate_limiter_an_exhausted_bucket_is_not_waited() {
    let exhausted = reply(
        "200 OK",
        &[
            ("X-RateLimit-Bucket", "abc"),
            ("X-RateLimit-Limit", "5"),
            ("X-RateLimit-Remaining", "0"),
            ("X-RateLimit-Reset-After", "60"),
        ],
        "{}",
    );
    let mut server = Server::start(vec![exhausted]).await;
    let settings = HttpSettings {
        rate_limiter: false,
        ..SETTINGS
    };
    let (rest, _receiver) = limited_rest(&server.url, 50, settings);

    for _ in 0..2 {
        assert_eq!(
            status(rest.execute(Ticket::from(1), message("1", "10")).await).0,
            200
        );
    }

    assert_eq!(server.received(), 2);
}

#[tokio::test]
async fn a_webhook_discord_no_longer_knows_gets_no_more_requests() {
    let not_found = |code: u32| {
        reply(
            "404 Not Found",
            &[],
            &format!(r#"{{"message":"x","code":{code}}}"#),
        )
    };
    let mut server = Server::start(vec![not_found(10008), not_found(10015)]).await;
    let (rest, _receiver) = rest(&server.url);

    let mut answers = Vec::new();
    for _ in 0..3 {
        answers.push(status(rest.execute(Ticket::from(1), webhook()).await));
    }

    assert_eq!(server.received(), 2);
    let codes: Vec<_> = answers
        .iter()
        .map(|(got, body)| (*got, sonic_rs::get(body, ["code"]).unwrap().as_u64()))
        .collect();
    assert_eq!(
        codes,
        [(404, Some(10008)), (404, Some(10015)), (404, Some(10015))]
    );
}

#[tokio::test]
async fn an_exhausted_bucket_delays_its_route_but_not_other_channels() {
    crate::init_test_tracing();
    let exhausted = reply(
        "200 OK",
        &[
            ("X-RateLimit-Bucket", "abc"),
            ("X-RateLimit-Limit", "5"),
            ("X-RateLimit-Remaining", "0"),
            ("X-RateLimit-Reset-After", "0.2"),
        ],
        "{}",
    );
    let mut server = Server::start(vec![exhausted]).await;
    let (rest, _receiver) = rest(&server.url);

    status(rest.execute(Ticket::from(1), message("1", "10")).await);
    tokio::join!(
        rest.execute(Ticket::from(1), message("1", "20")),
        rest.execute(Ticket::from(1), message("2", "30")),
    );

    let arrivals = arrivals(&mut server, 3).await;
    let first = arrivals["/api/v10/channels/1/messages/10"];
    let same_channel = arrivals["/api/v10/channels/1/messages/20"];
    let other_channel = arrivals["/api/v10/channels/2/messages/30"];
    let waited = same_channel - first;
    assert!(waited >= Duration::from_millis(190), "{waited:?}");
    assert!(other_channel < same_channel);
}

#[tokio::test]
async fn an_exhausted_followup_bucket_delays_or_holds_back_only_its_token() {
    crate::init_test_tracing();
    for (reset_after, same_token) in [("0.2", 200), ("120", 429)] {
        let exhausted = reply(
            "200 OK",
            &[
                ("X-RateLimit-Bucket", "abc"),
                ("X-RateLimit-Limit", "5"),
                ("X-RateLimit-Remaining", "0"),
                ("X-RateLimit-Reset-After", reset_after),
            ],
            "{}",
        );
        let mut server = Server::start(vec![exhausted]).await;
        let (rest, _receiver) = rest(&server.url);

        status(rest.execute(Ticket::from(1), followup("first")).await);
        let (same, other, interaction) = timeout(PATIENCE, async {
            tokio::join!(
                rest.execute(Ticket::from(2), followup("first")),
                rest.execute(Ticket::from(3), followup("second")),
                rest.execute(Ticket::from(4), callback()),
            )
        })
        .await
        .unwrap();

        let codes = (status(same).0, status(other).0, status(interaction).0);
        assert_eq!(codes, (same_token, 200, 200), "{reset_after}");
        let first = server.next().await.at;
        let sent = 2 + usize::from(same_token == 200);
        let arrivals = arrivals(&mut server, sent).await;
        assert_eq!(server.received(), 0, "{reset_after}");
        if let Some(&same) = arrivals.get("/api/v10/webhooks/1/first") {
            let waited = same - first;
            assert!(waited >= Duration::from_millis(190), "{waited:?}");
            assert!(arrivals["/api/v10/webhooks/1/second"] < same);
            assert!(arrivals["/api/v10/interactions/1/abc/callback"] < same);
        }
    }
}

#[tokio::test]
async fn the_global_limit_is_configurable_and_spares_interaction_routes() {
    crate::init_test_tracing();
    let mut server = Server::start(Vec::new()).await;
    let (rest, _receiver) = limited_rest(&server.url, 1, SETTINGS);

    status(rest.execute(Ticket::from(1), me()).await);
    tokio::join!(
        rest.execute(Ticket::from(2), message("1", "10")),
        rest.execute(Ticket::from(3), followup("first")),
        rest.execute(Ticket::from(4), callback()),
    );

    let first = server.next().await.at;
    let arrivals = arrivals(&mut server, 3).await;
    let bot = arrivals["/api/v10/channels/1/messages/10"];
    let waited = bot - first;
    assert!(waited >= Duration::from_millis(900), "{waited:?}");
    assert!(arrivals["/api/v10/webhooks/1/first"] < bot);
    assert!(arrivals["/api/v10/interactions/1/abc/callback"] < bot);
}

#[tokio::test]
async fn a_user_rate_limit_is_retried_after_retry_after() {
    crate::init_test_tracing();
    let limited = too_many(&[
        ("Retry-After", "0.3"),
        ("X-RateLimit-Scope", "user"),
        ("X-RateLimit-Bucket", "abc"),
        ("X-RateLimit-Limit", "1"),
        ("X-RateLimit-Remaining", "0"),
        ("X-RateLimit-Reset-After", "0.3"),
    ]);
    let mut server = Server::start(vec![limited]).await;
    let (rest, _receiver) = rest(&server.url);

    let (code, _) = status(rest.execute(Ticket::from(1), message("1", "10")).await);

    assert_eq!(code, 200);
    let first = server.next().await.at;
    let retry = server.next().await.at;
    assert!(retry - first >= Duration::from_millis(300));
}

#[tokio::test]
async fn a_global_rate_limit_holds_back_other_routes() {
    crate::init_test_tracing();
    let global = too_many(&[
        ("Retry-After", "0.5"),
        ("X-RateLimit-Global", "true"),
        ("X-RateLimit-Scope", "global"),
    ]);
    let mut server = Server::start(vec![global]).await;
    let settings = HttpSettings {
        max_rate_limit_wait: Duration::from_millis(100),
        ..SETTINGS
    };
    let (rest, _receiver) = limited_rest(&server.url, 50, settings);

    let (limited_code, _) = status(rest.execute(Ticket::from(1), me()).await);
    let (other_code, _) = status(rest.execute(Ticket::from(2), message("2", "20")).await);

    assert_eq!((limited_code, other_code), (429, 200));
    let first = server.next().await.at;
    let waited = server.next().await.at - first;
    assert!(waited >= Duration::from_millis(500), "{waited:?}");
}

#[tokio::test]
async fn a_global_rate_limit_holds_back_requests_already_queued_on_its_route() {
    crate::init_test_tracing();
    let global = too_many(&[
        ("Retry-After", "0.5"),
        ("X-RateLimit-Global", "true"),
        ("X-RateLimit-Scope", "global"),
    ]);
    let mut server = Server::start(vec![global]).await;
    let (rest, _receiver) = rest(&server.url);

    let (limited, queued) = tokio::join!(
        rest.execute(Ticket::from(1), me()),
        rest.execute(Ticket::from(2), me()),
    );

    assert_eq!((status(limited).0, status(queued).0), (200, 200));
    let first = server.next().await.at;
    for _ in 0..2 {
        let waited = server.next().await.at - first;
        assert!(waited >= Duration::from_millis(500), "{waited:?}");
    }
}

#[tokio::test]
async fn a_long_shared_rate_limit_reaches_queued_and_later_requests() {
    crate::init_test_tracing();
    let shared = too_many(&[
        ("Retry-After", "120"),
        ("X-RateLimit-Scope", "shared"),
        ("X-RateLimit-Bucket", "abc"),
        ("X-RateLimit-Limit", "10"),
        ("X-RateLimit-Remaining", "9"),
        ("X-RateLimit-Reset-After", "120"),
    ]);
    let mut server = Server::start(vec![shared]).await;
    let (rest, _receiver) = rest(&server.url);

    let (first, queued) = timeout(PATIENCE, async {
        tokio::join!(
            rest.execute(Ticket::from(1), message("1", "10")),
            rest.execute(Ticket::from(2), message("1", "20")),
        )
    })
    .await
    .unwrap();
    let later = rest.execute(Ticket::from(3), message("1", "30")).await;

    assert_eq!(status(first).0, 429);
    for held_back in [queued, later] {
        let (code, body) = status(held_back);
        let held: sonic_rs::Value = sonic_rs::from_slice(&body).unwrap();
        let retry_after = held.get("retry_after").as_f64().unwrap();
        assert_eq!(code, 429);
        assert!((100.0..=120.0).contains(&retry_after), "{retry_after}");
        assert_eq!(held.get("global").as_bool(), Some(false));
    }
    assert_eq!(server.received(), 1);
}

#[tokio::test]
async fn a_body_that_breaks_off_is_retried_after_a_429_or_an_unposted_200() {
    crate::init_test_tracing();
    for (method, head, sent, delivered) in [
        (
            "POST",
            "429 Too Many Requests\r\nRetry-After: 0.1\r\nX-RateLimit-Scope: user",
            2,
            Some(200),
        ),
        ("GET", "200 OK", 2, Some(200)),
        ("POST", "200 OK", 1, None),
    ] {
        let truncated =
            format!("HTTP/1.1 {head}\r\nConnection: close\r\nContent-Length: 100\r\n\r\n{{}}");
        let mut server = Server::start(vec![truncated]).await;
        let (rest, _receiver) = rest(&server.url);
        primed_to_warn(&rest);
        let request = request(
            method,
            "/channels/{channel_id}/messages",
            &[("channel_id", "1")],
        );

        let delivery = rest.execute(Ticket::from(1), request).await;

        let code = match delivery {
            Delivery::Response { status, .. } => Some(status),
            Delivery::Failure {
                kind: FailureKind::Transport,
                ..
            } => None,
            other => panic!("unexpected {other:?}"),
        };
        assert_eq!(
            (code, server.received()),
            (delivered, sent),
            "{method} {head}"
        );
        assert_eq!(warned(&rest), head.starts_with("429"), "{method} {head}");
    }
}

#[tokio::test]
async fn a_rejected_token_stops_bot_requests_but_not_interactions() {
    crate::init_test_tracing();
    let mut server = Server::start(vec![reply("401 Unauthorized", &[], "{}")]).await;
    let (rest, _receiver) = rest(&server.url);

    let (rejected, queued) = tokio::join!(
        rest.execute(Ticket::from(1), me()),
        rest.execute(Ticket::from(2), me()),
    );
    let later = rest.execute(Ticket::from(3), me()).await;
    let (interaction, _) = status(rest.execute(Ticket::from(4), callback()).await);

    assert_eq!((status(rejected).0, interaction), (401, 200));
    for refused in [queued, later] {
        assert!(
            matches!(
                refused,
                Delivery::Failure {
                    kind: FailureKind::InvalidToken,
                    ..
                }
            ),
            "{refused:?}"
        );
    }
    assert_eq!(server.received(), 2);
}

#[tokio::test]
async fn invalid_requests_warn_then_block_everything_at_the_limit() {
    crate::init_test_tracing();
    let mut server = Server::start(vec![reply("403 Forbidden", &[], "{}")]).await;
    let (rest, _receiver) = rest(&server.url);
    let now = Instant::now();
    let record = |times| {
        let mut invalid = rest.invalid.lock();
        for _ in 0..times {
            invalid.record(now);
        }
    };
    record(999);

    let (forbidden, _) = status(rest.execute(Ticket::from(1), me()).await);
    record(8_500);
    let refused = rest.execute(Ticket::from(2), callback()).await;

    assert_eq!(forbidden, 403);
    assert!(matches!(
        rest.inbox.drain().as_slice(),
        [Delivery::InvalidRequests { count: 1_000 }]
    ));
    assert!(
        matches!(
            refused,
            Delivery::Failure {
                kind: FailureKind::RequestBudget,
                ..
            }
        ),
        "{refused:?}"
    );
    assert_eq!(server.received(), 1);
}

#[tokio::test]
async fn server_errors_are_retried_only_when_not_posted() {
    crate::init_test_tracing();
    let bad_gateway = || reply("502 Bad Gateway", &[], "");
    let mut server = Server::start(vec![bad_gateway(); 4]).await;
    let (rest, _receiver) = rest(&server.url);
    let post = request(
        "POST",
        "/channels/{channel_id}/messages",
        &[("channel_id", "1")],
    );

    let (got, _) = status(rest.execute(Ticket::from(1), me()).await);
    assert_eq!((got, server.received()), (502, 3));
    let (posted, _) = status(rest.execute(Ticket::from(1), post).await);
    assert_eq!((posted, server.received()), (502, 1));
}

#[tokio::test]
async fn responses_count_as_invalid_pause_the_gate_or_reject_the_token() {
    let limited = |scope| {
        Some(Limited {
            scope,
            retry_after: Duration::from_secs(60),
        })
    };
    let too_many = StatusCode::TOO_MANY_REQUESTS;
    for (status, class, limited, expected) in [
        (StatusCode::OK, RouteClass::Bot, None, (false, false, false)),
        (
            StatusCode::UNAUTHORIZED,
            RouteClass::Bot,
            None,
            (true, false, true),
        ),
        (
            StatusCode::UNAUTHORIZED,
            RouteClass::Interaction,
            None,
            (true, false, false),
        ),
        (
            StatusCode::UNAUTHORIZED,
            RouteClass::Callback,
            None,
            (true, false, false),
        ),
        (
            StatusCode::UNAUTHORIZED,
            RouteClass::Webhook,
            None,
            (true, false, false),
        ),
        (
            StatusCode::FORBIDDEN,
            RouteClass::Bot,
            None,
            (true, false, false),
        ),
        (
            StatusCode::NOT_FOUND,
            RouteClass::Bot,
            None,
            (false, false, false),
        ),
        (
            too_many,
            RouteClass::Bot,
            limited(Scope::Route),
            (true, false, false),
        ),
        (
            too_many,
            RouteClass::Bot,
            limited(Scope::Shared),
            (false, false, false),
        ),
        (
            too_many,
            RouteClass::Bot,
            limited(Scope::Global),
            (true, true, false),
        ),
        (
            too_many,
            RouteClass::Interaction,
            limited(Scope::Global),
            (true, true, false),
        ),
        (
            StatusCode::BAD_GATEWAY,
            RouteClass::Bot,
            None,
            (false, false, false),
        ),
    ] {
        let (rest, _receiver) = rest("http://127.0.0.1:1/api/v10");
        primed_to_warn(&rest);

        rest.observe(class, status, limited);

        let paused = rest.gate.wait().now_or_never().is_none();
        let rejected = matches!(
            rest.refusal(Ticket::from(1), &me()),
            Some(Delivery::Failure {
                kind: FailureKind::InvalidToken,
                ..
            })
        );
        assert_eq!(
            (warned(&rest), paused, rejected),
            expected,
            "{status} {class:?} {limited:?}"
        );
    }
}

#[tokio::test]
async fn connection_failures_are_retried_and_reported_without_the_url() {
    crate::init_test_tracing();
    let refusing = crate::refusing_socket();
    let address = refusing.local_addr().unwrap();
    let (rest, _receiver) = rest(&format!("http://{address}/api/v10"));
    let webhook = request(
        "POST",
        "/webhooks/{webhook_id}/{webhook_token}",
        &[("webhook_id", "1"), ("webhook_token", "secret-token")],
    );
    let started = Instant::now();

    let delivery = rest.execute(Ticket::from(1), webhook).await;

    assert!(started.elapsed() >= SETTINGS.backoff_base * 3 / 2);
    let Delivery::Failure {
        kind: FailureKind::Transport,
        detail,
        ..
    } = delivery
    else {
        panic!("expected a transport failure, got {delivery:?}");
    };
    assert!(!detail.contains("secret-token"), "{detail}");
    assert!(!detail.contains("webhooks"), "{detail}");
}

#[tokio::test]
async fn interaction_routes_skip_the_global_gate_and_the_shared_limiter() {
    crate::init_test_tracing();
    let server = Server::start(Vec::new()).await;
    let (rest, _receiver) = rest(&server.url);
    rest.gate.pause(Duration::from_secs(60));
    let _busy_queue_of_routes_without_a_resource = rest
        .limiter
        .acquire(Endpoint {
            method: LimiterMethod::Get,
            path: "users/@me".to_owned(),
        })
        .await;
    let _busy_queue_of_the_followup = rest.limiter.acquire(followup("first").endpoint).await;

    for request in [callback(), followup("first")] {
        let delivery = timeout(PATIENCE, rest.execute(Ticket::from(1), request)).await;

        assert_eq!(status(delivery.unwrap()).0, 200);
    }
}

#[tokio::test]
async fn a_request_cancelled_after_sending_keeps_its_global_slot() {
    crate::init_test_tracing();
    let mut server = Server::start(vec![NO_REPLY]).await;
    let (rest, _receiver) = limited_rest(&server.url, 1, SETTINGS);
    let rest = Arc::new(rest);
    let sent = tokio::spawn({
        let rest = Arc::clone(&rest);
        async move { rest.execute(Ticket::from(1), message("1", "10")).await }
    });
    let first = server.next().await.at;

    sent.abort();
    status(rest.execute(Ticket::from(2), message("1", "20")).await);

    let waited = server.next().await.at - first;
    assert!(waited >= Duration::from_millis(900), "{waited:?}");
}

#[tokio::test]
async fn cancelling_a_request_frees_its_permit() {
    crate::init_test_tracing();
    let mut server = Server::start(vec![NO_REPLY]).await;
    let (rest, _receiver) = rest(&server.url);
    let rest = Arc::new(rest);
    let stuck = tokio::spawn({
        let rest = Arc::clone(&rest);
        async move { rest.execute(Ticket::from(1), message("1", "10")).await }
    });
    server.next().await;

    stuck.abort();
    let delivery = timeout(PATIENCE, rest.execute(Ticket::from(1), message("1", "20"))).await;

    assert_eq!(status(delivery.unwrap()).0, 200);
}
