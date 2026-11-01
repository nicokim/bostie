import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import msgspec
import pytest

from bostie.context import (
    CALLBACK_ROUTE,
    FOLLOWUP_ROUTE,
    ORIGINAL_ROUTE,
    AutocompleteContext,
    ComponentContext,
    Context,
    ResponseState,
)
from bostie.enums import MessageFlags
from bostie.errors import (
    HTTPException,
    InteractionExpired,
    InteractionResponded,
    NotFound,
)
from bostie.file import File
from bostie.models import AllowedMentions, Interaction, decode

FIXTURES = Path(__file__).parent / "fixtures" / "models"
CALLBACK_BODY = (FIXTURES / "rest" / "interaction_callback.json").read_bytes()
MESSAGE_BODY = (FIXTURES / "rest" / "message.json").read_bytes()
EPHEMERAL = 64


@dataclass
class Request:
    method: str
    route: str
    json: Any
    files: Sequence[File]
    query: Mapping[str, Any] | None
    params: dict[str, object]


class RecordingHTTP:
    def __init__(self) -> None:
        self.requests: list[Request] = []
        self.gate = asyncio.Event()
        self.gate.set()
        self.callback_body = CALLBACK_BODY
        self.failures: list[HTTPException] = []

    async def _request(
        self,
        method: str,
        route: str,
        /,
        *,
        json: Any = None,
        files: Sequence[File] = (),
        query: Mapping[str, Any] | None = None,
        reason: str | None = None,
        **params: object,
    ) -> bytes:
        self.requests.append(Request(method, route, json, files, query, params))
        await self.gate.wait()
        if self.failures:
            raise self.failures.pop(0)
        if method == "DELETE":
            return b""
        return self.callback_body if route == CALLBACK_ROUTE else MESSAGE_BODY

    @property
    def calls(self) -> list[tuple[str, str, int | None]]:
        return [
            (request.method, request.route, (request.json or {}).get("type"))
            for request in self.requests
        ]


def interaction(name: str) -> Interaction:
    return decode(Interaction, (FIXTURES / "events" / f"{name}.json").read_bytes())


def context[C: Context | AutocompleteContext](
    kind: type[C],
    http: RecordingHTTP,
    *,
    source: Interaction | None = None,
    age: float = 0.0,
    ephemeral: bool = False,
    auto_defer: float | None = None,
    allowed_mentions: AllowedMentions | None = None,
) -> C:
    fixture = {
        Context: "INTERACTION_CREATE.command",
        ComponentContext: "INTERACTION_CREATE.button",
        AutocompleteContext: "INTERACTION_CREATE.autocomplete",
    }[kind]
    return kind(
        source or interaction(fixture),
        None,
        http,
        received=asyncio.get_running_loop().time() - age,
        ephemeral=ephemeral,
        auto_defer=auto_defer,
        allowed_mentions=allowed_mentions,
    )


def run(
    scenario: Callable[[RecordingHTTP], Coroutine[Any, Any, None]],
) -> RecordingHTTP:
    http = RecordingHTTP()
    asyncio.run(scenario(http))
    return http


ENTER: dict[ResponseState, Callable[[ComponentContext], Awaitable[object]]] = {
    ResponseState.PENDING: lambda ctx: asyncio.sleep(0),
    ResponseState.DEFERRED: lambda ctx: ctx.defer(),
    ResponseState.DEFERRED_UPDATE: lambda ctx: ctx.defer_update(),
    ResponseState.RESPONDED: lambda ctx: ctx.respond("first"),
}
CALLS: dict[str, Callable[[ComponentContext], Awaitable[object]]] = {
    "respond": lambda ctx: ctx.respond("hi"),
    "defer": lambda ctx: ctx.defer(),
    "defer_update": lambda ctx: ctx.defer_update(),
    "update": lambda ctx: ctx.update("hi"),
    "edit": lambda ctx: ctx.edit("hi"),
    "followup": lambda ctx: ctx.followup("hi"),
    "delete": lambda ctx: ctx.delete(),
}
PENDING, DEFERRED, DEFERRED_UPDATE, RESPONDED, EXPIRED = ResponseState
CALLBACK_4 = ("POST", CALLBACK_ROUTE, 4)
CALLBACK_5 = ("POST", CALLBACK_ROUTE, 5)
CALLBACK_6 = ("POST", CALLBACK_ROUTE, 6)
CALLBACK_7 = ("POST", CALLBACK_ROUTE, 7)
EDIT = ("PATCH", ORIGINAL_ROUTE, None)
FOLLOWUP = ("POST", FOLLOWUP_ROUTE, None)
DELETE = ("DELETE", ORIGINAL_ROUTE, None)
REFUSED = None
NOTHING = ()

