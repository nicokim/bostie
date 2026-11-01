use proptest::collection::vec;
use proptest::prelude::*;

use super::*;
use crate::voice::testkit::mls::{Call, Corruption, ExternalSenderFake, Signal};

const CHANNEL: u64 = 900;
const EARLY: u64 = 50;
const BOT: u64 = 100;
const A: u64 = 200;
const B: u64 = 300;
const C: u64 = 400;
const STRANGER: u64 = 500;
const OPUS: &[u8] = &[
    0xFC, 0xFF, 0xFE, 0x10, 0x32, 0x54, 0x76, 0x98, 0xBA, 0xDC, 0xFE,
];
const OPUS_ENDING_IN_MARKER: &[u8] = &[
    0xFC, 0xFF, 0xFE, 0x10, 0x32, 0x54, 0x76, 0x98, 0xBA, 0x00, 0xFA, 0xFA,
];

type Heard<'a> = (u64, &'a [u8], Result<&'a [u8], DecryptFailure>);

fn user(id: u64) -> UserId {
    UserId::from(id)
}

fn channel() -> ChannelId {
    ChannelId::from(CHANNEL)
}

fn call(protocol_version: u16) -> Call {
    crate::init_test_tracing();
    Call::new(channel(), protocol_version)
}

fn group(ids: &[u64]) -> Call {
    group_with(ids, ReceiveTuning::default())
}

fn group_with(ids: &[u64], receive: ReceiveTuning) -> Call {
    let mut call = call(1);
    call.receive = receive;
    for &id in ids {
        call.join(user(id));
        call.run();
    }
    let formed = if ids.len() > 1 {
        SendMode::Encrypt
    } else {
        SendMode::Discard
    };
    for &id in ids {
        assert_eq!(call.dave(user(id)).send_mode(), formed, "{id}");
    }
    assert!(call.failures.is_empty());
    call.sent.clear();
    call.executed.clear();
    call
}

fn release(call: &mut Call) {
    let held = call.fake.release_transitions();
    call.enqueue(held);
    call.run();
}

fn hear(call: &mut Call, speaker: u64, listener: u64) -> Vec<u8> {
    let frame = call.speak(user(speaker), OPUS);
    assert_ne!(frame.as_slice(), OPUS);
    let mut heard = Vec::new();
    call.dave_mut(user(listener))
        .receive(user(speaker), &frame, &mut heard)
        .unwrap();
    heard
}

fn check(dave: &mut Dave, cases: &[Heard<'_>]) {
    for &(from, frame, expected) in cases {
        let mut out = vec![0xEE; 2];
        let heard = dave.receive(user(from), frame, &mut out).map(|()| out);
        assert_eq!(heard, expected.map(<[u8]>::to_vec), "{from} {frame:02X?}");
    }
}

fn started(protocol_version: u16) -> (Dave, Vec<Outbound>) {
    crate::init_test_tracing();
    let mut dave = Dave::new(user(BOT), channel(), &ReceiveTuning::default());
    let mut out = Vec::new();
    dave.start(protocol_version, &mut out).unwrap();
    (dave, out)
}

fn ready_ids(messages: &[Outbound]) -> Vec<u16> {
    messages
        .iter()
        .filter_map(|message| match message {
            Outbound::TransitionReady(id) => Some(*id),
            _ => None,
        })
        .collect()
}

fn sent_count(messages: &[Outbound], wanted: fn(&Outbound) -> bool) -> usize {
    messages.iter().filter(|message| wanted(message)).count()
}

fn is_key_package(message: &Outbound) -> bool {
    matches!(message, Outbound::KeyPackage(_))
}

fn is_flag(message: &Outbound) -> bool {
    matches!(message, Outbound::InvalidCommitWelcome(_))
}

fn founder(call: &Call) -> UserId {
    call.fake.leaves()[0].1
}

#[test]
fn send_mode_follows_the_protocol_version_the_members_and_the_key() {
    let (mut plain, _) = started(0);
    plain.clients_connect(&[user(A)]);
    let (alone, _) = started(1);
    let (mut waiting, _) = started(1);
    waiting.clients_connect(&[user(A), user(B), user(BOT)]);
    let call = group(&[BOT, A]);

    assert_eq!(plain.send_mode(), SendMode::Plain);
    assert_eq!(alone.send_mode(), SendMode::Discard);
    assert_eq!(waiting.send_mode(), SendMode::Hold);
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
    waiting.client_disconnect(user(A));
    assert_eq!(waiting.send_mode(), SendMode::Hold);
    waiting.client_disconnect(user(B));
    assert_eq!(waiting.send_mode(), SendMode::Discard);
}

#[test]
fn alone_in_a_dave_call_the_bot_sends_a_key_package_and_discards_media() {
    let mut call = call(1);
    call.join(user(BOT));
    call.run();

    assert!(matches!(
        call.sent_by(user(BOT)).as_slice(),
        [Outbound::KeyPackage(_)]
    ));
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Discard);
    assert_eq!(
        call.dave(user(BOT)).info(),
        DaveInfo {
            protocol_version: 1,
            epoch: None,
            privacy_code: None,
        }
    );
    assert!(call.failures.is_empty());
}

