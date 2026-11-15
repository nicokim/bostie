import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from bostie import Bot, Context, Permissions
from bostie.checks import (
    Cooldown,
    MaxConcurrency,
    bot_has_permissions,
    has_any_role,
    has_permissions,
    has_role,
    is_owner,
)
from bostie.errors import (
    BotMissingPermissions,
    CooldownActive,
    MaxConcurrencyReached,
    MissingPermissions,
)
from bostie.events import GuildCreate
from bostie.testing import FakeDiscord, payloads, run, running
from bostie.testing.payloads import Json

P = Permissions
USER = int(payloads.interaction(2, {})["member"]["user"]["id"])
APP_OWNER = int(payloads.fixture("rest/application_me")["owner"]["id"])
TEAM_OWNER, ADMIN, READER, INVITED = 101, 102, 103, 104
MODERATOR_ROLE = int(payloads.interaction(2, {})["member"]["roles"][0])
ADMIN_ROLE = 1335174050611331073


def context(
    user: int = 1,
    guild: int | None = 10,
    member: Permissions | None = P.SEND_MESSAGES,
    bot: Permissions = P.SEND_MESSAGES,
    channel: int = 20,
) -> Any:
    return SimpleNamespace(
        user=SimpleNamespace(id=user),
        guild_id=guild,
        channel_id=channel,
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


def odd_user(ctx: Any) -> bool:
    return bool(ctx.user.id % 2)


@pytest.mark.parametrize(
    ("per", "first", "second", "shared"),
    [
        ("guild", context(user=1), context(user=2), True),
        ("guild", context(user=1, guild=None), context(user=2, guild=None), False),
        ("channel", context(user=1), context(user=2), True),
        ("channel", context(channel=20), context(channel=21), False),
        (odd_user, context(user=1), context(user=3), True),
        (odd_user, context(user=1), context(user=2), False),
    ],
)
def test_limits_key_on_the_guild_or_dm_user_the_channel_or_your_own_key(
    per: Any, first: Any, second: Any, shared: bool
) -> None:
    limit = MaxConcurrency(1, per=per)
    assert limit(first)
    try:
        limit(second)
    except MaxConcurrencyReached:
        refused = True
    else:
        refused = False
    assert refused is shared


def team_member(user_id: int, role: str, state: int = 2) -> Json:
    return {
        "membership_state": state,
        "team_id": "1",
        "user": payloads.user(id=str(user_id)),
        "role": role,
    }


TEAM = {
    "icon": None,
    "id": "1",
    "name": "Crew",
    "owner_user_id": str(TEAM_OWNER),
    "members": [
        team_member(ADMIN, "admin"),
        team_member(USER, "developer"),
        team_member(READER, "read_only"),
        team_member(INVITED, "admin", state=1),
    ],
}


@pytest.mark.parametrize(
    ("application", "owner_ids", "owners"),
    [
        ({}, None, {APP_OWNER}),
        ({"team": TEAM}, None, {TEAM_OWNER, ADMIN, USER}),
        ({"team": TEAM}, [USER], {USER}),
        ({}, [], set()),
    ],
)
def test_is_owner_lets_through_the_given_owners_or_the_apps(
    application: Json, owner_ids: list[int] | None, owners: set[int]
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url, owner_ids=owner_ids)

        @bot.command(checks=[is_owner])
        async def shutdown(ctx: Context) -> None:
            """Stop the bot."""
            await ctx.respond("Bye.")

        app = payloads.fixture("rest/application_me") | application
        fake.fail(
            "GET", "/applications/@me", 200, app | {"id": str(fake.application_id)}
        )
        async with running(bot, fake):
            answer = await fake.interaction(2, payloads.command("shutdown"))

        assert bot.owner_ids == owners
        assert answer.json["data"]["content"] == (
            "Bye." if USER in owners else "Only the bot's owners can use this."
        )

    run(scenario)


@pytest.mark.parametrize(
    ("roles", "cached", "refusal"),
    [
        ((MODERATOR_ROLE,), False, None),
        (("Moderator",), True, None),
        (("Moderator",), False, "You need the Moderator role."),
        (("Admin", MODERATOR_ROLE), True, None),
        ((ADMIN_ROLE, "Owner"), True, f"You need the <@&{ADMIN_ROLE}> or Owner role."),
    ],
)
def test_role_checks_match_the_users_role_ids_or_the_cached_names(
    roles: tuple[int | str, ...], cached: bool, refusal: str | None
) -> None:
    check = has_role(roles[0]) if len(roles) == 1 else has_any_role(*roles)

    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)

        @bot.command(checks=[check])
        async def warn(ctx: Context) -> None:
            """Warn a member."""
            await ctx.respond("Warned.")

        async with running(bot, fake):
            if cached:
                await asyncio.gather(
                    bot.wait_for(GuildCreate),
                    fake.dispatch("GUILD_CREATE", payloads.guild()),
                )
            answer = await fake.interaction(2, payloads.command("warn"))

        assert answer.json["data"]["content"] == (refusal or "Warned.")

    run(scenario)
