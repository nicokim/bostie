import msgspec

from bostie.enums import (
    ActivityFlags,
    ActivityType,
    Status,
    StatusDisplayType,
    UserFlags,
)
from bostie.models.base import MaybeSnowflake, Model, Snowflake


class PresenceUser(Model, Snowflake, kw_only=True):
    """The user of a presence: only `id` is sure to be sent."""

    id: int
    username: str | None = None
    discriminator: str | None = None
    global_name: str | None = None
    avatar: str | None = None
    bot: bool | None = None
    system: bool | None = None
    banner: str | None = None
    accent_color: int | None = None
    public_flags: UserFlags | None = None


class ClientStatus(Model, kw_only=True):
    """A user's status on each platform with an active session."""

    desktop: Status | None = None
    mobile: Status | None = None
    web: Status | None = None
    vr: Status | None = None


class ActivityTimestamps(Model, kw_only=True):
    """Unix times in milliseconds."""

    start: int | None = None
    end: int | None = None


class ActivityEmoji(Model, MaybeSnowflake, kw_only=True):
    name: str
    id: int | None = None
    animated: bool = False


class ActivityParty(Model, kw_only=True):
    id: str | None = None
    size: tuple[int, int] | None = None


class ActivityAssets(Model, kw_only=True):
    large_image: str | None = None
    large_text: str | None = None
    large_url: str | None = None
    small_image: str | None = None
    small_text: str | None = None
    small_url: str | None = None
    invite_cover_image: str | None = None


class ActivitySecrets(Model, kw_only=True):
    """Rich Presence secrets, kept out of the repr."""

    _join: str | None = msgspec.field(default=None, name="join")
    _spectate: str | None = msgspec.field(default=None, name="spectate")
    _match: str | None = msgspec.field(default=None, name="match")

    @property
    def join(self) -> str | None:
        return self._join

    @property
    def spectate(self) -> str | None:
        return self._spectate

    @property
    def match(self) -> str | None:
        return self._match


class Activity(Model, kw_only=True):
    """`created_at` is a unix time in milliseconds; `buttons` holds the labels."""

    name: str
    type: ActivityType
    url: str | None = None
    created_at: int
    timestamps: ActivityTimestamps | None = None
    application_id: int | None = None
    status_display_type: StatusDisplayType | None = None
    details: str | None = None
    details_url: str | None = None
    state: str | None = None
    state_url: str | None = None
    emoji: ActivityEmoji | None = None
    party: ActivityParty | None = None
    assets: ActivityAssets | None = None
    secrets: ActivitySecrets | None = None
    instance: bool = False
    flags: ActivityFlags = ActivityFlags(0)
    buttons: tuple[str, ...] = ()


class Presence(Model, kw_only=True):
    """A user's status and activities in a guild."""

    user: PresenceUser
    status: Status
    activities: tuple[Activity, ...]
    client_status: ClientStatus
