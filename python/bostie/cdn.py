from typing import Literal

from bostie.enums import StickerFormatType
from bostie.models import PartialUser, User

BASE = "https://cdn.discordapp.com"
MEDIA = "https://media.discordapp.net"
SIZES = frozenset(1 << power for power in range(4, 13))
STICKER_PACK_APPLICATION = 710982414301790216

type ImageFormat = Literal["png", "jpg", "jpeg", "webp", "gif"]

STILL: tuple[ImageFormat, ...] = ("png", "jpg", "jpeg", "webp")
ANIMATED: tuple[ImageFormat, ...] = (*STILL, "gif")


def emoji(
    emoji_id: int,
    *,
    animated: bool = False,
    size: int | None = None,
    format: ImageFormat = "webp",
) -> str:
    return _image(f"emojis/{emoji_id}", ANIMATED, size, format, animated=animated)


def guild_icon(
    guild_id: int, icon: str, *, size: int | None = None, format: ImageFormat = "webp"
) -> str:
    return _hashed(f"icons/{guild_id}", icon, ANIMATED, size, format)


def guild_splash(
    guild_id: int, splash: str, *, size: int | None = None, format: ImageFormat = "webp"
) -> str:
    return _hashed(f"splashes/{guild_id}", splash, STILL, size, format)


def guild_discovery_splash(
    guild_id: int, splash: str, *, size: int | None = None, format: ImageFormat = "webp"
) -> str:
    return _hashed(f"discovery-splashes/{guild_id}", splash, STILL, size, format)


def guild_banner(
    guild_id: int, banner: str, *, size: int | None = None, format: ImageFormat = "webp"
) -> str:
    return _hashed(f"banners/{guild_id}", banner, ANIMATED, size, format)


def user_banner(
    user_id: int, banner: str, *, size: int | None = None, format: ImageFormat = "webp"
) -> str:
    return _hashed(f"banners/{user_id}", banner, ANIMATED, size, format)


def default_avatar(user_id: int, discriminator: str = "0") -> str:
    """The avatar of a user who has not set one; bots pass their discriminator."""
    legacy = int(discriminator or 0)
    index = legacy % 5 if legacy else (user_id >> 22) % 6
    return f"{BASE}/embed/avatars/{index}.png"


def user_avatar(
    user_id: int, avatar: str, *, size: int | None = None, format: ImageFormat = "webp"
) -> str:
    return _hashed(f"avatars/{user_id}", avatar, ANIMATED, size, format)


def display_avatar(
    user: PartialUser, *, size: int | None = None, format: ImageFormat = "webp"
) -> str:
    """The user's avatar, or the default one when they have not set any."""
    if user.avatar is not None:
        return user_avatar(user.id, user.avatar, size=size, format=format)
    return default_avatar(
        user.id, user.discriminator if isinstance(user, User) else "0"
    )


def member_avatar(
    guild_id: int,
    user_id: int,
    avatar: str,
    *,
    size: int | None = None,
    format: ImageFormat = "webp",
) -> str:
    path = f"guilds/{guild_id}/users/{user_id}/avatars"
    return _hashed(path, avatar, ANIMATED, size, format)


def member_banner(
    guild_id: int,
    user_id: int,
    banner: str,
    *,
    size: int | None = None,
    format: ImageFormat = "webp",
) -> str:
    path = f"guilds/{guild_id}/users/{user_id}/banners"
    return _hashed(path, banner, ANIMATED, size, format)


def avatar_decoration(asset: str, *, size: int | None = None) -> str:
    return _image(f"avatar-decoration-presets/{asset}", ("png",), size, "png")


def application_icon(
    application_id: int,
    icon: str,
    *,
    size: int | None = None,
    format: ImageFormat = "webp",
) -> str:
    return _hashed(f"app-icons/{application_id}", icon, STILL, size, format)


def application_cover(
    application_id: int,
    cover_image: str,
    *,
    size: int | None = None,
    format: ImageFormat = "webp",
) -> str:
    return _hashed(f"app-icons/{application_id}", cover_image, STILL, size, format)


def application_asset(
    application_id: int,
    asset_id: str,
    *,
    size: int | None = None,
    format: ImageFormat = "webp",
) -> str:
    return _hashed(f"app-assets/{application_id}", asset_id, STILL, size, format)


def store_page_asset(
    application_id: int,
    asset_id: int,
    *,
    size: int | None = None,
    format: ImageFormat = "webp",
) -> str:
    path = f"app-assets/{application_id}/store"
    return _hashed(path, str(asset_id), STILL, size, format)


def sticker_pack_banner(
    banner_asset_id: int, *, size: int | None = None, format: ImageFormat = "webp"
) -> str:
    return store_page_asset(
        STICKER_PACK_APPLICATION, banner_asset_id, size=size, format=format
    )


def team_icon(
    team_id: int, icon: str, *, size: int | None = None, format: ImageFormat = "webp"
) -> str:
    return _hashed(f"team-icons/{team_id}", icon, STILL, size, format)


def sticker(sticker_id: int, format_type: StickerFormatType) -> str:
    """A PNG, Lottie JSON or GIF, as the sticker's format type says."""
    if format_type == StickerFormatType.GIF:
        return f"{MEDIA}/stickers/{sticker_id}.gif"
    extension = "json" if format_type == StickerFormatType.LOTTIE else "png"
    return f"{BASE}/stickers/{sticker_id}.{extension}"


def role_icon(
    role_id: int, icon: str, *, size: int | None = None, format: ImageFormat = "webp"
) -> str:
    return _hashed(f"role-icons/{role_id}", icon, STILL, size, format)


def scheduled_event_cover(
    event_id: int, image: str, *, size: int | None = None, format: ImageFormat = "webp"
) -> str:
    return _hashed(f"guild-events/{event_id}", image, STILL, size, format)


def guild_tag_badge(
    guild_id: int, badge: str, *, size: int | None = None, format: ImageFormat = "webp"
) -> str:
    return _hashed(f"guild-tag-badges/{guild_id}", badge, STILL, size, format)


def soundboard_sound(sound_id: int) -> str:
    return f"{BASE}/soundboard-sounds/{sound_id}"


def _hashed(
    path: str,
    image: str,
    formats: tuple[ImageFormat, ...],
    size: int | None,
    format: ImageFormat,
) -> str:
    animated = image.startswith("a_") and "gif" in formats
    return _image(f"{path}/{image}", formats, size, format, animated=animated)


def _image(
    path: str,
    formats: tuple[ImageFormat, ...],
    size: int | None,
    format: ImageFormat,
    *,
    animated: bool = False,
) -> str:
    if format not in formats or (format == "gif" and not animated):
        allowed = [kind for kind in formats if kind != "gif" or animated]
        raise ValueError(f"format must be one of {', '.join(allowed)}, got {format!r}")
    if size is not None and size not in SIZES:
        raise ValueError(f"size must be a power of two from 16 to 4096, got {size}")
    query = [f"size={size}"] if size is not None else []
    if animated and format == "webp":
        query.append("animated=true")
    return f"{BASE}/{path}.{format}" + (f"?{'&'.join(query)}" if query else "")
