use std::fmt;

use aes_gcm::Aes256Gcm;
use aes_gcm::aead::array::Array;
use aes_gcm::aead::{AeadInOut, KeyInit};
use chacha20poly1305::XChaCha20Poly1305;

use super::rtp::{Header, RtpError};

pub const KEY_LEN: usize = 32;
pub const TAG_LEN: usize = 16;
pub const NONCE_LEN: usize = 4;
pub const OVERHEAD: usize = TAG_LEN + NONCE_LEN;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Aes256GcmRtpSize,
    XChaCha20Poly1305RtpSize,
}

impl Mode {
    pub fn choose<S: AsRef<str>>(offered: &[S]) -> Option<Self> {
        [Self::Aes256GcmRtpSize, Self::XChaCha20Poly1305RtpSize]
            .into_iter()
            .find(|mode| offered.iter().any(|name| name.as_ref() == mode.as_str()))
    }

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Aes256GcmRtpSize => "aead_aes256_gcm_rtpsize",
            Self::XChaCha20Poly1305RtpSize => "aead_xchacha20_poly1305_rtpsize",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum CryptoError {
    #[error("malformed RTP packet: {0}")]
    Rtp(#[from] RtpError),
    #[error("a {0}-byte packet is too short for its header, tag and nonce")]
    TooShort(usize),
    #[error("the packet failed authentication")]
    Authentication,
    #[error("the payload is too long to encrypt")]
    Seal,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Opened<'a> {
    pub header: Header,
    pub nonce: u32,
    pub payload: &'a [u8],
    pub extension: &'a [u8],
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReplayWindow {
    window: u32,
    capacity: u64,
    highest: Option<u32>,
    seen: Box<[u64]>,
}

impl ReplayWindow {
    pub fn new(bits: u32) -> Self {
        let words = bits.div_ceil(u64::BITS).next_power_of_two();
        Self {
            window: bits,
            capacity: u64::from(words) * u64::from(u64::BITS),
            highest: None,
            seen: (0..words).map(|_| 0).collect(),
        }
    }

    pub fn accept(&mut self, nonce: u32) -> bool {
        let Some(highest) = self.highest else {
            self.highest = Some(nonce);
            return self.mark(nonce);
        };
        let ahead = nonce.wrapping_sub(highest).cast_signed();
        if ahead > 0 {
            self.forget_after(highest, ahead.unsigned_abs());
            self.highest = Some(nonce);
            self.mark(nonce)
        } else {
            ahead.unsigned_abs() < self.window && self.mark(nonce)
        }
    }

    fn forget_after(&mut self, highest: u32, count: u32) {
        if u64::from(count) >= self.capacity {
            self.seen.fill(0);
            return;
        }
        let mut from = highest.wrapping_add(1);
        let mut left = count;
        while left > 0 {
            let offset = from % u64::BITS;
            let span = left.min(u64::BITS - offset);
            if let Some(word) = self.word(from) {
                *word &= !((u64::MAX >> (u64::BITS - span)) << offset);
            }
            from = from.wrapping_add(span);
            left -= span;
        }
    }

    fn mark(&mut self, nonce: u32) -> bool {
        let bit = 1 << (nonce % u64::BITS);
        let Some(word) = self.word(nonce) else {
            return false;
        };
        let fresh = *word & bit == 0;
        *word |= bit;
        fresh
    }

    fn word(&mut self, nonce: u32) -> Option<&mut u64> {
        let index = usize::try_from(nonce / u64::BITS)
            .ok()?
            .checked_rem(self.seen.len())?;
        self.seen.get_mut(index)
    }
}

pub struct Cipher(Aead);

enum Aead {
    Aes256Gcm(Box<Aes256Gcm>),
    XChaCha20Poly1305(XChaCha20Poly1305),
}

impl fmt::Debug for Cipher {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Cipher")
            .field("mode", &self.mode())
            .finish_non_exhaustive()
    }
}

impl Cipher {
    pub fn new(mode: Mode, key: &[u8; KEY_LEN]) -> Self {
        let key = Array::from(*key);
        Self(match mode {
            Mode::Aes256GcmRtpSize => Aead::Aes256Gcm(Box::new(Aes256Gcm::new(&key))),
            Mode::XChaCha20Poly1305RtpSize => Aead::XChaCha20Poly1305(XChaCha20Poly1305::new(&key)),
        })
    }

    pub const fn mode(&self) -> Mode {
        match self.0 {
            Aead::Aes256Gcm(_) => Mode::Aes256GcmRtpSize,
            Aead::XChaCha20Poly1305(_) => Mode::XChaCha20Poly1305RtpSize,
        }
    }

    pub fn seal(
        &self,
        nonce: u32,
        header: &[u8],
        payload: &[u8],
        out: &mut Vec<u8>,
    ) -> Result<(), CryptoError> {
        out.clear();
        out.extend_from_slice(header);
        out.extend_from_slice(payload);
        let (aad, body) = out
            .split_at_mut_checked(header.len())
            .ok_or(CryptoError::Seal)?;
        let tag = match &self.0 {
            Aead::Aes256Gcm(aead) => {
                aead.encrypt_inout_detached(&full_nonce(nonce).into(), aad, body.into())
            }
            Aead::XChaCha20Poly1305(aead) => {
                aead.encrypt_inout_detached(&full_nonce(nonce).into(), aad, body.into())
            }
        }
        .map_err(|_| CryptoError::Seal)?;
        out.extend_from_slice(&tag);
        out.extend_from_slice(&nonce.to_be_bytes());
        Ok(())
    }

    pub fn open<'a>(&self, packet: &'a mut [u8]) -> Result<Opened<'a>, CryptoError> {
        let header = Header::parse(packet)?;
        let too_short = CryptoError::TooShort(packet.len());
        let (sealed, nonce) = packet
            .split_last_chunk_mut::<NONCE_LEN>()
            .ok_or(too_short)?;
        let nonce = u32::from_be_bytes(*nonce);
        let (sealed, tag) = sealed.split_last_chunk_mut::<TAG_LEN>().ok_or(too_short)?;
        let tag = Array::from(*tag);
        let (aad, body) = sealed
            .split_at_mut_checked(header.size())
            .ok_or(too_short)?;
        match &self.0 {
            Aead::Aes256Gcm(aead) => aead.decrypt_inout_detached(
                &full_nonce(nonce).into(),
                aad,
                (&mut *body).into(),
                &tag,
            ),
            Aead::XChaCha20Poly1305(aead) => aead.decrypt_inout_detached(
                &full_nonce(nonce).into(),
                aad,
                (&mut *body).into(),
                &tag,
            ),
        }
        .map_err(|_| CryptoError::Authentication)?;
        let body: &'a [u8] = body;
        Ok(Opened {
            header,
            nonce,
            payload: header.payload(body)?,
            extension: header.extension_body(body),
        })
    }
}

