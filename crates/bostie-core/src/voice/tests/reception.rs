use super::*;
use crate::voice::testkit::sfu::Sealing;

fn frames(items: &[Item]) -> Vec<(Option<UserId>, Bytes)> {
    items
        .iter()
        .filter_map(|item| match item {
            Item::Frame { user, data, .. } => Some((*user, data.clone())),
            _ => None,
        })
        .collect()
}

fn spoken(peer: u64, packets: &[Bytes]) -> Vec<(Option<UserId>, Bytes)> {
    packets
        .iter()
        .map(|packet| (Some(user(peer)), packet.clone()))
        .collect()
}

impl Harness {
    async fn speaking(&mut self, peer: u64, ssrc: u32) {
        self.server.gateway.send(
            op::SPEAKING,
            json!({"user_id": peer.to_string(), "ssrc": ssrc, "speaking": 1}),
        );
        self.speaking_from(ssrc).await;
    }

    fn say(&self, ssrc: u32, sequence: u16, payload: &[u8]) {
        self.server.sfu.send(
            ssrc,
            sequence,
            u32::from(sequence) * 960,
            payload,
            &Sealing::default(),
        );
    }

    async fn hear(&self, receiver: &Receiver, count: usize) -> Vec<Item> {
        let deadline = Instant::now() + WAIT;
        let mut items = Vec::new();
        while frames(&items).len() < count {
            assert!(
                Instant::now() < deadline,
                "only {} of {count} frames arrived: {items:?}",
                frames(&items).len()
            );
            self.tick(1);
            if let ReceiveRead::Items(batch) = receiver.read(256).unwrap() {
                items.extend(batch.into_iter().map(|(_, item)| item));
            }
            sleep(Duration::from_millis(2)).await;
        }
        items
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_reassigned_ssrc_keeps_its_new_owner_when_op_5_repeats() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let receiver = harness.receiver(receiving(ReceiveFormat::Opus));
    let packets = opus(1);
    harness.speaking(EARLY_PEER, 70).await;
    harness.tick(1);

    harness.speaking(LATE_PEER, 70).await;
    harness.say(70, 0, &packets[0]);
    harness
        .settle(|harness| harness.media().arrivals.lock().len() == 1)
        .await;
    harness.speaking(LATE_PEER, 70).await;
    let heard = harness.hear(&receiver, 1).await;

    assert_eq!(frames(&heard), spoken(LATE_PEER, &packets));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_user_who_rejoins_with_a_new_ssrc_keeps_their_stream() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let receiver = harness.receiver(receiving(ReceiveFormat::Opus));
    let packets = opus(6);
    harness.speaking(LATE_PEER, 55).await;
    for (sequence, packet) in (0..3).zip(&packets) {
        harness.say(55, sequence, packet);
    }
    let first = harness.hear(&receiver, 3).await;

    harness.server.gateway.send(
        op::CLIENT_DISCONNECT,
        json!({"user_id": LATE_PEER.to_string()}),
    );
    harness
        .wait_for(|delivery| matches!(delivery, Delivery::VoiceClientLeft { .. }).then_some(()))
        .await;
    harness.server.gateway.send(
        op::CLIENTS_CONNECT,
        json!({"user_ids": [LATE_PEER.to_string()]}),
    );
    harness.speaking(LATE_PEER, 56).await;
    for (sequence, packet) in (40_000..).zip(&packets[3..]) {
        harness.say(56, sequence, packet);
    }
    let second = harness.hear(&receiver, 3).await;

    assert_eq!(
        frames(&[first, second].concat()),
        spoken(LATE_PEER, &packets)
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn receiving_continues_across_a_resume_and_a_server_change() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let receiver = harness.receiver(receiving(ReceiveFormat::Opus));
    let ending = harness.receiver(ReceiveOptions {
        stall: ReceiveStall::End,
        ..receiving(ReceiveFormat::Opus)
    });
    let packets = opus(6);
    harness.speaking(EARLY_PEER, 70).await;
    harness.say(70, 0, &packets[0]);
    harness.say(70, 1, &packets[1]);
    let mut heard = harness.hear(&receiver, 2).await;

    harness.server.gateway.drop_connection();
    harness.until(VoiceStatus::Resuming).await;
    harness.until(VoiceStatus::Ready).await;
    harness.say(70, 2, &packets[2]);
    harness.say(70, 3, &packets[3]);
    heard.extend(harness.hear(&receiver, 2).await);
    let open_after_the_resume = ending.ended();
    harness.fresh_server().await;
    harness.until(VoiceStatus::Reconnecting).await;
    harness.until(VoiceStatus::Ready).await;
    harness.speaking(EARLY_PEER, 70).await;
    harness.say(70, 4, &packets[4]);
    harness.say(70, 5, &packets[5]);
    heard.extend(harness.hear(&receiver, 2).await);
    harness.server.collect().await;

    assert_eq!(frames(&heard), spoken(EARLY_PEER, &packets));
    assert_eq!(harness.server.count(op::RESUME), 1);
    assert_eq!(receiver.ended(), None);
    assert_eq!(open_after_the_resume, None);
    assert_eq!(ending.ended(), Some(Ended::Reconnecting));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn include_self_hears_the_bots_own_audio() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let receiver = harness.receiver(ReceiveOptions {
        users: Some(HashSet::new()),
        include_self: true,
        ..receiving(ReceiveFormat::Opus)
    });
    let sink = harness.opus_sink();

    let sent = harness.play(&sink, 3);
    let heard = harness.hear(&receiver, 3).await;

    let open = harness.media().open_receivers();
    receiver.close();
    harness.tick(1);

    assert_eq!(frames(&heard)[..3], spoken(u64::from(bot()), &sent));
    assert_eq!(open, 1);
    assert_eq!(harness.media().open_receivers(), 0);
    assert_eq!(
        harness.count(|delivery| matches!(delivery, Delivery::VoiceSpeech { .. })),
        0
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_peer_that_never_decrypts_makes_the_bot_ask_for_a_new_group() {
    let mut tuning = tuning();
    tuning.connection.receive = ReceiveTuning {
        decrypt_tolerance: 3,
        dave_grace: Duration::ZERO,
        ..ReceiveTuning::default()
    };
    let mut harness = Harness::build(|config| config.dave_protocol_version = 1, None, tuning).await;
    let _call = harness.talking_with(&[LATE_PEER]).await;
    harness.speaking(LATE_PEER, 55).await;
    let _receiver = harness.receiver(receiving(ReceiveFormat::Opus));

    let deadline = Instant::now() + WAIT;
    let mut sequence = 0;
    while harness.server.count(op::DAVE_MLS_INVALID_COMMIT_WELCOME) == 0 {
        assert!(Instant::now() < deadline, "no recovery was asked for");
        harness.say(55, sequence, &[0x78, 0x01, 0x02, 0x03, 0x04, 0xFA, 0xFA]);
        sequence += 1;
        harness.tick(1);
        harness.server.collect().await;
    }

    assert_eq!(harness.server.count(op::DAVE_MLS_INVALID_COMMIT_WELCOME), 1);
    assert!(
        harness
            .statuses()
            .iter()
            .all(|(status, ..)| *status != VoiceStatus::Disconnected)
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_full_receiver_never_slows_sending() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    harness.speaking(EARLY_PEER, 70).await;
    let _receiver = harness.receiver(ReceiveOptions {
        buffer: FRAME,
        ..receiving(ReceiveFormat::Opus)
    });
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 40)).unwrap();
    let packets = opus(30);

    for (sequence, packet) in (0..).zip(&packets) {
        harness.say(70, sequence, packet);
        sleep(Duration::from_millis(2)).await;
        harness.tick(1);
    }

    assert_eq!(harness.server.sfu.wait_packets(30, WAIT).len(), 30);
    assert_eq!(sink.stats().unwrap().held_ticks, 0);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_move_to_another_channel_ends_the_receivers() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    harness.speaking(EARLY_PEER, 70).await;
    let receiver = harness.receiver(receiving(ReceiveFormat::Opus));
    let packets = opus(2);
    harness.say(70, 0, &packets[0]);
    harness.say(70, 1, &packets[1]);
    let delivered = || receiver.stats().unwrap().delivered;
    let deadline = Instant::now() + WAIT;
    while delivered() < 2 {
        assert!(Instant::now() < deadline, "the frames never arrived");
        harness.tick(1);
        sleep(Duration::from_millis(2)).await;
    }

    harness.state(Some(OTHER_CHANNEL));
    harness.wait_for(moved).await;
    harness.until(VoiceStatus::Reconnecting).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 2)).unwrap();
    harness.tick(2);

    let ReceiveRead::Items(items) = receiver.read(256).unwrap() else {
        panic!("the queued frames were lost");
    };
    let items: Vec<Item> = items.into_iter().map(|(_, item)| item).collect();
    assert_eq!(frames(&items), spoken(EARLY_PEER, &packets));
    assert_eq!(
        receiver.read(256).unwrap(),
        ReceiveRead::Ended(Ended::Moved)
    );
    assert_eq!(harness.server.sfu.wait_packets(2, WAIT).len(), 2);
}
