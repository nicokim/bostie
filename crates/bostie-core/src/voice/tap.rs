use std::collections::HashMap;
use std::fmt;
use std::sync::LazyLock;

use parking_lot::{Mutex, RwLock};
use sonic_rs::{JsonValueTrait, LazyValue, PointerTree};
use tokio::sync::watch;
use tracing::debug;

use crate::gateway::tree;
use crate::ids::{ChannelId, GuildId, ShardId, UserId};

pub(crate) const VOICE_STATE_UPDATE: &str = "VOICE_STATE_UPDATE";
pub(crate) const VOICE_SERVER_UPDATE: &str = "VOICE_SERVER_UPDATE";

const STATE_KEYS: [&str; 9] = [
    "guild_id",
    "user_id",
    "channel_id",
    "session_id",
    "self_mute",
    "self_deaf",
    "mute",
    "deaf",
    "suppress",
];
const SERVER_KEYS: [&str; 3] = ["guild_id", "token", "endpoint"];

static STATE_FIELDS: LazyLock<PointerTree> = LazyLock::new(|| tree(&STATE_KEYS));
static SERVER_FIELDS: LazyLock<PointerTree> = LazyLock::new(|| tree(&SERVER_KEYS));

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct Snapshot {
    pub(crate) state: Option<StateUpdate>,
    pub(crate) state_seq: u64,
    pub(crate) server: Option<ServerUpdate>,
    pub(crate) server_seq: u64,
    pub(crate) main_session: u64,
}

#[derive(Clone, PartialEq, Eq)]
pub(crate) struct StateUpdate {
    pub(crate) channel: Option<ChannelId>,
    pub(crate) session_id: Box<str>,
    pub(crate) self_mute: bool,
    pub(crate) self_deaf: bool,
    pub(crate) mute: bool,
    pub(crate) deaf: bool,
    pub(crate) suppress: bool,
}

impl fmt::Debug for StateUpdate {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("StateUpdate")
            .field("channel", &self.channel)
            .field("self_mute", &self.self_mute)
            .field("self_deaf", &self.self_deaf)
            .field("mute", &self.mute)
            .field("deaf", &self.deaf)
            .field("suppress", &self.suppress)
            .finish_non_exhaustive()
    }
}

#[derive(Clone, PartialEq, Eq)]
pub(crate) struct ServerUpdate {
    pub(crate) token: Box<str>,
    pub(crate) endpoint: Option<Box<str>>,
}

impl fmt::Debug for ServerUpdate {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ServerUpdate")
            .field("endpoint", &self.endpoint)
            .finish_non_exhaustive()
    }
}

struct Entry {
    shard: ShardId,
    user: UserId,
    slot: watch::Sender<Snapshot>,
}

#[derive(Default)]
pub(crate) struct GatewayTap {
    guilds: RwLock<HashMap<GuildId, Entry>>,
    sessions: Mutex<HashMap<ShardId, u64>>,
}

impl GatewayTap {
    pub(crate) fn new() -> Self {
        Self::default()
    }

    pub(crate) fn has_guilds(&self) -> bool {
        !self.guilds.read().is_empty()
    }

    pub(crate) fn register(
        &self,
        guild: GuildId,
        shard: ShardId,
        user: UserId,
    ) -> watch::Receiver<Snapshot> {
        let sessions = self.sessions.lock();
        let main_session = sessions.get(&shard).copied().unwrap_or(0);
        let (slot, receiver) = watch::channel(Snapshot {
            main_session,
            ..Snapshot::default()
        });
        self.guilds
            .write()
            .insert(guild, Entry { shard, user, slot });
        receiver
    }

    pub(crate) fn unregister(&self, guild: GuildId) {
        self.guilds.write().remove(&guild);
    }

    pub(crate) fn offer(&self, shard: ShardId, name: &str, data: &[u8]) {
        match name {
            VOICE_STATE_UPDATE => self.offer_state(shard, data),
            VOICE_SERVER_UPDATE => self.offer_server(shard, data),
            _ => {}
        }
    }

    pub(crate) fn session_started(&self, shard: ShardId) {
        let mut sessions = self.sessions.lock();
        let counter = sessions.entry(shard).or_default();
        *counter += 1;
        let counter = *counter;
        for entry in self.guilds.read().values() {
            if entry.shard == shard {
                entry
                    .slot
                    .send_modify(|snapshot| snapshot.main_session = counter);
            }
        }
    }