#[test]
fn a_refused_external_sender_leaves_the_bot_to_join_by_welcome() {
    let mut call = call(1);
    call.join(user(BOT));
    call.run();
    let garbage = DaveOp::ExternalSender(Bytes::from_static(b"not an external sender"));
    call.deliver(user(BOT), garbage);
    call.run();

    call.join(user(A));
    call.run();

    assert!(matches!(
        call.sent_by(user(BOT)).as_slice(),
        [Outbound::KeyPackage(_), Outbound::TransitionReady(_)]
    ));
    assert_eq!(founder(&call), user(A));
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
    assert_eq!(hear(&mut call, BOT, A), OPUS);
}

#[test]
fn a_peer_joining_forms_a_group_and_hears_the_exact_opus_bytes() {
    for peer in [A, EARLY] {
        let mut call = call(1);
        call.join(user(BOT));
        call.run();
        call.join(user(peer));
        call.run();

        let sent = call.sent_by(user(BOT));
        assert_eq!(founder(&call) == user(BOT), peer == A);
        let ready = ready_ids(&sent);
        assert_eq!(ready.len(), 1);
        assert_ne!(ready[0], 0);
        assert_eq!(
            call.executed_by(user(BOT)),
            [Executed {
                protocol_version: 1,
                epoch: Some(1),
                transition_id: ready[0],
            }]
        );
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
        assert_eq!(hear(&mut call, BOT, peer), OPUS);
        assert_eq!(hear(&mut call, peer, BOT), OPUS);
    }
}

#[test]
fn joining_peers_waits_for_the_execute_before_encrypting() {
    for peers in [&[EARLY][..], &[A, B, C]] {
        let mut call = group(peers);
        call.fake.hold_transitions = true;
        call.join(user(BOT));
        call.run();

        let ready = ready_ids(&call.sent_by(user(BOT)));
        assert_eq!(ready.len(), 1);
        assert_ne!(ready[0], 0);
        assert_ne!(founder(&call), user(BOT));
        assert!(call.executed_by(user(BOT)).is_empty());
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Hold);

        release(&mut call);
        assert_eq!(call.executed_by(user(BOT))[0].transition_id, ready[0]);
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
        for &peer in peers {
            assert_eq!(hear(&mut call, BOT, peer), OPUS);
        }
    }
}

#[test]
fn a_transition_id_of_zero_executes_at_once_without_op_23() {
    for peer in [A, EARLY] {
        let mut call = call(1);
        call.join(user(BOT));
        call.run();
        call.fake.next_transition = 0;
        call.join(user(peer));
        call.run();

        assert!(ready_ids(&call.sent_by(user(BOT))).is_empty());
        assert!(ready_ids(&call.sent_by(user(peer))).is_empty());
        assert_eq!(call.executed_by(user(BOT))[0].transition_id, 0);
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
        assert_eq!(hear(&mut call, BOT, peer), OPUS);
    }
}

