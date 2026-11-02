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

pub use self::limits::HttpSettings;
pub use self::request::RestRequest;

use self::invalid::InvalidRequests;
use self::ratelimit::{GlobalGate, InteractionBuckets, Limited, ResponseLimits, Scope};
use self::request::RouteClass;
use crate::error::{CoreError, describe};
use crate::ids::Ticket;
use crate::inbox::{Delivery, FailureKind, Inbox};
use crate::proxy::Proxy;

const USER_AGENT: &str = concat!(
    "DiscordBot (https://pypi.org/project/bostie, ",
    env!("CARGO_PKG_VERSION"),
    ")"
);
const HTTP2_PING_INTERVAL: Duration = Duration::from_secs(30);
const RATE_LIMIT_RETRIES: u32 = 3;
const CLOUDFLARE_TIMEOUT: u16 = 524;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Auth<'a> {
    None,
    Bot(&'a str),
    Bearer(&'a str),
}

impl<'a> Auth<'a> {
    pub const fn bot_token(self) -> Option<&'a str> {
        match self {
            Self::Bot(token) => Some(token),
            Self::None | Self::Bearer(_) => None,
        }
    }

    fn header(self) -> Result<Option<HeaderValue>, CoreError> {
        let value = match self {
            Self::None => return Ok(None),
            Self::Bot(token) => format!("Bot {token}"),
            Self::Bearer(token) => format!("Bearer {token}"),
        };
        let Ok(mut header) = HeaderValue::try_from(value) else {
            return Err(CoreError::InvalidRequest(
                "the token contains invalid characters".to_owned(),
            ));
        };
        header.set_sensitive(true);
        Ok(Some(header))
    }
}

pub(crate) struct Rest {
    client: reqwest::Client,
    limiter: RateLimiter,
    interaction_buckets: Mutex<InteractionBuckets>,
    gate: GlobalGate,
    invalid: Mutex<InvalidRequests>,
    authorization: Option<HeaderValue>,
    base: Url,
    token_invalid: AtomicBool,
    inbox: Arc<Inbox>,
    limits: HttpSettings,
}

