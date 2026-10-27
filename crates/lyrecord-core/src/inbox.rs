use std::mem;
use std::pin::pin;

use bytes::Bytes;
use parking_lot::Mutex;
use tokio::sync::Notify;

use crate::bell::Bell;
use crate::ids::{ShardId, Ticket};

#[cfg(not(test))]
pub(crate) const DISPATCH_BYTES_LIMIT: usize = 64 * 1024 * 1024;
#[cfg(test)]
pub(crate) const DISPATCH_BYTES_LIMIT: usize = 64 * 1024;

#[derive(Debug)]
pub enum Delivery {
    Response {
        ticket: Ticket,
        status: u16,
        body: Bytes,
    },
    Failure {
        ticket: Ticket,
        kind: FailureKind,
        detail: String,
    },
    Dispatch {
        shard: ShardId,
        seq: u64,
        name: Box<str>,
        data: Bytes,
    },
    Disconnected {
        shard: ShardId,
        code: Option<u16>,
        reason: String,
        resuming: bool,
    },
    Stopped {
        shard: ShardId,
        code: Option<u16>,
        reason: String,
    },
    Backpressure {
        shard: ShardId,
        queued_bytes: usize,
    },
    InvalidRequests {
        count: u32,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FailureKind {
    Transport,
    InvalidToken,
    RequestBudget,
    Bug,
}

impl FailureKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Transport => "transport",
            Self::InvalidToken => "invalid_token",
            Self::RequestBudget => "request_budget",
            Self::Bug => "bug",
        }
    }
}

pub struct Inbox {
    state: Mutex<State>,
    room: Notify,
    bell: Bell,
}

#[derive(Default)]
struct State {
    deliveries: Vec<Delivery>,
    dispatch_bytes: usize,
    rung: bool,
}

impl Inbox {
    pub(crate) fn new(bell: Bell) -> Self {
        Self {
            state: Mutex::new(State::default()),
            room: Notify::new(),
            bell,
        }
    }

    pub fn push(&self, delivery: Delivery) {
        let first_since_drain = {
            let mut state = self.state.lock();
            if let Delivery::Dispatch { data, .. } = &delivery {
                state.dispatch_bytes = state.dispatch_bytes.saturating_add(data.len());
            }
            state.deliveries.push(delivery);
            !mem::replace(&mut state.rung, true)
        };
        if first_since_drain && !self.bell.ring() {
            self.state.lock().rung = false;
        }
    }

    pub fn drain(&self) -> Vec<Delivery> {
        let deliveries = {
            let mut state = self.state.lock();
            state.rung = false;
            state.dispatch_bytes = 0;
            mem::take(&mut state.deliveries)
        };
        self.room.notify_waiters();
        deliveries
    }

    pub(crate) fn queued_bytes(&self) -> usize {
        self.state.lock().dispatch_bytes
    }

    pub async fn wait_for_room(&self) {
        loop {
            let mut room = pin!(self.room.notified());
            room.as_mut().enable();
            if self.state.lock().dispatch_bytes < DISPATCH_BYTES_LIMIT {
                return;
            }
            room.await;
        }
    }
}

#[cfg(test)]
mod tests {
    use std::io::{ErrorKind, Read};
    use std::sync::Arc;
    use std::task::{Context, Waker};
    use std::time::Duration;

    use super::*;
    use crate::bell;

    fn inbox() -> (Inbox, bell::BellReceiver) {
        let (bell, receiver) = bell::pair().unwrap();
        (Inbox::new(bell), receiver)
    }

    fn failure(ticket: u64) -> Delivery {
        Delivery::Failure {
            ticket: Ticket::from(ticket),
            kind: FailureKind::Transport,
            detail: String::new(),
        }
    }

    fn dispatch(size: usize) -> Delivery {
        Delivery::Dispatch {
            shard: ShardId::from(0),
            seq: 1,
            name: "MESSAGE_CREATE".into(),
            data: Bytes::from(vec![b' '; size]),
        }
    }

