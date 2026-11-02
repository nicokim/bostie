from collections.abc import Callable

import pytest

from bostie import StickerFormatType, cdn
from bostie.models import PartialUser

CDN = "https://cdn.discordapp.com"
USER = 80351110224678912
STILL = "8342729096ea3675442027381ff50dfe"
ANIMATED = "a_1269e74af4df7417b13759eae50c83dc"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (cdn.user_avatar(USER, STILL), f"{CDN}/avatars/{USER}/{STILL}.webp"),
        (
            cdn.user_avatar(USER, ANIMATED, size=64),
            f"{CDN}/avatars/{USER}/{ANIMATED}.webp?size=64&animated=true",
        ),
        (cdn.guild_icon(1, ANIMATED, format="gif"), f"{CDN}/icons/1/{ANIMATED}.gif"),
        (
            cdn.member_avatar(1, 2, STILL, format="png"),
            f"{CDN}/guilds/1/users/2/avatars/{STILL}.png",
        ),
        (cdn.emoji(3, animated=True), f"{CDN}/emojis/3.webp?animated=true"),
        (cdn.default_avatar(USER), f"{CDN}/embed/avatars/5.png"),
        (cdn.default_avatar(USER, "1337"), f"{CDN}/embed/avatars/2.png"),
        (
            cdn.sticker_pack_banner(4, size=4096),
            f"{CDN}/app-assets/710982414301790216/store/4.webp?size=4096",
        ),
        (cdn.sticker(5, StickerFormatType.LOTTIE), f"{CDN}/stickers/5.json"),
        (
            cdn.sticker(5, StickerFormatType.GIF),
            "https://media.discordapp.net/stickers/5.gif",
        ),
        (cdn.soundboard_sound(6), f"{CDN}/soundboard-sounds/6"),
        (
            cdn.display_avatar(PartialUser(id=USER << 1, username="hook", avatar=None)),
            f"{CDN}/embed/avatars/4.png",
        ),
    ],
)
def test_urls_follow_the_documented_cdn_endpoints(url: str, expected: str) -> None:
    assert url == expected


@pytest.mark.parametrize(
    "build",
    [
        lambda: cdn.user_avatar(USER, STILL, size=100),
        lambda: cdn.user_avatar(USER, STILL, size=8192),
        lambda: cdn.user_avatar(USER, STILL, format="gif"),
        lambda: cdn.guild_splash(1, ANIMATED, format="gif"),
    ],
    ids=["size 100", "size 8192", "still gif", "splash gif"],
)
def test_sizes_and_formats_the_cdn_refuses_raise(build: Callable[[], str]) -> None:
    with pytest.raises(ValueError, match="must be"):
        build()
