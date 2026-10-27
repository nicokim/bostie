use std::error::Error;
use std::io;

use super::envelope::Frame;
use super::inflate;
use crate::bell::{self, BellReceiver};
use crate::ids::ShardId;
use crate::inbox::{Delivery, Inbox};

pub struct Inflater(inflate::Inflater);

impl Inflater {
    pub fn new() -> Option<Self> {
        inflate::Inflater::new().map(Self)
    }

    pub fn inflate(&mut self, message: &[u8]) -> Result<&[u8], Box<dyn Error>> {
        Ok(self.0.inflate(message)?)
    }
}

pub fn parse(
    frame: &[u8],
    wanted: impl FnOnce(&str) -> bool,
) -> Result<Option<Delivery>, Box<dyn Error>> {
    Ok(match Frame::parse(frame, wanted)? {
        Frame::Dispatch {
            seq,
            forward: Some(forward),
        } => Some(Delivery::Dispatch {
            shard: ShardId::from(0),
            seq,
            name: forward.name,
            data: forward.data,
        }),
        _ => None,
    })
}

pub fn inbox() -> io::Result<(Inbox, BellReceiver)> {
    let (bell, receiver) = bell::pair()?;
    Ok((Inbox::new(bell), receiver))
}
