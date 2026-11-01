from bostie.enums import StickerFormatType, StickerType
from bostie.models.base import Model
from bostie.models.user import User


class Emoji(Model, kw_only=True):
    id: int | None
    name: str | None
    roles: tuple[int, ...] = ()
    user: User | None = None
    require_colons: bool | None = None
    managed: bool = False
    animated: bool = False
    available: bool | None = None


class Sticker(Model, kw_only=True):
    id: int
    pack_id: int | None = None
    name: str
    description: str | None
    tags: str
    type: StickerType
    format_type: StickerFormatType
    available: bool | None = None
    guild_id: int | None = None
    user: User | None = None
    sort_value: int | None = None


class SoundboardSound(Model, kw_only=True):
    sound_id: int
    name: str
    volume: float
    emoji_id: int | None
    emoji_name: str | None
    guild_id: int | None = None
    available: bool
    user: User | None = None
