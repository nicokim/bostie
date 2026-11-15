use bytes::BytesMut;

use super::*;
use crate::voice::codec::OpusDecoder;

#[derive(Debug, Clone, PartialEq, Eq)]
enum Heard {
    Frame {
        user: Option<u64>,
        timestamp: Option<u32>,
        offset: u64,
        lost: bool,
    },
    Packet {
        sequence: u16,
        opus: Bytes,
        extensions: Vec<(u8, Bytes)>,
    },
    Speech {
        speaking: bool,
        offset: Option<u64>,
    },
    Leave {
        user: u64,
        offset: Option<u64>,
    },
}

fn heard(items: &[Item]) -> Vec<Heard> {
    items
        .iter()
        .map(|item| match item {
            Item::Frame {
                user,
                timestamp,
                offset,
                lost,
                ..
            } => Heard::Frame {
                user: user.map(u64::from),
                timestamp: *timestamp,
                offset: *offset,
                lost: *lost,
            },
            Item::Packet {
                sequence,
                opus,
                extensions,
                ..
            } => Heard::Packet {
                sequence: *sequence,
                opus: opus.clone(),
                extensions: extensions.clone(),
            },
            Item::Speech {
                speaking, offset, ..
            } => Heard::Speech {
                speaking: *speaking == Speaking::Started,
                offset: *offset,
            },
            Item::Leave { user, offset } => Heard::Leave {
                user: u64::from(*user),
                offset: *offset,
            },
        })
        .collect()
}

#[test]
fn the_playout_conceals_losses_and_skips_gaps_in_each_format() {
    let gaps: [&[u16]; 12] = [
        &[0],
        &[1],
        &[],
        &[3],
        &[],
        &[],
        &[],
        &[],
        &[],
        &[],
        &[10],
        &[11],
    ];
    let skipped = [
        (0, false),
        (960, false),
        (1_920, true),
        (2_880, false),
        (9_600, false),
        (10_560, false),
    ];
    let pcm16k = pcm(16_000, 1, SampleFormat::S16Le);
    for format in [ReceiveFormat::Opus, pcm16k] {
        let mut rig = Rig::new(tuning());
        rig.map(ALICE_SSRC, ALICE);
        let receiver = rig.open(options(format));
        let packets = opus(12, 0.3);
        for sequences in gaps {
            for &sequence in sequences {
                rig.send(ALICE_SSRC, sequence, &packets[usize::from(sequence)]);
            }
            rig.step();
        }

        let items = read(&receiver);
        let played: Vec<(u32, bool)> = heard(&items)
            .into_iter()
            .map(|item| match item {
                Heard::Frame {
                    timestamp: Some(timestamp),
                    lost,
                    ..
                } => (timestamp, lost),
                other => panic!("{other:?}"),
            })
            .collect();
        let lengths: Vec<usize> = data(&items).iter().map(Bytes::len).collect();
        assert_eq!(played, skipped, "{format:?}");
        assert_eq!(receiver.stats().unwrap().lost, 7, "{format:?}");
        if format == pcm16k {
            assert!(lengths.iter().all(|&length| length == 640));
            let mut fresh = OpusDecoder::new(16_000, 1).unwrap();
            let mut scratch = Vec::new();
            let samples = fresh.decode(&packets[10], &mut scratch).unwrap();
            let mut after_the_skip = BytesMut::new();
            codec::write_samples(
                &scratch[..samples],
                SampleFormat::S16Le,
                &mut after_the_skip,
            );
            assert_eq!(data(&items)[4], after_the_skip.freeze());
        } else {
            let silent: Vec<bool> = lengths.iter().map(|&length| length == 0).collect();
            let concealed: Vec<bool> = skipped.iter().map(|&(_, lost)| lost).collect();
            assert_eq!(silent, concealed);
        }
    }
}

fn floats(items: &[Item]) -> Vec<f32> {
    data(items)
        .iter()
        .flat_map(|frame| {
            frame
                .as_chunks::<4>()
                .0
                .iter()
                .map(|bytes| f32::from_le_bytes(*bytes))
                .collect::<Vec<_>>()
        })
        .collect()
}

fn offsets(items: &[Item]) -> Vec<u64> {
    items
        .iter()
        .filter_map(|item| match item {
            Item::Frame { offset, user, .. } => {
                assert!(user.is_none());
                Some(*offset)
            }
            _ => None,
        })
        .collect()
}

