mod invalid;
mod limits;
mod ratelimit;
mod request;

use std::collections::HashMap;
use std::num::NonZeroU16;
use std::ops::ControlFlow;
use std::pin::pin;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use bytes::Bytes;
use futures_util::FutureExt;
use parking_lot::Mutex;
use reqwest::header::{AUTHORIZATION, HeaderValue};
use reqwest::{Method, StatusCode, Url};
use sonic_rs::JsonValueTrait;
use twilight_http_ratelimiting::{Bucket, Permit, RateLimitHeaders, RateLimiter};

pub use self::request::RestRequest;

use self::invalid::InvalidRequests;
use self::limits::{BACKOFF_BASE, MAX_RETRY_WAIT};
use self::ratelimit::{GlobalGate, InteractionBuckets, Limited, ResponseLimits, Scope};
use self::request::RouteClass;
use crate::error::{CoreError, describe};
use crate::ids::Ticket;
use crate::inbox::{Delivery, FailureKind, Inbox};

const USER_AGENT: &str = concat!(
    "DiscordBot (https://pypi.org/project/bostie, ",
    env!("CARGO_PKG_VERSION"),
    ")"
);
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const READ_TIMEOUT: Duration = Duration::from_secs(60);
const HTTP2_PING_INTERVAL: Duration = Duration::from_secs(30);
const RATE_LIMIT_RETRIES: u32 = 3;
const FAILURE_RETRIES: u32 = 2;

pub struct Rest {
    client: reqwest::Client,
    limiter: RateLimiter,
    interaction_buckets: Mutex<InteractionBuckets>,
    gate: GlobalGate,
    invalid: Mutex<InvalidRequests>,
    token: HeaderValue,
    base: Url,
    token_invalid: AtomicBool,
    inbox: Arc<Inbox>,
}

impl Rest {
    pub fn new(
        token: &str,
        api_url: &str,
        global_limit: NonZeroU16,
        inbox: Arc<Inbox>,
    ) -> Result<Self, CoreError> {
        let base = Url::parse(api_url)
            .ok()
            .filter(|url| matches!(url.scheme(), "http" | "https"))
            .ok_or_else(|| CoreError::InvalidRequest(format!("invalid api url {api_url:?}")))?;
        let mut token = HeaderValue::try_from(format!("Bot {token}")).map_err(|_| {
            CoreError::InvalidRequest("the token contains invalid characters".to_owned())
        })?;
        token.set_sensitive(true);
        let client = reqwest::Client::builder()
            .user_agent(USER_AGENT)
            .connect_timeout(CONNECT_TIMEOUT)
            .read_timeout(READ_TIMEOUT)
            .http2_keep_alive_interval(HTTP2_PING_INTERVAL)
            .http2_keep_alive_while_idle(true)
            .build()
            .map_err(CoreError::HttpClient)?;
        Ok(Self {
            client,
            limiter: RateLimiter::new(global_limit.get()),
            interaction_buckets: Mutex::new(InteractionBuckets::default()),
            gate: GlobalGate::default(),
            invalid: Mutex::new(InvalidRequests::default()),
            token,
            base,
            token_invalid: AtomicBool::new(false),
            inbox,
        })
    }

    pub async fn execute(&self, ticket: Ticket, request: RestRequest) -> Delivery {
        let url = request.url(&self.base);
        let mut retries = Retries::default();
        loop {
            if let Some(failure) = self.refusal(ticket, &request) {
                return failure;
            }
            if request.class.waits_for_the_global_gate() {
                self.gate.wait().await;
            }
            let permit = match self.permit(&request).await {
                Ok(permit) => permit,
                Err(retry_after) => {
                    tracing::debug!(
                        route = %request.endpoint.path,
                        ?retry_after,
                        "bucket exhausted beyond the retry limit"
                    );
                    return Delivery::Response {
                        ticket,
                        status: StatusCode::TOO_MANY_REQUESTS.as_u16(),
                        body: held_back(retry_after),
                    };
                }
            };
            if request.class.waits_for_the_global_gate()
                && self.gate.wait().now_or_never().is_none()
            {
                drop(permit);
                continue;
            }
            if let Some(failure) = self.refusal(ticket, &request) {
                return failure;
            }
            match self
                .attempt(ticket, &request, url.clone(), permit, &mut retries)
                .await
            {
                ControlFlow::Break(delivery) => return delivery,
                ControlFlow::Continue(wait) => tokio::time::sleep(wait).await,
            }
        }
    }

