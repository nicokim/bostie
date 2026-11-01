use std::collections::{HashMap, HashSet, VecDeque};
use std::mem;
use std::time::{Duration, Instant};

use bytes::{Bytes, BytesMut};
use parking_lot::MutexGuard;
use tracing::{debug, warn};

use super::{Core, Key, Work};
use crate::ids::{GuildId, UserId};
use crate::voice::Throttle;
use crate::voice::dave::{Dave, DecryptFailure};
use crate::voice::media::Arrival;
use crate::voice::peak::Peak;
use crate::voice::playout::Check;
use crate::voice::types::ReceiveTuning;

const PAYLOAD_CHUNK: usize = 8 * 1024;
const RECOVERY_INTERVAL: Duration = Duration::from_secs(30);
const RECOVERIES_PER_USER: u8 = 2;

struct Held {
    user: UserId,
    arrival: Arrival,
    check: Check,
    tried: u64,
}

#[derive(Default)]
pub(super) struct Undecryptable {
    pub(super) total: u64,
    warned_up_to: u64,
    warned: Throttle,
}

#[derive(Debug, Default)]
struct Streak {
    failures: u32,
    requested: u8,
    last_request: Option<Instant>,
}

#[derive(Default)]
pub(super) struct Hold {
    work: Vec<Work>,
    retried: Vec<Work>,
    waiting: VecDeque<Work>,
    waits: HashSet<UserId>,
    held: VecDeque<Held>,
    per_user: HashMap<UserId, usize>,
    admitted: HashSet<(Key, u16)>,
    streaks: HashMap<UserId, Streak>,
    last_request: Option<Instant>,
    plain: Vec<u8>,
    payloads: BytesMut,
    peak: Peak,
    pub(super) undecryptable: Undecryptable,
}

impl Hold {
    pub(super) fn is_empty(&self) -> bool {
        self.held.is_empty() && self.waiting.is_empty()
    }

    pub(super) fn oldest(&self, wanted: impl Fn(UserId, bool) -> bool) -> Option<Instant> {
        let held = self.held.iter().map(|held| (held.user, &held.arrival));
        let waiting = self.waiting.iter().map(|item| (item.user, &item.arrival));
        held.chain(waiting)
            .filter(|(user, arrival)| wanted(*user, arrival.own))
            .map(|(_, arrival)| arrival.at)
            .min()
    }

    pub(super) fn is_pending(&self, key: Key, sequence: u16) -> bool {
        self.admitted.contains(&(key, sequence))
    }

    pub(super) fn admit(&mut self, key: Key, sequence: u16) {
        self.admitted.insert((key, sequence));
    }

    pub(super) fn take_work(&mut self) -> Vec<Work> {
        let mut work = mem::take(&mut self.work);
        work.extend(self.waiting.drain(..));
        work
    }

    pub(super) fn take_retried(&mut self) -> Vec<Work> {
        let retried = mem::take(&mut self.retried);
        self.peak.observe(retried.len());
        retried
    }

    pub(super) fn defers(&self, item: &Work, behind: bool) -> bool {
        self.waits.contains(&item.user) || (behind && item.plain.is_none())
    }

    pub(super) fn wait(&mut self, mut item: Work, limit: usize) {
        self.waits.insert(item.user);
        item.fresh = false;
        if self.waiting.len() >= limit {
            self.waiting.pop_front();
        }
        self.waiting.push_back(item);
    }

    pub(super) fn settle(&mut self, work: Vec<Work>, retried: Vec<Work>) {
        self.waits.clear();
        self.work = work;
        self.retried = retried;
        self.admitted.clear();
        let held = self
            .held
            .iter()
            .map(|held| ((held.arrival.ssrc, held.user), held.arrival.sequence));
        let waiting = self
            .waiting
            .iter()
            .map(|item| ((item.arrival.ssrc, item.user), item.arrival.sequence));
        self.admitted.extend(held.chain(waiting));
    }

    pub(super) fn unsubscribed(&mut self) {
        self.drop_frames();
        self.payloads = BytesMut::new();
    }

    pub(super) fn new_session(&mut self) {
        self.drop_frames();
        self.streaks.clear();
    }

    pub(super) fn expire(&mut self, now: Instant, hold: Duration) {
        while self
            .held
            .front()
            .is_some_and(|held| now.saturating_duration_since(held.arrival.at) > hold)
        {
            if let Some(held) = self.held.pop_front() {
                unhold(&mut self.per_user, held.user);
                self.undecryptable.count();
            }
        }
    }

    pub(super) fn warn_undecryptable(&mut self, now: Instant, guild: GuildId) {
        self.undecryptable.warn(now, guild);
    }

    pub(super) fn forget_streaks(&mut self, kept: impl Fn(&UserId) -> bool) {
        self.streaks.retain(|user, _| kept(user));
    }

