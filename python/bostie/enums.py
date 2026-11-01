from enum import KEEP, IntEnum, IntFlag, StrEnum
from typing import Self, override

FLAG_WIDTH = 128
MSGSPEC_ENUM_MAX = (1 << 63) - 1


class DiscordIntEnum(IntEnum):
    """An IntEnum that maps unknown values to stable `UNKNOWN_<value>` members."""

    @classmethod
    @override
    def _missing_(cls, value: object) -> Self | None:
        if not isinstance(value, int):
            return None
        unknown = int.__new__(cls, value)
        unknown._name_ = f"UNKNOWN_{value}"
        unknown._value_ = value
        cls._value2member_map_.setdefault(value, unknown)
        return cls(value)


class DiscordIntFlag(IntFlag, boundary=KEEP):
    """An IntFlag whose operators, `~` included, keep bits bostie does not know."""

    @classmethod
    @override
    def _missing_(cls, value: object) -> Self:
        if isinstance(value, int) and value < 0:
            return cls(value & ((1 << FLAG_WIDTH) - 1))
        flag: Self = super()._missing_(value)
        if flag > MSGSPEC_ENUM_MAX:
            cls._value2member_map_.pop(value, None)
        return flag


class DiscordStrEnum(StrEnum):
    """A StrEnum that maps unknown values to stable `UNKNOWN_<value>` members."""

    @classmethod
    @override
    def _missing_(cls, value: object) -> Self | None:
        if not isinstance(value, str):
            return None
        unknown = str.__new__(cls, value)
        unknown._name_ = f"UNKNOWN_{value}"
        unknown._value_ = value
        cls._value2member_map_.setdefault(value, unknown)
        return cls(value)


class ChannelType(DiscordIntEnum):
    GUILD_TEXT = 0
    DM = 1
    GUILD_VOICE = 2
    GROUP_DM = 3
    GUILD_CATEGORY = 4
    GUILD_ANNOUNCEMENT = 5
    ANNOUNCEMENT_THREAD = 10
    PUBLIC_THREAD = 11
    PRIVATE_THREAD = 12
    GUILD_STAGE_VOICE = 13
    GUILD_DIRECTORY = 14
    GUILD_FORUM = 15
    GUILD_MEDIA = 16


class MessageType(DiscordIntEnum):
    DEFAULT = 0
    RECIPIENT_ADD = 1
    RECIPIENT_REMOVE = 2
    CALL = 3
    CHANNEL_NAME_CHANGE = 4
    CHANNEL_ICON_CHANGE = 5
    CHANNEL_PINNED_MESSAGE = 6
    USER_JOIN = 7
    GUILD_BOOST = 8
    GUILD_BOOST_TIER_1 = 9
    GUILD_BOOST_TIER_2 = 10
    GUILD_BOOST_TIER_3 = 11
    CHANNEL_FOLLOW_ADD = 12
    GUILD_DISCOVERY_DISQUALIFIED = 14
    GUILD_DISCOVERY_REQUALIFIED = 15
    GUILD_DISCOVERY_GRACE_PERIOD_INITIAL_WARNING = 16
    GUILD_DISCOVERY_GRACE_PERIOD_FINAL_WARNING = 17
    THREAD_CREATED = 18
    REPLY = 19
    CHAT_INPUT_COMMAND = 20
    THREAD_STARTER_MESSAGE = 21
    GUILD_INVITE_REMINDER = 22
    CONTEXT_MENU_COMMAND = 23
    AUTO_MODERATION_ACTION = 24
    ROLE_SUBSCRIPTION_PURCHASE = 25
    INTERACTION_PREMIUM_UPSELL = 26
    STAGE_START = 27
    STAGE_END = 28
    STAGE_SPEAKER = 29
    STAGE_TOPIC = 31
    GUILD_APPLICATION_PREMIUM_SUBSCRIPTION = 32
    GUILD_INCIDENT_ALERT_MODE_ENABLED = 36
    GUILD_INCIDENT_ALERT_MODE_DISABLED = 37
    GUILD_INCIDENT_REPORT_RAID = 38
    GUILD_INCIDENT_REPORT_FALSE_ALARM = 39
    PURCHASE_NOTIFICATION = 44
    POLL_RESULT = 46


