import asyncio
import itertools
import logging
from collections.abc import Awaitable, Callable, Coroutine
from functools import partial
from typing import Annotated, Any

import pytest
from fakediscord import FakeDiscord, Recorded, payloads, run, running, settle

from bostie import (
    AllowedMentions,
    AutocompleteContext,
    Bot,
    ComponentContext,
    Context,
    File,
    HTTPException,
    InteractionExpired,
    InteractionNotAnswered,
    InteractionResponded,
    MessageFlags,
    NotFound,
    Option,
    UnexpectedResponse,
    WebhookMessage,
)
from bostie.context import ResponseState

EPHEMERAL = 64
MENTIONS = {"parse": ["users", "roles"]}
CALLBACK = "/interactions/"
WEBHOOK = "/webhooks/"

type Call = tuple[str, str, int | None]

_names = itertools.count()


class Interactions:
    def __init__(self, fake: FakeDiscord, bot: Bot) -> None:
        self.fake = fake
        self.bot = bot

    async def answer[C: Context | ComponentContext](
        self,
        kind: type[C],
        handler: Callable[[C], Awaitable[None]],
        *,
        ephemeral: bool = False,
        **fields: Any,
    ) -> list[Recorded]:
        """Run `handler` as the handler of a new interaction; return its requests."""
        name = f"handler{next(_names)}"
        done: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        async def handle(ctx: C) -> None:
            try:
                await handler(ctx)
            except Exception as error:
                done.set_exception(error)
            else:
                done.set_result(None)

        if issubclass(kind, ComponentContext):
            self.bot.on_component(name)(handle)
            payload = payloads.interaction(3, payloads.button(name), **fields)
        else:
            self.bot.command(name=name, ephemeral=ephemeral)(handle)
            payload = payloads.interaction(2, payloads.command(name), **fields)
        start = len(self.fake.requests)
        await self.fake.dispatch("INTERACTION_CREATE", payload)
        await done
        return self.fake.requests[start:]


def run_bot(
    scenario: Callable[[Interactions], Coroutine[Any, Any, None]], **options: Any
) -> None:
    async def main(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url, **({"auto_defer": None} | options))
        async with running(bot, fake):
            await scenario(Interactions(fake, bot))

    run(main)


def calls(requests: list[Recorded]) -> list[Call]:
    return [
        (
            request.method,
            "callback"
            if request.path.endswith("/callback")
            else "original"
            if request.path.endswith("/messages/@original")
            else "followup",
            (request.json or {}).get("type"),
        )
        for request in requests
    ]


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
CALLBACK_4 = ("POST", "callback", 4)
CALLBACK_5 = ("POST", "callback", 5)
CALLBACK_6 = ("POST", "callback", 6)
CALLBACK_7 = ("POST", "callback", 7)
EDIT = ("PATCH", "original", None)
FOLLOWUP = ("POST", "followup", None)
DELETE = ("DELETE", "original", None)
REFUSED = InteractionResponded
UNANSWERED = InteractionNotAnswered
NOTHING = ()

TABLE = [
    (PENDING, "respond", CALLBACK_4, RESPONDED),
    (PENDING, "defer", CALLBACK_5, DEFERRED),
    (PENDING, "defer_update", CALLBACK_6, DEFERRED_UPDATE),
    (PENDING, "update", CALLBACK_7, RESPONDED),
    (PENDING, "edit", UNANSWERED, PENDING),
    (PENDING, "followup", UNANSWERED, PENDING),
    (PENDING, "delete", UNANSWERED, PENDING),
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
    sent: Call | tuple[()] | type[Exception],
    after: ResponseState,
) -> None:
    async def scenario(on: Interactions) -> None:
        async def handler(ctx: ComponentContext) -> None:
            await ENTER[state](ctx)
            start = len(on.fake.requests)
            if isinstance(sent, type):
                with pytest.raises(sent):
                    await CALLS[call](ctx)
                assert on.fake.requests[start:] == []
            else:
                await CALLS[call](ctx)
                assert calls(on.fake.requests[start:]) == ([sent] if sent else [])
            assert ctx.state is after

        await on.answer(ComponentContext, handler)

    run_bot(scenario)


def test_callbacks_target_the_interaction_and_webhooks_the_application() -> None:
    async def scenario(on: Interactions) -> None:
        async def handler(ctx: Context) -> None:
            await ctx.respond("hi")
            await ctx.edit("edited")

        callback, edit = await on.answer(Context, handler)
        assert callback.json == {
            "type": 4,
            "data": {"content": "hi", "allowed_mentions": MENTIONS},
        }
        assert callback.query == {"with_response": "true"}
        assert callback.path.startswith(CALLBACK)
        assert edit.json == {"content": "edited", "allowed_mentions": MENTIONS}
        assert edit.path.startswith(f"{WEBHOOK}{on.fake.application_id}/")

    run_bot(scenario)


