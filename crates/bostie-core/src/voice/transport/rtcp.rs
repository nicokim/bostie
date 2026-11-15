use bytes::Bytes;

use crate::ids::UserId;

pub(crate) const HEADER_LEN: usize = 8;
const VERSION: u8 = 2;
const WORD: usize = 4;
const COUNT_BITS: u8 = 0x1F;
const SENDER_REPORT: u8 = 200;
const RECEIVER_REPORT: u8 = 201;
const SENDER_INFO_LEN: usize = 20;
const BLOCK_LEN: usize = 24;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RtcpPacket {
    pub kind: u8,
    pub count: u8,
    pub ssrc: u32,
    pub user: Option<UserId>,
    pub reports: Vec<ReceptionReport>,
    pub body: Bytes,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ReceptionReport {
    pub ssrc: u32,
    pub user: Option<UserId>,
    pub fraction_lost: u8,
    pub lost: i32,
    pub highest_sequence: u32,
    pub jitter: u32,
    pub last_sender_report: u32,
    pub delay: u32,
}

pub(crate) fn parse(compound: &[u8]) -> Vec<RtcpPacket> {
    let mut packets = Vec::new();
    let mut rest = compound;
    while let Some(&[first, kind, l0, l1, s0, s1, s2, s3]) = rest.first_chunk::<HEADER_LEN>() {
        let words = usize::from(u16::from_be_bytes([l0, l1])) + 1;
        let Some((packet, tail)) = rest.split_at_checked(words * WORD) else {
            break;
        };
        if first >> 6 != VERSION {
            break;
        }
        let count = first & COUNT_BITS;
        let body = packet.get(HEADER_LEN..).unwrap_or_default();
        let blocks = match kind {
            SENDER_REPORT => body.get(SENDER_INFO_LEN..).unwrap_or_default(),
            RECEIVER_REPORT => body,
            _ => &[],
        };
        let (blocks, _) = blocks.as_chunks::<BLOCK_LEN>();
        packets.push(RtcpPacket {
            kind,
            count,
            ssrc: u32::from_be_bytes([s0, s1, s2, s3]),
            user: None,
            reports: blocks
                .iter()
                .take(usize::from(count))
                .map(ReceptionReport::read)
                .collect(),
            body: Bytes::copy_from_slice(body),
        });
        rest = tail;
    }
    packets
}

impl ReceptionReport {
    fn read(block: &[u8; BLOCK_LEN]) -> Self {
        let (words, _) = block.as_chunks::<WORD>();
        let mut words = words.iter().map(|word| u32::from_be_bytes(*word));
        let mut next = || words.next().unwrap_or_default();
        let ssrc = next();
        let [fraction_lost, l0, l1, l2] = next().to_be_bytes();
        Self {
            ssrc,
            user: None,
            fraction_lost,
            lost: i32::from_be_bytes([l0, l1, l2, 0]) >> 8,
            highest_sequence: next(),
            jitter: next(),
            last_sender_report: next(),
            delay: next(),
        }
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    pub(crate) fn receiver_report(ssrc: u32, blocks: &[[u32; 6]]) -> Vec<u8> {
        let words = 1 + blocks.len() * 6;
        let mut packet = vec![0x80 | u8::try_from(blocks.len()).unwrap(), RECEIVER_REPORT];
        packet.extend_from_slice(&u16::try_from(words).unwrap().to_be_bytes());
        packet.extend_from_slice(&ssrc.to_be_bytes());
        for block in blocks {
            for word in block {
                packet.extend_from_slice(&word.to_be_bytes());
            }
        }
        packet
    }

    #[test]
    fn a_compound_packet_parses_its_report_blocks_and_keeps_other_bodies() {
        let mut sender = vec![0x81, SENDER_REPORT, 0, 12, 0, 0, 0, 7];
        sender.extend_from_slice(&[0xEE; SENDER_INFO_LEN]);
        for word in [9_u32, 0x40FF_FFFE, 70_000, 480, 0x1234_5678, 32_768] {
            sender.extend_from_slice(&word.to_be_bytes());
        }
        let receiver = receiver_report(8, &[[11, 0x0000_0003, 5, 96, 0, 0]]);
        let description = [0x81, 202, 0, 1, 0, 0, 0, 9];
        let truncated = [0x80, RECEIVER_REPORT, 0, 9, 0, 0, 0, 1];
        let compound = [&sender[..], &receiver, &description, &truncated].concat();

        let packets = parse(&compound);

        let report =
            |ssrc, fraction_lost, lost, highest_sequence, jitter, lsr, delay| ReceptionReport {
                ssrc,
                user: None,
                fraction_lost,
                lost,
                highest_sequence,
                jitter,
                last_sender_report: lsr,
                delay,
            };
        let summary: Vec<(u8, u8, u32, Vec<ReceptionReport>, usize)> = packets
            .iter()
            .map(|packet| {
                let reports = packet.reports.clone();
                (
                    packet.kind,
                    packet.count,
                    packet.ssrc,
                    reports,
                    packet.body.len(),
                )
            })
            .collect();
        assert_eq!(
            summary,
            [
                (
                    SENDER_REPORT,
                    1,
                    7,
                    vec![report(9, 0x40, -2, 70_000, 480, 0x1234_5678, 32_768)],
                    44
                ),
                (
                    RECEIVER_REPORT,
                    1,
                    8,
                    vec![report(11, 0, 3, 5, 96, 0, 0)],
                    24
                ),
                (202, 1, 9, Vec::new(), 0),
            ]
        );
    }

    #[test]
    fn report_counts_cap_the_blocks_read_and_another_version_ends_parsing() {
        let block = [11, 0, 5, 96, 0, 0];
        let mut overcounted = receiver_report(7, &[block]);
        overcounted[0] = 0x80 | 3;
        let mut undercounted = receiver_report(8, &[block]);
        undercounted[0] = 0x80;
        let mut version_one = receiver_report(9, &[]);
        version_one[0] = 0x40;
        let after = receiver_report(10, &[]);

        let packets = parse(&[overcounted, undercounted, version_one, after].concat());

        let summary: Vec<(u32, u8, usize)> = packets
            .iter()
            .map(|packet| (packet.ssrc, packet.count, packet.reports.len()))
            .collect();
        assert_eq!(summary, [(7, 3, 1), (8, 0, 0)]);
    }
}
