use super::*;
use crate::voice::testkit::gateway::HelloAt;

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_plain_call_identifies_selects_aes_and_speaks_with_its_ssrc() {
    let mut harness = Harness::start().await;
    harness.connect();
    harness.announce().await;

    let identify = harness.server.expect_op(op::IDENTIFY).await;
    let select = harness.server.expect_op(op::SELECT_PROTOCOL).await;
    let speaking = harness.server.expect_op(op::SPEAKING).await;
    let statuses = [harness.status().await, harness.status().await];

    let join = &harness.recorder.voice_states()[0];
    assert_eq!(join["op"].as_u64(), Some(4));
    assert_eq!(
        join["d"]["guild_id"].as_str(),
        Some(guild().to_string().as_str())
    );
    assert_eq!(
        join["d"]["channel_id"].as_str(),
        Some(CHANNEL.to_string().as_str())
    );
    assert_eq!(
        identify["server_id"].as_str(),
        Some(guild().to_string().as_str())
    );
    assert_eq!(
        identify["user_id"].as_str(),
        Some(bot().to_string().as_str())
    );
    assert_eq!(
        identify["session_id"].as_str(),
        Some(Config::default().session_id.as_str())
    );
    assert_eq!(identify["token"].as_str(), Some("token-1"));
    assert_eq!(identify["max_dave_protocol_version"].as_u64(), Some(1));
    assert_eq!(select["data"]["mode"].as_str(), Some(AES));
    assert_eq!(speaking["speaking"].as_u64(), Some(1));
    assert_eq!(
        speaking["ssrc"].as_u64(),
        Some(u64::from(Config::default().ssrc))
    );
    assert_eq!(statuses[0].0, VoiceStatus::Connecting);
    assert_eq!(statuses[0].3, "voice_events");
    assert_eq!(statuses[1].0, VoiceStatus::Ready);

    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES)).unwrap();
    harness.tick(1);
    let packets = harness.server.sfu.wait_packets(1, WAIT);
    assert_eq!(packets.len(), 1);
    assert_eq!(packets[0].header.ssrc, Config::default().ssrc);
    assert_eq!(harness.voice.dave(guild()).unwrap().protocol_version, 0);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn only_xchacha_offered_selects_xchacha_and_its_packets_decrypt() {
    let mut harness = Harness::with(|config| {
        config.modes = vec![XCHACHA.to_owned()];
        config.hello = HelloAt::AfterReady;
    })
    .await;
    harness.connected().await;

    let select = harness.server.expect_op(op::SELECT_PROTOCOL).await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 2)).unwrap();
    harness.tick(2);

    assert_eq!(select["data"]["mode"].as_str(), Some(XCHACHA));
    assert_eq!(harness.server.sfu.wait_packets(2, WAIT).len(), 2);
    assert_eq!(harness.server.sfu.rejected(), 0);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn no_supported_mode_ends_with_a_protocol_error_and_leaves() {
    let mut harness =
        Harness::with(|config| config.modes = vec!["xsalsa20_poly1305_lite".to_owned()]).await;
    harness.connect();
    harness.announce().await;

    let (reason, _, detail) = harness.ended().await;

    assert_eq!(reason, DisconnectReason::ProtocolError);
    assert!(detail.contains("no supported encryption mode"), "{detail}");
    assert!(detail.contains("xsalsa20_poly1305_lite"), "{detail}");
    assert_eq!(harness.leaves(), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_unknown_opcode_is_ignored() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    harness.server.gateway.send(20, json!({"any": [1, 2]}));
    harness.server.gateway.send(
        op::SPEAKING,
        json!({"user_id": "9", "ssrc": 90, "speaking": 1}),
    );
    harness.speaking_from(90).await;

    assert_eq!(harness.statuses().len(), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_malformed_frame_after_ready_is_skipped_and_never_replayed() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    harness.server.gateway.send(
        op::SPEAKING,
        json!({"user_id": "9", "ssrc": 90, "speaking": 1}),
    );
    harness
        .server
        .gateway
        .send(op::SPEAKING, json!({"user_id": "not a snowflake"}));
    let speaking = json!({"user_id": "9", "ssrc": 92, "speaking": 1});
    let unsequenced = json!({"op": op::SPEAKING, "d": speaking});
    harness
        .server
        .gateway
        .send_raw(Message::text(unsequenced.to_string()));
    harness.speaking_from(90).await;
    harness.speaking_from(92).await;
    harness.server.gateway.drop_connection();
    let resume = harness.server.expect_op(op::RESUME).await;
    harness.until(VoiceStatus::Ready).await;
    harness.server.gateway.send(
        op::SPEAKING,
        json!({"user_id": "9", "ssrc": 91, "speaking": 1}),
    );
    harness.speaking_from(91).await;

    assert_eq!(resume["seq_ack"].as_u64(), Some(4));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_truncated_binary_frame_before_ready_fails_the_attempt_and_rejoins() {
    let mut harness = Harness::start().await;
    harness.server.sfu.ignore_discoveries(1);
    harness.connect();
    harness.announce().await;
    harness.server.expect_op(op::IDENTIFY).await;

    harness
        .server
        .gateway
        .send_raw(Message::binary(vec![0_u8, 1]));
    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;

    let statuses: Vec<VoiceStatus> = harness.statuses().iter().map(|state| state.0).collect();
    assert_eq!(statuses, [VoiceStatus::Connecting, VoiceStatus::Ready]);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_session_description_with_another_mode_ends_with_a_protocol_error() {
    let mut harness = Harness::with(|config| config.session_mode = Some(XCHACHA.to_owned())).await;
    harness.connect();
    harness.announce().await;

    let (reason, _, detail) = harness.ended().await;

    assert_eq!(reason, DisconnectReason::ProtocolError);
    assert!(detail.contains(XCHACHA) && detail.contains(AES), "{detail}");
    assert_eq!(harness.leaves(), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn speaking_updates_are_coalesced_and_the_last_flags_win() {
    let mut harness =
        Harness::tuned(|tuning| tuning.speaking_coalesce = Duration::from_millis(200)).await;
    harness.connected().await;
    harness.server.expect_op(op::SPEAKING).await;

    for index in 0..30_u8 {
        harness.voice.set_speaking(guild(), index % 3 + 1).unwrap();
    }
    harness
        .server
        .expect(|message| match message {
            ClientMessage::Json { op, d } if *op == u64::from(op::SPEAKING) => {
                d["speaking"].as_u64() == Some(3)
            }
            _ => false,
        })
        .await;

    let updates = harness.server.json(op::SPEAKING);
    assert!(updates.len() <= 3, "{updates:?}");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn each_change_of_the_mute_suppress_and_deaf_flags_is_reported_once() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let flags = |delivery: &Delivery| match delivery {
        Delivery::VoiceSelf {
            self_mute,
            self_deaf,
            mute,
            suppress,
            ..
        } => Some([*self_mute, *self_deaf, *mute, *suppress]),
        _ => None,
    };
    assert_eq!(harness.wait_for(flags).await, [false; 4]);

    harness.offer_state(guild(), Some(CHANNEL), r#","mute":true"#);
    assert_eq!(harness.wait_for(flags).await, [false, false, true, false]);
    harness.offer_state(guild(), Some(CHANNEL), r#","mute":true,"suppress":true"#);
    assert_eq!(harness.wait_for(flags).await, [false, false, true, true]);
    let deafened = r#","mute":true,"suppress":true,"deaf":true"#;
    harness.offer_state(guild(), Some(CHANNEL), deafened);
    let deaf = harness
        .wait_for(|delivery| match delivery {
            Delivery::VoiceDeaf { deaf, .. } => Some(*deaf),
            _ => None,
        })
        .await;
    harness.offer_state(guild(), Some(CHANNEL), deafened);
    harness.server.gateway.send(
        op::SPEAKING,
        json!({"user_id": "9", "ssrc": 90, "speaking": 1}),
    );
    harness.speaking_from(90).await;

    assert!(deaf);
    assert_eq!(
        harness.count(|delivery| matches!(delivery, Delivery::VoiceSelf { .. })),
        3
    );
    assert_eq!(
        harness.count(|delivery| matches!(delivery, Delivery::VoiceDeaf { .. })),
        1
    );
}

fn early_clients(count: usize) -> Vec<Outgoing> {
    (0..count)
        .map(|index| Outgoing::Json {
            op: op::CLIENTS_CONNECT,
            d: json!({"user_ids": [(100 + index).to_string()]}),
        })
        .collect()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn messages_before_the_session_description_are_kept_and_applied_in_order() {
    let mut harness = Harness::with(|config| config.early = early_clients(8)).await;
    harness.connected().await;

    let users: Vec<Vec<UserId>> = harness
        .deliveries
        .iter()
        .filter_map(|delivery| match delivery {
            Delivery::VoiceClients { users, .. } => Some(users.clone()),
            _ => None,
        })
        .collect();

    let expected: Vec<Vec<UserId>> = (100..108).map(|id| vec![user(id)]).collect();
    assert_eq!(users, expected);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn one_message_too_many_before_the_session_description_fails_the_attempt() {
    let mut harness = Harness::with(|config| config.early = early_clients(9)).await;
    harness.connect();
    harness.announce().await;

    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;

    assert_eq!(harness.statuses().len(), 1);
}