def test_message_calls_return_the_message_discord_created() -> None:
    async def scenario(on: Interactions) -> None:
        async def handler(ctx: Context) -> None:
            answer = await ctx.respond("hi")
            followup = await ctx.followup("more")
            edited = await ctx.edit("edited")
            await ctx.delete()

            assert {type(answer), type(followup), type(edited)} == {WebhookMessage}
            assert (answer.content, followup.content) == ("hi", "more")
            assert answer.raw is not None and followup.raw is not None
            assert answer.raw["id"] != followup.raw["id"]
            in_guild = f"https://discord.com/channels/{ctx.guild_id}/"
            assert answer.jump_url.startswith(in_guild)
            assert followup.jump_url.startswith(in_guild)

        await on.answer(Context, handler)

    run_bot(scenario)


def test_an_answer_that_fails_to_decode_raises_but_counts_as_sent() -> None:
    async def scenario(on: Interactions) -> None:
        async def handler(ctx: Context) -> None:
            with pytest.raises(UnexpectedResponse, match="took effect") as raised:
                await ctx.respond(embeds=[{"fields": {}}])
            assert raised.value.raw["resource"]["message"]["embeds"] == [{"fields": {}}]
            await ctx.respond("again")

        requests = await on.answer(Context, handler)
        assert calls(requests) == [CALLBACK_4, FOLLOWUP]

    run_bot(scenario)


@pytest.mark.parametrize(
    ("call", "sent"), [("update", CALLBACK_7), ("defer_update", CALLBACK_6)]
)
def test_a_component_on_a_message_that_fails_to_decode_still_answers(
    call: str, sent: Call
) -> None:
    broken = payloads.fixture("events/INTERACTION_CREATE.button")["message"]
    del broken["content"]

    async def scenario(on: Interactions) -> None:
        async def handler(ctx: ComponentContext) -> None:
            await CALLS[call](ctx)

        requests = await on.answer(ComponentContext, handler, message=broken)
        assert calls(requests) == [sent]

    run_bot(scenario)


ENTER_EPHEMERAL: dict[str, Callable[[Any], Awaitable[object]]] = {
    "pending": lambda ctx: asyncio.sleep(0),
    "deferred": lambda ctx: ctx.defer(),
    "deferred_ephemeral": lambda ctx: ctx.defer(ephemeral=True),
    "deferred_update": lambda ctx: ctx.defer_update(),
    "responded": lambda ctx: ctx.respond("first"),
}
CALL_EPHEMERAL: dict[str, Callable[[Any, dict[str, bool]], Awaitable[object]]] = {
    "respond": lambda ctx, given: ctx.respond("hi", **given),
    "defer": lambda ctx, given: ctx.defer(**given),
    "update": lambda ctx, given: ctx.update("hi", **given),
    "edit": lambda ctx, given: ctx.edit("hi", **given),
    "followup": lambda ctx, given: ctx.followup("hi", **given),
}


def flagged(request: Recorded) -> bool:
    body = request.json
    if request.path.endswith("/callback"):
        body = body.get("data") or {}
    return bool(body.get("flags", 0) & EPHEMERAL)


