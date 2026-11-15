from collections.abc import Callable, Collection, Iterable
from typing import Any

import msgspec

from bostie.enums import ChannelType, Permissions, Status
from bostie.events import (
    ChannelCreate,
    ChannelDelete,
    ChannelUpdate,
    EmojisUpdate,
    Event,
    GuildCreate,
    GuildDelete,
    GuildUpdate,
    MemberJoin,
    MemberRemove,
    MembersChunk,
    MemberUpdate,
    MessageCreate,
    MessageDelete,
    MessageDeleteBulk,
    MessageUpdate,
    PresenceUpdate,
    Ready,
    RoleCreate,
    RoleDelete,
    RoleUpdate,
    ScheduledEventCreate,
    ScheduledEventDelete,
    ScheduledEventUpdate,
    SoundboardSoundCreate,
    SoundboardSoundDelete,
    SoundboardSounds,
    SoundboardSoundsUpdate,
    SoundboardSoundUpdate,
    StageInstanceCreate,
    StageInstanceDelete,
    StageInstanceUpdate,
    StickersUpdate,
    ThreadCreate,
    ThreadDelete,
    ThreadListSync,
    ThreadMembersUpdate,
    ThreadUpdate,
    UserUpdate,
    VoiceStateUpdate,
)
from bostie.models import (
    Channel,
    CurrentUser,
    Guild,
    Member,
    Message,
    Presence,
    Role,
    ScheduledEvent,
    SoundboardSound,
    StageInstance,
    User,
    VoiceState,
    WebhookMessage,
)
from bostie.permissions import _THREAD_TYPES, AnyMember, compute_permissions
from bostie.snowflake import shard_of

type _CachedMessage = Message | WebhookMessage
type _Changes = dict[str, Any] | None


class _GuildChildren[T]:
    def __init__(self) -> None:
        self._by_id: dict[int, T] = {}
        self._by_guild: dict[int, dict[int, T]] = {}

    def get(self, id: int) -> T | None:
        return self._by_id.get(id)

    def of(self, guild_id: int) -> list[T]:
        return list(self._by_guild.get(guild_id, {}).values())

    def put(self, guild_id: int, id: int, child: T) -> T | None:
        old = self._by_id.get(id)
        self._by_id[id] = child
        self._by_guild.setdefault(guild_id, {})[id] = child
        return old

    def pop(self, guild_id: int, id: int) -> T | None:
        self._by_guild.get(guild_id, {}).pop(id, None)
        return self._by_id.pop(id, None)

    def reset(self, guild_id: int, children: Iterable[tuple[int, T]]) -> None:
        self.drop(guild_id)
        for id, child in children:
            self.put(guild_id, id, child)

    def drop(self, guild_id: int) -> None:
        for id in self._by_guild.pop(guild_id, {}):
            self._by_id.pop(id, None)

    def clear(self) -> None:
        self._by_id.clear()
        self._by_guild.clear()


