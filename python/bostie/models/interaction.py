from typing import cast

import msgspec

from bostie.enums import (
    ApplicationIntegrationType,
    ChannelType,
    CommandPermissionType,
    CommandType,
    ComponentType,
    EntryPointCommandHandlerType,
    InteractionContextType,
    InteractionType,
    Locale,
    OptionType,
    Permissions,
)
from bostie.models.base import Builder, Model, decode, decoder
from bostie.models.channel import PartialChannel
from bostie.models.components import SubmittedComponent, decode_submitted
from bostie.models.guild import InteractionGuild, Member
from bostie.models.message import (
    NULL,
    Message,
    Resolved,
    WebhookMessage,
    nested_message,
)
from bostie.models.monetization import PartialEntitlement, decode_entitlement
from bostie.models.user import User


class InteractionDataOption(Model, kw_only=True):
    """An option the user filled; subcommands and groups nest their options."""

    name: str
    type: OptionType
    value: str | int | float | bool | None = None
    options: tuple["InteractionDataOption", ...] = ()
    focused: bool = False


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


class ApplicationCommandOptionChoice(Builder, kw_only=True):
    name: str
    name_localizations: dict[str, str] | None = None
    value: str | int | float


class ApplicationCommandOption(Builder, kw_only=True):
    """An option of a slash command; subcommands and groups nest their options."""

    type: OptionType
    name: str
    name_localizations: dict[str, str] | None = None
    description: str
    description_localizations: dict[str, str] | None = None
    required: bool | None = None
    choices: tuple[ApplicationCommandOptionChoice, ...] = ()
    options: tuple["ApplicationCommandOption", ...] = ()
    channel_types: tuple[ChannelType, ...] = ()
    min_value: int | float | None = None
    max_value: int | float | None = None
    min_length: int | None = None
    max_length: int | None = None
    autocomplete: bool | None = None
    file_types: tuple[str, ...] = ()


class ApplicationCommand(Model, kw_only=True):
    """A command of the app, global or in the guild of `guild_id`."""

    id: int
    type: CommandType = CommandType.CHAT_INPUT
    application_id: int
    guild_id: int | None = None
    name: str
    name_localizations: dict[str, str] | None = None
    description: str
    description_localizations: dict[str, str] | None = None
    options: tuple[ApplicationCommandOption, ...] = ()
    default_member_permissions: Permissions | None
    nsfw: bool = False
    integration_types: tuple[ApplicationIntegrationType, ...] = ()
    contexts: tuple[InteractionContextType, ...] | None = None
    version: int
    handler: EntryPointCommandHandlerType | None = None


class CommandPermission(Model, kw_only=True):
    """Whether a role, a user or a channel may use a command."""

    id: int
    type: CommandPermissionType
    permission: bool


class GuildCommandPermissions(Model, kw_only=True):
    """A command's permissions in a guild, or every command's if `id` is the app's."""

    id: int
    application_id: int
    guild_id: int
    permissions: tuple[CommandPermission, ...]


class _Interaction(Model, kw_only=True):
    id: int
    application_id: int
    type: InteractionType
    token: str
    version: int
    _app_permissions: str = msgspec.field(name="app_permissions")
    locale: Locale
    authorizing_integration_owners: dict[ApplicationIntegrationType, int]
    attachment_size_limit: int
    _entitlements: tuple[msgspec.Raw, ...] = msgspec.field(name="entitlements")
    guild: InteractionGuild | None = None
    guild_id: int | None = None
    channel: PartialChannel | None = None
    channel_id: int | None = None
    member: Member | None = None
    _user: User | None = msgspec.field(default=None, name="user")
    guild_locale: Locale | None = None
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

    @property
    def entitlements(self) -> tuple[PartialEntitlement, ...]:
        """The user's entitlements to the app's SKUs; test ones have no period."""
        return tuple(decode_entitlement(raw) for raw in self._entitlements)


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
