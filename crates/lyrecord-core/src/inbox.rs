use std::mem;

use parking_lot::Mutex;

use crate::bell::Bell;
use crate::http::HttpResponse;
use crate::ticket::Ticket;

#[derive(Debug)]
pub enum Message {
    Response {
        ticket: Ticket,
        response: HttpResponse,
    },
    Failure {
        ticket: Ticket,
        detail: String,
    },
}

pub(crate) struct Inbox {
    state: Mutex<State>,
    bell: Bell,
}

#[derive(Default)]
struct State {
    messages: Vec<Message>,
    rung: bool,
}

impl Inbox {
    pub(crate) fn new(bell: Bell) -> Self {
        Self {
            state: Mutex::new(State::default()),
            bell,
        }
    }

    pub(crate) fn push(&self, message: Message) {
        let first_since_drain = {
            let mut state = self.state.lock();
            state.messages.push(message);
            !mem::replace(&mut state.rung, true)
        };
        if first_since_drain && !self.bell.ring() {
            self.state.lock().rung = false;
        }
    }

    pub(crate) fn drain(&self) -> Vec<Message> {
        let mut state = self.state.lock();
        state.rung = false;
        mem::take(&mut state.messages)
    }
}

#[cfg(test)]
mod tests {
    use std::io::{ErrorKind, Read};

    use super::*;
    use crate::bell;

    fn failure(ticket: u64) -> Message {
        Message::Failure {
            ticket: Ticket::from(ticket),
            detail: String::new(),
        }
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
        let (bell, receiver) = bell::pair().unwrap();
        let inbox = Inbox::new(bell);

        for ticket in 0..3 {
            inbox.push(failure(ticket));
        }

        assert_eq!(pending_rings(&receiver), 1);
        let tickets: Vec<u64> = inbox
            .drain()
            .into_iter()
            .map(|message| match message {
                Message::Failure { ticket, .. } => ticket.into(),
                Message::Response { .. } => unreachable!(),
            })
            .collect();
        assert_eq!(tickets, [0, 1, 2]);
    }

    #[test]
    fn rings_again_after_a_drain() {
        let (bell, receiver) = bell::pair().unwrap();
        let inbox = Inbox::new(bell);

        inbox.push(failure(0));
        assert_eq!(pending_rings(&receiver), 1);
        inbox.drain();

        inbox.push(failure(1));
        assert_eq!(pending_rings(&receiver), 1);
        assert_eq!(inbox.drain().len(), 1);
    }

    #[test]
    fn drain_without_messages_is_empty() {
        let (bell, receiver) = bell::pair().unwrap();
        let inbox = Inbox::new(bell);

        assert!(inbox.drain().is_empty());
        assert_eq!(pending_rings(&receiver), 0);
    }
}
