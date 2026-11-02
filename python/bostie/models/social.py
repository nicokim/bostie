from typing import Any

from bostie.enums import LobbyMemberFlags, MessageFlags, MessageType, ProfileFieldType
from bostie.models.base import Builder, Model
from bostie.models.channel import Channel
from bostie.models.user import User


class LobbyMember(Model, kw_only=True):
    id: int
    metadata: dict[str, str] | None = None
    flags: LobbyMemberFlags = LobbyMemberFlags(0)
    additional_name: str | None = None


class Lobby(Model, kw_only=True):
    """A Social SDK lobby; `metadata` holds the game's key-value pairs."""

    id: int
    application_id: int
    metadata: dict[str, str] | None
    members: tuple[LobbyMember, ...]
    linked_channel: Channel | None = None


class LobbyMessageMember(Model, kw_only=True):
    additional_name: str


class LobbyMessage(Model, kw_only=True):
    """A message in a lobby; `application_id` is absent outside SDK sessions."""

    id: int
    type: MessageType
    content: str
    lobby_id: int
    channel_id: int
    author: User
    lobby_member: LobbyMessageMember | None = None
    metadata: dict[str, str] | None = None
    moderation_metadata: dict[str, str] | None = None
    flags: MessageFlags
    application_id: int | None = None


class ApplicationIdentity(Model, kw_only=True):
    """A user's account in the game's own account system, linked to Discord."""

    provider_type: str
    provider_id: str | None = None
    provider_issued_user_id: str
    user_id: int | None = None


class ProfileMedia(Builder, kw_only=True):
    url: str


class PrimaryProfileData(Builder, kw_only=True):
    """The game stats every Game Stats Widget knows; each one is optional."""

    season: str | None = None
    rank_name: str | None = None
    rank_image: ProfileMedia | None = None
    highest_rank: str | None = None
    highest_rank_image: ProfileMedia | None = None
    featured_played_character: str | None = None
    featured_played_character_image: ProfileMedia | None = None
    playtime_hours: float | None = None
    total_wins: int | None = None
    current_period_wins: int | None = None
    total_games: int | None = None
    current_period_games: int | None = None
    total_kills: int | None = None
    current_period_kills: int | None = None
    total_assists: int | None = None
    current_period_assists: int | None = None
    total_deaths: int | None = None
    current_period_deaths: int | None = None


class ProfileField(Builder, kw_only=True):
    """A game stat of the game's own, named for the widget editor."""

    type: ProfileFieldType
    name: str
    value: str | float | ProfileMedia


class ProfileData(Builder, kw_only=True):
    primary: PrimaryProfileData | None = None
    dynamic: tuple[ProfileField, ...] = ()


class ApplicationIdentityProfile(Model, kw_only=True):
    """The game data a Game Stats Widget shows on a user's profile."""

    username: str | None
    metadata: Any
    data: ProfileData | None
