from typing import NotRequired, TypedDict

from bostie.enums import ActivityType

__all__ = [
    "ActivityDict",
    "competing",
    "custom",
    "listening",
    "playing",
    "streaming",
    "watching",
]

_CUSTOM_STATUS_NAME = "Custom Status"


class ActivityDict(TypedDict):
    name: str
    type: ActivityType
    state: NotRequired[str]
    url: NotRequired[str]


def playing(name: str, *, state: str | None = None) -> ActivityDict:
    return _activity(ActivityType.PLAYING, name, state)


def streaming(name: str, url: str, *, state: str | None = None) -> ActivityDict:
    """A stream on `url`; Discord accepts only Twitch and YouTube URLs."""
    activity = _activity(ActivityType.STREAMING, name, state)
    activity["url"] = url
    return activity


def listening(name: str, *, state: str | None = None) -> ActivityDict:
    return _activity(ActivityType.LISTENING, name, state)


def watching(name: str, *, state: str | None = None) -> ActivityDict:
    return _activity(ActivityType.WATCHING, name, state)


def competing(name: str, *, state: str | None = None) -> ActivityDict:
    return _activity(ActivityType.COMPETING, name, state)


def custom(state: str) -> ActivityDict:
    """A custom status that shows `state` as its text."""
    return _activity(ActivityType.CUSTOM, _CUSTOM_STATUS_NAME, state)


def _activity(type: ActivityType, name: str, state: str | None) -> ActivityDict:
    activity: ActivityDict = {"name": name, "type": type}
    if state is not None:
        activity["state"] = state
    return activity
