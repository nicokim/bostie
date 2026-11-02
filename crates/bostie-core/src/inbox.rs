use std::mem;
use std::pin::pin;
use std::time::{Duration, Instant};

use bytes::Bytes;
use parking_lot::Mutex;
use tokio::sync::Notify;

use crate::bell::Bell;
use crate::ids::{ChannelId, GuildId, Handle, ShardId, Ticket, UserId};
use crate::voice::types::{Direction, DisconnectReason, Speaking, VoiceStatus};

const DISPATCH_BYTES_LIMIT: usize = 64 * 1024 * 1024;
const MIN_CAPACITY: usize = 16;

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
    IdentifyRequest {
        shard: ShardId,
        request: u64,
    },
    InvalidRequests {
        count: u32,
    },
    VoiceState {
        guild: GuildId,
        status: VoiceStatus,
        reason: Option<DisconnectReason>,
        code: Option<u16>,
        detail: Box<str>,
    },
    VoiceMoved {
        guild: GuildId,
        from: ChannelId,
        to: ChannelId,
        by_us: bool,
    },
    VoiceDave {
        guild: GuildId,
        protocol_version: u16,
        epoch: Option<u64>,
        transition_id: u16,
    },
    VoiceSelf {
        guild: GuildId,
        self_mute: bool,
        self_deaf: bool,
        mute: bool,
        suppress: bool,
    },
    VoiceSpeaking {
        guild: GuildId,
        user: UserId,
        ssrc: u32,
        flags: u32,
    },
    VoiceClients {
        guild: GuildId,
        users: Vec<UserId>,
    },
    VoiceClientLeft {
        guild: GuildId,
        user: UserId,
    },
    VoiceStalled {
        guild: GuildId,
        direction: Direction,
        stalled: bool,
        duration: Duration,
    },
    VoiceWake {
        handle: Handle,
    },
    VoiceMarker {
        handle: Handle,
        marker: u64,
        played: bool,
    },
    VoiceSpeech {
        guild: GuildId,
        user: UserId,
        ssrc: u32,
        speaking: Speaking,
        at: Instant,
    },
    VoiceDeaf {
        guild: GuildId,
        deaf: bool,
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
    dispatch_bytes_limit: usize,
}

#[derive(Default)]
struct State {
    deliveries: Vec<Delivery>,
    dispatch_bytes: usize,
    rung: bool,
}

impl Inbox {
    pub(crate) fn new(bell: Bell) -> Self {
        Self::with_dispatch_limit(bell, DISPATCH_BYTES_LIMIT)
    }

    pub(crate) fn with_dispatch_limit(bell: Bell, dispatch_bytes_limit: usize) -> Self {
        Self {
            state: Mutex::new(State::default()),
            room: Notify::new(),
            bell,
            dispatch_bytes_limit,
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
            let capacity = state.deliveries.len().saturating_mul(2).max(MIN_CAPACITY);
            mem::replace(&mut state.deliveries, Vec::with_capacity(capacity))
        };
        self.room.notify_waiters();
        deliveries
    }

    pub(crate) fn backlog(&self) -> Option<usize> {
        let queued_bytes = self.state.lock().dispatch_bytes;
        (queued_bytes >= self.dispatch_bytes_limit).then_some(queued_bytes)
    }

    pub async fn wait_for_room(&self) {
        loop {
            let mut room = pin!(self.room.notified());
            room.as_mut().enable();
            if self.backlog().is_none() {
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

    use super::*;
    use crate::bell;

    const LIMIT: usize = 64 * 1024;

    fn inbox() -> (Inbox, bell::BellReceiver) {
        let (bell, receiver) = bell::pair().unwrap();
        (Inbox::with_dispatch_limit(bell, LIMIT), receiver)
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
    fn rings_again_after_a_drain_and_never_while_empty() {
        let (inbox, receiver) = inbox();

        assert!(inbox.drain().is_empty());
        assert_eq!(pending_rings(&receiver), 0);

        inbox.push(failure(0));
        assert_eq!(pending_rings(&receiver), 1);
        inbox.drain();

        inbox.push(failure(1));
        assert_eq!(pending_rings(&receiver), 1);
        assert_eq!(inbox.drain().len(), 1);
    }

    #[test]
    fn dispatches_fill_the_budget_until_drained() {
        let (inbox, _receiver) = inbox();

        inbox.push(dispatch(LIMIT - 1));
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
            body: Bytes::from(vec![0; LIMIT * 2]),
        });
        inbox.push(failure(2));

        assert!(has_room(&inbox));
    }

    #[test]
    fn an_oversized_dispatch_passes_when_it_is_alone() {
        let (inbox, _receiver) = inbox();
        let oversized = LIMIT * 4;

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
        inbox.push(dispatch(LIMIT));

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
