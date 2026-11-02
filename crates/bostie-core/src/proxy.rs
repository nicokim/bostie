use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use percent_encoding::percent_decode_str;
use reqwest::Url;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;

use crate::error::{CoreError, describe};

const MAX_ANSWER_HEAD: usize = 8 * 1024;

#[derive(Clone)]
pub(crate) struct Proxy {
    url: Url,
    authorization: Option<String>,
}

impl Proxy {
    pub(crate) fn parse(url: &str) -> Result<Self, CoreError> {
        let url = Url::parse(url)
            .ok()
            .filter(|url| url.scheme() == "http" && url.host_str().is_some())
            .ok_or_else(|| {
                CoreError::InvalidRequest("the proxy must be an http:// URL with a host".to_owned())
            })?;
        let authorization = (!url.username().is_empty()).then(|| {
            let credentials = format!(
                "{}:{}",
                percent_decode_str(url.username()).decode_utf8_lossy(),
                percent_decode_str(url.password().unwrap_or_default()).decode_utf8_lossy()
            );
            format!("Basic {}", STANDARD.encode(credentials))
        });
        Ok(Self { url, authorization })
    }

    pub(crate) fn for_requests(&self) -> Result<reqwest::Proxy, CoreError> {
        reqwest::Proxy::all(self.url.as_str()).map_err(CoreError::HttpClient)
    }

    pub(crate) async fn tunnel(&self, host: &str, port: u16) -> Result<TcpStream, String> {
        let proxy_host = self
            .url
            .host_str()
            .unwrap_or_default()
            .trim_start_matches('[')
            .trim_end_matches(']');
        let proxy_port = self.url.port_or_known_default().unwrap_or(80);
        let mut stream = TcpStream::connect((proxy_host, proxy_port))
            .await
            .map_err(|error| format!("the proxy is unreachable: {}", describe(&error)))?;
        let mut request = format!("CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n");
        if let Some(authorization) = &self.authorization {
            request.push_str("Proxy-Authorization: ");
            request.push_str(authorization);
            request.push_str("\r\n");
        }
        request.push_str("\r\n");
        let head = async {
            stream.write_all(request.as_bytes()).await?;
            let mut head = Vec::new();
            while !head.ends_with(b"\r\n\r\n") && head.len() < MAX_ANSWER_HEAD {
                head.push(stream.read_u8().await?);
            }
            Ok::<_, std::io::Error>(head)
        }
        .await
        .map_err(|error| format!("the proxy dropped the tunnel: {}", describe(&error)))?;
        let status = head.split(|&byte| byte == b' ').nth(1).unwrap_or_default();
        if status.starts_with(b"2") && head.ends_with(b"\r\n\r\n") {
            return Ok(stream);
        }
        let line = head.split(|&byte| byte == b'\r').next().unwrap_or_default();
        Err(format!(
            "the proxy refused the tunnel: {}",
            String::from_utf8_lossy(line)
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_an_http_proxy_with_a_host_is_accepted() {
        for url in [
            "https://proxy:8080",
            "socks5://proxy:1080",
            "http://",
            "proxy:8080",
        ] {
            assert!(Proxy::parse(url).is_err(), "{url}");
        }
        assert!(
            Proxy::parse("http://proxy:8080")
                .unwrap()
                .authorization
                .is_none()
        );
    }
}
