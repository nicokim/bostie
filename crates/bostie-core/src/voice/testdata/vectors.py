# /// script
# requires-python = ">=3.12"
# dependencies = ["cryptography", "pynacl"]
# ///
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_encrypt

KEY = bytes(range(32))
HEADER = bytes.fromhex("80781234 56789abc deadbeef")
EXTENDED_HEADER = bytes.fromhex("91781234 56789abc deadbeef 00000001 bede0001")
SILENCE = bytes.fromhex("f8fffe")
EXTENSION_BODY = bytes.fromhex("10aa0000")
OPUS = b"bostie voice"


def aead_aes256_gcm_rtpsize(header: bytes, counter: int, payload: bytes) -> bytes:
    nonce = counter.to_bytes(4, "big")
    sealed = AESGCM(KEY).encrypt(nonce + bytes(8), payload, header)
    return header + sealed + nonce


def aead_xchacha20_poly1305_rtpsize(
    header: bytes, counter: int, payload: bytes
) -> bytes:
    nonce = counter.to_bytes(4, "big")
    sealed = crypto_aead_xchacha20poly1305_ietf_encrypt(
        payload, header, nonce + bytes(20), KEY
    )
    return header + sealed + nonce


for seal in (aead_aes256_gcm_rtpsize, aead_xchacha20_poly1305_rtpsize):
    for header, counter, payload in (
        (HEADER, 0, SILENCE),
        (HEADER, 0xFFFFFFFF, OPUS),
        (EXTENDED_HEADER, 1, EXTENSION_BODY + SILENCE),
    ):
        print(seal.__name__, f"{counter:#010x}", seal(header, counter, payload).hex())