TABLE = [
    (PENDING, "respond", CALLBACK_4, RESPONDED),
    (PENDING, "defer", CALLBACK_5, DEFERRED),
    (PENDING, "defer_update", CALLBACK_6, DEFERRED_UPDATE),
    (PENDING, "update", CALLBACK_7, RESPONDED),
    (PENDING, "edit", REFUSED, PENDING),
    (PENDING, "followup", REFUSED, PENDING),
    (PENDING, "delete", REFUSED, PENDING),
    (DEFERRED, "respond", EDIT, RESPONDED),
    (DEFERRED, "defer", NOTHING, DEFERRED),
    (DEFERRED, "defer_update", NOTHING, DEFERRED),
    (DEFERRED, "update", REFUSED, DEFERRED),
    (DEFERRED, "edit", EDIT, RESPONDED),
    (DEFERRED, "followup", EDIT, RESPONDED),
    (DEFERRED, "delete", DELETE, RESPONDED),
    (DEFERRED_UPDATE, "respond", FOLLOWUP, RESPONDED),
    (DEFERRED_UPDATE, "defer", NOTHING, DEFERRED_UPDATE),
    (DEFERRED_UPDATE, "defer_update", NOTHING, DEFERRED_UPDATE),
    (DEFERRED_UPDATE, "update", EDIT, RESPONDED),
    (DEFERRED_UPDATE, "edit", EDIT, DEFERRED_UPDATE),
    (DEFERRED_UPDATE, "followup", FOLLOWUP, DEFERRED_UPDATE),
    (DEFERRED_UPDATE, "delete", DELETE, DEFERRED_UPDATE),
    (RESPONDED, "respond", FOLLOWUP, RESPONDED),
    (RESPONDED, "defer", REFUSED, RESPONDED),
    (RESPONDED, "defer_update", REFUSED, RESPONDED),
    (RESPONDED, "update", REFUSED, RESPONDED),
    (RESPONDED, "edit", EDIT, RESPONDED),
    (RESPONDED, "followup", FOLLOWUP, RESPONDED),
    (RESPONDED, "delete", DELETE, RESPONDED),
]


@pytest.mark.parametrize(
    ("state", "call", "sent", "after"),
    TABLE,
    ids=[f"{state.value}-{call}" for state, call, _, _ in TABLE],
)
def test_response_state_machine(
    state: ResponseState,
    call: str,
    sent: tuple[str, str, int | None] | tuple[()] | None,
    after: ResponseState,
) -> None:
    async def scenario(http: RecordingHTTP) -> None:
        ctx = context(ComponentContext, http)
        await ENTER[state](ctx)
        http.requests.clear()

        if sent is REFUSED:
            with pytest.raises(InteractionResponded):
                await CALLS[call](ctx)
        else:
            await CALLS[call](ctx)

        assert http.calls == ([sent] if sent else [])
        assert ctx.state is after

    run(scenario)


def test_the_user_is_the_member_in_guilds_the_user_in_dms_and_never_none() -> None:
    dm = interaction("INTERACTION_CREATE.command_dm")
    nobody = msgspec.structs.replace(dm, user=None)

    async def scenario(http: RecordingHTTP) -> None:
        in_guild = context(Context, http).user
        in_dm = context(Context, http, source=dm).user
        assert (in_guild.username, in_dm.username) == ("grace", "grace")
        with pytest.raises(RuntimeError, match="without a user"):
            _ = context(Context, http, source=nobody).user

    run(scenario)