class InteractionType(DiscordIntEnum):
    PING = 1
    APPLICATION_COMMAND = 2
    MESSAGE_COMPONENT = 3
    APPLICATION_COMMAND_AUTOCOMPLETE = 4
    MODAL_SUBMIT = 5


class InteractionCallbackType(DiscordIntEnum):
    PONG = 1
    CHANNEL_MESSAGE_WITH_SOURCE = 4
    DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5
    DEFERRED_UPDATE_MESSAGE = 6
    UPDATE_MESSAGE = 7
    APPLICATION_COMMAND_AUTOCOMPLETE_RESULT = 8
    MODAL = 9
    LAUNCH_ACTIVITY = 12


class InteractionContextType(DiscordIntEnum):
    GUILD = 0
    BOT_DM = 1
    PRIVATE_CHANNEL = 2


class ComponentType(DiscordIntEnum):
    ACTION_ROW = 1
    BUTTON = 2
    STRING_SELECT = 3
    TEXT_INPUT = 4
    USER_SELECT = 5
    ROLE_SELECT = 6
    MENTIONABLE_SELECT = 7
    CHANNEL_SELECT = 8
    SECTION = 9
    TEXT_DISPLAY = 10
    THUMBNAIL = 11
    MEDIA_GALLERY = 12
    FILE = 13
    SEPARATOR = 14
    CONTAINER = 17
    LABEL = 18
    FILE_UPLOAD = 19
    RADIO_GROUP = 21
    CHECKBOX_GROUP = 22
    CHECKBOX = 23


class OverwriteType(DiscordIntEnum):
    ROLE = 0
    MEMBER = 1


