use super::*;

const OPUS_ENDING_IN_MARKER: &[u8] = &[
    0xFC, 0xFF, 0xFE, 0x10, 0x32, 0x54, 0x76, 0x98, 0xBA, 0x00, 0xFA, 0xFA,
];

type Heard<'a> = (u64, &'a [u8], Result<&'a [u8], DecryptFailure>);

fn check(dave: &mut Dave, cases: &[Heard<'_>]) {
    for &(from, frame, expected) in cases {
        let mut out = vec![0xEE; 2];
        let heard = dave.receive(user(from), frame, &mut out).map(|()| out);
        assert_eq!(heard, expected.map(<[u8]>::to_vec), "{from} {frame:02X?}");
    }
}

#[test]
fn received_frames_follow_the_protocol_version_and_the_marker() {
    let (mut stage, _) = started(0);
    check(
        &mut stage,
        &[
            (A, OPUS, Ok(OPUS)),
            (A, OPUS_ENDING_IN_MARKER, Ok(OPUS_ENDING_IN_MARKER)),
            (A, &SILENCE_FRAME, Ok(&SILENCE_FRAME)),
        ],
    );

    let receive = ReceiveTuning {
        downgrade_expiry: 0,
        ..ReceiveTuning::default()
    };
    let mut call = group_with(&[BOT, A], receive);
    let encrypted = call.speak(user(A), OPUS);
    let empty = call.speak(user(A), &[]);
    check(
        call.dave_mut(user(BOT)),
        &[
            (A, &SILENCE_FRAME, Ok(&SILENCE_FRAME)),
            (A, &encrypted, Ok(OPUS)),
            (A, &empty, Err(DecryptFailure::Failed)),
            (A, OPUS, Err(DecryptFailure::Unencrypted)),
            (STRANGER, &encrypted, Err(DecryptFailure::NoDecryptor)),
        ],
    );

    call.fake.hold_transitions = true;
    let downgrade = call.fake.downgrade();
    call.enqueue(downgrade);
    call.run();
    let in_flight = call.speak(user(A), OPUS);
    check(
        call.dave_mut(user(BOT)),
        &[(A, OPUS_ENDING_IN_MARKER, Err(DecryptFailure::Unencrypted))],
    );

    let generation = call.dave(user(BOT)).generation();
    release(&mut call);
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Plain);
    assert_ne!(call.dave(user(BOT)).generation(), generation);
    check(
        call.dave_mut(user(BOT)),
        &[
            (A, &in_flight, Ok(OPUS)),
            (A, OPUS_ENDING_IN_MARKER, Ok(OPUS_ENDING_IN_MARKER)),
            (STRANGER, OPUS, Ok(OPUS)),
        ],
    );
}

#[test]
fn passthrough_survives_a_downgrade_and_an_upgrade() {
    for (unexecuted_downgrade, upgrade_passthrough, accepted) in
        [(false, 10, true), (true, 10, true), (false, 0, false)]
    {
        let receive = ReceiveTuning {
            upgrade_passthrough,
            downgrade_expiry: 0,
            ..ReceiveTuning::default()
        };
        let mut call = group_with(&[BOT, A], receive);
        let downgrade = call.fake.downgrade();
        call.enqueue(downgrade);
        call.run();
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Plain);
        call.fake.hold_transitions = true;
        if unexecuted_downgrade {
            let downgrade = call.fake.downgrade();
            call.enqueue(downgrade);
            call.run();
        }
        let upgrade = call.fake.upgrade();
        call.enqueue(upgrade);
        call.run();
        check(
            call.dave_mut(user(BOT)),
            &[(A, OPUS_ENDING_IN_MARKER, Ok(OPUS_ENDING_IN_MARKER))],
        );

        release(&mut call);
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
        let expected = if accepted {
            Ok(OPUS)
        } else {
            Err(DecryptFailure::Unencrypted)
        };
        check(call.dave_mut(user(BOT)), &[(A, OPUS, expected)]);
        assert!(call.failures.is_empty());
    }
}

