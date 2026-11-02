from datetime import UTC, datetime, timedelta
from typing import Any

import msgspec
import pytest
from fakediscord import payloads

from bostie import Permissions
from bostie.models import Channel, Guild, Member, Role
from bostie.models.base import Model, decode
from bostie.permissions import ALL, compute_permissions

GUILD = 1
OWNER = 2
USER = 3
MODS = 4
MUTED = 5
PARENT = 6
P = Permissions
EVERYONE = P.VIEW_CHANNEL | P.SEND_MESSAGES | P.ATTACH_FILES | P.CONNECT | P.SPEAK


def model[M: Model](cls: type[M], **fields: Any) -> M:
    return decode(cls, msgspec.json.encode(fields))


def role(id: int, permissions: Permissions) -> Role:
    colors = {"primary_color": 0, "secondary_color": None, "tertiary_color": None}
    return model(
        Role,
        id=id,
        name=str(id),
        colors=colors,
        hoist=False,
        position=0,
        permissions=str(int(permissions)),
        managed=False,
        mentionable=False,
        flags=0,
    )


def overwrite(id: int, type: int, allow: Permissions, deny: Permissions) -> Any:
    return {"id": id, "type": type, "allow": str(int(allow)), "deny": str(int(deny))}


def channel(type: int = 0, *overwrites: Any, parent_id: int | None = None) -> Channel:
    return model(
        Channel,
        id=PARENT if parent_id is None else 7,
        type=type,
        guild_id=GUILD,
        parent_id=parent_id,
        permission_overwrites=list(overwrites),
    )


GUILD_MODEL = model(Guild, **payloads.guild(id=GUILD, owner_id=OWNER))
ROLES = [
    role(GUILD, EVERYONE),
    role(MODS, P.MANAGE_MESSAGES),
    role(MUTED, P(0)),
]
HIDDEN = overwrite(GUILD, 0, P(0), P.VIEW_CHANNEL)
NO_SENDING = overwrite(GUILD, 0, P(0), P.SEND_MESSAGES)
NO_CONNECTING = overwrite(GUILD, 0, P(0), P.CONNECT)


def member(
    user: int = USER, roles: tuple[int, ...] = (), timed_out: bool = False
) -> Member:
    until = datetime.now(UTC) + timedelta(hours=1 if timed_out else -1)
    return model(
        Member,
        user=payloads.user(id=str(user)),
        roles=list(roles),
        joined_at=None,
        flags=0,
        communication_disabled_until=until.isoformat(),
    )


@pytest.mark.parametrize(
    ("target", "where", "parent", "expected"),
    [
        (member(OWNER), channel(0, HIDDEN), None, ALL),
        (member(), None, None, EVERYONE),
        (member(roles=(MODS,)), None, None, EVERYONE | P.MANAGE_MESSAGES),
        (member(roles=(MODS,), timed_out=True), None, None, P.VIEW_CHANNEL),
        (member(), channel(0, HIDDEN), None, P(0)),
        (
            member(roles=(MODS, MUTED)),
            channel(
                0,
                HIDDEN,
                overwrite(MUTED, 0, P(0), P.VIEW_CHANNEL | P.SEND_MESSAGES),
                overwrite(MODS, 0, P.VIEW_CHANNEL, P(0)),
                overwrite(USER, 1, P.SEND_MESSAGES, P.MANAGE_MESSAGES),
            ),
            None,
            EVERYONE,
        ),
        (member(), channel(0, NO_SENDING), None, P.VIEW_CHANNEL | P.CONNECT | P.SPEAK),
        (member(), channel(2, NO_CONNECTING), None, EVERYONE & ~(P.CONNECT | P.SPEAK)),
        (
            member(),
            channel(11, parent_id=PARENT),
            channel(0, NO_SENDING),
            P.VIEW_CHANNEL | P.CONNECT | P.SPEAK,
        ),
        (
            member(),
            channel(11, parent_id=PARENT),
            channel(
                0, overwrite(GUILD, 0, P.SEND_MESSAGES_IN_THREADS, P.SEND_MESSAGES)
            ),
            P.VIEW_CHANNEL
            | P.ATTACH_FILES
            | P.CONNECT
            | P.SPEAK
            | P.SEND_MESSAGES_IN_THREADS,
        ),
    ],
)
def test_permissions_follow_discords_order_and_implicit_rules(
    target: Member,
    where: Channel | None,
    parent: Channel | None,
    expected: Permissions,
) -> None:
    assert compute_permissions(GUILD_MODEL, ROLES, target, where, parent=parent) == (
        expected
    )


def test_a_thread_without_its_parent_raises() -> None:
    with pytest.raises(ValueError, match="parent"):
        compute_permissions(GUILD_MODEL, ROLES, member(), channel(11, parent_id=PARENT))
