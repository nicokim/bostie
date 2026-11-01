from bostie.enums import OnboardingMode, OnboardingPromptType
from bostie.models.base import Model
from bostie.models.components import PartialEmoji


class OnboardingPromptOption(Model, kw_only=True):
    id: int
    channel_ids: tuple[int, ...]
    role_ids: tuple[int, ...]
    emoji: PartialEmoji | None = None
    emoji_id: int | None = None
    emoji_name: str | None = None
    emoji_animated: bool | None = None
    title: str
    description: str | None


class OnboardingPrompt(Model, kw_only=True):
    id: int
    type: OnboardingPromptType
    options: tuple[OnboardingPromptOption, ...]
    title: str
    single_select: bool
    required: bool
    in_onboarding: bool


class GuildOnboarding(Model, kw_only=True):
    """The questions and default channels a guild shows its new members."""

    guild_id: int
    prompts: tuple[OnboardingPrompt, ...]
    default_channel_ids: tuple[int, ...]
    enabled: bool
    mode: OnboardingMode
