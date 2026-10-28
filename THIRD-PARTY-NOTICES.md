# Third-party notices

The voice support in bostie's native extension (`bostie._native`) statically links the libraries below. Each section names the license bostie uses them under and repeats the notices that license asks to be kept.

## libopus

Built from source by `opusic-sys` and linked statically, under the BSD 3-Clause license.

```text
Copyright 2001-2023 Xiph.Org, Skype Limited, Octasic,
                    Jean-Marc Valin, Timothy B. Terriberry,
                    CSIRO, Gregory Maxwell, Mark Borgerding,
                    Erik de Castro Lopo, Mozilla, Amazon

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

- Redistributions of source code must retain the above copyright
notice, this list of conditions and the following disclaimer.

- Redistributions in binary form must reproduce the above copyright
notice, this list of conditions and the following disclaimer in the
documentation and/or other materials provided with the distribution.

- Neither the name of Internet Society, IETF or IETF Trust, nor the
names of specific contributors, may be used to endorse or promote
products derived from this software without specific prior written
permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER
OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

Opus is subject to the royalty-free patent licenses which are
specified at:

Xiph.Org Foundation:
https://datatracker.ietf.org/ipr/1524/

Microsoft Corporation:
https://datatracker.ietf.org/ipr/1914/

Broadcom Corporation:
https://datatracker.ietf.org/ipr/1526/
```

## Mozilla Public License 2.0

