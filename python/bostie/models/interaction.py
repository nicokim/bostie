from typing import cast

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
from bostie.models.base import Model, decode, decoder
from bostie.models.channel import PartialChannel
from bostie.models.guild import Member, PartialMember, Role, with_user
from bostie.models.message import (
    Attachment,
    Message,
    PartialMessage,
    WebhookMessage,
    nested_message,
)
from bostie.models.user import User


class InteractionDataOption(Model, kw_only=True):
    """An option the user filled; subcommands and groups nest their options."""

    name: str
    type: OptionType
    value: str | int | float | bool | None = None
    options: tuple["InteractionDataOption", ...] = ()
    focused: bool = False


class Resolved(Model, kw_only=True):
    """The users, members, roles, channels, messages and files an interaction names."""

    users: dict[int, User] = msgspec.field(default_factory=dict[int, User])
    members: dict[int, PartialMember] = msgspec.field(
        default_factory=dict[int, PartialMember]
    )
    roles: dict[int, Role] = msgspec.field(default_factory=dict[int, Role])
    channels: dict[int, PartialChannel] = msgspec.field(
        default_factory=dict[int, PartialChannel]
    )
    messages: dict[int, PartialMessage] = msgspec.field(
        default_factory=dict[int, PartialMessage]
    )
    attachments: dict[int, Attachment] = msgspec.field(
        default_factory=dict[int, Attachment]
    )

    def member(self, id: int) -> Member | None:
        """The member with this id, with its user."""
        member = self.members.get(id)
        user = self.users.get(id)
        if member is None or user is None:
            return None
        return with_user(member, user)


class CommandData(Model, kw_only=True):
    """The command an interaction invokes and the options the user filled."""

    id: int
    name: str
    type: CommandType
    resolved: Resolved = msgspec.field(default_factory=Resolved)
    options: tuple[InteractionDataOption, ...] = ()
    guild_id: int | None = None
    target_id: int | None = None


class ComponentData(Model, kw_only=True):
    """The component an interaction comes from and the values picked in it."""

    custom_id: str
    component_type: ComponentType
    values: tuple[str, ...] = ()
    resolved: Resolved = msgspec.field(default_factory=Resolved)


class PartialApplication(Model, kw_only=True):
    id: int
    _flags: str = msgspec.field(name="flags_new")

    @property
    def flags(self) -> ApplicationFlags:
        return ApplicationFlags(int(self._flags))


class _Interaction(Model, kw_only=True):
    id: int
    application_id: int
    type: InteractionType
    token: str
    version: int
    _app_permissions: str = msgspec.field(name="app_permissions")
    locale: str
    authorizing_integration_owners: dict[str, int]
    attachment_size_limit: int
    guild_id: int | None = None
    channel: PartialChannel | None = None
    channel_id: int | None = None
    member: Member | None = None
    _user: User | None = msgspec.field(default=None, name="user")
    guild_locale: str | None = None
    context: InteractionContextType | None = None

    def __post_init__(self) -> None:
        if self.member is None and self._user is None:
            missing = "user" if self.guild_id is None else "member"
            raise ValueError(f"Object missing required field `{missing}`")

    @property
    def user(self) -> User:
        """The user who triggered the interaction, in a guild or in a DM."""
        return self.member.user if self.member is not None else cast("User", self._user)

    @property
    def app_permissions(self) -> Permissions:
        return Permissions(int(self._app_permissions))


class CommandInteraction(_Interaction, kw_only=True):
    """A slash command run."""

    data: CommandData


class AutocompleteInteraction(_Interaction, kw_only=True):
    """A request for choices while the user types an option."""

    data: CommandData


class ComponentInteraction(_Interaction, kw_only=True):
    """A button press or a select menu pick."""

    data: ComponentData
    _message: msgspec.Raw = msgspec.field(name="message")

    @property
    def message(self) -> Message | WebhookMessage:
        """The message the component is on, decoded on each access."""
        return nested_message(self._message, self.guild_id)


class ModalSubmitData(Model, kw_only=True):
    """The modal a user submitted; the values they entered are in `raw`."""

    custom_id: str


class ModalSubmitInteraction(_Interaction, kw_only=True):
    """A modal submit, which bostie decodes but does not route."""

    data: ModalSubmitData


class UnknownInteraction(Model, kw_only=True):
    """An interaction bostie does not model; see `raw`."""

    id: int
    type: InteractionType


type Interaction = (
    CommandInteraction
    | ComponentInteraction
    | AutocompleteInteraction
    | ModalSubmitInteraction
    | UnknownInteraction
)

INTERACTIONS: dict[InteractionType, type[Interaction]] = {
    InteractionType.APPLICATION_COMMAND: CommandInteraction,
    InteractionType.MESSAGE_COMPONENT: ComponentInteraction,
    InteractionType.APPLICATION_COMMAND_AUTOCOMPLETE: AutocompleteInteraction,
    InteractionType.MODAL_SUBMIT: ModalSubmitInteraction,
}


class _InteractionKind(msgspec.Struct, gc=False):
    type: InteractionType


def decode_interaction(data: bytes) -> Interaction:
    """Decode an interaction into the model of its type, or `UnknownInteraction`."""
    kind = decoder(_InteractionKind).decode(data).type
    return decode(INTERACTIONS.get(kind, UnknownInteraction), data)
