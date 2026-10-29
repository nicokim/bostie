use std::io;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr};
use std::sync::Arc;
use std::time::{Duration, Instant};

use tokio::net::UdpSocket;
use tokio::sync::watch;
use tokio::time::timeout;

use super::rtp::{self, Kind};

pub const DISCOVERY_LEN: usize = 74;

const REQUEST: u16 = 1;
const RESPONSE: u16 = 2;
const BODY_LEN: u16 = 70;
const MAX_DATAGRAM: usize = 2048;

const DISCOVERY_ATTEMPTS: usize = 3;

#[derive(Debug, thiserror::Error)]
pub enum DiscoveryError {
    #[error("a {0}-byte datagram is not a {DISCOVERY_LEN}-byte discovery response")]
    Size(usize),
    #[error("discovery packet type {0:#06x} is not a response")]
    Type(u16),
    #[error("discovery length field {0} is not {BODY_LEN}")]
    Length(u16),
    #[error("discovery response for SSRC {got}, expected {expected}")]
    Ssrc { expected: u32, got: u32 },
    #[error("the discovery address is not NUL-terminated")]
    Unterminated,
    #[error("the discovery address is not an IP address")]
    Address,
    #[error("ip discovery: the socket has no peer")]
    Socket(#[source] io::Error),
    #[error("ip discovery: failed to send to {peer}")]
    Send {
        peer: SocketAddr,
        #[source]
        source: io::Error,
    },
    #[error("ip discovery: no answer from {0}; outbound UDP may be blocked")]
    NoAnswer(SocketAddr),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Answer {
    pub address: SocketAddr,
    pub at: Instant,
}

pub fn bind(remote: SocketAddr) -> io::Result<(UdpSocket, Arc<std::net::UdpSocket>)> {
    let local = match remote.ip() {
        IpAddr::V4(_) => IpAddr::V4(Ipv4Addr::UNSPECIFIED),
        IpAddr::V6(_) => IpAddr::V6(Ipv6Addr::UNSPECIFIED),
    };
    let socket = std::net::UdpSocket::bind(SocketAddr::new(local, 0))?;
    socket.connect(remote)?;
    socket.set_nonblocking(true)?;
    let sender = socket.try_clone()?;
    sender.set_nonblocking(true)?;
    Ok((UdpSocket::from_std(socket)?, Arc::new(sender)))
}

pub fn discovery_request(ssrc: u32) -> [u8; DISCOVERY_LEN] {
    let mut request = [0; DISCOVERY_LEN];
    if let Some(head) = request.first_chunk_mut::<8>() {
        let [t0, t1] = REQUEST.to_be_bytes();
        let [l0, l1] = BODY_LEN.to_be_bytes();
        let [s0, s1, s2, s3] = ssrc.to_be_bytes();
        *head = [t0, t1, l0, l1, s0, s1, s2, s3];
    }
    request
}

pub fn parse_discovery(packet: &[u8], ssrc: u32) -> Result<SocketAddr, DiscoveryError> {
    let &[t0, t1, l0, l1, s0, s1, s2, s3, address @ .., p0, p1] =
        <&[u8; DISCOVERY_LEN]>::try_from(packet).map_err(|_| DiscoveryError::Size(packet.len()))?;
    let kind = u16::from_be_bytes([t0, t1]);
    if kind != RESPONSE {
        return Err(DiscoveryError::Type(kind));
    }
    let length = u16::from_be_bytes([l0, l1]);
    if length != BODY_LEN {
        return Err(DiscoveryError::Length(length));
    }
    let got = u32::from_be_bytes([s0, s1, s2, s3]);
    if got != ssrc {
        return Err(DiscoveryError::Ssrc {
            expected: ssrc,
            got,
        });
    }
    let end = address
        .iter()
        .position(|&byte| byte == 0)
        .ok_or(DiscoveryError::Unterminated)?;
    let ip = address
        .get(..end)
        .and_then(|text| std::str::from_utf8(text).ok())
        .and_then(|text| text.parse::<IpAddr>().ok())
        .ok_or(DiscoveryError::Address)?;
    Ok(SocketAddr::new(ip, u16::from_be_bytes([p0, p1])))
}

pub async fn discover(
    socket: &UdpSocket,
    answers: &mut watch::Receiver<Option<Answer>>,
    ssrc: u32,
    interval: Duration,
) -> Result<SocketAddr, DiscoveryError> {
    let peer = socket.peer_addr().map_err(DiscoveryError::Socket)?;
    let request = discovery_request(ssrc);
    for _ in 0..DISCOVERY_ATTEMPTS {
        answers.mark_unchanged();
        socket
            .send(&request)
            .await
            .map_err(|source| DiscoveryError::Send { peer, source })?;
        if let Ok(Ok(())) = timeout(interval, answers.changed()).await
            && let Some(answer) = *answers.borrow_and_update()
        {
            return Ok(answer.address);
        }
    }
    Err(DiscoveryError::NoAnswer(peer))
}

pub async fn receive(
    socket: &UdpSocket,
    ssrc: u32,
    answers: &watch::Sender<Option<Answer>>,
    mut on_rtp: impl FnMut(&mut [u8]),
    mut on_rtcp: impl FnMut(&[u8]),
) -> io::Result<()> {
    let mut buffer = [0; MAX_DATAGRAM];
    loop {
        let len = match socket.recv(&mut buffer).await {
            Ok(len) => len,
            Err(error)
                if matches!(
                    error.kind(),
                    io::ErrorKind::ConnectionRefused | io::ErrorKind::ConnectionReset
                ) =>
            {
                continue;
            }
            Err(error) => return Err(error),
        };
        let at = Instant::now();
        let Some(packet) = buffer.get_mut(..len) else {
            continue;
        };
        if let Ok(address) = parse_discovery(packet, ssrc) {
            answers.send_replace(Some(Answer { address, at }));
        } else {
            match rtp::classify(packet) {
                Kind::Rtp => on_rtp(packet),
                Kind::Rtcp => on_rtcp(packet),
                Kind::Other => {}
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicUsize, Ordering};

    use parking_lot::Mutex;

    use proptest::prelude::*;

    use super::*;
    use crate::voice::testkit::sfu::{Sfu, response};

    const SSRC: u32 = 0x8000_0001;
    const WAIT: Duration = Duration::from_secs(5);
    const INTERVAL: Duration = Duration::from_millis(50);

    fn external() -> SocketAddr {
        "203.0.113.7:50004".parse().unwrap()
    }

    fn rejection(packet: &[u8]) -> DiscoveryError {
        parse_discovery(packet, SSRC).unwrap_err()
    }

    fn with_address(text: &[u8]) -> [u8; DISCOVERY_LEN] {
        let mut packet = response(SSRC, external());
        packet[8..72].fill(0);
        packet[8..8 + text.len()].copy_from_slice(text);
        packet
    }

    #[test]
    fn the_request_is_type_1_length_70_the_ssrc_then_zeros() {
        let request = discovery_request(0xDEAD_BEEF);

        assert_eq!(request[..8], [0, 1, 0, 70, 0xDE, 0xAD, 0xBE, 0xEF]);
        assert!(request[8..].iter().all(|&byte| byte == 0));
    }

    #[test]
    fn a_response_yields_the_external_address_and_port() {
        let mut packet = [0; DISCOVERY_LEN];
        packet[..8].copy_from_slice(&[0, 2, 0, 70, 0x80, 0, 0, 1]);
        packet[8..19].copy_from_slice(b"203.0.113.7");
        packet[72..].copy_from_slice(&[0xC3, 0x54]);

        assert_eq!(parse_discovery(&packet, SSRC).unwrap(), external());
        assert_eq!(response(SSRC, external()), packet);
    }

    #[test]
    fn an_ipv6_response_yields_an_ipv6_address() {
        let address: SocketAddr = "[2001:db8::7]:50004".parse().unwrap();

        assert_eq!(
            parse_discovery(&response(SSRC, address), SSRC).unwrap(),
            address
        );
    }

    #[test]
    fn a_request_is_not_a_response() {
        assert!(matches!(
            rejection(&discovery_request(SSRC)),
            DiscoveryError::Type(1)
        ));
    }

    #[test]
    fn a_datagram_of_another_size_is_rejected() {
        let packet = response(SSRC, external());

        assert!(matches!(rejection(&packet[..73]), DiscoveryError::Size(73)));
        assert!(matches!(
            rejection(&[&packet[..], &[0]].concat()),
            DiscoveryError::Size(75)
        ));
    }

    #[test]
    fn a_length_field_other_than_70_is_rejected() {
        let mut packet = response(SSRC, external());
        packet[3] = 66;

        assert!(matches!(rejection(&packet), DiscoveryError::Length(66)));
    }

    #[test]
    fn a_response_for_another_ssrc_is_rejected() {
        assert!(matches!(
            rejection(&response(7, external())),
            DiscoveryError::Ssrc {
                expected: SSRC,
                got: 7
            }
        ));
    }

    #[test]
    fn an_address_without_nul_is_rejected() {
        let mut packet = response(SSRC, external());
        packet[8..72].fill(b'1');

        assert!(matches!(rejection(&packet), DiscoveryError::Unterminated));
    }

    #[test]
    fn an_address_that_is_not_an_ip_is_rejected() {
        for text in [&b"discord.media"[..], b"", b"203.0.113.256", b"\xFF\xFE"] {
            assert!(
                matches!(rejection(&with_address(text)), DiscoveryError::Address),
                "{text:?}"
            );
        }
    }

    #[test]
    fn bytes_after_the_nul_are_ignored() {
        let mut packet = with_address(b"203.0.113.7");
        packet[40] = b'x';

        assert_eq!(parse_discovery(&packet, SSRC).unwrap(), external());
    }

    proptest! {
        #[test]
        fn random_bytes_never_panic(
            packet in proptest::collection::vec(any::<u8>(), 0..96),
            ssrc in any::<u32>(),
        ) {
            let _ = parse_discovery(&packet, ssrc);
        }

        #[test]
        fn random_discovery_sized_bytes_never_panic(
            mut packet in proptest::collection::vec(any::<u8>(), DISCOVERY_LEN),
        ) {
            packet[..8].copy_from_slice(&response(SSRC, external())[..8]);
            let _ = parse_discovery(&packet, SSRC);
        }
    }

    #[tokio::test]
    async fn bound_sockets_follow_the_remote_family_and_share_one_local_port() {
        for remote in ["127.0.0.1:9", "[::1]:9"] {
            let remote: SocketAddr = remote.parse().unwrap();

            let (socket, sender) = bind(remote).unwrap();

            let local = socket.local_addr().unwrap();
            assert_eq!(local.is_ipv4(), remote.is_ipv4(), "{remote}");
            assert_eq!(sender.local_addr().unwrap(), local);
            assert_eq!(socket.peer_addr().unwrap(), remote);
            assert_eq!(sender.peer_addr().unwrap(), remote);
        }
    }

    #[tokio::test]
    async fn the_pacer_socket_never_blocks() {
        let (_socket, sender) = bind("127.0.0.1:9".parse().unwrap()).unwrap();
        let mut buffer = [0; 16];

        let error = sender.recv(&mut buffer).unwrap_err();

        assert_eq!(error.kind(), io::ErrorKind::WouldBlock);
    }

    type Rtcp = Arc<Mutex<Vec<Vec<u8>>>>;

    fn spawn_receiver(
        socket: Arc<UdpSocket>,
    ) -> (watch::Receiver<Option<Answer>>, Arc<AtomicUsize>, Rtcp) {
        let (answers, receiver) = watch::channel(None);
        let rtp = Arc::new(AtomicUsize::new(0));
        let rtcp = Rtcp::default();
        let counted = Arc::clone(&rtp);
        let kept = Arc::clone(&rtcp);
        tokio::spawn(async move {
            receive(
                &socket,
                SSRC,
                &answers,
                |_| {
                    counted.fetch_add(1, Ordering::Relaxed);
                },
                |packet| kept.lock().push(packet.to_vec()),
            )
            .await
        });
        (receiver, rtp, rtcp)
    }

    #[tokio::test]
    async fn the_receiver_routes_answers_counts_rtp_and_ignores_everything_else() {
        let server = std::net::UdpSocket::bind("127.0.0.1:0").unwrap();
        let (socket, _sender) = bind(server.local_addr().unwrap()).unwrap();
        let client = SocketAddr::new(
            Ipv4Addr::LOCALHOST.into(),
            socket.local_addr().unwrap().port(),
        );
        let (mut answers, rtp, _) = spawn_receiver(Arc::new(socket));
        let mut voice = [0; 40];
        rtp::write_header((&mut voice[..12]).try_into().unwrap(), 1, 2, 3);
        let before = Instant::now();

        for datagram in [
            &voice[..],
            &response(7, external())[..],
            b"junk",
            &voice[..],
            &response(SSRC, external())[..],
        ] {
            server.send_to(datagram, client).unwrap();
        }
        timeout(WAIT, answers.changed()).await.unwrap().unwrap();

        let answer = answers.borrow_and_update().unwrap();
        assert_eq!(answer.address, external());
        assert!(answer.at >= before);
        assert_eq!(rtp.load(Ordering::Relaxed), 2);
    }

    #[tokio::test]
    async fn rtcp_datagrams_reach_the_rtcp_callback_and_never_the_rtp_one() {
        let server = std::net::UdpSocket::bind("127.0.0.1:0").unwrap();
        let (socket, _sender) = bind(server.local_addr().unwrap()).unwrap();
        let client = SocketAddr::new(
            Ipv4Addr::LOCALHOST.into(),
            socket.local_addr().unwrap().port(),
        );
        let (mut answers, rtp, rtcp) = spawn_receiver(Arc::new(socket));
        let sender_report = [0x80, 0xC8, 0, 6, 0, 0, 0, 1, 9, 9, 9, 9];
        let receiver_report = [0x81, 0xC9, 0, 7, 0, 0, 0, 2];
        let mut voice = [0; 40];
        rtp::write_header((&mut voice[..12]).try_into().unwrap(), 1, 2, 3);

        for datagram in [
            &sender_report[..],
            &voice[..],
            &receiver_report[..],
            &response(SSRC, external())[..],
        ] {
            server.send_to(datagram, client).unwrap();
        }
        timeout(WAIT, answers.changed()).await.unwrap().unwrap();

        assert_eq!(*rtcp.lock(), [&sender_report[..], &receiver_report[..]]);
        assert_eq!(rtp.load(Ordering::Relaxed), 1);
    }

    #[tokio::test]
    async fn the_receiver_outlives_port_unreachable_errors() {
        let (server, client, mut answers) = loop {
            let closed = std::net::UdpSocket::bind("127.0.0.1:0").unwrap();
            let address = closed.local_addr().unwrap();
            drop(closed);
            let (socket, sender) = bind(address).unwrap();
            let client = SocketAddr::new(
                Ipv4Addr::LOCALHOST.into(),
                socket.local_addr().unwrap().port(),
            );
            let (answers, ..) = spawn_receiver(Arc::new(socket));

            sender.send(b"anyone there").unwrap();
            tokio::time::sleep(Duration::from_millis(100)).await;
            if let Ok(server) = std::net::UdpSocket::bind(address) {
                break (server, client, answers);
            }
        };

        server.send_to(&response(SSRC, external()), client).unwrap();

        timeout(WAIT, answers.changed()).await.unwrap().unwrap();
        assert_eq!(answers.borrow().unwrap().address, external());
    }

    #[tokio::test]
    async fn discovery_needs_a_connected_socket() {
        let socket = UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let (_answers, mut receiver) = watch::channel(None);

        let error = discover(&socket, &mut receiver, SSRC, INTERVAL)
            .await
            .unwrap_err();

        assert!(matches!(error, DiscoveryError::Socket(_)), "{error:?}");
    }

    #[tokio::test]
    async fn discovery_returns_the_address_the_server_saw() {
        let sfu = Sfu::start();
        let (socket, _sender) = bind(sfu.address()).unwrap();
        let port = socket.local_addr().unwrap().port();
        let socket = Arc::new(socket);
        let (mut answers, ..) = spawn_receiver(Arc::clone(&socket));

        let address = discover(&socket, &mut answers, SSRC, INTERVAL)
            .await
            .unwrap();

        assert_eq!(address, SocketAddr::new(Ipv4Addr::LOCALHOST.into(), port));
        assert_eq!(sfu.discoveries(), 1);
    }

    #[tokio::test]
    async fn discovery_retries_until_its_last_attempt_is_answered() {
        let sfu = Sfu::start();
        sfu.ignore_discoveries(DISCOVERY_ATTEMPTS - 1);
        let (socket, _sender) = bind(sfu.address()).unwrap();
        let socket = Arc::new(socket);
        let (mut answers, ..) = spawn_receiver(Arc::clone(&socket));

        let address = discover(&socket, &mut answers, SSRC, INTERVAL)
            .await
            .unwrap();

        assert_eq!(address.port(), socket.local_addr().unwrap().port());
        assert_eq!(sfu.discoveries(), DISCOVERY_ATTEMPTS);
    }

    #[tokio::test]
    async fn discovery_without_any_answer_names_the_server() {
        let sfu = Sfu::start();
        sfu.ignore_discoveries(DISCOVERY_ATTEMPTS);
        let (socket, _sender) = bind(sfu.address()).unwrap();
        let socket = Arc::new(socket);
        let (mut answers, ..) = spawn_receiver(Arc::clone(&socket));

        let error = discover(&socket, &mut answers, SSRC, INTERVAL)
            .await
            .unwrap_err();

        assert_eq!(
            error.to_string(),
            format!(
                "ip discovery: no answer from {}; outbound UDP may be blocked",
                sfu.address()
            )
        );
        assert_eq!(sfu.discoveries(), DISCOVERY_ATTEMPTS);
    }
}
