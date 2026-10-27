use std::error::Error;
use std::io;

#[derive(Debug, thiserror::Error)]
pub enum CoreError {
    #[error(
        "lyrecord was started in process {owner} and cannot be used in a forked child; \
         start child processes with the 'spawn' or 'forkserver' method"
    )]
    Forked { owner: u32 },
    #[error("this core is closed")]
    Closed,
    #[error("{0}")]
    InvalidRequest(String),
    #[error("failed to start the async runtime")]
    Runtime(#[source] io::Error),
    #[error("failed to create the wake-up socket")]
    Bell(#[source] io::Error),
    #[error("failed to build the http client")]
    HttpClient(#[source] reqwest::Error),
}

pub fn describe(error: &dyn Error) -> String {
    let mut text = error.to_string();
    for cause in std::iter::successors(error.source(), |&cause| cause.source()) {
        text.push_str(": ");
        text.push_str(&cause.to_string());
    }
    text
}