#[test]
fn leaving_members_rekey_and_a_sole_member_resets_until_the_next_group() {
    let mut call = group(&[BOT, A, B]);
    call.leave(user(B));
    call.run();
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
    assert_eq!(hear(&mut call, BOT, A), OPUS);
    call.dave(user(BOT)).fingerprint_pair(user(B)).unwrap_err();
    let sent = call.sent_by(user(BOT)).len();

    call.leave(user(A));
    call.run();

    assert!(matches!(
        call.sent_by(user(BOT))[sent..],
        [Outbound::KeyPackage(_)]
    ));
    assert_eq!(call.executed_by(user(BOT)).last().unwrap().transition_id, 0);
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Discard);
    let mut out = Vec::new();
    assert_eq!(
        call.dave_mut(user(BOT)).encrypt(OPUS, &mut out),
        Err(EncryptFailure)
    );

    call.fake.hold_transitions = true;
    call.join(user(C));
    call.run();
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Hold);

    release(&mut call);
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
    assert_eq!(hear(&mut call, BOT, C), OPUS);
}

#[test]
fn a_downgrade_keeps_encrypting_until_the_execute_then_sends_plain_opus() {
    let mut call = group(&[BOT, A]);
    call.fake.hold_transitions = true;
    let prepare = call.fake.downgrade();
    call.enqueue(prepare);
    call.run();

    let ready = ready_ids(&call.sent_by(user(BOT)));
    assert_eq!(ready.len(), 1);
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
    assert_eq!(hear(&mut call, BOT, A), OPUS);

    release(&mut call);
    let executed = call.executed_by(user(BOT));
    assert_eq!(executed[0].protocol_version, 0);
    assert_eq!(executed[0].transition_id, ready[0]);
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Plain);
    assert_eq!(call.dave(user(BOT)).info().privacy_code, None);
}

#[test]
fn an_upgrade_from_protocol_zero_stays_plain_until_it_executes() {
    let mut call = call(0);
    call.join(user(BOT));
    call.join(user(A));
    call.run();
    assert!(call.sent.is_empty());
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Plain);

    call.fake.hold_transitions = true;
    let prepare = call.fake.upgrade();
    call.enqueue(prepare);
    call.run();
    let sent = call.sent_by(user(BOT));
    assert_eq!(sent_count(&sent, is_key_package), 1);
    assert_eq!(ready_ids(&sent).len(), 1);
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Plain);
    assert_eq!(call.dave(user(BOT)).info().privacy_code, None);

    release(&mut call);
    assert_eq!(call.executed_by(user(BOT))[0].protocol_version, 1);
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
    assert!(call.dave(user(BOT)).info().privacy_code.is_some());
    assert_eq!(hear(&mut call, BOT, A), OPUS);
}

#[test]
fn a_plain_call_answers_no_mls_message() {
    let mut call = call(0);
    call.join(user(BOT));
    call.run();
    let garbage = Bytes::from_static(b"not mls at all");
    let ops = [
        DaveOp::ExternalSender(garbage.clone()),
        DaveOp::Proposals {
            operation: ProposalsOp::Append,
            data: garbage.clone(),
        },
        DaveOp::AnnounceCommit {
            transition_id: 3,
            commit: garbage.clone(),
        },
        DaveOp::Welcome {
            transition_id: 4,
            welcome: garbage,
        },
    ];

    let now = call.now;
    let dave = call.dave_mut(user(BOT));
    let mut out = Vec::new();
    for op in ops {
        assert_eq!(dave.handle(op, now, &mut out).unwrap(), None);
    }

    assert!(out.is_empty());
    assert!(call.sent.is_empty());
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Plain);
    assert_eq!(
        call.dave(user(BOT)).info(),
        DaveInfo {
            protocol_version: 0,
            epoch: None,
            privacy_code: None,
        }
    );
}