@pytest.mark.parametrize(
    (
        "kind",
        "command",
        "message",
        "enter",
        "call",
        "ephemeral",
        "sent",
        "ephemeral_sent",
        "warned",
    ),
    [
        (Context, True, 0, "pending", "respond", None, CALLBACK_4, True, False),
        (Context, True, 0, "pending", "respond", False, CALLBACK_4, False, False),
        (Context, True, 0, "pending", "defer", None, CALLBACK_5, True, False),
        (Context, False, 0, "pending", "defer", None, CALLBACK_5, False, False),
        (Context, False, 0, "pending", "defer", True, CALLBACK_5, True, False),
        (Context, True, 0, "responded", "followup", None, FOLLOWUP, True, False),
        (Context, True, 0, "responded", "followup", False, FOLLOWUP, False, False),
        (Context, False, 0, "deferred_ephemeral", "respond", True, EDIT, False, False),
        (
            ComponentContext,
            False,
            64,
            "pending",
            "update",
            True,
            CALLBACK_7,
            False,
            False,
        ),
        (
            ComponentContext,
            False,
            0,
            "pending",
            "update",
            True,
            CALLBACK_7,
            False,
            True,
        ),
        (
            ComponentContext,
            False,
            64,
            "deferred_update",
            "edit",
            False,
            EDIT,
            False,
            True,
        ),
        (
            ComponentContext,
            False,
            0,
            "deferred_update",
            "respond",
            True,
            FOLLOWUP,
            True,
            False,
        ),
    ],
)
def test_ephemeral_reaches_new_messages_and_warns_on_edits_that_differ(
    kind: type[Context | ComponentContext],
    command: bool,
    message: int,
    enter: str,
    call: str,
    ephemeral: bool | None,
    sent: Call,
    ephemeral_sent: bool,
    warned: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clicked = payloads.fixture("events/INTERACTION_CREATE.button")["message"]

    async def scenario(on: Interactions) -> None:
        async def handler(ctx: Any) -> None:
            await ENTER_EPHEMERAL[enter](ctx)
            start = len(on.fake.requests)
            given = {} if ephemeral is None else {"ephemeral": ephemeral}
            await CALL_EPHEMERAL[call](ctx, given)
            (request,) = on.fake.requests[start:]
            assert calls([request]) == [sent]
            assert flagged(request) is ephemeral_sent

        await on.answer(
            kind, handler, ephemeral=command, message=clicked | {"flags": message}
        )

    with caplog.at_level(logging.WARNING, logger="bostie.context"):
        run_bot(scenario)

    assert any("is ignored because" in r.getMessage() for r in caplog.records) is warned


def test_an_ephemeral_flag_that_cannot_apply_logs_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(on: Interactions) -> None:
        async def handler(ctx: Context) -> None:
            await ctx.defer()
            with caplog.at_level(logging.WARNING, logger="bostie.context"):
                await ctx.respond("hi", ephemeral=True)
                await ctx.edit("again", ephemeral=True)

        _, answer, edit = await on.answer(Context, handler)
        assert "flags" not in answer.json
        assert "flags" not in edit.json

    run_bot(scenario)

    warnings = [record for record in caplog.records if record.name == "bostie.context"]
    assert len(warnings) == 1
    assert "is ignored because the message being edited is public" in (
        warnings[0].getMessage()
    )


def test_allowed_mentions_default_to_the_bot_setting() -> None:
    async def scenario(on: Interactions) -> None:
        async def handler(ctx: Context) -> None:
            await ctx.respond("hi")
            await ctx.followup("quiet", allowed_mentions=AllowedMentions(parse=()))

        answer, followup = await on.answer(Context, handler)
        assert answer.json["data"]["allowed_mentions"] == {"parse": ["users"]}
        assert followup.json["allowed_mentions"] == {"parse": []}

    run_bot(scenario, allowed_mentions=AllowedMentions(parse=("users",)))


def test_autocomplete_sees_the_options_filled_so_far_and_the_command_name() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        seen: list[tuple[object, str | None]] = []

        async def suggest(ctx: AutocompleteContext, value: str) -> list[str]:
            seen.append((ctx.options, ctx.command_name))
            return [value]

        @bot.command
        async def search(
            ctx: Context,
            query: Annotated[str, Option(autocomplete=suggest)],
            limit: int = 5,
        ) -> None:
            pass

        data = payloads.command(
            "search",
            payloads.option("limit", 4, 10),
            payloads.option("query", 3, "lyre", focused=True),
        )
        async with running(bot, fake):
            await fake.interaction(4, data)

        assert seen == [({"limit": 10, "query": "lyre"}, "search")]

    run(scenario)


def test_command_name_includes_groups_and_subcommands() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        names: list[str] = []
        inspect = bot.group("kit", "Kit").group("inspect", "Inspect")

        @inspect.command(name="all")
        async def inspect_all(ctx: Context) -> None:
            names.append(ctx.command_name)
            await ctx.respond("ok")

        subcommand = payloads.group("inspect", payloads.subcommand("all"))
        async with running(bot, fake):
            await fake.interaction(2, payloads.command("kit", subcommand))

        assert names == ["kit inspect all"]

    run(scenario)


@pytest.mark.parametrize("call", list(CALLS))
def test_an_expired_interaction_sends_nothing(
    call: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bostie.context.TOKEN_LIFETIME", -1)

    async def scenario(on: Interactions) -> None:
        async def handler(ctx: ComponentContext) -> None:
            with pytest.raises(InteractionExpired):
                await CALLS[call](ctx)

        assert await on.answer(ComponentContext, handler) == []

    run_bot(scenario)


async def wait(ctx: Context | ComponentContext) -> None:
    await settle()


async def wait_then_update(ctx: ComponentContext) -> None:
    await settle()
    await ctx.update("hi")


async def answer_at_once(ctx: Context) -> None:
    await ctx.respond("hi")


async def wait_then_answer(ctx: Context) -> None:
    await settle()
    await ctx.respond("hi")


async def leave(ctx: Context) -> None:
    pass


DEFERRED_EPHEMERALLY = (*CALLBACK_5, EPHEMERAL)
ANSWERED_EPHEMERALLY = (*CALLBACK_4, EPHEMERAL)
FOLLOWED_UP_EPHEMERALLY = (*FOLLOWUP, EPHEMERAL)


@pytest.mark.parametrize(
    ("kind", "ephemeral", "delay", "handler", "sent"),
    [
        (Context, False, 0.0, wait, [CALLBACK_5, EDIT]),
        (Context, True, 0.0, wait, [DEFERRED_EPHEMERALLY, EDIT]),
        (ComponentContext, False, 0.0, wait_then_update, [CALLBACK_6, EDIT, FOLLOWUP]),
        (Context, False, 0.0, answer_at_once, [CALLBACK_4, FOLLOWUP]),
        (Context, False, 0.0, leave, [CALLBACK_4]),
        (
            Context,
            True,
            60.0,
            wait_then_answer,
            [ANSWERED_EPHEMERALLY, FOLLOWED_UP_EPHEMERALLY],
        ),
    ],
)
def test_auto_defer_fires_once_due_unless_the_handler_answered_or_left(
    kind: type[Context | ComponentContext],
    ephemeral: bool,
    delay: float,
    handler: Callable[[Any], Awaitable[None]],
    sent: list[tuple[object, ...]],
) -> None:
    async def scenario(on: Interactions) -> None:
        contexts: list[Context | ComponentContext] = []

        async def handle(ctx: Any) -> None:
            contexts.append(ctx)
            await handler(ctx)

        start = len(on.fake.requests)
        await on.answer(kind, handle, ephemeral=ephemeral)
        await settle()
        await contexts[0].respond("after the handler")
        requests = on.fake.requests[start:]
        assert [
            (*call, EPHEMERAL) if flagged(request) else call
            for call, request in zip(calls(requests), requests, strict=True)
        ] == sent

    run_bot(scenario, auto_defer=delay)


def test_a_failed_auto_defer_expires_the_context_and_names_the_route(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario(on: Interactions) -> None:
        async def handler(ctx: ComponentContext) -> None:
            custom_ids.append(ctx.custom_id)
            await settle()
            with pytest.raises(InteractionExpired):
                await ctx.update("hi")

        on.fake.fail("POST", CALLBACK, 404, {"code": 10062, "message": "dead"})
        assert calls(await on.answer(ComponentContext, handler)) == [CALLBACK_6]

    custom_ids: list[str] = []
    with caplog.at_level(logging.WARNING, logger="bostie.context"):
        run_bot(scenario, auto_defer=0.0)

    assert [r.getMessage() for r in caplog.records if r.name == "bostie.context"] == [
        f"automatic defer of component {custom_ids[0]} failed"
    ]


def test_auto_defer_waits_for_a_response_in_flight_and_then_does_nothing() -> None:
    async def scenario(on: Interactions) -> None:
        async def handler(ctx: Context) -> None:
            release = on.fake.hold("POST", CALLBACK)
            answer = asyncio.create_task(ctx.respond("slow"))
            await settle()
            release.set()
            await answer
            await settle()

        assert calls(await on.answer(Context, handler)) == [CALLBACK_4]

    run_bot(scenario, auto_defer=0.0)


def test_a_response_waits_for_an_auto_defer_in_flight_and_edits_it() -> None:
    async def scenario(on: Interactions) -> None:
        async def handler(ctx: Context) -> None:
            release = on.fake.hold("POST", CALLBACK)
            await settle()
            answer = asyncio.create_task(ctx.respond("after the defer"))
            await settle()
            release.set()
            await answer
            assert ctx.state is ResponseState.RESPONDED

        assert calls(await on.answer(Context, handler)) == [CALLBACK_5, EDIT]

    run_bot(scenario, auto_defer=0.0)


@pytest.mark.parametrize(("status", "code"), [(404, 10062), (404, 10015), (401, 50027)])
def test_a_dead_token_expires_the_context_and_nothing_is_sent_again(
    status: int, code: int
) -> None:
    async def scenario(on: Interactions) -> None:
        async def handler(ctx: ComponentContext) -> None:
            on.fake.fail("POST", CALLBACK, status, {"code": code, "message": "dead"})
            with pytest.raises(HTTPException):
                await ctx.respond("hi")
            assert ctx.state is ResponseState.EXPIRED

            for call in CALLS.values():
                with pytest.raises(InteractionExpired):
                    await call(ctx)

        assert calls(await on.answer(ComponentContext, handler)) == [CALLBACK_4]

    run_bot(scenario)


def test_a_dead_token_after_a_defer_is_never_edited_again() -> None:
    async def scenario(on: Interactions) -> None:
        async def handler(ctx: Context) -> None:
            await ctx.defer()
            on.fake.fail("PATCH", WEBHOOK, 404, {"code": 10015, "message": "gone"})
            with pytest.raises(NotFound):
                await ctx.respond("hi")
            with pytest.raises(InteractionExpired):
                await ctx.respond("again")

        assert calls(await on.answer(Context, handler)) == [CALLBACK_5, EDIT]

    run_bot(scenario)


def test_an_unknown_follow_up_message_keeps_the_context_alive() -> None:
    async def scenario(on: Interactions) -> None:
        async def handler(ctx: Context) -> None:
            await ctx.respond("hi")
            on.fake.fail("POST", WEBHOOK, 404, {"code": 10008, "message": "gone"})
            with pytest.raises(NotFound):
                await ctx.followup("gone")
            await ctx.followup("still works")
            assert ctx.state is ResponseState.RESPONDED

        requests = await on.answer(Context, handler)
        assert calls(requests) == [CALLBACK_4, FOLLOWUP, FOLLOWUP]

    run_bot(scenario)


def test_flags_reach_every_answer_and_ephemeral_adds_its_bit() -> None:
    v2 = MessageFlags.IS_COMPONENTS_V2
    text = {"type": 10, "content": "hi"}

    async def scenario(on: Interactions) -> None:
        async def answered(ctx: ComponentContext) -> None:
            await ctx.respond(components=[text], ephemeral=True)
            await ctx.followup(flags=MessageFlags.SUPPRESS_NOTIFICATIONS)
            await ctx.edit(components=[text])

        async def deferred(ctx: Context) -> None:
            await ctx.defer(ephemeral=True)
            await ctx.respond(components=[text])

        async def updated(ctx: ComponentContext) -> None:
            await ctx.update(components=[text])

        answer, followup, edit = await on.answer(ComponentContext, answered)
        _, filled = await on.answer(Context, deferred)
        (update,) = await on.answer(ComponentContext, updated)
        assert answer.json["data"]["flags"] == v2 | EPHEMERAL
        assert followup.json["flags"] == MessageFlags.SUPPRESS_NOTIFICATIONS
        assert edit.json["flags"] == v2
        cleared = {"components": [text], "flags": v2, "content": None, "embeds": []}
        assert filled.json == cleared | {"allowed_mentions": MENTIONS}
        assert update.json["data"] == cleared | {"allowed_mentions": MENTIONS}

    run_bot(scenario)


def test_new_messages_refuse_attachments_which_only_edits_take() -> None:
    parts: dict[str, Any] = {"attachments": [1]}

    async def scenario(on: Interactions) -> None:
        async def handler(ctx: Context) -> None:
            for send in (
                ctx.respond,
                ctx.followup,
                ctx.defer,
                partial(on.bot.send_message, 1),
            ):
                with pytest.raises(TypeError, match=": attachments"):
                    await send(**parts)
            await ctx.respond("ok")

        assert calls(await on.answer(Context, handler)) == [CALLBACK_4]

    run_bot(scenario)


def test_answers_send_files_and_edits_keep_only_the_listed_attachments() -> None:
    files = [File("new.png", b"png")]
    kept = 1564609863064092673

    async def scenario(on: Interactions) -> None:
        async def updated(ctx: ComponentContext) -> None:
            await ctx.update(attachments=[kept])

        async def answered(ctx: Context) -> None:
            await ctx.respond("hi", files=files)
            await ctx.followup(files=files)
            await ctx.edit(files=files, attachments=[kept])

        (update,) = await on.answer(ComponentContext, updated)
        answer, followup, edit = await on.answer(Context, answered)
        new = {"id": 0, "filename": "new.png"}
        for request in (answer, followup, edit):
            assert [(part.filename, part.data) for part in request.parts[1:]] == [
                ("new.png", b"png")
            ]
        assert answer.json["data"]["attachments"] == [new]
        assert followup.json["attachments"] == [new]
        assert edit.json["attachments"] == [{"id": kept}, new]
        assert update.json["data"]["attachments"] == [{"id": kept}]

    run_bot(scenario)
