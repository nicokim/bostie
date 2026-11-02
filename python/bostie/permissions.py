from datetime import UTC, datetime

from bostie.enums import ChannelType, Permissions
from bostie.models import Channel, Guild, Member

THREAD_TYPES = frozenset(
    {
        ChannelType.ANNOUNCEMENT_THREAD,
        ChannelType.PUBLIC_THREAD,
        ChannelType.PRIVATE_THREAD,
    }
)
VOICE_TYPES = frozenset({ChannelType.GUILD_VOICE, ChannelType.GUILD_STAGE_VOICE})
ALL = Permissions(sum(Permissions))
NONE = Permissions(0)
TIMED_OUT = Permissions.VIEW_CHANNEL | Permissions.READ_MESSAGE_HISTORY
NEEDS_SENDING = (
    Permissions.MENTION_EVERYONE
    | Permissions.SEND_TTS_MESSAGES
    | Permissions.ATTACH_FILES
    | Permissions.EMBED_LINKS
)
NEEDS_CONNECTING = (
    Permissions.CONNECT
    | Permissions.SPEAK
    | Permissions.MUTE_MEMBERS
    | Permissions.DEAFEN_MEMBERS
    | Permissions.MOVE_MEMBERS
    | Permissions.USE_VAD
    | Permissions.PRIORITY_SPEAKER
    | Permissions.STREAM
    | Permissions.REQUEST_TO_SPEAK
    | Permissions.USE_EMBEDDED_ACTIVITIES
    | Permissions.USE_SOUNDBOARD
    | Permissions.USE_EXTERNAL_SOUNDS
    | Permissions.MANAGE_CHANNELS
    | Permissions.MANAGE_ROLES
)


def compute_permissions(
    guild: Guild,
    member: Member,
    channel: Channel | None = None,
    *,
    parent: Channel | None = None,
) -> Permissions:
    """The member's permissions guild-wide, or in `channel`; a thread needs `parent`."""
    if member.user.id == guild.owner_id:
        return ALL
    by_id = {role.id: role for role in guild.roles}
    everyone = by_id.get(guild.id)
    permissions = NONE if everyone is None else everyone.permissions
    for role_id in member.roles:
        if (role := by_id.get(role_id)) is not None:
            permissions |= role.permissions
    if permissions & Permissions.ADMINISTRATOR:
        return ALL
    thread = channel is not None and channel.type in THREAD_TYPES
    source = parent if thread else channel
    if thread and source is None:
        raise ValueError("a thread's permissions need its parent channel")
    if source is not None:
        permissions = _overwrite(permissions, source, guild.id, member)
    until = member.communication_disabled_until
    if until is not None and until > datetime.now(UTC):
        permissions &= TIMED_OUT
    if source is None:
        return permissions
    if not permissions & Permissions.VIEW_CHANNEL:
        return NONE
    sending = (
        Permissions.SEND_MESSAGES_IN_THREADS if thread else Permissions.SEND_MESSAGES
    )
    if not permissions & sending:
        permissions &= ~NEEDS_SENDING
    if source.type in VOICE_TYPES and not permissions & Permissions.CONNECT:
        permissions &= ~NEEDS_CONNECTING
    return permissions


def _overwrite(
    permissions: Permissions, channel: Channel, everyone_id: int, member: Member
) -> Permissions:
    overwrites = {
        overwrite.id: overwrite for overwrite in channel.permission_overwrites
    }
    if (everyone := overwrites.get(everyone_id)) is not None:
        permissions = (permissions & ~everyone.deny) | everyone.allow
    allow = deny = NONE
    for role_id in member.roles:
        if (role := overwrites.get(role_id)) is not None:
            allow |= role.allow
            deny |= role.deny
    permissions = (permissions & ~deny) | allow
    if (own := overwrites.get(member.user.id)) is not None:
        permissions = (permissions & ~own.deny) | own.allow
    return permissions
