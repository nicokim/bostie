use super::*;

fn long(frames: u8) -> Vec<u8> {
    [0xFF, frames].into_iter().chain(1..=frames * 2).collect()
}

#[test]
fn users_nobody_receives_are_never_decrypted_but_still_speak() {
    let mut rig = Rig::dave();
    let mut call = rig.group(&[ALICE]);
    rig.map(ALICE_SSRC, ALICE);
    rig.map(BOB_SSRC, BOB);
    rig.send(ALICE_SSRC, 0, &garbage(0));
    let receiver = rig.open(only(&[ALICE], ReceiveFormat::Opus));
    rig.step();
    let queued_before_the_receiver = read(&receiver);
    rig.send(BOB_SSRC, 0, &garbage(1));
    rig.step();
    let speech = rig.speech();
    rig.send(ALICE_SSRC, 1, &garbage(2));
    rig.send(ALICE_SSRC, 2, &call.speak(user(ALICE), &long(3)));
    rig.step();
    rig.now += rig.media.speech_hangover() * 2;
    rig.step();
    let mut stopped = rig.speech();
    stopped.sort_unstable_by_key(|(user, _)| *user);
    rig.send(BOB_SSRC, 1, &garbage(3));
    rig.step();
    let restarted = rig.speech();
    rig.media.set_speech_activity(false);
    rig.step();
    let switched_off = rig.speech();
    rig.send(BOB_SSRC, 2, &garbage(4));
    rig.step();
    rig.now += rig.reception.core.tuning.hold;
    rig.step();

    assert!(rig.speech().is_empty());
    assert_eq!(restarted, [(BOB, Speaking::Started)]);
    assert_eq!(switched_off, [(BOB, Speaking::Stopped(FRAME))]);
    assert!(queued_before_the_receiver.is_empty());
    assert_eq!(
        speech,
        [(ALICE, Speaking::Started), (BOB, Speaking::Started)]
    );
    let sixty_ms = Duration::from_millis(60);
    assert_eq!(
        stopped,
        [
            (ALICE, Speaking::Stopped(FRAME * 2 + sixty_ms)),
            (BOB, Speaking::Stopped(FRAME))
        ]
    );
    assert_eq!(rig.undecryptable(), 1);
}

#[test]
fn frames_failing_in_a_late_commit_retry_on_dave_changes() {
    for speaker in [BOB, ALICE] {
        let mut rig = Rig::new(ReceiveTuning {
            dave_retries: 10,
            ..tuning()
        });
        let mut call = rig.group(&[ALICE]);
        rig.map(ALICE_SSRC, ALICE);
        rig.map(BOB_SSRC, BOB);
        rig.map(STRANGER_SSRC, STRANGER);
        let receiver = rig.open(options(ReceiveFormat::Opus));
        let packets = opus(16, 0.3);
        for (sequence, packet) in (0..4).zip(&packets) {
            let frame = call.speak(user(ALICE), packet);
            rig.send(ALICE_SSRC, sequence, &frame);
            rig.step();
        }
        let before = read(&receiver).len();
        let foreign = call.speak(user(ALICE), &packets[0]);

        join_behind_the_bot(&mut call, BOB);
        let ssrc = if speaker == ALICE {
            ALICE_SSRC
        } else {
            BOB_SSRC
        };
        let spoken = &packets[4..];
        let frames: Vec<Vec<u8>> = spoken
            .iter()
            .map(|packet| call.speak(user(speaker), packet))
            .collect();
        for (sequence, frame) in (4..).zip(&frames) {
            rig.send(ssrc, sequence, frame);
        }
        rig.send(STRANGER_SSRC, 0, &foreign);
        rig.step();
        let while_behind = read(&receiver).len();
        rig.send(ssrc, 4, &frames[0]);
        rig.step();
        let without_a_change = read(&receiver).len();
        rig.deliver(&mut call);
        rig.step();
        let first = read(&receiver);
        rig.step();
        let rest = read(&receiver);
        let still_held = rig.undecryptable();
        rig.now += rig.reception.core.tuning.hold;
        rig.step();

        assert_eq!(before, 4, "{speaker}");
        assert_eq!((while_behind, without_a_change), (0, 0), "{speaker}");
        assert_eq!((first.len(), rest.len()), (10, 2), "{speaker}");
        assert_eq!(data(&[first, rest].concat()), spoken, "{speaker}");
        assert_eq!(still_held, 0, "{speaker}");
        assert_eq!(rig.undecryptable(), 1, "{speaker}");
        assert!(read(&receiver).is_empty(), "{speaker}");
        assert!(!rig.recovered(), "{speaker}");
    }
}