    pub(crate) async fn fetch_gateway_url(&self) -> Option<String> {
        let request = RestRequest::parse(
            "GET",
            "/gateway",
            HashMap::new(),
            Vec::new(),
            None,
            None,
            None,
        )
        .ok()?;
        let Delivery::Response {
            status: 200, body, ..
        } = self.execute(Ticket::INTERNAL, request).await
        else {
            return None;
        };
        sonic_rs::get(&body, ["url"])
            .ok()?
            .as_str()
            .map(str::to_owned)
    }

    fn refusal(&self, ticket: Ticket, request: &RestRequest) -> Option<Delivery> {
        if request.class == RouteClass::Bot && self.token_invalid.load(Ordering::Relaxed) {
            return Some(Delivery::Failure {
                ticket,
                kind: FailureKind::InvalidToken,
                detail: "Discord rejected the bot token, so bostie no longer sends requests that need it"
                    .to_owned(),
            });
        }
        let wait = self.invalid.lock().refusal(Instant::now())?;
        Some(Delivery::Failure {
            ticket,
            kind: FailureKind::RequestBudget,
            detail: format!(
                "too many invalid requests; retry in {} s",
                wait.as_millis().div_ceil(1000)
            ),
        })
    }

    async fn permit(&self, request: &RestRequest) -> Result<PermitGuard, Duration> {
        match request.class {
            RouteClass::Callback => return Ok(PermitGuard(None)),
            RouteClass::Interaction => {
                let wait = self
                    .interaction_buckets
                    .lock()
                    .wait(&request.endpoint, Instant::now());
                if wait > MAX_RETRY_WAIT {
                    return Err(wait);
                }
                if !wait.is_zero() {
                    tokio::time::sleep(wait).await;
                }
                return Ok(PermitGuard(None));
            }
            RouteClass::Webhook | RouteClass::Anonymous | RouteClass::Bot => {}
        }
        let mut acquire = pin!(self.limiter.acquire_if(request.endpoint.clone(), |bucket| {
            bucket.is_none_or(within_reach)
        }));
        loop {
            let refused = match tokio::time::timeout(MAX_RETRY_WAIT, acquire.as_mut()).await {
                Ok(Some(permit)) => return Ok(PermitGuard(Some(permit))),
                Ok(None) => true,
                Err(_) => false,
            };
            let bucket = self.limiter.bucket(request.endpoint.clone()).await;
            if refused || bucket.is_some_and(|bucket| !within_reach(bucket)) {
                return Err(bucket.map_or(Duration::ZERO, |bucket| {
                    bucket.reset_at.saturating_duration_since(Instant::now())
                }));
            }
        }
    }