impl Rest {
    pub(crate) fn new(
        auth: Auth<'_>,
        api_url: &str,
        global_limit: NonZeroU16,
        inbox: Arc<Inbox>,
        limits: HttpSettings,
        proxy: Option<&Proxy>,
    ) -> Result<Self, CoreError> {
        let base = Url::parse(api_url)
            .ok()
            .filter(|url| matches!(url.scheme(), "http" | "https"))
            .ok_or_else(|| CoreError::InvalidRequest(format!("invalid api url {api_url:?}")))?;
        let authorization = auth.header()?;
        let builder = match proxy {
            Some(proxy) => reqwest::Client::builder().proxy(proxy.for_requests()?),
            None => reqwest::Client::builder().no_proxy(),
        };
        let client = builder
            .user_agent(USER_AGENT)
            .connect_timeout(limits.connect_timeout)
            .read_timeout(limits.read_timeout)
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
            authorization,
            base,
            token_invalid: AtomicBool::new(false),
            inbox,
            limits,
        })
    }

    pub(crate) async fn execute(&self, ticket: Ticket, request: RestRequest) -> Delivery {
        let url = request.url(&self.base);
        let mut retries = Retries::new(self.limits);
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
                    tracing::warn!(
                        route = %request.endpoint.path,
                        ?retry_after,
                        "held back: the route stays rate limited for longer than bostie waits"
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
            &HashMap::new(),
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
        if request.class == RouteClass::Bot && self.authorization.is_none() {
            return Some(Delivery::Failure {
                ticket,
                kind: FailureKind::NoToken,
                detail: "this client has no token, so it sends only webhook, interaction and OAuth2 token requests"
                    .to_owned(),
            });
        }
        if request.class == RouteClass::Bot && self.token_invalid.load(Ordering::Relaxed) {
            return Some(Delivery::Failure {
                ticket,
                kind: FailureKind::InvalidToken,
                detail:
                    "Discord rejected the token, so bostie no longer sends requests that need it"
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
        let max_wait = self.limits.max_retry_wait;
        match request.class {
            RouteClass::Callback => return Ok(PermitGuard(None)),
            RouteClass::Interaction => {
                let wait = self
                    .interaction_buckets
                    .lock()
                    .wait(&request.endpoint, Instant::now());
                if wait > max_wait {
                    return Err(wait);
                }
                if !wait.is_zero() {
                    tokio::time::sleep(wait).await;
                }
                return Ok(PermitGuard(None));
            }
            RouteClass::Webhook | RouteClass::Anonymous | RouteClass::Bot => {}
        }
        let acquire = self
            .limiter
            .acquire_if(request.endpoint.clone(), move |bucket| {
                bucket.is_none_or(|bucket| within_reach(bucket, max_wait))
            });
        let mut acquire = pin!(acquire);
        loop {
            let refused = match tokio::time::timeout(max_wait, acquire.as_mut()).await {
                Ok(Some(permit)) => return Ok(PermitGuard(Some(permit))),
                Ok(None) => true,
                Err(_) => false,
            };
            let bucket = self.limiter.bucket(request.endpoint.clone()).await;
            if refused || bucket.is_some_and(|bucket| !within_reach(bucket, max_wait)) {
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
        if let (RouteClass::Bot, Some(authorization)) = (request.class, &self.authorization) {
            builder = builder.header(AUTHORIZATION, authorization.clone());
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
        if let Some(Limited { scope, retry_after }) = limits.limited {
            if scope == Scope::Shared {
                tracing::debug!(route = %request.endpoint.path, ?retry_after, "shared rate limit");
            } else {
                tracing::warn!(route = %request.endpoint.path, ?scope, ?retry_after, "rate limited");
            }
        }
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
            tracing::debug!("Discord rejected the token");
            self.token_invalid.store(true, Ordering::Relaxed);
        }
        if let Some(Limited {
            scope: Scope::Global,
            retry_after,
        }) = limited
        {
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

fn within_reach(bucket: Bucket, max_wait: Duration) -> bool {
    bucket.remaining > 0 || bucket.reset_at.saturating_duration_since(Instant::now()) <= max_wait
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

struct Retries {
    limits: HttpSettings,
    rate_limited: u32,
    failed: u32,
}

impl Retries {
    fn new(limits: HttpSettings) -> Self {
        Self {
            limits,
            rate_limited: 0,
            failed: 0,
        }
    }

    fn after_response(
        &mut self,
        method: &Method,
        status: StatusCode,
        limited: Option<Limited>,
    ) -> Option<Duration> {
        if let Some(Limited { retry_after, .. }) = limited {
            if retry_after > self.limits.max_retry_wait || self.rate_limited == RATE_LIMIT_RETRIES {
                return None;
            }
            self.rate_limited += 1;
            return Some(retry_after);
        }
        let retried_status = matches!(status.as_u16(), 500 | 502 | 503 | 504 | CLOUDFLARE_TIMEOUT);
        if retried_status && *method != Method::POST {
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
        if self.failed == self.limits.retries {
            return None;
        }
        let wait = self
            .limits
            .backoff_base
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
    pub(crate) const LIMITS: HttpSettings = HttpSettings {
        max_retry_wait: Duration::from_secs(1),
        retries: 2,
        backoff_base: Duration::from_millis(10),
        connect_timeout: Duration::from_secs(10),
        read_timeout: Duration::from_secs(60),
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
        limited_rest(url, 50, LIMITS)
    }

    fn limited_rest(url: &str, global_limit: u16, limits: HttpSettings) -> (Rest, BellReceiver) {
        rest_with(Auth::Bot("token"), url, global_limit, limits)
    }

    fn rest_with(
        auth: Auth<'_>,
        url: &str,
        global_limit: u16,
        limits: HttpSettings,
    ) -> (Rest, BellReceiver) {
        let (bell, receiver) = bell::pair().unwrap();
        let global_limit = NonZeroU16::new(global_limit).unwrap();
        let inbox = Arc::new(Inbox::new(bell));
        let rest = Rest::new(auth, url, global_limit, inbox, limits, None).unwrap();
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
            retry_after: LIMITS.max_retry_wait,
        });
        let long = Some(Limited {
            scope: Scope::Shared,
            retry_after: LIMITS.max_retry_wait + Duration::from_millis(1),
        });
        let mut retries = Retries::new(LIMITS);

        for _ in 0..RATE_LIMIT_RETRIES {
            assert_eq!(
                retries.after_response(&Method::POST, StatusCode::TOO_MANY_REQUESTS, short),
                Some(LIMITS.max_retry_wait)
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
            Retries::new(LIMITS).after_response(&Method::GET, StatusCode::TOO_MANY_REQUESTS, long),
            None
        );
    }

    #[test]
    fn server_errors_retry_twice_with_jittered_backoff_unless_posted() {
        let mut retries = Retries::new(LIMITS);

        let first = retries.after_response(&Method::GET, StatusCode::BAD_GATEWAY, None);
        let second = retries.after_response(&Method::DELETE, StatusCode::SERVICE_UNAVAILABLE, None);
        let third = retries.after_response(&Method::GET, StatusCode::BAD_GATEWAY, None);

        let base = LIMITS.backoff_base;
        assert!(first.is_some_and(|wait| wait >= base && wait <= base * 3 / 2));
        assert!(second.is_some_and(|wait| wait >= base * 2 && wait <= base * 3));
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
                Retries::new(LIMITS)
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
                Retries::new(LIMITS)
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
        let rest = Rest::new(auth, api_url, global_limit, inbox, LIMITS, Some(&proxy));

        let (got, _) = status(rest.unwrap().execute(Ticket::from(1), me()).await);

        assert_eq!(got, 200);
        assert_eq!(server.next().await.path, format!("{api_url}/users/@me"));
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
            let (rest, _receiver) = rest_with(auth, &server.url, 50, LIMITS);
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
        let (rest, _receiver) = limited_rest(&server.url, 1, LIMITS);

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
        let limits = HttpSettings {
            max_retry_wait: Duration::from_millis(100),
            ..LIMITS
        };
        let (rest, _receiver) = limited_rest(&server.url, 50, limits);

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

        assert!(started.elapsed() >= LIMITS.backoff_base * 3);
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
        let (rest, _receiver) = limited_rest(&server.url, 1, LIMITS);
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