#[test]
fn a_prepared_downgrade_accepts_plain_frames_until_its_expiry() {
    for (closed_upgrade, downgrade_expiry, expected) in [
        (false, 24, Ok(OPUS)),
        (true, 24, Ok(OPUS)),
        (false, 0, Err(DecryptFailure::Unencrypted)),
    ] {
        let receive = ReceiveTuning {
            upgrade_passthrough: 0,
            downgrade_expiry,
            ..ReceiveTuning::default()
        };
        let mut call = group_with(&[BOT, A], receive);
        if closed_upgrade {
            for transition in [call.fake.downgrade(), call.fake.upgrade()] {
                call.enqueue(transition);
                call.run();
            }
            assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
        }
        call.fake.hold_transitions = true;
        let downgrade = call.fake.downgrade();
        call.enqueue(downgrade);
        call.run();

        check(call.dave_mut(user(BOT)), &[(A, OPUS, expected)]);
        assert!(call.failures.is_empty());
    }
}

#[test]
fn receive_recovery_waits_for_a_settled_group() {
    let grace = ReceiveTuning::default().dave_grace;
    let take = |call: &mut Call| {
        let fresh = Dave::new(user(BOT), channel(), &ReceiveTuning::default());
        (mem::replace(call.dave_mut(user(BOT)), fresh), call.now)
    };
    let (fresh, _) = started(1);
    let mut downgraded = group(&[BOT, A]);
    let downgrade = downgraded.fake.downgrade();
    downgraded.enqueue(downgrade);
    downgraded.run();
    let mut pending = group(&[BOT, A]);
    pending.now += grace;
    pending.fake.hold_transitions = true;
    pending.join(user(C));
    pending.run();
    let mut settled = group(&[BOT, A]);

    for (name, (mut dave, now), asks) in [
        ("fresh", (fresh, Instant::now()), false),
        ("downgraded", take(&mut downgraded), false),
        ("pending", take(&mut pending), false),
        ("settled", take(&mut settled), true),
    ] {
        let mut out = Vec::new();
        let asked = dave.receive_recovery(now + grace, &mut out).unwrap();
        assert_eq!(asked, asks, "{name}");
        assert_eq!(out.is_empty(), !asks, "{name}");
    }
}

#[test]
fn receive_recoveries_never_reach_the_recovery_limit() {
    let grace = ReceiveTuning::default().dave_grace;
    let mut call = group(&[BOT, A]);
    let mut out = Vec::new();

    for round in 0..=RECOVERY_LIMIT {
        let settling = call.now;
        assert!(
            !call
                .dave_mut(user(BOT))
                .receive_recovery(settling, &mut out)
                .unwrap(),
            "{round}"
        );
        call.now += grace;
        let settled = call.now;
        assert!(
            call.dave_mut(user(BOT))
                .receive_recovery(settled, &mut out)
                .unwrap(),
            "{round}"
        );
        assert!(matches!(
            out.as_slice(),
            [Outbound::InvalidCommitWelcome(_), Outbound::KeyPackage(_)]
        ));
        for message in out.drain(..) {
            call.receive(user(BOT), message);
        }
        call.run();
    }

    assert_eq!(
        sent_count(&call.sent_by(user(BOT)), is_flag),
        RECOVERY_LIMIT + 1
    );
    assert!(call.failures.is_empty());
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
    assert_eq!(hear(&mut call, A, BOT), OPUS);
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(48))]

    #[test]
    fn malformed_dave_frames_never_panic_the_decryptor(
        body in vec(any::<u8>(), 0..64),
        supplemental in vec(any::<u8>(), 0..24),
    ) {
        let mut call = group(&[BOT, A]);
        let size = u8::try_from(supplemental.len() + 3).unwrap();
        let frame = [body, supplemental, vec![size], MARKER.to_vec()].concat();
        let mut out = vec![0; frame.len()];

        let session = call.dave_mut(user(BOT)).session.as_mut().unwrap();
        let decrypted = session.decrypt_into(A, MediaType::AUDIO, &frame, &mut out);
        prop_assert_eq!(decrypted.unwrap_or(0), 0);
    }
}