    async fn attempt(
        &self,
        ticket: Ticket,
        request: &RestRequest,
        url: Url,
        permit: PermitGuard,
        retries: &mut Retries,
    ) -> ControlFlow<Delivery, Duration> {
        let mut builder = self
            .client
            .request(request.method.clone(), url)
            .headers(request.headers.clone());
        if request.class == RouteClass::Bot {
            builder = builder.header(AUTHORIZATION, self.token.clone());
        }
        if let Some(body) = &request.body {
            builder = builder.body(body.clone());
        }
        let response = match builder.send().await {
            Ok(response) => response,
            Err(error) => return after_transport_error(ticket, request, error, retries),
        };
        let status = response.status();
        let limits = ResponseLimits::parse(status, response.headers(), Instant::now());
        self.observe(request.class, status, limits.limited);
        if request.class == RouteClass::Interaction {
            self.interaction_buckets.lock().observe(
                &request.endpoint,
                limits.bucket.as_ref(),
                Instant::now(),
            );
        }
        permit.complete(limits.bucket);
        if let Some(wait) = retries.after_response(&request.method, status, limits.limited) {
            tracing::debug!(route = %request.endpoint.path, %status, ?wait, "retrying");
            return ControlFlow::Continue(wait);
        }
        match response.bytes().await {
            Ok(body) => ControlFlow::Break(Delivery::Response {
                ticket,
                status: status.as_u16(),
                body,
            }),
            Err(error) => after_transport_error(ticket, request, error, retries),
        }
    }

    fn observe(&self, class: RouteClass, status: StatusCode, limited: Option<Limited>) {
        if status == StatusCode::UNAUTHORIZED && class == RouteClass::Bot {
            tracing::debug!("Discord rejected the bot token");
            self.token_invalid.store(true, Ordering::Relaxed);
        }
        if let Some(Limited {
            scope: Scope::Global,
            retry_after,
        }) = limited
        {
            tracing::debug!(?retry_after, "global rate limit");
            self.gate.pause(retry_after);
        }
        let invalid = matches!(status, StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN)
            || limited.is_some_and(|limited| limited.scope != Scope::Shared);
        if invalid {
            let crossed = self.invalid.lock().record(Instant::now());
            if let Some(count) = crossed {
                self.inbox.push(Delivery::InvalidRequests { count });
            }
        }
    }
}

struct PermitGuard(Option<Permit>);

impl PermitGuard {
    fn complete(mut self, headers: Option<RateLimitHeaders>) {
        if let Some(permit) = self.0.take() {
            permit.complete(headers);
        }
    }
}

impl Drop for PermitGuard {
    fn drop(&mut self) {
        if let Some(permit) = self.0.take() {
            permit.complete(None);
        }
    }
}

fn within_reach(bucket: Bucket) -> bool {
    bucket.remaining > 0
        || bucket.reset_at.saturating_duration_since(Instant::now()) <= MAX_RETRY_WAIT
}

fn after_transport_error(
    ticket: Ticket,
    request: &RestRequest,
    error: reqwest::Error,
    retries: &mut Retries,
) -> ControlFlow<Delivery, Duration> {
    let error = error.without_url();
    match retries.after_error(&request.method, error.is_connect()) {
        Some(wait) => {
            tracing::debug!(
                route = %request.endpoint.path,
                error = %describe(&error),
                ?wait,
                "retrying"
            );
            ControlFlow::Continue(wait)
        }
        None => ControlFlow::Break(Delivery::Failure {
            ticket,
            kind: FailureKind::Transport,
            detail: describe(&error),
        }),
    }
}

fn held_back(retry_after: Duration) -> Bytes {
    Bytes::from(format!(
        r#"{{"message":"This route is rate limited for longer than bostie waits, so the request was not sent.","code":0,"retry_after":{:.3},"global":false}}"#,
        retry_after.as_secs_f64()
    ))
}

#[derive(Default)]
struct Retries {
    rate_limited: u32,
    failed: u32,
}

impl Retries {
    fn after_response(
        &mut self,
        method: &Method,
        status: StatusCode,
        limited: Option<Limited>,
    ) -> Option<Duration> {
        if let Some(Limited { retry_after, .. }) = limited {
            if retry_after > MAX_RETRY_WAIT || self.rate_limited == RATE_LIMIT_RETRIES {
                return None;
            }
            self.rate_limited += 1;
            return Some(retry_after);
        }
        let server_error = matches!(
            status,
            StatusCode::INTERNAL_SERVER_ERROR
                | StatusCode::BAD_GATEWAY
                | StatusCode::SERVICE_UNAVAILABLE
                | StatusCode::GATEWAY_TIMEOUT
        );
        if server_error && *method != Method::POST {
            self.backoff()
        } else {
            None
        }
    }

