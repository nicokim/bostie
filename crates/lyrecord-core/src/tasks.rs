use std::collections::HashMap;
use std::sync::Arc;

use parking_lot::Mutex;
use tokio::task::AbortHandle;

use crate::inbox::{Inbox, Message};
use crate::ticket::Ticket;

pub(crate) struct TaskTable {
    pub(crate) running: HashMap<Ticket, AbortHandle>,
    pub(crate) http: Option<reqwest::Client>,
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

    pub(crate) fn resolve(&self, message: Message) {
        if self.tasks.lock().running.remove(&self.ticket).is_some() {
            self.inbox.push(message);
        }
    }
}

impl Drop for PendingTicket {
    fn drop(&mut self) {
        self.resolve(Message::Failure {
            ticket: self.ticket,
            detail: "lyrecord stopped this request unexpectedly; please report this bug".to_owned(),
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
            http: None,
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
    fn resolving_delivers_exactly_one_message() {
        let ticket = Ticket::from(1);
        let (pending, inbox, tasks) = pending(ticket);

        pending.resolve(Message::Failure {
            ticket,
            detail: "done".to_owned(),
        });
        drop(pending);

        let messages = inbox.drain();
        assert!(
            matches!(messages.as_slice(), [Message::Failure { detail, .. }] if detail == "done")
        );
        assert!(tasks.lock().running.is_empty());
    }

    #[test]
    fn dropping_an_unresolved_ticket_reports_a_failure() {
        let ticket = Ticket::from(2);
        let (pending, inbox, tasks) = pending(ticket);

        drop(pending);

        let messages = inbox.drain();
        assert!(
            matches!(messages.as_slice(), [Message::Failure { ticket: got, .. }] if *got == ticket)
        );
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
