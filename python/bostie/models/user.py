from typing import TYPE_CHECKING, override

from bostie.enums import Locale, PremiumType, UserFlags
from bostie.models.base import Model, Snowflake

if TYPE_CHECKING:
    from bostie.models.message import Resolved


class PartialUser(Model, Snowflake, kw_only=True):
    """The author of a webhook's message, which carries only the webhook's identity."""

    id: int
    username: str
    avatar: str | None
    bot: bool = False

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"

    @property
    def display_name(self) -> str:
        return self.username


class AvatarDecorationData(Model, kw_only=True):
    asset: str
    sku_id: int


class Nameplate(Model, kw_only=True):
    """`palette` names the background color, such as "crimson"."""

    sku_id: int
    asset: str
    label: str
    palette: str


class Collectibles(Model, kw_only=True):
    """The collectibles of a user or member, avatar decorations aside."""

    nameplate: Nameplate | None = None


class PrimaryGuild(Model, kw_only=True):
    """The guild whose server tag a user shows."""

    identity_guild_id: int | None = None
    identity_enabled: bool | None = None
    tag: str | None = None
    badge: str | None = None


class DisplayNameStyles(Model, kw_only=True):
    """The font, effect and colors a user's display name is drawn with."""

    font_id: int
    effect_id: int
    colors: tuple[int, ...]


class User(PartialUser, kw_only=True):
    """A Discord user or bot account."""

    discriminator: str
    global_name: str | None
    system: bool = False
    banner: str | None = None
    accent_color: int | None = None
    public_flags: UserFlags = UserFlags(0)
    avatar_decoration_data: AvatarDecorationData | None = None
    collectibles: Collectibles | None = None
    primary_guild: PrimaryGuild | None = None
    display_name_styles: DisplayNameStyles | None = None

    @property
    @override
    def display_name(self) -> str:
        return self.global_name or self.username

    @classmethod
    def _from_resolved(cls, resolved: "Resolved", id: int) -> "User | None":
        return resolved.users.get(id)


class CurrentUser(User, kw_only=True):
    """The bot's own user, with the fields only the account's owner sees."""

    mfa_enabled: bool | None = None
    locale: Locale | None = None
    verified: bool | None = None
    email: str | None = None
    flags: UserFlags = UserFlags(0)
    premium_type: PremiumType | None = None
