use std::net::{SocketAddr, UdpSocket};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use parking_lot::{Condvar, Mutex, MutexGuard};

use crate::voice::crypto::{Cipher, KEY_LEN, Mode};
use crate::voice::rtp::{self, HEADER_LEN, Header, Kind};
use crate::voice::types::SILENCE_FRAME;
use crate::voice::udp::DISCOVERY_LEN;

const POLL: Duration = Duration::from_millis(10);
const REQUEST_HEAD: [u8; 4] = [0, 1, 0, 70];

pub(crate) fn response(ssrc: u32, address: SocketAddr) -> [u8; DISCOVERY_LEN] {
    let mut packet = [0; DISCOVERY_LEN];
    packet[..4].copy_from_slice(&[0, 2, 0, 70]);
    packet[4..8].copy_from_slice(&ssrc.to_be_bytes());
    let text = address.ip().to_string();
    packet[8..8 + text.len()].copy_from_slice(text.as_bytes());
    packet[72..].copy_from_slice(&address.port().to_be_bytes());
    packet
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct Sealing {
    pub extension: Option<Vec<u8>>,
    pub padding: u8,
    pub csrcs: u8,
    pub marker: bool,
}

pub(crate) fn seal(
    cipher: &Cipher,
    nonce: u32,
    ssrc: u32,
    sequence: u16,
    timestamp: u32,
    payload: &[u8],
    send: &Sealing,
) -> Vec<u8> {
    assert!(send.csrcs < 16, "an RTP header holds at most 15 CSRCs");
    let mut fixed = [0; HEADER_LEN];
    rtp::write_header(&mut fixed, sequence, timestamp, ssrc);
    fixed[0] |= send.csrcs;
    if send.padding > 0 {
        fixed[0] |= rtp::PADDING;
    }
    if send.marker {
        fixed[1] |= rtp::MARKER;
    }
    let mut header = fixed.to_vec();
    header.extend((1..=u32::from(send.csrcs)).flat_map(u32::to_be_bytes));
    let mut body = Vec::new();
    if let Some(extension) = &send.extension {
        let words = extension.len().div_ceil(4);
        header[0] |= rtp::EXTENSION;
        header.extend_from_slice(&rtp::ONE_BYTE_PROFILE.to_be_bytes());
        header.extend_from_slice(&u16::try_from(words).unwrap().to_be_bytes());
        body.extend_from_slice(extension);
        body.resize(4 * words, 0);
    }
    body.extend_from_slice(payload);
    if send.padding > 0 {
        body.resize(body.len() + usize::from(send.padding) - 1, 0);
        body.push(send.padding);
    }
    let mut packet = Vec::new();
    let little_endian = u32::from_be_bytes(nonce.to_le_bytes());
    cipher
        .seal(little_endian, &header, &body, &mut packet)
        .unwrap();
    packet
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Packet {
    pub at: Instant,
    pub key: usize,
    pub header: Header,
    pub nonce: u32,
    pub payload: Vec<u8>,
}

#[derive(Default)]
struct State {
    keys: Vec<Cipher>,
    unanswered: usize,
    discoveries: usize,
    client: Option<SocketAddr>,
    nonce: u32,
    packets: Vec<Packet>,
    rejected: usize,
}

#[derive(Default)]
struct Shared {
    state: Mutex<State>,
    arrived: Condvar,
    stop: AtomicBool,
}

pub(crate) struct Sfu {
    address: SocketAddr,
    socket: UdpSocket,
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

impl Sfu {
    pub(crate) fn start() -> Self {
        let socket = UdpSocket::bind("127.0.0.1:0").unwrap();
        socket.set_read_timeout(Some(POLL)).unwrap();
        let address = socket.local_addr().unwrap();
        let sender = socket.try_clone().unwrap();
        let shared = Arc::new(Shared::default());
        let thread = thread::Builder::new()
            .name("test-sfu".to_owned())
            .spawn({
                let shared = Arc::clone(&shared);
                move || serve(&socket, &shared)
            })
            .unwrap();
        Self {
            address,
            socket: sender,
            shared,
            thread: Some(thread),
        }
    }

    pub(crate) fn address(&self) -> SocketAddr {
        self.address
    }

    pub(crate) fn send(
        &self,
        ssrc: u32,
        sequence: u16,
        timestamp: u32,
        payload: &[u8],
        send: &Sealing,
    ) {
        let packet = {
            let mut state = self.shared.state.lock();
            let nonce = state.nonce;
            state.nonce = nonce.wrapping_add(1);
            let cipher = state.keys.last().expect("send needs a registered key");
            seal(cipher, nonce, ssrc, sequence, timestamp, payload, send)
        };
        self.send_raw(&packet);
    }

    pub(crate) fn send_raw(&self, bytes: &[u8]) {
        let client = self.shared.state.lock().client;
        let client = client.expect("send needs a client that sent a discovery request");
        self.socket.send_to(bytes, client).unwrap();
    }

    pub(crate) fn key(&self, mode: Mode, key: &[u8; KEY_LEN]) -> usize {
        let mut state = self.shared.state.lock();
        state.keys.push(Cipher::new(mode, key));
        state.keys.len() - 1
    }

    pub(crate) fn ignore_discoveries(&self, count: usize) {
        self.shared.state.lock().unanswered = count;
    }

    pub(crate) fn discoveries(&self) -> usize {
        self.shared.state.lock().discoveries
    }

    pub(crate) fn rejected(&self) -> usize {
        self.shared.state.lock().rejected
    }

    pub(crate) fn packets(&self) -> Vec<Packet> {
        self.shared.state.lock().packets.clone()
    }

    pub(crate) fn silence_runs(&self) -> Vec<usize> {
        let state = self.shared.state.lock();
        let mut runs = Vec::new();
        let mut run = 0;
        for packet in &state.packets {
            if packet.payload == SILENCE_FRAME {
                run += 1;
            } else if run > 0 {
                runs.push(run);
                run = 0;
            }
        }
        if run > 0 {
            runs.push(run);
        }
        runs
    }

    pub(crate) fn flush(&self, socket: &UdpSocket, timeout: Duration) -> Vec<Packet> {
        let rejected = self.rejected();
        socket.send(&[0]).unwrap();
        self.wait(timeout, |state| state.rejected > rejected)
            .packets
            .clone()
    }

    pub(crate) fn wait_packets(&self, count: usize, timeout: Duration) -> Vec<Packet> {
        self.wait(timeout, |state| state.packets.len() >= count)
            .packets
            .clone()
    }

    fn wait(&self, timeout: Duration, done: impl Fn(&State) -> bool) -> MutexGuard<'_, State> {
        let deadline = Instant::now() + timeout;
        let mut state = self.shared.state.lock();
        while !done(&state) {
            if self
                .shared
                .arrived
                .wait_until(&mut state, deadline)
                .timed_out()
            {
                break;
            }
        }
        state
    }
}

impl Drop for Sfu {
    fn drop(&mut self) {
        self.shared.stop.store(true, Ordering::Relaxed);
        if let Some(thread) = self.thread.take() {
            drop(thread.join());
        }
    }
}

fn serve(socket: &UdpSocket, shared: &Shared) {
    let mut buffer = [0; 2048];
    while !shared.stop.load(Ordering::Relaxed) {
        let Ok((len, from)) = socket.recv_from(&mut buffer) else {
            continue;
        };
        let at = Instant::now();
        let datagram = &buffer[..len];
        if len == DISCOVERY_LEN && datagram.starts_with(&REQUEST_HEAD) {
            let ssrc = u32::from_be_bytes(datagram[4..8].try_into().unwrap());
            if answer(shared, from) {
                drop(socket.send_to(&response(ssrc, from), from));
            }
        } else {
            capture(shared, datagram, at);
        }
        shared.arrived.notify_all();
    }
}

fn answer(shared: &Shared, client: SocketAddr) -> bool {
    let mut state = shared.state.lock();
    state.discoveries += 1;
    state.client = Some(client);
    if state.unanswered == 0 {
        return true;
    }
    state.unanswered -= 1;
    false
}

fn capture(shared: &Shared, datagram: &[u8], at: Instant) {
    let mut state = shared.state.lock();
    let opened = if rtp::classify(datagram) == Kind::Rtp {
        state
            .keys
            .iter()
            .enumerate()
            .rev()
            .find_map(|(key, cipher)| {
                let mut scratch = datagram.to_vec();
                let opened = cipher.open(&mut scratch).ok()?;
                Some(Packet {
                    at,
                    key,
                    header: opened.header,
                    nonce: u32::from_be_bytes(opened.nonce),
                    payload: opened.payload.to_vec(),
                })
            })
    } else {
        None
    };
    match opened {
        Some(packet) => state.packets.push(packet),
        None => state.rejected += 1,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::voice::crypto::NONCE_LEN;
    use crate::voice::rtp::{Extension, PAYLOAD_TYPE};
    use crate::voice::udp::discovery_request;

    const SSRC: u32 = 42;
    const KEY: [u8; KEY_LEN] = [7; KEY_LEN];
    const WAIT: Duration = Duration::from_secs(5);

    #[test]
    fn packets_reach_the_last_discovered_client_with_the_newest_key() {
        let sfu = Sfu::start();
        sfu.key(Mode::XChaCha20Poly1305RtpSize, &[1; KEY_LEN]);
        sfu.key(Mode::Aes256GcmRtpSize, &KEY);
        let mut buffer = [0; 2048];
        let mut discover = || {
            let socket = UdpSocket::bind("127.0.0.1:0").unwrap();
            socket.connect(sfu.address()).unwrap();
            socket.set_read_timeout(Some(WAIT)).unwrap();
            socket.send(&discovery_request(SSRC)).unwrap();
            socket.recv(&mut buffer).unwrap();
            socket
        };
        let _earlier = discover();
        let client = discover();
        let cipher = Cipher::new(Mode::Aes256GcmRtpSize, &KEY);
        let mut receive = || {
            let len = client.recv(&mut buffer).unwrap();
            buffer[..len].to_vec()
        };

        sfu.send(
            SSRC,
            9,
            960,
            b"opus",
            &Sealing {
                extension: Some(vec![0x10, 0xAA]),
                padding: 3,
                csrcs: 2,
                marker: true,
            },
        );
        sfu.send(SSRC, 10, 1920, b"next", &Sealing::default());
        sfu.send_raw(b"rtcp");

        let mut first = receive();
        let opened = cipher.open(&mut first).unwrap();
        assert_eq!(
            opened.header,
            Header {
                padding: true,
                marker: true,
                payload_type: PAYLOAD_TYPE,
                sequence: 9,
                timestamp: 960,
                ssrc: SSRC,
                csrc_count: 2,
                extension: Some(Extension {
                    profile: rtp::ONE_BYTE_PROFILE,
                    words: 1
                }),
            }
        );
        assert_eq!(opened.nonce, [0; 4]);
        assert_eq!(opened.extension, [0x10, 0xAA, 0, 0]);
        assert_eq!(opened.payload, b"opus");
        let mut second = receive();
        assert_eq!(second[second.len() - NONCE_LEN..], [1, 0, 0, 0]);
        let opened = cipher.open(&mut second).unwrap();
        assert_eq!((opened.header.sequence, opened.payload), (10, &b"next"[..]));
        assert_eq!(receive(), b"rtcp");
    }
}
