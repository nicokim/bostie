use aws_lc_rs::signature::{ED25519, UnparsedPublicKey};

pub fn verify_signature(public_key: &str, signature: &str, timestamp: &str, body: &[u8]) -> bool {
    let (Some(public_key), Some(signature)) = (hex(public_key), hex(signature)) else {
        return false;
    };
    let mut message = Vec::with_capacity(timestamp.len() + body.len());
    message.extend_from_slice(timestamp.as_bytes());
    message.extend_from_slice(body);
    UnparsedPublicKey::new(&ED25519, public_key)
        .verify(&message, &signature)
        .is_ok()
}

fn hex(text: &str) -> Option<Vec<u8>> {
    let (pairs, []) = text.as_bytes().as_chunks::<2>() else {
        return None;
    };
    pairs
        .iter()
        .map(|&[high, low]| {
            let high = char::from(high).to_digit(16)?;
            let low = char::from(low).to_digit(16)?;
            u8::try_from(high << 4 | low).ok()
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use std::fmt::Write;

    use aws_lc_rs::signature::{Ed25519KeyPair, KeyPair};

    use super::*;

    const RFC_8032_PUBLIC_KEY: &str =
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a";
    const RFC_8032_EMPTY_SIGNATURE: &str = "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b";

    fn to_hex(bytes: &[u8]) -> String {
        bytes.iter().fold(String::new(), |mut text, byte| {
            write!(text, "{byte:02x}").unwrap();
            text
        })
    }

    #[test]
    fn accepts_the_rfc_8032_vector_for_an_empty_message() {
        assert!(verify_signature(
            RFC_8032_PUBLIC_KEY,
            RFC_8032_EMPTY_SIGNATURE,
            "",
            b""
        ));
        assert!(verify_signature(
            &RFC_8032_PUBLIC_KEY.to_uppercase(),
            RFC_8032_EMPTY_SIGNATURE,
            "",
            b""
        ));
    }

    #[test]
    fn signs_the_timestamp_followed_by_the_body() {
        let pair = Ed25519KeyPair::from_seed_unchecked(&[7; 32]).unwrap();
        let public_key = to_hex(pair.public_key().as_ref());
        let signature = to_hex(pair.sign(b"1700000000{\"type\":1}").as_ref());

        assert!(verify_signature(
            &public_key,
            &signature,
            "1700000000",
            b"{\"type\":1}"
        ));
        for (timestamp, body) in [
            ("1700000001", &b"{\"type\":1}"[..]),
            ("1700000000", b"{\"type\":2}"),
            ("", b"1700000000{\"type\":1}x"),
        ] {
            assert!(!verify_signature(&public_key, &signature, timestamp, body));
        }
    }

    #[test]
    fn malformed_keys_and_signatures_fail_without_panicking() {
        for (public_key, signature) in [
            ("", RFC_8032_EMPTY_SIGNATURE),
            ("d75a", RFC_8032_EMPTY_SIGNATURE),
            (
                "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
                RFC_8032_EMPTY_SIGNATURE,
            ),
            (RFC_8032_PUBLIC_KEY, "e556"),
            (RFC_8032_PUBLIC_KEY, "e55"),
            (RFC_8032_PUBLIC_KEY, "é5"),
        ] {
            assert!(
                !verify_signature(public_key, signature, "", b""),
                "{public_key} {signature}"
            );
        }
    }
}
