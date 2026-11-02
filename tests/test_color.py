import pytest

from bostie import color


def test_a_color_comes_from_channels_hsv_or_hex() -> None:
    assert color.rgb(88, 101, 242) == color.BLURPLE
    assert color.hsv(0, 1, 1) == 0xFF0000
    assert color.parse("#5865F2") == color.parse(" 0x5865f2") == color.BLURPLE


@pytest.mark.parametrize("text", ["#5865F", "blurple", "#5865F2FF"])
def test_text_that_is_not_six_hex_digits_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="six hex digits"):
        color.parse(text)


def test_a_channel_outside_0_to_255_is_refused() -> None:
    with pytest.raises(ValueError, match="from 0 to 255"):
        color.rgb(256, 0, 0)
