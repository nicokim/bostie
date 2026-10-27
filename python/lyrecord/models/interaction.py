import msgspec

from lyrecord.enums import (
    CommandType,
    ComponentType,
    InteractionContextType,
    InteractionType,
    OptionType,
    Permissions,
)
from lyrecord.models.base import Model
from lyrecord.models.channel import Channel
from lyrecord.models.guild import Member, Role
from lyrecord.models.message import Attachment, Message
from lyrecord.models.user import User


class InteractionDataOption(Model, kw_only=True):
    """An option the user filled; subcommands and groups nest their options."""

    name: str | None = None
    type: OptionType | None = None
    value: str | int | float | bool | None = None
    options: tuple["InteractionDataOption", ...] = ()
    focused: bool | None = None


class Resolved(Model, kw_only=True):
    """The users, members, roles, channels, messages and files an interaction names."""

    users: dict[int, User] | None = None
    members: dict[int, Member] | None = None
    roles: dict[int, Role] | None = None
    channels: dict[int, Channel] | None = None
    messages: dict[int, Message] | None = None
    attachments: dict[int, Attachment] | None = None

    def member(self, id: int) -> Member | None:
        """The member with this id, with its user filled in."""
        member = self.members.get(id) if self.members else None
        user = self.users.get(id) if self.users else None
        if member is None or user is None:
            return member
        return msgspec.structs.replace(member, user=user)


class InteractionData(Model, kw_only=True):
    """Command data or component data; the interaction's `type` tells which."""

    id: int | None = None
    name: str | None = None
    type: CommandType | None = None
    resolved: Resolved | None = None
    options: tuple[InteractionDataOption, ...] = ()
    guild_id: int | None = None
    target_id: int | None = None
    custom_id: str | None = None
    component_type: ComponentType | None = None
    values: tuple[str, ...] = ()


class Application(Model, kw_only=True):
    id: int
    name: str | None = None
    flags: int | None = None
    flags_new: int | None = None
    bot_public: bool | None = None


class Interaction(Model, kw_only=True):
    """A slash command, autocomplete request or component use."""

    id: int
    application_id: int | None = None
    type: InteractionType | None = None
    data: InteractionData | None = None
    guild_id: int | None = None
    channel: Channel | None = None
    channel_id: int | None = None
    member: Member | None = None
    user: User | None = None
    token: str | None = None
    version: int | None = None
    message: Message | None = None
    app_permissions: Permissions | None = None
    locale: str | None = None
    guild_locale: str | None = None
    authorizing_integration_owners: dict[str, int] | None = None
    context: InteractionContextType | None = None
    attachment_size_limit: int | None = None

    @property
    def author(self) -> User | None:
        """The user who triggered the interaction, in a guild or in a DM."""
        if self.member is not None and self.member.user is not None:
            return self.member.user
        return self.user
