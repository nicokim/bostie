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

fn roster(dave: &Dave) -> Vec<u64> {
    let mut users: Vec<u64> = dave
        .leaves()
        .into_iter()
        .map(|(_, id)| u64::from(id))
        .collect();
    users.sort_unstable();
    users
}

fn recognized(dave: &Dave) -> Vec<u64> {
    let mut users = dave.recognized();
    users.sort_unstable();
    users
}

#[test]
fn send_mode_follows_the_protocol_version_the_members_and_the_key() {
    let (mut plain, _) = started(0);
    plain.clients_connect(&[user(A)]);
    let (alone, _) = started(1);
    let (mut waiting, _) = started(1);
    waiting.clients_connect(&[user(A)]);
    let call = group(&[BOT, A]);

    assert_eq!(plain.send_mode(), SendMode::Plain);
    assert_eq!(alone.send_mode(), SendMode::Discard);
    assert_eq!(waiting.send_mode(), SendMode::Hold);
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
}

#[test]
fn the_recognized_users_are_the_announced_ones_and_the_bot_itself() {
    let (mut dave, _) = started(1);
    dave.clients_connect(&[user(A), user(B), user(BOT)]);
    assert_eq!(recognized(&dave), [BOT, A, B]);
    assert_eq!(dave.send_mode(), SendMode::Hold);

    dave.client_disconnect(user(A));
    assert_eq!(recognized(&dave), [BOT, B]);
    dave.client_disconnect(user(B));
    assert_eq!(recognized(&dave), [BOT]);
    assert_eq!(dave.send_mode(), SendMode::Discard);
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
    assert_eq!(call.dave(user(BOT)).counters().key_packages, 1);
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
fn a_refused_external_sender_is_not_cached_and_changes_nothing() {
    let fake = ExternalSenderFake::new(channel(), 1);
    let (mut dave, _) = started(1);
    let mut out = Vec::new();
    let now = Instant::now();
    dave.handle(fake.external_sender(), now, &mut out).unwrap();
    let cached = dave.external_sender.clone();

    let garbage = DaveOp::ExternalSender(Bytes::from_static(b"not an external sender"));
    dave.handle(garbage, now, &mut out).unwrap();

    assert!(out.is_empty());
    assert_eq!(dave.external_sender, cached);
    assert_eq!(dave.counters().recoveries, 0);
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
        let counters = call.dave(user(BOT)).counters();
        assert_eq!(counters.commits, u64::from(peer == A));
        assert_eq!(counters.welcomes, u64::from(peer == EARLY));
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
        assert_eq!(call.dave(user(BOT)).counters().welcomes, 1);
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
fn a_sole_member_reset_drops_the_old_key_and_holds_until_the_new_group() {
    let mut call = group(&[BOT, A]);
    call.leave(user(A));
    call.run();

    assert!(matches!(
        call.sent_by(user(BOT)).as_slice(),
        [Outbound::KeyPackage(_)]
    ));
    assert_eq!(call.executed_by(user(BOT))[0].transition_id, 0);
    assert!(call.dave(user(BOT)).pending.is_empty());
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Discard);
    let mut out = Vec::new();
    assert_eq!(
        call.dave_mut(user(BOT)).encrypt(OPUS, &mut out),
        Err(EncryptFailure)
    );

    call.fake.hold_transitions = true;
    call.join(user(B));
    call.run();
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Hold);

    release(&mut call);
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
    assert_eq!(hear(&mut call, BOT, B), OPUS);
}

#[test]
fn peers_hear_the_bot_every_time_they_join_after_a_reset() {
    let mut call = call(1);
    call.join(user(BOT));
    call.run();
    for round in 0..3 {
        call.join(user(A));
        call.run();
        assert_eq!(hear(&mut call, BOT, A), OPUS, "round {round}");
        call.leave(user(A));
        call.run();
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Discard);
    }

    assert_eq!(call.dave(user(BOT)).counters().key_packages, 4);
    assert!(call.failures.is_empty());
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
    assert_eq!(call.dave(user(BOT)).counters().key_packages, 1);
    assert_eq!(ready_ids(&call.sent_by(user(BOT))).len(), 1);
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Plain);
    assert_eq!(call.dave(user(BOT)).info().privacy_code, None);

    release(&mut call);
    assert_eq!(call.executed_by(user(BOT))[0].protocol_version, 1);
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
    assert!(call.dave(user(BOT)).info().privacy_code.is_some());
    assert_eq!(hear(&mut call, BOT, A), OPUS);
}

