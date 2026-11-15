import colorsys
import re

BLURPLE = 0x5865F2
GREEN = 0x57F287
YELLOW = 0xFEE75C
FUCHSIA = 0xEB459E
RED = 0xED4245

_HEX = re.compile(r"(?:#|0x)?([0-9a-fA-F]{6})")
_CHANNEL_MAX = 255


def rgb(red: int, green: int, blue: int) -> int:
    """A color from red, green and blue, each from 0 to 255."""
    if not all(0 <= channel <= _CHANNEL_MAX for channel in (red, green, blue)):
        raise ValueError(
            f"red, green and blue must be from 0 to 255, got {(red, green, blue)}"
        )
    return red << 16 | green << 8 | blue


def hsv(hue: float, saturation: float, value: float) -> int:
    """A color from hue, saturation and value, each from 0 to 1."""
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
    return rgb(round(red * 255), round(green * 255), round(blue * 255))


def parse(text: str) -> int:
    """A color from hex such as "#5865F2" or "0x5865F2"."""
    match = _HEX.fullmatch(text.strip())
    if match is None:
        raise ValueError(f"expected six hex digits such as #5865F2, got {text!r}")
    return int(match.group(1), 16)
