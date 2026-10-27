from collections.abc import Collection
from typing import ClassVar

import msgspec

from lyrecord.enums import ChannelType
from lyrecord.events import (
    ChannelCreate,
    ChannelDelete,
    ChannelUpdate,
    Event,
    GuildCreate,
    GuildDelete,
    GuildUpdate,
    Ready,
    RoleCreate,
    RoleDelete,
    RoleUpdate,
    ThreadCreate,
    ThreadDelete,
    ThreadListSync,
    ThreadUpdate,
)
from lyrecord.models import Channel, Guild, Role, User

THREAD_TYPES = frozenset(
    {
        ChannelType.ANNOUNCEMENT_THREAD,
        ChannelType.PUBLIC_THREAD,
        ChannelType.PRIVATE_THREAD,
    }
)


class Cache:
    """The bot user, guilds, channels, threads and roles, kept current by events.

    Channels are stored as received, obfuscated ones included, except the fields
    CHANNEL_UPDATE does not keep current (last_message_id, message_count,
    member_count, last_pin_timestamp), which are None.
    """

    NAMES: ClassVar[frozenset[str]] = frozenset(
        event.NAME
        for event in (
            Ready,
            GuildCreate,
            GuildUpdate,
            GuildDelete,
            ChannelCreate,
            ChannelUpdate,
            ChannelDelete,
            ThreadCreate,
            ThreadUpdate,
            ThreadDelete,
            ThreadListSync,
            RoleCreate,
            RoleUpdate,
            RoleDelete,
        )
    )

    def __init__(self) -> None:
        self.user: User | None = None
        self._guilds: dict[int, Guild] = {}
        self._channels: dict[int, Channel] = {}
        self._guild_channels: dict[int, dict[int, Channel]] = {}
        self._roles: dict[int, Role] = {}
        self._guild_roles: dict[int, dict[int, Role]] = {}

    def guild(self, id: int) -> Guild | None:
        return self._guilds.get(id)

    def guilds(self) -> list[Guild]:
        return list(self._guilds.values())

    def channel(self, id: int) -> Channel | None:
        return self._channels.get(id)

    def guild_channels(self, guild_id: int) -> list[Channel]:
        """The guild's channels, threads included."""
        return list(self._guild_channels.get(guild_id, {}).values())

    def role(self, id: int) -> Role | None:
        return self._roles.get(id)

    def guild_roles(self, guild_id: int) -> list[Role]:
        return list(self._guild_roles.get(guild_id, {}).values())

    def clear(self) -> None:
        self.user = None
        self._guilds.clear()
        self._channels.clear()
        self._guild_channels.clear()
        self._roles.clear()
        self._guild_roles.clear()

    def apply(self, event: Event) -> None:
        """Update the cache from an event named in `NAMES`; others change nothing."""
        match event:
            case Ready():
                self.clear()
                self.user = event.user
            case GuildCreate(guild=Guild(unavailable=True)):
                self._mark_unavailable(event.guild.id)
            case GuildCreate():
                self._add_guild(event)
            case GuildUpdate():
                self._update_guild(event.guild)
            case GuildDelete(unavailable=True):
                self._mark_unavailable(event.guild_id)
            case GuildDelete():
                self._remove_guild(event.guild_id)
            case ChannelCreate() | ChannelUpdate() | ThreadCreate() | ThreadUpdate():
                self._store_channel(event.channel)
            case ChannelDelete() | ThreadDelete():
                self._remove_channel(event.channel.id)
            case ThreadListSync(guild_id=int(guild_id)) if guild_id in self._guilds:
                parents = None if event.channel_ids is None else set(event.channel_ids)
                self._drop_threads(guild_id, parents)
                for thread in event.threads:
                    self._store_channel(_in_guild(thread, guild_id))
            case (
                RoleCreate(guild_id=int(guild_id), role=Role() as role)
                | RoleUpdate(guild_id=int(guild_id), role=Role() as role)
            ):
                self._store_role(guild_id, role)
            case RoleDelete(guild_id=int(guild_id), role_id=int(role_id)):
                self._guild_roles.get(guild_id, {}).pop(role_id, None)
                self._roles.pop(role_id, None)

    def _add_guild(self, event: GuildCreate) -> None:
        guild_id = event.guild.id
        self._remove_guild(guild_id)
        self._guilds[guild_id] = event.guild
        self._guild_channels[guild_id] = {}
        self._guild_roles[guild_id] = {}
        for channel in (*event.channels, *event.threads):
            self._store_channel(_in_guild(channel, guild_id))
        for role in event.roles:
            self._store_role(guild_id, role)

    def _update_guild(self, guild: Guild) -> None:
        cached = self._guilds.get(guild.id)
        if cached is not None:
            self._guilds[guild.id] = msgspec.structs.replace(
                guild,
                raw_json=None,
                member_count=cached.member_count,
                large=cached.large,
            )

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

    def _store_channel(self, channel: Channel) -> None:
        cached = self._channels.get(channel.id)
        guild_id = channel.guild_id if cached is None else cached.guild_id
        if guild_id is None or guild_id not in self._guild_channels:
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

    def _remove_channel(self, channel_id: int) -> None:
        channel = self._channels.pop(channel_id, None)
        if channel is not None and channel.guild_id is not None:
            self._guild_channels[channel.guild_id].pop(channel_id, None)
            self._drop_threads(channel.guild_id, {channel_id})

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

    def _store_role(self, guild_id: int, role: Role) -> None:
        if guild_id in self._guild_roles:
            self._roles[role.id] = role
            self._guild_roles[guild_id][role.id] = role


def _in_guild(channel: Channel, guild_id: int) -> Channel:
    if channel.guild_id is None:
        return msgspec.structs.replace(channel, guild_id=guild_id)
    return channel