#[test]
fn a_mix_sums_users_and_soft_clips() {
    let mut rig = Rig::new(tuning());
    rig.map(ALICE_SSRC, ALICE);
    rig.map(BOB_SSRC, BOB);
    let receiver = rig.open(options(mixed()));
    let (quiet, loud) = (opus(3, 0.2), opus(2, 0.9));
    let decoded = |packets: &[Bytes]| -> Vec<f32> {
        let mut decoder = OpusDecoder::new(48_000, 2).unwrap();
        let mut pcm = Vec::new();
        for packet in packets {
            let mut scratch = Vec::new();
            let samples = decoder.decode(packet, &mut scratch).unwrap();
            pcm.extend_from_slice(&scratch[..samples * 2]);
        }
        pcm
    };
    let both = |rig: &mut Rig, first: u16, packets: &[Bytes]| {
        for (sequence, packet) in (first..).zip(packets) {
            rig.send(ALICE_SSRC, sequence, packet);
            rig.send(BOB_SSRC, sequence, packet);
            rig.step();
        }
        read(&receiver)
    };

    let summed = both(&mut rig, 0, &quiet);
    let clipped = both(&mut rig, 3, &loud);

    let expected: Vec<f32> = decoded(&quiet).iter().map(|sample| sample * 2.0).collect();
    assert_eq!(floats(&summed), expected);
    let loud_sum = decoded(&loud)
        .iter()
        .map(|sample| sample * 2.0)
        .fold(0.0, f32::max);
    assert!(loud_sum > 1.0, "{loud_sum}");
    assert!(floats(&clipped).iter().all(|sample| sample.abs() <= 1.0));
    assert_eq!(offsets(&[summed, clipped].concat()).len(), 5);
}

#[test]
fn a_mix_keeps_one_clock_through_skipped_ticks_silence_and_a_reconnect() {
    let mut rig = Rig::new(tuning());
    rig.map(ALICE_SSRC, ALICE);
    let receiver = rig.open(options(mixed()));
    let quiet = opus(6, 0.2);

    for (sequence, packet) in (0..3).zip(&quiet) {
        rig.send(ALICE_SSRC, sequence, packet);
        rig.step();
    }
    let started = read(&receiver);
    for (sequence, packet) in (3..6).zip(&quiet[3..]) {
        rig.send(ALICE_SSRC, sequence, packet);
        rig.now += FRAME;
    }
    rig.run(2, rig.now + LATER);
    let late = read(&receiver);
    rig.step();
    let silent = read(&receiver);
    for (sequence, packet) in (6..).zip(&opus(10, 0.2)) {
        rig.send(ALICE_SSRC, sequence, packet);
    }
    rig.step();
    let dropped = receiver.stats().unwrap().dropped;
    let flooded = read(&receiver);
    rig.media.install(None);
    rig.step();
    let reconnecting = read(&receiver);
    rig.now += FRAME * 5;
    rig.install(2);
    rig.step();
    let returned = read(&receiver);
    rig.send(ALICE_SSRC, 100, &quiet[0]);
    rig.send(ALICE_SSRC, 102, &quiet[2]);
    for _ in 0..3 {
        rig.step();
    }
    let concealed: Vec<bool> = heard(&read(&receiver))
        .into_iter()
        .map(|item| matches!(item, Heard::Frame { lost: true, .. }))
        .collect();

    assert_eq!((started.len(), late.len(), silent.len()), (3, 3, 1));
    assert!(floats(&silent).iter().all(|&sample| sample == 0.0));
    let offsets = offsets(&[started, late, silent, flooded].concat());
    let contiguous: Vec<u64> = (0..offsets.len() as u64)
        .map(|index| offsets[0] + index * 960)
        .collect();
    assert_eq!(offsets, contiguous);
    assert_eq!(dropped, 5);
    assert!(reconnecting.is_empty());
    let resumed = heard(&returned);
    assert_eq!(resumed.len(), 1);
    let Heard::Frame { offset, .. } = resumed[0] else {
        panic!("{resumed:?}");
    };
    let before_the_gap = offsets.last().unwrap() + 960;
    assert!(offset >= before_the_gap + 960 * 5, "{offset}");
    assert_eq!(offset % 960, 0);
    assert_eq!(concealed, [false, true, false]);
}

#[test]
fn in_band_markers_bracket_each_users_frames_on_the_receivers_timeline() {
    let formats = [
        pcm(16_000, 1, SampleFormat::S16Le),
        ReceiveFormat::Opus,
        ReceiveFormat::Packets,
    ];
    let slips = [Duration::ZERO, Duration::from_millis(50)];
    for (format, slip) in formats.into_iter().flat_map(|f| slips.map(|s| (f, s))) {
        let mut rig = Rig::new(tuning());
        rig.media.set_speech_hangover(Duration::from_millis(100));
        rig.media.set_speech_activity(false);
        rig.map(ALICE_SSRC, ALICE);
        let receiver = rig.open(ReceiveOptions {
            markers: true,
            jitter: Duration::from_millis(200),
            ..options(format)
        });
        let mut packets = opus(9, 0.3);
        packets.push(Bytes::from_static(&SILENCE_FRAME));
        for (sequence, packet) in (0..).zip(&packets) {
            rig.now += slip;
            rig.send(ALICE_SSRC, sequence, packet);
            rig.step();
        }
        for _ in 0..20 {
            rig.step();
        }

        let items = read(&receiver);
        let heard = heard(&items);
        assert!(rig.speech().is_empty(), "{format:?} {slip:?}");
        assert_eq!(heard.len(), 12, "{format:?} {slip:?}");
        if format == ReceiveFormat::Packets {
            assert_eq!(
                heard[0],
                Heard::Speech {
                    speaking: true,
                    offset: None
                }
            );
            assert_eq!(
                heard[11],
                Heard::Speech {
                    speaking: false,
                    offset: None
                }
            );
            assert!(
                heard[1..11]
                    .iter()
                    .all(|item| matches!(item, Heard::Packet { .. }))
            );
        } else {
            let offsets: Vec<u64> = heard[1..11]
                .iter()
                .map(|item| match item {
                    Heard::Frame { offset, .. } => *offset,
                    other => panic!("{other:?}"),
                })
                .collect();
            assert_eq!(
                heard[0],
                Heard::Speech {
                    speaking: true,
                    offset: Some(offsets[0])
                }
            );
            assert_eq!(
                heard[11],
                Heard::Speech {
                    speaking: false,
                    offset: Some(offsets[8] + 960)
                }
            );
            assert!(offsets.windows(2).all(|pair| pair[1] == pair[0] + 960));
            if format != ReceiveFormat::Opus {
                assert!(data(&items).iter().all(|frame| frame.len() == 640));
            }
        }
    }
}

