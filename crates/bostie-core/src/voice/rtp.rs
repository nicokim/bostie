use std::ops::RangeInclusive;

pub(crate) const HEADER_LEN: usize = 12;
pub(crate) const PAYLOAD_TYPE: u8 = 120;

const VERSION: u8 = 2;
const FIRST_BYTE: u8 = VERSION << 6;
pub(crate) const PADDING: u8 = 0x20;
pub(crate) const EXTENSION: u8 = 0x10;
pub(crate) const MARKER: u8 = 0x80;
pub(crate) const ONE_BYTE_PROFILE: u16 = 0xBEDE;
const CSRC_COUNT: u8 = 0x0F;
const PAYLOAD_TYPE_BITS: u8 = 0x7F;
const RTCP_PAYLOAD_TYPES: RangeInclusive<u8> = 72..=78;
const RTCP_HEADER_LEN: usize = 8;
const CSRC_LEN: usize = 4;
const EXTENSION_PREAMBLE_LEN: usize = 4;
const EXTENSION_WORD_LEN: usize = 4;
const TWO_BYTE_PROFILE: u16 = 0x1000;
const TWO_BYTE_PROFILE_BITS: u16 = 0xFFF0;
const ONE_BYTE_STOP: u8 = 15;
const ONE_BYTE_LENGTH_BITS: u8 = 0x0F;

#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub(crate) enum RtpError {
    #[error("{len} bytes is shorter than the {needed}-byte RTP header")]
    Truncated { len: usize, needed: usize },
    #[error("RTP version {0}, expected 2")]
    Version(u8),
    #[error("an extension body of {words} words overruns the {len}-byte body")]
    Extension { words: u16, len: usize },
    #[error("a padding count of {count} overruns the {len}-byte payload")]
    Padding { count: u8, len: usize },
    #[error("extension element {id} overruns the {left} bytes left in its list")]
    Element { id: u8, left: usize },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Kind {
    Rtp,
    Rtcp,
    Other,
}

pub(crate) fn classify(packet: &[u8]) -> Kind {
    let Some(&[first, second]) = packet.first_chunk::<2>() else {
        return Kind::Other;
    };
    if first >> 6 != VERSION {
        return Kind::Other;
    }
    let payload_type = second & PAYLOAD_TYPE_BITS;
    if payload_type == PAYLOAD_TYPE && packet.len() >= HEADER_LEN {
        Kind::Rtp
    } else if RTCP_PAYLOAD_TYPES.contains(&payload_type) && packet.len() >= RTCP_HEADER_LEN {
        Kind::Rtcp
    } else {
        Kind::Other
    }
}

pub(crate) fn extensions(profile: u16, body: &[u8]) -> Result<Vec<(u8, &[u8])>, RtpError> {
    let one_byte = profile == ONE_BYTE_PROFILE;
    if !one_byte && profile & TWO_BYTE_PROFILE_BITS != TWO_BYTE_PROFILE {
        return Ok(Vec::new());
    }
    let mut elements = Vec::new();
    let mut rest = body;
    while let Some((&first, tail)) = rest.split_first() {
        let id = if one_byte { first >> 4 } else { first };
        if id == 0 {
            rest = tail;
            continue;
        }
        if one_byte && id == ONE_BYTE_STOP {
            break;
        }
        let (len, tail) = if one_byte {
            (usize::from(first & ONE_BYTE_LENGTH_BITS) + 1, tail)
        } else {
            let (&len, tail) = tail
                .split_first()
                .ok_or(RtpError::Element { id, left: 0 })?;
            (usize::from(len), tail)
        };
        let (data, tail) = tail.split_at_checked(len).ok_or(RtpError::Element {
            id,
            left: tail.len(),
        })?;
        elements.push((id, data));
        rest = tail;
    }
    Ok(elements)
}

