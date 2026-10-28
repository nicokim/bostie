#[cfg(feature = "bench")]
#[doc(hidden)]
pub mod bench;
mod close;
mod envelope;
mod identify;
mod inflate;
mod limiter;
mod limits;
mod shard;
#[cfg(test)]
mod tests;

use std::collections::{HashMap, HashSet};
use std::sync::atomic::Ordering;
use std::sync::{Arc, Weak};
use std::time::Duration;

use bytes::Bytes;
use parking_lot::{Mutex, RwLock};
use tokio::runtime::Handle;
use tokio::sync::mpsc::{self, error::TrySendError};
use tokio::task::AbortHandle;
use tokio::time::Instant;
use tokio_websockets::Connector;

use self::identify::IdentifyGate;
pub use self::identify::IdentifyLimit;
use self::shard::{Shard, Signals, UNKNOWN_LATENCY};
use crate::error::CoreError;
use crate::ids::ShardId;
use crate::inbox::Inbox;
use crate::rest::Rest;

const COMMAND_QUEUE: usize = 128;

type Filter = Option<HashSet<Box<str>>>;

pub struct Gateway {
    runtime: Handle,
    shared: Arc<Shared>,
    shards: Mutex<HashMap<ShardId, ShardHandle>>,
}

struct Shared {
    inbox: Arc<Inbox>,
    token: Box<str>,
    connector: Connector,
    filter: RwLock<Filter>,
    identify: IdentifyGate,
    rest: Weak<Rest>,
}

struct ShardHandle {
    commands: mpsc::Sender<Bytes>,
    signals: Arc<Signals>,
    task: AbortHandle,
}

impl Gateway {
    pub fn new(
        runtime: Handle,
        inbox: Arc<Inbox>,
        token: &str,
        rest: Weak<Rest>,
    ) -> Result<Self, CoreError> {
        Ok(Self {
            runtime,
            shared: Arc::new(Shared {
                inbox,
                token: token.into(),
                connector: Connector::new().map_err(CoreError::Tls)?,
                filter: RwLock::new(None),
                identify: IdentifyGate::default(),
                rest,
            }),
            shards: Mutex::new(HashMap::new()),
        })
    }

    pub fn set_identify_limit(&self, limit: IdentifyLimit) {
        self.shared.identify.seed(limit, Instant::now());
    }

    pub fn start(&self, shard: ShardId, url: &str, identify: Bytes) -> Result<(), CoreError> {
        let url = shard::gateway_url(url).ok_or_else(|| {
            CoreError::InvalidRequest(format!("{url:?} is not a ws:// or wss:// gateway URL"))
        })?;
        let identify = envelope::identify_frame(&identify)?;
        let mut shards = self.shards.lock();
        if shards
            .get(&shard)
            .is_some_and(|handle| !handle.task.is_finished())
        {
            return Err(CoreError::ShardRunning(shard));
        }
        let (commands, queued) = mpsc::channel(COMMAND_QUEUE);
        let signals = Arc::new(Signals::new());
        let task = Shard::new(
            shard,
            url,
            identify,
            Arc::clone(&self.shared),
            queued,
            Arc::clone(&signals),
        );
        let task = self.runtime.spawn(task.run()).abort_handle();
        shards.insert(
            shard,
            ShardHandle {
                commands,
                signals,
                task,
            },
        );
        Ok(())
    }

    pub fn send(&self, shard: ShardId, payload: Bytes) -> Result<(), CoreError> {
        envelope::check_command(&payload)?;
        self.with_running(shard, |handle| {
            handle
                .commands
                .try_send(payload)
                .map_err(|error| match error {
                    TrySendError::Full(_) => CoreError::SendQueueFull(shard),
                    TrySendError::Closed(_) => CoreError::UnknownShard(shard),
                })
        })
    }

    pub fn set_filter(&self, names: Option<Vec<String>>) {
        *self.shared.filter.write() =
            names.map(|names| names.into_iter().map(String::into_boxed_str).collect());
    }

    pub fn reconnect(&self, shard: ShardId) -> Result<(), CoreError> {
        self.with_running(shard, |handle| {
            handle.signals.reconnect.notify_one();
            Ok(())
        })
    }

    pub fn close(&self, shard: ShardId) -> Result<(), CoreError> {
        self.with_running(shard, |handle| {
            handle.signals.close.notify_one();
            Ok(())
        })
    }

    pub fn latency(&self, shard: ShardId) -> Result<Option<Duration>, CoreError> {
        self.with_running(shard, |handle| {
            let nanos = handle.signals.latency.load(Ordering::Relaxed);
            Ok((nanos != UNKNOWN_LATENCY).then(|| Duration::from_nanos(nanos)))
        })
    }

    pub fn shutdown(&self) {
        for (_, handle) in self.shards.lock().drain() {
            handle.task.abort();
        }
    }

    fn with_running<T>(
        &self,
        shard: ShardId,
        action: impl FnOnce(&ShardHandle) -> Result<T, CoreError>,
    ) -> Result<T, CoreError> {
        match self.shards.lock().get(&shard) {
            Some(handle) if !handle.task.is_finished() => action(handle),
            _ => Err(CoreError::UnknownShard(shard)),
        }
    }
}
