from collections.abc import Mapping, Sequence
from functools import partial
from typing import Required, TypedDict, Unpack

from bostie.enums import (
    ApplicationIntegrationType,
    CommandType,
    EntryPointCommandHandlerType,
    InteractionContextType,
    Permissions,
)
from bostie.models import (
    ApplicationCommand,
    ApplicationCommandOption,
    GuildCommandPermissions,
    decode,
)
from bostie.rest.base import Group, each, wire


class _CommandFields(TypedDict, total=False):
    name_localizations: Mapping[str, str] | None
    description: str
    description_localizations: Mapping[str, str] | None
    options: Sequence[ApplicationCommandOption]
    default_member_permissions: Permissions | None
    nsfw: bool


class _GlobalCommandFields(TypedDict, total=False):
    integration_types: Sequence[ApplicationIntegrationType]
    contexts: Sequence[InteractionContextType]
    handler: EntryPointCommandHandlerType


class CommandCreateGuild(_CommandFields, total=False):
    name: Required[str]
    type: CommandType


class CommandCreate(CommandCreateGuild, _GlobalCommandFields, total=False):
    pass


class CommandEditGuild(_CommandFields, total=False):
    name: str


class CommandEdit(CommandEditGuild, _GlobalCommandFields, total=False):
    pass


class CommandBulkOverwrite(CommandCreate, total=False):
    """A command as the bulk overwrites take it, with its `id` if it exists."""

    id: int


class CommandList(TypedDict, total=False):
    with_localizations: bool


class Commands(Group):
    async def bulk_overwrite(
        self, application_id: int, /, commands: Sequence[CommandBulkOverwrite]
    ) -> tuple[ApplicationCommand, ...]:
        """Replace every global command of the app with `commands`."""
        return await self._http._decoded(
            each(partial(decode, ApplicationCommand)),
            "PUT",
            "/applications/{application_id}/commands",
            json=[wire(command, CommandBulkOverwrite) for command in commands],
            application_id=application_id,
        )

    async def bulk_overwrite_guild(
        self,
        application_id: int,
        guild_id: int,
        /,
        commands: Sequence[CommandBulkOverwrite],
    ) -> tuple[ApplicationCommand, ...]:
        """Replace every command of the app in one guild with `commands`."""
        return await self._http._decoded(
            each(partial(decode, ApplicationCommand)),
            "PUT",
            "/applications/{application_id}/guilds/{guild_id}/commands",
            json=[wire(command, CommandBulkOverwrite) for command in commands],
            application_id=application_id,
            guild_id=guild_id,
        )

    async def create(
        self, application_id: int, /, **options: Unpack[CommandCreate]
    ) -> ApplicationCommand:
        """Create a global command, or replace the one with the same name."""
        return await self._http._decoded(
            partial(decode, ApplicationCommand),
            "POST",
            "/applications/{application_id}/commands",
            json=wire(options, CommandCreate),
            application_id=application_id,
        )

    async def create_guild(
        self,
        application_id: int,
        guild_id: int,
        /,
        **options: Unpack[CommandCreateGuild],
    ) -> ApplicationCommand:
        """Create a guild command, or replace the one with the same name."""
        return await self._http._decoded(
            partial(decode, ApplicationCommand),
            "POST",
            "/applications/{application_id}/guilds/{guild_id}/commands",
            json=wire(options, CommandCreateGuild),
            application_id=application_id,
            guild_id=guild_id,
        )

    async def delete(self, application_id: int, command_id: int, /) -> None:
        await self._http._request(
            "DELETE",
            "/applications/{application_id}/commands/{command_id}",
            application_id=application_id,
            command_id=command_id,
        )

    async def delete_guild(
        self, application_id: int, guild_id: int, command_id: int, /
    ) -> None:
        await self._http._request(
            "DELETE",
            "/applications/{application_id}/guilds/{guild_id}/commands/{command_id}",
            application_id=application_id,
            guild_id=guild_id,
            command_id=command_id,
        )

    async def edit(
        self, application_id: int, command_id: int, /, **options: Unpack[CommandEdit]
    ) -> ApplicationCommand:
        return await self._http._decoded(
            partial(decode, ApplicationCommand),
            "PATCH",
            "/applications/{application_id}/commands/{command_id}",
            json=wire(options, CommandEdit),
            application_id=application_id,
            command_id=command_id,
        )

    async def edit_guild(
        self,
        application_id: int,
        guild_id: int,
        command_id: int,
        /,
        **options: Unpack[CommandEditGuild],
    ) -> ApplicationCommand:
        return await self._http._decoded(
            partial(decode, ApplicationCommand),
            "PATCH",
            "/applications/{application_id}/guilds/{guild_id}/commands/{command_id}",
            json=wire(options, CommandEditGuild),
            application_id=application_id,
            guild_id=guild_id,
            command_id=command_id,
        )

    async def fetch(
        self, application_id: int, command_id: int, /
    ) -> ApplicationCommand:
        return await self._http._decoded(
            partial(decode, ApplicationCommand),
            "GET",
            "/applications/{application_id}/commands/{command_id}",
            application_id=application_id,
            command_id=command_id,
        )

    async def fetch_guild(
        self, application_id: int, guild_id: int, command_id: int, /
    ) -> ApplicationCommand:
        return await self._http._decoded(
            partial(decode, ApplicationCommand),
            "GET",
            "/applications/{application_id}/guilds/{guild_id}/commands/{command_id}",
            application_id=application_id,
            guild_id=guild_id,
            command_id=command_id,
        )

    async def fetch_permissions(
        self, application_id: int, guild_id: int, command_id: int, /
    ) -> GuildCommandPermissions:
        return await self._http._decoded(
            partial(decode, GuildCommandPermissions),
            "GET",
            "/applications/{application_id}/guilds/{guild_id}/commands/{command_id}/permissions",
            application_id=application_id,
            guild_id=guild_id,
            command_id=command_id,
        )

    async def list(
        self, application_id: int, /, **options: Unpack[CommandList]
    ) -> tuple[ApplicationCommand, ...]:
        return await self._http._decoded(
            each(partial(decode, ApplicationCommand)),
            "GET",
            "/applications/{application_id}/commands",
            query=wire(options, CommandList),
            application_id=application_id,
        )

    async def list_guild(
        self, application_id: int, guild_id: int, /, **options: Unpack[CommandList]
    ) -> tuple[ApplicationCommand, ...]:
        return await self._http._decoded(
            each(partial(decode, ApplicationCommand)),
            "GET",
            "/applications/{application_id}/guilds/{guild_id}/commands",
            query=wire(options, CommandList),
            application_id=application_id,
            guild_id=guild_id,
        )

    async def list_permissions(
        self, application_id: int, guild_id: int, /
    ) -> tuple[GuildCommandPermissions, ...]:
        return await self._http._decoded(
            each(partial(decode, GuildCommandPermissions)),
            "GET",
            "/applications/{application_id}/guilds/{guild_id}/commands/permissions",
            application_id=application_id,
            guild_id=guild_id,
        )