    pub(super) fn shrink(&mut self) {
        self.peak.observe(self.held.len());
        self.peak.observe(self.waiting.len());
        let peak = &self.peak;
        peak.shrink(self.work.capacity(), |to| self.work.shrink_to(to));
        peak.shrink(self.retried.capacity(), |to| self.retried.shrink_to(to));
        peak.shrink(self.held.capacity(), |to| self.held.shrink_to(to));
        peak.shrink(self.waiting.capacity(), |to| self.waiting.shrink_to(to));
        self.peak.advance();
    }

    pub(super) fn decrypt(
        &mut self,
        work: &mut [Work],
        core: &Core,
        now: Instant,
        deadline: Instant,
    ) -> usize {
        self.peak.observe(work.len());
        if work.iter().all(|item| item.plain.is_some()) && self.held.is_empty() {
            return work.len();
        }
        let media = &core.media;
        let Some(mut dave) = media.dave.try_lock() else {
            return 0;
        };
        let settling = dave.settling(now);
        let mut retries = core.tuning.dave_retries;
        let mut cut = 0;
        loop {
            let spent;
            (cut, spent) = self.slice(&mut dave, work, cut, &mut retries, core, now);
            if !spent || Instant::now() >= deadline {
                drop(dave);
                break;
            }
            MutexGuard::unlock_fair(dave);
            match media.dave.try_lock() {
                Some(next) => dave = next,
                None => break,
            }
        }
        self.request_recovery(core, settling, now);
        cut
    }

    fn slice(
        &mut self,
        dave: &mut Dave,
        work: &mut [Work],
        from: usize,
        retries: &mut usize,
        core: &Core,
        now: Instant,
    ) -> (usize, bool) {
        let started = Instant::now();
        let generation = dave.generation();
        let protocol_version = core.media.protocol_version();
        let settling = dave.settling(now);
        let budget = core.tuning.dave_budget;
        let mut spent = false;
        {
            let Self {
                undecryptable,
                held,
                per_user,
                plain,
                payloads,
                retried,
                streaks,
                ..
            } = self;
            held.retain_mut(|held| {
                if spent || *retries == 0 || held.tried == generation {
                    return true;
                }
                *retries = retries.saturating_sub(1);
                let payload = held.arrival.payload.as_deref().unwrap_or_default();
                let decrypted = dave.receive(held.user, payload, plain);
                spent = started.elapsed() >= budget;
                match decrypted {
                    Ok(()) => {
                        streaks.remove(&held.user);
                        retried.push(Work {
                            user: held.user,
                            arrival: held.arrival.clone(),
                            check: held.check,
                            plain: Some(share(payloads, plain)),
                            fresh: false,
                        });
                        unhold(per_user, held.user);
                        false
                    }
                    Err(failure) => {
                        held.tried = generation;
                        let kept = holds(failure, protocol_version);
                        if !kept {
                            undecryptable.count();
                            unhold(per_user, held.user);
                        }
                        kept
                    }
                }
            });
        }
        let mut cut = work.len();
        for (index, item) in work.iter_mut().enumerate().skip(from) {
            if item.plain.is_some() {
                continue;
            }
            if spent {
                cut = index;
                break;
            }
            let payload = item.arrival.payload.as_deref().unwrap_or_default();
            let decrypted = dave.receive(item.user, payload, &mut self.plain);
            spent = started.elapsed() >= budget;
            match decrypted {
                Ok(()) => {
                    self.streaks.remove(&item.user);
                    item.plain = Some(share(&mut self.payloads, &self.plain));
                }
                Err(failure) => {
                    if counts(failure, protocol_version, settling) {
                        let streak = self.streaks.entry(item.user).or_default();
                        streak.failures = streak.failures.saturating_add(1);
                    }
                    if holds(failure, protocol_version) {
                        self.keep(item, generation, &core.tuning);
                    } else {
                        self.undecryptable.count();
                    }
                }
            }
        }
        (cut, spent)
    }

    fn keep(&mut self, item: &Work, tried: u64, tuning: &ReceiveTuning) {
        if tuning.hold_packets == 0 || tuning.arrivals == 0 {
            self.undecryptable.count();
            return;
        }
        let count = self.per_user.entry(item.user).or_default();
        if *count >= tuning.hold_packets {
            if let Some(index) = self.held.iter().position(|held| held.user == item.user)
                && self.held.remove(index).is_some()
            {
                self.undecryptable.count();
            }
        } else {
            *count = count.saturating_add(1);
        }
        if self.held.len() >= tuning.arrivals
            && let Some(oldest) = self.held.pop_front()
        {
            unhold(&mut self.per_user, oldest.user);
            self.undecryptable.count();
        }
        self.held.push_back(Held {
            user: item.user,
            arrival: item.arrival.clone(),
            check: item.check,
            tried,
        });
    }

