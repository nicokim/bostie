use std::process;
use std::sync::OnceLock;
use std::thread;

use tokio::runtime::{Builder, Handle, Runtime};

use crate::error::CoreError;

const MIN_WORKERS: usize = 2;
const MAX_WORKERS: usize = 4;

struct OwnedRuntime {
    owner: u32,
    runtime: Runtime,
}

static RUNTIME: OnceLock<OwnedRuntime> = OnceLock::new();

pub(crate) fn shared() -> Result<Handle, CoreError> {
    let shared = if let Some(existing) = RUNTIME.get() {
        existing
    } else {
        let built = OwnedRuntime {
            owner: process::id(),
            runtime: build()?,
        };
        RUNTIME.get_or_init(|| built)
    };
    check_owner()?;
    Ok(shared.runtime.handle().clone())
}

pub(crate) fn check_owner() -> Result<(), CoreError> {
    match RUNTIME.get() {
        Some(shared) if shared.owner != process::id() => Err(CoreError::Forked {
            owner: shared.owner,
        }),
        _ => Ok(()),
    }
}

pub(crate) fn is_owner() -> bool {
    check_owner().is_ok()
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
