import base64
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal, Self, TypedDict, Unpack, override

import msgspec

from bostie.enums import (
    ApplicationIntegrationType,
    AttachmentFlags,
    BaseTheme,
    ChannelType,
    ComponentType,
    EmbedFlags,
    EmbedMediaFlags,
    EmbedType,
    InteractionType,
    MessageActivityType,
    MessageFlags,
    MessageReferenceType,
    MessageType,
    PollLayoutType,
    StickerFormatType,
)
from bostie.file import File
from bostie.models.application import PartialApplication
from bostie.models.base import (
    Builder,
    Model,
    Snowflake,
    TypeTag,
    check_keys,
    decode,
    decoder,
)
from bostie.models.channel import Channel, PartialChannel, ThreadMember
from bostie.models.components import (
    Component,
    PartialEmoji,
    UnknownComponent,
    decode_components,
)
from bostie.models.guild import PartialMember, ResolvedMember, Role, with_user
from bostie.models.user import PartialUser, User

type MentionType = Literal["roles", "users", "everyone"]

NULL = msgspec.Raw(b"null")

NOT_IN_V2 = ("content", "embeds", "sticker_ids", "poll", "shared_client_theme")
VOICE_MESSAGE_EXTRAS = (
    "content",
    "embeds",
    "components",
    "poll",
    "sticker_ids",
    "shared_client_theme",
)

V2_COMPONENT_TYPES = frozenset(
    {
        ComponentType.SECTION,
        ComponentType.TEXT_DISPLAY,
        ComponentType.THUMBNAIL,
        ComponentType.MEDIA_GALLERY,
        ComponentType.FILE,
        ComponentType.SEPARATOR,
        ComponentType.CONTAINER,
    }
)


class Attachment(Model, Snowflake, kw_only=True):
    """A file attached to a message or passed as a command option."""

    id: int
    filename: str
    title: str | None = None
    description: str | None = None
    content_type: str | None = None
    size: int
    url: str
    proxy_url: str
    height: int | None = None
    width: int | None = None
    placeholder: str | None = None
    placeholder_version: int | None = None
    ephemeral: bool = False
    duration_secs: float | None = None
    waveform: bytes | None = None
    flags: AttachmentFlags = AttachmentFlags(0)
    clip_participants: tuple[User, ...] = ()
    clip_created_at: datetime | None = None
    application: PartialApplication | None = None

    @classmethod
    def from_resolved(cls, resolved: "Resolved", id: int) -> "Attachment | None":
        return resolved.attachments.get(id)


class Resolved(Model, kw_only=True):
    """The users, members, roles, channels, messages and files mentioned by id."""

    users: dict[int, User] = {}
    members: dict[int, PartialMember] = {}
    roles: dict[int, Role] = {}
    channels: dict[int, PartialChannel] = {}
    _messages: dict[int, msgspec.Raw] = msgspec.field(default={}, name="messages")
    attachments: dict[int, Attachment] = {}

    @property
    def messages(self) -> "dict[int, PartialMessage]":
        """The messages by id, decoded on each access."""
        return {
            id: decoder(PartialMessage).decode(raw)
            for id, raw in self._messages.items()
        }

    def member(self, id: int) -> ResolvedMember | None:
        """The member with this id, with its user."""
        member = self.members.get(id)
        user = self.users.get(id)
        if member is None or user is None:
            return None
        return with_user(member, user)

    def message(self, id: int) -> "Message | WebhookMessage | None":
        """The whole message with this id, as a message command's target carries it."""
        raw = self._messages.get(id)
        return None if raw is None else decoder(message_model(raw)).decode(raw)


class EmbedField(Builder, kw_only=True):
    name: str
    value: str
    inline: bool | None = None


class EmbedFooter(Builder, kw_only=True):
    text: str
    icon_url: str | None = None
    proxy_icon_url: str | None = None


class EmbedAuthor(Builder, kw_only=True):
    name: str
    url: str | None = None
    icon_url: str | None = None
    proxy_icon_url: str | None = None


class EmbedMedia(Builder, kw_only=True):
    """An embed image, thumbnail or video."""

    url: str | None = None
    proxy_url: str | None = None
    height: int | None = None
    width: int | None = None
    content_type: str | None = None
    placeholder: str | None = None
    placeholder_version: int | None = None
    description: str | None = None
    flags: EmbedMediaFlags | None = None


