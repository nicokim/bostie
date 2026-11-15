from datetime import UTC, datetime, timedelta
from typing import Any

import msgspec
import pytest

from bostie import Permissions
from bostie.models import Channel, Guild, ResolvedMember, Role, RoleColors
from bostie.models.base import Model, decode
from bostie.permissions import (
    ALL,
    can_act_on,
    compute_permissions,
    display_colors,
    top_role,
)
from bostie.testing import payloads

GUILD = 1
OWNER = 2
USER = 3
MODS = 4
MUTED = 5
PARENT = 6
ADMINS = 8
P = Permissions
EVERYONE = P.VIEW_CHANNEL | P.SEND_MESSAGES | P.ATTACH_FILES | P.CONNECT | P.SPEAK


def model[M: Model](cls: type[M], **fields: Any) -> M:
    return decode(cls, msgspec.json.encode(fields))


def role(id: int, permissions: Permissions, position: int = 0, color: int = 0) -> Role:
    colors = {"primary_color": color, "secondary_color": None, "tertiary_color": None}
    return model(
        Role,
        id=id,
        name=str(id),
        colors=colors,
        hoist=False,
        position=position,
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


ROLES = (
    role(GUILD, EVERYONE),
    role(MODS, P.MANAGE_MESSAGES),
    role(MUTED, P(0)),
    role(ADMINS, P.ADMINISTRATOR),
)
GUILD_MODEL = msgspec.structs.replace(
    model(Guild, **payloads.guild(id=GUILD, owner_id=OWNER)), roles=ROLES
)
HIDDEN = overwrite(GUILD, 0, P(0), P.VIEW_CHANNEL)
NO_SENDING = overwrite(GUILD, 0, P(0), P.SEND_MESSAGES)
NO_CONNECTING = overwrite(GUILD, 0, P(0), P.CONNECT)


def member(
    user: int = USER, roles: tuple[int, ...] = (), timed_out: bool = False
) -> ResolvedMember:
    until = datetime.now(UTC) + timedelta(hours=1 if timed_out else -1)
    return model(
        ResolvedMember,
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
        (member(roles=(ADMINS,)), channel(0, HIDDEN), None, ALL),
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
    target: ResolvedMember,
    where: Channel | None,
    parent: Channel | None,
    expected: Permissions,
) -> None:
    assert compute_permissions(GUILD_MODEL, target, where, parent=parent) == (expected)


def test_a_thread_without_its_parent_raises() -> None:
    with pytest.raises(ValueError, match="parent"):
        compute_permissions(GUILD_MODEL, member(), channel(11, parent_id=PARENT))


HIGH = 10
TIE = 11
LOW = 12
RED = 0xFF0000
RANKED = msgspec.structs.replace(
    GUILD_MODEL,
    roles=(
        role(GUILD, EVERYONE),
        role(HIGH, P(0), position=2),
        role(TIE, P(0), position=2, color=0x00FF00),
        role(LOW, P(0), position=1, color=RED),
    ),
)


@pytest.mark.parametrize(
    ("roles", "top", "color"),
    [
        ((), None, None),
        ((LOW,), LOW, RED),
        ((LOW, HIGH), HIGH, RED),
        ((TIE, HIGH), HIGH, 0x00FF00),
    ],
)
def test_the_top_role_and_display_colors_follow_position_then_id(
    roles: tuple[int, ...], top: int | None, color: int | None
) -> None:
    highest = top_role(RANKED, member(roles=roles))
    colors = display_colors(RANKED, member(roles=roles))

    assert (None if highest is None else highest.id) == top
    assert colors == (
        None
        if color is None
        else RoleColors(primary_color=color, secondary_color=None, tertiary_color=None)
    )


@pytest.mark.parametrize(
    ("actor", "target", "expected"),
    [
        (member(OWNER), member(roles=(HIGH,)), True),
        (member(roles=(HIGH,)), member(OWNER), False),
        (member(roles=(HIGH,)), member(roles=(LOW,)), True),
        (member(roles=(LOW,)), member(roles=(HIGH,)), False),
        (member(roles=(HIGH,)), member(roles=(HIGH,)), False),
        (member(roles=(HIGH,)), member(roles=(TIE,)), True),
        (member(roles=(LOW,)), member(), True),
    ],
)
def test_a_member_acts_only_on_members_it_outranks(
    actor: ResolvedMember, target: ResolvedMember, expected: bool
) -> None:
    assert can_act_on(RANKED, actor, target) is expected


@pytest.mark.parametrize(
    ("actor", "target", "expected"),
    [
        (member(OWNER), HIGH, True),
        (member(roles=(HIGH,)), LOW, True),
        (member(roles=(HIGH,)), TIE, True),
        (member(roles=(HIGH,)), HIGH, False),
        (member(roles=(LOW,)), HIGH, False),
        (member(), GUILD, False),
    ],
)
def test_a_member_acts_only_on_roles_below_its_top_role(
    actor: ResolvedMember, target: int, expected: bool
) -> None:
    role = next(role for role in RANKED.roles if role.id == target)
    assert can_act_on(RANKED, actor, role) is expected
