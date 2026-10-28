use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Ticket(u64);

impl Ticket {
    pub(crate) const INTERNAL: Self = Self(0);
}

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

macro_rules! u64_ids {
    ($($name:ident),+) => {$(
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
        pub struct $name(u64);

        impl From<u64> for $name {
            fn from(value: u64) -> Self {
                Self(value)
            }
        }

        impl From<$name> for u64 {
            fn from(id: $name) -> Self {
                id.0
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                self.0.fmt(formatter)
            }
        }
    )+};
}

u64_ids!(GuildId, ChannelId, UserId, Handle);
