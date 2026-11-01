use std::process;
use std::sync::OnceLock;
use std::thread;

use tokio::runtime::{Builder, Handle, Runtime};

use crate::error::CoreError;

const MIN_WORKERS: usize = 2;
const MAX_WORKERS: usize = 4;

#[derive(Clone, Copy)]
pub(crate) struct Owner(u32);

impl Owner {
    pub(crate) fn current() -> Self {
        Self(process::id())
    }

    pub(crate) fn is_current(self) -> bool {
        self.0 == process::id()
    }

    pub(crate) fn check(self) -> Result<(), CoreError> {
        if self.is_current() {
            Ok(())
        } else {
            Err(CoreError::Forked { owner: self.0 })
        }
    }

    #[cfg(test)]
    pub(crate) fn foreign() -> Self {
        Self(process::id().wrapping_add(1))
    }
}

struct OwnedRuntime {
    owner: Owner,
    runtime: Runtime,
}

static RUNTIME: OnceLock<OwnedRuntime> = OnceLock::new();

pub(crate) fn shared() -> Result<Handle, CoreError> {
    let shared = if let Some(existing) = RUNTIME.get() {
        existing
    } else {
        let built = OwnedRuntime {
            owner: Owner::current(),
            runtime: build()?,
        };
        RUNTIME.get_or_init(|| built)
    };
    shared.owner.check()?;
    Ok(shared.runtime.handle().clone())
}

fn build() -> Result<Runtime, CoreError> {
    let workers = thread::available_parallelism()
        .map_or(MIN_WORKERS, usize::from)
        .clamp(MIN_WORKERS, MAX_WORKERS);
    Builder::new_multi_thread()
        .worker_threads(workers)
        .thread_name("bostie-io")
        .enable_all()
        .build()
        .map_err(CoreError::Runtime)
}
