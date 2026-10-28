# Transport encryption vectors

`sealed_packets_match_the_reference_vectors` in `crypto.rs` checks `Cipher::seal` against packets built by an independent implementation: Python's `cryptography` for `aead_aes256_gcm_rtpsize` and PyNaCl (libsodium) for `aead_xchacha20_poly1305_rtpsize`.

Each packet is the 12-byte RTP header (sequence `0x1234`, timestamp `0x56789abc`, SSRC `0xdeadbeef`), authenticated but not encrypted, then the encrypted payload, the 16-byte tag and the 4-byte nonce counter in big endian. The cipher nonce is that counter followed by zeros: 12 bytes for AES-GCM, 24 for XChaCha20. The key is the bytes `0x00` to `0x1f`.

To regenerate the vectors, save the script below as `vectors.py` and run:

```sh
uv run --no-project --with cryptography --with pynacl python vectors.py
```

```python
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
```

Output:

```
aead_aes256_gcm_rtpsize 0x00000000 8078123456789abcdeadbeeff6434b00ffbbf32669c5d5684992a3d839a46100000000
aead_aes256_gcm_rtpsize 0xffffffff 8078123456789abcdeadbeeffbe0d9bb51e38ed1d85a707d476a77a9f87e2927ec3672e9166fd386ffffffff
aead_xchacha20_poly1305_rtpsize 0x00000000 8078123456789abcdeadbeef6df97263b63481d0370a12d90cacdfef481a2100000000
aead_xchacha20_poly1305_rtpsize 0xffffffff 8078123456789abcdeadbeeff258f75f9b48283a48c1e837f197050bfc4db2cf5c9ed8b2baf3165cffffffff
```
