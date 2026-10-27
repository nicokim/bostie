use bytes::Bytes;
use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use reqwest::{Method, Url};

use crate::error::CoreError;

#[derive(Debug)]
pub struct HttpRequest {
    method: Method,
    url: Url,
    headers: HeaderMap,
    body: Option<Bytes>,
}

impl HttpRequest {
    pub fn parse(
        method: &str,
        url: &str,
        headers: &[(String, String)],
        body: Option<Vec<u8>>,
    ) -> Result<Self, CoreError> {
        let method = Method::from_bytes(method.as_bytes())
            .map_err(|_| CoreError::InvalidRequest(format!("invalid method {method:?}")))?;
        let url = Url::parse(url)
            .map_err(|error| CoreError::InvalidRequest(format!("invalid url: {error}")))?;
        let headers = headers
            .iter()
            .map(|(name, value)| parse_header(name, value))
            .collect::<Result<HeaderMap, _>>()?;
        Ok(Self {
            method,
            url,
            headers,
            body: body.map(Bytes::from),
        })
    }
}

fn parse_header(name: &str, value: &str) -> Result<(HeaderName, HeaderValue), CoreError> {
    let parsed_name = HeaderName::from_bytes(name.as_bytes())
        .map_err(|_| CoreError::InvalidRequest(format!("invalid header name {name:?}")))?;
    let parsed_value = HeaderValue::from_str(value)
        .map_err(|_| CoreError::InvalidRequest(format!("invalid value for header {name:?}")))?;
    Ok((parsed_name, parsed_value))
}

pub(crate) async fn send(
    client: &reqwest::Client,
    request: HttpRequest,
) -> Result<(u16, Bytes), reqwest::Error> {
    let mut builder = client
        .request(request.method, request.url)
        .headers(request.headers);
    if let Some(body) = request.body {
        builder = builder.body(body);
    }
    let response = builder.send().await?;
    let status = response.status().as_u16();
    Ok((status, response.bytes().await?))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_a_valid_request() {
        let headers = [("Authorization".to_owned(), "Bot abc".to_owned())];
        let request =
            HttpRequest::parse("POST", "https://discord.com/api/v10/x", &headers, None).unwrap();

        assert_eq!(request.method, Method::POST);
        assert_eq!(request.headers["authorization"], "Bot abc");
    }

    #[test]
    fn rejects_invalid_parts() {
        let no_headers: [(String, String); 0] = [];
        let bad_header = [("bad name".to_owned(), "v".to_owned())];

        for (method, url, headers) in [
            ("GE T", "https://x.y", &no_headers[..]),
            ("GET", "not a url", &no_headers[..]),
            ("GET", "https://x.y", &bad_header[..]),
        ] {
            let error = HttpRequest::parse(method, url, headers, None).unwrap_err();
            assert!(matches!(error, CoreError::InvalidRequest(_)), "{error}");
        }
    }
}
