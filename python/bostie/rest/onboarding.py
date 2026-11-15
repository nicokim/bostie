from collections.abc import Sequence
from typing import Required, TypedDict, Unpack

from bostie.enums import OnboardingMode, OnboardingPromptType
from bostie.models import GuildOnboarding
from bostie.rest.base import ResourceGroup, wire


class OnboardingPromptOptionData(TypedDict, total=False):
    """An option to send; a custom emoji goes by `emoji_id`, a unicode one by name."""

    id: int
    channel_ids: Required[Sequence[int]]
    role_ids: Required[Sequence[int]]
    emoji_id: int
    emoji_name: str
    emoji_animated: bool
    title: Required[str]
    description: str | None


class OnboardingPromptData(TypedDict):
    id: int
    type: OnboardingPromptType
    options: Sequence[OnboardingPromptOptionData]
    title: str
    single_select: bool
    required: bool
    in_onboarding: bool


class OnboardingEdit(TypedDict, total=False):
    prompts: Sequence[OnboardingPromptData]
    default_channel_ids: Sequence[int]
    enabled: bool
    mode: OnboardingMode


class Onboarding(ResourceGroup):
    async def edit(
        self,
        guild_id: int,
        /,
        *,
        reason: str | None = None,
        **options: Unpack[OnboardingEdit],
    ) -> GuildOnboarding:
        return await self._call(
            GuildOnboarding,
            "PUT",
            "/guilds/{guild_id}/onboarding",
            json=wire(options, OnboardingEdit),
            reason=reason,
            guild_id=guild_id,
        )

    async def fetch(self, guild_id: int, /) -> GuildOnboarding:
        return await self._call(
            GuildOnboarding,
            "GET",
            "/guilds/{guild_id}/onboarding",
            guild_id=guild_id,
        )