def test_callbacks_target_the_interaction_and_webhooks_the_application() -> None:
    async def scenario(http: RecordingHTTP) -> None:
        ctx = context(Context, http)
        await ctx.respond("hi")
        await ctx.edit("edited")

    callback, edit = run(scenario).requests
    source = interaction("INTERACTION_CREATE.command")
    assert callback.json == {"type": 4, "data": {"content": "hi"}}
    assert callback.query == {"with_response": True}
    assert callback.params == {
        "interaction_id": source.id,
        "interaction_token": source.token,
    }
    assert edit.json == {"content": "edited"}
    assert edit.params == {
        "application_id": source.application_id,
        "interaction_token": source.token,
    }


def test_message_calls_return_the_message_discord_created() -> None:
    async def scenario(http: RecordingHTTP) -> None:
        ctx = context(Context, http)
        answer = await ctx.respond("hi")
        followup = await ctx.followup("more")
        await ctx.delete()

        assert answer is not None
        assert answer.content == "Inspected Voice Lounge 7 times (thorough)."
        assert answer.raw is not None
        assert answer.raw["id"] == "1564609864741814273"
        assert followup is not None
        assert followup.raw_json == MESSAGE_BODY
        in_guild = f"https://discord.com/channels/{ctx.guild_id}/"
        assert answer.jump_url.startswith(in_guild)
        assert followup.jump_url.startswith(in_guild)

    run(scenario)


def test_an_accepted_answer_counts_even_when_its_message_fails_to_decode() -> None:
    async def scenario(http: RecordingHTTP) -> None:
        http.callback_body = b'{"resource": {"message": {"id": "1", "mentions": {}}}}'
        ctx = context(Context, http)
        with pytest.raises(msgspec.ValidationError):
            await ctx.respond("hi")
        await ctx.respond("again")

    assert run(scenario).calls == [CALLBACK_4, FOLLOWUP]


def test_ephemeral_defaults_to_the_command_setting() -> None:
    async def scenario(http: RecordingHTTP) -> None:
        ephemeral = context(Context, http, ephemeral=True)
        await ephemeral.respond("secret")
        await ephemeral.followup("also secret")
        await ephemeral.followup("public", ephemeral=False)
        public = context(Context, http)
        await public.defer()
        await context(Context, http).defer(ephemeral=True)

    answer, followup, public_followup, deferred, ephemeral_defer = run(
        scenario
    ).requests
    assert answer.json["data"]["flags"] == EPHEMERAL
    assert followup.json["flags"] == EPHEMERAL
    assert "flags" not in public_followup.json
    assert deferred.json == {"type": 5}
    assert ephemeral_defer.json == {"type": 5, "data": {"flags": EPHEMERAL}}


def test_an_ephemeral_flag_that_cannot_apply_logs_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(http: RecordingHTTP) -> None:
        ctx = context(Context, http)
        await ctx.defer()
        with caplog.at_level(logging.WARNING, logger="bostie.context"):
            await ctx.respond("hi", ephemeral=True)
            await ctx.edit("again", ephemeral=True)

    _, answer, edit = run(scenario).requests
    assert "flags" not in answer.json
    assert "flags" not in edit.json
    assert len(caplog.records) == 1
    assert "kit inspect all" in caplog.records[0].getMessage()


def test_allowed_mentions_default_to_the_bot_setting() -> None:
    bot_default = AllowedMentions(parse=("users",))
    override = AllowedMentions(parse=())

    async def scenario(http: RecordingHTTP) -> None:
        ctx = context(Context, http, allowed_mentions=bot_default)
        await ctx.respond("hi")
        await ctx.followup("quiet", allowed_mentions=override)

    answer, followup = run(scenario).requests
    assert answer.json["data"]["allowed_mentions"] is bot_default
    assert followup.json["allowed_mentions"] is override