Used unmodified under the [Mozilla Public License 2.0](https://mozilla.org/MPL/2.0/). Their source code is available from crates.io and from <https://github.com/pdeljanov/Symphonia> and <https://github.com/cryspen/hpke-rs>.

- `hpke-rs`: Franziskus Kiefer
- `hpke-rs-crypto`: Franziskus Kiefer
- `hpke-rs-rust-crypto`: Franziskus Kiefer
- `symphonia`: Philip Deljanov
- `symphonia-bundle-flac`: Philip Deljanov
- `symphonia-bundle-mp3`: Philip Deljanov
- `symphonia-codec-pcm`: Philip Deljanov
- `symphonia-codec-vorbis`: Philip Deljanov
- `symphonia-common`: Philip Deljanov
- `symphonia-core`: Philip Deljanov
- `symphonia-format-mkv`: Dariusz Niedoba, Philip Deljanov
- `symphonia-format-ogg`: Philip Deljanov
- `symphonia-format-riff`: Philip Deljanov, dedobbin
- `symphonia-metadata`: Philip Deljanov

## Apache License 2.0

Used under the Apache License 2.0, whose text is in `LICENSE-APACHE` next to this file.

- `hax-lib`: hax Authors
- `libcrux-intrinsics`: Cryspen
- `libcrux-platform`: Cryspen
- `libcrux-secrets`: Cryspen
- `libcrux-sha3`: Cryspen
- `libcrux-traits`: Cryspen

## BSD 3-Clause

### curve25519-dalek

```text
Copyright (c) 2016-2021 isis agora lovecruft. All rights reserved.
Copyright (c) 2016-2021 Henry de Valence. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

1. Redistributions of source code must retain the above copyright
notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright
notice, this list of conditions and the following disclaimer in the
documentation and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
contributors may be used to endorse or promote products derived from
this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS
IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

========================================================================

Portions of curve25519-dalek were originally derived from Adam Langley's
Go ed25519 implementation, found at <https://github.com/agl/ed25519/>,
under the following licence:

========================================================================

Copyright (c) 2012 The Go Authors. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

   * Redistributions of source code must retain the above copyright
notice, this list of conditions and the following disclaimer.
   * Redistributions in binary form must reproduce the above
copyright notice, this list of conditions and the following disclaimer
in the documentation and/or other materials provided with the
distribution.
   * Neither the name of Google Inc. nor the names of its
contributors may be used to endorse or promote products derived from
this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS
IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER
OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### ed25519-dalek

```text
Copyright (c) 2017-2019 isis agora lovecruft. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

1. Redistributions of source code must retain the above copyright
notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright
notice, this list of conditions and the following disclaimer in the
documentation and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
contributors may be used to endorse or promote products derived from
this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS
IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### instant

```text
Copyright (c) 2019, Sébastien Crozet
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the author nor the names of its contributors may be used
   to endorse or promote products derived from this software without specific
   prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### x25519-dalek

```text
Copyright (c) 2017-2021 isis agora lovecruft. All rights reserved.
Copyright (c) 2019-2021 DebugSteven. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

1. Redistributions of source code must retain the above copyright
notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright
notice, this list of conditions and the following disclaimer in the
documentation and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
contributors may be used to endorse or promote products derived from
this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS
IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## MIT

Used under the MIT license; several are also offered under Apache-2.0 or Zlib.

- `aead`: Copyright (c) 2019 The RustCrypto Project Developers; Copyright (c) 2019 MobileCoin, LLC; Copyright (c) 2019-2026 The RustCrypto Project Developers
- `aes`: Copyright (c) 2018 Artyom Pavlov; Copyright (c) 2018-2024 The RustCrypto Project Developers
- `aes-gcm`: Copyright (c) 2019 The RustCrypto Project Developers; Copyright (c) 2019-2026 The RustCrypto Project Developers
- `audioadapter`: Copyright (c) 2026 Henrik Enquist
- `audioadapter-buffers`: Copyright (c) 2026 Henrik Enquist
- `audioadapter-sample`: Copyright (c) 2026 Henrik Enquist
- `base16ct`: Copyright (c) 2014 Steve "Sc00bz" Thomas (steve at tobtu dot com); Copyright (c) 2022 The RustCrypto Project Developers
- `base64ct`: Copyright (c) 2014 Steve "Sc00bz" Thomas (steve at tobtu dot com); Copyright (c) 2021-2025 The RustCrypto Project Developers
- `block-buffer`: Copyright (c) 2018-2019 The RustCrypto Project Developers; Copyright (c) 2018-2025 The RustCrypto Project Developers
- `bytemuck`: Copyright (c) 2019 Daniel "Lokathor" Gee.
- `chacha20`: Copyright (c) 2019-2023 The RustCrypto Project Developers
- `chacha20poly1305`: Copyright (c) 2019 The RustCrypto Project Developers; Copyright (c) 2019-2026 The RustCrypto Project Developers
- `cipher`: Copyright (c) 2016-2020 RustCrypto Developers; Copyright (c) 2016-2025 RustCrypto Developers
- `cmov`: Copyright (c) 2022-2026 The RustCrypto Project Developers
- `const-oid`: Copyright (c) 2020-2022 The RustCrypto Project Developers
- `cpubits`: Copyright (c) 2023-2026 The RustCrypto Project Developers
- `cpufeatures`: Copyright (c) 2020-2025 The RustCrypto Project Developers
- `crossbeam-deque`: Copyright (c) 2019 The Crossbeam Project Developers
- `crossbeam-epoch`: Copyright (c) 2019 The Crossbeam Project Developers
- `crossbeam-utils`: Copyright (c) 2019 The Crossbeam Project Developers
- `crypto-bigint`: Copyright (c) 2021 The RustCrypto Project Developers
- `crypto-common`: Copyright (c) 2021 RustCrypto Developers; Copyright (c) 2021-2026 RustCrypto Developers
- `ctr`: Copyright (c) 2018-2022 RustCrypto Developers; Copyright (c) 2018 Artyom Pavlov
- `ctutils`: Copyright (c) 2025-2026 The RustCrypto Project Developers
- `davey`: Copyright (c) 2025-present Snazzah
- `der`: Copyright (c) 2020-2023 The RustCrypto Project Developers
- `digest`: Copyright (c) 2017 Artyom Pavlov
- `ecdsa`: Copyright 2018-2022 RustCrypto Developers; Copyright (c) 2018-2022 RustCrypto Developers
- `ed25519`: Copyright 2018-2022 RustCrypto Developers; Copyright (c) 2018-2023 RustCrypto Developers
- `elliptic-curve`: Copyright (c) 2020-2022 RustCrypto Developers
- `extended`: Copyright 2022 Dietrich Epp
- `ff`: Copyright (c) 2017 Sean Bowe
- `fluvio-wasm-timer`: Copyright 2019 Pierre Krieger; Copyright (c) 2019 Tokio Contributors
- `futures`: Copyright (c) 2016 Alex Crichton; Copyright (c) 2017 The Tokio Authors
- `futures-executor`: Copyright (c) 2016 Alex Crichton; Copyright (c) 2017 The Tokio Authors
- `futures-io`: Copyright (c) 2016 Alex Crichton; Copyright (c) 2017 The Tokio Authors
- `generic-array`: Copyright (c) 2015 Bartłomiej Kamiński
- `ghash`: Copyright (c) 2019 RustCrypto Developers; Copyright (c) 2019-2026 The RustCrypto Project Developers
- `group`: Copyright (c) Sean Bowe and Jack Grigg
- `hkdf`: Copyright (c) 2015-2018 Vlad Filippov; Copyright (c) 2018-2021 RustCrypto Developers
- `hmac`: Copyright (c) 2017 Artyom Pavlov
- `hybrid-array`: Copyright (c) 2022-2026 The RustCrypto Project Developers
- `inout`: Copyright (c) 2022 The RustCrypto Project Developers; Copyright (c) 2022 Artyom Pavlov; Copyright (c) 2022-2025 The RustCrypto Project Developers
- `k256`: Copyright (c) 2020-2024 RustCrypto Developers
- `lock_api`: Copyright (c) 2016 The Rust Project Developers
- `num-complex`: Copyright (c) 2014 The Rust Project Developers
- `num-integer`: Copyright (c) 2014 The Rust Project Developers
- `opaque-debug`: Copyright (c) 2018-2024 The RustCrypto Project Developers
- `openmls`: Copyright (c) 2020 OpenMLS Authors
- `openmls_basic_credential`: Copyright (c) 2020 OpenMLS Authors
- `openmls_memory_storage`: Copyright (c) 2020 OpenMLS Authors
- `openmls_rust_crypto`: Copyright (c) 2020 OpenMLS Authors
- `openmls_traits`: Copyright (c) 2020 OpenMLS Authors
- `opus`: Copyright (c) 2016 Tad Hardesty
- `p256`: Copyright (c) 2020-2023 RustCrypto Developers
- `p384`: Copyright (c) 2020-2021 RustCrypto Developers
- `parking_lot`: Copyright (c) 2016 The Rust Project Developers
- `parking_lot_core`: Copyright (c) 2016 The Rust Project Developers
- `pbkdf2`: Copyright (c) 2017 Artyom Pavlov; Copyright (c) 2018-2023 The RustCrypto Project Developers
- `pem-rfc7468`: Copyright (c) 2021 The RustCrypto Project Developers
- `pin-utils`: Copyright 2018 The pin-utils authors; Copyright (c) 2018 The pin-utils authors
- `pkcs8`: Copyright (c) 2020-2023 The RustCrypto Project Developers
- `poly1305`: Copyright (c) 2015-2019 RustCrypto Developers; Copyright (c) 2015-2026 The RustCrypto Project Developers
- `polyval`: Copyright (c) 2019-2023 RustCrypto Developers; Copyright (c) 2019-2026 The RustCrypto Project Developers
- `ppv-lite86`: Copyright 2019 The CryptoCorrosion Contributors; Copyright (c) 2019 The CryptoCorrosion Contributors
- `primal-check`: Copyright (c) 2014 Huon Wilson
- `primeorder`: Copyright (c) 2020-2023 RustCrypto Developers
- `rand`: Copyright 2018 Developers of the Rand project; Copyright (c) 2014 The Rust Project Developers
- `rand_chacha`: Copyright 2018 Developers of the Rand project; Copyright (c) 2014 The Rust Project Developers
- `rand_core`: Copyright 2018 Developers of the Rand project; Copyright (c) 2014 The Rust Project Developers
- `rayon`: Copyright (c) 2010 The Rust Project Developers
- `rayon-core`: Copyright (c) 2010 The Rust Project Developers
- `realfft`: Copyright (c) 2020 Henrik Enquist and RealFFT contributors
- `regex-lite`: Copyright (c) 2014 The Rust Project Developers
- `rfc6979`: Copyright 2018-2022 RustCrypto Developers; Copyright (c) 2018-2022 RustCrypto Developers
- `rubato`: Copyright (c) 2020 Henrik Enquist
- `rustfft`: Copyright (c) 2015 The RustFFT Developers
- `salsa20`: Copyright (c) 2019 Eric McCorkle
- `scrypt`: Copyright (c) 2021-2023 The RustCrypto Project Developers
- `sec1`: Copyright (c) 2021-2022 The RustCrypto Project Developers
- `serde_bytes`: Copyright (c) David Tolnay
- `sha2`: Copyright (c) 2006-2009 Graydon Hoare; Copyright (c) 2009-2013 Mozilla Foundation; Copyright (c) 2016 Artyom Pavlov
- `signature`: Copyright (c) 2018-2023 RustCrypto Developers
- `spki`: Copyright (c) 2021-2023 The RustCrypto Project Developers
- `strength_reduce`: Copyright (c) 2015 The RustFFT Developers
- `tls_codec`: Copyright (c) 2020 The RustCrypto Project Developers
- `transpose`: Copyright 2022 The transpose developers; Copyright (c) 2022 The transpose Developers
- `typenum`: Copyright 2014 Paho Lurie-Gregg; Copyright (c) 2014 Paho Lurie-Gregg
- `universal-hash`: Copyright (c) 2019-2020 RustCrypto Developers; Copyright (c) 2019-2025 RustCrypto Developers
- `windowfunctions`: Copyright (c) 2025 Henrik Enquist

```text
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Zero-Clause BSD

Used under the Zero-Clause BSD license, which asks for no notice: `audio-codec-algorithms`.