#[test]
fn a_refused_commit_or_welcome_is_flagged_and_the_bot_is_added_again() {
    fn corrupt_commit(call: &mut Call) {
        call.fake.corrupt_next(user(BOT), Corruption::Commit);
        call.join(user(C));
    }
    fn corrupt_welcome(call: &mut Call) {
        call.fake.corrupt_next(user(BOT), Corruption::Welcome);
        call.join(user(BOT));
    }
    fn replayed_commit(call: &mut Call) {
        let commit = Bytes::from_static(b"a replayed commit");
        let op = DaveOp::AnnounceCommit {
            transition_id: 9,
            commit,
        };
        call.deliver(user(BOT), op);
    }
    fn replayed_welcome(call: &mut Call) {
        let welcome = Bytes::from_static(b"a replayed welcome");
        let op = DaveOp::Welcome {
            transition_id: 9,
            welcome,
        };
        call.deliver(user(BOT), op);
    }
    fn removing_the_bot(call: &mut Call) {
        let kick = call.fake.kick(user(BOT));
        call.enqueue(kick);
    }
    type Case = (&'static str, &'static [u64], fn(&mut Call));
    let cases: [Case; 5] = [
        ("corrupt commit", &[BOT, A, B], corrupt_commit),
        ("corrupt welcome", &[A, B], corrupt_welcome),
        ("replayed commit", &[BOT, A], replayed_commit),
        ("replayed welcome", &[BOT, A], replayed_welcome),
        ("commit removing the bot", &[BOT, A, B], removing_the_bot),
    ];

    for (name, members, trigger) in cases {
        let mut call = group(members);
        call.fake.hold_transitions = true;
        trigger(&mut call);
        call.run();
        let sent = call.sent_by(user(BOT));
        let flagged = sent
            .iter()
            .position(|message| matches!(message, Outbound::InvalidCommitWelcome(_)))
            .unwrap();
        let held = call.dave(user(BOT)).send_mode();
        release(&mut call);

        assert!(
            matches!(sent[flagged + 1], Outbound::KeyPackage(_)),
            "{name}"
        );
        assert_eq!(sent_count(&sent, is_flag), 1, "{name}");
        assert_eq!(held, SendMode::Hold, "{name}");
        assert_eq!(
            call.dave(user(BOT)).send_mode(),
            SendMode::Encrypt,
            "{name}"
        );
        let peers: Vec<u64> = call
            .fake
            .members()
            .into_iter()
            .map(u64::from)
            .filter(|&id| id != BOT)
            .collect();
        for peer in peers {
            assert_eq!(hear(&mut call, BOT, peer), OPUS, "{name} {peer}");
        }
    }
}

#[test]
fn recoveries_count_toward_the_limit_for_exactly_the_window() {
    let corrupt = |transition_id| DaveOp::Welcome {
        transition_id,
        welcome: Bytes::from_static(b"not a welcome"),
    };
    let limit = u16::try_from(RECOVERY_LIMIT).unwrap();

    for (after, flagged) in [
        (RECOVERY_WINDOW / 2, false),
        (
            RECOVERY_WINDOW.saturating_sub(Duration::from_nanos(1)),
            false,
        ),
        (RECOVERY_WINDOW, true),
    ] {
        let (mut dave, _) = started(1);
        let now = Instant::now();
        let mut out = Vec::new();
        for transition_id in 1..=limit {
            dave.handle(corrupt(transition_id), now, &mut out).unwrap();
        }
        out.clear();

        let next = dave.handle(corrupt(limit + 1), now + after, &mut out);

        if flagged {
            assert!(next.is_ok(), "{after:?}");
            assert!(out.contains(&Outbound::InvalidCommitWelcome(limit + 1)));
        } else {
            assert!(
                matches!(next, Err(DaveError::RecoveryLimit { .. })),
                "{after:?}"
            );
            assert!(out.is_empty(), "{after:?}");
        }
    }
}

