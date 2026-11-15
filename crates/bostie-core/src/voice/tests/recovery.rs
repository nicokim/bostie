use std::net::SocketAddr;

use futures_util::{SinkExt, StreamExt};
use tokio::net::TcpListener;
use tokio_websockets::{ClientBuilder, CloseCode, ServerBuilder};

use super::*;

struct Proxy {
    address: SocketAddr,
    refusals: Arc<AtomicUsize>,
    stalls: Arc<AtomicUsize>,
}

impl Proxy {
    async fn start(target: String) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let refusals = Arc::new(AtomicUsize::new(0));
        let stalls = Arc::new(AtomicUsize::new(0));
        let (refused, stalled) = (Arc::clone(&refusals), Arc::clone(&stalls));
        tokio::spawn(async move {
            while let Ok((stream, _)) = listener.accept().await {
                let take = |counter: &AtomicUsize| {
                    counter
                        .try_update(Ordering::SeqCst, Ordering::SeqCst, |left| {
                            left.checked_sub(1)
                        })
                        .is_ok()
                };
                let (stall, refuse) = (take(&stalled), take(&refused));
                let target = target.clone();
                tokio::spawn(async move {
                    if stall {
                        sleep(WAIT).await;
                        drop(stream);
                        return;
                    }
                    let Ok((_, mut client)) = ServerBuilder::new().accept(stream).await else {
                        return;
                    };
                    if refuse {
                        let close = Message::close(CloseCode::try_from(1000).ok(), "");
                        drop(client.send(close).await);
                        while client.next().await.is_some() {}
                        return;
                    }
                    let url = format!("{target}/?v=8");
                    let Ok((mut upstream, _)) =
                        ClientBuilder::new().uri(&url).unwrap().connect().await
                    else {
                        return;
                    };
                    loop {
                        tokio::select! {
                            message = client.next() => match message {
                                Some(Ok(message)) => {
                                    if upstream.send(message).await.is_err() {
                                        return;
                                    }
                                }
                                _ => return,
                            },
                            message = upstream.next() => match message {
                                Some(Ok(message)) => {
                                    if client.send(message).await.is_err() {
                                        return;
                                    }
                                }
                                _ => return,
                            },
                        }
                    }
                });
            }
        });
        Self {
            address,
            refusals,
            stalls,
        }
    }

    fn endpoint(&self) -> String {
        format!("ws://{}", self.address)
    }
}

impl Harness {
    async fn via_proxy(&mut self) -> Proxy {
        let proxy = Proxy::start(self.server.endpoint()).await;
        self.connect();
        self.state(Some(CHANNEL));
        let token = self.next_token();
        self.server.token(&token).await;
        self.offer_server(guild(), &token, Some(&proxy.endpoint()));
        self.until(VoiceStatus::Ready).await;
        proxy
    }