fn full_nonce<const N: usize>(counter: u32) -> [u8; N] {
    let mut nonce = [0; N];
    if let Some(head) = nonce.first_chunk_mut::<NONCE_LEN>() {
        *head = counter.to_be_bytes();
    }
    nonce
}

#[cfg(test)]
mod tests {
    use proptest::prelude::*;

    use super::*;
    use crate::voice::rtp::tests::header_bytes;
    use crate::voice::rtp::{Extension, HEADER_LEN, write_header};

    const KEY: [u8; KEY_LEN] = {
        let mut key = [0; KEY_LEN];
        let mut index = 0;
        while index < KEY_LEN {
            key[index] = index as u8;
            index += 1;
        }
        key
    };
    const MODES: [Mode; 2] = [Mode::Aes256GcmRtpSize, Mode::XChaCha20Poly1305RtpSize];
    const OPUS: &[u8] = b"bostie voice";

    fn hex(text: &str) -> Vec<u8> {
        let digits: Vec<u8> = text.bytes().filter(u8::is_ascii_hexdigit).collect();
        digits
            .chunks(2)
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect()
    }

    fn header() -> [u8; HEADER_LEN] {
        let mut header = [0; HEADER_LEN];
        write_header(&mut header, 0x1234, 0x5678_9ABC, 0xDEAD_BEEF);
        header
    }

    fn sealed(mode: Mode, nonce: u32, header: &[u8], payload: &[u8]) -> Vec<u8> {
        let mut out = Vec::new();
        Cipher::new(mode, &KEY)
            .seal(nonce, header, payload, &mut out)
            .unwrap();
        out
    }

    #[test]
    fn aes_gcm_is_chosen_whenever_it_is_offered() {
        let offered = [
            "xsalsa20_poly1305_lite_rtpsize",
            "aead_xchacha20_poly1305_rtpsize",
            "aead_aes256_gcm_rtpsize",
        ];

        assert_eq!(Mode::choose(&offered), Some(Mode::Aes256GcmRtpSize));
    }

    #[test]
    fn xchacha_is_chosen_when_aes_gcm_is_not_offered() {
        let offered = vec![
            Box::<str>::from("aead_xchacha20_poly1305_rtpsize"),
            Box::from("xsalsa20_poly1305"),
        ];

        assert_eq!(Mode::choose(&offered), Some(Mode::XChaCha20Poly1305RtpSize));
    }

