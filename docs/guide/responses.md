---
description: "Answer Discord interactions: respond, defer, edit, follow up, ephemeral messages, auto defer, the 3-second and 15-minute limits, and allowed mentions."
---

# Answering interactions

Every slash command, context menu command, button, select and modal submit is an interaction, and Discord waits for the app to answer it. A command handler gets a `Context`, a component handler a `ComponentContext` and a modal handler a `ModalContext`; they share the answering methods below.

## What a context knows

| Attribute | Value |
|---|---|
| `ctx.user` | the `User` who triggered it, in a guild or a DM |
| `ctx.member` | their `Member` in a guild, else `None` |
| `ctx.guild_id`, `ctx.channel_id` | where it happened; `guild_id` is `None` in DMs |
| `ctx.channel` | the `PartialChannel` the interaction carries |
| `ctx.locale` | the user's client language |
| `ctx.interaction` | the whole interaction model, for anything else, such as `app_permissions` or `guild_locale` |
| `ctx.bot` | the bot |
| `ctx.voice` | [voice](../voice/index.md) for this guild and user |
| `ctx.command_name` | on `Context`, the command with its subcommands, such as `"roles color set"` |

## Answer, edit, follow up

```python
--8<-- "docs/snippets/responses/answer_flow.py"
```

`ctx.respond(...)` sends the first answer. Called again, it sends a follow-up message instead, so a handler can call it as often as it needs. It returns the message Discord created, a `WebhookMessage`.

- `ctx.edit(**parts)` edits the first answer, and `ctx.edit(message_id, **parts)` a follow-up.
- `ctx.followup(...)` always sends a new message, except right after `defer()`, where it fills in the deferred answer.
- `ctx.fetch()` and `ctx.fetch(message_id)` return the answer or a follow-up as Discord has it now.
- `ctx.delete()` and `ctx.delete(message_id)` delete them.

`respond` and `followup` take `content` and the parts listed in [Message content](messages.md): `embeds`, `components`, `files`, `allowed_mentions`, `flags`, `tts`, `poll`, plus `ephemeral`. An edit takes the same parts by keyword: a part you leave out stays as it is, and `None` clears it.

## Messages that delete themselves

```python
--8<-- "docs/snippets/responses/short_lived.py"
```

`delete_after=` on `respond`, `followup` and `bot.rest.messages.create` deletes the message it sent that many seconds later. The bot runs the deletion in the background and cancels it when it closes, so a message due after the bot stops stays. An interaction's messages are deleted through its token, which works for ephemeral ones too but ends 15 minutes after the interaction; a deletion due later fails with a logged warning. A message someone deleted already is skipped quietly.

## Ephemeral answers

`ephemeral=True` on a call makes that message visible only to the user. `ephemeral=True` on a command, a component route, a modal route or a router makes it the default for their answers, and a call can still pass `ephemeral=False`. An existing message cannot change between ephemeral and public: an edit that asks for it logs a warning once and keeps the message as it is.

## Deferring

A first answer must reach Discord within 3 seconds. `await ctx.defer()` shows "thinking…" in its place, and the handler then has 15 minutes to `respond`; `defer(ephemeral=True)` makes the coming answer ephemeral.

```python
--8<-- "docs/snippets/responses/deferring.py"
```

bostie also defers by itself: a use still unanswered `auto_defer` seconds after it arrived is deferred, 2.5 seconds by default. A component or a modal opened from a message is deferred with `defer_update`, which keeps the message as it is until `update` edits it. `auto_defer` is set on the bot, a router, a command or a route, and `None` turns it off there. The timer counts from when the gateway delivered the interaction, so a blocked event loop delays the handler but not the deadline; bostie logs a warning when a handler starts more than a second late.

## Modals and Activities

`await ctx.modal(ui.modal(...))` answers with a popup form; see [Modals](modals.md). `await ctx.launch_activity()` opens the app's Activity and returns the id of its instance. Both have to be the first answer, and nothing can answer the interaction after a modal: the modal's submit is a new interaction.

## The rules, and the errors for breaking them

Each call checks the state of the interaction first:

| Call | Before any answer | After `defer()` | After `defer_update()` | After an answer |
|---|---|---|---|---|
| `respond` | answers | fills in the answer | sends a follow-up | sends a follow-up |
| `defer` | defers | nothing | nothing | `InteractionResponded` |
| `edit`, `delete` | `InteractionNotAnswered` | edits, deletes the answer | edits, deletes the message | edits, deletes the answer |
| `followup` | `InteractionNotAnswered` | fills in the answer | sends a follow-up | sends a follow-up |
| `update` | edits the message | `InteractionResponded` | edits the message | `InteractionResponded` |
| `modal`, `launch_activity` | answers | `InteractionResponded` | `InteractionResponded` | `InteractionResponded` |

`update` and `defer_update` exist on component and modal contexts only. Every call made after a modal raises `InteractionResponded`.

An interaction token lives 15 minutes. Any call after that, or after Discord said the token is invalid, raises `InteractionExpired` without sending anything.

A handler that returns without answering leaves the user with "The application did not respond", and bostie logs a warning naming the handler. One that deferred and never answered leaves "thinking…" until the token expires, also with a warning. When a handler raises before it answered, bostie answers "Something went wrong.", ephemeral unless the handler had deferred, unless an [error hook](checks.md#error-hooks) answers.

Discord allows 5 follow-ups for an interaction of an app installed only to the user, and bostie warns at the sixth.

## Allowed mentions

By default an answer can ping users and roles but not `@everyone` or `@here`: `Bot(allowed_mentions=...)` is `AllowedMentions(parse=("users", "roles"))`. An `allowed_mentions` given with a message changes only the kinds of mention it sets, so `AllowedMentions(users=(123,))` limits user pings and leaves the roles setting alone. `AllowedMentions(parse=())` silences every mention, and `replied_user=False` keeps a reply from pinging the author of the message it replies to.