pub(crate) fn write_header(out: &mut [u8; HEADER_LEN], sequence: u16, timestamp: u32, ssrc: u32) {
    let [s0, s1] = sequence.to_be_bytes();
    let [t0, t1, t2, t3] = timestamp.to_be_bytes();
    let [r0, r1, r2, r3] = ssrc.to_be_bytes();
    *out = [
        FIRST_BYTE,
        PAYLOAD_TYPE,
        s0,
        s1,
        t0,
        t1,
        t2,
        t3,
        r0,
        r1,
        r2,
        r3,
    ];
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct Extension {
    pub profile: u16,
    pub words: u16,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct Header {
    pub padding: bool,
    pub marker: bool,
    pub payload_type: u8,
    pub sequence: u16,
    pub timestamp: u32,
    pub ssrc: u32,
    pub csrc_count: u8,
    pub extension: Option<Extension>,
}

impl Header {
    pub(crate) fn parse(packet: &[u8]) -> Result<Self, RtpError> {
        let truncated = |needed| RtpError::Truncated {
            len: packet.len(),
            needed,
        };
        let &[first, second, s0, s1, t0, t1, t2, t3, r0, r1, r2, r3] = packet
            .first_chunk::<HEADER_LEN>()
            .ok_or(truncated(HEADER_LEN))?;
        if first >> 6 != VERSION {
            return Err(RtpError::Version(first >> 6));
        }
        let csrc_count = first & CSRC_COUNT;
        let csrc_end = HEADER_LEN + CSRC_LEN * usize::from(csrc_count);
        if packet.len() < csrc_end {
            return Err(truncated(csrc_end));
        }
        let extension = if first & EXTENSION == 0 {
            None
        } else {
            let needed = csrc_end + EXTENSION_PREAMBLE_LEN;
            let &[p0, p1, w0, w1] = packet
                .get(csrc_end..)
                .and_then(<[u8]>::first_chunk::<EXTENSION_PREAMBLE_LEN>)
                .ok_or(truncated(needed))?;
            Some(Extension {
                profile: u16::from_be_bytes([p0, p1]),
                words: u16::from_be_bytes([w0, w1]),
            })
        };
        Ok(Self {
            padding: first & PADDING != 0,
            marker: second & MARKER != 0,
            payload_type: second & PAYLOAD_TYPE_BITS,
            sequence: u16::from_be_bytes([s0, s1]),
            timestamp: u32::from_be_bytes([t0, t1, t2, t3]),
            ssrc: u32::from_be_bytes([r0, r1, r2, r3]),
            csrc_count,
            extension,
        })
    }

    pub(crate) fn size(&self) -> usize {
        let preamble = if self.extension.is_some() {
            EXTENSION_PREAMBLE_LEN
        } else {
            0
        };
        HEADER_LEN + CSRC_LEN * usize::from(self.csrc_count) + preamble
    }

    pub(crate) fn extension_body<'a>(&self, body: &'a [u8]) -> &'a [u8] {
        let words = self.extension.map_or(0, |extension| extension.words);
        body.get(..EXTENSION_WORD_LEN * usize::from(words))
            .unwrap_or_default()
    }

    pub(crate) fn payload<'a>(&self, body: &'a [u8]) -> Result<&'a [u8], RtpError> {
        let rest = match self.extension {
            None => body,
            Some(Extension { words, .. }) => body
                .get(EXTENSION_WORD_LEN * usize::from(words)..)
                .ok_or(RtpError::Extension {
                    words,
                    len: body.len(),
                })?,
        };
        if !self.padding {
            return Ok(rest);
        }
        let count = rest.last().copied().unwrap_or(0);
        let overrun = RtpError::Padding {
            count,
            len: rest.len(),
        };
        if count == 0 {
            return Err(overrun);
        }
        rest.len()
            .checked_sub(usize::from(count))
            .and_then(|end| rest.get(..end))
            .ok_or(overrun)
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use proptest::collection::vec;
    use proptest::option;
    use proptest::prelude::*;

    use super::*;

    pub(crate) fn header_bytes(
        padding: bool,
        csrcs: &[u32],
        extension: Option<Extension>,
        sequence: u16,
    ) -> Vec<u8> {
        let mut fixed = [0; HEADER_LEN];
        write_header(&mut fixed, sequence, 0x5678_9ABC, 0xDEAD_BEEF);
        fixed[0] |= u8::try_from(csrcs.len()).unwrap();
        if padding {
            fixed[0] |= PADDING;
        }
        if extension.is_some() {
            fixed[0] |= EXTENSION;
        }
        let mut header = fixed.to_vec();
        for csrc in csrcs {
            header.extend_from_slice(&csrc.to_be_bytes());
        }
        if let Some(Extension { profile, words }) = extension {
            header.extend_from_slice(&profile.to_be_bytes());
            header.extend_from_slice(&words.to_be_bytes());
        }
        header
    }

    pub(crate) fn any_extension() -> impl Strategy<Value = (Option<Extension>, Vec<u8>)> {
        option::of((any::<u16>(), vec(any::<[u8; 4]>(), 0..=3))).prop_map(|extension| {
            extension.map_or((None, Vec::new()), |(profile, words)| {
                let extension = Extension {
                    profile,
                    words: u16::try_from(words.len()).unwrap(),
                };
                (Some(extension), words.concat())
            })
        })
    }

    pub(crate) fn padded(payload: &[u8], padding: u8) -> Vec<u8> {
        let mut body = payload.to_vec();
        if padding > 0 {
            body.extend(std::iter::repeat_n(0, usize::from(padding) - 1));
            body.push(padding);
        }
        body
    }

    #[test]
    fn written_header_is_80_78_then_sequence_timestamp_and_ssrc_big_endian() {
        let mut header = [0; HEADER_LEN];

        write_header(&mut header, 0x1234, 0x5678_9ABC, 0xDEAD_BEEF);

        assert_eq!(
            header,
            [
                0x80, 0x78, 0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xAD, 0xBE, 0xEF
            ]
        );
    }

    #[test]
    fn written_header_parses_back_with_no_csrc_extension_padding_or_marker() {
        let mut header = [0; HEADER_LEN];
        write_header(&mut header, u16::MAX, u32::MAX, 0x8000_0001);

        let parsed = Header::parse(&header).unwrap();

        assert_eq!(
            parsed,
            Header {
                padding: false,
                marker: false,
                payload_type: PAYLOAD_TYPE,
                sequence: u16::MAX,
                timestamp: u32::MAX,
                ssrc: 0x8000_0001,
                csrc_count: 0,
                extension: None,
            }
        );
        assert_eq!(parsed.size(), HEADER_LEN);
        assert_eq!(parsed.payload(b"opus").unwrap(), b"opus");
    }

    #[test]
    fn padding_of_zero_or_past_the_payload_is_an_error() {
        let header = Header::parse(&header_bytes(true, &[], None, 7)).unwrap();

        assert_eq!(
            header.payload(b"opus\x00"),
            Err(RtpError::Padding { count: 0, len: 5 })
        );
        assert_eq!(
            header.payload(b"opus\x06"),
            Err(RtpError::Padding { count: 6, len: 5 })
        );
        assert_eq!(
            header.payload(b""),
            Err(RtpError::Padding { count: 0, len: 0 })
        );
    }

    #[test]
    fn padding_is_counted_after_the_extension_body() {
        let extension = Extension {
            profile: 0xBEDE,
            words: 1,
        };
        let header = Header::parse(&header_bytes(true, &[], Some(extension), 7)).unwrap();

        assert_eq!(
            header.payload(b"\x10\x00\x00\x02opus\x00\x02").unwrap(),
            b"opus"
        );
        assert_eq!(
            header.payload(b"\x10\x00\x00\x09"),
            Err(RtpError::Padding { count: 0, len: 0 })
        );
    }

    #[test]
    fn an_extension_body_past_the_packet_is_an_error() {
        let extension = Extension {
            profile: 0xBEDE,
            words: 2,
        };
        let header = Header::parse(&header_bytes(false, &[], Some(extension), 7)).unwrap();

        assert_eq!(
            header.payload(&[0; 7]),
            Err(RtpError::Extension { words: 2, len: 7 })
        );
        assert_eq!(header.payload(&[0; 8]).unwrap(), b"");
    }

    #[test]
    fn extensions_parse_one_byte_and_two_byte_elements() {
        let one_byte = [0x10, 0xAA, 0x00, 0x92, 1, 2, 3, 0x00, 0xF0, 0x30, 0x55];
        let two_byte = [0x01, 0x00, 0x00, 0x0F, 0x02, 0xAB, 0xCD, 0x00, 0x00];

        assert_eq!(
            extensions(ONE_BYTE_PROFILE, &one_byte).unwrap(),
            [(1, &[0xAA][..]), (9, &[1, 2, 3][..])]
        );
        for profile in [0x1000, 0x100F] {
            assert_eq!(
                extensions(profile, &two_byte).unwrap(),
                [(1, &[][..]), (15, &[0xAB, 0xCD][..])]
            );
        }
        for profile in [0xBEDF, 0x1010, 0] {
            assert_eq!(extensions(profile, &one_byte).unwrap(), []);
        }
    }

    #[test]
    fn truncated_extensions_are_an_error() {
        assert_eq!(
            extensions(ONE_BYTE_PROFILE, &[0x10, 0xAA, 0x23, 1, 2]),
            Err(RtpError::Element { id: 2, left: 2 })
        );
        assert_eq!(
            extensions(0x1000, &[0x07, 0x05, 1, 2]),
            Err(RtpError::Element { id: 7, left: 2 })
        );
        assert_eq!(
            extensions(0x1000, &[0x00, 0x07]),
            Err(RtpError::Element { id: 7, left: 0 })
        );
    }

    #[test]
    fn every_truncated_header_is_an_error() {
        let extension = Extension {
            profile: 0xBEDE,
            words: 1,
        };
        let packet = header_bytes(false, &[1, 2, 3], Some(extension), 7);

        for len in 0..packet.len() {
            assert!(
                matches!(
                    Header::parse(&packet[..len]),
                    Err(RtpError::Truncated { .. })
                ),
                "{len} bytes"
            );
        }
        assert!(Header::parse(&packet).is_ok());
        assert_eq!(
            Header::parse(&packet[..15]),
            Err(RtpError::Truncated {
                len: 15,
                needed: 24
            })
        );
        assert_eq!(
            Header::parse(&packet[..25]),
            Err(RtpError::Truncated {
                len: 25,
                needed: 28
            })
        );
    }

    #[test]
    fn versions_other_than_2_are_rejected() {
        for version in [0u8, 1, 3] {
            let mut packet = header_bytes(false, &[], None, 7);
            packet[0] = (packet[0] & 0x3F) | (version << 6);

            assert_eq!(Header::parse(&packet), Err(RtpError::Version(version)));
        }
    }

    #[test]
    fn marker_and_payload_type_are_read_from_the_second_byte() {
        let mut packet = header_bytes(false, &[], None, 7);
        packet[1] = 0x80 | 96;

        let header = Header::parse(&packet).unwrap();

        assert!(header.marker);
        assert_eq!(header.payload_type, 96);
    }

    #[test]
    fn rtcp_payload_types_classify_as_rtcp() {
        for payload_type in 200..=206u8 {
            let packet = [0x80, payload_type, 0, 6, 0, 0, 0, 1];

            assert_eq!(classify(&packet), Kind::Rtcp, "{payload_type}");
        }
    }

    #[test]
    fn voice_packets_classify_as_rtp_with_or_without_marker() {
        let mut packet = header_bytes(false, &[], None, 7);

        assert_eq!(classify(&packet), Kind::Rtp);
        packet[1] |= MARKER;
        assert_eq!(classify(&packet), Kind::Rtp);
    }

    #[test]
    fn other_packets_classify_as_other() {
        let mut video = header_bytes(false, &[], None, 7);
        video[1] = 101;
        let mut discovery = [0; 74];
        discovery[..4].copy_from_slice(&[0, 2, 0, 70]);

        for packet in [
            &video[..],
            &discovery[..],
            &[0x80, 0x78, 0, 1][..],
            &[0x80, 0xC8, 0, 1][..],
            &[0x80][..],
            &[][..],
        ] {
            assert_eq!(classify(packet), Kind::Other, "{packet:?}");
        }
    }

    proptest! {
        #[test]
        fn built_packets_parse_back_to_their_fields_and_payload(
            csrcs in vec(any::<u32>(), 0..=15),
            (extension, extension_body) in any_extension(),
            padding in any::<u8>(),
            marker in any::<bool>(),
            payload_type in 0_u8..128,
            sequence in any::<u16>(),
            payload in vec(any::<u8>(), 0..32),
        ) {
            let mut packet = header_bytes(padding > 0, &csrcs, extension, sequence);
            packet[1] = u8::from(marker) << 7 | payload_type;
            packet.extend_from_slice(&extension_body);
            packet.extend_from_slice(&padded(&payload, padding));

            let header = Header::parse(&packet).unwrap();
            let body = &packet[header.size()..];

            prop_assert_eq!(header, Header {
                padding: padding > 0,
                marker,
                payload_type,
                sequence,
                timestamp: 0x5678_9ABC,
                ssrc: 0xDEAD_BEEF,
                csrc_count: u8::try_from(csrcs.len()).unwrap(),
                extension,
            });
            prop_assert_eq!(
                header.size(),
                HEADER_LEN + 4 * csrcs.len() + 4 * usize::from(extension.is_some())
            );
            prop_assert_eq!(header.extension_body(body), &extension_body[..]);
            prop_assert_eq!(header.payload(body).unwrap(), &payload[..]);
        }

        #[test]
        fn random_bytes_never_panic(packet in vec(any::<u8>(), 0..64)) {
            let _ = classify(&packet);
            for profile in [ONE_BYTE_PROFILE, TWO_BYTE_PROFILE] {
                let _ = extensions(profile, &packet);
            }
            if let Ok(header) = Header::parse(&packet) {
                let _ = header.payload(packet.get(header.size()..).unwrap_or_default());
            }
        }
    }
}