    #[test]
    fn no_mode_is_chosen_from_deprecated_or_unknown_modes() {
        let offered = ["aead_aes256_gcm", "xsalsa20_poly1305_lite", "future_mode"];

        assert_eq!(Mode::choose(&offered), None);
        assert_eq!(Mode::choose::<&str>(&[]), None);
    }

    #[test]
    fn modes_use_their_wire_names() {
        assert_eq!(
            MODES.map(Mode::as_str),
            ["aead_aes256_gcm_rtpsize", "aead_xchacha20_poly1305_rtpsize"]
        );
        assert_eq!(MODES.map(|mode| Cipher::new(mode, &KEY).mode()), MODES);
    }

    #[test]
    fn debug_output_never_shows_the_key() {
        let debug = format!("{:?}", Cipher::new(Mode::Aes256GcmRtpSize, &KEY));

        assert_eq!(debug, "Cipher { mode: Aes256GcmRtpSize, .. }");
    }

    #[test]
    fn sealed_packets_match_the_reference_vectors() {
        let silence = [0xF8, 0xFF, 0xFE];
        let vectors = [
            (
                Mode::Aes256GcmRtpSize,
                0,
                &silence[..],
                "80781234 56789abc deadbeef f6434b 00ffbbf32669c5d5684992a3d839a461 00000000",
            ),
            (
                Mode::Aes256GcmRtpSize,
                u32::MAX,
                OPUS,
                "80781234 56789abc deadbeef fbe0d9bb51e38ed1d85a707d \
                 476a77a9f87e2927ec3672e9166fd386 ffffffff",
            ),
            (
                Mode::XChaCha20Poly1305RtpSize,
                0,
                &silence[..],
                "80781234 56789abc deadbeef 6df972 63b63481d0370a12d90cacdfef481a21 00000000",
            ),
            (
                Mode::XChaCha20Poly1305RtpSize,
                u32::MAX,
                OPUS,
                "80781234 56789abc deadbeef f258f75f9b48283a48c1e837 \
                 f197050bfc4db2cf5c9ed8b2baf3165c ffffffff",
            ),
        ];

        for (mode, nonce, payload, expected) in vectors {
            assert_eq!(
                sealed(mode, nonce, &header(), payload),
                hex(expected),
                "{mode:?} nonce {nonce:#x}"
            );
        }
    }

    #[test]
    fn sealed_packets_open_to_their_header_nonce_and_payload() {
        for mode in MODES {
            let cipher = Cipher::new(mode, &KEY);
            let mut packet = Vec::new();
            cipher.seal(7, &header(), OPUS, &mut packet).unwrap();

            let opened = cipher.open(&mut packet).unwrap();

            assert_eq!(opened.header, Header::parse(&header()).unwrap());
            assert_eq!(opened.nonce, 7);
            assert_eq!(opened.payload, OPUS);
            assert_eq!(opened.extension, b"");
        }
    }

    #[test]
    fn the_nonce_counter_is_appended_big_endian_after_the_tag() {
        for mode in MODES {
            let packet = sealed(mode, 0x0102_0304, &header(), OPUS);

            assert_eq!(packet.len(), HEADER_LEN + OPUS.len() + OVERHEAD);
            assert_eq!(&packet[..HEADER_LEN], header());
            assert_eq!(packet[packet.len() - NONCE_LEN..], [1, 2, 3, 4]);
            assert_ne!(&packet[HEADER_LEN..HEADER_LEN + OPUS.len()], OPUS);
        }
    }

    #[test]
    fn the_largest_nonce_round_trips() {
        for mode in MODES {
            let cipher = Cipher::new(mode, &KEY);
            let mut packet = sealed(mode, u32::MAX, &header(), OPUS);

            assert_eq!(packet[packet.len() - NONCE_LEN..], [0xFF; 4]);
            assert_eq!(cipher.open(&mut packet).unwrap().nonce, u32::MAX);
        }
    }

    #[test]
    fn opening_skips_csrcs_the_encrypted_extension_body_and_padding() {
        let extension = Extension {
            profile: 0xBEDE,
            words: 2,
        };
        let header = header_bytes(true, &[11, 22, 33], Some(extension), 9);
        let elements = [0x51, 0x02, 0x80, 0x00, 0x92, 0x01, 0x00, 0x00];
        let mut body = elements.to_vec();
        body.extend_from_slice(OPUS);
        body.extend_from_slice(&[0, 0, 0, 4]);

        for mode in MODES {
            let cipher = Cipher::new(mode, &KEY);
            let mut packet = Vec::new();
            cipher.seal(3, &header, &body, &mut packet).unwrap();

            let opened = cipher.open(&mut packet).unwrap();

            assert_eq!(opened.header.csrc_count, 3);
            assert_eq!(opened.header.extension, Some(extension));
            assert_eq!(opened.extension, elements);
            assert_eq!(opened.payload, OPUS);
        }
    }

