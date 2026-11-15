from datetime import datetime
from typing import Any

import msgspec

from bostie.enums import EntitlementType, SKUFlags, SKUType, SubscriptionStatus
from bostie.models.base import Model, Snowflake, decode, decoder


class PartialEntitlement(Model, Snowflake, kw_only=True):
    """A test entitlement, which has no period because it never ends."""

    id: int
    sku_id: int
    application_id: int
    user_id: int | None = None
    type: EntitlementType
    deleted: bool
    guild_id: int | None = None
    consumed: bool | None = None


class Entitlement(PartialEntitlement, kw_only=True):
    """A user's or a guild's access to a SKU of the app."""

    starts_at: datetime | None
    ends_at: datetime | None


class _Period(msgspec.Struct, gc=False):
    starts_at: Any = msgspec.UNSET


def decode_entitlement(data: bytes) -> PartialEntitlement:
    """Decode an entitlement, as a `PartialEntitlement` when it has no period."""
    period = decoder(_Period).decode(data).starts_at
    model = PartialEntitlement if period is msgspec.UNSET else Entitlement
    return decode(model, data)


class SKU(Model, Snowflake, kw_only=True):
    """A premium offering of the app."""

    id: int
    type: SKUType
    application_id: int
    name: str
    slug: str
    flags: SKUFlags


class Subscription(Model, Snowflake, kw_only=True):
    """A user's recurring payment for SKUs of the app."""

    id: int
    user_id: int
    sku_ids: tuple[int, ...]
    entitlement_ids: tuple[int, ...]
    renewal_sku_ids: tuple[int, ...] | None
    current_period_start: datetime
    current_period_end: datetime
    status: SubscriptionStatus
    canceled_at: datetime | None
    country: str | None = None
