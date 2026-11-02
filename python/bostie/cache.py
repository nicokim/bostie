from collections.abc import Callable, Collection
from typing import Any

import msgspec

from bostie.enums import ChannelType, Permissions
from bostie.events import (
    ChannelCreate,
    ChannelDelete,
    ChannelUpdate,
    Event,
    GuildCreate,
    GuildDelete,
    GuildUpdate,
    MemberJoin,
    MemberRemove,
    MemberUpdate,
    MessageCreate,
    MessageDelete,
    MessageDeleteBulk,
    MessageUpdate,
    Ready,
    RoleCreate,
    RoleDelete,
    RoleUpdate,
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
    Role,
    User,
    VoiceState,
    WebhookMessage,
)
from bostie.permissions import THREAD_TYPES, compute_permissions

type CachedMessage = Message | WebhookMessage
type Changes = dict[str, Any] | None


class Cache:
    """What the gateway told the bot, kept current by events.

    The bot user, guilds, channels, active threads, roles, voice states and the
    bot's own member in each guild, plus the latest messages with `max_messages`.
    Channels are stored as received, obfuscated ones included, except the fields
    CHANNEL_UPDATE does not keep current (last_message_id, message_count,
    member_count, last_pin_timestamp), which are None. `Guild.member_count` follows
    joins and leaves only with Intents.GUILD_MEMBERS.
    """

    def __init__(self, max_messages: int = 0) -> None:
        if max_messages < 0:
            raise ValueError(f"max_messages must be 0 or more, got {max_messages}")
        self.user: CurrentUser | None = None
        self._max_messages = max_messages
        self._guilds: dict[int, Guild] = {}
        self._channels: dict[int, Channel] = {}
        self._guild_channels: dict[int, dict[int, Channel]] = {}
        self._roles: dict[int, Role] = {}
        self._guild_roles: dict[int, dict[int, Role]] = {}
        self._voice_states: dict[int, dict[int, VoiceState]] = {}
        self._me: dict[int, Member] = {}
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
            UserUpdate: self._update_user,
            VoiceStateUpdate: self._update_voice_state,
            MemberJoin: self._join,
            MemberRemove: self._leave,
            MemberUpdate: self._update_me,
        }
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
        return list(self._guild_roles.get(guild_id, {}).values())

    def voice_state(self, guild_id: int, user_id: int) -> VoiceState | None:
        """The user's voice state, or None when they are in no voice channel."""
        return self._voice_states.get(guild_id, {}).get(user_id)

    def guild_voice_states(self, guild_id: int) -> list[VoiceState]:
        """Everyone in a voice channel of the guild."""
        return list(self._voice_states.get(guild_id, {}).values())

    def me(self, guild_id: int) -> Member | None:
        """The bot's own member in a guild."""
        return self._me.get(guild_id)

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
        self.user = None
        self._guilds.clear()
        self._channels.clear()
        self._guild_channels.clear()
        self._roles.clear()
        self._guild_roles.clear()
        self._voice_states.clear()
        self._me.clear()
        self._messages.clear()

    def apply[E: Event](self, event: E) -> E:
        """Update the cache from an event and return it with what the cache held.

        Update events get `old`, delete events the removed object; events not in
        `names` change nothing.
        """
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
        member = self._me.get(guild_id) if member is None else member
        if guild is None or member is None:
            return None
        roles = self._guild_roles.get(guild_id, {}).values()
        return compute_permissions(guild, roles, member, channel, parent=parent)

    def _is_me(self, user: User) -> bool:
        return self.user is not None and user.id == self.user.id

    def _start_session(self, event: Ready) -> Changes:
        self.user = event.user
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
        self._guild_channels[guild_id] = {}
        self._guild_roles[guild_id] = {}
        self._voice_states[guild_id] = {}
        for channel in (*event.channels, *event.threads):
            self._store_channel(_in_guild(channel, guild_id))
        for role in event.roles:
            self._store_role(guild_id, role)
        for state in event.voice_states:
            self._store_voice_state(msgspec.structs.replace(state, guild_id=guild_id))
        user = self.user
        if user is not None:
            me = next((m for m in event.members if m.user.id == user.id), None)
            if me is not None:
                self._me[guild_id] = _stored(me)
        return None

    def _update_guild(self, event: GuildUpdate) -> Changes:
        guild = event.guild
        cached = self._guilds.get(guild.id)
        if cached is not None:
            self._guilds[guild.id] = msgspec.structs.replace(
                guild,
                raw_json=None,
                joined_at=cached.joined_at,
                member_count=cached.member_count,
                large=cached.large,
            )
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
        self._guilds.pop(guild_id, None)
        for channel_id in self._guild_channels.pop(guild_id, {}):
            self._channels.pop(channel_id, None)
        for role_id in self._guild_roles.pop(guild_id, {}):
            self._roles.pop(role_id, None)
        self._voice_states.pop(guild_id, None)
        self._me.pop(guild_id, None)

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
        user = self.user
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
        self._guild_roles.get(event.guild_id, {}).pop(event.role_id, None)
        return {"role": self._roles.pop(event.role_id, None)}

    def _store_role(self, guild_id: int, role: Role) -> None:
        if guild_id in self._guild_roles:
            self._roles[role.id] = role
            self._guild_roles[guild_id][role.id] = role

    def _update_user(self, event: UserUpdate) -> Changes:
        old = self.user
        self.user = _stored(event.user)
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
        if self._is_me(event.member.user):
            self._me[event.guild_id] = _stored(event.member)
        return None

    def _leave(self, event: MemberRemove) -> Changes:
        self._count_member(event.guild_id, -1)
        return None

    def _count_member(self, guild_id: int, change: int) -> None:
        cached = self._guilds.get(guild_id)
        if cached is not None and cached.member_count is not None:
            self._guilds[guild_id] = msgspec.structs.replace(
                cached, member_count=cached.member_count + change
            )

    def _update_me(self, event: MemberUpdate) -> Changes:
        me = self._me.get(event.guild_id)
        if me is not None and self._is_me(event.user):
            self._me[event.guild_id] = msgspec.structs.replace(
                me,
                user=event.user,
                roles=event.roles,
                nick=event.nick,
                avatar=event.avatar,
                banner=event.banner,
                joined_at=event.joined_at,
                premium_since=event.premium_since,
                deaf=me.deaf if event.deaf is None else event.deaf,
                mute=me.mute if event.mute is None else event.mute,
                pending=event.pending,
                communication_disabled_until=event.communication_disabled_until,
                flags=event.flags,
                avatar_decoration_data=event.avatar_decoration_data,
                collectibles=event.collectibles,
            )
        return None

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
