import msgspec

from bostie.enums import (
    ApplicationFlags,
    CommandType,
    ComponentType,
    InteractionContextType,
    InteractionType,
    OptionType,
    Permissions,
)
from bostie.models.base import Model, permissions_from
from bostie.models.channel import Channel
from bostie.models.guild import Member, PartialMember, Role
from bostie.models.message import Attachment, Message, PartialMessage, in_guild
from bostie.models.user import User


class InteractionDataOption(Model, kw_only=True):
    """An option the user filled; subcommands and groups nest their options."""

    name: str | None = None
    type: OptionType | None = None
    value: str | int | float | bool | None = None
    options: tuple["InteractionDataOption", ...] = ()
    focused: bool | None = None


class Resolved(Model, kw_only=True):
    """The users, members, roles, channels, messages and files an interaction names."""

    users: dict[int, User] = msgspec.field(default_factory=dict)
    members: dict[int, PartialMember] = msgspec.field(default_factory=dict)
    roles: dict[int, Role] = msgspec.field(default_factory=dict)
    channels: dict[int, Channel] = msgspec.field(default_factory=dict)
    messages: dict[int, PartialMessage] = msgspec.field(default_factory=dict)
    attachments: dict[int, Attachment] = msgspec.field(default_factory=dict)

    def member(self, id: int) -> Member | None:
        """The member with this id, with its user."""
        member = self.members.get(id)
        user = self.users.get(id)
        if member is None or user is None:
            return None
        return Member(user=user, **msgspec.structs.asdict(member))


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
    _flags: str | None = msgspec.field(default=None, name="flags_new")
    bot_public: bool | None = None

    @property
    def flags(self) -> ApplicationFlags | None:
        return None if self._flags is None else ApplicationFlags(int(self._flags))


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
    _app_permissions: str | None = msgspec.field(default=None, name="app_permissions")
    locale: str | None = None
    guild_locale: str | None = None
    authorizing_integration_owners: dict[str, int] | None = None
    context: InteractionContextType | None = None
    attachment_size_limit: int | None = None

    def __post_init__(self) -> None:
        if self.message is not None:
            in_guild(self.message, self.guild_id)

    @property
    def app_permissions(self) -> Permissions | None:
        return permissions_from(self._app_permissions)

    @property
    def author(self) -> User | None:
        """The user who triggered the interaction, in a guild or in a DM."""
        if self.member is not None and self.member.user is not None:
            return self.member.user
        return self.user
