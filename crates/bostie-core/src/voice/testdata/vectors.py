# /// script
# requires-python = ">=3.12"
# dependencies = ["cryptography", "pynacl"]
# ///
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_encrypt

KEY = bytes(range(32))
HEADER = bytes.fromhex("80781234 56789abc deadbeef")
SILENCE = bytes.fromhex("f8fffe")
OPUS = b"bostie voice"


def aead_aes256_gcm_rtpsize(counter: int, payload: bytes) -> bytes:
    nonce = counter.to_bytes(4, "big")
    sealed = AESGCM(KEY).encrypt(nonce + bytes(8), payload, HEADER)
    return HEADER + sealed + nonce


def aead_xchacha20_poly1305_rtpsize(counter: int, payload: bytes) -> bytes:
    nonce = counter.to_bytes(4, "big")
    sealed = crypto_aead_xchacha20poly1305_ietf_encrypt(
        payload, HEADER, nonce + bytes(20), KEY
    )
    return HEADER + sealed + nonce


for seal in (aead_aes256_gcm_rtpsize, aead_xchacha20_poly1305_rtpsize):
    for counter, payload in ((0, SILENCE), (0xFFFFFFFF, OPUS)):
        print(seal.__name__, f"{counter:#010x}", seal(counter, payload).hex())
