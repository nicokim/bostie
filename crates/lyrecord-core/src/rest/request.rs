use std::borrow::Cow;
use std::collections::HashMap;
use std::hash::{DefaultHasher, Hash, Hasher};

use bytes::Bytes;
use percent_encoding::{NON_ALPHANUMERIC, utf8_percent_encode};
use reqwest::header::{CONTENT_TYPE, HeaderMap, HeaderName, HeaderValue};
use reqwest::{Method, Url};
use twilight_http_ratelimiting::{Endpoint, Method as LimiterMethod};

use crate::error::CoreError;

const MAJOR_PARAMS: [&str; 3] = ["channel_id", "guild_id", "webhook_id"];
const INTERACTION_TOKEN: &str = "interaction_token";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum RouteClass {
    Callback,
    Interaction,
    Webhook,
    Anonymous,
    Bot,
}

impl RouteClass {
    pub(super) fn waits_for_the_global_gate(self) -> bool {
        !matches!(self, Self::Callback | Self::Interaction)
    }
}

#[derive(Debug)]
pub struct RestRequest {
    pub(super) method: Method,
    pub(super) class: RouteClass,
    pub(super) endpoint: Endpoint,
    pub(super) headers: HeaderMap,
    pub(super) body: Option<Bytes>,
    segments: Vec<String>,
    query: Vec<(String, String)>,
}

impl RestRequest {
    pub fn parse(
        method: &str,
        route: &str,
        params: HashMap<String, String>,
        query: Vec<(String, String)>,
        body: Option<Vec<u8>>,
        content_type: Option<String>,
        reason: Option<String>,
    ) -> Result<Self, CoreError> {
        let (method, limiter_method) = match method {
            "GET" => (Method::GET, LimiterMethod::Get),
            "POST" => (Method::POST, LimiterMethod::Post),
            "PUT" => (Method::PUT, LimiterMethod::Put),
            "PATCH" => (Method::PATCH, LimiterMethod::Patch),
            "DELETE" => (Method::DELETE, LimiterMethod::Delete),
            _ => {
                return Err(CoreError::InvalidRequest(format!(
                    "unsupported method {method:?}"
                )));
            }
        };
        let template = route
            .strip_prefix('/')
            .filter(|_| !route.contains('?'))
            .ok_or_else(|| {
                CoreError::InvalidRequest(format!(
                    "route {route:?} must start with '/' and contain no '?'"
                ))
            })?;

        let mut segments = Vec::new();
        let mut key = Vec::new();
        let mut placeholders = Vec::new();
        for segment in template.split('/') {
            let Some(name) = placeholder(segment)? else {
                segments.push(segment.to_owned());
                key.push(Cow::Borrowed(segment));
                continue;
            };
            let value = params.get(name).ok_or_else(|| {
                CoreError::InvalidRequest(format!("route {route:?} needs the param {name:?}"))
            })?;
            if matches!(value.as_str(), "" | "." | "..") || value.contains(['\t', '\n', '\r']) {
                return Err(CoreError::InvalidRequest(format!(
                    "the param {name:?} is empty, \".\" or \"..\", or contains a tab, newline or CR"
                )));
            }
            segments.push(value.clone());
            key.push(if name == INTERACTION_TOKEN {
                Cow::Owned(digest(value))
            } else if MAJOR_PARAMS.contains(&name) {
                Cow::Borrowed(value.as_str())
            } else {
                Cow::Borrowed(segment)
            });
            placeholders.push(name);
        }
        if let Some(extra) = params
            .keys()
            .find(|name| !placeholders.contains(&name.as_str()))
        {
            return Err(CoreError::InvalidRequest(format!(
                "route {route:?} has no placeholder for the param {extra:?}"
            )));
        }

        let class = if route.starts_with("/interactions/") {
            RouteClass::Callback
        } else if placeholders.contains(&INTERACTION_TOKEN) {
            RouteClass::Interaction
        } else if placeholders.contains(&"webhook_token") {
            RouteClass::Webhook
        } else if route == "/gateway" {
            RouteClass::Anonymous
        } else {
            RouteClass::Bot
        };

        let mut headers = HeaderMap::new();
        if let Some(content_type) = content_type {
            headers.insert(CONTENT_TYPE, header_value(content_type)?);
        } else if body.is_some() {
            headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        }
        if let Some(reason) = reason {
            let encoded = utf8_percent_encode(&reason, NON_ALPHANUMERIC).to_string();
            headers.insert(
                HeaderName::from_static("x-audit-log-reason"),
                header_value(encoded)?,
            );
        }

        Ok(Self {
            method,
            class,
            endpoint: Endpoint {
                method: limiter_method,
                path: key.join("/"),
            },
            headers,
            body: body.map(Bytes::from),
            segments,
            query,
        })
    }