class EmbedProvider(Builder, kw_only=True):
    name: str | None = None
    url: str | None = None


class Embed(Builder, kw_only=True):
    """A rich embed, as received in messages or built to send one."""

    title: str | None = None
    type: EmbedType | None = None
    description: str | None = None
    url: str | None = None
    timestamp: datetime | None = None
    color: int | None = None
    footer: EmbedFooter | None = None
    image: EmbedMedia | None = None
    thumbnail: EmbedMedia | None = None
    video: EmbedMedia | None = None
    provider: EmbedProvider | None = None
    author: EmbedAuthor | None = None
    fields: tuple[EmbedField, ...] = ()
    _components: tuple[msgspec.Raw, ...] = msgspec.field(default=(), name="components")
    flags: EmbedFlags | None = None

    @property
    def components(self) -> tuple[Component, ...]:
        """The components of a link preview built from components."""
        return decode_components(self._components)


class ReactionCountDetails(Model, kw_only=True):
    """The reactions of one emoji, split into super reactions and normal ones."""

    burst: int
    normal: int


class Reaction(Model, kw_only=True):
    """An emoji's reactions on a message; `count` includes super reactions."""

    count: int
    count_details: ReactionCountDetails
    me: bool
    me_burst: bool
    emoji: PartialEmoji
    burst_colors: tuple[str, ...]


class MessageReference(Builder, kw_only=True):
    type: MessageReferenceType | None = None
    message_id: int | None = None
    channel_id: int | None = None
    guild_id: int | None = None
    fail_if_not_exists: bool | None = None


class StickerItem(Model, Snowflake, kw_only=True):
    id: int
    name: str
    format_type: StickerFormatType


class PollMedia(Builder, kw_only=True):
    """The text of a poll question or answer, and the emoji of an answer."""

    text: str | None = None
    emoji: PartialEmoji | None = None


class PollAnswer(Builder, kw_only=True):
    """A poll answer; Discord numbers `answer_id` itself."""

    answer_id: int | None = None
    poll_media: PollMedia


class PollAnswerCount(Model, kw_only=True):
    """The votes of the answer whose `answer_id` is `id`."""

    id: int
    count: int
    me_voted: bool


class PollResults(Model, kw_only=True):
    """The votes per answer; an answer missing from `answer_counts` has none."""

    is_finalized: bool
    answer_counts: tuple[PollAnswerCount, ...]


class Poll(Model, kw_only=True):
    """A poll on a message; `results` is None when Discord did not count them."""

    question: PollMedia
    answers: tuple[PollAnswer, ...]
    expiry: datetime | None
    allow_multiselect: bool
    layout_type: PollLayoutType
    results: PollResults | None = None


class PollCreateRequest(Builder, kw_only=True):
    """A poll to send; it stays open `duration` hours, 24 when not given."""

    question: PollMedia
    answers: tuple[PollAnswer, ...]
    duration: int | None = None
    allow_multiselect: bool | None = None
    layout_type: PollLayoutType | None = None


class SharedClientTheme(Builder, kw_only=True):
    """A client theme shared in a message; `colors` are hex strings."""

    colors: tuple[str, ...]
    gradient_angle: int
    base_mix: int
    base_theme: BaseTheme | None = None


class AllowedMentions(Builder, kw_only=True):
    """Which mentions in a message may notify; `parse=()` silences all of them.

    Sent with a bot that has a default, it changes only the kinds of mention it sets.
    """

    parse: tuple[MentionType, ...] | None = None
    roles: tuple[int, ...] | None = None
    users: tuple[int, ...] | None = None
    replied_user: bool | None = None


DEFAULT_ALLOWED_MENTIONS = AllowedMentions(parse=("users", "roles"))


class MessageActivity(Model, kw_only=True):
    """The Rich Presence invite a message carries."""

    type: MessageActivityType
    party_id: str | None = None


class ChannelMention(Model, Snowflake, kw_only=True):
    """A channel mentioned in a crossposted message."""

    id: int
    guild_id: int
    type: ChannelType
    name: str


class RoleSubscriptionData(Model, kw_only=True):
    """The role subscription purchase or renewal a message announces."""

    role_subscription_listing_id: int
    tier_name: str
    total_months_subscribed: int
    is_renewal: bool


