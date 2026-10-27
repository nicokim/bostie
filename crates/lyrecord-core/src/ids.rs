use std::fmt;

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

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct ShardId(u32);

impl From<u32> for ShardId {
    fn from(value: u32) -> Self {
        Self(value)
    }
}

impl From<ShardId> for u32 {
    fn from(shard: ShardId) -> Self {
        shard.0
    }
}

impl fmt::Display for ShardId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(formatter)
    }
}
