from typing import Any, ClassVar

import pytest

from lyrecord.errors import CommandDefinitionError
from lyrecord.router import Listener, Router


class MessageCreate:
    NAME: ClassVar[str] = "MESSAGE_CREATE"


class MessageUpdate:
    NAME: ClassVar[str] = "MESSAGE_UPDATE"


class RawEvent:
    NAME: ClassVar[str] = "*"


class AppError(Exception):
    pass


class DetailedError(AppError):
    pass


async def on_message(event: MessageCreate) -> None:
    pass


async def on_change(event: MessageCreate | MessageUpdate) -> None:
    pass


async def on_anything(event: RawEvent) -> None:
    pass


async def unannotated(event: object) -> None:
    pass


async def component(ctx: object, **params: object) -> None:
    pass


async def command(ctx: object) -> None:
    pass


async def hook(ctx: object, error: object) -> None:
    pass


async def other_hook(ctx: object, error: object) -> None:
    pass


async def parent_hook(ctx: object, error: object) -> None:
    pass


def test_listeners_come_from_annotations() -> None:
    router = Router()

    assert router.listen(on_message) is on_message
    router.listen(on_change)
    router.listen(on_anything)

    assert router.listeners == {
        "MESSAGE_CREATE": [
            Listener(on_message, MessageCreate),
            Listener(on_change, MessageCreate),
        ],
        "MESSAGE_UPDATE": [Listener(on_change, MessageUpdate)],
        "*": [Listener(on_anything, None)],
    }


def test_explicit_event_classes_and_raw_names() -> None:
    router = Router()

    router.listen(MessageUpdate)(unannotated)
    router.listen_raw("SOUNDBOARD_SOUNDS", "MESSAGE_CREATE")(unannotated)

    assert router.listeners == {
        "MESSAGE_UPDATE": [Listener(unannotated, MessageUpdate)],
        "SOUNDBOARD_SOUNDS": [Listener(unannotated, None)],
        "MESSAGE_CREATE": [Listener(unannotated, None)],
    }


def test_a_listener_needs_an_event_class() -> None:
    with pytest.raises(TypeError, match="is not an event class"):
        Router().listen(unannotated)


def test_component_routes_prefer_exact_templates_then_registration_order() -> None:
    router = Router()
    first = router.on_component("vote:{tag}")(component)
    second = router.on_component("vote:{tag}:{up:int}")(component)
    exact = router.on_component("vote:all")(component)

    assert router.match_component("vote:all") == (exact, {})
    assert router.match_component("vote:a:1") == (first, {"tag": "a:1"})
    assert second.match("vote:a:1") == {"tag": "a", "up": 1}
    assert router.match_component("poll:a") is None
    with pytest.raises(ValueError, match="already registered"):
        router.on_component("vote:{tag}")(component)


def test_include_merges_and_keeps_following_the_child() -> None:
    bot = Router()
    music = Router()
    play = music.command(command)
    music.listen(on_message)
    route = music.on_component("music:{track}")(component)

    bot.include(music)
    later = music.command(name="later")(command)

    assert bot.commands == {"command": play, "later": later}
    assert bot.listeners == {"MESSAGE_CREATE": [Listener(on_message, MessageCreate)]}
    assert bot.match_component("music:a") == (route, {"track": "a"})
    assert play.router is music


def test_command_names_are_unique_across_included_routers() -> None:
    bot = Router()
    bot.group("command", "A group")
    music = Router()
    music.command(command)

    with pytest.raises(CommandDefinitionError, match="'command' is already registered"):
        bot.include(music)
    with pytest.raises(CommandDefinitionError, match="already registered"):
        bot.command(command)


def test_a_clashing_include_changes_nothing_and_can_be_retried() -> None:
    bot = Router()
    ping = bot.command(name="ping")(command)
    bot.on_component("taken:{a}")(component)
    plugin = Router()
    echo = plugin.command(name="echo")(command)
    plugin.command(name="ping")(command)
    plugin.listen(on_message)
    clashing_route = Router()
    clashing_route.on_component("taken:{a}")(component)

    with pytest.raises(CommandDefinitionError, match="'ping' is already registered"):
        bot.include(plugin)
    with pytest.raises(ValueError, match="component template 'taken:"):
        bot.include(clashing_route)

    assert bot.commands == {"ping": ping}
    assert bot.listeners == {}
    assert plugin.parent is None and clashing_route.parent is None
    del plugin.commands["ping"]
    bot.include(plugin)
    assert bot.commands == {"ping": ping, "echo": echo}
    assert bot.listeners == {"MESSAGE_CREATE": [Listener(on_message, MessageCreate)]}


def test_error_hooks_register_bare_or_for_types_and_all_or_nothing() -> None:
    router = Router()

    assert router.on_error(hook) is hook
    router.on_error(AppError)(other_hook)
    with pytest.raises(ValueError, match="AppError already exists"):
        router.on_error(KeyError, AppError)(parent_hook)
    not_an_exception: Any = hook
    with pytest.raises(TypeError, match="is not an exception class"):
        router.on_error(AppError, not_an_exception)

    assert router.error_handlers == {Exception: hook, AppError: other_hook}


def test_error_hooks_pick_the_nearest_base_then_walk_up() -> None:
    bot = Router()
    music = Router()
    bot.include(music)
    bot.on_error(Exception)(parent_hook)
    music.on_error(Exception)(other_hook)
    music.on_error(AppError)(hook)

    assert list(music.find_error_handlers(DetailedError())) == [hook, parent_hook]
    assert list(music.find_error_handlers(KeyError())) == [other_hook, parent_hook]
    assert list(bot.find_error_handlers(DetailedError())) == [parent_hook]
    with pytest.raises(ValueError, match="AppError already exists"):
        music.on_error(AppError)(hook)


def test_walk_commands_yields_leaves_in_order() -> None:
    router = Router()
    first = router.command(name="first")(command)
    role = router.group("role", "Roles")
    color = role.group("color", "Colors")
    color_set = color.command(name="set")(command)
    remove = role.command(name="remove")(command)

    assert list(router.walk_commands()) == [first, color_set, remove]