class VerificationLevel(DiscordIntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    VERY_HIGH = 4


class DefaultMessageNotificationLevel(DiscordIntEnum):
    ALL_MESSAGES = 0
    ONLY_MENTIONS = 1


class ExplicitContentFilterLevel(DiscordIntEnum):
    DISABLED = 0
    MEMBERS_WITHOUT_ROLES = 1
    ALL_MEMBERS = 2


class PremiumTier(DiscordIntEnum):
    NONE = 0
    TIER_1 = 1
    TIER_2 = 2
    TIER_3 = 3


class NSFWLevel(DiscordIntEnum):
    DEFAULT = 0
    EXPLICIT = 1
    SAFE = 2
    AGE_RESTRICTED = 3


class StickerType(DiscordIntEnum):
    STANDARD = 1
    GUILD = 2


class StickerFormatType(DiscordIntEnum):
    PNG = 1
    APNG = 2
    LOTTIE = 3
    GIF = 4


class JSONErrorCode(DiscordIntEnum):
    """The JSON error codes bostie acts on; HTTPException.code holds any code."""

    UNKNOWN_WEBHOOK = 10015
    UNKNOWN_INTERACTION = 10062
    UNKNOWN_VOICE_STATE = 10065
    INDEX_NOT_YET_AVAILABLE = 110000


class ReactionType(DiscordIntEnum):
    NORMAL = 0
    BURST = 1


class MessageReferenceType(DiscordIntEnum):
    DEFAULT = 0
    FORWARD = 1


class PollLayoutType(DiscordIntEnum):
    DEFAULT = 1


class BaseTheme(DiscordIntEnum):
    UNSET = 0
    DARK = 1
    LIGHT = 2
    DARKER = 3
    MIDNIGHT = 4


class ButtonStyle(DiscordIntEnum):
    PRIMARY = 1
    SECONDARY = 2
    SUCCESS = 3
    DANGER = 4
    LINK = 5
    PREMIUM = 6


class TextInputStyle(DiscordIntEnum):
    SHORT = 1
    PARAGRAPH = 2


class OptionType(DiscordIntEnum):
    SUB_COMMAND = 1
    SUB_COMMAND_GROUP = 2
    STRING = 3
    INTEGER = 4
    BOOLEAN = 5
    USER = 6
    CHANNEL = 7
    ROLE = 8
    MENTIONABLE = 9
    NUMBER = 10
    ATTACHMENT = 11


class CommandType(DiscordIntEnum):
    CHAT_INPUT = 1
    USER = 2
    MESSAGE = 3
    PRIMARY_ENTRY_POINT = 4


class EntryPointCommandHandlerType(DiscordIntEnum):
    APP_HANDLER = 1
    DISCORD_LAUNCH_ACTIVITY = 2


class ApplicationIntegrationType(DiscordIntEnum):
    GUILD_INSTALL = 0
    USER_INSTALL = 1


class ApplicationEventWebhookStatus(DiscordIntEnum):
    DISABLED = 1
    ENABLED = 2
    DISABLED_BY_DISCORD = 3


class ApplicationRoleConnectionMetadataType(DiscordIntEnum):
    INTEGER_LESS_THAN_OR_EQUAL = 1
    INTEGER_GREATER_THAN_OR_EQUAL = 2
    INTEGER_EQUAL = 3
    INTEGER_NOT_EQUAL = 4
    DATETIME_LESS_THAN_OR_EQUAL = 5
    DATETIME_GREATER_THAN_OR_EQUAL = 6
    BOOLEAN_EQUAL = 7
    BOOLEAN_NOT_EQUAL = 8


class MembershipState(DiscordIntEnum):
    INVITED = 1
    ACCEPTED = 2


class ActivityLocationKind(DiscordStrEnum):
    GUILD_CHANNEL = "gc"
    PRIVATE_CHANNEL = "pc"


class AutoModerationTriggerType(DiscordIntEnum):
    KEYWORD = 1
    SPAM = 3
    KEYWORD_PRESET = 4
    MENTION_SPAM = 5
    MEMBER_PROFILE = 6


class AutoModerationEventType(DiscordIntEnum):
    MESSAGE_SEND = 1
    MEMBER_UPDATE = 2


class AutoModerationActionType(DiscordIntEnum):
    BLOCK_MESSAGE = 1
    SEND_ALERT_MESSAGE = 2
    TIMEOUT = 3
    BLOCK_MEMBER_INTERACTION = 4


class KeywordPresetType(DiscordIntEnum):
    PROFANITY = 1
    SEXUAL_CONTENT = 2
    SLURS = 3


class AuditLogEvent(DiscordIntEnum):
    GUILD_UPDATE = 1
    CHANNEL_CREATE = 10
    CHANNEL_UPDATE = 11
    CHANNEL_DELETE = 12
    CHANNEL_OVERWRITE_CREATE = 13
    CHANNEL_OVERWRITE_UPDATE = 14
    CHANNEL_OVERWRITE_DELETE = 15
    CHANNEL_POSITION_UPDATE = 16
    MEMBER_KICK = 20
    MEMBER_PRUNE = 21
    MEMBER_BAN_ADD = 22
    MEMBER_BAN_REMOVE = 23
    MEMBER_UPDATE = 24
    MEMBER_ROLE_UPDATE = 25
    MEMBER_MOVE = 26
    MEMBER_DISCONNECT = 27
    BOT_ADD = 28
    ROLE_CREATE = 30
    ROLE_UPDATE = 31
    ROLE_DELETE = 32
    ROLE_POSITION_UPDATE = 33
    INVITE_CREATE = 40
    INVITE_UPDATE = 41
    INVITE_DELETE = 42
    WEBHOOK_CREATE = 50
    WEBHOOK_UPDATE = 51
    WEBHOOK_DELETE = 52
    EMOJI_CREATE = 60
    EMOJI_UPDATE = 61
    EMOJI_DELETE = 62
    MESSAGE_DELETE = 72
    MESSAGE_BULK_DELETE = 73
    MESSAGE_PIN = 74
    MESSAGE_UNPIN = 75
    INTEGRATION_CREATE = 80
    INTEGRATION_UPDATE = 81
    INTEGRATION_DELETE = 82
    STAGE_INSTANCE_CREATE = 83
    STAGE_INSTANCE_UPDATE = 84
    STAGE_INSTANCE_DELETE = 85
    STICKER_CREATE = 90
    STICKER_UPDATE = 91
    STICKER_DELETE = 92
    GUILD_SCHEDULED_EVENT_CREATE = 100
    GUILD_SCHEDULED_EVENT_UPDATE = 101
    GUILD_SCHEDULED_EVENT_DELETE = 102
    THREAD_CREATE = 110
    THREAD_UPDATE = 111
    THREAD_DELETE = 112
    APPLICATION_COMMAND_PERMISSION_UPDATE = 121
    SOUNDBOARD_SOUND_CREATE = 130
    SOUNDBOARD_SOUND_UPDATE = 131
    SOUNDBOARD_SOUND_DELETE = 132
    AUTO_MODERATION_RULE_CREATE = 140
    AUTO_MODERATION_RULE_UPDATE = 141
    AUTO_MODERATION_RULE_DELETE = 142
    AUTO_MODERATION_BLOCK_MESSAGE = 143
    AUTO_MODERATION_FLAG_TO_CHANNEL = 144
    AUTO_MODERATION_USER_COMMUNICATION_DISABLED = 145
    AUTO_MODERATION_QUARANTINE_USER = 146
    CREATOR_MONETIZATION_REQUEST_CREATED = 150
    CREATOR_MONETIZATION_TERMS_ACCEPTED = 151
    ONBOARDING_PROMPT_CREATE = 163
    ONBOARDING_PROMPT_UPDATE = 164
    ONBOARDING_PROMPT_DELETE = 165
    ONBOARDING_CREATE = 166
    ONBOARDING_UPDATE = 167
    HOME_SETTINGS_CREATE = 190
    HOME_SETTINGS_UPDATE = 191
    VOICE_CHANNEL_STATUS_CREATE = 192
    VOICE_CHANNEL_STATUS_DELETE = 193


class ScheduledEventPrivacyLevel(DiscordIntEnum):
    GUILD_ONLY = 2


class ScheduledEventEntityType(DiscordIntEnum):
    STAGE_INSTANCE = 1
    VOICE = 2
    EXTERNAL = 3


class ScheduledEventStatus(DiscordIntEnum):
    SCHEDULED = 1
    ACTIVE = 2
    COMPLETED = 3
    CANCELED = 4


class RecurrenceFrequency(DiscordIntEnum):
    YEARLY = 0
    MONTHLY = 1
    WEEKLY = 2
    DAILY = 3


class Weekday(DiscordIntEnum):
    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6


class Month(DiscordIntEnum):
    JANUARY = 1
    FEBRUARY = 2
    MARCH = 3
    APRIL = 4
    MAY = 5
    JUNE = 6
    JULY = 7
    AUGUST = 8
    SEPTEMBER = 9
    OCTOBER = 10
    NOVEMBER = 11
    DECEMBER = 12


class EntitlementType(DiscordIntEnum):
    PURCHASE = 1
    PREMIUM_SUBSCRIPTION = 2
    DEVELOPER_GIFT = 3
    TEST_MODE_PURCHASE = 4
    FREE_PURCHASE = 5
    USER_GIFT = 6
    PREMIUM_PURCHASE = 7
    APPLICATION_SUBSCRIPTION = 8


class SubscriptionStatus(DiscordIntEnum):
    ACTIVE = 0
    INACTIVE = 1
    ENDING = 2


class SKUType(DiscordIntEnum):
    DURABLE = 2
    CONSUMABLE = 3
    SUBSCRIPTION = 5
    SUBSCRIPTION_GROUP = 6


class Status(DiscordStrEnum):
    ONLINE = "online"
    DND = "dnd"
    IDLE = "idle"
    INVISIBLE = "invisible"
    OFFLINE = "offline"


class ActivityType(DiscordIntEnum):
    PLAYING = 0
    STREAMING = 1
    LISTENING = 2
    WATCHING = 3
    CUSTOM = 4
    COMPETING = 5


class StatusDisplayType(DiscordIntEnum):
    NAME = 0
    STATE = 1
    DETAILS = 2


class AnimationType(DiscordIntEnum):
    PREMIUM = 0
    BASIC = 1


class CommandPermissionType(DiscordIntEnum):
    ROLE = 1
    USER = 2
    CHANNEL = 3


class IntegrationExpireBehavior(DiscordIntEnum):
    REMOVE_ROLE = 0
    KICK = 1


class OnboardingMode(DiscordIntEnum):
    ONBOARDING_DEFAULT = 0
    ONBOARDING_ADVANCED = 1


class OnboardingPromptType(DiscordIntEnum):
    MULTIPLE_CHOICE = 0
    DROPDOWN = 1


class InviteType(DiscordIntEnum):
    GUILD = 0
    GROUP_DM = 1
    FRIEND = 2


class InviteTargetType(DiscordIntEnum):
    STREAM = 1
    EMBEDDED_APPLICATION = 2


class TargetUsersJobStatus(DiscordIntEnum):
    UNSPECIFIED = 0
    PROCESSING = 1
    COMPLETED = 2
    FAILED = 3


class StagePrivacyLevel(DiscordIntEnum):
    PUBLIC = 1
    GUILD_ONLY = 2


class Permissions(DiscordIntFlag):
    CREATE_INSTANT_INVITE = 1 << 0
    KICK_MEMBERS = 1 << 1
    BAN_MEMBERS = 1 << 2
    ADMINISTRATOR = 1 << 3
    MANAGE_CHANNELS = 1 << 4
    MANAGE_GUILD = 1 << 5
    ADD_REACTIONS = 1 << 6
    VIEW_AUDIT_LOG = 1 << 7
    PRIORITY_SPEAKER = 1 << 8
    STREAM = 1 << 9
    VIEW_CHANNEL = 1 << 10
    SEND_MESSAGES = 1 << 11
    SEND_TTS_MESSAGES = 1 << 12
    MANAGE_MESSAGES = 1 << 13
    EMBED_LINKS = 1 << 14
    ATTACH_FILES = 1 << 15
    READ_MESSAGE_HISTORY = 1 << 16
    MENTION_EVERYONE = 1 << 17
    USE_EXTERNAL_EMOJIS = 1 << 18
    VIEW_GUILD_INSIGHTS = 1 << 19
    CONNECT = 1 << 20
    SPEAK = 1 << 21
    MUTE_MEMBERS = 1 << 22
    DEAFEN_MEMBERS = 1 << 23
    MOVE_MEMBERS = 1 << 24
    USE_VAD = 1 << 25
    CHANGE_NICKNAME = 1 << 26
    MANAGE_NICKNAMES = 1 << 27
    MANAGE_ROLES = 1 << 28
    MANAGE_WEBHOOKS = 1 << 29
    MANAGE_GUILD_EXPRESSIONS = 1 << 30
    USE_APPLICATION_COMMANDS = 1 << 31
    REQUEST_TO_SPEAK = 1 << 32
    MANAGE_EVENTS = 1 << 33
    MANAGE_THREADS = 1 << 34
    CREATE_PUBLIC_THREADS = 1 << 35
    CREATE_PRIVATE_THREADS = 1 << 36
    USE_EXTERNAL_STICKERS = 1 << 37
    SEND_MESSAGES_IN_THREADS = 1 << 38
    USE_EMBEDDED_ACTIVITIES = 1 << 39
    MODERATE_MEMBERS = 1 << 40
    VIEW_CREATOR_MONETIZATION_ANALYTICS = 1 << 41
    USE_SOUNDBOARD = 1 << 42
    CREATE_GUILD_EXPRESSIONS = 1 << 43
    CREATE_EVENTS = 1 << 44
    USE_EXTERNAL_SOUNDS = 1 << 45
    SEND_VOICE_MESSAGES = 1 << 46
    SET_VOICE_CHANNEL_STATUS = 1 << 48
    SEND_POLLS = 1 << 49
    USE_EXTERNAL_APPS = 1 << 50
    PIN_MESSAGES = 1 << 51
    BYPASS_SLOWMODE = 1 << 52

    @classmethod
    @override
    def _missing_(cls, value: object) -> Self:
        permissions = super()._missing_(value)
        cls._value2member_map_.pop(value, None)
        return permissions


class Intents(DiscordIntFlag):
    GUILDS = 1 << 0
    GUILD_MEMBERS = 1 << 1
    GUILD_MODERATION = 1 << 2
    GUILD_EXPRESSIONS = 1 << 3
    GUILD_INTEGRATIONS = 1 << 4
    GUILD_WEBHOOKS = 1 << 5
    GUILD_INVITES = 1 << 6
    GUILD_VOICE_STATES = 1 << 7
    GUILD_PRESENCES = 1 << 8
    GUILD_MESSAGES = 1 << 9
    GUILD_MESSAGE_REACTIONS = 1 << 10
    GUILD_MESSAGE_TYPING = 1 << 11
    DIRECT_MESSAGES = 1 << 12
    DIRECT_MESSAGE_REACTIONS = 1 << 13
    DIRECT_MESSAGE_TYPING = 1 << 14
    MESSAGE_CONTENT = 1 << 15
    GUILD_SCHEDULED_EVENTS = 1 << 16
    AUTO_MODERATION_CONFIGURATION = 1 << 20
    AUTO_MODERATION_EXECUTION = 1 << 21
    GUILD_MESSAGE_POLLS = 1 << 24
    DIRECT_MESSAGE_POLLS = 1 << 25
    DEFAULT = (
        GUILDS
        | GUILD_MODERATION
        | GUILD_EXPRESSIONS
        | GUILD_INTEGRATIONS
        | GUILD_WEBHOOKS
        | GUILD_INVITES
        | GUILD_VOICE_STATES
        | GUILD_MESSAGES
        | GUILD_MESSAGE_REACTIONS
        | GUILD_MESSAGE_TYPING
        | DIRECT_MESSAGES
        | DIRECT_MESSAGE_REACTIONS
        | DIRECT_MESSAGE_TYPING
        | GUILD_SCHEDULED_EVENTS
        | AUTO_MODERATION_CONFIGURATION
        | AUTO_MODERATION_EXECUTION
        | GUILD_MESSAGE_POLLS
        | DIRECT_MESSAGE_POLLS
    )


class MessageFlags(DiscordIntFlag):
    CROSSPOSTED = 1 << 0
    IS_CROSSPOST = 1 << 1
    SUPPRESS_EMBEDS = 1 << 2
    SOURCE_MESSAGE_DELETED = 1 << 3
    URGENT = 1 << 4
    HAS_THREAD = 1 << 5
    EPHEMERAL = 1 << 6
    LOADING = 1 << 7
    FAILED_TO_MENTION_SOME_ROLES_IN_THREAD = 1 << 8
    SUPPRESS_NOTIFICATIONS = 1 << 12
    IS_VOICE_MESSAGE = 1 << 13
    HAS_SNAPSHOT = 1 << 14
    IS_COMPONENTS_V2 = 1 << 15


class ChannelFlags(DiscordIntFlag):
    PINNED = 1 << 1
    REQUIRE_TAG = 1 << 4
    HIDE_MEDIA_DOWNLOAD_OPTIONS = 1 << 15
    CHANNEL_OBFUSCATED = 1 << 17
    IS_SPOILER_CHANNEL = 1 << 21


class SKUFlags(DiscordIntFlag):
    AVAILABLE = 1 << 2
    GUILD_SUBSCRIPTION = 1 << 7
    USER_SUBSCRIPTION = 1 << 8


class GuildInviteFlags(DiscordIntFlag):
    IS_GUEST_INVITE = 1 << 0


class ApplicationFlags(DiscordIntFlag):
    APPLICATION_AUTO_MODERATION_RULE_CREATE_BADGE = 1 << 6
    GATEWAY_PRESENCE = 1 << 12
    GATEWAY_PRESENCE_LIMITED = 1 << 13
    GATEWAY_GUILD_MEMBERS = 1 << 14
    GATEWAY_GUILD_MEMBERS_LIMITED = 1 << 15
    VERIFICATION_PENDING_GUILD_LIMIT = 1 << 16
    EMBEDDED = 1 << 17
    GATEWAY_MESSAGE_CONTENT = 1 << 18
    GATEWAY_MESSAGE_CONTENT_LIMITED = 1 << 19
    APPLICATION_COMMAND_BADGE = 1 << 23


class UserFlags(DiscordIntFlag):
    STAFF = 1 << 0
    PARTNER = 1 << 1
    HYPESQUAD = 1 << 2
    BUG_HUNTER_LEVEL_1 = 1 << 3
    HYPESQUAD_ONLINE_HOUSE_1 = 1 << 6
    HYPESQUAD_ONLINE_HOUSE_2 = 1 << 7
    HYPESQUAD_ONLINE_HOUSE_3 = 1 << 8
    PREMIUM_EARLY_SUPPORTER = 1 << 9
    TEAM_PSEUDO_USER = 1 << 10
    BUG_HUNTER_LEVEL_2 = 1 << 14
    VERIFIED_BOT = 1 << 16
    VERIFIED_DEVELOPER = 1 << 17
    CERTIFIED_MODERATOR = 1 << 18
    BOT_HTTP_INTERACTIONS = 1 << 19


class MemberFlags(DiscordIntFlag):
    DID_REJOIN = 1 << 0
    COMPLETED_ONBOARDING = 1 << 1
    BYPASSES_VERIFICATION = 1 << 2
    STARTED_ONBOARDING = 1 << 3
    IS_GUEST = 1 << 4
    STARTED_HOME_ACTIONS = 1 << 5
    COMPLETED_HOME_ACTIONS = 1 << 6
    AUTOMOD_QUARANTINED_USERNAME = 1 << 7
    DM_SETTINGS_UPSELL_ACKNOWLEDGED = 1 << 9
    AUTOMOD_QUARANTINED_GUILD_TAG = 1 << 10


class RoleFlags(DiscordIntFlag):
    IN_PROMPT = 1 << 0


class SystemChannelFlags(DiscordIntFlag):
    SUPPRESS_JOIN_NOTIFICATIONS = 1 << 0
    SUPPRESS_PREMIUM_SUBSCRIPTIONS = 1 << 1
    SUPPRESS_GUILD_REMINDER_NOTIFICATIONS = 1 << 2
    SUPPRESS_JOIN_NOTIFICATION_REPLIES = 1 << 3
    SUPPRESS_ROLE_SUBSCRIPTION_PURCHASE_NOTIFICATIONS = 1 << 4
    SUPPRESS_ROLE_SUBSCRIPTION_PURCHASE_NOTIFICATION_REPLIES = 1 << 5


class AttachmentFlags(DiscordIntFlag):
    IS_CLIP = 1 << 0
    IS_THUMBNAIL = 1 << 1
    IS_SPOILER = 1 << 3
    CONTAINS_EXPLICIT_CONTENT = 1 << 4
    IS_ANIMATED = 1 << 5
    CONTAINS_GORE_CONTENT = 1 << 6
    CONTAINS_SELF_HARM_CONTENT = 1 << 7


class ActivityFlags(DiscordIntFlag):
    INSTANCE = 1 << 0
    JOIN = 1 << 1
    SPECTATE = 1 << 2
    JOIN_REQUEST = 1 << 3
    SYNC = 1 << 4
    PLAY = 1 << 5
    PARTY_PRIVACY_FRIENDS = 1 << 6
    PARTY_PRIVACY_VOICE_CHANNEL = 1 << 7
    EMBEDDED = 1 << 8


class SpeakingFlags(DiscordIntFlag):
    MICROPHONE = 1 << 0
    SOUNDSHARE = 1 << 1
    PRIORITY = 1 << 2


class State(StrEnum):
    CONNECTING = "connecting"
    READY = "ready"
    RESUMING = "resuming"
    RECONNECTING = "reconnecting"
    DISCONNECTED = "disconnected"


class DisconnectReason(StrEnum):
    REQUESTED = "requested"
    REMOVED = "removed"
    CALL_TERMINATED = "call_terminated"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    SESSION_INVALID = "session_invalid"
    AUTHENTICATION_FAILED = "authentication_failed"
    DAVE_REQUIRED = "dave_required"
    DAVE_FAILED = "dave_failed"
    PROTOCOL_ERROR = "protocol_error"
    CLOSED = "closed"


class Direction(StrEnum):
    SEND = "send"
    RECEIVE = "receive"


class ClosedReason(StrEnum):
    """Why a receiver ended: closed, disconnected, moved or began reconnecting."""

    CLOSED = "closed"
    DISCONNECTED = "disconnected"
    MOVED = "moved"
    RECONNECTING = "reconnecting"