def test_autocomplete_answers_once_with_choices() -> None:
    choices = [{"name": "bostie", "value": "bostie"}]

    async def scenario(http: RecordingHTTP) -> None:
        ctx = context(AutocompleteContext, http)
        await ctx._respond(choices)
        with pytest.raises(InteractionResponded):
            await ctx._respond(choices)

        assert ctx.options == {"limit": 10, "query": "lyre"}
        assert ctx.command_name == "search"

    (request,) = run(scenario).requests
    assert request.json == {"type": 8, "data": {"choices": choices}}


def test_command_name_includes_groups_and_subcommands() -> None:
    async def scenario(http: RecordingHTTP) -> None:
        assert context(Context, http).command_name == "kit inspect all"
        assert context(ComponentContext, http).command_name is None

    run(scenario)


@pytest.mark.parametrize("call", list(CALLS))
def test_an_expired_interaction_sends_nothing(call: str) -> None:
    async def scenario(http: RecordingHTTP) -> None:
        ctx = context(ComponentContext, http, age=15 * 60 + 1)
        with pytest.raises(InteractionExpired):
            await CALLS[call](ctx)

    assert run(scenario).requests == []


def test_auto_defer_fires_at_receipt_plus_the_delay() -> None:
    async def scenario(http: RecordingHTTP) -> None:
        ctx = context(Context, http, ephemeral=True, auto_defer=0.05)
        with ctx.auto_defer():
            await asyncio.sleep(0.01)
            assert http.requests == []
            await asyncio.sleep(0.15)

        assert ctx.state is ResponseState.DEFERRED

    (request,) = run(scenario).requests
    assert request.json == {"type": 5, "data": {"flags": EPHEMERAL}}


@pytest.mark.parametrize(
    ("kind", "sent", "after"),
    [
        (Context, CALLBACK_5, ResponseState.DEFERRED),
        (ComponentContext, CALLBACK_6, ResponseState.DEFERRED_UPDATE),
    ],
)
def test_auto_defer_fires_at_once_when_the_delay_already_passed(
    kind: type[Context], sent: tuple[str, str, int], after: ResponseState
) -> None:
    async def scenario(http: RecordingHTTP) -> None:
        ctx = context(kind, http, age=10, auto_defer=2.5)
        with ctx.auto_defer():
            await asyncio.sleep(0.01)
            assert http.calls == [sent]

        assert ctx.state is after

    run(scenario)


def test_no_auto_defer_after_a_response() -> None:
    async def scenario(http: RecordingHTTP) -> None:
        ctx = context(Context, http, auto_defer=0.05)
        with ctx.auto_defer():
            await ctx.respond("fast")
            await asyncio.sleep(0.15)

    assert run(scenario).calls == [CALLBACK_4]


def test_no_auto_defer_after_the_handler_finished() -> None:
    async def scenario(http: RecordingHTTP) -> None:
        ctx = context(Context, http, auto_defer=0.05)
        with ctx.auto_defer():
            await asyncio.sleep(0)
        await asyncio.sleep(0.15)

        assert ctx.state is ResponseState.PENDING

    assert run(scenario).requests == []


def test_auto_defer_waits_for_a_response_in_flight_and_then_does_nothing() -> None:
    async def scenario(http: RecordingHTTP) -> None:
        ctx = context(Context, http, age=10, auto_defer=2.5)
        http.gate.clear()
        answer = asyncio.create_task(ctx.respond("slow"))
        await asyncio.sleep(0)
        with ctx.auto_defer():
            await asyncio.sleep(0.01)
            http.gate.set()
            await answer
            await asyncio.sleep(0.01)

    assert run(scenario).calls == [CALLBACK_4]


def test_a_response_waits_for_an_auto_defer_in_flight_and_edits_it() -> None:
    async def scenario(http: RecordingHTTP) -> None:
        ctx = context(Context, http, age=10, auto_defer=2.5)
        http.gate.clear()
        with ctx.auto_defer():
            await asyncio.sleep(0.01)
            answer = asyncio.create_task(ctx.respond("after the defer"))
            await asyncio.sleep(0.01)
            http.gate.set()
            await answer

        assert ctx.state is ResponseState.RESPONDED

    assert run(scenario).calls == [CALLBACK_5, EDIT]


def error(status: int, code: int) -> HTTPException:
    kind = NotFound if status == 404 else HTTPException
    return kind(status, code, "dead token", {}, b"")


