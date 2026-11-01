from typing import TYPE_CHECKING, ClassVar

from bostie.enums import UserFlags
from bostie.models.base import Model

if TYPE_CHECKING:
    from bostie.models.interaction import Resolved


class User(Model, kw_only=True):
    """A Discord user or bot account."""

    __option_type__: ClassVar[int] = 6

    id: int
    username: str | None = None
    global_name: str | None = None
    discriminator: str | None = None
    avatar: str | None = None
    bot: bool | None = None
    system: bool | None = None
    banner: str | None = None
    accent_color: int | None = None
    public_flags: UserFlags | None = None

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"

    @property
    def display_name(self) -> str:
        return self.global_name or self.username or ""

    @classmethod
    def from_resolved(cls, resolved: "Resolved", id: int) -> "User | None":
        return resolved.users.get(id) if resolved.users else None
