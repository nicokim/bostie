use std::collections::HashMap;
use std::sync::Arc;

use parking_lot::Mutex;
use tokio::task::AbortHandle;

use crate::ids::Ticket;
use crate::inbox::{Delivery, FailureKind, Inbox};
use crate::rest::Rest;

pub(crate) struct TaskTable {
    pub(crate) running: HashMap<Ticket, AbortHandle>,
    pub(crate) rest: Option<Arc<Rest>>,
}

pub(crate) struct PendingTicket {
    ticket: Ticket,
    inbox: Arc<Inbox>,
    tasks: Arc<Mutex<TaskTable>>,
}

impl PendingTicket {
    pub(crate) fn new(ticket: Ticket, inbox: Arc<Inbox>, tasks: Arc<Mutex<TaskTable>>) -> Self {
        Self {
            ticket,
            inbox,
            tasks,
        }
    }

    pub(crate) fn resolve(&self, delivery: Delivery) {
        if self.tasks.lock().running.remove(&self.ticket).is_some() {
            self.inbox.push(delivery);
        }
    }
}

impl Drop for PendingTicket {
    fn drop(&mut self) {
        self.resolve(Delivery::Failure {
            ticket: self.ticket,
            kind: FailureKind::Bug,
            detail: "bostie stopped this request unexpectedly; please report this bug".to_owned(),
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{bell, runtime};

    fn table_with(ticket: Ticket) -> Arc<Mutex<TaskTable>> {
        let task = runtime::shared()
            .unwrap()
            .spawn(std::future::pending::<()>());
        Arc::new(Mutex::new(TaskTable {
            running: HashMap::from([(ticket, task.abort_handle())]),
            rest: None,
        }))
    }

    fn pending(ticket: Ticket) -> (PendingTicket, Arc<Inbox>, Arc<Mutex<TaskTable>>) {
        let (bell, _receiver) = bell::pair().unwrap();
        let inbox = Arc::new(Inbox::new(bell));
        let tasks = table_with(ticket);
        let pending = PendingTicket::new(ticket, Arc::clone(&inbox), Arc::clone(&tasks));
        (pending, inbox, tasks)
    }

    #[test]
    fn resolving_delivers_exactly_one_delivery() {
        let ticket = Ticket::from(1);
        let (pending, inbox, tasks) = pending(ticket);

        pending.resolve(Delivery::Response {
            ticket,
            status: 204,
            body: bytes::Bytes::new(),
        });
        drop(pending);

        let drained = inbox.drain();
        assert!(matches!(
            drained.as_slice(),
            [Delivery::Response { status: 204, .. }]
        ));
        assert!(tasks.lock().running.is_empty());
    }

    #[test]
    fn dropping_an_unresolved_ticket_reports_a_bug() {
        let ticket = Ticket::from(2);
        let (pending, inbox, tasks) = pending(ticket);

        drop(pending);

        let drained = inbox.drain();
        assert!(matches!(
            drained.as_slice(),
            [Delivery::Failure { ticket: got, kind: FailureKind::Bug, .. }] if *got == ticket
        ));
        assert!(tasks.lock().running.is_empty());
    }

    #[test]
    fn dropping_a_cancelled_ticket_stays_silent() {
        let ticket = Ticket::from(3);
        let (pending, inbox, tasks) = pending(ticket);

        tasks.lock().running.remove(&ticket);
        drop(pending);

        assert!(inbox.drain().is_empty());
    }
}
