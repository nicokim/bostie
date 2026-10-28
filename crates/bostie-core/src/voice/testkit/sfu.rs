use std::net::{SocketAddr, UdpSocket};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use parking_lot::{Condvar, Mutex, MutexGuard};

use crate::voice::crypto::{Cipher, KEY_LEN, Mode};
use crate::voice::rtp::{self, Header, Kind};
use crate::voice::types::SILENCE_FRAME;
use crate::voice::udp::DISCOVERY_LEN;

const POLL: Duration = Duration::from_millis(10);
const REQUEST_HEAD: [u8; 4] = [0, 1, 0, 70];

pub fn response(ssrc: u32, address: SocketAddr) -> [u8; DISCOVERY_LEN] {
    let mut packet = [0; DISCOVERY_LEN];
    packet[..4].copy_from_slice(&[0, 2, 0, 70]);
    packet[4..8].copy_from_slice(&ssrc.to_be_bytes());
    let text = address.ip().to_string();
    packet[8..8 + text.len()].copy_from_slice(text.as_bytes());
    packet[72..].copy_from_slice(&address.port().to_be_bytes());
    packet
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Packet {
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
    packets: Vec<Packet>,
    rejected: usize,
}

#[derive(Default)]
struct Shared {
    state: Mutex<State>,
    arrived: Condvar,
    stop: AtomicBool,
}

pub struct Sfu {
    address: SocketAddr,
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

impl Sfu {
    pub fn start() -> Self {
        let socket = UdpSocket::bind("127.0.0.1:0").unwrap();
        socket.set_read_timeout(Some(POLL)).unwrap();
        let address = socket.local_addr().unwrap();
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
            shared,
            thread: Some(thread),
        }
    }

    pub fn address(&self) -> SocketAddr {
        self.address
    }

    pub fn key(&self, mode: Mode, key: &[u8; KEY_LEN]) -> usize {
        let mut state = self.shared.state.lock();
        state.keys.push(Cipher::new(mode, key));
        state.keys.len() - 1
    }

    pub fn ignore_discoveries(&self, count: usize) {
        self.shared.state.lock().unanswered = count;
    }

    pub fn discoveries(&self) -> usize {
        self.shared.state.lock().discoveries
    }

    pub fn rejected(&self) -> usize {
        self.shared.state.lock().rejected
    }

    pub fn packets(&self) -> Vec<Packet> {
        self.shared.state.lock().packets.clone()
    }

    pub fn arrivals(&self) -> Vec<Instant> {
        self.shared
            .state
            .lock()
            .packets
            .iter()
            .map(|packet| packet.at)
            .collect()
    }

    pub fn silence_runs(&self) -> Vec<usize> {
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

    pub fn wait_packets(&self, count: usize, timeout: Duration) -> Vec<Packet> {
        self.wait(timeout, |state| state.packets.len() >= count)
            .packets
            .clone()
    }

    pub fn wait_discoveries(&self, count: usize, timeout: Duration) -> usize {
        self.wait(timeout, |state| state.discoveries >= count)
            .discoveries
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
            let _ = thread.join();
        }
    }
}

fn serve(socket: &UdpSocket, shared: &Shared) {
    let mut buffer = [0; 2048];
    while !shared.stop.load(Ordering::Relaxed) {
        let (len, from) = match socket.recv_from(&mut buffer) {
            Ok(received) => received,
            Err(_) => continue,
        };
        let at = Instant::now();
        let datagram = &buffer[..len];
        if len == DISCOVERY_LEN && datagram.starts_with(&REQUEST_HEAD) {
            let ssrc = u32::from_be_bytes(datagram[4..8].try_into().unwrap());
            if answer(shared) {
                let _ = socket.send_to(&response(ssrc, from), from);
            }
        } else {
            capture(shared, datagram, at);
        }
        shared.arrived.notify_all();
    }
}

fn answer(shared: &Shared) -> bool {
    let mut state = shared.state.lock();
    state.discoveries += 1;
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
                    nonce: opened.nonce,
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
    use crate::voice::rtp::{HEADER_LEN, write_header};
    use crate::voice::udp;

    const SSRC: u32 = 42;
    const KEY: [u8; KEY_LEN] = [7; KEY_LEN];
    const WAIT: Duration = Duration::from_secs(5);

    fn send(
        socket: &std::net::UdpSocket,
        cipher: &Cipher,
        sequence: u16,
        payload: &[u8],
    ) -> Instant {
        let mut header = [0; HEADER_LEN];
        write_header(&mut header, sequence, u32::from(sequence) * 960, SSRC);
        let mut packet = Vec::new();
        cipher
            .seal(u32::from(sequence), &header, payload, &mut packet)
            .unwrap();
        let sent = Instant::now();
        socket.send(&packet).unwrap();
        sent
    }

    #[test]
    fn sealed_packets_are_decrypted_and_parsed_on_arrival() {
        let sfu = Sfu::start();
        sfu.key(Mode::Aes256GcmRtpSize, &KEY);
        let cipher = Cipher::new(Mode::Aes256GcmRtpSize, &KEY);
        let socket = std::net::UdpSocket::bind("127.0.0.1:0").unwrap();
        socket.connect(sfu.address()).unwrap();

        let sent = send(&socket, &cipher, 9, b"opus");
        let packets = sfu.wait_packets(1, WAIT);

        assert_eq!(packets.len(), 1);
        let packet = &packets[0];
        assert_eq!(packet.key, 0);
        assert_eq!(packet.header.sequence, 9);
        assert_eq!(packet.header.timestamp, 9 * 960);
        assert_eq!(packet.header.ssrc, SSRC);
        assert_eq!(packet.nonce, 9);
        assert_eq!(packet.payload, b"opus");
        assert!(packet.at >= sent);
        assert_eq!(sfu.rejected(), 0);
    }

    #[test]
    fn arrivals_are_captured_on_the_sfu_thread_while_the_sender_waits() {
        let sfu = Sfu::start();
        sfu.key(Mode::XChaCha20Poly1305RtpSize, &KEY);
        let cipher = Cipher::new(Mode::XChaCha20Poly1305RtpSize, &KEY);
        let socket = std::net::UdpSocket::bind("127.0.0.1:0").unwrap();
        socket.connect(sfu.address()).unwrap();
        let mut sent = Vec::new();

        for sequence in 0..3 {
            sent.push(send(&socket, &cipher, sequence, b"opus"));
            sfu.wait_packets(usize::from(sequence) + 1, WAIT);
            thread::sleep(Duration::from_millis(5));
        }

        let arrivals = sfu.arrivals();
        assert_eq!(arrivals.len(), 3);
        for (arrival, sent) in arrivals.iter().zip(&sent) {
            assert!(arrival >= sent);
        }
        assert!(arrivals[1] < sent[2]);
    }

    #[test]
    fn packets_try_the_newest_key_first_and_fall_back_to_older_ones() {
        let sfu = Sfu::start();
        let old = [1; KEY_LEN];
        assert_eq!(sfu.key(Mode::Aes256GcmRtpSize, &old), 0);
        assert_eq!(sfu.key(Mode::XChaCha20Poly1305RtpSize, &KEY), 1);
        let socket = std::net::UdpSocket::bind("127.0.0.1:0").unwrap();
        socket.connect(sfu.address()).unwrap();

        send(
            &socket,
            &Cipher::new(Mode::Aes256GcmRtpSize, &old),
            1,
            b"old",
        );
        send(
            &socket,
            &Cipher::new(Mode::XChaCha20Poly1305RtpSize, &KEY),
            2,
            b"new",
        );
        let packets = sfu.wait_packets(2, WAIT);

        assert_eq!(
            packets
                .iter()
                .map(|packet| (packet.key, packet.payload.as_slice()))
                .collect::<Vec<_>>(),
            [(0, &b"old"[..]), (1, &b"new"[..])]
        );
    }

    #[test]
    fn datagrams_no_key_opens_and_datagrams_that_are_not_rtp_are_rejected() {
        let sfu = Sfu::start();
        sfu.key(Mode::Aes256GcmRtpSize, &KEY);
        let socket = std::net::UdpSocket::bind("127.0.0.1:0").unwrap();
        socket.connect(sfu.address()).unwrap();

        send(
            &socket,
            &Cipher::new(Mode::Aes256GcmRtpSize, &[2; KEY_LEN]),
            1,
            b"x",
        );
        socket.send(b"junk").unwrap();
        send(&socket, &Cipher::new(Mode::Aes256GcmRtpSize, &KEY), 2, b"y");
        let packets = sfu.wait_packets(1, WAIT);

        assert_eq!(packets.len(), 1);
        assert_eq!(packets[0].payload, b"y");
        assert_eq!(sfu.rejected(), 2);
    }

    #[test]
    fn silence_runs_count_consecutive_silence_frames() {
        let sfu = Sfu::start();
        sfu.key(Mode::Aes256GcmRtpSize, &KEY);
        let cipher = Cipher::new(Mode::Aes256GcmRtpSize, &KEY);
        let socket = std::net::UdpSocket::bind("127.0.0.1:0").unwrap();
        socket.connect(sfu.address()).unwrap();
        let payloads: [&[u8]; 9] = [
            b"a",
            &SILENCE_FRAME,
            &SILENCE_FRAME,
            b"b",
            &SILENCE_FRAME,
            &SILENCE_FRAME,
            &SILENCE_FRAME,
            &SILENCE_FRAME,
            &SILENCE_FRAME,
        ];

        for (sequence, payload) in (0..).zip(payloads) {
            send(&socket, &cipher, sequence, payload);
        }
        sfu.wait_packets(payloads.len(), WAIT);

        assert_eq!(sfu.silence_runs(), [2, 5]);
    }

    #[test]
    fn ignored_discoveries_are_still_counted() {
        let sfu = Sfu::start();
        sfu.ignore_discoveries(1);
        let socket = std::net::UdpSocket::bind("127.0.0.1:0").unwrap();
        socket.set_read_timeout(Some(WAIT)).unwrap();
        socket.connect(sfu.address()).unwrap();
        let mut answer = [0; DISCOVERY_LEN];

        socket.send(&udp::discovery_request(SSRC)).unwrap();
        socket.send(&udp::discovery_request(SSRC)).unwrap();
        let len = socket.recv(&mut answer).unwrap();

        assert_eq!(len, DISCOVERY_LEN);
        assert_eq!(
            udp::parse_discovery(&answer, SSRC).unwrap(),
            socket.local_addr().unwrap()
        );
        assert_eq!(sfu.discoveries(), 2);
    }

    #[test]
    fn waiting_for_discoveries_returns_once_enough_arrived() {
        let sfu = Sfu::start();
        sfu.ignore_discoveries(usize::MAX);
        let socket = std::net::UdpSocket::bind("127.0.0.1:0").unwrap();
        socket.connect(sfu.address()).unwrap();

        for _ in 0..3 {
            socket.send(&udp::discovery_request(SSRC)).unwrap();
        }

        assert_eq!(sfu.wait_discoveries(3, WAIT), 3);
        assert_eq!(sfu.wait_discoveries(4, Duration::from_millis(30)), 3);
    }
}
