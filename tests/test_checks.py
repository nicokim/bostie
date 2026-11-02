import time
from types import SimpleNamespace
from typing import Any

import pytest

from bostie import Permissions
from bostie.checks import Cooldown, MaxConcurrency, bot_has_permissions, has_permissions
from bostie.errors import (
    BotMissingPermissions,
    CooldownActive,
    MaxConcurrencyReached,
    MissingPermissions,
)

P = Permissions


def context(
    user: int = 1,
    guild: int | None = 10,
    member: Permissions | None = P.SEND_MESSAGES,
    bot: Permissions = P.SEND_MESSAGES,
) -> Any:
    return SimpleNamespace(
        user=SimpleNamespace(id=user),
        guild_id=guild,
        channel_id=20,
        interaction=SimpleNamespace(
            member=None if member is None else SimpleNamespace(permissions=member),
            app_permissions=bot,
        ),
    )


def test_permission_checks_name_what_the_user_or_the_bot_lacks() -> None:
    moderate = P.KICK_MEMBERS | P.BAN_MEMBERS

    assert has_permissions(P.SEND_MESSAGES)(context())
    assert bot_has_permissions(P.SEND_MESSAGES)(context())
    with pytest.raises(MissingPermissions) as user:
        has_permissions(moderate | P.SEND_MESSAGES)(context())
    with pytest.raises(MissingPermissions):
        has_permissions(P.SEND_MESSAGES)(context(guild=None, member=None))
    with pytest.raises(BotMissingPermissions) as bot:
        bot_has_permissions(P.BAN_MEMBERS)(context())

    assert user.value.missing == moderate
    assert str(user.value) == "You need the Kick Members and Ban Members permissions."
    assert str(bot.value) == "The bot needs the Ban Members permission."


def test_a_cooldown_counts_uses_per_key_until_its_window_passes() -> None:
    per_guild = Cooldown(2, 0.05, per="guild")

    assert per_guild(context(user=1))
    assert per_guild(context(user=2))
    with pytest.raises(CooldownActive) as refused:
        per_guild(context(user=3))
    assert per_guild(context(guild=11))
    assert 0 < refused.value.retry_after <= 0.05
    time.sleep(0.06)
    assert per_guild(context(user=3))


def test_max_concurrency_refuses_until_a_run_is_released() -> None:
    one_per_user = MaxConcurrency(1, per="user")

    assert one_per_user(context(user=1))
    assert one_per_user(context(user=2))
    with pytest.raises(MaxConcurrencyReached):
        one_per_user(context(user=1))
    one_per_user.release(context(user=1))
    assert one_per_user(context(user=1))