#[test]
fn an_add_proposal_for_an_unannounced_user_is_rejected() {
    let mut call = group(&[BOT, A]);
    call.join_unannounced(user(STRANGER));
    call.run();

    let sent = call.sent_by(user(BOT));
    assert!(
        !sent
            .iter()
            .any(|message| matches!(message, Outbound::CommitWelcome(_)))
    );
    assert_eq!(sent_count(&sent, is_flag), 0);
    call.dave(user(BOT))
        .fingerprint_pair(user(STRANGER))
        .unwrap_err();
    assert_eq!(hear(&mut call, BOT, A), OPUS);
}

#[test]
fn a_welcome_naming_a_user_who_left_is_refused_until_the_recovery_limit() {
    let mut call = group(&[A, B]);
    call.join(user(BOT));
    call.enqueue(vec![(user(BOT), Signal::ClientDisconnect(user(B)))]);
    call.run();

    let sent = call.sent_by(user(BOT));
    assert!(matches!(
        sent[..3],
        [
            Outbound::KeyPackage(_),
            Outbound::InvalidCommitWelcome(_),
            Outbound::KeyPackage(_)
        ]
    ));
    assert!(ready_ids(&sent).is_empty());
    assert!(matches!(
        call.failures.as_slice(),
        [(failed, DaveError::RecoveryLimit { .. })] if *failed == user(BOT)
    ));
}

#[test]
fn revoked_proposals_are_left_out_of_the_next_commit() {
    let mut call = group(&[BOT, A]);
    call.fake.hold_commits = true;
    call.join(user(B));
    call.join(user(C));
    call.run();
    let revoke = call.fake.revoke(user(B));
    call.enqueue(revoke);
    call.run();
    call.fake.hold_commits = false;
    call.run();

    call.dave(user(BOT)).fingerprint_pair(user(A)).unwrap();
    assert_eq!(hear(&mut call, BOT, C), OPUS);
    call.dave(user(BOT)).fingerprint_pair(user(B)).unwrap_err();
}

#[test]
fn a_commit_for_a_pending_group_is_ignored() {
    let fake = ExternalSenderFake::new(channel(), 1);
    let (mut dave, _) = started(1);
    let mut out = Vec::new();
    dave.handle(fake.external_sender(), Instant::now(), &mut out)
        .unwrap();
    let commit = DaveOp::AnnounceCommit {
        transition_id: 5,
        commit: Bytes::from_static(b"a commit for someone else"),
    };

    assert_eq!(dave.handle(commit, Instant::now(), &mut out).unwrap(), None);
    assert!(out.is_empty());
}

#[test]
fn an_execute_for_an_unknown_transition_is_ignored() {
    let mut call = group(&[BOT, A]);
    call.deliver(user(BOT), DaveOp::ExecuteTransition { transition_id: 77 });
    call.run();

    assert!(call.sent.is_empty());
    assert!(call.executed.is_empty());
    assert_eq!(hear(&mut call, BOT, A), OPUS);
}

#[test]
fn frames_between_commit_and_execute_use_the_old_key() {
    let mut call = group(&[BOT, A]);
    call.fake.hold_transitions = true;
    call.join(user(B));
    call.run();
    assert_eq!(ready_ids(&call.sent_by(user(BOT))).len(), 1);
    assert_eq!(hear(&mut call, BOT, A), OPUS);
    let in_flight = call.speak(user(BOT), OPUS);

    release(&mut call);
    let mut heard = Vec::new();
    call.dave_mut(user(A))
        .receive(user(BOT), &in_flight, &mut heard)
        .unwrap();

    assert_eq!(heard, OPUS);
    assert_eq!(hear(&mut call, BOT, A), OPUS);
    assert_eq!(hear(&mut call, BOT, B), OPUS);
}

#[test]
fn the_privacy_code_appears_at_the_prepare_and_matches_the_peer() {
    let mut call = call(1);
    call.join(user(BOT));
    call.run();
    assert_eq!(call.dave(user(BOT)).info().privacy_code, None);

    call.fake.hold_transitions = true;
    call.join(user(A));
    call.run();
    let prepared = call.dave(user(BOT)).info();
    assert!(prepared.privacy_code.is_some());
    assert_eq!(prepared, call.dave(user(A)).info());

    release(&mut call);
    assert_eq!(call.dave(user(BOT)).info(), prepared);
    assert_eq!(prepared.epoch, Some(1));
}

