from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

import msgspec

from lyrecord.enums import (
    ComponentType,
    MessageFlags,
    MessageReferenceType,
    MessageType,
)
from lyrecord.file import File
from lyrecord.models.base import Builder, Model
from lyrecord.models.channel import Channel
from lyrecord.models.components import (
    Component,
    PartialEmoji,
    UnknownComponent,
    decode_components,
)
from lyrecord.models.guild import Member
from lyrecord.models.user import User

if TYPE_CHECKING:
    from lyrecord.models.interaction import Resolved

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

    __option_type__: ClassVar[int] = 11

    id: int
    filename: str | None = None
    title: str | None = None
    description: str | None = None
    content_type: str | None = None
    size: int | None = None
    url: str | None = None
    proxy_url: str | None = None
    height: int | None = None
    width: int | None = None
    ephemeral: bool | None = None
    duration_secs: float | None = None
    flags: int | None = None

    @classmethod
    def from_resolved(cls, resolved: "Resolved", id: int) -> "Attachment | None":
        return resolved.attachments.get(id) if resolved.attachments else None


class EmbedField(Builder, kw_only=True):
    name: str | None = None
    value: str | None = None
    inline: bool | None = None


class EmbedFooter(Builder, kw_only=True):
    text: str | None = None
    icon_url: str | None = None
    proxy_icon_url: str | None = None


class EmbedAuthor(Builder, kw_only=True):
    name: str | None = None
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
    count: int | None = None
    me: bool | None = None
    emoji: PartialEmoji | None = None


class MessageReference(Builder, kw_only=True):
    type: MessageReferenceType | None = None
    message_id: int | None = None
    channel_id: int | None = None
    guild_id: int | None = None
    fail_if_not_exists: bool | None = None


class StickerItem(Model, kw_only=True):
    id: int
    name: str | None = None
    format_type: int | None = None


class AllowedMentions(Builder, kw_only=True):
    """Which mentions in a message may notify; `parse=()` silences all of them."""

    parse: tuple[str, ...] | None = None
    roles: tuple[int, ...] | None = None
    users: tuple[int, ...] | None = None
    replied_user: bool | None = None


class Message(Model, kw_only=True):
    """A message in a channel."""

    id: int
    channel_id: int | None = None
    guild_id: int | None = None
    author: User | None = None
    member: Member | None = None
    content: str | None = None
    timestamp: datetime | None = None
    edited_timestamp: datetime | None = None
    tts: bool | None = None
    mention_everyone: bool | None = None
    mentions: tuple[User, ...] = ()
    mention_roles: tuple[int, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    embeds: tuple[Embed, ...] = ()
    reactions: tuple[Reaction, ...] = ()
    nonce: int | str | None = None
    pinned: bool | None = None
    webhook_id: int | None = None
    type: MessageType | None = None
    flags: MessageFlags | None = None
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
    def from_resolved(cls, resolved: "Resolved", id: int) -> "Message | None":
        return resolved.messages.get(id) if resolved.messages else None


def build_message(
    content: str | None = None,
    *,
    embeds: Sequence[Embed | Mapping[str, Any]] | None = None,
    components: Sequence[Component | Mapping[str, Any]] | None = None,
    files: Sequence[File] = (),
    attachments: Sequence[Attachment | int] = (),
    allowed_mentions: AllowedMentions | Mapping[str, Any] | None = None,
    flags: int | None = None,
    tts: bool | None = None,
    reply_to: int | None = None,
) -> tuple[dict[str, Any], Sequence[File]]:
    """Build the JSON body of a message to send or edit; only given parts are set.

    V2 components set IS_COMPONENTS_V2 and clear content and embeds, and with `files`
    or `attachments` an edit keeps only the listed `attachments`.
    """
    components_json = None
    if components is not None:
        components_json = [
            component.raw if isinstance(component, UnknownComponent) else component
            for component in components
        ]
        if any(_type_of(component) in V2_COMPONENT_TYPES for component in components):
            flags = (flags or 0) | MessageFlags.IS_COMPONENTS_V2
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
        for attachment in attachments
    ] + [_attachment(index, file) for index, file in enumerate(files)]
    parts: dict[str, Any] = {
        "content": content,
        "embeds": embeds,
        "components": components_json,
        "allowed_mentions": allowed_mentions,
        "flags": flags,
        "tts": tts,
        "message_reference": reference,
        "attachments": attachment_list or None,
    }
    body = {key: value for key, value in parts.items() if value is not None}
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