@pytest.mark.parametrize(
    "dead", [error(404, 10062), error(404, 10015), error(401, 50027)]
)
def test_a_dead_token_expires_the_context_and_nothing_is_sent_again(
    dead: HTTPException,
) -> None:
    async def scenario(http: RecordingHTTP) -> None:
        ctx = context(ComponentContext, http)
        http.failures.append(dead)
        with pytest.raises(HTTPException):
            await ctx.respond("hi")
        assert ctx.state is ResponseState.EXPIRED

        for call in CALLS.values():
            with pytest.raises(InteractionExpired):
                await call(ctx)

    assert run(scenario).calls == [CALLBACK_4]


def test_a_dead_token_after_a_defer_is_never_edited_again() -> None:
    async def scenario(http: RecordingHTTP) -> None:
        ctx = context(Context, http)
        await ctx.defer()
        http.failures.append(error(404, 10015))
        with pytest.raises(NotFound):
            await ctx.respond("hi")
        with pytest.raises(InteractionExpired):
            await ctx.respond("again")

    assert run(scenario).calls == [CALLBACK_5, EDIT]


def test_an_unknown_follow_up_message_keeps_the_context_alive() -> None:
    async def scenario(http: RecordingHTTP) -> None:
        ctx = context(Context, http)
        await ctx.respond("hi")
        http.failures.append(error(404, 10008))
        with pytest.raises(NotFound):
            await ctx.followup("gone")
        await ctx.followup("still works")

        assert ctx.state is ResponseState.RESPONDED

    assert run(scenario).calls == [CALLBACK_4, FOLLOWUP, FOLLOWUP]


def test_a_failed_auto_defer_on_a_dead_token_stops_the_answer() -> None:
    async def scenario(http: RecordingHTTP) -> None:
        ctx = context(Context, http, age=10, auto_defer=2.5)
        http.failures.append(error(404, 10062))
        with ctx.auto_defer():
            await asyncio.sleep(0.01)
            with pytest.raises(InteractionExpired):
                await ctx.respond("late")

    assert run(scenario).calls == [CALLBACK_5]


def test_flags_reach_every_answer_and_ephemeral_adds_its_bit() -> None:
    v2 = MessageFlags.IS_COMPONENTS_V2
    text = {"type": 10, "content": "hi"}

    async def scenario(http: RecordingHTTP) -> None:
        answered = context(ComponentContext, http)
        await answered.respond(components=[text], ephemeral=True)
        await answered.followup(flags=MessageFlags.SUPPRESS_NOTIFICATIONS)
        await answered.edit(components=[text])
        deferred = context(Context, http)
        await deferred.defer(ephemeral=True)
        await deferred.respond(components=[text])
        await context(ComponentContext, http).update(components=[text])

    answer, followup, edit, _, filled, updated = run(scenario).requests
    assert answer.json["data"]["flags"] == v2 | EPHEMERAL
    assert followup.json["flags"] == MessageFlags.SUPPRESS_NOTIFICATIONS
    assert edit.json["flags"] == v2
    cleared = {"components": [text], "flags": v2, "content": None, "embeds": []}
    assert filled.json == cleared
    assert updated.json["data"] == cleared


def test_answers_send_their_files_and_edits_keep_only_the_listed_attachments() -> None:
    files = [File("new.png", b"png")]
    kept = 1564609863064092673

    async def scenario(http: RecordingHTTP) -> None:
        await context(ComponentContext, http).update(attachments=[kept])
        ctx = context(Context, http)
        await ctx.respond("hi", files=files)
        await ctx.followup(files=files)
        await ctx.edit(files=files, attachments=[kept])

    update, answer, followup, edit = run(scenario).requests
    new = {"id": 0, "filename": "new.png"}
    assert [answer.files, followup.files, edit.files] == [files] * 3
    assert answer.json["data"]["attachments"] == [new]
    assert followup.json == {"attachments": [new]}
    assert edit.json["attachments"] == [{"id": kept}, new]
    assert update.json["data"]["attachments"] == [{"id": kept}]