    async fn acknowledged(&mut self, ssrc: u32) {
        self.server.collect().await;
        let from = self.server.messages.len();
        let deadline = Instant::now() + WAIT;
        while !self.server.messages[from..]
            .iter()
            .any(|message| message.op() == Some(u64::from(op::HEARTBEAT)))
        {
            assert!(Instant::now() < deadline, "no heartbeat after READY");
            self.server.collect().await;
        }
        self.server.gateway.send(
            op::SPEAKING,
            json!({"user_id": "9", "ssrc": ssrc, "speaking": 1}),
        );
        self.speaking_from(ssrc).await;
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_dropped_socket_resumes_with_the_last_seq_and_keeps_sending() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 20)).unwrap();
    harness.tick(2);
    harness
        .server
        .gateway
        .send_binary(op::DAVE_MLS_EXTERNAL_SENDER, b"cached");
    harness.server.gateway.send(
        op::SPEAKING,
        json!({"user_id": "7", "ssrc": 70, "speaking": 1}),
    );
    harness
        .wait_for(|delivery| {
            matches!(delivery, Delivery::VoiceSpeaking { ssrc: 70, .. }).then_some(())
        })
        .await;

    harness.server.gateway.drop_connection();
    harness.server.gateway.send(
        op::SPEAKING,
        json!({"user_id": "8", "ssrc": 80, "speaking": 1}),
    );
    harness.server.gateway.send(
        op::DAVE_PREPARE_TRANSITION,
        json!({"transition_id": 6, "protocol_version": 0}),
    );
    harness.until(VoiceStatus::Resuming).await;
    harness.tick(3);
    let resume = harness.server.expect_op(op::RESUME).await;
    harness.until(VoiceStatus::Ready).await;
    harness
        .wait_for(|delivery| {
            matches!(delivery, Delivery::VoiceSpeaking { ssrc: 80, .. }).then_some(())
        })
        .await;
    let ready = harness.server.expect_op(op::DAVE_TRANSITION_READY).await;
    harness.tick(3);

    assert_eq!(resume["seq_ack"].as_u64(), Some(4));
    assert_eq!(ready["transition_id"].as_u64(), Some(6));
    assert_eq!(harness.server.count(op::IDENTIFY), 1);
    assert_eq!(harness.server.sfu.wait_packets(8, WAIT).len(), 8);
    assert_eq!(sink.stats().unwrap().held_ticks, 0);
    assert_eq!(harness.server.count(op::RESUME), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_lost_transport_resumes_once_ready_and_rejoins_before() {
    for (ready, close) in [(true, Some(4015)), (false, Some(4015)), (false, None)] {
        let mut harness = Harness::tuned(|tuning| tuning.discovery_interval = WAIT).await;
        if !ready {
            harness.server.sfu.ignore_discoveries(usize::MAX);
        }
        harness.connect();
        harness.announce().await;
        harness.server.expect_op(op::IDENTIFY).await;
        if ready {
            harness.until(VoiceStatus::Ready).await;
        }

        match close {
            Some(code) => harness.server.gateway.close(code),
            None => harness.server.gateway.drop_connection(),
        }

        if ready {
            let (_, _, _, detail) = harness.until(VoiceStatus::Resuming).await;
            harness.server.expect_op(op::RESUME).await;
            harness.until(VoiceStatus::Ready).await;
            assert!(detail.contains("4015"), "{detail}");
        } else {
            harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
            assert_eq!(harness.server.count(op::RESUME), 0, "{close:?}");
        }
        assert_eq!(harness.server.count(op::IDENTIFY), 1, "{close:?}");
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_missed_heartbeat_ack_resumes_as_a_zombie() {
    let mut harness = Harness::with(|config| config.heartbeat_interval = QUICK_HEARTBEAT).await;
    harness.connected().await;

    harness.server.gateway.hold_acks().await;
    harness
        .server
        .gateway
        .configure(|config| config.heartbeat_interval = HEARTBEAT)
        .await;

    let (_, _, _, detail) = harness.until(VoiceStatus::Resuming).await;
    harness.server.expect_op(op::RESUME).await;
    harness.until(VoiceStatus::Ready).await;
    assert!(detail.contains("heartbeat"), "{detail}");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_close_code_bostie_caused_ends_the_connection_without_retrying() {
    let mut harness = closed_with(4021).await;

    assert_eq!(harness.ended().await.0, DisconnectReason::RateLimited);
    let statuses = harness.statuses();
    let steps: Vec<VoiceStatus> = statuses.iter().map(|state| state.0).collect();
    assert_eq!(
        steps,
        [
            VoiceStatus::Connecting,
            VoiceStatus::Ready,
            VoiceStatus::Disconnected
        ]
    );
    assert_eq!(statuses.last().unwrap().2, Some(4021));
    assert_eq!(harness.joins(CHANNEL), 1);
    assert_eq!(harness.leaves(), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn disconnected_and_call_terminated_wait_for_the_gateway_then_end() {
    for (code, reason, leaves) in [
        (4014, DisconnectReason::Removed, 0),
        (4022, DisconnectReason::CallTerminated, 1),
    ] {
        let mut harness =
            Harness::tuned(|tuning| tuning.move_window = Duration::from_millis(200)).await;
        harness.connected().await;
        harness.server.gateway.close(code);

        let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
        let (ended, ended_code, _) = harness.ended().await;

        assert_eq!(detail, "waiting_for_gateway", "{code}");
        assert_eq!((ended, ended_code), (reason, Some(code)));
        assert_eq!(harness.leaves(), leaves, "{code}");
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn reconnect_closes_with_4000_and_resumes() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    harness.voice.reconnect(guild()).unwrap();

    assert_eq!(harness.server.expect_close().await, 4000);
    harness.server.expect_op(op::RESUME).await;
    harness.until(VoiceStatus::Resuming).await;
    harness.until(VoiceStatus::Ready).await;
    assert_eq!(harness.server.count(op::IDENTIFY), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_exhausted_nonce_rejoins_for_a_new_key_and_restarts_at_zero() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 10)).unwrap();
    harness.tick(2);

    harness.media().request_rekey();
    let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
    harness.server.rekey([9; 32]).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;
    harness.tick(2);

    assert_eq!(detail, "rejoining");
    let packets = harness.server.sfu.wait_packets(4, WAIT);
    let nonces: Vec<(usize, u32)> = packets
        .iter()
        .map(|packet| (packet.key, packet.nonce))
        .collect();
    assert_eq!(nonces, [(0, 0), (0, 1), (1, 0), (1, 1)]);
    assert_eq!(harness.joins(CHANNEL), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_move_while_resuming_abandons_the_resume_for_the_new_server() {
    let mut harness = Harness::tuned(|tuning| tuning.ws.connect = WAIT).await;
    let proxy = harness.via_proxy().await;
    proxy.stalls.store(5, Ordering::SeqCst);

    harness.server.gateway.drop_connection();
    harness.until(VoiceStatus::Resuming).await;
    harness.state(Some(OTHER_CHANNEL));
    let moved = harness.wait_for(moved).await;
    let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
    let mut server = Server::start(guild(), |_| {}).await;
    harness.fresh_server_at(&mut server).await;
    harness.until(VoiceStatus::Ready).await;
    server.expect_op(op::IDENTIFY).await;
    harness.server.collect().await;

    assert_eq!(moved, (CHANNEL, OTHER_CHANNEL, false));
    assert_eq!(detail, "moving");
    assert_eq!(server.count(op::RESUME), 0);
    assert_eq!(harness.server.count(op::RESUME), 0);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn resumes_closed_before_resumed_count_as_failures_then_rejoin() {
    let mut harness = Harness::start().await;
    let proxy = harness.via_proxy().await;
    proxy.refusals.store(2, Ordering::SeqCst);

    harness.server.gateway.drop_connection();
    harness.until(VoiceStatus::Resuming).await;
    let (_, _, _, detail) = harness.until(VoiceStatus::Reconnecting).await;
    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
    let token = harness.next_token();
    harness.server.token(&token).await;
    harness.offer_server(guild(), &token, Some(&proxy.endpoint()));
    harness.until(VoiceStatus::Ready).await;
    harness.server.collect().await;

    assert_eq!(detail, "rejoining");
    assert_eq!(proxy.refusals.load(Ordering::SeqCst), 0);
    assert_eq!(harness.server.count(op::RESUME), 0);
    assert_eq!(harness.server.count(op::IDENTIFY), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_resume_whose_dial_times_out_is_retried_before_any_rejoin() {
    let mut harness = Harness::tuned(|tuning| tuning.ws.connect = Duration::from_millis(200)).await;
    let proxy = harness.via_proxy().await;
    proxy.stalls.store(1, Ordering::SeqCst);

    harness.server.gateway.drop_connection();
    harness.until(VoiceStatus::Resuming).await;
    harness.until(VoiceStatus::Ready).await;
    harness.server.collect().await;

    assert_eq!(proxy.stalls.load(Ordering::SeqCst), 0);
    assert_eq!(harness.server.count(op::RESUME), 1);
    assert_eq!(harness.joins(CHANNEL), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn sessions_that_die_right_after_ready_end_after_three_rejoins() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    for rejoin in 0..4 {
        harness.server.gateway.close(4006);
        if rejoin == 3 {
            break;
        }
        harness
            .settle(|harness| harness.joins(CHANNEL) == rejoin + 2)
            .await;
        harness.fresh_server().await;
        harness.until(VoiceStatus::Ready).await;
    }
    let (reason, code, _) = harness.ended().await;

    assert_eq!(
        (reason, code),
        (DisconnectReason::SessionInvalid, Some(4006))
    );
    assert_eq!(harness.joins(CHANNEL), 4);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn resumes_that_drop_before_their_first_ack_fall_back_to_a_rejoin() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    for _ in 0..2 {
        harness.server.gateway.drop_connection();
        harness.server.expect_op(op::RESUME).await;
        harness.until(VoiceStatus::Ready).await;
    }
    harness.server.gateway.drop_connection();
    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;

    assert_eq!(harness.server.count(op::RESUME), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn an_acknowledged_heartbeat_restores_the_rejoin_and_resume_budgets() {
    for rejoins in [true, false] {
        let mut harness = Harness::with(|config| config.heartbeat_interval = QUICK_HEARTBEAT).await;
        harness.connected().await;

        for (round, ssrc) in (0..4).zip(90..) {
            harness.acknowledged(ssrc).await;
            if rejoins {
                harness.server.gateway.close(4006);
                harness
                    .settle(|harness| harness.joins(CHANNEL) == round + 2)
                    .await;
                harness.fresh_server().await;
            } else {
                harness.server.gateway.drop_connection();
                harness.until(VoiceStatus::Resuming).await;
            }
            harness.until(VoiceStatus::Ready).await;
        }
        harness
            .settle(|harness| {
                let (gateway, udp) = harness.voice.latency(guild()).unwrap();
                gateway.is_some() && udp.is_some()
            })
            .await;
        harness.server.collect().await;

        let expected = if rejoins { (5, 0) } else { (1, 4) };
        let budgets = (harness.joins(CHANNEL), harness.server.count(op::RESUME));
        assert_eq!(budgets, expected, "{rejoins}");
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_close_code_before_an_acknowledged_heartbeat_is_forgotten() {
    let mut harness = Harness::with(|config| config.heartbeat_interval = QUICK_HEARTBEAT).await;
    harness.connected().await;
    harness.server.gateway.close(4004);
    harness.settle(|harness| harness.joins(CHANNEL) == 2).await;
    harness.fresh_server().await;
    harness.until(VoiceStatus::Ready).await;
    harness.acknowledged(90).await;

    for failure in 0..4 {
        harness.offer_server(guild(), "token", Some("http://unusable"));
        if failure < 3 {
            harness
                .settle(|harness| harness.joins(CHANNEL) == failure + 3)
                .await;
        }
    }
    let (reason, code, _) = harness.ended().await;

    assert_eq!((reason, code), (DisconnectReason::SessionInvalid, None));
}