class MessageCall(Model, kw_only=True):
    """The call in a private channel a message announces."""

    participants: tuple[int, ...]
    ended_timestamp: datetime | None = None


class InteractionMetadata(Model, Snowflake, kw_only=True):
    """The interaction a message answers; its type picks the subclass."""

    id: int
    type: InteractionType
    user: User
    authorizing_integration_owners: dict[ApplicationIntegrationType, int]
    original_response_message_id: int | None = None


class CommandInteractionMetadata(InteractionMetadata, kw_only=True):
    """A command run; the target is set for user and message commands."""

    target_user: User | None = None
    target_message_id: int | None = None


class ComponentInteractionMetadata(InteractionMetadata, kw_only=True):
    interacted_message_id: int


class ModalInteractionMetadata(InteractionMetadata, kw_only=True):
    _triggering_interaction_metadata: msgspec.Raw = msgspec.field(
        name="triggering_interaction_metadata"
    )

    @property
    def triggering_interaction_metadata(self) -> InteractionMetadata:
        """The command or component that opened the modal, decoded on each access."""
        return decode_interaction_metadata(self._triggering_interaction_metadata)


INTERACTION_METADATA: dict[int, type[InteractionMetadata]] = {
    InteractionType.APPLICATION_COMMAND: CommandInteractionMetadata,
    InteractionType.MESSAGE_COMPONENT: ComponentInteractionMetadata,
    InteractionType.MODAL_SUBMIT: ModalInteractionMetadata,
}


def decode_interaction_metadata(data: bytes) -> InteractionMetadata:
    kind = decoder(TypeTag).decode(data).type
    model = INTERACTION_METADATA.get(kind or 0, InteractionMetadata)
    metadata: InteractionMetadata = decoder(model).decode(data)
    return metadata


class MentionedUser(User, kw_only=True):
    """A user a message mentions, with their member when it was sent in a guild."""

    member: PartialMember | None = None


class SnapshotMessage(Model, kw_only=True):
    """The fields of a forwarded message that its snapshot keeps."""

    type: MessageType
    content: str
    embeds: tuple[Embed, ...]
    attachments: tuple[Attachment, ...]
    timestamp: datetime
    edited_timestamp: datetime | None
    flags: MessageFlags = MessageFlags(0)
    mentions: tuple[MentionedUser, ...]
    mention_roles: tuple[int, ...]
    sticker_items: tuple[StickerItem, ...] = ()
    _components: tuple[msgspec.Raw, ...] = msgspec.field(default=(), name="components")

    @property
    def components(self) -> tuple[Component, ...]:
        return decode_components(self._components)


class MessageSnapshot(Model, kw_only=True):
    """A forwarded message, as it was when it was forwarded."""

    message: SnapshotMessage


class PartialMessage(Model, Snowflake, kw_only=True):
    """A message as an interaction resolves it, with only some of its fields."""

    id: int
    channel_id: int
    guild_id: int | None = None
    member: PartialMember | None = None
    channel_type: ChannelType | None = None
    reactions: tuple[Reaction, ...] = ()
    nonce: int | str | None = None
    webhook_id: int | None = None
    flags: MessageFlags = MessageFlags(0)
    message_reference: MessageReference | None = None
    _referenced_message: msgspec.Raw = msgspec.field(
        default=NULL, name="referenced_message"
    )
    thread: Channel | None = None
    _components: tuple[msgspec.Raw, ...] = msgspec.field(default=(), name="components")
    sticker_items: tuple[StickerItem, ...] = ()
    position: int | None = None

    @property
    def components(self) -> tuple[Component, ...]:
        return decode_components(self._components)

    @property
    def referenced_message(self) -> "Message | WebhookMessage | None":
        """The message this one replies to, decoded on each access."""
        if self._referenced_message == NULL:
            return None
        return nested_message(self._referenced_message, self.guild_id)

    @property
    def jump_url(self) -> str:
        guild = self.guild_id or "@me"
        return f"https://discord.com/channels/{guild}/{self.channel_id}/{self.id}"

    @classmethod
    def from_resolved(cls, resolved: "Resolved", id: int) -> "PartialMessage | None":
        return resolved.messages.get(id)


