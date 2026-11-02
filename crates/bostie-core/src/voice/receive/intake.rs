use std::collections::HashMap;
use std::net::UdpSocket;
use std::panic::{self, AssertUnwindSafe};
use std::sync::Arc;
use std::time::Instant;

use bytes::Bytes;
use tracing::{debug, error};

use crate::ids::UserId;
use crate::inbox::Delivery;
use crate::voice::connection::Info;
use crate::voice::crypto::ReplayWindow;
use crate::voice::media::{Arrival, Media, Transport};
use crate::voice::rtcp;
use crate::voice::rtp::Kind;
use crate::voice::types::SILENCE_FRAME;
use crate::voice::{Throttle, panic_message};

const SPARE_WINDOWS: usize = 8;

pub(crate) struct Intake {
    media: Arc<Media>,
    info: Arc<Info>,
    socket: Arc<UdpSocket>,
    user: UserId,
    transport: Option<Arc<Transport>>,
    windows: HashMap<u32, ReplayWindow>,
    replay_window: u32,
    panicked: Throttle,
}

impl Intake {
    pub(crate) fn new(
        media: Arc<Media>,
        info: Arc<Info>,
        socket: Arc<UdpSocket>,
        user: UserId,
        replay_window: u32,
    ) -> Self {
        Self {
            media,
            info,
            socket,
            user,
            transport: None,
            windows: HashMap::new(),
            replay_window,
            panicked: Throttle::default(),
        }
    }

    pub(crate) fn receive(&mut self, kind: Kind, packet: &mut [u8]) {
        let opened = panic::catch_unwind(AssertUnwindSafe(|| match kind {
            Kind::Rtp => self.open(packet),
            Kind::Rtcp => self.report(packet),
            Kind::Other => {}
        }));
        if let Err(payload) = opened
            && self.panicked.allows(Instant::now())
        {
            error!(
                panic = panic_message(payload.as_ref()),
                "opening a received voice packet panicked and it was dropped"
            );
        }
    }

    fn adopt_transport(&mut self) {
        if self.transport.is_none() {
            let installed = self.media.transport.lock().clone();
            self.transport =
                installed.filter(|transport| Arc::ptr_eq(&transport.socket, &self.socket));
        }
    }

    fn report(&mut self, packet: &mut [u8]) {
        self.adopt_transport();
        let Some(transport) = &self.transport else {
            return;
        };
        let Ok(plain) = transport.cipher.open_rtcp(packet) else {
            debug!("an RTCP packet failed to decrypt and was dropped");
            return;
        };
        let mut packets = rtcp::parse(plain);
        if packets.is_empty() {
            return;
        }
        {
            let ssrcs = self.info.ssrcs.lock();
            let owner = |ssrc: u32| {
                if ssrc == transport.ssrc {
                    Some(self.user)
                } else {
                    ssrcs.get(&ssrc).map(|&(user, _)| user)
                }
            };
            for packet in &mut packets {
                packet.user = owner(packet.ssrc);
                for report in &mut packet.reports {
                    report.user = owner(report.ssrc);
                }
            }
        }
        let guild = self.media.guild();
        self.media.push(Delivery::VoiceRtcp { guild, packets });
    }

    fn open(&mut self, packet: &mut [u8]) {
        if !self.media.speech_activity() && self.media.open_receivers() == 0 {
            return;
        }
        self.adopt_transport();
        let media = &self.media;
        let Some(transport) = &self.transport else {
            media.transport_failed();
            return;
        };
        let Ok(opened) = transport.cipher.open(packet) else {
            media.transport_failed();
            return;
        };
        let header = opened.header;
        if !self.windows.contains_key(&header.ssrc) {
            let ssrcs = self.info.ssrcs.lock();
            if self.windows.len() >= ssrcs.len().saturating_add(SPARE_WINDOWS) {
                self.windows.retain(|ssrc, _| ssrcs.contains_key(ssrc));
            }
        }
        let replay_window = self.replay_window;
        let window = self
            .windows
            .entry(header.ssrc)
            .or_insert_with(|| ReplayWindow::new(replay_window));
        let counter = u32::from_le_bytes(opened.nonce);
        if !window.accept(counter) || header.ssrc == transport.ssrc {
            return;
        }
        let payload = (media.open_receivers() > 0).then(|| Bytes::copy_from_slice(opened.payload));
        let extension = header
            .extension
            .filter(|_| media.packet_receivers() > 0)
            .map(|extension| (extension.profile, Bytes::copy_from_slice(opened.extension)));
        let silence = opened.payload == SILENCE_FRAME;
        media.arrive(|at| Arrival {
            ssrc: header.ssrc,
            sequence: header.sequence,
            timestamp: header.timestamp,
            at,
            payload,
            silence,
            extension,
            own: false,
        });
    }
}

#[cfg(test)]
mod tests {
    use std::collections::HashSet;
    use std::time::Duration;

    use proptest::collection::vec;
    use proptest::prelude::*;

    use super::*;
    use crate::ids::{ChannelId, GuildId, Handle, ShardId};
    use crate::voice::crypto::{Cipher, Mode};
    use crate::voice::receiver::Receiver;
    use crate::voice::sink::tests::Fixture;
    use crate::voice::testkit::sfu::{self, Sealing};
    use crate::voice::types::{
        Overflow, ReceiveFormat, ReceiveOptions, ReceiveStall, ReceiveTuning,
    };

    const KEY: [u8; 32] = [9; 32];
    const BOT_SSRC: u32 = 77;
    const ALICE_SSRC: u32 = 1_001;
    const BOB_SSRC: u32 = 1_002;

    struct Rig {
        fixture: Fixture,
        info: Arc<Info>,
        intake: Intake,
        cipher: Cipher,
    }

