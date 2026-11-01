from collections.abc import AsyncIterator, Sequence
from functools import partial
from typing import Literal, Required, TypedDict, Unpack

from bostie.models import SKU, Entitlement, PartialEntitlement, Subscription, decode
from bostie.rest.base import Group, each, paginate, wire


class MonetizationCreateTestEntitlement(TypedDict, total=False):
    """`owner_type` is 1 for a guild subscription and 2 for a user subscription."""

    sku_id: Required[int]
    owner_id: Required[int]
    owner_type: Required[Literal[1, 2]]


class MonetizationListEntitlements(TypedDict, total=False):
    user_id: int
    sku_ids: Sequence[int]
    before: int
    after: int
    limit: int
    guild_id: int
    exclude_ended: bool
    exclude_deleted: bool


class MonetizationListSubscriptions(TypedDict, total=False):
    before: int
    after: int
    limit: int
    user_id: int


class Monetization(Group):
    async def consume_entitlement(
        self, application_id: int, entitlement_id: int, /
    ) -> None:
        await self._http._request(
            "POST",
            "/applications/{application_id}/entitlements/{entitlement_id}/consume",
            application_id=application_id,
            entitlement_id=entitlement_id,
        )

    async def create_test_entitlement(
        self,
        application_id: int,
        /,
        **options: Unpack[MonetizationCreateTestEntitlement],
    ) -> PartialEntitlement:
        return await self._http._decoded(
            partial(decode, PartialEntitlement),
            "POST",
            "/applications/{application_id}/entitlements",
            json=wire(options, MonetizationCreateTestEntitlement),
            application_id=application_id,
        )

    async def delete_test_entitlement(
        self, application_id: int, entitlement_id: int, /
    ) -> None:
        await self._http._request(
            "DELETE",
            "/applications/{application_id}/entitlements/{entitlement_id}",
            application_id=application_id,
            entitlement_id=entitlement_id,
        )

    async def fetch_entitlement(
        self, application_id: int, entitlement_id: int, /
    ) -> Entitlement:
        return await self._http._decoded(
            partial(decode, Entitlement),
            "GET",
            "/applications/{application_id}/entitlements/{entitlement_id}",
            application_id=application_id,
            entitlement_id=entitlement_id,
        )

    async def fetch_subscription(
        self, sku_id: int, subscription_id: int, /
    ) -> Subscription:
        return await self._http._decoded(
            partial(decode, Subscription),
            "GET",
            "/skus/{sku_id}/subscriptions/{subscription_id}",
            sku_id=sku_id,
            subscription_id=subscription_id,
        )

    def list_entitlements(
        self, application_id: int, /, **options: Unpack[MonetizationListEntitlements]
    ) -> AsyncIterator[Entitlement]:
        """Walk the app's entitlements, active and ended, oldest first."""
        query = wire(options, MonetizationListEntitlements)
        if "sku_ids" in options:
            query["sku_ids"] = ",".join(map(str, options["sku_ids"]))
        pages = paginate(
            self._http,
            "/applications/{application_id}/entitlements",
            query,
            decode=each(partial(decode, Entitlement)),
            items=tuple,
            cursor=lambda entitlement: entitlement.id,
            page_size=100,
            forward=True,
            application_id=application_id,
        )
        return (entitlement async for page in pages for entitlement in page)

    async def list_skus(self, application_id: int, /) -> tuple[SKU, ...]:
        return await self._http._decoded(
            each(partial(decode, SKU)),
            "GET",
            "/applications/{application_id}/skus",
            application_id=application_id,
        )

    def list_subscriptions(
        self, sku_id: int, /, **options: Unpack[MonetizationListSubscriptions]
    ) -> AsyncIterator[Subscription]:
        pages = paginate(
            self._http,
            "/skus/{sku_id}/subscriptions",
            wire(options, MonetizationListSubscriptions),
            decode=each(partial(decode, Subscription)),
            items=tuple,
            cursor=lambda subscription: subscription.id,
            page_size=100,
            forward=True,
            sku_id=sku_id,
        )
        return (subscription async for page in pages for subscription in page)
