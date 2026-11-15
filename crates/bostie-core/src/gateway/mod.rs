mod close;
mod envelope;
mod identify;
mod inflate;
mod limiter;
mod shard;
#[cfg(test)]
mod tests;
mod tuning;

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Weak};
use std::time::Duration;

use bytes::Bytes;
use parking_lot::{Mutex, RwLock};
use tokio::runtime::Handle;
use tokio::sync::mpsc::{self, error::TrySendError};
use tokio::task::AbortHandle;
use tokio::time::Instant;
use tokio_websockets::Connector;

pub(crate) use self::envelope::{command, tree};
use self::identify::IdentifyGate;
pub use self::identify::IdentifyLimit;
use self::shard::{Shard, ShardSession, Signals};
use self::tuning::GatewayTuning;
use crate::error::CoreError;
use crate::ids::ShardId;
use crate::inbox::Inbox;
use crate::proxy::Proxy;
use crate::rest::Rest;
use crate::voice::tap::GatewayTap;

const COMMAND_QUEUE: usize = 128;

type Filter = Option<HashSet<Box<str>>>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SavedSession {
    pub id: String,
    pub seq: u64,
    pub resume_url: String,
}

pub struct Gateway {
    runtime: Handle,
    closed: AtomicBool,
    shared: Arc<GatewayShared>,
    shards: Mutex<HashMap<ShardId, ShardHandle>>,
}

struct GatewayShared {
    inbox: Arc<Inbox>,
    token: Box<str>,
    connector: Connector,
    proxy: Option<Proxy>,
    filter: RwLock<Filter>,
    identify: IdentifyGate,
    identify_gated: AtomicBool,
    rest: Weak<Rest>,
    tap: Arc<GatewayTap>,
    tuning: GatewayTuning,
}

struct ShardHandle {
    commands: mpsc::Sender<Bytes>,
    signals: Arc<Signals>,
    task: AbortHandle,
}

impl ShardHandle {
    fn is_running(&self) -> bool {
        !self.task.is_finished() && !self.signals.stopped.load(Ordering::Acquire)
    }
}

impl Gateway {
    pub(crate) fn new(
        runtime: Handle,
        inbox: Arc<Inbox>,
        token: &str,
        rest: Weak<Rest>,
        tap: Arc<GatewayTap>,
        proxy: Option<Proxy>,
    ) -> Result<Self, CoreError> {
        Self::with_tuning(
            runtime,
            inbox,
            token,
            rest,
            tap,
            proxy,
            GatewayTuning::default(),
        )
    }

    fn with_tuning(
        runtime: Handle,
        inbox: Arc<Inbox>,
        token: &str,
        rest: Weak<Rest>,
        tap: Arc<GatewayTap>,
        proxy: Option<Proxy>,
        tuning: GatewayTuning,
    ) -> Result<Self, CoreError> {
        Ok(Self {
            runtime,
            closed: AtomicBool::new(false),
            shared: Arc::new(GatewayShared {
                inbox,
                token: token.into(),
                connector: Connector::new().map_err(CoreError::Tls)?,
                proxy,
                filter: RwLock::new(None),
                identify: IdentifyGate::new(tuning.identify_spacing),
                identify_gated: AtomicBool::new(false),
                rest,
                tap,
                tuning,
            }),
            shards: Mutex::new(HashMap::new()),
        })
    }

    pub fn set_identify_limit(&self, limit: IdentifyLimit) {
        self.shared.identify.seed(limit, Instant::now());
    }

    pub fn gate_identifies(&self) {
        self.shared.identify_gated.store(true, Ordering::Relaxed);
    }

    pub fn approve_identify(&self, shard: ShardId, request: u64) -> Result<(), CoreError> {
        self.with_running(shard, |handle| {
            handle
                .signals
                .approved_request
                .fetch_max(request, Ordering::Release);
            handle.signals.approved.notify_one();
            Ok(())
        })
    }

    pub fn start(
        &self,
        shard: ShardId,
        url: &str,
        identify: &[u8],
        saved: Option<SavedSession>,
    ) -> Result<(), CoreError> {
        let url = shard::gateway_url(url).ok_or_else(|| {
            CoreError::InvalidRequest(format!("{url:?} is not a ws:// or wss:// gateway URL"))
        })?;
        let session = saved
            .map(|saved| {
                ShardSession::restored(&saved).ok_or_else(|| {
                    CoreError::InvalidRequest(format!(
                        "{:?} is not a ws:// or wss:// resume URL",
                        saved.resume_url
                    ))
                })
            })
            .transpose()?;
        let identify = envelope::identify_frame(identify)?;
        let mut shards = self.shards.lock();
        if self.closed.load(Ordering::Relaxed) {
            return Err(CoreError::Closed);
        }
        if shards.get(&shard).is_some_and(ShardHandle::is_running) {
            return Err(CoreError::ShardRunning(shard));
        }
        let (commands, queued) = mpsc::channel(COMMAND_QUEUE);
        let signals = Arc::new(Signals::new(identify));
        let task = Shard::new(
            shard,
            url,
            Arc::clone(&self.shared),
            queued,
            Arc::clone(&signals),
            session,
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

    pub fn set_identify(&self, shard: ShardId, identify: &[u8]) -> Result<(), CoreError> {
        let identify = envelope::identify_frame(identify)?;
        self.with_running(shard, |handle| {
            *handle.signals.identify.lock() = identify;
            Ok(())
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

    pub fn suspend(&self, shard: ShardId) -> Result<(), CoreError> {
        self.with_running(shard, |handle| {
            handle.signals.keep_session.store(true, Ordering::Release);
            handle.signals.close.notify_one();
            Ok(())
        })
    }

    pub fn latency(&self, shard: ShardId) -> Result<Option<Duration>, CoreError> {
        self.with_running(shard, |handle| Ok(handle.signals.latency.get()))
    }

    pub(crate) fn shutdown(&self) {
        let mut shards = self.shards.lock();
        self.closed.store(true, Ordering::Relaxed);
        for (_, handle) in shards.drain() {
            handle.task.abort();
        }
    }

    fn with_running<T>(
        &self,
        shard: ShardId,
        action: impl FnOnce(&ShardHandle) -> Result<T, CoreError>,
    ) -> Result<T, CoreError> {
        match self.shards.lock().get(&shard) {
            Some(handle) if handle.is_running() => action(handle),
            _ => Err(CoreError::UnknownShard(shard)),
        }
    }
}