    fn offer_state(&self, shard: ShardId, data: &[u8]) {
        let Ok(fields) = sonic_rs::get_many(data, &STATE_FIELDS) else {
            debug!("ignoring an unreadable VOICE_STATE_UPDATE");
            return;
        };
        let [
            guild,
            user,
            channel,
            session_id,
            self_mute,
            self_deaf,
            mute,
            deaf,
            suppress,
        ] = <[_; STATE_KEYS.len()]>::try_from(fields).unwrap_or_default();
        let (Some(guild), Some(user), Some(session_id)) = (
            guild.as_ref().and_then(snowflake).map(GuildId::from),
            user.as_ref().and_then(snowflake).map(UserId::from),
            session_id.as_ref().and_then(|value| value.as_str()),
        ) else {
            return;
        };
        let channel = match channel {
            Some(value) if !value.is_null() => match snowflake(&value) {
                Some(channel) => Some(ChannelId::from(channel)),
                None => return,
            },
            _ => None,
        };
        let state = StateUpdate {
            channel,
            session_id: session_id.into(),
            self_mute: flag(self_mute.as_ref()),
            self_deaf: flag(self_deaf.as_ref()),
            mute: flag(mute.as_ref()),
            deaf: flag(deaf.as_ref()),
            suppress: flag(suppress.as_ref()),
        };
        let guilds = self.guilds.read();
        let Some(entry) = guilds
            .get(&guild)
            .filter(|entry| entry.shard == shard && entry.user == user)
        else {
            return;
        };
        entry.slot.send_modify(|snapshot| {
            snapshot.state = Some(state);
            snapshot.state_seq += 1;
        });
    }

    fn offer_server(&self, shard: ShardId, data: &[u8]) {
        let Ok(fields) = sonic_rs::get_many(data, &SERVER_FIELDS) else {
            debug!("ignoring an unreadable VOICE_SERVER_UPDATE");
            return;
        };
        let [guild, token, endpoint] =
            <[_; SERVER_KEYS.len()]>::try_from(fields).unwrap_or_default();
        let (Some(guild), Some(token)) = (
            guild.as_ref().and_then(snowflake).map(GuildId::from),
            token.as_ref().and_then(|value| value.as_str()),
        ) else {
            return;
        };
        let endpoint = endpoint
            .as_ref()
            .and_then(|value| value.as_str())
            .map(Box::from);
        let guilds = self.guilds.read();
        let Some(entry) = guilds.get(&guild).filter(|entry| entry.shard == shard) else {
            return;
        };
        let server = ServerUpdate {
            token: token.into(),
            endpoint,
        };
        entry.slot.send_modify(|snapshot| {
            snapshot.server = Some(server);
            snapshot.server_seq += 1;
        });
    }
}

fn snowflake(value: &LazyValue<'_>) -> Option<u64> {
    value.as_str()?.parse().ok()
}