class _Message(PartialMessage, kw_only=True):
    content: str
    timestamp: datetime
    edited_timestamp: datetime | None
    tts: bool
    mention_everyone: bool
    mentions: tuple[MentionedUser, ...]
    mention_roles: tuple[int, ...]
    attachments: tuple[Attachment, ...]
    embeds: tuple[Embed, ...]
    pinned: bool
    type: MessageType
    poll: Poll | None = None
    shared_client_theme: SharedClientTheme | None = None
    mention_channels: tuple[ChannelMention, ...] = ()
    activity: MessageActivity | None = None
    application: PartialApplication | None = None
    application_id: int | None = None
    message_snapshots: tuple[MessageSnapshot, ...] = ()
    _interaction_metadata: msgspec.Raw = msgspec.field(
        default=NULL, name="interaction_metadata"
    )
    role_subscription_data: RoleSubscriptionData | None = None
    resolved: Resolved | None = None
    call: MessageCall | None = None

    @property
    def interaction_metadata(self) -> InteractionMetadata | None:
        """The interaction this message answers, decoded on each access."""
        if self._interaction_metadata == NULL:
            return None
        return decode_interaction_metadata(self._interaction_metadata)

    @classmethod
    @override
    def from_resolved(cls, resolved: "Resolved", id: int) -> Self | None:
        message = resolved.message(id)
        return message if isinstance(message, cls) else None


class Message(_Message, kw_only=True):
    """A message in a channel, sent by a user or a bot."""

    author: User

    @property
    def author_member(self) -> ResolvedMember | None:
        """The author as a guild member, when the message carries its member."""
        return None if self.member is None else with_user(self.member, self.author)


class WebhookMessage(_Message, kw_only=True):
    """A message a webhook sent, interaction responses included."""

    author: PartialUser


class _MessageKind(msgspec.Struct, gc=False):
    webhook_id: int | None = None


def message_model(data: bytes) -> type[Message] | type[WebhookMessage]:
    if b'"webhook_id"' not in bytes(data):
        return Message
    kind = decoder(_MessageKind).decode(data)
    return Message if kind.webhook_id is None else WebhookMessage


def decode_message(data: bytes) -> Message | WebhookMessage:
    """Decode a message into `Message`, or `WebhookMessage` when a webhook sent it."""
    return decode(message_model(data), data)


def nested_message(data: bytes, guild_id: int | None) -> Message | WebhookMessage:
    message: Message | WebhookMessage = decoder(message_model(data)).decode(data)
    return in_guild(message, guild_id)


def in_guild[M: PartialMessage](message: M, guild_id: int | None) -> M:
    if message.guild_id is None:
        msgspec.structs.force_setattr(message, "guild_id", guild_id)
    return message


class MessagePin(Model, kw_only=True):
    """A pinned message and when it was pinned."""

    pinned_at: datetime
    message: Message | WebhookMessage


class MessageSearchResults(Model, kw_only=True):
    """One page of a guild message search, with the threads its messages are in."""

    doing_deep_historical_index: bool
    documents_indexed: int | None = None
    total_results: int
    messages: tuple[Message | WebhookMessage, ...]
    threads: tuple[Channel, ...] = ()
    members: tuple[ThreadMember, ...] = ()


class MessageParts(TypedDict, total=False):
    """The parts of a message to send; a part not given is left out."""

    embeds: Sequence[Embed | Mapping[str, Any]]
    components: Sequence[Component | Mapping[str, Any]]
    files: Sequence[File]
    allowed_mentions: AllowedMentions
    flags: MessageFlags
    tts: bool
    poll: PollCreateRequest


class EditParts(TypedDict, total=False):
    """The parts of a message to edit: a part not given stays and None clears it.

    With `files` or `attachments`, only the listed `attachments` stay.
    """

    content: str | None
    embeds: Sequence[Embed | Mapping[str, Any]] | None
    components: Sequence[Component | Mapping[str, Any]] | None
    files: Sequence[File]
    allowed_mentions: AllowedMentions | None
    flags: MessageFlags | None
    attachments: Sequence[Attachment | int] | None


MESSAGE_PARTS = MessageParts.__optional_keys__
EDIT_PARTS = EditParts.__optional_keys__


def build_message(
    content: str | None = None,
    *,
    reply_to: int | None = None,
    **parts: Unpack[MessageParts],
) -> tuple[dict[str, Any], Sequence[File]]:
    """Build the JSON body of a message to send; only given parts are set.

    V2 components set IS_COMPONENTS_V2 and clear content and embeds.
    """
    check_keys(parts, MESSAGE_PARTS, "message parts")
    return message_body(content, parts, reply_to=reply_to)