#[test]
fn a_leave_marker_ends_the_speech_and_follows_the_users_last_frame() {
    for format in [ReceiveFormat::Opus, ReceiveFormat::Packets] {
        let mut rig = Rig::new(tuning());
        rig.media.set_speech_activity(false);
        rig.map(ALICE_SSRC, ALICE);
        let receiver = rig.open(ReceiveOptions {
            markers: true,
            jitter: Duration::from_millis(100),
            ..options(format)
        });
        let others = rig.open(ReceiveOptions {
            markers: true,
            ..only(&[BOB], format)
        });
        for (sequence, packet) in (0..).zip(&opus(3, 0.3)) {
            rig.send(ALICE_SSRC, sequence, packet);
            rig.step();
        }
        rig.media.depart(user(ALICE), rig.now);
        for _ in 0..10 {
            rig.step();
        }

        let heard = heard(&read(&receiver));
        let end = match heard.get(3) {
            Some(Heard::Frame { offset, .. }) => Some(offset + 960),
            _ => None,
        };
        let stop = Heard::Speech {
            speaking: false,
            offset: end,
        };
        let leave = Heard::Leave {
            user: ALICE,
            offset: end,
        };
        assert_eq!(heard.len(), 6, "{format:?}");
        assert_eq!(heard[4..], [stop, leave], "{format:?}");
        assert!(read(&others).is_empty());
    }
}

#[test]
fn a_packets_receiver_gets_every_decrypted_packet_in_arrival_order() {
    let mut rig = Rig::dave();
    let mut call = rig.group(&[ALICE]);
    rig.map(ALICE_SSRC, ALICE);
    let packets = opus(7, 0.3);
    let frames: Vec<Vec<u8>> = packets
        .iter()
        .map(|packet| call.speak(user(ALICE), packet))
        .collect();
    let _earlier = rig.open(options(ReceiveFormat::Opus));
    rig.media.arrive(|_| Arrival {
        at: rig.reception.core.epoch,
        ..rig.arrival(ALICE_SSRC, 3, &frames[3])
    });
    let receiver = rig.open(only(&[ALICE], ReceiveFormat::Packets));
    let extension = |body: &[u8]| Some((rtp::ONE_BYTE_PROFILE, Bytes::copy_from_slice(body)));
    for (sequence, body) in [
        (2, extension(&[0x10, 0xAA, 0, 0])),
        (0, extension(&[0x13, 0xAA, 0, 0])),
        (1, None),
    ] {
        let arrival = Arrival {
            extension: body,
            ..rig.arrival(ALICE_SSRC, sequence, &frames[usize::from(sequence)])
        };
        rig.media.arrive(|_| arrival);
    }
    rig.step();
    rig.send(ALICE_SSRC, 100, &frames[4]);
    rig.step();
    rig.send(ALICE_SSRC, 30, &frames[5]);
    rig.send(ALICE_SSRC, 20_000, &frames[6]);
    rig.step();

    let packet = |sequence: u16, index: usize, extensions: Vec<(u8, Bytes)>| Heard::Packet {
        sequence,
        opus: packets[index].clone(),
        extensions,
    };
    assert_eq!(
        heard(&read(&receiver)),
        [
            packet(2, 2, vec![(1, Bytes::from_static(&[0xAA]))]),
            packet(0, 0, Vec::new()),
            packet(1, 1, Vec::new()),
            packet(100, 4, Vec::new()),
            packet(30, 5, Vec::new()),
            packet(20_000, 6, Vec::new()),
        ]
    );
}

#[test]
fn release_and_decode_stop_at_the_deadline_and_resume_next_tick() {
    let mut rig = Rig::new(tuning());
    rig.map(ALICE_SSRC, ALICE);
    let receiver = rig.open(options(pcm(48_000, 2, SampleFormat::S16Le)));
    for (sequence, packet) in (0..).zip(&opus(3, 0.3)) {
        rig.send(ALICE_SSRC, sequence, packet);
    }

    rig.run(0, rig.reception.core.epoch);
    let deferred = read(&receiver);
    rig.step();

    assert!(deferred.is_empty());
    assert_eq!(senders(&read(&receiver)), [Some(ALICE); 3]);
}
