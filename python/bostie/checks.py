import time
from collections import deque
from collections.abc import Callable, Hashable
from typing import TYPE_CHECKING, Any, Literal

from bostie.enums import Permissions
from bostie.errors import (
    BotMissingPermissions,
    CooldownActive,
    MaxConcurrencyReached,
    MissingPermissions,
)

if TYPE_CHECKING:
    from bostie.context import ComponentContext, Context, ModalContext

__all__ = [
    "Cooldown",
    "MaxConcurrency",
    "Per",
    "bot_has_permissions",
    "has_permissions",
]

type AnyContext = Context | ComponentContext | ModalContext
type Per = Literal["user", "guild", "channel", "global"] | Callable[[Any], Hashable]


def has_permissions(permissions: Permissions) -> Callable[[Any], bool]:
    """A check that the user has `permissions` in the channel it was used in."""

    def check(ctx: AnyContext) -> bool:
        member = ctx.interaction.member
        granted = None if member is None else member.permissions
        missing = permissions & ~(Permissions(0) if granted is None else granted)
        if missing:
            raise MissingPermissions(missing)
        return True

    return check


def bot_has_permissions(permissions: Permissions) -> Callable[[Any], bool]:
    """A check that the bot has `permissions` in the channel it was used in."""

    def check(ctx: AnyContext) -> bool:
        missing = permissions & ~ctx.interaction.app_permissions
        if missing:
            raise BotMissingPermissions(missing)
        return True

    return check


class Cooldown:
    """A check that allows `rate` uses every `seconds` per user, guild or channel."""

    def __init__(self, rate: int, seconds: float, *, per: Per = "user") -> None:
        if rate < 1 or seconds <= 0:
            raise ValueError("a cooldown needs a rate of 1 or more and seconds > 0")
        self.rate = rate
        self.seconds = seconds
        self.per: Per = per
        self._uses: dict[Hashable, deque[float]] = {}
        self._sweep_at = 0.0

    def __call__(self, ctx: AnyContext) -> bool:
        now = time.monotonic()
        self._sweep(now)
        uses = self._uses.setdefault(_key(self.per, ctx), deque(maxlen=self.rate))
        if len(uses) == self.rate and (wait := uses[0] + self.seconds - now) > 0:
            raise CooldownActive(wait)
        uses.append(now)
        return True

    def _sweep(self, now: float) -> None:
        if now < self._sweep_at:
            return
        expired = now - self.seconds
        self._uses = {
            key: uses for key, uses in self._uses.items() if uses[-1] > expired
        }
        self._sweep_at = now + self.seconds


class MaxConcurrency:
    """A check that lets `number` runs go on at once per user, guild or channel."""

    def __init__(self, number: int, *, per: Per = "global") -> None:
        if number < 1:
            raise ValueError("max_concurrency needs a number of 1 or more")
        self.number = number
        self.per: Per = per
        self._running: dict[Hashable, int] = {}

    def __call__(self, ctx: AnyContext) -> bool:
        key = _key(self.per, ctx)
        running = self._running.get(key, 0)
        if running >= self.number:
            raise MaxConcurrencyReached(self.number)
        self._running[key] = running + 1
        return True

    def release(self, ctx: AnyContext) -> None:
        """End one run that this check let start."""
        key = _key(self.per, ctx)
        running = self._running.pop(key, 1) - 1
        if running:
            self._running[key] = running


def _key(per: Per, ctx: AnyContext) -> Hashable:
    match per:
        case "user":
            return ctx.user.id
        case "guild":
            return ("guild", ctx.guild_id) if ctx.guild_id else ("user", ctx.user.id)
        case "channel":
            return ctx.channel_id
        case "global":
            return None
        case _:
            return per(ctx)
