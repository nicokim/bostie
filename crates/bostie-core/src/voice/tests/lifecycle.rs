use super::*;
use crate::voice::source::tests::fixture;
use crate::voice::testkit::sfu::Packet;

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_connect_timeout_names_what_is_missing() {
    let short = Duration::from_millis(300);
    let long = Duration::from_secs(1);
    for (offered, timeout, missing) in [
        (
            "nothing",
            short,
            "voice_events: no VOICE_STATE_UPDATE or VOICE_SERVER_UPDATE arrived for the channel",
        ),
        (
            "state",
            short,
            "voice_events: no VOICE_SERVER_UPDATE with an endpoint arrived",
        ),
        (
            "server",
            short,
            "voice_events: no VOICE_STATE_UPDATE arrived for the channel",
        ),
        (
            "a server that never answers discovery",
            short,
            "no progress within",
        ),
        (
            "a server whose discovery fails",
            long,
            "ip discovery: no answer from",
        ),
        (
            "an endpointless server after ready",
            long,
            "server_unavailable: no VOICE_SERVER_UPDATE with an endpoint arrived",
        ),
    ] {
        let slow = offered == "a server that never answers discovery";
        let mut harness = Harness::tuned(|tuning| {
            if slow {
                tuning.discovery_interval = WAIT;
            }
        })
        .await;
        harness
            .voice
            .connect(&ConnectRequest {
                timeout,
                ..Harness::request()
            })
            .unwrap();
        match offered {
            "state" => harness.state(Some(CHANNEL)),
            "server" => harness.fresh_server().await,
            "nothing" => {}
            "an endpointless server after ready" => {
                harness.announce().await;
                harness.until(VoiceStatus::Ready).await;
                harness.offer_server(guild(), "gone", None);
            }
            _ => {
                harness.server.sfu.ignore_discoveries(usize::MAX);
                harness.announce().await;
            }
        }

        let (reason, code, detail) = harness.ended().await;

        assert_eq!(
            (reason, code),
            (DisconnectReason::Timeout, None),
            "{offered}"
        );
        assert!(detail.contains(missing), "{offered}: {detail}");
        if offered == "nothing" {
            assert_eq!(harness.leaves(), 1);
        }
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn disconnect_closes_with_1000_leaves_and_closes_the_sinks_and_receivers() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    sink.write(&tone(FRAME_SAMPLES * 3)).unwrap();
    let (marker, _) = sink.mark().unwrap();
    let receiver = harness.receiver(receiving(ReceiveFormat::Opus));

    harness.voice.disconnect(guild()).unwrap();
    let (reason, code, _) = harness.ended().await;
    let played = harness
        .wait_for(|delivery| match delivery {
            Delivery::VoiceMarker {
                marker: got,
                played,
                ..
            } if *got == marker => Some(*played),
            _ => None,
        })
        .await;

    assert_eq!((reason, code), (DisconnectReason::Requested, None));
    assert_eq!(harness.server.expect_close().await, 1000);
    assert_eq!(harness.leaves(), 1);
    assert!(sink.is_closed());
    assert!(!played);
    assert_eq!(
        receiver.read(256).unwrap(),
        ReceiveRead::Ended(Ended::Disconnected)
    );
    harness.voice.disconnect(guild()).unwrap();
    assert!(matches!(
        harness.voice.dave(guild()),
        Err(CoreError::UnknownVoice(_))
    ));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn closing_the_core_ends_without_a_leave_and_drops_the_lane() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);

    harness.voice.shutdown();
    let (reason, _, _) = harness.ended().await;
    harness.tick(1);

    assert_eq!(reason, DisconnectReason::Closed);
    assert_eq!(harness.leaves(), 0);
    assert!(sink.is_closed());
    assert_eq!(harness.pacer.lanes(), 0);
    assert!(matches!(
        harness.voice.connect(&Harness::request()),
        Err(CoreError::Closed)
    ));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_stopped_lane_ends_its_connection_and_spares_the_others() {
    let mut harness = Harness::start().await;
    harness.connected().await;
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
                after: None,
                crossfade: Duration::ZERO,
            },
        )
        .unwrap();
    other.write(&tone(FRAME_SAMPLES * 5)).unwrap();

    harness.media().close();
    harness.tick(3);
    let (reason, _, detail) = harness.ended().await;

    assert_eq!(reason, DisconnectReason::ProtocolError);
    assert_eq!(detail, "internal error: the media lane ended");
    assert_eq!(harness.leaves(), 1);
    assert_eq!(second.sfu.wait_packets(3, WAIT).len(), 3);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_panicking_connection_task_is_ended_by_its_supervisor() {
    let mut harness = Harness::start().await;
    harness.connect();
    let (info, media) = harness
        .voice
        .with_connection(guild(), |handle| {
            (Arc::clone(&handle.info), Arc::clone(&handle.media))
        })
        .unwrap();
    let task: JoinHandle<Ending> = tokio::spawn(async { panic!("boom") });

    supervise(
        task,
        Arc::clone(&harness.voice.shared),
        info,
        Arc::clone(&media),
    )
    .await;
    let (reason, _, detail) = harness.ended().await;

    assert_eq!(reason, DisconnectReason::ProtocolError);
    assert_eq!(detail, "internal error: boom");
    assert_eq!(harness.leaves(), 1);
    assert!(media.is_closed());
    assert!(matches!(
        harness.voice.dave(guild()),
        Err(CoreError::UnknownVoice(_))
    ));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn real_pacer_threads_send_on_a_20_ms_clock() {
    let mut harness = Harness::build(|_| {}, Some(Arc::new(PacerPool::new())), tuning()).await;
    harness.connected().await;
    let sink = harness.sink(pcm(), StallPolicy::Hold);
    let written = std::time::Instant::now();
    sink.write(&tone(FRAME_SAMPLES * 10)).unwrap();
    sink.flush().unwrap();

    let packets: Vec<Packet> = harness.server.sfu.wait_packets(15, WAIT);

    assert_eq!(packets.len(), 15);
    let last = packets[14].at.duration_since(written);
    assert!(last >= FRAME * 10, "{last:?}");
}

fn tone_file() -> SourceSpec {
    SourceSpec::File(fixture("tone.flac"))
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn shutdown_closes_every_open_source_and_refuses_new_ones() {
    let harness = Harness::start().await;
    let kept = harness.voice.open_source(tone_file()).unwrap();
    drop(harness.voice.open_source(tone_file()).unwrap());

    harness.voice.shutdown();

    assert!(kept.is_closed());
    assert!(matches!(kept.read(), Err(CoreError::SourceClosed(_))));
    assert!(matches!(
        harness.voice.open_source(tone_file()),
        Err(CoreError::Closed)
    ));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn dropped_sources_are_forgotten_when_the_next_one_opens() {
    let harness = Harness::start().await;
    for _ in 0..10 {
        drop(harness.voice.open_source(tone_file()).unwrap());
    }
    let kept = harness.voice.open_source(tone_file()).unwrap();

    assert_eq!(harness.voice.shared.sources.lock().len(), 1);
    assert!(!kept.is_closed());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn sinks_and_sources_never_share_an_id() {
    let harness = Harness::start().await;
    harness.connect();

    let first = harness.sink(pcm(), StallPolicy::Hold).id();
    let source = harness.voice.open_source(tone_file()).unwrap().id();
    let last = harness.sink(pcm(), StallPolicy::Hold).id();

    assert!(first != source && source != last && first != last);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_stall_is_reported_after_one_frame_at_the_earliest() {
    let mut harness = Harness::start().await;
    harness.connected().await;

    let refused = harness
        .voice
        .set_stall_after(guild(), FRAME.saturating_sub(Duration::from_nanos(1)));
    harness.voice.set_stall_after(guild(), FRAME).unwrap();

    assert!(matches!(refused, Err(CoreError::InvalidRequest(_))));
    assert_eq!(harness.media().stall_after(), FRAME);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn the_speech_hangover_is_checked_against_its_bounds() {
    let mut harness = Harness::start().await;
    harness.connected().await;
    let hangover = |duration| harness.voice.set_speech_hangover(guild(), duration);

    for refused in [
        FRAME.saturating_sub(Duration::from_nanos(1)),
        Duration::from_secs(10) + FRAME,
    ] {
        assert!(
            matches!(hangover(refused), Err(CoreError::InvalidRequest(_))),
            "{refused:?}"
        );
    }
    hangover(Duration::from_millis(350)).unwrap();

    assert_eq!(
        harness.media().speech_hangover(),
        Duration::from_millis(350)
    );
}

#[test]
fn a_throttle_lets_one_message_through_per_second() {
    let start = std::time::Instant::now();
    let mut throttle = Throttle::default();

    assert!(throttle.allows(start));
    assert!(!throttle.allows(start + LOG_EVERY / 2));
    assert!(throttle.allows(start + LOG_EVERY));
}
