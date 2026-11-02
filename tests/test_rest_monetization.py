from typing import Any

from bostie import Bot, Entitlement, PartialEntitlement, SKUFlags, SKUType
from bostie.testing import FakeDiscord, payloads, run, running

ROUTE = "/applications/1/entitlements"


def entitlements(ids: range) -> list[dict[str, Any]]:
    entitlement = payloads.fixture("rest/entitlement")
    return [entitlement | {"id": str(id)} for id in ids]


def endless_entitlement(id: int) -> dict[str, Any]:
    entitlement = payloads.fixture("rest/entitlement") | {"id": str(id)}
    del entitlement["starts_at"], entitlement["ends_at"]
    return entitlement


def test_entitlements_walk_from_the_oldest_with_sku_ids_as_one_value() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            fake.fail("GET", ROUTE, 200, entitlements(range(1, 101)))
            fake.fail(
                "GET",
                ROUTE,
                200,
                [*entitlements(range(101, 130)), endless_entitlement(130)],
            )
            monetization = bot.rest.monetization
            walked = monetization.list_entitlements(1, sku_ids=[7, 8], limit=150)
            listed = [entitlement async for entitlement in walked]
            assert [entitlement.id for entitlement in listed] == [*range(1, 131)]
            assert type(listed[-1]) is PartialEntitlement
            assert isinstance(listed[0], Entitlement)
            assert [request.query for request in fake.requests[-2:]] == [
                {"sku_ids": "7,8", "limit": "100"},
                {"sku_ids": "7,8", "limit": "50", "after": "100"},
            ]

    run(scenario)


def test_test_entitlements_have_no_period_and_skus_read_their_flags() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            monetization = bot.rest.monetization
            entitlement = endless_entitlement(1)
            fake.fail("POST", ROUTE, 200, entitlement)
            created = await monetization.create_test_entitlement(
                1, sku_id=2, owner_id=3, owner_type=2
            )
            assert fake.requests[-1].json == {
                "sku_id": 2,
                "owner_id": 3,
                "owner_type": 2,
            }
            assert created.sku_id == int(entitlement["sku_id"])

            sku = payloads.fixture("rest/sku")
            fake.fail("GET", "/applications/1/skus", 200, [sku])
            (listed,) = await monetization.list_skus(1)
            assert (listed.type, listed.flags) == (
                SKUType.SUBSCRIPTION,
                SKUFlags.GUILD_SUBSCRIPTION,
            )

    run(scenario)
