use std::collections::HashMap;
use std::net::UdpSocket;
use std::panic::{self, AssertUnwindSafe};
use std::sync::Arc;
use std::time::Instant;

use bytes::Bytes;
use tracing::error;

use crate::voice::connection::Info;
use crate::voice::crypto::ReplayWindow;
use crate::voice::media::{Arrival, Media, Transport};
use crate::voice::types::SILENCE_FRAME;
use crate::voice::{Throttle, panic_message};

pub(super) const SPARE_WINDOWS: usize = 8;

pub struct Intake {
    media: Arc<Media>,
    info: Arc<Info>,
    socket: Arc<UdpSocket>,
    transport: Option<Arc<Transport>>,
    pub(super) windows: HashMap<u32, ReplayWindow>,
    replay_window: u32,
    panicked: Throttle,
}

impl Intake {
    pub fn new(
        media: Arc<Media>,
        info: Arc<Info>,
        socket: Arc<UdpSocket>,
        replay_window: u32,
    ) -> Self {
        Self {
            media,
            info,
            socket,
            transport: None,
            windows: HashMap::new(),
            replay_window,
            panicked: Throttle::default(),
        }
    }

    pub fn rtp(&mut self, packet: &mut [u8]) {
        if let Err(payload) = panic::catch_unwind(AssertUnwindSafe(|| self.open(packet)))
            && self.panicked.allows(Instant::now())
        {
            error!(
                panic = panic_message(payload.as_ref()),
                "opening a received voice packet panicked and it was dropped"
            );
        }
    }

    pub(super) fn open(&mut self, packet: &mut [u8]) {
        let media = &self.media;
        if !media.speech_activity() && media.open_receivers() == 0 {
            return;
        }
        let at = Instant::now();
        if self.transport.is_none() {
            let installed = media.transport.lock().clone();
            self.transport =
                installed.filter(|transport| Arc::ptr_eq(&transport.socket, &self.socket));
        }
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
        media.arrive(Arrival {
            ssrc: header.ssrc,
            sequence: header.sequence,
            timestamp: header.timestamp,
            at,
            payload,
            silence: opened.payload == SILENCE_FRAME,
            extension,
            own: false,
        });
    }
}