#[test]
fn a_busy_dave_lock_does_not_hold_plain_frames() {
    let plain_ending_in_the_marker = Bytes::from_static(&[0xFC, 0x01, 0x02, 0xFA, 0xFA]);
    let mut rig = Rig::new(tuning());
    rig.map(ALICE_SSRC, ALICE);
    let receiver = rig.open(options(ReceiveFormat::Opus));
    let media = Arc::clone(&rig.media);
    let busy = media.dave();
    rig.send(ALICE_SSRC, 0, &plain_ending_in_the_marker);
    rig.step();
    drop(busy);

    assert_eq!(data(&read(&receiver)), [plain_ending_in_the_marker]);
}

#[test]
fn dave_frames_wait_in_user_order_and_decrypt_in_slices() {
    let mut rig = Rig::new(ReceiveTuning {
        dave_budget: Duration::ZERO,
        ..tuning()
    });
    let mut call = rig.group(&[ALICE]);
    rig.map(ALICE_SSRC, ALICE);
    rig.map(BOB_SSRC, BOB);
    let receiver = rig.open(options(ReceiveFormat::Packets));
    let frames: Vec<Vec<u8>> = opus(5, 0.3)
        .iter()
        .map(|packet| call.speak(user(ALICE), packet))
        .collect();
    let order = |items: Vec<Item>| -> Vec<(u64, u16)> {
        items
            .into_iter()
            .filter_map(|item| match item {
                Item::Packet { user, sequence, .. } => Some((u64::from(user), sequence)),
                _ => None,
            })
            .collect()
    };
    let media = Arc::clone(&rig.media);
    let busy = media.dave();
    for (ssrc, sequence, frame) in [
        (ALICE_SSRC, 0, &frames[0][..]),
        (ALICE_SSRC, 1, &frames[1][..]),
        (ALICE_SSRC, 2, &SILENCE_FRAME[..]),
        (BOB_SSRC, 0, &SILENCE_FRAME[..]),
    ] {
        rig.send(ssrc, sequence, frame);
    }
    rig.step();
    drop(busy);
    let started = rig.speech();
    let mut ticks = vec![order(read(&receiver))];
    rig.send(ALICE_SSRC, 3, &frames[2]);
    for _ in 0..3 {
        rig.now += FRAME;
        rig.run(0, Instant::now());
        ticks.push(order(read(&receiver)));
    }
    rig.send(ALICE_SSRC, 4, &frames[3]);
    rig.send(ALICE_SSRC, 5, &frames[4]);
    rig.step();
    ticks.push(order(read(&receiver)));

    assert_eq!(started, [(ALICE, Speaking::Started)]);
    assert_eq!(
        ticks,
        [
            vec![(BOB, 0)],
            vec![(ALICE, 0)],
            vec![(ALICE, 1), (ALICE, 2)],
            vec![(ALICE, 3)],
            vec![(ALICE, 4), (ALICE, 5)]
        ]
    );
}

