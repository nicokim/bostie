from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, TypedDict, Unpack

import msgspec

from bostie.enums import (
    AttachmentFlags,
    ComponentType,
    MessageFlags,
    MessageReferenceType,
    MessageType,
    StickerFormatType,
)
from bostie.file import File
from bostie.models.base import Builder, Model
from bostie.models.channel import Channel
from bostie.models.components import (
    Component,
    PartialEmoji,
    UnknownComponent,
    decode_components,
)
from bostie.models.guild import PartialMember
from bostie.models.user import User

if TYPE_CHECKING:
    from bostie.models.interaction import Resolved

type MentionType = Literal["roles", "users", "everyone"]

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


class Attachment(Model, kw_only=True):
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
    ephemeral: bool = False
    duration_secs: float | None = None
    flags: AttachmentFlags = AttachmentFlags(0)

    @classmethod
    def from_resolved(cls, resolved: "Resolved", id: int) -> "Attachment | None":
        return resolved.attachments.get(id)


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


class EmbedProvider(Builder, kw_only=True):
    name: str | None = None
    url: str | None = None


class Embed(Builder, kw_only=True):
    """A rich embed, as received in messages or built to send one."""

    title: str | None = None
    type: str | None = None
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


class Reaction(Model, kw_only=True):
    count: int
    me: bool
    emoji: PartialEmoji


class MessageReference(Builder, kw_only=True):
    type: MessageReferenceType | None = None
    message_id: int | None = None
    channel_id: int | None = None
    guild_id: int | None = None
    fail_if_not_exists: bool | None = None


class StickerItem(Model, kw_only=True):
    id: int
    name: str
    format_type: StickerFormatType


class AllowedMentions(Builder, kw_only=True):
    """Which mentions in a message may notify; `parse=()` silences all of them."""

    parse: tuple[MentionType, ...] | None = None
    roles: tuple[int, ...] | None = None
    users: tuple[int, ...] | None = None
    replied_user: bool | None = None


class PartialMessage(Model, kw_only=True):
    """A message as an interaction resolves it, with only some of its fields."""

    id: int
    channel_id: int
    guild_id: int | None = None
    member: PartialMember | None = None
    reactions: tuple[Reaction, ...] = ()
    nonce: int | str | None = None
    webhook_id: int | None = None
    flags: MessageFlags = MessageFlags(0)
    message_reference: MessageReference | None = None
    referenced_message: "Message | None" = None
    thread: Channel | None = None
    components_json: tuple[msgspec.Raw, ...] = msgspec.field(
        default=(), name="components"
    )
    sticker_items: tuple[StickerItem, ...] = ()
    position: int | None = None

    @property
    def components(self) -> tuple[Component, ...]:
        return decode_components(self.components_json)

    @property
    def jump_url(self) -> str:
        guild = self.guild_id or "@me"
        return f"https://discord.com/channels/{guild}/{self.channel_id}/{self.id}"

    @classmethod
    def from_resolved(cls, resolved: "Resolved", id: int) -> "PartialMessage | None":
        return resolved.messages.get(id)


class Message(PartialMessage, kw_only=True):
    """A message in a channel."""

    author: User
    content: str
    timestamp: datetime
    edited_timestamp: datetime | None
    tts: bool
    mention_everyone: bool
    mentions: tuple[User, ...]
    mention_roles: tuple[int, ...]
    attachments: tuple[Attachment, ...]
    embeds: tuple[Embed, ...]
    pinned: bool
    type: MessageType


def in_guild[M: PartialMessage](message: M, guild_id: int | None) -> M:
    if message.guild_id is None:
        msgspec.structs.force_setattr(message, "guild_id", guild_id)
    if message.referenced_message is not None:
        in_guild(message.referenced_message, message.guild_id)
    return message


class MessageParts(TypedDict, total=False):
    """The parts of a message to send; a part not given is left out."""

    embeds: Sequence[Embed | Mapping[str, Any]]
    components: Sequence[Component | Mapping[str, Any]]
    files: Sequence[File]
    allowed_mentions: AllowedMentions
    flags: MessageFlags
    tts: bool


class EditParts(MessageParts, total=False):
    """The parts of a message to edit; with files or attachments, only these stay."""

    attachments: Sequence[Attachment | int]


EDIT_PARTS = EditParts.__optional_keys__


def build_message(
    content: str | None = None,
    *,
    reply_to: int | None = None,
    **parts: Unpack[EditParts],
) -> tuple[dict[str, Any], Sequence[File]]:
    """Build the JSON body of a message to send or edit; only given parts are set.

    V2 components set IS_COMPONENTS_V2 and clear content and embeds, and with `files`
    or `attachments` an edit keeps only the listed `attachments`.
    """
    unknown = parts.keys() - EDIT_PARTS
    if unknown:
        raise TypeError(f"unexpected message parts: {', '.join(sorted(unknown))}")
    embeds = parts.get("embeds")
    components = parts.get("components")
    files = parts.get("files", ())
    flags = parts.get("flags")
    components_json = None
    if components is not None:
        components_json = [
            component.raw if isinstance(component, UnknownComponent) else component
            for component in components
        ]
        if any(_type_of(component) in V2_COMPONENT_TYPES for component in components):
            flags = MessageFlags.IS_COMPONENTS_V2 | (flags or 0)
    components_v2 = bool((flags or 0) & MessageFlags.IS_COMPONENTS_V2)
    if components_v2 and (content or embeds):
        raise ValueError(
            "messages with Components V2 cannot have content or embeds; "
            "use ui.text() and ui.container() instead"
        )
    reference = (
        None
        if reply_to is None
        else MessageReference(type=MessageReferenceType.DEFAULT, message_id=reply_to)
    )
    attachment_list = [
        {"id": attachment if isinstance(attachment, int) else attachment.id}
        for attachment in parts.get("attachments", ())
    ] + [_attachment(index, file) for index, file in enumerate(files)]
    fields: dict[str, Any] = {
        "content": content,
        "embeds": embeds,
        "components": components_json,
        "allowed_mentions": parts.get("allowed_mentions"),
        "flags": flags,
        "tts": parts.get("tts"),
        "message_reference": reference,
        "attachments": attachment_list or None,
    }
    body = {key: value for key, value in fields.items() if value is not None}
    if components_v2:
        body |= {"content": None, "embeds": []}
    return body, files


def _type_of(component: Component | Mapping[str, Any]) -> int | None:
    if isinstance(component, Mapping):
        tag: int | None = component.get("type")
        return tag
    return component.type


def _attachment(index: int, file: File) -> dict[str, Any]:
    attachment: dict[str, Any] = {"id": index, "filename": file.name}
    if file.description is not None:
        attachment["description"] = file.description
    if file.spoiler:
        attachment["is_spoiler"] = True
    return attachment