class Cache:
    """Guilds, channels, active threads, voice states and the bot's member and user.

    Scheduled events, stage instances and soundboard sounds are kept too.

    Members, presences, the latest messages and DM channels are kept only when
    asked for.
    Channels are stored as received, obfuscated ones included, except the fields
    CHANNEL_UPDATE does not keep current (last_message_id, message_count,
    member_count, last_pin_timestamp), which are None.
    """

    def __init__(
        self,
        max_messages: int = 0,
        *,
        members: bool = False,
        presences: bool = False,
        max_dm_channels: int = 0,
    ) -> None:
        for name, limit in (
            ("max_messages", max_messages),
            ("max_dm_channels", max_dm_channels),
        ):
            if limit < 0:
                raise ValueError(f"{name} must be 0 or more, got {limit}")
        self._current: CurrentUser | None = None
        self._max_messages = max_messages
        self._max_dm_channels = max_dm_channels
        self._all_members = members
        self._keeps_presences = presences
        self._guilds: dict[int, Guild] = {}
        self._channels: dict[int, Channel] = {}
        self._guild_channels: dict[int, dict[int, Channel]] = {}
        self._roles: dict[int, Role] = {}
        self._voice_states: dict[int, dict[int, VoiceState]] = {}
        self._members: dict[int, dict[int, Member]] = {}
        self._presences: dict[int, dict[int, Presence]] = {}
        self._messages: dict[int, _CachedMessage] = {}
        self._dm_channels: dict[int, Channel] = {}
        self._scheduled_events = _GuildChildren[ScheduledEvent]()
        self._stage_instances = _GuildChildren[StageInstance]()
        self._sounds = _GuildChildren[SoundboardSound]()
        self._handlers: dict[type[Event], Callable[[Any], _Changes]] = {
            Ready: self._start_session,
            GuildCreate: self._create_guild,
            GuildUpdate: self._update_guild,
            GuildDelete: self._delete_guild,
            ChannelCreate: self._create_channel,
            ThreadCreate: self._create_channel,
            ChannelUpdate: self._update_channel,
            ThreadUpdate: self._update_channel,
            ChannelDelete: self._delete_channel,
            ThreadDelete: self._delete_thread,
            ThreadListSync: self._sync_threads,
            ThreadMembersUpdate: self._leave_thread,
            RoleCreate: self._create_role,
            RoleUpdate: self._update_role,
            RoleDelete: self._delete_role,
            EmojisUpdate: self._update_emojis,
            StickersUpdate: self._update_stickers,
            UserUpdate: self._update_user,
            VoiceStateUpdate: self._update_voice_state,
            MemberJoin: self._join,
            MemberRemove: self._leave,
            MemberUpdate: self._update_member,
            ScheduledEventCreate: self._keep_scheduled_event,
            ScheduledEventUpdate: self._keep_scheduled_event,
            ScheduledEventDelete: self._delete_scheduled_event,
            StageInstanceCreate: self._keep_stage_instance,
            StageInstanceUpdate: self._keep_stage_instance,
            StageInstanceDelete: self._delete_stage_instance,
            SoundboardSoundCreate: self._keep_sound,
            SoundboardSoundUpdate: self._keep_sound,
            SoundboardSoundDelete: self._delete_sound,
            SoundboardSoundsUpdate: self._update_sounds,
            SoundboardSounds: self._replace_sounds,
        }
        if members or presences:
            self._handlers[MembersChunk] = self._store_chunk
        if presences:
            self._handlers[PresenceUpdate] = self._update_presence
        if max_messages or max_dm_channels:
            self._handlers[MessageCreate] = self._create_message
        if max_messages:
            self._handlers |= {
                MessageUpdate: self._update_message,
                MessageDelete: self._delete_message,
                MessageDeleteBulk: self._delete_messages,
            }
        self.names = frozenset(event.NAME for event in self._handlers)

    def guild(self, id: int) -> Guild | None:
        return self._guilds.get(id)

    def guilds(self) -> list[Guild]:
        return list(self._guilds.values())

    def channel(self, id: int) -> Channel | None:
        return self._channels.get(id)

    def guild_channels(self, guild_id: int) -> list[Channel]:
        """The guild's channels, active threads included."""
        return list(self._guild_channels.get(guild_id, {}).values())

    def role(self, id: int) -> Role | None:
        return self._roles.get(id)

    def guild_roles(self, guild_id: int) -> list[Role]:
        guild = self._guilds.get(guild_id)
        return [] if guild is None else list(guild.roles)

    def scheduled_event(self, id: int) -> ScheduledEvent | None:
        return self._scheduled_events.get(id)

    def guild_scheduled_events(self, guild_id: int) -> list[ScheduledEvent]:
        return self._scheduled_events.of(guild_id)

    def stage_instance(self, id: int) -> StageInstance | None:
        return self._stage_instances.get(id)

    def guild_stage_instances(self, guild_id: int) -> list[StageInstance]:
        return self._stage_instances.of(guild_id)

    def soundboard_sound(self, id: int) -> SoundboardSound | None:
        return self._sounds.get(id)

    def guild_soundboard_sounds(self, guild_id: int) -> list[SoundboardSound]:
        return self._sounds.of(guild_id)

    def voice_state(self, guild_id: int, user_id: int) -> VoiceState | None:
        """The user's voice state, or None when they are in no voice channel."""
        return self._voice_states.get(guild_id, {}).get(user_id)

    def guild_voice_states(self, guild_id: int) -> list[VoiceState]:
        """Everyone in a voice channel of the guild."""
        return list(self._voice_states.get(guild_id, {}).values())

    def me(self, guild_id: int) -> Member | None:
        """The bot's own member in a guild."""
        current = self._current
        return None if current is None else self.member(guild_id, current.id)

    def member(self, guild_id: int, user_id: int) -> Member | None:
        """A member; only the bot's own unless the cache keeps members."""
        return self._members.get(guild_id, {}).get(user_id)

    def guild_members(self, guild_id: int) -> list[Member]:
        return list(self._members.get(guild_id, {}).values())

    def presence(self, guild_id: int, user_id: int) -> Presence | None:
        """A member's presence when the cache keeps presences; None when offline."""
        return self._presences.get(guild_id, {}).get(user_id)

    def guild_presences(self, guild_id: int) -> list[Presence]:
        """The presences of the guild's members who are not offline."""
        return list(self._presences.get(guild_id, {}).values())

    def user(self, id: int) -> User | None:
        """The bot's user, or the user of a cached member or DM channel."""
        current = self._current
        if current is not None and current.id == id:
            return current
        for members in self._members.values():
            member = members.get(id)
            if member is not None:
                return member.user
        dm = self._dm_channels.get(id)
        return None if dm is None else dm.recipients[0]

    def dm_channel(self, user_id: int) -> Channel | None:
        """The bot's DM channel with a user, if it is one of the latest kept."""
        return self._dm_channels.get(user_id)

    def message(self, id: int) -> _CachedMessage | None:
        """One of the latest `max_messages` messages."""
        return self._messages.get(id)

    def permissions(
        self, channel_id: int, member: AnyMember | None = None
    ) -> Permissions | None:
        """A member's permissions in a channel, else the bot's; None if unknown."""
        channel = self._channels.get(channel_id)
        if channel is None or channel.guild_id is None:
            return None
        parent = None
        if channel.type in _THREAD_TYPES:
            parent = self._channels.get(channel.parent_id or 0)
            if parent is None:
                return None
        return self._compute(channel.guild_id, member, channel, parent)

    def guild_permissions(
        self, guild_id: int, member: AnyMember | None = None
    ) -> Permissions | None:
        """A member's guild-wide permissions, else the bot's; None if unknown."""
        return self._compute(guild_id, member, None, None)

    def clear(self) -> None:
        self._current = None
        self._guilds.clear()
        self._channels.clear()
        self._guild_channels.clear()
        self._roles.clear()
        self._voice_states.clear()
        self._members.clear()
        self._presences.clear()
        self._messages.clear()
        self._dm_channels.clear()
        self._scheduled_events.clear()
        self._stage_instances.clear()
        self._sounds.clear()

    def _set_current_user(self, user: CurrentUser) -> None:
        """Note the bot's own user where no READY brought it, as on a resume."""
        self._current = user

    def _apply[E: Event](self, event: E) -> E:
        """Update the cache; return the event with its `old` or removed object."""
        handler = self._handlers.get(type(event))
        changes = None if handler is None else handler(event)
        return event if not changes else msgspec.structs.replace(event, **changes)

    def _compute(
        self,
        guild_id: int,
        member: AnyMember | None,
        channel: Channel | None,
        parent: Channel | None,
    ) -> Permissions | None:
        guild = self._guilds.get(guild_id)
        member = self.me(guild_id) if member is None else member
        if guild is None or member is None:
            return None
        return compute_permissions(guild, member, channel, parent=parent)

    def _keeps(self, user_id: int) -> bool:
        return self._all_members or self._is_current(user_id)

    def _is_current(self, user_id: int) -> bool:
        return self._current is not None and user_id == self._current.id

    def _start_session(self, event: Ready) -> _Changes:
        self._current = event.user
        shard_id, count = (0, 1) if event.shard is None else event.shard
        ended = {id for id in self._guilds if shard_of(id, count) == shard_id}
        for guild_id in ended:
            self._remove_guild(guild_id)
        self._forget_messages(
            lambda message: (
                message.guild_id in ended
                or (message.guild_id is None and shard_id == 0)
            )
        )
        return None

    def _create_guild(self, event: GuildCreate) -> _Changes:
        if not isinstance(event.guild, Guild):
            self._mark_unavailable(event.guild.id)
            return None
        guild_id = event.guild.id
        self._remove_guild(guild_id)
        self._guilds[guild_id] = event.guild
        self._roles |= {role.id: role for role in event.guild.roles}
        self._guild_channels[guild_id] = {}
        self._voice_states[guild_id] = {}
        for channel in (*event.channels, *event.threads):
            self._store_channel(_in_guild(channel, guild_id))
        for state in event.voice_states:
            self._store_voice_state(msgspec.structs.replace(state, guild_id=guild_id))
        if self._all_members:
            members = event.members
        else:
            me = None if self._current is None else event._member(self._current.id)
            members = () if me is None else (me,)
        self._members[guild_id] = {
            member.user.id: _stored(member) for member in members
        }
        self._presences[guild_id] = {}
        if self._keeps_presences:
            for presence in event.presences:
                self._store_presence(guild_id, presence)
        self._scheduled_events.reset(
            guild_id,
            ((each.id, _stored(each)) for each in event.guild_scheduled_events),
        )
        self._stage_instances.reset(
            guild_id, ((each.id, _stored(each)) for each in event.stage_instances)
        )
        self._sounds.reset(guild_id, _sounds(guild_id, event.soundboard_sounds))
        return None

    def _update_guild(self, event: GuildUpdate) -> _Changes:
        guild = event.guild
        cached = self._guilds.get(guild.id)
        if cached is not None:
            self._guilds[guild.id] = msgspec.structs.replace(
                guild,
                raw_json=None,
                stickers=cached.stickers,
                joined_at=cached.joined_at,
                member_count=cached.member_count,
                large=cached.large,
            )
            for role in cached.roles:
                self._roles.pop(role.id, None)
            self._roles |= {role.id: role for role in guild.roles}
        return {"old": cached}

    def _delete_guild(self, event: GuildDelete) -> _Changes:
        cached = self._guilds.get(event.guild_id)
        if event.unavailable:
            self._mark_unavailable(event.guild_id)
        else:
            self._remove_guild(event.guild_id)
            self._forget_messages(lambda message: message.guild_id == event.guild_id)
        return {"guild": cached}

    def _mark_unavailable(self, guild_id: int) -> None:
        cached = self._guilds.get(guild_id)
        if cached is not None:
            self._guilds[guild_id] = msgspec.structs.replace(cached, unavailable=True)

    def _remove_guild(self, guild_id: int) -> None:
        guild = self._guilds.pop(guild_id, None)
        for channel_id in self._guild_channels.pop(guild_id, {}):
            self._channels.pop(channel_id, None)
        for role in () if guild is None else guild.roles:
            self._roles.pop(role.id, None)
        self._voice_states.pop(guild_id, None)
        self._members.pop(guild_id, None)
        self._presences.pop(guild_id, None)
        self._scheduled_events.drop(guild_id)
        self._stage_instances.drop(guild_id)
        self._sounds.drop(guild_id)

    def _keep_scheduled_event(
        self, event: ScheduledEventCreate | ScheduledEventUpdate
    ) -> _Changes:
        scheduled = event.scheduled_event
        if scheduled.guild_id not in self._guilds:
            return None
        old = self._scheduled_events.put(
            scheduled.guild_id, scheduled.id, _stored(scheduled)
        )
        return {"old": old} if isinstance(event, ScheduledEventUpdate) else None

    def _delete_scheduled_event(self, event: ScheduledEventDelete) -> _Changes:
        scheduled = event.scheduled_event
        self._scheduled_events.pop(scheduled.guild_id, scheduled.id)
        return None

    def _keep_stage_instance(
        self, event: StageInstanceCreate | StageInstanceUpdate
    ) -> _Changes:
        stage = event.stage_instance
        if stage.guild_id not in self._guilds:
            return None
        old = self._stage_instances.put(stage.guild_id, stage.id, _stored(stage))
        return {"old": old} if isinstance(event, StageInstanceUpdate) else None

    def _delete_stage_instance(self, event: StageInstanceDelete) -> _Changes:
        stage = event.stage_instance
        self._stage_instances.pop(stage.guild_id, stage.id)
        return None

    def _keep_sound(
        self, event: SoundboardSoundCreate | SoundboardSoundUpdate
    ) -> _Changes:
        guild_id = event.sound.guild_id
        if guild_id is None or guild_id not in self._guilds:
            return None
        [(sound_id, sound)] = _sounds(guild_id, (event.sound,))
        old = self._sounds.put(guild_id, sound_id, sound)
        return {"old": old} if isinstance(event, SoundboardSoundUpdate) else None

    def _delete_sound(self, event: SoundboardSoundDelete) -> _Changes:
        return {"sound": self._sounds.pop(event.guild_id, event.sound_id)}

    def _update_sounds(self, event: SoundboardSoundsUpdate) -> _Changes:
        if event.guild_id not in self._guilds:
            return None
        olds = [
            self._sounds.put(event.guild_id, sound_id, sound)
            for sound_id, sound in _sounds(event.guild_id, event.soundboard_sounds)
        ]
        return {"old": tuple(old for old in olds if old is not None)}

    def _replace_sounds(self, event: SoundboardSounds) -> _Changes:
        if event.guild_id in self._guilds:
            sounds = _sounds(event.guild_id, event.soundboard_sounds)
            self._sounds.reset(event.guild_id, sounds)
        return None

    def _create_channel(self, event: ChannelCreate | ThreadCreate) -> _Changes:
        self._store_channel(event.channel)
        return None

    def _update_channel(self, event: ChannelUpdate | ThreadUpdate) -> _Changes:
        old = self._channels.get(event.channel.id)
        self._store_channel(event.channel)
        return {"old": old}

    def _delete_channel(self, event: ChannelDelete) -> _Changes:
        self._remove_channel(event.channel.id)
        return None

    def _delete_thread(self, event: ThreadDelete) -> _Changes:
        return {"thread": self._remove_channel(event.id)}

    def _store_channel(self, channel: Channel) -> None:
        cached = self._channels.get(channel.id)
        guild_id = channel.guild_id if cached is None else cached.guild_id
        if guild_id is None or guild_id not in self._guild_channels:
            return
        if channel.thread_metadata is not None and channel.thread_metadata.archived:
            self._remove_channel(channel.id)
            return
        channel = msgspec.structs.replace(
            channel,
            raw_json=None,
            guild_id=guild_id,
            last_message_id=None,
            message_count=None,
            member_count=None,
            last_pin_timestamp=None,
        )
        self._channels[channel.id] = channel
        self._guild_channels[guild_id][channel.id] = channel
        if channel.obfuscated:
            self._drop_threads(guild_id, {channel.id})

    def _remove_channel(self, channel_id: int) -> Channel | None:
        channel = self._channels.pop(channel_id, None)
        if channel is not None and channel.guild_id is not None:
            self._guild_channels[channel.guild_id].pop(channel_id, None)
            self._drop_threads(channel.guild_id, {channel_id})
        return channel

    def _drop_threads(self, guild_id: int, parent_ids: Collection[int] | None) -> None:
        channels = self._guild_channels[guild_id]
        for thread in [
            channel
            for channel in channels.values()
            if channel.type in _THREAD_TYPES
            and (parent_ids is None or channel.parent_id in parent_ids)
        ]:
            del channels[thread.id]
            del self._channels[thread.id]

    def _sync_threads(self, event: ThreadListSync) -> _Changes:
        if event.guild_id in self._guilds:
            parents = None if event.channel_ids is None else set(event.channel_ids)
            self._drop_threads(event.guild_id, parents)
            for thread in event.threads:
                self._store_channel(_in_guild(thread, event.guild_id))
        return None

    def _leave_thread(self, event: ThreadMembersUpdate) -> _Changes:
        thread = self._channels.get(event.id)
        user = self._current
        if (
            thread is not None
            and thread.type is ChannelType.PRIVATE_THREAD
            and user is not None
            and user.id in event.removed_member_ids
        ):
            permissions = self.permissions(thread.id)
            if permissions is not None and not permissions & Permissions.MANAGE_THREADS:
                self._remove_channel(thread.id)
        return None

    def _create_role(self, event: RoleCreate) -> _Changes:
        self._store_role(event.guild_id, event.role)
        return None

    def _update_role(self, event: RoleUpdate) -> _Changes:
        old = self._roles.get(event.role.id)
        self._store_role(event.guild_id, event.role)
        return {"old": old}

    def _delete_role(self, event: RoleDelete) -> _Changes:
        guild = self._guilds.get(event.guild_id)
        if guild is not None:
            roles = tuple(role for role in guild.roles if role.id != event.role_id)
            self._guilds[guild.id] = msgspec.structs.replace(guild, roles=roles)
        return {"role": self._roles.pop(event.role_id, None)}

    def _store_role(self, guild_id: int, role: Role) -> None:
        guild = self._guilds.get(guild_id)
        if guild is None:
            return
        roles = guild.roles
        if role.id in self._roles:
            roles = tuple(role if cached.id == role.id else cached for cached in roles)
        else:
            roles = (*roles, role)
        self._guilds[guild_id] = msgspec.structs.replace(guild, roles=roles)
        self._roles[role.id] = role

    def _update_emojis(self, event: EmojisUpdate) -> _Changes:
        guild = self._guilds.get(event.guild_id)
        if guild is None:
            return None
        self._guilds[guild.id] = msgspec.structs.replace(guild, emojis=event.emojis)
        return {"old": guild.emojis}

    def _update_stickers(self, event: StickersUpdate) -> _Changes:
        guild = self._guilds.get(event.guild_id)
        if guild is None:
            return None
        self._guilds[guild.id] = msgspec.structs.replace(guild, stickers=event.stickers)
        return {"old": guild.stickers}

    def _update_user(self, event: UserUpdate) -> _Changes:
        old = self._current
        self._current = _stored(event.user)
        return {"old": old}

    def _update_voice_state(self, event: VoiceStateUpdate) -> _Changes:
        return {"old": self._store_voice_state(event.voice_state)}

    def _store_voice_state(self, state: VoiceState) -> VoiceState | None:
        states = self._voice_states.get(state.guild_id or 0)
        if states is None:
            return None
        if state.channel_id is None:
            return states.pop(state.user_id, None)
        old = states.get(state.user_id)
        states[state.user_id] = msgspec.structs.replace(
            state, raw_json=None, member=None
        )
        return old

    def _join(self, event: MemberJoin) -> _Changes:
        self._count_member(event.guild_id, 1)
        members = self._members.get(event.guild_id)
        if members is not None and self._keeps(event.member.user.id):
            members[event.member.user.id] = _stored(event.member)
        return None

    def _leave(self, event: MemberRemove) -> _Changes:
        self._count_member(event.guild_id, -1)
        self._presences.get(event.guild_id, {}).pop(event.user.id, None)
        return {
            "member": self._members.get(event.guild_id, {}).pop(event.user.id, None)
        }

    def _count_member(self, guild_id: int, change: int) -> None:
        cached = self._guilds.get(guild_id)
        if cached is not None and cached.member_count is not None:
            self._guilds[guild_id] = msgspec.structs.replace(
                cached, member_count=cached.member_count + change
            )

    def _update_member(self, event: MemberUpdate) -> _Changes:
        members = self._members.get(event.guild_id, {})
        old = members.get(event.user.id)
        if old is not None:
            members[old.user.id] = msgspec.structs.replace(
                old,
                user=event.user,
                roles=event.roles,
                nick=event.nick,
                avatar=event.avatar,
                banner=event.banner,
                joined_at=event.joined_at,
                premium_since=event.premium_since,
                deaf=old.deaf if event.deaf is None else event.deaf,
                mute=old.mute if event.mute is None else event.mute,
                pending=event.pending,
                communication_disabled_until=event.communication_disabled_until,
                unusual_dm_activity_until=event.unusual_dm_activity_until,
                flags=event.flags,
                avatar_decoration_data=event.avatar_decoration_data,
                collectibles=event.collectibles,
            )
        return {"old": old}

    def _store_chunk(self, event: MembersChunk) -> _Changes:
        members = self._members.get(event.guild_id)
        if members is not None and self._all_members:
            members |= {member.user.id: _stored(member) for member in event.members}
        if self._keeps_presences:
            for presence in event.presences:
                self._store_presence(event.guild_id, presence)
        return None

    def _update_presence(self, event: PresenceUpdate) -> _Changes:
        return {"old": self._store_presence(event.guild_id, event.presence)}

    def _store_presence(self, guild_id: int, presence: Presence) -> Presence | None:
        presences = self._presences.get(guild_id)
        if presences is None:
            return None
        if presence.status is Status.OFFLINE:
            return presences.pop(presence.user.id, None)
        old = presences.get(presence.user.id)
        presences[presence.user.id] = _stored(presence)
        return old

    def _create_message(self, event: MessageCreate) -> _Changes:
        message = event.message
        if self._max_messages:
            self._messages[message.id] = _stored(message)
            if len(self._messages) > self._max_messages:
                del self._messages[next(iter(self._messages))]
        if (
            message.guild_id is None
            and isinstance(message, Message)
            and not self._is_current(message.author.id)
        ):
            dm = Channel(
                id=message.channel_id, type=ChannelType.DM, recipients=(message.author,)
            )
            self._store_dm(dm)
        return None

    def _store_dm(self, channel: Channel) -> None:
        """Keep a DM channel, such as one REST opened, within `max_dm_channels`."""
        if not self._max_dm_channels or channel.type is not ChannelType.DM:
            return
        user_id = channel.recipients[0].id
        old = self._dm_channels.pop(user_id, None)
        if old is not None:
            self._channels.pop(old.id, None)
        channel = _stored(channel)
        self._dm_channels[user_id] = channel
        self._channels[channel.id] = channel
        if len(self._dm_channels) > self._max_dm_channels:
            evicted = self._dm_channels.pop(next(iter(self._dm_channels)))
            self._channels.pop(evicted.id, None)

    def _update_message(self, event: MessageUpdate) -> _Changes:
        old = self._messages.get(event.message.id)
        if old is not None:
            self._messages[old.id] = _stored(event.message)
        return {"old": old}

    def _delete_message(self, event: MessageDelete) -> _Changes:
        return {"message": self._messages.pop(event.id, None)}

    def _delete_messages(self, event: MessageDeleteBulk) -> _Changes:
        removed = (self._messages.pop(id, None) for id in event.ids)
        return {"messages": tuple(m for m in removed if m is not None)}

    def _forget_messages(self, forget: Callable[[_CachedMessage], bool]) -> None:
        if self._messages:
            self._messages = {
                id: message
                for id, message in self._messages.items()
                if not forget(message)
            }


def _stored[M: msgspec.Struct](model: M) -> M:
    return msgspec.structs.replace(model, raw_json=None)


def _sounds(
    guild_id: int, sounds: Iterable[SoundboardSound]
) -> list[tuple[int, SoundboardSound]]:
    return [
        (
            sound.sound_id,
            msgspec.structs.replace(sound, raw_json=None, guild_id=guild_id),
        )
        for sound in sounds
    ]


def _in_guild(channel: Channel, guild_id: int) -> Channel:
    if channel.guild_id is None:
        return msgspec.structs.replace(channel, guild_id=guild_id)
    return channel
