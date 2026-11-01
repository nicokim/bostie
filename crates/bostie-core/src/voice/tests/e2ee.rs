use super::*;
use crate::voice::dave::RECOVERY_LIMIT;

fn transition_readies(server: &Server) -> Vec<u16> {
    server
        .dave_outbound()
        .into_iter()
        .filter_map(|message| match message {
            Outbound::TransitionReady(id) => Some(id),
            _ => None,
        })
        .collect()
}

impl Harness {
    async fn rejoined(&mut self, call: &mut Call, members: usize, executed: usize) {
        self.pump(call, |harness, call| {
            call.fake.members().len() == members
                && harness.executed() > executed
                && harness.gate() == SendMode::Encrypt
        })
        .await;
    }

    fn heard(&self, call: &mut Call, peer: u64, frames: usize) -> Vec<Option<Vec<u8>>> {
        let packets = self.server.sfu.packets();
        packets[packets.len() - frames..]
            .iter()
            .map(|packet| hear(call, peer, &packet.payload))
            .collect()
    }

    fn assert_heard(&self, call: &mut Call, peers: &[u64]) {
        let sink = self.opus_sink();
        let sent = self.play(&sink, 2);
        for &peer in peers {
            let heard = self.heard(call, peer, 2);
            let expected: Vec<Option<Vec<u8>>> =
                sent.iter().map(|packet| Some(packet.to_vec())).collect();
            assert_eq!(heard, expected, "{peer}");
        }
        sink.close();
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_peer_joining_a_lone_bot_forms_a_group_and_hears_the_exact_opus_bytes() {
    let mut harness = Harness::dave().await;
    let mut call = dave_call(CHANNEL, &[]);
    harness.in_call(&mut call).await;
    let alone = harness.gate();
    call.fake.hold_transitions = true;

    call.join(user(LATE_PEER));
    harness
        .pump(&mut call, |harness, _| {
            transition_readies(&harness.server).len() == 1
        })
        .await;
    let forming = harness.gate();
    let sink = harness.opus_sink();
    let packets = opus(2);
    for packet in &packets {
        sink.write(packet).unwrap();
    }
    harness.tick(2);
    let released = call.fake.release_transitions();
    call.enqueue(released);
    harness.encrypting(&mut call).await;
    let executed = harness.wait_for(dave_states).await;
    harness.tick(3);
    let arrived = harness.server.sfu.wait_packets(3, WAIT);

    assert_eq!((alone, forming), (SendMode::Discard, SendMode::Hold));
    assert_eq!(arrived.len(), 3);
    for (packet, sent) in arrived.iter().zip(&packets) {
        assert_eq!(
            hear(&mut call, LATE_PEER, &packet.payload),
            Some(sent.to_vec())
        );
    }
    assert_eq!(executed.0, 1);
    assert_eq!(executed.1, Some(1));
    assert_ne!(executed.2, 0);
    assert!(harness.voice.dave(guild()).unwrap().privacy_code.is_some());
    assert_eq!(
        harness
            .voice
            .verification_code(guild(), user(LATE_PEER))
            .unwrap(),
        dave::verification_code(call.dave(user(LATE_PEER)).fingerprint_pair(bot()).unwrap())
            .unwrap()
    );
    assert_eq!(call.fake.leaves()[0].1, bot());
    let statuses: Vec<VoiceStatus> = harness.statuses().iter().map(|state| state.0).collect();
    assert_eq!(statuses, [VoiceStatus::Connecting, VoiceStatus::Ready]);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn too_many_dave_recoveries_end_the_connection() {
    let mut harness = Harness::dave().await;
    let mut call = dave_call(CHANNEL, &[]);
    harness.in_call(&mut call).await;

    for transition_id in 1..=u16::try_from(RECOVERY_LIMIT).unwrap() + 1 {
        harness.server.send_dave(DaveOp::Welcome {
            transition_id,
            welcome: Bytes::from_static(b"not a welcome"),
        });
    }
    let (reason, _, _) = harness.ended().await;

    assert_eq!(reason, DisconnectReason::DaveFailed);
    assert_eq!(harness.leaves(), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn after_a_recovery_media_waits_for_the_re_add_and_never_uses_the_old_key() {
    let mut harness = Harness::dave().await;
    let mut call = harness.talking_with(&[LATE_PEER]).await;
    let readies = transition_readies(&harness.server).len();
    call.fake.hold_transitions = true;
    let sink = harness.opus_sink();
    let before = harness.server.sfu.packets().len();

    harness.server.send_dave(DaveOp::AnnounceCommit {
        transition_id: 9,
        commit: Bytes::from_static(b"a commit"),
    });
    harness
        .pump(&mut call, |harness, _| {
            transition_readies(&harness.server).len() > readies
        })
        .await;
    let rejoining = harness.gate();
    let packets = opus(2);
    for packet in &packets {
        sink.write(packet).unwrap();
    }
    harness.tick(2);
    let released = call.fake.release_transitions();
    call.enqueue(released);
    harness.encrypting(&mut call).await;
    harness.tick(3);
    let arrived = harness.server.sfu.wait_packets(before + 3, WAIT);

    assert_eq!(invalid_commit_welcomes(&harness.server), [9]);
    assert_eq!(rejoining, SendMode::Hold);
    assert_eq!(arrived.len(), before + 3);
    for (packet, sent) in arrived[before..].iter().zip(&packets) {
        assert_eq!(
            hear(&mut call, LATE_PEER, &packet.payload),
            Some(sent.to_vec())
        );
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_transition_split_by_two_resumes_completes_from_the_replays() {
    let mut harness = Harness::dave().await;
    let mut call = harness.talking_with(&[LATE_PEER]).await;
    harness.wait_for(dave_states).await;
    call.fake.hold_commits = true;
    call.fake.hold_transitions = true;
    call.join(user(THIRD_PEER));
    harness
        .pump(&mut call, |_, call| call.fake.awaiting().is_empty())
        .await;

    harness.server.gateway.drop_connection();
    call.fake.hold_commits = false;
    let settled = call.fake.settle();
    call.enqueue(settled);
    harness
        .pump(&mut call, |harness, _| {
            transition_readies(&harness.server).len() == 2
        })
        .await;
    harness.server.gateway.drop_connection();
    let held = call.fake.release_transitions();
    call.enqueue(held);
    harness
        .pump(&mut call, |harness, _| harness.executed() == 2)
        .await;
    let executed = harness.wait_for(dave_states).await;
    harness.server.collect().await;

    assert_eq!(harness.server.count(op::RESUME), 2);
    assert_eq!(executed.1, Some(2));
    assert!(invalid_commit_welcomes(&harness.server).is_empty());
    harness.assert_heard(&mut call, &[LATE_PEER, THIRD_PEER]);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_move_builds_a_new_dave_group_and_never_uses_the_old_one() {
    let mut harness = Harness::dave().await;
    let mut old = harness.talking_with(&[LATE_PEER]).await;

    harness.state(Some(OTHER_CHANNEL));
    harness.until(VoiceStatus::Reconnecting).await;
    harness.fresh_server().await;
    let mut call = dave_call(OTHER_CHANNEL, &[THIRD_PEER]);
    harness.server.expect_op(op::IDENTIFY).await;
    call.join_remote(bot());
    harness.encrypting(&mut call).await;

    harness.assert_heard(&mut call, &[THIRD_PEER]);
    assert_eq!(harness.heard(&mut old, LATE_PEER, 2), [None, None]);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn clients_joining_and_leaving_update_members_ssrcs_and_speaking() {
    let mut harness = Harness::dave().await;
    let mut call = dave_call(CHANNEL, &[]);
    harness.in_call(&mut call).await;
    let speaking = harness.server.count(op::SPEAKING);

    call.join(user(LATE_PEER));
    harness.encrypting(&mut call).await;
    harness.server.gateway.send(
        op::SPEAKING,
        json!({"user_id": LATE_PEER.to_string(), "ssrc": 55, "speaking": 1}),
    );
    harness.speaking_from(55).await;
    let joined = harness
        .wait_for(|delivery| match delivery {
            Delivery::VoiceClients { users, .. } => Some(users.clone()),
            _ => None,
        })
        .await;
    let ssrcs = |harness: &Harness| harness.voice.ssrcs(guild()).unwrap();
    assert_eq!(ssrcs(&harness), [(55, user(LATE_PEER))]);
    harness
        .pump(&mut call, |harness, _| {
            harness.server.count(op::SPEAKING) > speaking
        })
        .await;

    call.leave(user(LATE_PEER));
    harness
        .pump(&mut call, |harness, _| harness.gate() == SendMode::Discard)
        .await;
    let left = harness
        .wait_for(|delivery| match delivery {
            Delivery::VoiceClientLeft { user, .. } => Some(*user),
            _ => None,
        })
        .await;

    let sink = harness.opus_sink();
    for packet in opus(3) {
        sink.write(&packet).unwrap();
    }
    harness.tick(3);
    let drained = queued_frames(&sink);
    sink.close();
    call.join(user(THIRD_PEER));
    harness.encrypting(&mut call).await;
    harness.assert_heard(&mut call, &[THIRD_PEER]);

    assert_eq!(joined, [user(LATE_PEER)]);
    assert_eq!(left, user(LATE_PEER));
    assert!(ssrcs(&harness).is_empty());
    assert_eq!(drained, 0);
    assert_eq!(harness.server.sfu.packets().len(), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn malformed_dave_messages_never_break_the_connection() {
    let mut harness = Harness::dave().await;
    let mut call = harness.talking_with(&[LATE_PEER]).await;
    let executed = harness.executed();
    let commits = harness.server.count(op::DAVE_MLS_COMMIT_WELCOME);

    let junk = Bytes::from_static(b"not mls at all");
    harness
        .server
        .send_dave(DaveOp::ExternalSender(junk.clone()));
    for operation in [ProposalsOp::Append, ProposalsOp::Revoke] {
        harness.server.send_dave(DaveOp::Proposals {
            operation,
            data: junk.clone(),
        });
    }
    harness.server.send_dave(DaveOp::AnnounceCommit {
        transition_id: 4,
        commit: Bytes::from_static(b"\x00\x01"),
    });
    harness
        .pump(&mut call, |harness, _| {
            invalid_commit_welcomes(&harness.server) == [4]
        })
        .await;
    harness
        .pump(&mut call, |harness, _| {
            harness.server.count(op::DAVE_MLS_KEY_PACKAGE) == 2
        })
        .await;
    harness.rejoined(&mut call, 2, executed).await;

    assert_eq!(invalid_commit_welcomes(&harness.server), [4]);
    assert_eq!(harness.server.count(op::DAVE_MLS_COMMIT_WELCOME), commits);
    assert_eq!(harness.statuses().len(), 2);
    harness.assert_heard(&mut call, &[LATE_PEER]);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_busy_dave_lock_holds_only_its_own_lane_and_never_blocks_readers() {
    let mut harness = Harness::dave().await;
    let _call = harness.talking_with(&[LATE_PEER]).await;
    let second = harness.second_guild().await;
    let other = harness
        .voice
        .open_sink(
            GuildId::from(SECOND_GUILD),
            SinkOptions {
                format: pcm(),
                buffer: Duration::from_secs(10),
                volume: 1.0,
                stall: StallPolicy::Hold,
            },
        )
        .unwrap();
    other.write(&tone(FRAME_SAMPLES * 5)).unwrap();
    let own = harness.sink(pcm(), StallPolicy::Hold);
    own.write(&tone(FRAME_SAMPLES * 5)).unwrap();
    let media = harness.media();
    let busy = media.dave.lock();
    let pacer = Arc::clone(&harness.pacer);
    let (ticked, on_ticked) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        pacer.tick(5);
        ticked.send(()).unwrap();
    });
    on_ticked
        .recv_timeout(WAIT)
        .expect("the ticks waited for the DAVE lock");
    let dave = harness.voice.dave(guild()).unwrap();
    drop(busy);

    assert_eq!(own.stats().unwrap().held_ticks, 5);
    assert_eq!(dave.protocol_version, 1);
    assert_eq!(queued_frames(&own), 5 * 960);
    assert_eq!(second.sfu.wait_packets(5, WAIT).len(), 5);
}