#[test]
fn a_plain_stage_call_only_caches_the_external_sender() {
    let mut call = call(0);
    call.join(user(BOT));
    call.run();

    let dave = call.dave(user(BOT));
    assert!(call.sent.is_empty());
    assert_eq!(dave.send_mode(), SendMode::Plain);
    assert!(dave.session.is_none());
    assert!(dave.external_sender.is_some());
    assert_eq!(
        dave.info(),
        DaveInfo {
            protocol_version: 0,
            epoch: None,
            privacy_code: None,
        }
    );
}

#[test]
fn a_corrupt_commit_is_flagged_and_the_bot_is_added_again() {
    let mut call = group(&[BOT, A, B]);
    call.fake.corrupt_next(user(BOT), Corruption::Commit);
    call.join(user(C));
    call.run();

    let sent = call.sent_by(user(BOT));
    let flagged = sent
        .iter()
        .position(|message| matches!(message, Outbound::InvalidCommitWelcome(_)))
        .unwrap();
    assert!(matches!(sent[flagged + 1], Outbound::KeyPackage(_)));
    assert_eq!(call.dave(user(BOT)).counters().recoveries, 1);
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
    for peer in [A, B, C] {
        assert_eq!(hear(&mut call, BOT, peer), OPUS);
    }
}

#[test]
fn a_corrupt_welcome_is_flagged_and_the_bot_is_added_again() {
    let mut call = group(&[A, B]);
    call.fake.corrupt_next(user(BOT), Corruption::Welcome);
    call.join(user(BOT));
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
    assert_eq!(call.dave(user(BOT)).counters().recoveries, 1);
    assert_eq!(call.dave(user(BOT)).counters().welcomes, 1);
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
    assert_eq!(hear(&mut call, BOT, A), OPUS);
}

#[test]
fn more_recoveries_than_the_limit_within_the_window_end_the_session() {
    let (mut dave, _) = started(1);
    let now = Instant::now();
    let mut out = Vec::new();
    let corrupt = |transition_id| DaveOp::Welcome {
        transition_id,
        welcome: Bytes::from_static(b"not a welcome"),
    };

    for transition_id in 1..=u16::try_from(RECOVERY_LIMIT).unwrap() {
        dave.handle(corrupt(transition_id), now, &mut out).unwrap();
    }
    out.clear();
    let past_limit = dave.handle(corrupt(9), now + RECOVERY_WINDOW / 2, &mut out);

    assert!(matches!(past_limit, Err(DaveError::RecoveryLimit { .. })));
    assert!(out.is_empty());
}

#[test]
fn recoveries_older_than_the_window_stop_counting() {
    let (mut dave, _) = started(1);
    let mut now = Instant::now();
    let mut out = Vec::new();

    for transition_id in 1..=10 {
        let welcome = DaveOp::Welcome {
            transition_id,
            welcome: Bytes::from_static(b"not a welcome"),
        };
        dave.handle(welcome, now, &mut out).unwrap();
        now += RECOVERY_WINDOW;
    }

    assert_eq!(dave.counters().recoveries, 10);
    assert!(out.contains(&Outbound::InvalidCommitWelcome(10)));
}

#[test]
fn an_add_proposal_for_an_unannounced_user_is_rejected() {
    let mut call = group(&[BOT, A]);
    call.join_unannounced(user(STRANGER));
    call.run();

    assert!(
        !call
            .sent_by(user(BOT))
            .iter()
            .any(|message| matches!(message, Outbound::CommitWelcome(_)))
    );
    let counters = call.dave(user(BOT)).counters();
    assert_eq!(counters.rejected_proposals, 1);
    assert_eq!(counters.recoveries, 0);
    assert_eq!(roster(call.dave(user(BOT))), [BOT, A]);
    assert_eq!(hear(&mut call, BOT, A), OPUS);
}

#[test]
fn a_welcome_naming_an_unknown_user_is_refused_and_recovered() {
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
    assert_eq!(call.dave(user(BOT)).counters().welcomes, 0);
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

    assert_eq!(roster(call.dave(user(BOT))), [BOT, A, C]);
    assert_eq!(call.dave(user(BOT)).counters().rejected_proposals, 0);
    assert_eq!(hear(&mut call, BOT, C), OPUS);
    assert!(call.dave(user(BOT)).fingerprint_pair(user(B)).is_err());
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
    assert_eq!(dave.counters().recoveries, 0);
}

#[test]
fn a_commit_without_queued_proposals_in_an_established_group_recovers() {
    let mut call = group(&[BOT, A]);
    call.deliver(
        user(BOT),
        DaveOp::AnnounceCommit {
            transition_id: 9,
            commit: Bytes::from_static(b"a replayed commit"),
        },
    );
    call.run();

    let sent = call.sent_by(user(BOT));
    assert_eq!(sent[0], Outbound::InvalidCommitWelcome(9));
    assert!(matches!(sent[1], Outbound::KeyPackage(_)));
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
    assert_eq!(hear(&mut call, BOT, A), OPUS);
}