#[test]
fn the_dave_hold_drops_its_oldest_frames_beyond_its_bounds() {
    let packets = opus(4, 0.3);
    let per_user = ReceiveTuning {
        hold_packets: 2,
        ..tuning()
    };
    let in_total = ReceiveTuning {
        arrivals: 3,
        ..tuning()
    };
    let rows = [
        (
            "per user",
            per_user,
            vec![vec![(BOB, 0), (ALICE, 0), (ALICE, 1), (ALICE, 2)]],
            vec![(BOB, 0), (ALICE, 1), (ALICE, 2)],
            false,
        ),
        (
            "in total",
            in_total,
            vec![vec![(BOB, 0), (ALICE, 0), (ALICE, 1)], vec![(ALICE, 2)]],
            vec![(ALICE, 0), (ALICE, 1), (ALICE, 2)],
            false,
        ),
        (
            "waiting behind a busy lock",
            in_total,
            (0..4).map(|sequence| vec![(ALICE, sequence)]).collect(),
            vec![(ALICE, 1), (ALICE, 2), (ALICE, 3)],
            true,
        ),
    ];
    for (bound, tuning, ticks, kept, busy) in rows {
        let mut rig = Rig::new(tuning);
        let mut call = rig.group(&[ALICE]);
        rig.map(ALICE_SSRC, ALICE);
        rig.map(BOB_SSRC, BOB);
        let receiver = rig.open(options(ReceiveFormat::Opus));
        join_behind_the_bot(&mut call, BOB);
        let media = Arc::clone(&rig.media);
        let lock = busy.then(|| media.dave());
        for tick in ticks {
            for (speaker, sequence) in tick {
                let ssrc = if speaker == ALICE {
                    ALICE_SSRC
                } else {
                    BOB_SSRC
                };
                let frame = call.speak(user(speaker), &packets[usize::from(sequence)]);
                rig.send(ssrc, sequence, &frame);
            }
            rig.step();
        }
        drop(lock);
        let dropped = rig.undecryptable();
        rig.deliver(&mut call);
        rig.step();

        let items = read(&receiver);
        let mut heard: Vec<(Option<u64>, Bytes)> =
            senders(&items).into_iter().zip(data(&items)).collect();
        heard.sort();
        let mut expected: Vec<(Option<u64>, Bytes)> = kept
            .into_iter()
            .map(|(speaker, sequence): (u64, u16)| {
                (Some(speaker), packets[usize::from(sequence)].clone())
            })
            .collect();
        expected.sort();
        assert_eq!(heard, expected, "{bound}");
        assert_eq!(dropped, u64::from(!busy), "{bound}");
    }
}

#[test]
fn a_failure_streak_asks_for_one_recovery_and_a_success_resets_it() {
    let tolerance = ReceiveTuning::default().decrypt_tolerance;
    let mut rig = Rig::dave();
    let mut call = rig.group(&[ALICE]);
    rig.map(ALICE_SSRC, ALICE);
    let _receiver = rig.open(options(ReceiveFormat::Opus));
    let decryptable = call.speak(user(ALICE), &opus(1, 0.3)[0]);
    let mut sequence = 0;
    let mut send = |rig: &mut Rig, frames: Vec<Vec<u8>>| {
        for frame in frames {
            rig.send(ALICE_SSRC, sequence, &frame);
            sequence += 1;
        }
        rig.step();
        rig.recovered()
    };
    let failing = |count: u32| {
        (0..count)
            .map(|index| garbage(u16::try_from(index).unwrap()))
            .collect()
    };
    let refused = (0..tolerance)
        .flat_map(|_| [Vec::new(), vec![0x03]])
        .collect();

    let after_refused = send(&mut rig, refused);
    let dropped = rig.undecryptable();
    let short = send(&mut rig, failing(tolerance - 1));
    let after_a_success = send(&mut rig, vec![decryptable]);
    let short_again = send(&mut rig, failing(tolerance - 1));
    let reached = send(&mut rig, failing(1));
    let within_the_interval = send(&mut rig, failing(tolerance));

    assert_eq!(dropped, 0);
    assert_eq!(
        (after_refused, short, after_a_success, short_again),
        (false, false, false, false)
    );
    assert_eq!((reached, within_the_interval), (true, false));
}

#[test]
fn streams_and_failure_streaks_unheard_for_the_stream_idle_are_forgotten() {
    let tolerance = ReceiveTuning::default().decrypt_tolerance;
    let tuning = ReceiveTuning {
        dave_grace: Duration::ZERO,
        stream_idle: Duration::from_secs(5),
        ..tuning()
    };
    let mut rig = Rig::new(tuning);
    let mut call = rig.group(&[ALICE]);
    rig.map(ALICE_SSRC, ALICE);
    rig.media
        .with_dave(|dave| dave.client_disconnect(user(ALICE)));

    let receiver = rig.open(options(ReceiveFormat::Opus));
    let packets = opus(2, 0.3);
    rig.send(ALICE_SSRC, 1, &call.speak(user(ALICE), &packets[0]));
    for sequence in 2..=u16::try_from(tolerance).unwrap() {
        rig.send(ALICE_SSRC, sequence, &garbage(sequence));
    }
    rig.step();
    rig.now += tuning.stream_idle;
    rig.step();
    rig.send(ALICE_SSRC, 40_000, &garbage(40_000));
    rig.step();
    let recovered = rig.recovered();
    rig.send(ALICE_SSRC, 40_001, &call.speak(user(ALICE), &packets[1]));
    rig.step();

    assert_eq!(data(&read(&receiver)), packets);
    assert!(!recovered);
}
