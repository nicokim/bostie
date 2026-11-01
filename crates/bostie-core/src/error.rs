use std::error::Error;
use std::io;

use crate::ids::{GuildId, Handle, ShardId};
use crate::voice::dave::DaveError;

#[derive(Debug, thiserror::Error)]
pub enum CoreError {
    #[error(
        "bostie was started in process {owner} and cannot be used in a forked child; \
         start child processes with the 'spawn' or 'forkserver' method"
    )]
    Forked { owner: u32 },
    #[error("this core is closed")]
    Closed,
    #[error("{0}")]
    InvalidRequest(String),
    #[error("shard {0} is not running")]
    UnknownShard(ShardId),
    #[error("shard {0} is already running")]
    ShardRunning(ShardId),
    #[error("the send queue of shard {0} is full")]
    SendQueueFull(ShardId),
    #[error("no voice connection in guild {0}")]
    UnknownVoice(GuildId),
    #[error("guild {0} already has a voice connection")]
    VoiceRunning(GuildId),
    #[error("the voice command queue of guild {0} is full")]
    VoiceQueueFull(GuildId),
    #[error("voice handle {0} is closed")]
    HandleClosed(Handle),
    #[error(transparent)]
    Dave(#[from] DaveError),
    #[error("failed to start the async runtime")]
    Runtime(#[source] io::Error),
    #[error("failed to create the wake-up socket")]
    Bell(#[source] io::Error),
    #[error("failed to build the http client")]
    HttpClient(#[source] reqwest::Error),
    #[error("failed to set up TLS for the gateway")]
    Tls(#[source] tokio_websockets::Error),
}

pub fn describe(error: &dyn Error) -> String {
    let mut text = error.to_string();
    for cause in std::iter::successors(error.source(), |&cause| cause.source()) {
        text.push_str(": ");
        text.push_str(&cause.to_string());
    }
    text
}