    pub(super) fn url(&self, base: &Url) -> Url {
        let mut url = base.clone();
        if let Ok(mut path) = url.path_segments_mut() {
            path.pop_if_empty().extend(&self.segments);
        }
        if !self.query.is_empty() {
            url.query_pairs_mut().extend_pairs(&self.query);
        }
        url
    }
}

fn placeholder(segment: &str) -> Result<Option<&str>, CoreError> {
    if !segment.contains(['{', '}']) {
        return Ok(None);
    }
    segment
        .strip_prefix('{')
        .and_then(|rest| rest.strip_suffix('}'))
        .filter(|name| {
            !name.is_empty()
                && name
                    .bytes()
                    .all(|byte| byte.is_ascii_lowercase() || byte == b'_')
        })
        .map(Some)
        .ok_or_else(|| CoreError::InvalidRequest(format!("invalid placeholder {segment:?}")))
}

fn digest(token: &str) -> String {
    let mut hasher = DefaultHasher::new();
    token.hash(&mut hasher);
    format!("{:016x}", hasher.finish())
}

fn header_value(value: String) -> Result<HeaderValue, CoreError> {
    HeaderValue::try_from(value)
        .map_err(|error| CoreError::InvalidRequest(format!("invalid header value: {error}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(method: &str, route: &str, params: &[(&str, &str)]) -> Result<RestRequest, CoreError> {
        let params = params
            .iter()
            .map(|(name, value)| ((*name).to_owned(), (*value).to_owned()))
            .collect();
        RestRequest::parse(method, route, params, Vec::new(), None, None, None)
    }

    #[test]
    fn substitutes_params_as_encoded_path_segments() {
        let route = "/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me";

        for (emoji, encoded) in [
            ("🔥", "%F0%9F%94%A5"),
            ("name:123", "name:123"),
            ("a/b", "a%2Fb"),
        ] {
            let request = parse(
                "PUT",
                route,
                &[("channel_id", "1"), ("message_id", "2"), ("emoji", emoji)],
            )
            .unwrap();
            for base in [
                "https://discord.com/api/v10",
                "https://discord.com/api/v10/",
            ] {
                assert_eq!(
                    request.url(&Url::parse(base).unwrap()).as_str(),
                    format!(
                        "https://discord.com/api/v10/channels/1/messages/2/reactions/{encoded}/@me"
                    )
                );
            }
        }
    }

    #[test]
    fn appends_repeated_query_pairs() {
        let query = [("id", "1"), ("id", "2"), ("with_counts", "true")]
            .map(|(name, value)| (name.to_owned(), value.to_owned()))
            .to_vec();
        let request =
            RestRequest::parse("GET", "/users/@me", HashMap::new(), query, None, None, None)
                .unwrap();

        let url = request.url(&Url::parse("https://discord.com/api/v10").unwrap());

        assert_eq!(
            url.as_str(),
            "https://discord.com/api/v10/users/@me?id=1&id=2&with_counts=true"
        );
    }

    #[test]
    fn rejects_bad_methods_routes_and_params() {
        for (method, route, params) in [
            ("get", "/users/@me", &[][..]),
            ("HEAD", "/users/@me", &[]),
            ("GET", "users/@me", &[]),
            ("GET", "/users/@me?limit=1", &[]),
            ("GET", "/channels/{channel_id}", &[]),
            ("GET", "/users/@me", &[("channel_id", "1")]),
            ("GET", "/channels/{Channel}", &[("Channel", "1")]),
            ("GET", "/channels/{}", &[]),
            ("GET", "/channels/x{channel_id}", &[("channel_id", "1")]),
            ("GET", "/channels/{channel_id}", &[("channel_id", "")]),
            ("GET", "/channels/{channel_id}", &[("channel_id", ".")]),
            ("GET", "/channels/{channel_id}", &[("channel_id", "..")]),
            ("GET", "/channels/{channel_id}", &[("channel_id", ".\t.")]),
            ("GET", "/channels/{channel_id}", &[("channel_id", "1\n2")]),
        ] {
            let error = parse(method, route, params).unwrap_err();
            assert!(
                matches!(error, CoreError::InvalidRequest(_)),
                "{route}: {error}"
            );
        }

        let bad_content_type = RestRequest::parse(
            "POST",
            "/users/@me",
            HashMap::new(),
            Vec::new(),
            Some(Vec::new()),
            Some("text\nplain".to_owned()),
            None,
        );
        assert!(matches!(
            bad_content_type,
            Err(CoreError::InvalidRequest(_))
        ));
    }

    #[test]
    fn limiter_keys_substitute_only_major_params() {
        for (method, route, params, limiter_method, key) in [
            (
                "GET",
                "/channels/{channel_id}/messages/{message_id}",
                &[("channel_id", "1"), ("message_id", "2")][..],
                LimiterMethod::Get,
                "channels/1/messages/{message_id}",
            ),
            (
                "PUT",
                "/guilds/{guild_id}/members/{user_id}/roles/{role_id}",
                &[("guild_id", "3"), ("user_id", "4"), ("role_id", "5")],
                LimiterMethod::Put,
                "guilds/3/members/{user_id}/roles/{role_id}",
            ),
            (
                "POST",
                "/webhooks/{webhook_id}/{webhook_token}",
                &[("webhook_id", "6"), ("webhook_token", "secret")],
                LimiterMethod::Post,
                "webhooks/6/{webhook_token}",
            ),
        ] {
            let request = parse(method, route, params).unwrap();

            assert_eq!(
                request.endpoint,
                Endpoint {
                    method: limiter_method,
                    path: key.to_owned()
                }
            );
        }
    }

    #[test]
    fn interaction_tokens_key_their_own_resource_without_appearing_in_it() {
        let route = "/webhooks/{application_id}/{interaction_token}/messages/@original";
        let key = |token| {
            parse(
                "PATCH",
                route,
                &[("application_id", "1"), ("interaction_token", token)],
            )
            .unwrap()
            .endpoint
            .path
        };

        let first = key("first-secret-token");

        assert!(first.starts_with("webhooks/{application_id}/"), "{first}");
        assert!(first.ends_with("/messages/@original"), "{first}");
        assert!(!first.contains("secret"), "{first}");
        assert_eq!(first, key("first-secret-token"));
        assert_ne!(first, key("second-secret-token"));
    }

    #[test]
    fn classifies_routes_by_their_credentials() {
        for (route, params, class) in [
            (
                "/interactions/{interaction_id}/{interaction_token}/callback",
                &[("interaction_id", "1"), ("interaction_token", "t")][..],
                RouteClass::Callback,
            ),
            (
                "/webhooks/{application_id}/{interaction_token}/messages/@original",
                &[("application_id", "1"), ("interaction_token", "t")],
                RouteClass::Interaction,
            ),
            (
                "/webhooks/{webhook_id}/{webhook_token}",
                &[("webhook_id", "1"), ("webhook_token", "t")],
                RouteClass::Webhook,
            ),
            (
                "/webhooks/{webhook_id}",
                &[("webhook_id", "1")],
                RouteClass::Bot,
            ),
            ("/gateway", &[], RouteClass::Anonymous),
            ("/gateway/bot", &[], RouteClass::Bot),
            (
                "/channels/{channel_id}",
                &[("channel_id", "1")],
                RouteClass::Bot,
            ),
        ] {
            assert_eq!(
                parse("POST", route, params).unwrap().class,
                class,
                "{route}"
            );
        }
    }
}
