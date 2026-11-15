from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Literal, TypedDict, Unpack, cast

from bostie.enums import (
    DefaultMessageNotificationLevel,
    ExplicitContentFilterLevel,
    Locale,
    SystemChannelFlags,
    VerificationLevel,
)
from bostie.file import File
from bostie.models import (
    CurrentUserGuild,
    Guild,
    GuildPreview,
    GuildWidget,
    GuildWidgetSettings,
    IncidentsData,
    VanityURL,
    WelcomeScreen,
    WelcomeScreenChannel,
)
from bostie.models.base import Model
from bostie.rest.base import ResourceGroup, each, wire

if TYPE_CHECKING:
    from bostie.http import Query


class GuildBeginPrune(TypedDict, total=False):
    days: int
    compute_prune_count: bool
    include_roles: Sequence[int]


class GuildEdit(TypedDict, total=False):
    name: str
    verification_level: VerificationLevel | None
    default_message_notifications: DefaultMessageNotificationLevel | None
    explicit_content_filter: ExplicitContentFilterLevel | None
    afk_channel_id: int | None
    afk_timeout: int
    icon: File | None
    splash: File | None
    discovery_splash: File | None
    banner: File | None
    system_channel_id: int | None
    system_channel_flags: SystemChannelFlags
    rules_channel_id: int | None
    public_updates_channel_id: int | None
    preferred_locale: Locale | None
    features: Sequence[str]
    description: str | None
    premium_progress_bar_enabled: bool
    safety_alerts_channel_id: int | None


class GuildEditIncidentActions(TypedDict, total=False):
    invites_disabled_until: datetime | None
    dms_disabled_until: datetime | None


class GuildEditWelcomeScreen(TypedDict, total=False):
    enabled: bool | None
    welcome_channels: Sequence[WelcomeScreenChannel] | None
    description: str | None


class GuildEditWidget(TypedDict, total=False):
    enabled: bool
    channel_id: int | None


class GuildFetch(TypedDict, total=False):
    with_counts: bool


class GuildFetchPruneCount(TypedDict, total=False):
    days: int
    include_roles: Sequence[int]


class GuildFetchWidgetImage(TypedDict, total=False):
    style: Literal["shield", "banner1", "banner2", "banner3", "banner4"]


class GuildList(TypedDict, total=False):
    before: int | datetime
    after: int | datetime
    limit: int
    shard: int
    with_counts: bool


class _PruneCount(Model, kw_only=True):
    pruned: int


class _Pruned(Model, kw_only=True):
    pruned: int | None


class BearerGuilds(ResourceGroup):
    def list(self, **options: Unpack[GuildList]) -> AsyncIterator[CurrentUserGuild]:
        """Walk the user's guilds oldest first, or newest first with `before`."""
        pages = self._paginate(
            "/users/@me/guilds",
            wire(options, GuildList),
            decodes=each(CurrentUserGuild),
            items=tuple,
            cursor=lambda guild: guild.id,
            page_size=200,
            forward=True,
        )
        return (guild async for page in pages for guild in page)


class Guilds(BearerGuilds):
    async def begin_prune(
        self,
        guild_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[GuildBeginPrune],
    ) -> int | None:
        """Kick inactive members; the count is None with `compute_prune_count=False`."""
        pruned = await self._call(
            _Pruned,
            "POST",
            "/guilds/{guild_id}/prune",
            json=wire(options, GuildBeginPrune),
            reason=reason,
            guild_id=guild_id,
        )
        return pruned.pruned

    async def edit(
        self,
        guild_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[GuildEdit],
    ) -> Guild:
        return await self._call(
            Guild,
            "PATCH",
            "/guilds/{guild_id}",
            json=wire(options, GuildEdit),
            reason=reason,
            guild_id=guild_id,
        )

    async def edit_incident_actions(
        self, guild_id: int, /, **options: Unpack[GuildEditIncidentActions]
    ) -> IncidentsData:
        return await self._call(
            IncidentsData,
            "PUT",
            "/guilds/{guild_id}/incident-actions",
            json=wire(options, GuildEditIncidentActions),
            guild_id=guild_id,
        )

    async def edit_welcome_screen(
        self,
        guild_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[GuildEditWelcomeScreen],
    ) -> WelcomeScreen:
        return await self._call(
            WelcomeScreen,
            "PATCH",
            "/guilds/{guild_id}/welcome-screen",
            json=wire(options, GuildEditWelcomeScreen),
            reason=reason,
            guild_id=guild_id,
        )

    async def edit_widget(
        self,
        guild_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[GuildEditWidget],
    ) -> GuildWidgetSettings:
        return await self._call(
            GuildWidgetSettings,
            "PATCH",
            "/guilds/{guild_id}/widget",
            json=wire(options, GuildEditWidget),
            reason=reason,
            guild_id=guild_id,
        )

    async def fetch(self, guild_id: int, /, **options: Unpack[GuildFetch]) -> Guild:
        return await self._call(
            Guild,
            "GET",
            "/guilds/{guild_id}",
            query=wire(options, GuildFetch),
            guild_id=guild_id,
        )

    async def fetch_preview(self, guild_id: int, /) -> GuildPreview:
        return await self._call(
            GuildPreview,
            "GET",
            "/guilds/{guild_id}/preview",
            guild_id=guild_id,
        )

    async def fetch_prune_count(
        self, guild_id: int, /, **options: Unpack[GuildFetchPruneCount]
    ) -> int:
        """How many members `begin_prune` would kick with the same options."""
        query = wire(options, GuildFetchPruneCount)
        if "include_roles" in options:
            query["include_roles"] = ",".join(map(str, options["include_roles"]))
        count = await self._call(
            _PruneCount,
            "GET",
            "/guilds/{guild_id}/prune",
            query=query,
            guild_id=guild_id,
        )
        return count.pruned

    async def fetch_vanity_url(self, guild_id: int, /) -> VanityURL:
        return await self._call(
            VanityURL,
            "GET",
            "/guilds/{guild_id}/vanity-url",
            guild_id=guild_id,
        )

    async def fetch_welcome_screen(self, guild_id: int, /) -> WelcomeScreen:
        return await self._call(
            WelcomeScreen,
            "GET",
            "/guilds/{guild_id}/welcome-screen",
            guild_id=guild_id,
        )

    async def fetch_widget(self, guild_id: int, /) -> GuildWidget:
        return await self._call(
            GuildWidget,
            "GET",
            "/guilds/{guild_id}/widget.json",
            guild_id=guild_id,
        )

    async def fetch_widget_image(
        self, guild_id: int, /, **options: Unpack[GuildFetchWidgetImage]
    ) -> bytes:
        """The widget as a PNG image."""
        return await self._http.send(
            "GET",
            "/guilds/{guild_id}/widget.png",
            query=cast("Query", wire(options, GuildFetchWidgetImage)),
            guild_id=guild_id,
        )

    async def fetch_widget_settings(self, guild_id: int, /) -> GuildWidgetSettings:
        return await self._call(
            GuildWidgetSettings,
            "GET",
            "/guilds/{guild_id}/widget",
            guild_id=guild_id,
        )

    async def leave(self, guild_id: int, /) -> None:
        await self._http.send(
            "DELETE", "/users/@me/guilds/{guild_id}", guild_id=guild_id
        )