    impl Rig {
        fn new() -> Self {
            let fixture = Fixture::new();
            let guild = GuildId::from(10);
            let media = Arc::new(Media::new(
                guild,
                UserId::from(100),
                ChannelId::from(900),
                Arc::clone(&fixture.inbox),
                Duration::from_secs(2),
                ReceiveTuning::default(),
            ));
            let packets = ReceiveOptions {
                format: ReceiveFormat::Packets,
                users: None,
                exclude: HashSet::new(),
                include_self: false,
                buffer: Duration::from_secs(10),
                jitter: Duration::ZERO,
                markers: false,
                overflow: Overflow::DropOldest,
                stall: ReceiveStall::Wait,
            };
            let receiver = Receiver::new(Handle::from(1), packets, Arc::clone(&fixture.inbox));
            assert!(media.add_receiver(&Arc::new(receiver.unwrap())));
            let socket = Arc::new(UdpSocket::bind("127.0.0.1:0").unwrap());
            media.install(Some(Arc::new(Transport {
                generation: 1,
                socket: Arc::clone(&socket),
                ssrc: BOT_SSRC,
                cipher: Cipher::new(Mode::Aes256GcmRtpSize, &KEY),
            })));
            let info = Arc::new(Info::new(guild, ShardId::from(0)));
            Self {
                fixture,
                info: Arc::clone(&info),
                intake: Intake::new(media, info, socket, UserId::from(100), 64),
                cipher: Cipher::new(Mode::Aes256GcmRtpSize, &KEY),
            }
        }

        fn map(&self, ssrc: u32) {
            let owner = (UserId::from(u64::from(ssrc)), Instant::now());
            self.info.ssrcs.lock().insert(ssrc, owner);
        }

        fn send(&mut self, nonce: u32, ssrc: u32) -> Vec<u32> {
            let sealing = Sealing::default();
            let mut packet = sfu::seal(&self.cipher, nonce, ssrc, 0, 0, &SILENCE_FRAME, &sealing);
            self.intake.receive(Kind::Rtp, &mut packet);
            let mut windows: Vec<u32> = self.intake.windows.keys().copied().collect();
            windows.sort_unstable();
            windows
        }
    }

    #[test]
    fn the_intake_forgets_departed_ssrcs_once_unmapped_windows_pile_up() {
        let mut rig = Rig::new();
        rig.map(ALICE_SSRC);
        rig.map(BOB_SSRC);
        let unmapped: Vec<u32> = (2_000..).take(SPARE_WINDOWS - 1).collect();

        rig.send(0, ALICE_SSRC);
        rig.send(1, BOB_SSRC);
        rig.info.ssrcs.lock().remove(&ALICE_SSRC);
        let mut piled = Vec::new();
        for (nonce, &ssrc) in (2..).zip(&unmapped) {
            piled = rig.send(nonce, ssrc);
        }
        let swept = rig.send(100, 1_003);

        assert_eq!(piled, [vec![ALICE_SSRC, BOB_SSRC], unmapped].concat());
        assert_eq!(swept, [BOB_SSRC, 1_003]);
    }

    #[test]
    fn rtcp_reports_reach_python_with_the_user_of_each_ssrc() {
        let mut rig = Rig::new();
        rig.map(ALICE_SSRC);
        let blocks = [
            [BOT_SSRC, 1, 0, 0, 0, 0],
            [ALICE_SSRC, 2, 0, 0, 0, 0],
            [5, 3, 0, 0, 0, 0],
        ];
        let report = rtcp::tests::receiver_report(1, &blocks);
        let (header, body) = report.split_at(rtcp::HEADER_LEN);
        let mut sealed = Vec::new();
        rig.cipher.seal(0, header, body, &mut sealed).unwrap();
        let mut forged = sealed.clone();
        forged[rtcp::HEADER_LEN] ^= 1;

        rig.intake.receive(Kind::Rtcp, &mut forged);
        rig.intake.receive(Kind::Rtcp, &mut sealed);

        let delivered: Vec<Vec<(u32, Option<UserId>)>> = rig
            .fixture
            .inbox
            .drain()
            .into_iter()
            .filter_map(|delivery| match delivery {
                Delivery::VoiceRtcp { packets, .. } => Some(
                    packets[0]
                        .reports
                        .iter()
                        .map(|report| (report.ssrc, report.user))
                        .collect(),
                ),
                _ => None,
            })
            .collect();
        let alice = UserId::from(u64::from(ALICE_SSRC));
        assert_eq!(
            delivered,
            [vec![
                (BOT_SSRC, Some(UserId::from(100))),
                (ALICE_SSRC, Some(alice)),
                (5, None)
            ]]
        );
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(48))]
        #[test]
        fn sealed_packets_with_arbitrary_fields_never_panic_the_intake(
            packets in vec(
                (
                    0..3_usize,
                    any::<u16>(),
                    any::<u32>(),
                    proptest::option::of(vec(any::<u8>(), 0..12)),
                    0..4_u8,
                    any::<bool>(),
                    vec(any::<u8>(), 0..48),
                ),
                1..48,
            ),
        ) {
            let mut rig = Rig::new();
            rig.map(ALICE_SSRC);
            let ssrcs = [ALICE_SSRC, BOT_SSRC, 0xFFFF_FFFF];
            for (nonce, (which, sequence, timestamp, extension, padding, marker, payload)) in (0..).zip(packets) {
                let sealing = Sealing { extension, padding, csrcs: padding, marker };
                let mut packet = sfu::seal(&rig.cipher, nonce, ssrcs[which], sequence, timestamp, &payload, &sealing);
                rig.intake.open(&mut packet);
            }
        }
    }
}