def message_body(
    content: str | None,
    parts: Mapping[str, Any],
    *,
    reply_to: int | None = None,
    mentions: AllowedMentions | None = None,
) -> tuple[dict[str, Any], Sequence[File]]:
    body: dict[str, Any] = {} if content is None else {"content": content}
    body |= parts
    given = body.get("allowed_mentions", AllowedMentions())
    if mentions is not None and given is not None:
        body["allowed_mentions"] = _merged_mentions(mentions, given)
    files: Sequence[File] = body.pop("files", ())
    components = body.get("components")
    if components is not None:
        body["components"] = [
            component.raw if isinstance(component, UnknownComponent) else component
            for component in components
        ]
        if any(_type_of(component) in V2_COMPONENT_TYPES for component in components):
            body["flags"] = MessageFlags.IS_COMPONENTS_V2 | (body.get("flags") or 0)
    components_v2 = bool((body.get("flags") or 0) & MessageFlags.IS_COMPONENTS_V2)
    if components_v2 and any(body.get(part) for part in NOT_IN_V2):
        raise ValueError(
            "messages with Components V2 cannot have content, embeds, stickers, "
            "a poll or a shared client theme; use ui.text() and ui.container() instead"
        )
    if any(file.waveform is not None for file in files):
        _check_voice_message(body, files)
        body["flags"] = MessageFlags.IS_VOICE_MESSAGE | (body.get("flags") or 0)
    if reply_to is not None:
        if "message_reference" in body:
            raise ValueError("reply_to and message_reference are mutually exclusive")
        body["message_reference"] = MessageReference(
            type=MessageReferenceType.DEFAULT, message_id=reply_to
        )
    if files or "attachments" in body:
        kept: Sequence[Attachment | int] | None = body.get("attachments")
        body["attachments"] = (
            None
            if kept is None and not files
            else [_kept(attachment) for attachment in kept or ()]
            + [_attachment(index, file) for index, file in enumerate(files)]
        )
    if components_v2:
        body |= {"content": None, "embeds": []}
    return body, files


def _merged_mentions(
    default: AllowedMentions, given: AllowedMentions
) -> AllowedMentions:
    """`given`, with each kind of mention it says nothing about as `default` has it."""
    users = given if given.parse is not None or given.users is not None else default
    roles = given if given.parse is not None or given.roles is not None else default
    everyone = given if given.parse is not None else default
    sources: tuple[tuple[MentionType, AllowedMentions], ...] = (
        ("everyone", everyone),
        ("users", users),
        ("roles", roles),
    )
    parse: tuple[MentionType, ...] = tuple(
        kind for kind, source in sources if kind in (source.parse or ())
    )
    replied = default.replied_user if given.replied_user is None else given.replied_user
    return AllowedMentions(
        parse=parse, roles=roles.roles, users=users.users, replied_user=replied
    )


def _type_of(component: Component | Mapping[str, Any]) -> int | None:
    if isinstance(component, Mapping):
        tag: int | None = component.get("type")
        return tag
    return component.type


def _kept(attachment: Attachment | int) -> dict[str, Any]:
    if isinstance(attachment, int):
        return {"id": attachment}
    kept: dict[str, Any] = {
        "id": attachment.id,
        "is_spoiler": AttachmentFlags.IS_SPOILER in attachment.flags,
    }
    if attachment.description is not None:
        kept["description"] = attachment.description
    return kept


def _attachment(index: int, file: File) -> dict[str, Any]:
    attachment: dict[str, Any] = {"id": index, "filename": file.name}
    if file.title is not None:
        attachment["title"] = file.title
    if file.description is not None:
        attachment["description"] = file.description
    if file.spoiler:
        attachment["is_spoiler"] = True
    if file.duration_secs is not None:
        attachment["duration_secs"] = file.duration_secs
    if file.waveform is not None:
        attachment["waveform"] = base64.b64encode(file.waveform).decode()
    return attachment


def _check_voice_message(body: Mapping[str, Any], files: Sequence[File]) -> None:
    extras = [part for part in VOICE_MESSAGE_EXTRAS if body.get(part)]
    if len(files) != 1 or extras or files[0].duration_secs is None:
        raise ValueError(
            "a voice message is one audio file with duration_secs and a waveform, "
            "and nothing else"
        )
