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
from bostie.models.components import SubmittedComponent, decode_submitted
from bostie.models.guild import Member, PartialMember, Role, with_user
from bostie.models.message import (
    NULL,
    Attachment,
    Message,
    PartialMessage,
    WebhookMessage,
    message_model,
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

    users: dict[int, User] = {}
    members: dict[int, PartialMember] = {}
    roles: dict[int, Role] = {}
    channels: dict[int, PartialChannel] = {}
    _messages: dict[int, msgspec.Raw] = msgspec.field(default={}, name="messages")
    attachments: dict[int, Attachment] = {}

    @property
    def messages(self) -> dict[int, PartialMessage]:
        """The messages by id, decoded on each access."""
        return {
            id: decoder(PartialMessage).decode(raw)
            for id, raw in self._messages.items()
        }

    def member(self, id: int) -> Member | None:
        """The member with this id, with its user."""
        member = self.members.get(id)
        user = self.users.get(id)
        if member is None or user is None:
            return None
        return with_user(member, user)

    def message(self, id: int) -> Message | WebhookMessage | None:
        """The whole message with this id, as a message command's target carries it."""
        raw = self._messages.get(id)
        return None if raw is None else decoder(message_model(raw)).decode(raw)


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
    _flags: int = msgspec.field(default=0, name="flags")
    _flags_new: str | None = msgspec.field(default=None, name="flags_new")

    @property
    def flags(self) -> ApplicationFlags:
        return ApplicationFlags(
            self._flags if self._flags_new is None else int(self._flags_new)
        )


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
    """A slash, user or message command run."""

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
    """The modal a user submitted and what they answered in it."""

    custom_id: str
    _components: tuple[msgspec.Raw, ...] = msgspec.field(name="components")
    resolved: Resolved = msgspec.field(default_factory=Resolved)

    @property
    def components(self) -> tuple[SubmittedComponent, ...]:
        return decode_submitted(self._components)


class ModalSubmitInteraction(_Interaction, kw_only=True):
    """A modal submit."""

    data: ModalSubmitData
    _message: msgspec.Raw = msgspec.field(default=NULL, name="message")

    @property
    def message(self) -> Message | WebhookMessage | None:
        """The message whose component opened the modal, decoded on each access."""
        if self._message == NULL:
            return None
        return nested_message(self._message, self.guild_id)


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
