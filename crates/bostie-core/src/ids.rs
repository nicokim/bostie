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

macro_rules! ids {
    ($inner:ty => $($name:ident),+) => {$(
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
        pub struct $name($inner);

        impl From<$inner> for $name {
            fn from(value: $inner) -> Self {
                Self(value)
            }
        }

        impl From<$name> for $inner {
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

ids!(u32 => ShardId);
ids!(u64 => GuildId, ChannelId, UserId, Handle);
