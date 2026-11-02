from datetime import UTC, datetime

import pytest

from bostie import fmt

ID = 80351110224678912


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            fmt.timestamp(datetime(2021, 4, 20, 16, 20, 30, tzinfo=UTC)),
            "<t:1618935630>",
        ),
        (fmt.timestamp(1618953630.9, "R"), "<t:1618953630:R>"),
        (fmt.user(ID), f"<@{ID}>"),
        (fmt.channel(ID), f"<#{ID}>"),
        (fmt.role(ID), f"<@&{ID}>"),
        (fmt.game_profile(ID), f"<@${ID}>"),
        (fmt.command("foo group bar", ID), f"</foo group bar:{ID}>"),
        (fmt.emoji("b1nzy", ID, animated=True), f"<a:b1nzy:{ID}>"),
        (fmt.guild_navigation("browse"), "<id:browse>"),
        (fmt.guild_navigation("linked-roles", ID), f"<id:linked-roles:{ID}>"),
    ],
)
def test_formats_follow_the_documented_message_formats(
    text: str, expected: str
) -> None:
    assert text == expected


def test_only_linked_roles_navigation_takes_a_role() -> None:
    with pytest.raises(ValueError, match="linked-roles"):
        fmt.guild_navigation("guide", ID)


def test_markdown_is_escaped_or_removed_outside_links() -> None:
    text = "# **hi** _you_ see https://a.io/x_y_z\n1. `code` > ~~gone~~ ||s||"

    assert fmt.escape_markdown(text) == (
        "\\# \\*\\*hi\\*\\* \\_you\\_ see https://a.io/x_y_z\n"
        "1\\. \\`code\\` \\> \\~\\~gone\\~\\~ \\|\\|s\\|\\|"
    )
    assert fmt.remove_markdown(text) == (
        "hi you see https://a.io/x_y_z\n1. code > gone s"
    )


def test_mentions_are_defused_or_read_back_as_ids() -> None:
    text = f"@everyone <@{ID}> <@!{ID + 1}> <@&{ID}> <#{ID}> @here"

    assert fmt.escape_mentions(text) == text.replace("@", "@\u200b")
    assert fmt.mentioned_users(text) == [ID, ID + 1]
    assert fmt.mentioned_roles(text) == [ID]
    assert fmt.mentioned_channels(text) == [ID]