#[test]
fn the_bot_and_the_peer_compute_the_same_verification_code() {
    let call = group(&[BOT, A]);
    let ours = verification_code(call.dave(user(BOT)).fingerprint_pair(user(A)).unwrap()).unwrap();
    let theirs =
        verification_code(call.dave(user(A)).fingerprint_pair(user(BOT)).unwrap()).unwrap();

    assert_eq!(ours, theirs);
    assert_eq!(ours.len(), 45);
    assert!(ours.bytes().all(|digit| digit.is_ascii_digit()));
    assert!(matches!(
        call.dave(user(BOT)).fingerprint_pair(user(STRANGER)),
        Err(DaveError::Verification(_))
    ));
}

#[test]
fn the_silence_frame_is_never_encrypted_into_plaintext() {
    let mut call = group(&[BOT, A]);
    let mut out = Vec::new();

    assert_eq!(
        call.dave_mut(user(BOT)).encrypt(&SILENCE_FRAME, &mut out),
        Err(EncryptFailure)
    );
    assert!(out.is_empty());
}

#[test]
fn nothing_is_encrypted_before_a_group() {
    let (mut alone, _) = started(1);
    let mut stage = Dave::new(user(BOT), channel(), &ReceiveTuning::default());
    let mut out = Vec::new();

    assert_eq!(alone.encrypt(OPUS, &mut out), Err(EncryptFailure));
    assert_eq!(stage.encrypt(OPUS, &mut out), Err(EncryptFailure));
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

#[test]
fn transition_ops_follow_the_epoch_the_version_and_the_id() {
    let epoch = |transition_id, epoch, protocol_version| DaveOp::PrepareEpoch {
        transition_id,
        epoch,
        protocol_version,
    };
    for (op, answered) in [
        (epoch(Some(4), 2, 1), vec![Outbound::TransitionReady(4)]),
        (epoch(None, 2, 1), vec![]),
        (epoch(Some(4), 1, 0), vec![]),
    ] {
        let (mut dave, _) = started(1);
        let mut out = Vec::new();

        assert_eq!(dave.handle(op, Instant::now(), &mut out).unwrap(), None);
        assert_eq!(out, answered);
    }
    let (mut dave, _) = started(1);
    let unsupported = dave.handle(epoch(None, 1, 2), Instant::now(), &mut Vec::new());
    assert!(matches!(unsupported, Err(DaveError::Session(_))));

    let mut call = group(&[BOT, A]);
    let now = call.now;
    let mut out = Vec::new();
    let immediate = DaveOp::PrepareTransition {
        transition_id: 0,
        protocol_version: 0,
    };
    let executed = call
        .dave_mut(user(BOT))
        .handle(immediate, now, &mut out)
        .unwrap()
        .unwrap();
    assert!(out.is_empty());
    assert_eq!((executed.protocol_version, executed.transition_id), (0, 0));
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Plain);
}

#[test]
fn a_prepare_on_a_call_without_a_session_is_still_answered() {
    let (mut dave, _) = started(0);
    let mut out = Vec::new();
    let prepare = DaveOp::PrepareTransition {
        transition_id: 6,
        protocol_version: 0,
    };

    assert_eq!(
        dave.handle(prepare, Instant::now(), &mut out).unwrap(),
        None
    );
    assert_eq!(out, [Outbound::TransitionReady(6)]);
    out.clear();
    let execute = DaveOp::ExecuteTransition { transition_id: 6 };
    assert_eq!(
        dave.handle(execute, Instant::now(), &mut out).unwrap(),
        None
    );
    let immediate = DaveOp::PrepareTransition {
        transition_id: 0,
        protocol_version: 0,
    };
    assert_eq!(
        dave.handle(immediate, Instant::now(), &mut out).unwrap(),
        None
    );
    assert!(out.is_empty());
}

