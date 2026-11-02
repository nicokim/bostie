from collections.abc import Callable, Collection
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
    User,
    VoiceState,
    WebhookMessage,
)
from bostie.permissions import THREAD_TYPES, compute_permissions

type CachedMessage = Message | WebhookMessage
type Changes = dict[str, Any] | None


class Cache:
    """Guilds, channels, active threads, voice states and the bot's member and user.

    Members, presences and the latest messages are kept only when asked for.
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
    ) -> None:
        if max_messages < 0:
            raise ValueError(f"max_messages must be 0 or more, got {max_messages}")
        self._current: CurrentUser | None = None
        self._max_messages = max_messages
        self._all_members = members
        self._keeps_presences = presences
        self._guilds: dict[int, Guild] = {}
        self._channels: dict[int, Channel] = {}
        self._guild_channels: dict[int, dict[int, Channel]] = {}
        self._roles: dict[int, Role] = {}
        self._voice_states: dict[int, dict[int, VoiceState]] = {}
        self._members: dict[int, dict[int, Member]] = {}
        self._presences: dict[int, dict[int, Presence]] = {}
        self._messages: dict[int, CachedMessage] = {}
        self._handlers: dict[type[Event], Callable[[Any], Changes]] = {
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
        }
        if members or presences:
            self._handlers[MembersChunk] = self._store_chunk
        if presences:
            self._handlers[PresenceUpdate] = self._update_presence
        if max_messages:
            self._handlers |= {
                MessageCreate: self._create_message,
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
        """The bot's user, or the user of a cached member."""
        current = self._current
        if current is not None and current.id == id:
            return current
        for members in self._members.values():
            member = members.get(id)
            if member is not None:
                return member.user
        return None

    def message(self, id: int) -> CachedMessage | None:
        """One of the latest `max_messages` messages."""
        return self._messages.get(id)

    def permissions(
        self, channel_id: int, member: Member | None = None
    ) -> Permissions | None:
        """A member's permissions in a channel, else the bot's; None if unknown."""
        channel = self._channels.get(channel_id)
        if channel is None or channel.guild_id is None:
            return None
        parent = None
        if channel.type in THREAD_TYPES:
            parent = self._channels.get(channel.parent_id or 0)
            if parent is None:
                return None
        return self._compute(channel.guild_id, member, channel, parent)

    def guild_permissions(
        self, guild_id: int, member: Member | None = None
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

    def apply[E: Event](self, event: E) -> E:
        """Update the cache; return the event with its `old` or removed object."""
        handler = self._handlers.get(type(event))
        changes = None if handler is None else handler(event)
        return event if not changes else msgspec.structs.replace(event, **changes)

    def _compute(
        self,
        guild_id: int,
        member: Member | None,
        channel: Channel | None,
        parent: Channel | None,
    ) -> Permissions | None:
        guild = self._guilds.get(guild_id)
        member = self.me(guild_id) if member is None else member
        if guild is None or member is None:
            return None
        return compute_permissions(guild, member, channel, parent=parent)

    def _keeps(self, user_id: int) -> bool:
        current = self._current
        return self._all_members or (current is not None and user_id == current.id)

    def _start_session(self, event: Ready) -> Changes:
        self._current = event.user
        shard_id, count = (0, 1) if event.shard is None else event.shard
        ended = {id for id in self._guilds if (id >> 22) % count == shard_id}
        for guild_id in ended:
            self._remove_guild(guild_id)
        self._forget_messages(
            lambda message: (
                message.guild_id in ended
                or (message.guild_id is None and shard_id == 0)
            )
        )
        return None

    def _create_guild(self, event: GuildCreate) -> Changes:
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
        self._members[guild_id] = {
            member.user.id: _stored(member)
            for member in event.members
            if self._keeps(member.user.id)
        }
        self._presences[guild_id] = {}
        if self._keeps_presences:
            for presence in event.presences:
                self._store_presence(guild_id, presence)
        return None

    def _update_guild(self, event: GuildUpdate) -> Changes:
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

    def _delete_guild(self, event: GuildDelete) -> Changes:
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

    def _create_channel(self, event: ChannelCreate | ThreadCreate) -> Changes:
        self._store_channel(event.channel)
        return None

    def _update_channel(self, event: ChannelUpdate | ThreadUpdate) -> Changes:
        old = self._channels.get(event.channel.id)
        self._store_channel(event.channel)
        return {"old": old}

    def _delete_channel(self, event: ChannelDelete) -> Changes:
        self._remove_channel(event.channel.id)
        return None

    def _delete_thread(self, event: ThreadDelete) -> Changes:
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
            if channel.type in THREAD_TYPES
            and (parent_ids is None or channel.parent_id in parent_ids)
        ]:
            del channels[thread.id]
            del self._channels[thread.id]

    def _sync_threads(self, event: ThreadListSync) -> Changes:
        if event.guild_id in self._guilds:
            parents = None if event.channel_ids is None else set(event.channel_ids)
            self._drop_threads(event.guild_id, parents)
            for thread in event.threads:
                self._store_channel(_in_guild(thread, event.guild_id))
        return None

    def _leave_thread(self, event: ThreadMembersUpdate) -> Changes:
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

    def _create_role(self, event: RoleCreate) -> Changes:
        self._store_role(event.guild_id, event.role)
        return None

    def _update_role(self, event: RoleUpdate) -> Changes:
        old = self._roles.get(event.role.id)
        self._store_role(event.guild_id, event.role)
        return {"old": old}

    def _delete_role(self, event: RoleDelete) -> Changes:
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

    def _update_emojis(self, event: EmojisUpdate) -> Changes:
        guild = self._guilds.get(event.guild_id)
        if guild is None:
            return None
        self._guilds[guild.id] = msgspec.structs.replace(guild, emojis=event.emojis)
        return {"old": guild.emojis}

    def _update_stickers(self, event: StickersUpdate) -> Changes:
        guild = self._guilds.get(event.guild_id)
        if guild is None:
            return None
        self._guilds[guild.id] = msgspec.structs.replace(guild, stickers=event.stickers)
        return {"old": guild.stickers}

    def _update_user(self, event: UserUpdate) -> Changes:
        old = self._current
        self._current = _stored(event.user)
        return {"old": old}

    def _update_voice_state(self, event: VoiceStateUpdate) -> Changes:
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

    def _join(self, event: MemberJoin) -> Changes:
        self._count_member(event.guild_id, 1)
        members = self._members.get(event.guild_id)
        if members is not None and self._keeps(event.member.user.id):
            members[event.member.user.id] = _stored(event.member)
        return None

    def _leave(self, event: MemberRemove) -> Changes:
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

    def _update_member(self, event: MemberUpdate) -> Changes:
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
                flags=event.flags,
                avatar_decoration_data=event.avatar_decoration_data,
                collectibles=event.collectibles,
            )
        return {"old": old}

    def _store_chunk(self, event: MembersChunk) -> Changes:
        members = self._members.get(event.guild_id)
        if members is not None and self._all_members:
            members |= {member.user.id: _stored(member) for member in event.members}
        if self._keeps_presences:
            for presence in event.presences:
                self._store_presence(event.guild_id, presence)
        return None

    def _update_presence(self, event: PresenceUpdate) -> Changes:
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

    def _create_message(self, event: MessageCreate) -> Changes:
        self._messages[event.message.id] = _stored(event.message)
        if len(self._messages) > self._max_messages:
            del self._messages[next(iter(self._messages))]
        return None

    def _update_message(self, event: MessageUpdate) -> Changes:
        old = self._messages.get(event.message.id)
        if old is not None:
            self._messages[old.id] = _stored(event.message)
        return {"old": old}

    def _delete_message(self, event: MessageDelete) -> Changes:
        return {"message": self._messages.pop(event.id, None)}

    def _delete_messages(self, event: MessageDeleteBulk) -> Changes:
        removed = (self._messages.pop(id, None) for id in event.ids)
        return {"messages": tuple(m for m in removed if m is not None)}

    def _forget_messages(self, forget: Callable[[CachedMessage], bool]) -> None:
        if self._messages:
            self._messages = {
                id: message
                for id, message in self._messages.items()
                if not forget(message)
            }


def _stored[M: msgspec.Struct](model: M) -> M:
    return msgspec.structs.replace(model, raw_json=None)


def _in_guild(channel: Channel, guild_id: int) -> Channel:
    if channel.guild_id is None:
        return msgspec.structs.replace(channel, guild_id=guild_id)
    return channel
