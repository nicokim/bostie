use std::collections::{HashMap, VecDeque};
use std::mem;
use std::time::{Duration, Instant};

use super::Core;
use crate::ids::UserId;
use crate::voice::media::Arrival;
use crate::voice::types::ReceiveTuning;

#[derive(Debug, Clone, Copy)]
struct Span {
    user: UserId,
    from: Option<Instant>,
    until: Option<Instant>,
}

#[derive(Default)]
pub(super) struct Owners {
    spans: HashMap<u32, Vec<Span>>,
    unmapped: HashMap<u32, VecDeque<Arrival>>,
}

impl Owners {
    pub(super) fn map(
        &mut self,
        core: &Core,
        now: Instant,
        arrivals: &mut VecDeque<Arrival>,
        batch: &mut Vec<(UserId, Arrival)>,
    ) {
        self.track(&core.info.ssrcs.lock(), now);
        let tuning = &core.tuning;
        batch.clear();
        for (ssrc, queue) in &mut self.unmapped {
            let Some(spans) = self.spans.get(ssrc) else {
                continue;
            };
            for arrival in mem::take(queue) {
                match owner(spans, arrival.at, tuning.hold) {
                    Some(user) => batch.push((user, arrival)),
                    None => queue.push_back(arrival),
                }
            }
        }
        self.unmapped.retain(|_, queue| !queue.is_empty());
        batch.sort_by_key(|(_, arrival)| arrival.at);
        for arrival in arrivals.drain(..) {
            if arrival.own {
                batch.push((core.user, arrival));
                continue;
            }
            let user = self
                .spans
                .get(&arrival.ssrc)
                .and_then(|spans| owner(spans, arrival.at, tuning.hold));
            match user {
                Some(user) => batch.push((user, arrival)),
                None => self.park(arrival, tuning),
            }
        }
    }

    pub(super) fn has_parked(&self) -> bool {
        !self.unmapped.is_empty()
    }

    pub(super) fn clear(&mut self) {
        self.spans.clear();
        self.unmapped.clear();
    }

    pub(super) fn prune(&mut self, now: Instant, hold: Duration) {
        let expired = |at: Instant| now.saturating_duration_since(at) > hold;
        self.unmapped.retain(|_, queue| {
            while queue.front().is_some_and(|arrival| expired(arrival.at)) {
                queue.pop_front();
            }
            !queue.is_empty()
        });
        self.spans.retain(|_, spans| {
            spans.retain(|span| span.until.is_none_or(|until| !expired(until)));
            !spans.is_empty()
        });
    }

    fn track(&mut self, ssrcs: &HashMap<u32, (UserId, Instant)>, now: Instant) {
        for (&ssrc, &(user, since)) in ssrcs {
            let spans = self
                .spans
                .entry(ssrc)
                .or_insert_with(|| Vec::with_capacity(1));
            match spans.last_mut() {
                Some(last) if last.until.is_none() && last.user == user => {}
                Some(last) => {
                    if last.until.is_none() {
                        last.until = Some(since);
                    }
                    spans.push(Span {
                        user,
                        from: Some(since),
                        until: None,
                    });
                }
                None => spans.push(Span {
                    user,
                    from: None,
                    until: None,
                }),
            }
        }
        for (ssrc, spans) in &mut self.spans {
            if !ssrcs.contains_key(ssrc)
                && let Some(last) = spans.last_mut()
                && last.until.is_none()
            {
                last.until = Some(now);
            }
        }
    }

    fn park(&mut self, arrival: Arrival, tuning: &ReceiveTuning) {
        if tuning.hold_packets == 0 || tuning.unmapped_ssrcs == 0 {
            return;
        }
        if !self.unmapped.contains_key(&arrival.ssrc)
            && self.unmapped.len() >= tuning.unmapped_ssrcs
            && let Some(oldest) = self
                .unmapped
                .iter()
                .min_by_key(|(_, queue)| queue.front().map(|arrival| arrival.at))
                .map(|(&ssrc, _)| ssrc)
        {
            self.unmapped.remove(&oldest);
        }
        let queue = self.unmapped.entry(arrival.ssrc).or_default();
        if queue.len() >= tuning.hold_packets {
            queue.pop_front();
        }
        queue.push_back(arrival);
    }
}

fn owner(spans: &[Span], at: Instant, hold: Duration) -> Option<UserId> {
    spans
        .iter()
        .rev()
        .find(|span| {
            span.from.is_none_or(|from| from <= at)
                && span
                    .until
                    .is_none_or(|until| until.checked_add(hold).is_none_or(|end| at <= end))
        })
        .map(|span| span.user)
}