#[test]
fn after_a_recovery_the_bot_holds_until_its_re_add_executes() {
    let mut call = group(&[BOT, A]);
    call.fake.hold_transitions = true;
    call.deliver(
        user(BOT),
        DaveOp::AnnounceCommit {
            transition_id: 9,
            commit: Bytes::from_static(b"a replayed commit"),
        },
    );
    call.run();

    assert_eq!(call.dave(user(BOT)).counters().recoveries, 1);
    assert_eq!(ready_ids(&call.sent_by(user(BOT))).len(), 1);
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Hold);
    release(&mut call);
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
    assert_eq!(hear(&mut call, BOT, A), OPUS);
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

    release(&mut call);
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
fn receive_recovery_waits_for_the_group_and_never_disconnects() {
    let grace = ReceiveTuning::default().dave_grace;
    let mut out = Vec::new();
    let (mut fresh, _) = started(1);
    assert!(
        !fresh
            .receive_recovery(Instant::now() + grace, &mut out)
            .unwrap()
    );

    let mut downgraded = group(&[BOT, A]);
    let downgrade = downgraded.fake.downgrade();
    downgraded.enqueue(downgrade);
    downgraded.run();
    let later = downgraded.now + grace;
    assert!(
        !downgraded
            .dave_mut(user(BOT))
            .receive_recovery(later, &mut out)
            .unwrap()
    );
    assert!(out.is_empty());

    let mut joining = group(&[BOT, A]);
    joining.now += grace;
    joining.fake.hold_transitions = true;
    joining.join(user(C));
    joining.run();
    let later = joining.now + grace;
    assert!(
        !joining
            .dave_mut(user(BOT))
            .receive_recovery(later, &mut out)
            .unwrap()
    );
    assert!(out.is_empty());
    release(&mut joining);
    assert!(
        joining
            .dave_mut(user(BOT))
            .receive_recovery(later, &mut out)
            .unwrap()
    );
    out.clear();

    let mut call = group(&[BOT, A]);
    for round in 0..6 {
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

    let counters = call.dave(user(BOT)).counters();
    assert_eq!(counters.receive_recoveries, 6);
    assert_eq!(counters.recoveries, 0);
    assert!(call.failures.is_empty());
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
    assert_eq!(hear(&mut call, A, BOT), OPUS);
}

#[test]
fn a_commit_that_removes_the_bot_recovers_and_the_bot_is_added_again() {
    let mut call = group(&[BOT, A, B]);
    let before = call.dave(user(BOT)).counters();
    let kick = call.fake.kick(user(BOT));
    call.enqueue(kick);
    call.run();

    let sent = call.sent_by(user(BOT));
    let flagged = sent
        .iter()
        .position(|message| matches!(message, Outbound::InvalidCommitWelcome(_)))
        .unwrap();
    assert!(matches!(sent[flagged + 1], Outbound::KeyPackage(_)));
    let counters = call.dave(user(BOT)).counters();
    assert_eq!(counters.commits, before.commits + 1);
    assert_eq!(counters.recoveries, 1);
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
    assert_eq!(hear(&mut call, BOT, A), OPUS);
    assert_eq!(hear(&mut call, BOT, B), OPUS);
}

#[test]
fn a_protocol_change_inside_a_group_is_still_answered() {
    let (mut dave, _) = started(1);
    let mut out = Vec::new();
    let with_id = DaveOp::PrepareEpoch {
        transition_id: Some(4),
        epoch: 2,
        protocol_version: 1,
    };
    let without_id = DaveOp::PrepareEpoch {
        transition_id: None,
        epoch: 2,
        protocol_version: 1,
    };

    dave.handle(with_id, Instant::now(), &mut out).unwrap();
    assert_eq!(out, [Outbound::TransitionReady(4)]);
    out.clear();
    dave.handle(without_id, Instant::now(), &mut out).unwrap();
    assert!(out.is_empty());
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
fn a_failing_prepare_or_execute_takes_the_recover_path() {
    let prepare = DaveOp::PrepareTransition {
        transition_id: 3,
        protocol_version: 1,
    };
    for (call, op) in [
        (MlsCall::Prepare, prepare.clone()),
        (
            MlsCall::Execute,
            DaveOp::ExecuteTransition { transition_id: 3 },
        ),
    ] {
        let (mut dave, _) = started(1);
        let mut out = Vec::new();
        if call == MlsCall::Execute {
            dave.handle(prepare.clone(), Instant::now(), &mut out)
                .unwrap();
            out.clear();
        }
        dave.inject(call, Fault::Error);

        assert_eq!(dave.handle(op, Instant::now(), &mut out).unwrap(), None);
        assert!(matches!(
            out.as_slice(),
            [Outbound::InvalidCommitWelcome(3), Outbound::KeyPackage(_)]
        ));
        assert_eq!(dave.counters().recoveries, 1);
        assert_eq!(dave.counters().dave_panics, 0);
    }
}

#[test]
fn a_davey_panic_rebuilds_the_session_from_the_cached_sender() {
    let mut call = group(&[BOT, A]);
    let last_transition = call.dave(user(BOT)).last_transition;
    assert_ne!(last_transition, 0);
    call.dave_mut(user(BOT))
        .inject(MlsCall::Proposals, Fault::Panic);
    call.join(user(B));
    call.run();

    let counters = call.dave(user(BOT)).counters();
    assert_eq!(counters.dave_panics, 1);
    assert_eq!(counters.recoveries, 1);
    let sent = call.sent_by(user(BOT));
    assert_eq!(sent[0], Outbound::InvalidCommitWelcome(last_transition));
    assert!(matches!(sent[1], Outbound::KeyPackage(_)));
    assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
    assert_eq!(hear(&mut call, BOT, A), OPUS);
    assert_eq!(hear(&mut call, BOT, B), OPUS);
}

#[test]
fn prepared_transitions_are_forgotten_after_a_reinit_or_a_recovery() {
    let (mut dave, _) = started(1);
    let mut out = Vec::new();
    let now = Instant::now();
    let prepare = |transition_id| DaveOp::PrepareTransition {
        transition_id,
        protocol_version: 1,
    };

    dave.handle(prepare(3), now, &mut out).unwrap();
    assert!(dave.pending.contains_key(&3));
    let epoch = DaveOp::PrepareEpoch {
        transition_id: None,
        epoch: 1,
        protocol_version: 1,
    };
    dave.handle(epoch, now, &mut out).unwrap();
    assert!(dave.pending.is_empty());
    let execute = DaveOp::ExecuteTransition { transition_id: 3 };
    assert_eq!(dave.handle(execute, now, &mut out).unwrap(), None);

    dave.handle(prepare(4), now, &mut out).unwrap();
    let corrupt = DaveOp::Welcome {
        transition_id: 5,
        welcome: Bytes::from_static(b"not a welcome"),
    };
    dave.handle(corrupt, now, &mut out).unwrap();
    assert!(dave.pending.is_empty());
    let execute = DaveOp::ExecuteTransition { transition_id: 4 };
    assert_eq!(dave.handle(execute, now, &mut out).unwrap(), None);
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
        for op in [
            DaveOp::Proposals { operation: ProposalsOp::Append, data: bytes.clone() },
            DaveOp::Proposals { operation: ProposalsOp::Revoke, data: bytes.clone() },
            DaveOp::AnnounceCommit { transition_id, commit: bytes.clone() },
            DaveOp::ExternalSender(bytes),
        ] {
            let _ = dave.handle(op, now, &mut out);
        }
        prop_assert_eq!(dave.counters().dave_panics, 0);
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

        for operation in [ProposalsOp::Append, ProposalsOp::Revoke] {
            let proposals = DaveOp::Proposals { operation, data: bytes.clone() };
            dave.handle(proposals, now, &mut out).unwrap();
        }
        let commit = DaveOp::AnnounceCommit { transition_id, commit: bytes.clone() };
        dave.handle(commit, now, &mut out).unwrap();
        prop_assert!(out.contains(&Outbound::InvalidCommitWelcome(transition_id)));
        let welcome = DaveOp::Welcome { transition_id, welcome: bytes.clone() };
        let _ = dave.handle(welcome, now, &mut out);
        let _ = dave.handle(DaveOp::ExternalSender(bytes), now, &mut out);
        prop_assert_eq!(dave.counters().dave_panics, 0);
    }

    #[test]
    fn malformed_dave_frames_never_panic_the_decryptor(
        body in vec(any::<u8>(), 0..64),
        supplemental in vec(any::<u8>(), 0..24),
    ) {
        let mut call = group(&[BOT, A]);
        let size = u8::try_from(supplemental.len() + 3).unwrap();
        let frame = [body, supplemental, vec![size], MARKER.to_vec()].concat();
        let mut out = Vec::new();

        let heard = call.dave_mut(user(BOT)).receive(user(A), &frame, &mut out);
        prop_assert!(heard.is_err() && out.is_empty());
        prop_assert_eq!(call.dave(user(BOT)).counters().decrypt_panics, 0);
    }
}
