use super::*;

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_move_by_someone_else_reconnects_and_keeps_held_audio_for_the_new_server() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 20)).unwrap();

    harness.state(Some(OTHER_CHANNEL));
    let moved = harness.wait_for(moved).await;
    let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
    harness.tick(5);
    let kept = queued_frames(&sink);
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;
    harness.tick(1);
    harness.server.collect().await;

    assert_eq!(moved, (CHANNEL, OTHER_CHANNEL, false));
    assert_eq!(detail, "moving");
    assert_eq!(harness.server.count(op::IDENTIFY), 2);
    assert_eq!(harness.server.count(op::RESUME), 0);
    assert!(!sink.is_closed());
    assert_eq!(kept, 20 * 960);
    assert_eq!(harness.server.sfu.wait_packets(1, WAIT).len(), 1);
    assert_eq!(queued_frames(&sink), 19 * 960);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_server_first_move_joins_the_new_channel_group() {
    let mut harness = Harness::with(|config| config.dave_protocol_version = 1).await;
    harness.connected().await;
    harness.server.expect_op(op::IDENTIFY).await;
    harness.server.sfu.ignore_discoveries(1);

    harness.fresh_server().await;
    harness.server.expect_op(op::IDENTIFY).await;
    harness.state(Some(OTHER_CHANNEL));
    let mut call = dave_call(OTHER_CHANNEL, &[LATE_PEER]);
    call.join_remote(bot());
    harness.encrypting(&mut call).await;
    let sink = harness.sink(SinkFormat::Opus, StallPolicy::Hold);
    let sent = opus(1);
    sink.write(&sent[0]).unwrap();
    harness.tick(1);

    assert_eq!(
        harness.wait_for(moved).await,
        (CHANNEL, OTHER_CHANNEL, false)
    );
    assert_eq!(harness.server.count(op::IDENTIFY), 2);
    let packets = harness.server.sfu.wait_packets(1, WAIT);
    assert_eq!(
        hear(&mut call, LATE_PEER, &packets[0].payload),
        Some(sent[0].to_vec())
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_disconnect_close_then_a_null_channel_is_a_removal_without_a_leave() {
    let mut harness = closed_with(4014).await;
    harness.until(VoiceStatus::Reconnecting).await;

    harness.state(None);
    let (reason, code, _) = harness.ended().await;

    assert_eq!((reason, code), (DisconnectReason::Removed, None));
    assert_eq!(harness.leaves(), 0);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_disconnect_close_then_a_new_channel_is_a_move() {
    let mut harness = closed_with(4014).await;
    harness.until(VoiceStatus::Reconnecting).await;

    harness.state(Some(OTHER_CHANNEL));
    let moved = harness.wait_for(moved).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;

    assert_eq!(moved, (CHANNEL, OTHER_CHANNEL, false));
    harness.server.collect().await;
    assert_eq!(harness.server.count(op::IDENTIFY), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_terminated_call_then_a_new_server_connects_to_it() {
    let mut harness = closed_with(4022).await;
    harness.until(VoiceStatus::Reconnecting).await;
    let mut server = Server::start(guild(), |_| {}).await;

    harness.fresh_server_at(&mut server).await;
    harness.until(VoiceStatus::Ready).await;

    server.expect_op(op::IDENTIFY).await;
    assert_eq!(harness.joins(CHANNEL), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_terminated_call_waits_the_server_change_delay_even_past_the_timeout() {
    let delay = Duration::from_millis(400);
    let mut harness = Harness::start().await;
    harness
        .voice
        .connect(&ConnectRequest {
            timeout: Duration::from_millis(200),
            server_change_delay: delay,
            ..Harness::request()
        })
        .unwrap();
    harness.announce().await;
    harness.until(VoiceStatus::Ready).await;
    harness.server.gateway.close(4022);
    harness.until(VoiceStatus::Reconnecting).await;
    let mut server = Server::start(guild(), |_| {}).await;

    let offered = Instant::now();
    harness.fresh_server_at(&mut server).await;
    server.expect_op(op::IDENTIFY).await;
    let waited = offered.elapsed();
    harness.until(VoiceStatus::Ready).await;

    assert!(waited >= delay, "identified after {waited:?}");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_first_connect_and_a_move_skip_the_server_change_delay() {
    let mut harness = Harness::start().await;
    harness
        .voice
        .connect(&ConnectRequest {
            server_change_delay: Duration::from_secs(3_600),
            ..Harness::request()
        })
        .unwrap();
    harness.announce().await;
    harness.until(VoiceStatus::Ready).await;

    harness.fresh_server().await;
    harness.until(VoiceStatus::Reconnecting).await;
    harness.state(Some(OTHER_CHANNEL));
    harness.wait_for(moved).await;
    harness.until(VoiceStatus::Ready).await;

    harness.server.collect().await;
    assert_eq!(harness.server.count(op::IDENTIFY), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_changed_server_change_delay_applies_to_the_next_server_change() {
    let mut harness = Harness::start().await;
    harness
        .voice
        .connect(&ConnectRequest {
            server_change_delay: Duration::from_secs(3_600),
            ..Harness::request()
        })
        .unwrap();
    harness.announce().await;
    harness.until(VoiceStatus::Ready).await;

    harness
        .voice
        .set_server_change_delay(guild(), Duration::ZERO)
        .unwrap();
    harness.fresh_server().await;
    harness.until(VoiceStatus::Reconnecting).await;
    harness.until(VoiceStatus::Ready).await;

    harness.server.collect().await;
    assert_eq!(harness.server.count(op::IDENTIFY), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_session_replaced_during_the_change_delay_waits_for_new_server() {
    let mut harness = Harness::start().await;
    harness
        .voice
        .connect(&ConnectRequest {
            server_change_delay: Duration::from_secs(3_600),
            ..Harness::request()
        })
        .unwrap();
    harness.announce().await;
    harness.until(VoiceStatus::Ready).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Reconnecting).await;

    harness.tap.session_started(ShardId::from(SHARD));
    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
    harness.announce().await;
    harness.server.expect_op(op::IDENTIFY).await;
    let identify = harness.server.expect_op(op::IDENTIFY).await;

    assert_eq!(identify["token"].as_str(), Some("token-3"));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_endpointless_server_holds_media_until_the_next_one() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 20)).unwrap();
    harness.tick(2);
    assert_eq!(harness.server.sfu.wait_packets(2, WAIT).len(), 2);

    harness.offer_server(guild(), "gone", None);
    let code = harness.server.expect_close().await;
    let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
    harness.tick(2);
    let mut server = Server::start(guild(), |_| {}).await;
    harness.fresh_server_at(&mut server).await;
    harness.until(VoiceStatus::Ready).await;
    harness.tick(3);

    assert_eq!((code, detail.as_str()), (4000, "server_unavailable"));
    assert_eq!(server.sfu.wait_packets(3, WAIT).len(), 3);
    assert_eq!(harness.server.sfu.packets().len(), 2);
    assert_eq!(sink.stats().unwrap().held_ticks, 2);
    assert_eq!(queued_frames(&sink), 15 * 960);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_new_token_on_the_same_endpoint_identifies_again_and_never_resumes() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    harness.fresh_server().await;
    let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
    harness.until(VoiceStatus::Ready).await;
    harness.server.collect().await;

    assert_eq!(detail, "server_changed");
    assert_eq!(harness.server.count(op::IDENTIFY), 2);
    assert_eq!(
        harness.server.json(op::IDENTIFY)[1]["token"].as_str(),
        Some("token-2")
    );
    assert_eq!(harness.server.count(op::RESUME), 0);
    assert_eq!(harness.joins(CHANNEL), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_replaced_main_session_retries_the_voice_state_and_starts_fresh() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    harness.recorder.refusals.store(2, Ordering::SeqCst);

    harness.tap.session_started(ShardId::from(SHARD));
    let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;
    harness.server.collect().await;

    assert_eq!(detail, "session_replaced");
    assert_eq!(harness.server.count(op::IDENTIFY), 2);
    assert_eq!(harness.recorder.refusals.load(Ordering::SeqCst), 0);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_connect_after_the_main_session_started_joins_once() {
    let mut harness = Harness::start().await;
    harness.tap.session_started(ShardId::from(SHARD));

    harness.connected().await;
    harness.server.collect().await;

    assert_eq!(harness.joins(CHANNEL), 1);
    assert_eq!(harness.server.count(op::IDENTIFY), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_update_to_another_channel_is_a_move_by_us() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    harness
        .voice
        .update(guild(), ChannelId::from(OTHER_CHANNEL), true, false)
        .unwrap();
    harness.state(Some(OTHER_CHANNEL));
    let moved = harness.wait_for(moved).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;

    assert_eq!(moved, (CHANNEL, OTHER_CHANNEL, true));
    let update = harness.recorder.voice_states().last().cloned().unwrap();
    assert_eq!(
        update["d"]["channel_id"].as_str(),
        Some(OTHER_CHANNEL.to_string().as_str())
    );
    assert_eq!(update["d"]["self_mute"].as_bool(), Some(true));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn the_last_leave_arriving_after_a_new_connect_is_not_a_removal() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    harness.voice.disconnect(guild()).unwrap();
    harness.ended().await;

    harness.connect();
    harness.state(None);
    harness.announce().await;
    harness.until(VoiceStatus::Ready).await;

    harness.state(None);
    let (reason, _, _) = harness.ended().await;
    assert_eq!(reason, DisconnectReason::Removed);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_new_server_while_waiting_for_dave_restarts_the_first_connect_there() {
    let mut harness = Harness::with(|config| {
        config.dave_protocol_version = 1;
        config.early = vec![Outgoing::Json {
            op: op::CLIENTS_CONNECT,
            d: json!({"user_ids": ["77"]}),
        }];
    })
    .await;
    harness.connect();
    harness.announce().await;
    harness.server.expect_op(op::DAVE_MLS_KEY_PACKAGE).await;
    let mut server = Server::start(guild(), |_| {}).await;

    harness.fresh_server_at(&mut server).await;
    harness.until(VoiceStatus::Ready).await;

    server.expect_op(op::IDENTIFY).await;
    let statuses: Vec<VoiceStatus> = harness.statuses().iter().map(|state| state.0).collect();
    assert_eq!(statuses, [VoiceStatus::Connecting, VoiceStatus::Ready]);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_replaced_main_session_inside_the_move_window_is_not_a_removal() {
    let mut harness = closed_with(4014).await;
    harness.until(VoiceStatus::Reconnecting).await;

    harness.tap.session_started(ShardId::from(SHARD));
    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;

    assert_eq!(harness.leaves(), 0);
}