    fn after_error(&mut self, method: &Method, before_sending: bool) -> Option<Duration> {
        if before_sending || *method != Method::POST {
            self.backoff()
        } else {
            None
        }
    }

    fn backoff(&mut self) -> Option<Duration> {
        if self.failed == FAILURE_RETRIES {
            return None;
        }
        let wait = BACKOFF_BASE
            .saturating_mul(1 << self.failed)
            .mul_f64(1.0 + fastrand::f64() / 2.0);
        self.failed += 1;
        Some(wait)
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use std::collections::VecDeque;
    use std::iter;

    use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
    use tokio::net::{TcpListener, TcpStream};
    use tokio::sync::mpsc;
    use tokio::time::timeout;
    use twilight_http_ratelimiting::{Endpoint, Method as LimiterMethod};

    use super::*;
    use crate::bell::{self, BellReceiver};

    const PATIENCE: Duration = Duration::from_secs(5);
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
            let _ = captured.send(request);
            if scripted.is_empty() {
                let _ = stream.read(&mut [0; 1]).await;
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
        let mut line = String::new();
        if stream.read_line(&mut line).await.ok()? == 0 {
            return None;
        }
        let path = line.split(' ').nth(1)?.to_owned();
        let mut headers = HashMap::new();
        loop {
            line.clear();
            stream.read_line(&mut line).await.ok()?;
            let Some((name, value)) = line.trim_end().split_once(':') else {
                break;
            };
            headers.insert(name.to_ascii_lowercase(), value.trim().to_owned());
        }
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
            .map(|(name, value)| format!("{name}: {value}\r\n"))
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
        limited_rest(url, 50)
    }

    fn limited_rest(url: &str, global_limit: u16) -> (Rest, BellReceiver) {
        let (bell, receiver) = bell::pair().unwrap();
        let global_limit = NonZeroU16::new(global_limit).unwrap();
        let rest = Rest::new("token", url, global_limit, Arc::new(Inbox::new(bell))).unwrap();
        (rest, receiver)
    }

    fn request(method: &str, route: &str, params: &[(&str, &str)]) -> RestRequest {
        let params = params
            .iter()
            .map(|(name, value)| ((*name).to_owned(), (*value).to_owned()))
            .collect();
        RestRequest::parse(method, route, params, Vec::new(), None, None, None).unwrap()
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

    fn invalid_count(rest: &Rest) -> u32 {
        rest.invalid.lock().count(Instant::now())
    }

    #[test]
    fn rate_limits_are_retried_three_times_when_the_wait_is_short() {
        let short = Some(Limited {
            scope: Scope::Route,
            retry_after: MAX_RETRY_WAIT,
        });
        let long = Some(Limited {
            scope: Scope::Shared,
            retry_after: MAX_RETRY_WAIT + Duration::from_millis(1),
        });
        let mut retries = Retries::default();

        for _ in 0..RATE_LIMIT_RETRIES {
            assert_eq!(
                retries.after_response(&Method::POST, StatusCode::TOO_MANY_REQUESTS, short),
                Some(MAX_RETRY_WAIT)
            );
        }
        assert_eq!(
            retries.after_response(&Method::POST, StatusCode::TOO_MANY_REQUESTS, short),
            None
        );
        assert_eq!(
            Retries::default().after_response(&Method::GET, StatusCode::TOO_MANY_REQUESTS, long),
            None
        );
    }

    #[test]
    fn server_errors_are_retried_twice_with_growing_jittered_waits_unless_posted() {
        let mut retries = Retries::default();

        let first = retries.after_response(&Method::GET, StatusCode::BAD_GATEWAY, None);
        let second = retries.after_response(&Method::DELETE, StatusCode::SERVICE_UNAVAILABLE, None);
        let third = retries.after_response(&Method::GET, StatusCode::BAD_GATEWAY, None);

        assert!(first.is_some_and(|wait| wait >= BACKOFF_BASE && wait <= BACKOFF_BASE * 3 / 2));
        assert!(second.is_some_and(|wait| wait >= BACKOFF_BASE * 2 && wait <= BACKOFF_BASE * 3));
        assert_eq!(third, None);
        for (method, status) in [
            (Method::POST, StatusCode::INTERNAL_SERVER_ERROR),
            (Method::GET, StatusCode::NOT_IMPLEMENTED),
            (Method::GET, StatusCode::BAD_REQUEST),
            (Method::GET, StatusCode::UNAUTHORIZED),
            (Method::GET, StatusCode::FORBIDDEN),
            (Method::GET, StatusCode::NOT_FOUND),
            (Method::PUT, StatusCode::NO_CONTENT),
        ] {
            assert_eq!(
                Retries::default().after_response(&method, status, None),
                None,
                "{method} {status}"
            );
        }
    }

    #[test]
    fn transport_errors_are_retried_when_nothing_was_sent_or_the_method_is_idempotent() {
        for (method, before_sending, retried) in [
            (Method::POST, true, true),
            (Method::POST, false, false),
            (Method::PATCH, false, true),
        ] {
            assert_eq!(
                Retries::default()
                    .after_error(&method, before_sending)
                    .is_some(),
                retried,
                "{method} {before_sending}"
            );
        }
    }

    #[tokio::test]
    async fn sends_the_token_only_on_bot_routes() {
        crate::init_test_tracing();
        let mut server = Server::start(vec![reply("200 OK", &[], "hello")]).await;
        let (rest, _receiver) = rest(&server.url);
        let webhook = RestRequest::parse(
            "POST",
            "/webhooks/{webhook_id}/{webhook_token}",
            HashMap::from([
                ("webhook_id".to_owned(), "1".to_owned()),
                ("webhook_token".to_owned(), "secret".to_owned()),
            ]),
            Vec::new(),
            Some(b"{}".to_vec()),
            None,
            None,
        )
        .unwrap();

        let (code, body) = status(rest.execute(Ticket::from(1), me()).await);
        status(rest.execute(Ticket::from(1), callback()).await);
        status(rest.execute(Ticket::from(1), webhook).await);

        assert_eq!((code, &body[..]), (200, &b"hello"[..]));
        let bot = server.next().await;
        let interaction = server.next().await;
        let webhook = server.next().await;
        assert_eq!(bot.path, "/api/v10/users/@me");
        assert_eq!(bot.headers["authorization"], "Bot token");
        assert_eq!(interaction.path, "/api/v10/interactions/1/abc/callback");
        assert_eq!(webhook.path, "/api/v10/webhooks/1/secret");
        assert_eq!(webhook.headers["content-type"], "application/json");
        for captured in [&bot, &interaction, &webhook] {
            assert!(
                captured.headers["user-agent"]
                    .starts_with("DiscordBot (https://pypi.org/project/bostie, ")
            );
        }
        for unauthenticated in [&interaction, &webhook] {
            assert!(!unauthenticated.headers.contains_key("authorization"));
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
            HashMap::from([("channel_id".to_owned(), "1".to_owned())]),
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
        let same_channel = arrivals["/api/v10/channels/1/messages/20"] - first;
        let other_channel = arrivals["/api/v10/channels/2/messages/30"] - first;
        assert!(
            same_channel >= Duration::from_millis(190),
            "{same_channel:?}"
        );
        assert!(
            other_channel < Duration::from_millis(190),
            "{other_channel:?}"
        );
    }

    #[tokio::test]
    async fn an_exhausted_followup_bucket_delays_its_token_but_not_others_or_callbacks() {
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

        status(rest.execute(Ticket::from(1), followup("first")).await);
        tokio::join!(
            rest.execute(Ticket::from(2), followup("first")),
            rest.execute(Ticket::from(3), followup("second")),
            rest.execute(Ticket::from(4), callback()),
        );

        let first = server.next().await.at;
        let arrivals = arrivals(&mut server, 3).await;
        let same_token = arrivals["/api/v10/webhooks/1/first"] - first;
        let other_token = arrivals["/api/v10/webhooks/1/second"] - first;
        let callback = arrivals["/api/v10/interactions/1/abc/callback"] - first;
        assert!(same_token >= Duration::from_millis(190), "{same_token:?}");
        assert!(other_token < Duration::from_millis(190), "{other_token:?}");
        assert!(callback < Duration::from_millis(190), "{callback:?}");
    }

    #[tokio::test]
    async fn the_global_limit_is_configurable_and_spares_interaction_routes() {
        crate::init_test_tracing();
        let mut server = Server::start(Vec::new()).await;
        let (rest, _receiver) = limited_rest(&server.url, 1);

        status(rest.execute(Ticket::from(1), me()).await);
        tokio::join!(
            rest.execute(Ticket::from(2), message("1", "10")),
            rest.execute(Ticket::from(3), followup("first")),
            rest.execute(Ticket::from(4), callback()),
        );

        let first = server.next().await.at;
        let arrivals = arrivals(&mut server, 3).await;
        let bot = arrivals["/api/v10/channels/1/messages/10"] - first;
        let followup = arrivals["/api/v10/webhooks/1/first"] - first;
        let callback = arrivals["/api/v10/interactions/1/abc/callback"] - first;
        assert!(bot >= Duration::from_millis(900), "{bot:?}");
        assert!(followup < Duration::from_millis(500), "{followup:?}");
        assert!(callback < Duration::from_millis(500), "{callback:?}");
    }

    #[tokio::test]
    async fn a_user_rate_limit_is_retried_after_retry_after_and_counted_invalid() {
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
        assert_eq!(invalid_count(&rest), 1);
    }

    #[tokio::test]
    async fn a_shared_rate_limit_is_retried_without_counting_as_invalid() {
        crate::init_test_tracing();
        let shared = too_many(&[
            ("Retry-After", "0.2"),
            ("X-RateLimit-Scope", "shared"),
            ("X-RateLimit-Bucket", "abc"),
            ("X-RateLimit-Limit", "10"),
            ("X-RateLimit-Remaining", "9"),
            ("X-RateLimit-Reset-After", "0.2"),
        ]);
        let mut server = Server::start(vec![shared]).await;
        let (rest, _receiver) = rest(&server.url);

        let (code, _) = status(rest.execute(Ticket::from(1), message("1", "10")).await);

        assert_eq!(code, 200);
        assert_eq!(server.received(), 2);
        assert_eq!(invalid_count(&rest), 0);
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
        let (rest, _receiver) = rest(&server.url);
        let rest = Arc::new(rest);

        let limited = tokio::spawn({
            let rest = Arc::clone(&rest);
            async move { rest.execute(Ticket::from(1), me()).await }
        });
        let first = server.next().await.at;
        timeout(PATIENCE, async {
            while timeout(Duration::ZERO, rest.gate.wait()).await.is_ok() {
                tokio::time::sleep(Duration::from_millis(1)).await;
            }
        })
        .await
        .unwrap();
        let (other_code, _) = status(rest.execute(Ticket::from(1), message("2", "20")).await);
        let (limited_code, _) = status(limited.await.unwrap());

        assert_eq!((other_code, limited_code), (200, 200));
        let arrivals = arrivals(&mut server, 2).await;
        let waited = arrivals["/api/v10/channels/2/messages/20"] - first;
        assert!(waited >= Duration::from_millis(500), "{waited:?}");
        assert_eq!(invalid_count(&rest), 1);
    }

    #[tokio::test]
    async fn webhook_routes_wait_for_the_global_gate() {
        crate::init_test_tracing();
        let mut server = Server::start(Vec::new()).await;
        let (rest, _receiver) = rest(&server.url);
        let paused = Instant::now();
        rest.gate.pause(Duration::from_millis(300));

        status(rest.execute(Ticket::from(1), webhook()).await);

        let waited = server.next().await.at - paused;
        assert!(waited >= Duration::from_millis(300), "{waited:?}");
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
    async fn a_long_shared_rate_limit_is_delivered_to_queued_and_later_requests_on_its_route() {
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
    async fn a_rate_limit_whose_body_breaks_off_is_still_counted_and_retried() {
        crate::init_test_tracing();
        let truncated = "HTTP/1.1 429 Too Many Requests\r\nRetry-After: 0.1\r\nX-RateLimit-Scope: user\r\nConnection: close\r\nContent-Length: 100\r\n\r\n{}";
        let mut server = Server::start(vec![truncated.to_owned()]).await;
        let (rest, _receiver) = rest(&server.url);
        let post = request(
            "POST",
            "/channels/{channel_id}/messages",
            &[("channel_id", "1")],
        );

        let (code, _) = status(rest.execute(Ticket::from(1), post).await);

        assert_eq!((code, server.received()), (200, 2));
        assert_eq!(invalid_count(&rest), 1);
    }

    #[tokio::test]
    async fn a_rejected_token_stops_queued_and_later_bot_requests_but_not_interactions() {
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
    async fn a_rejected_interaction_token_does_not_stop_other_requests() {
        crate::init_test_tracing();
        let rejected = || reply("401 Unauthorized", &[], r#"{"code":50027}"#);
        let mut server = Server::start(vec![rejected(), rejected()]).await;
        let (rest, _receiver) = rest(&server.url);

        let mut codes = Vec::new();
        for (ticket, request) in [callback(), followup("first"), me(), webhook(), callback()]
            .into_iter()
            .enumerate()
        {
            let ticket = Ticket::from(u64::try_from(ticket).unwrap());
            codes.push(status(rest.execute(ticket, request).await).0);
        }

        assert_eq!(codes, [401, 401, 200, 200, 200]);
        assert_eq!(server.received(), 5);
    }

    #[tokio::test]
    async fn invalid_requests_warn_at_a_threshold_and_stop_every_request_at_the_hard_limit() {
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
    async fn client_errors_are_delivered_and_server_errors_retried_only_when_idempotent() {
        crate::init_test_tracing();
        let bad_gateway = || reply("502 Bad Gateway", &[], "");
        let mut server = Server::start(vec![
            reply("403 Forbidden", &[], "{}"),
            reply("404 Not Found", &[], "{}"),
            bad_gateway(),
            bad_gateway(),
            bad_gateway(),
            bad_gateway(),
        ])
        .await;
        let (rest, _receiver) = rest(&server.url);
        let post = request(
            "POST",
            "/channels/{channel_id}/messages",
            &[("channel_id", "1")],
        );

        for (expected, attempts) in [(403, 1), (404, 1), (502, 3)] {
            let (code, _) = status(rest.execute(Ticket::from(1), me()).await);
            assert_eq!((code, server.received()), (expected, attempts));
        }
        let (code, _) = status(rest.execute(Ticket::from(1), post).await);
        assert_eq!((code, server.received()), (502, 1));
    }

    #[tokio::test]
    async fn connection_failures_are_retried_and_reported_without_the_url() {
        crate::init_test_tracing();
        let unused = std::net::TcpListener::bind("127.0.0.1:0")
            .unwrap()
            .local_addr()
            .unwrap();
        let (rest, _receiver) = rest(&format!("http://{unused}/api/v10"));
        let webhook = request(
            "POST",
            "/webhooks/{webhook_id}/{webhook_token}",
            &[("webhook_id", "1"), ("webhook_token", "secret-token")],
        );
        let started = Instant::now();

        let delivery = rest.execute(Ticket::from(1), webhook).await;

        assert!(started.elapsed() >= BACKOFF_BASE * 3);
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
        let (rest, _receiver) = limited_rest(&server.url, 1);
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
}
