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
    let shared = match RUNTIME.get() {
        Some(existing) => existing,
        None => {
            let built = OwnedRuntime {
                owner: process::id(),
                runtime: build()?,
            };
            RUNTIME.get_or_init(|| built)
        }
    };
    if shared.owner != process::id() {
        return Err(CoreError::Forked {
            owner: shared.owner,
        });
    }
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
