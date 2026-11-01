from datetime import datetime

from bostie.enums import EntitlementType, SKUFlags, SKUType, SubscriptionStatus
from bostie.models.base import Model


class PartialEntitlement(Model, kw_only=True):
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


class SKU(Model, kw_only=True):
    """A premium offering of the app."""

    id: int
    type: SKUType
    application_id: int
    name: str
    slug: str
    flags: SKUFlags


class Subscription(Model, kw_only=True):
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
