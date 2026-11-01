from typing import TYPE_CHECKING

from bostie.enums import UserFlags
from bostie.models.base import Model

if TYPE_CHECKING:
    from bostie.models.interaction import Resolved


class User(Model, kw_only=True):
    """A Discord user or bot account."""

    id: int
    username: str
    discriminator: str
    global_name: str | None
    avatar: str | None
    bot: bool = False
    system: bool = False
    banner: str | None = None
    accent_color: int | None = None
    public_flags: UserFlags = UserFlags(0)

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"

    @property
    def display_name(self) -> str:
        return self.global_name or self.username

    @classmethod
    def from_resolved(cls, resolved: "Resolved", id: int) -> "User | None":
        return resolved.users.get(id)
