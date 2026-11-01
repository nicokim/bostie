from typing import TYPE_CHECKING, override

from bostie.enums import UserFlags
from bostie.models.base import Model

if TYPE_CHECKING:
    from bostie.models.interaction import Resolved


class PartialUser(Model, kw_only=True):
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


class User(PartialUser, kw_only=True):
    """A Discord user or bot account."""

    discriminator: str
    global_name: str | None
    system: bool = False
    banner: str | None = None
    accent_color: int | None = None
    public_flags: UserFlags = UserFlags(0)

    @property
    @override
    def display_name(self) -> str:
        return self.global_name or self.username

    @classmethod
    def from_resolved(cls, resolved: "Resolved", id: int) -> "User | None":
        return resolved.users.get(id)
