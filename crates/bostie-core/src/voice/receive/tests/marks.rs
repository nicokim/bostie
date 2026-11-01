use super::*;
use crate::voice::types::MAX_JITTER;

#[test]
fn a_mark_waits_only_while_audio_heard_before_it_is_in_the_jitter_buffer() {
    let mut rig = Rig::new(tuning());
    rig.map(ALICE_SSRC, ALICE);
    let receiver = rig.open(ReceiveOptions {
        jitter: MAX_JITTER,
        ..options(ReceiveFormat::Opus)
    });
    let idle = receiver.mark().unwrap();
    rig.tick();
    let unheard = rig.marks();
    let heard = Instant::now();
    rig.media.arrive(|_| Arrival {
        at: heard,
        ..rig.arrival(ALICE_SSRC, 0, &opus(1, 0.3)[0])
    });
    let waiting = receiver.mark().unwrap();

    rig.now = heard + MAX_JITTER.saturating_sub(Duration::from_millis(1));
    rig.tick();
    let buffered = (read(&receiver).len(), rig.marks());
    rig.now = heard + MAX_JITTER;
    rig.tick();
    let released = (read(&receiver).len(), rig.marks());
    let ending = receiver.mark().unwrap();
    receiver.end(Ended::Disconnected);
    let ended = receiver.mark().unwrap();

    assert_eq!(unheard, [(idle, true)]);
    assert_eq!(buffered, (0, Vec::new()));
    assert_eq!(released, (1, vec![(waiting, true)]));
    assert_eq!(rig.marks(), [(ending, false), (ended, false)]);
}

#[test]
fn a_mark_waits_for_audio_parked_until_its_ssrc_is_mapped() {
    let mut rig = Rig::new(ReceiveTuning {
        hold: LATER,
        ..tuning()
    });
    let receiver = rig.open(options(ReceiveFormat::Packets));
    let heard = Instant::now();
    rig.media.arrive(|_| Arrival {
        at: heard,
        ..rig.arrival(ALICE_SSRC, 0, &SILENCE_FRAME)
    });
    let mark = receiver.mark().unwrap();

    rig.step();
    let parked = (read(&receiver).len(), rig.marks());
    rig.map(ALICE_SSRC, ALICE);
    rig.step();
    let mapped = (read(&receiver).len(), rig.marks());

    assert_eq!(parked, (0, Vec::new()));
    assert_eq!(mapped, (1, vec![(mark, true)]));
}

#[test]
fn a_mark_gives_up_once_audio_heard_before_it_outstays_the_bound() {
    let mut rig = Rig::new(tuning());
    rig.map(ALICE_SSRC, ALICE);
    let receiver = rig.open(options(ReceiveFormat::Opus));
    let heard = Instant::now();
    rig.media.arrive(|_| Arrival {
        at: heard,
        ..rig.arrival(ALICE_SSRC, 0, &opus(1, 0.3)[0])
    });
    let mark = receiver.mark().unwrap();
    let marked = Instant::now();
    let bound = rig.reception.core.tuning.hold + Duration::from_millis(200);
    let starved = rig.reception.core.epoch;

    rig.now = heard + bound.saturating_sub(Duration::from_millis(1));
    rig.run(0, starved);
    let waiting = rig.marks();
    rig.now = marked + bound;
    rig.run(0, starved);

    assert_eq!(waiting, []);
    assert_eq!(
        (read(&receiver).len(), rig.marks()),
        (0, vec![(mark, false)])
    );
}

#[test]
fn a_mark_waits_for_the_audio_heard_before_it_to_leave_the_mix() {
    let mut rig = Rig::new(tuning());
    rig.map(ALICE_SSRC, ALICE);
    let receiver = rig.open(options(mixed()));
    let packets = opus(7, 0.3);
    let heard = Instant::now();
    for (sequence, packet) in (0..4).zip(&packets) {
        rig.media.arrive(|_| Arrival {
            at: heard,
            ..rig.arrival(ALICE_SSRC, sequence, packet)
        });
    }
    rig.step();
    let mark = receiver.mark().unwrap();
    let after = Instant::now() + Duration::from_millis(1);

    let mut ticks = Vec::new();
    for (sequence, packet) in (4..).zip(&packets[4..]) {
        rig.media.arrive(|_| Arrival {
            at: after,
            ..rig.arrival(ALICE_SSRC, sequence, packet)
        });
        rig.step();
        ticks.push(rig.marks());
    }

    assert_eq!(read(&receiver).len(), 4);
    assert_eq!(ticks, [vec![], vec![], vec![(mark, true)]]);
}

#[test]
fn a_lost_transport_drops_the_mixed_audio_left_and_passes_its_marks() {
    let mut rig = Rig::new(tuning());
    rig.map(ALICE_SSRC, ALICE);
    let receiver = rig.open(options(mixed()));
    let heard = Instant::now();
    for (sequence, packet) in (0..3).zip(&opus(3, 0.3)) {
        rig.media.arrive(|_| Arrival {
            at: heard,
            ..rig.arrival(ALICE_SSRC, sequence, packet)
        });
    }

    rig.step();
    rig.media.install(None);
    let mark = receiver.mark().unwrap();
    rig.step();

    assert_eq!(
        (read(&receiver).len(), rig.marks()),
        (1, vec![(mark, true)])
    );
}

#[test]
fn a_mark_waits_for_a_dave_held_frame_only_if_its_receiver_takes_it() {
    let mut rig = Rig::new(ReceiveTuning {
        hold: LATER,
        ..tuning()
    });
    let mut call = rig.group(&[ALICE]);
    rig.map(ALICE_SSRC, ALICE);
    let alice = rig.open(options(ReceiveFormat::Opus));
    let bob = rig.open(only(&[BOB], ReceiveFormat::Opus));
    join_behind_the_bot(&mut call, BOB);
    let frame = call.speak(user(ALICE), &opus(1, 0.3)[0]);
    let heard = Instant::now();
    rig.media.arrive(|_| Arrival {
        at: heard,
        ..rig.arrival(ALICE_SSRC, 0, &frame)
    });
    rig.step();
    let marks = [alice.mark().unwrap(), bob.mark().unwrap()];

    rig.step();
    let held = (read(&alice).len(), rig.marks());
    rig.deliver(&mut call);
    rig.step();
    let released = (read(&alice).len(), rig.marks());

    assert_eq!(held, (0, vec![(marks[1], true)]));
    assert_eq!(released, (1, vec![(marks[0], true)]));
}