#[test]
fn a_davey_panic_drops_the_session_and_rebuilds_it_from_the_cached_sender() {
    let mut call = group(&[BOT, A]);
    let now = call.now;
    let dave = call.dave_mut(user(BOT));
    let last_transition = dave.last_transition;
    let failure = dave
        .attempt(MlsCall::Proposals, |_| -> Result<(), String> {
            panic!("a davey bug")
        })
        .unwrap_err();
    let dropped = dave.session.is_none();
    let mut out = Vec::new();
    dave.failed(MlsCall::Proposals, &failure, last_transition, now, &mut out)
        .unwrap();
    for message in out {
        call.receive(user(BOT), message);
    }
    call.run();

    assert!(dropped);
    assert_ne!(last_transition, 0);
    let sent = call.sent_by(user(BOT));
    assert_eq!(sent[0], Outbound::InvalidCommitWelcome(last_transition));
    assert!(matches!(sent[1], Outbound::KeyPackage(_)));
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
    assert_eq!(hear(&mut call, BOT, A), OPUS);
}

#[test]
fn prepared_transitions_are_forgotten_after_a_reinit_or_a_recovery() {
    let reinit = DaveOp::PrepareEpoch {
        transition_id: None,
        epoch: 1,
        protocol_version: 1,
    };
    let recovery = DaveOp::Welcome {
        transition_id: 5,
        welcome: Bytes::from_static(b"not a welcome"),
    };
    for (interruption, executes) in [(None, true), (Some(reinit), false), (Some(recovery), false)] {
        let (mut dave, _) = started(1);
        let mut out = Vec::new();
        let now = Instant::now();
        let prepare = DaveOp::PrepareTransition {
            transition_id: 3,
            protocol_version: 1,
        };
        dave.handle(prepare, now, &mut out).unwrap();
        if let Some(op) = interruption {
            dave.handle(op, now, &mut out).unwrap();
        }

        let execute = DaveOp::ExecuteTransition { transition_id: 3 };
        let executed = dave.handle(execute, now, &mut out).unwrap();

        assert_eq!(executed.is_some(), executes, "{executed:?}");
    }
}

fn feed_davey(dave: &mut Dave, bytes: &[u8]) {
    let recognized = dave.recognized();
    let session = dave.session.as_mut().unwrap();
    for operation in [
        ProposalsOperationType::APPEND,
        ProposalsOperationType::REVOKE,
    ] {
        drop(session.process_proposals(operation, bytes, Some(&recognized)));
    }
    drop(session.process_commit(bytes));
    drop(session.process_welcome(bytes, Some(&recognized)));
    drop(session.set_external_sender(bytes));
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(48))]

    #[test]
    fn malformed_mls_bytes_never_panic_a_pending_session(
        bytes in vec(any::<u8>(), 0..300),
        transition_id in 1..u16::MAX,
    ) {
        let fake = ExternalSenderFake::new(channel(), 1);
        let (mut dave, _) = started(1);
        let mut out = Vec::new();
        let now = Instant::now();
        dave.handle(fake.external_sender(), now, &mut out).unwrap();
        let bytes = Bytes::from(bytes);

        let welcome = DaveOp::Welcome { transition_id, welcome: bytes.clone() };
        dave.handle(welcome, now, &mut out).unwrap();
        prop_assert!(out.contains(&Outbound::InvalidCommitWelcome(transition_id)));
        feed_davey(&mut dave, &bytes);
    }

    #[test]
    fn malformed_mls_bytes_never_panic_an_established_group(
        bytes in vec(any::<u8>(), 0..300),
        transition_id in 1..u16::MAX,
    ) {
        let mut call = group(&[BOT, A]);
        let bytes = Bytes::from(bytes);
        let now = call.now;
        let dave = call.dave_mut(user(BOT));
        let mut out = Vec::new();

        feed_davey(dave, &bytes);
        let commit = DaveOp::AnnounceCommit { transition_id, commit: bytes };
        dave.handle(commit, now, &mut out).unwrap();
        prop_assert!(out.contains(&Outbound::InvalidCommitWelcome(transition_id)));
    }

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