    #[test]
    fn the_replay_window_accepts_reordered_nonces_once_and_refuses_old_ones() {
        let cases: [(u32, &[(u32, bool)]); 3] = [
            (
                64,
                &[
                    (1, true),
                    (66, true),
                    (65, true),
                    (65, false),
                    (66, false),
                    (3, true),
                    (2, false),
                    (70, true),
                    (67, true),
                    (67, false),
                    (7, true),
                    (6, false),
                ],
            ),
            (
                256,
                &[
                    (u32::MAX - 25, true),
                    (40, true),
                    (41, true),
                    (133, true),
                    (296, true),
                    (230, true),
                    (133, false),
                    (41, false),
                ],
            ),
            (
                4096,
                &[
                    (u32::MAX - 1, true),
                    (1, true),
                    (u32::MAX, true),
                    (u32::MAX - 1, false),
                    (0, true),
                    (1, false),
                    (u32::MAX - 4093, true),
                    (u32::MAX - 4094, false),
                ],
            ),
        ];

        for (bits, nonces) in cases {
            let mut window = ReplayWindow::new(bits);
            for &(nonce, accepted) in nonces {
                assert_eq!(window.accept(nonce), accepted, "{bits} bits, nonce {nonce}");
            }
        }
    }

    #[test]
    fn a_tampered_tag_header_ciphertext_or_nonce_fails_authentication() {
        for mode in MODES {
            let cipher = Cipher::new(mode, &KEY);
            let packet = sealed(mode, 5, &header(), OPUS);
            let tag = packet.len() - OVERHEAD;
            let nonce = packet.len() - 1;

            for (index, what) in [
                (3, "header"),
                (HEADER_LEN, "ciphertext"),
                (tag, "tag"),
                (nonce, "nonce"),
            ] {
                let mut tampered = packet.clone();
                tampered[index] ^= 1;

                assert_eq!(
                    cipher.open(&mut tampered),
                    Err(CryptoError::Authentication),
                    "{mode:?} {what}"
                );
            }
        }
    }

    #[test]
    fn a_packet_sealed_with_another_key_or_mode_fails_authentication() {
        let mut other_key = sealed(Mode::Aes256GcmRtpSize, 5, &header(), OPUS);
        let cipher = Cipher::new(Mode::Aes256GcmRtpSize, &[9; KEY_LEN]);
        let mut other_mode = sealed(Mode::XChaCha20Poly1305RtpSize, 5, &header(), OPUS);

        assert_eq!(
            cipher.open(&mut other_key),
            Err(CryptoError::Authentication)
        );
        assert_eq!(
            Cipher::new(Mode::Aes256GcmRtpSize, &KEY).open(&mut other_mode),
            Err(CryptoError::Authentication)
        );
    }

    #[test]
    fn packets_shorter_than_header_tag_and_nonce_are_rejected() {
        let cipher = Cipher::new(Mode::Aes256GcmRtpSize, &KEY);
        let mut packet = header().to_vec();
        packet.extend_from_slice(&[0; OVERHEAD - 1]);

        assert_eq!(cipher.open(&mut packet), Err(CryptoError::TooShort(31)));
        assert_eq!(
            cipher.open(&mut [0x80, 0x78]),
            Err(CryptoError::Rtp(RtpError::Truncated { len: 2, needed: 12 }))
        );
    }

    #[test]
    fn a_padding_count_past_the_payload_is_rejected_after_decryption() {
        let cipher = Cipher::new(Mode::XChaCha20Poly1305RtpSize, &KEY);
        let header = header_bytes(true, &[], None, 9);
        let mut packet = Vec::new();
        cipher.seal(3, &header, b"op\x09", &mut packet).unwrap();

        assert_eq!(
            cipher.open(&mut packet),
            Err(CryptoError::Rtp(RtpError::Padding { count: 9, len: 3 }))
        );
    }

    proptest! {
        #[test]
        fn random_bytes_never_panic(packet in proptest::collection::vec(any::<u8>(), 0..128)) {
            let mut version_2 = packet.clone();
            if let Some(first) = version_2.first_mut() {
                *first = 0x80 | (*first & 0x3F);
            }
            for mode in MODES {
                let cipher = Cipher::new(mode, &KEY);
                let _ = cipher.open(&mut packet.clone());
                let _ = cipher.open(&mut version_2.clone());
            }
        }
    }
}