    fn request_recovery(&mut self, core: &Core, settling: bool, now: Instant) {
        let tolerance = core.tuning.decrypt_tolerance;
        let last_request = self.last_request;
        let asking = self
            .streaks
            .iter_mut()
            .find(|(_, streak)| streak.asks(settling, last_request, now, tolerance));
        if let Some((user, streak)) = asking {
            debug!(%user, failures = streak.failures, "asking for a DAVE recovery after repeated decrypt failures");
            streak.failures = 0;
            streak.requested = streak.requested.saturating_add(1);
            streak.last_request = Some(now);
            self.last_request = Some(now);
            core.media.recover.notify_one();
        }
    }

    fn drop_frames(&mut self) {
        self.held.clear();
        self.per_user.clear();
        self.waiting.clear();
    }
}

impl Undecryptable {
    fn count(&mut self) {
        self.total = self.total.saturating_add(1);
    }

    fn warn(&mut self, now: Instant, guild: GuildId) {
        if self.total > self.warned_up_to && self.warned.allows(now) {
            warn!(
                %guild,
                dropped = self.total.saturating_sub(self.warned_up_to),
                "dropped received voice frames that could not be decrypted"
            );
            self.warned_up_to = self.total;
        }
    }
}

impl Streak {
    fn asks(
        &self,
        settling: bool,
        last_request: Option<Instant>,
        now: Instant,
        tolerance: u32,
    ) -> bool {
        !settling
            && due(last_request, now)
            && self.failures >= tolerance
            && self.requested < RECOVERIES_PER_USER
            && due(self.last_request, now)
    }
}

fn due(last: Option<Instant>, now: Instant) -> bool {
    last.is_none_or(|last| now.saturating_duration_since(last) >= RECOVERY_INTERVAL)
}

fn counts(failure: DecryptFailure, protocol_version: u16, settling: bool) -> bool {
    protocol_version > 0 && !settling && failure != DecryptFailure::NoDecryptor
}

fn holds(failure: DecryptFailure, protocol_version: u16) -> bool {
    protocol_version > 0 && failure != DecryptFailure::Unencrypted
}

fn share(payloads: &mut BytesMut, payload: &[u8]) -> Bytes {
    if payloads.capacity() < payload.len() {
        *payloads = BytesMut::with_capacity(PAYLOAD_CHUNK.max(payload.len()));
    }
    payloads.extend_from_slice(payload);
    payloads.split().freeze()
}

fn unhold(per_user: &mut HashMap<UserId, usize>, user: UserId) {
    if let Some(count) = per_user.get_mut(&user) {
        *count = count.saturating_sub(1);
        if *count == 0 {
            per_user.remove(&user);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_hold_reports_the_oldest_frame_a_filter_takes_wherever_it_sits() {
        let (alice, bob) = (UserId::from(1), UserId::from(2));
        let earlier = Instant::now();
        let later = earlier + Duration::from_millis(5);
        let held = |user, at| Held {
            user,
            arrival: Arrival {
                ssrc: 1,
                sequence: 0,
                timestamp: 0,
                at,
                payload: None,
                silence: false,
                extension: None,
                own: false,
            },
            check: Check::Fresh,
            tried: 0,
        };
        let mut hold = Hold::default();
        hold.held.extend([held(bob, later), held(alice, earlier)]);

        let everyone = hold.oldest(|_, _| true);
        let only_bob = hold.oldest(|user, _| user == bob);

        assert_eq!((everyone, only_bob), (Some(earlier), Some(later)));
    }

    #[test]
    fn a_recovery_is_asked_only_when_every_condition_holds() {
        let asked = Instant::now();
        let now = asked + RECOVERY_INTERVAL;
        let (old, recent) = (Some(asked), Some(asked + RECOVERY_INTERVAL / 2));
        let cap = RECOVERIES_PER_USER;
        let streak = |failures, requested, last_request| Streak {
            failures,
            requested,
            last_request,
        };
        let rows = [
            ("all hold", false, old, streak(3, 1, old), true),
            ("settling", true, old, streak(3, 1, old), false),
            ("any asked lately", false, recent, streak(3, 1, old), false),
            ("below tolerance", false, old, streak(2, 1, old), false),
            ("at the cap", false, old, streak(3, cap, old), false),
            ("user asked lately", false, old, streak(3, 1, recent), false),
        ];

        for (case, settling, any, streak, asks) in rows {
            assert_eq!(streak.asks(settling, any, now, 3), asks, "{case}");
        }
    }

    #[test]
    fn decrypt_failures_count_and_wait_by_kind_version_and_settling() {
        use DecryptFailure::{Failed, NoDecryptor, Unencrypted};
        let rows = [
            ("a failed decrypt", Failed, 1, false, true, true),
            ("no decryptor", NoDecryptor, 1, false, false, true),
            ("an unencrypted frame", Unencrypted, 1, false, true, false),
            ("protocol version 0", Failed, 0, false, false, false),
            ("settling", Failed, 1, true, false, true),
        ];

        for (case, failure, protocol_version, settling, counted, held) in rows {
            let verdict = (
                counts(failure, protocol_version, settling),
                holds(failure, protocol_version),
            );
            assert_eq!(verdict, (counted, held), "{case}");
        }
    }
}
