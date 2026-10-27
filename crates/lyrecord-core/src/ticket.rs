#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Ticket(u64);

impl From<u64> for Ticket {
    fn from(value: u64) -> Self {
        Self(value)
    }
}

impl From<Ticket> for u64 {
    fn from(ticket: Ticket) -> Self {
        ticket.0
    }
}
