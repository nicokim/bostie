from datetime import UTC, datetime

from bostie.enums import ChannelType, Permissions
from bostie.models import Channel, Guild, Member, ResolvedMember, Role, RoleColors

type AnyMember = Member | ResolvedMember

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
TEXT = (
    Permissions.CREATE_INSTANT_INVITE
    | Permissions.MANAGE_CHANNELS
    | Permissions.ADD_REACTIONS
    | Permissions.VIEW_CHANNEL
    | Permissions.SEND_MESSAGES
    | Permissions.SEND_TTS_MESSAGES
    | Permissions.MANAGE_MESSAGES
    | Permissions.EMBED_LINKS
    | Permissions.ATTACH_FILES
    | Permissions.READ_MESSAGE_HISTORY
    | Permissions.MENTION_EVERYONE
    | Permissions.USE_EXTERNAL_EMOJIS
    | Permissions.MANAGE_ROLES
    | Permissions.MANAGE_WEBHOOKS
    | Permissions.USE_APPLICATION_COMMANDS
    | Permissions.MANAGE_THREADS
    | Permissions.CREATE_PUBLIC_THREADS
    | Permissions.CREATE_PRIVATE_THREADS
    | Permissions.USE_EXTERNAL_STICKERS
    | Permissions.SEND_MESSAGES_IN_THREADS
    | Permissions.USE_EMBEDDED_ACTIVITIES
    | Permissions.SEND_VOICE_MESSAGES
    | Permissions.SEND_POLLS
    | Permissions.USE_EXTERNAL_APPS
    | Permissions.PIN_MESSAGES
    | Permissions.BYPASS_SLOWMODE
)
VOICE = (
    Permissions.CREATE_INSTANT_INVITE
    | Permissions.MANAGE_CHANNELS
    | Permissions.ADD_REACTIONS
    | Permissions.PRIORITY_SPEAKER
    | Permissions.STREAM
    | Permissions.VIEW_CHANNEL
    | Permissions.SEND_MESSAGES
    | Permissions.SEND_TTS_MESSAGES
    | Permissions.MANAGE_MESSAGES
    | Permissions.EMBED_LINKS
    | Permissions.ATTACH_FILES
    | Permissions.READ_MESSAGE_HISTORY
    | Permissions.MENTION_EVERYONE
    | Permissions.USE_EXTERNAL_EMOJIS
    | Permissions.CONNECT
    | Permissions.SPEAK
    | Permissions.MUTE_MEMBERS
    | Permissions.DEAFEN_MEMBERS
    | Permissions.MOVE_MEMBERS
    | Permissions.USE_VAD
    | Permissions.MANAGE_ROLES
    | Permissions.MANAGE_WEBHOOKS
    | Permissions.USE_APPLICATION_COMMANDS
    | Permissions.MANAGE_EVENTS
    | Permissions.USE_EXTERNAL_STICKERS
    | Permissions.USE_EMBEDDED_ACTIVITIES
    | Permissions.USE_SOUNDBOARD
    | Permissions.CREATE_EVENTS
    | Permissions.USE_EXTERNAL_SOUNDS
    | Permissions.SEND_VOICE_MESSAGES
    | Permissions.SET_VOICE_CHANNEL_STATUS
    | Permissions.SEND_POLLS
    | Permissions.USE_EXTERNAL_APPS
    | Permissions.BYPASS_SLOWMODE
)
STAGE = (
    Permissions.CREATE_INSTANT_INVITE
    | Permissions.MANAGE_CHANNELS
    | Permissions.ADD_REACTIONS
    | Permissions.STREAM
    | Permissions.VIEW_CHANNEL
    | Permissions.SEND_MESSAGES
    | Permissions.SEND_TTS_MESSAGES
    | Permissions.MANAGE_MESSAGES
    | Permissions.EMBED_LINKS
    | Permissions.ATTACH_FILES
    | Permissions.READ_MESSAGE_HISTORY
    | Permissions.MENTION_EVERYONE
    | Permissions.USE_EXTERNAL_EMOJIS
    | Permissions.CONNECT
    | Permissions.MUTE_MEMBERS
    | Permissions.MOVE_MEMBERS
    | Permissions.MANAGE_ROLES
    | Permissions.MANAGE_WEBHOOKS
    | Permissions.USE_APPLICATION_COMMANDS
    | Permissions.REQUEST_TO_SPEAK
    | Permissions.MANAGE_EVENTS
    | Permissions.USE_EXTERNAL_STICKERS
    | Permissions.CREATE_EVENTS
    | Permissions.SEND_VOICE_MESSAGES
    | Permissions.SEND_POLLS
    | Permissions.USE_EXTERNAL_APPS
    | Permissions.BYPASS_SLOWMODE
)
ELEVATED = (
    Permissions.KICK_MEMBERS
    | Permissions.BAN_MEMBERS
    | Permissions.ADMINISTRATOR
    | Permissions.MANAGE_CHANNELS
    | Permissions.MANAGE_GUILD
    | Permissions.MANAGE_MESSAGES
    | Permissions.MANAGE_ROLES
    | Permissions.MANAGE_WEBHOOKS
    | Permissions.MANAGE_GUILD_EXPRESSIONS
    | Permissions.MANAGE_THREADS
    | Permissions.VIEW_CREATOR_MONETIZATION_ANALYTICS
)
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
    member: AnyMember,
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


def top_role(guild: Guild, member: AnyMember) -> Role | None:
    """The member's highest role, or None when it has only @everyone."""
    held = [role for role in guild.roles if role.id in member.roles]
    return max(held, key=_rank, default=None)


def display_colors(guild: Guild, member: AnyMember) -> RoleColors | None:
    """The colors of the member's highest colored role, which its name shows."""
    colored = [
        role
        for role in guild.roles
        if role.id in member.roles and role.colors.primary_color
    ]
    return max(colored, key=_rank).colors if colored else None


def can_act_on(guild: Guild, actor: AnyMember, target: AnyMember) -> bool:
    """Whether `actor` outranks `target`: the owner outranks all, nobody the owner."""
    if target.id == guild.owner_id:
        return False
    if actor.id == guild.owner_id:
        return True
    return _rank(top_role(guild, actor)) > _rank(top_role(guild, target))


def _rank(role: Role | None) -> tuple[int, int]:
    return (-1, 0) if role is None else (role.position, -role.id)


def _overwrite(
    permissions: Permissions, channel: Channel, everyone_id: int, member: AnyMember
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