fn flag(value: Option<&LazyValue<'_>>) -> bool {
    value.and_then(JsonValueTrait::as_bool).unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    const GUILD: u64 = 81_384_788_765_712_384;
    const OTHER_GUILD: u64 = 81_384_788_765_712_385;
    const USER: u64 = 80_351_110_224_678_912;
    const CHANNEL: u64 = 127_121_515_262_115_840;

    fn shard(id: u32) -> ShardId {
        ShardId::from(id)
    }

    fn state(guild: u64, user: u64, channel: Option<u64>, extra: &str) -> Vec<u8> {
        let channel = channel.map_or_else(|| "null".to_owned(), |id| format!(r#""{id}""#));
        format!(
            r#"{{"guild_id":"{guild}","user_id":"{user}","channel_id":{channel},"session_id":"session","self_video":false{extra}}}"#
        )
        .into_bytes()
    }

    fn server(guild: u64, token: &str, endpoint: Option<&str>) -> Vec<u8> {
        let endpoint = endpoint.map_or_else(|| "null".to_owned(), |text| format!(r#""{text}""#));
        format!(r#"{{"token":"{token}","guild_id":"{guild}","endpoint":{endpoint}}}"#).into_bytes()
    }

    fn registered() -> (GatewayTap, watch::Receiver<Snapshot>) {
        let tap = GatewayTap::new();
        let receiver = tap.register(GuildId::from(GUILD), shard(0), UserId::from(USER));
        (tap, receiver)
    }

    #[test]
    fn payloads_that_are_not_ours_or_unreadable_change_nothing() {
        let unreadable_channel = format!(
            r#"{{"guild_id":"{GUILD}","user_id":"{USER}","channel_id":"x","session_id":"s"}}"#
        );
        let offers: [(u32, &str, Vec<u8>); 9] = [
            (
                0,
                VOICE_STATE_UPDATE,
                state(GUILD, USER + 1, Some(CHANNEL), ""),
            ),
            (
                0,
                VOICE_STATE_UPDATE,
                state(OTHER_GUILD, USER, Some(CHANNEL), ""),
            ),
            (
                0,
                VOICE_SERVER_UPDATE,
                server(OTHER_GUILD, "t", Some("host")),
            ),
            (0, "MESSAGE_CREATE", state(GUILD, USER, Some(CHANNEL), "")),
            (1, VOICE_STATE_UPDATE, state(GUILD, USER, Some(CHANNEL), "")),
            (1, VOICE_SERVER_UPDATE, server(GUILD, "t", Some("host"))),
            (0, VOICE_STATE_UPDATE, b"{not json".to_vec()),
            (0, VOICE_SERVER_UPDATE, br#"{"guild_id":"1"}"#.to_vec()),
            (0, VOICE_STATE_UPDATE, unreadable_channel.into_bytes()),
        ];

        for (from, name, payload) in offers {
            let (tap, receiver) = registered();
            tap.offer(shard(from), name, &payload);
            assert_eq!(*receiver.borrow(), Snapshot::default(), "{from} {name}");
        }
    }

    #[test]
    fn the_own_voice_state_keeps_its_channel_session_and_flags() {
        let (tap, receiver) = registered();

        tap.offer(
            shard(0),
            VOICE_STATE_UPDATE,
            &state(
                GUILD,
                USER,
                Some(CHANNEL),
                r#","self_mute":true,"self_deaf":false,"mute":true,"deaf":true,"suppress":true"#,
            ),
        );

        let snapshot = receiver.borrow();
        assert_eq!(snapshot.state_seq, 1);
        assert_eq!(
            snapshot.state,
            Some(StateUpdate {
                channel: Some(ChannelId::from(CHANNEL)),
                session_id: "session".into(),
                self_mute: true,
                self_deaf: false,
                mute: true,
                deaf: true,
                suppress: true,
            })
        );
    }

    #[test]
    fn a_null_channel_is_a_leave_and_missing_flags_are_false() {
        let (tap, receiver) = registered();

        tap.offer(shard(0), VOICE_STATE_UPDATE, &state(GUILD, USER, None, ""));

        let state = receiver.borrow().state.clone().unwrap();
        assert_eq!(state.channel, None);
        assert!(
            !(state.self_mute || state.self_deaf || state.mute || state.deaf || state.suppress)
        );
    }

    #[test]
    fn the_slot_keeps_the_latest_values_and_counts_each_update() {
        let (tap, receiver) = registered();

        tap.offer(
            shard(0),
            VOICE_SERVER_UPDATE,
            &server(GUILD, "first", Some("a:1")),
        );
        tap.offer(
            shard(0),
            VOICE_SERVER_UPDATE,
            &server(GUILD, "second", None),
        );
        tap.offer(
            shard(0),
            VOICE_STATE_UPDATE,
            &state(GUILD, USER, Some(CHANNEL), ""),
        );
        tap.offer(
            shard(0),
            VOICE_STATE_UPDATE,
            &state(GUILD, USER, Some(CHANNEL + 1), ""),
        );

        let snapshot = receiver.borrow();
        assert_eq!((snapshot.server_seq, snapshot.state_seq), (2, 2));
        assert_eq!(
            snapshot.server,
            Some(ServerUpdate {
                token: "second".into(),
                endpoint: None,
            })
        );
        assert_eq!(
            snapshot.state.as_ref().unwrap().channel,
            Some(ChannelId::from(CHANNEL + 1))
        );
    }

    #[test]
    fn a_session_start_bumps_only_the_entries_of_that_shard() {
        let tap = GatewayTap::new();
        let first = tap.register(GuildId::from(GUILD), shard(0), UserId::from(USER));
        let second = tap.register(GuildId::from(OTHER_GUILD), shard(1), UserId::from(USER));

        tap.session_started(shard(0));
        tap.session_started(shard(0));

        assert_eq!(first.borrow().main_session, 2);
        assert_eq!(second.borrow().main_session, 0);
    }

    #[test]
    fn registering_after_a_session_start_does_not_count_it() {
        let tap = GatewayTap::new();
        tap.session_started(shard(0));

        let receiver = tap.register(GuildId::from(GUILD), shard(0), UserId::from(USER));
        assert_eq!(receiver.borrow().main_session, 1);
        assert!(!receiver.has_changed().unwrap());

        tap.session_started(shard(0));
        assert!(receiver.has_changed().unwrap());
        assert_eq!(receiver.borrow().main_session, 2);
    }

    #[test]
    fn unregistering_stops_updates_and_empties_the_tap() {
        let (tap, receiver) = registered();
        assert!(tap.has_guilds());

        tap.unregister(GuildId::from(GUILD));
        tap.offer(
            shard(0),
            VOICE_SERVER_UPDATE,
            &server(GUILD, "t", Some("host")),
        );

        assert!(!tap.has_guilds());
        assert_eq!(receiver.borrow().server_seq, 0);
    }

    #[test]
    fn debug_output_hides_the_token_and_the_session_id() {
        let update = ServerUpdate {
            token: "secret-token".into(),
            endpoint: Some("host".into()),
        };
        let state = StateUpdate {
            channel: None,
            session_id: "secret-session".into(),
            self_mute: false,
            self_deaf: false,
            mute: false,
            deaf: false,
            suppress: false,
        };

        assert!(!format!("{update:?}").contains("secret"));
        assert!(!format!("{state:?}").contains("secret"));
    }
}
