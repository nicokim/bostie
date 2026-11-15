mod invalid;
mod ratelimit;
mod request;
mod settings;

use std::collections::HashSet;
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
pub use self::settings::HttpSettings;

use self::invalid::InvalidRequests;
use self::ratelimit::{GlobalGate, InteractionBuckets, Limited, ResponseLimits, Scope};
use self::request::RouteClass;
use crate::backoff::backoff;
use crate::error::{CoreError, describe};
use crate::ids::Ticket;
use crate::inbox::{Delivery, FailureKind, Inbox};
use crate::proxy::{Proxy, client_builder};

const USER_AGENT: &str = concat!(
    "DiscordBot (https://pypi.org/project/bostie, ",
    env!("CARGO_PKG_VERSION"),
    ")"
);
const HTTP2_PING_INTERVAL: Duration = Duration::from_secs(30);
const RATE_LIMIT_RETRIES: u32 = 3;
const CLOUDFLARE_TIMEOUT: u16 = 524;
const UNKNOWN_WEBHOOK: u64 = 10015;
const DEAD_WEBHOOK: &str = r#"{"message":"Unknown Webhook: Discord answered 404 for it before, so bostie no longer sends to it","code":10015}"#;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Auth<'a> {
    None,
    Bot(&'a str),
    Bearer(&'a str),
}

impl<'a> Auth<'a> {
    pub(crate) const fn bot_token(self) -> Option<&'a str> {
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
    dead_webhooks: Mutex<HashSet<String>>,
    inbox: Arc<Inbox>,
    settings: HttpSettings,
}

impl Rest {
    pub(crate) fn new(
        auth: Auth<'_>,
        api_url: &str,
        global_limit: NonZeroU16,
        inbox: Arc<Inbox>,
        settings: HttpSettings,
        proxy: Option<&Proxy>,
    ) -> Result<Self, CoreError> {
        let base = Url::parse(api_url)
            .ok()
            .filter(|url| matches!(url.scheme(), "http" | "https"))
            .ok_or_else(|| CoreError::InvalidRequest(format!("invalid api url {api_url:?}")))?;
        let authorization = auth.header()?;
        let client = client_builder(proxy)?
            .user_agent(USER_AGENT)
            .connect_timeout(settings.connect_timeout)
            .read_timeout(settings.read_timeout)
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
            dead_webhooks: Mutex::new(HashSet::new()),
            inbox,
            settings,
        })
    }

    pub(crate) async fn execute(&self, ticket: Ticket, request: RestRequest) -> Delivery {
        let url = request.url(&self.base);
        let mut retries = Retries::new(self.settings);
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

    pub(crate) async fn download(&self, ticket: Ticket, url: Url) -> Delivery {
        let mut retries = Retries::new(self.settings);
        loop {
            match self.fetch(ticket, &url, &mut retries).await {
                ControlFlow::Break(delivery) => return delivery,
                ControlFlow::Continue(wait) => tokio::time::sleep(wait).await,
            }
        }
    }

    pub(crate) async fn fetch_gateway_url(&self) -> Option<String> {
        let request = RestRequest::get("/gateway").ok()?;
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
        if request.class == RouteClass::Webhook
            && self.dead_webhooks.lock().contains(webhook_of(request))
        {
            return Some(Delivery::Response {
                ticket,
                status: StatusCode::NOT_FOUND.as_u16(),
                body: Bytes::from_static(DEAD_WEBHOOK.as_bytes()),
            });
        }
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
        let max_wait = self.settings.max_rate_limit_wait;
        match request.class {
            _ if !self.settings.rate_limiter => return Ok(PermitGuard(None)),
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
            Err(error) => {
                return after_transport_error(
                    ticket,
                    &request.method,
                    &request.endpoint.path,
                    error,
                    retries,
                );
            }
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
            Ok(body) => {
                if status == StatusCode::NOT_FOUND
                    && request.class == RouteClass::Webhook
                    && sonic_rs::get(&body, ["code"])
                        .ok()
                        .and_then(|code| code.as_u64())
                        == Some(UNKNOWN_WEBHOOK)
                {
                    self.dead_webhooks
                        .lock()
                        .insert(webhook_of(request).to_owned());
                }
                ControlFlow::Break(Delivery::Response {
                    ticket,
                    status: status.as_u16(),
                    body,
                })
            }
            Err(error) => after_transport_error(
                ticket,
                &request.method,
                &request.endpoint.path,
                error,
                retries,
            ),
        }
    }

    async fn fetch(
        &self,
        ticket: Ticket,
        url: &Url,
        retries: &mut Retries,
    ) -> ControlFlow<Delivery, Duration> {
        let response = match self.client.get(url.clone()).send().await {
            Ok(response) => response,
            Err(error) => {
                return after_transport_error(ticket, &Method::GET, url.path(), error, retries);
            }
        };
        let status = response.status();
        if let Some(wait) = retries.after_response(&Method::GET, status, None) {
            tracing::debug!(path = url.path(), %status, ?wait, "retrying the download");
            return ControlFlow::Continue(wait);
        }
        match response.bytes().await {
            Ok(body) => ControlFlow::Break(Delivery::Response {
                ticket,
                status: status.as_u16(),
                body,
            }),
            Err(error) => after_transport_error(ticket, &Method::GET, url.path(), error, retries),
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
    method: &Method,
    route: &str,
    error: reqwest::Error,
    retries: &mut Retries,
) -> ControlFlow<Delivery, Duration> {
    let error = error.without_url();
    match retries.after_error(method, error.is_connect()) {
        Some(wait) => {
            tracing::debug!(
                route,
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

fn webhook_of(request: &RestRequest) -> &str {
    let path = request.endpoint.path.as_str();
    path.match_indices('/')
        .nth(1)
        .and_then(|(end, _)| path.get(..end))
        .unwrap_or(path)
}

fn held_back(retry_after: Duration) -> Bytes {
    Bytes::from(format!(
        r#"{{"message":"This route is rate limited for longer than bostie waits, so the request was not sent.","code":0,"retry_after":{:.3},"global":false}}"#,
        retry_after.as_secs_f64()
    ))
}

struct Retries {
    settings: HttpSettings,
    rate_limited: u32,
    failed: u32,
}

impl Retries {
    fn new(settings: HttpSettings) -> Self {
        Self {
            settings,
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
            if retry_after > self.settings.max_rate_limit_wait
                || self.rate_limited == RATE_LIMIT_RETRIES
            {
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
        if self.failed == self.settings.retries {
            return None;
        }
        let wait = backoff(
            self.settings.backoff_base,
            self.settings.max_rate_limit_wait,
            self.failed,
        );
        self.failed += 1;
        Some(wait)
    }
}

#[cfg(test)]
pub(crate) mod tests;