    fn has_room(inbox: &Inbox) -> bool {
        pin!(inbox.wait_for_room())
            .poll(&mut Context::from_waker(Waker::noop()))
            .is_ready()
    }

    fn pending_rings(receiver: &bell::BellReceiver) -> usize {
        let mut stream = receiver.stream();
        stream.set_nonblocking(true).unwrap();
        let mut buffer = [0; 64];
        match stream.read(&mut buffer) {
            Ok(count) => count,
            Err(error) if error.kind() == ErrorKind::WouldBlock => 0,
            Err(error) => panic!("{error}"),
        }
    }

    #[test]
    fn a_burst_rings_once_and_drains_in_order() {
        let (inbox, receiver) = inbox();

        inbox.push(failure(0));
        inbox.push(dispatch(8));
        inbox.push(failure(2));

        assert_eq!(pending_rings(&receiver), 1);
        let drained = inbox.drain();
        assert!(
            matches!(
                drained.as_slice(),
                [
                    Delivery::Failure { ticket: first, .. },
                    Delivery::Dispatch { .. },
                    Delivery::Failure { ticket: last, .. },
                ] if u64::from(*first) == 0 && u64::from(*last) == 2
            ),
            "{drained:?}"
        );
    }

    #[test]
    fn rings_again_after_a_drain() {
        let (inbox, receiver) = inbox();

        inbox.push(failure(0));
        assert_eq!(pending_rings(&receiver), 1);
        inbox.drain();

        inbox.push(failure(1));
        assert_eq!(pending_rings(&receiver), 1);
        assert_eq!(inbox.drain().len(), 1);
    }

    #[test]
    fn drain_without_deliveries_is_empty() {
        let (inbox, receiver) = inbox();

        assert!(inbox.drain().is_empty());
        assert_eq!(pending_rings(&receiver), 0);
    }

    #[test]
    fn dispatches_fill_the_budget_until_drained() {
        let (inbox, _receiver) = inbox();

        inbox.push(dispatch(DISPATCH_BYTES_LIMIT - 1));
        assert!(has_room(&inbox));
        inbox.push(dispatch(1));
        assert!(!has_room(&inbox));

        inbox.drain();
        assert!(has_room(&inbox));
    }

    #[test]
    fn responses_and_failures_are_not_budgeted() {
        let (inbox, _receiver) = inbox();

        inbox.push(Delivery::Response {
            ticket: Ticket::from(1),
            status: 200,
            body: Bytes::from(vec![0; DISPATCH_BYTES_LIMIT * 2]),
        });
        inbox.push(failure(2));

        assert!(has_room(&inbox));
    }

    #[test]
    fn an_oversized_dispatch_passes_when_it_is_alone() {
        let (inbox, _receiver) = inbox();
        let oversized = DISPATCH_BYTES_LIMIT * 4;

        assert!(has_room(&inbox));
        inbox.push(dispatch(oversized));
        assert!(!has_room(&inbox));

        let drained = inbox.drain();
        assert!(
            matches!(drained.as_slice(), [Delivery::Dispatch { data, .. }] if data.len() == oversized)
        );
    }

    #[tokio::test]
    async fn draining_wakes_every_waiter() {
        crate::init_test_tracing();
        let (inbox, _receiver) = inbox();
        let inbox = Arc::new(inbox);
        inbox.push(dispatch(DISPATCH_BYTES_LIMIT));

        let waiters: Vec<_> = (0..3)
            .map(|_| {
                let inbox = Arc::clone(&inbox);
                tokio::spawn(async move { inbox.wait_for_room().await })
            })
            .collect();
        tokio::task::yield_now().await;
        assert!(waiters.iter().all(|waiter| !waiter.is_finished()));

        inbox.drain();
        for waiter in waiters {
            tokio::time::timeout(Duration::from_secs(5), waiter)
                .await
                .unwrap()
                .unwrap();
        }
    }
}
