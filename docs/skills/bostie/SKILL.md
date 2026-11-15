---
name: bostie
description: Write and change Discord bots built on bostie, the Python Discord library with a Rust core. Use when code imports bostie, or when asked to add a slash command, button, select menu, modal, event listener, REST call, voice playback or recording, or a test with bostie.testing to such a bot.
---

# Writing a bostie bot

bostie has an API of its own and is still pre-alpha. Do not assume a method exists because another library has one. The installed package is the truth for the version this project uses: it is fully typed, `python -c "import bostie; print(bostie.__file__)"` shows where it is, and `python -m bostie` prints its version. Read a signature there before you call anything this skill does not show.

## A whole bot

```python
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import bostie
from bostie import ComponentContext, Context, Forbidden, Intents, ResolvedMember, ui
from bostie.events import MessageCreate

bot = bostie.Bot(intents=Intents.DEFAULT | Intents.MESSAGE_CONTENT)
kudos: dict[int, int] = {}


@bot.command
async def thank(
    ctx: Context,
    member: Annotated[ResolvedMember, "Who to thank"],
    size: Literal["small", "big"] = "small",
) -> None:
    """Give a member kudos."""
    kudos[member.id] = kudos.get(member.id, 0) + (5 if size == "big" else 1)
    undo = ui.button("Undo", custom_id=undo_thanks.id(user_id=member.id))
    await ctx.respond(
        f"{member.mention} has {kudos[member.id]} kudos.", components=[ui.row(undo)]
    )


@bot.on_component("thank:undo:{user_id:int}")
async def undo_thanks(ctx: ComponentContext, user_id: int) -> None:
    kudos[user_id] = max(kudos.get(user_id, 0) - 1, 0)
    await ctx.update(
        content=f"Undone: <@{user_id}> has {kudos[user_id]}.", components=[]
    )


@bot.command
async def busy(ctx: Context) -> None:
    """Count the messages sent here in the last hour."""
    if ctx.channel_id is None:
        await ctx.respond("Use this in a channel.", ephemeral=True)
        return
    await ctx.defer(ephemeral=True)
    since = datetime.now(UTC) - timedelta(hours=1)
    count = 0
    try:
        async for _ in bot.rest.messages.list(ctx.channel_id, after=since, limit=500):
            count += 1
    except Forbidden:
        await ctx.respond("I cannot read this channel.")
        return
    await ctx.respond(f"{count} messages in the last hour.")


@bot.listen
async def ping(event: MessageCreate) -> None:
    message = event.message
    if not message.author.bot and message.content == "!ping":
        await bot.rest.messages.create(message.channel_id, "pong", reply_to=message.id)


if __name__ == "__main__":
    bot.run()
```

- `bot.run()` reads the token from the `BOSTIE_TOKEN` environment variable. Never write a token into code. Keep `bot.run()` under `if __name__ == "__main__":` so tests can import the bot.
- On every start, `bot.run()` registers the commands with Discord if they changed. While developing, `bostie.Bot(dev_guilds=[guild_id])` registers them in one test guild.

## Commands

- A slash command is an `async def` under `@bot.command`. Its first parameter is `ctx: Context`, and every parameter after it is an option. The function's name is the command's name: lowercase, 1 to 32 characters. The first line of the docstring is the description, at most 100 characters.
- The type hint picks the option type: `str`, `int`, `float`, `bool`, `User`, `ResolvedMember`, `Role`, `PartialChannel`, `Attachment`, or `Literal[...]` or an `Enum` for fixed choices. A member option is `ResolvedMember`, not `Member`, and a channel option is `PartialChannel`, not `Channel`. Any other hint raises `CommandDefinitionError` when the decorator runs.
- `Annotated[str, "What it is"]` gives an option its description, `Range[int, 1, 10]` bounds it, and a default or `| None` makes it optional. `Option(...)` inside `Annotated` sets the rest, such as `autocomplete` or `channel_types`.
- `bot.group("name", "description")` returns a group whose `@group.command` adds subcommands. `@bot.user_command("Name")` and `@bot.message_command("Name")` add the right-click commands.

## Answering an interaction

- Discord waits 3 seconds for the first answer. `await ctx.respond(...)` answers, and calling it again sends a follow-up. Before slow work, `await ctx.defer()`; the next `respond` fills in the deferred answer. bostie defers by itself after 2.5 seconds (`auto_defer`).
- Answer on every path, early returns included. A handler that returns without answering leaves the user with "The application did not respond".
- `ephemeral=True` on a call, or `@bot.command(ephemeral=True)`, shows the answer only to the user who ran the command.
- `ctx.edit(...)` edits the first answer and `ctx.followup(...)` always sends a new message.
- `async with ctx.typing():` shows the bot typing during slow work, renewed until the block ends.
- Blocking work (reading files, CPU work, synchronous HTTP clients) goes through `asyncio.to_thread`. Blocking the event loop delays every handler past Discord's deadline.

## Components and modals

- `bostie.ui` builds components as dictionaries. `ui.row` holds up to five buttons or one select menu: `ui.button(label, custom_id=...)`, `ui.link_button(label, url)`, `ui.string_select(custom_id, options)` with `ui.select_option(label, value)`, `ui.user_select`, `ui.role_select` and `ui.channel_select`.
- `@bot.on_component("thank:undo:{user_id:int}")` handles every custom id that fits the template, and the placeholders arrive as keyword arguments. Build the ids with the route's `.id(...)`, as `undo_thanks.id(user_id=...)` does above. State lives in the custom id, not in a Python object, so buttons keep working after a restart.
- In a component handler, `ctx.update(...)` edits the message the component is on, and `ctx.values`, `ctx.users`, `ctx.roles` and `ctx.channels` hold what a select picked.
- `ui.text`, `ui.section`, `ui.container`, `ui.media_gallery`, `ui.separator` and `ui.file` make a Components V2 message. Such a message has no `content` or `embeds`; bostie raises `ValueError` if you pass them, so put the text in `ui.text`.
- A modal is a first answer: `await ctx.modal(ui.modal("profile", "Profile", ui.label("Name", ui.text_input("name"))))`. `@bot.on_modal("profile")` gets the submit, where `ctx.text("name")` reads the field.

## Events

- `@bot.listen` registers an `async def` for the event class in its parameter's type hint, or for each class of a union. The classes live in `bostie.events`: `MessageCreate`, `MemberJoin`, `ReactionAdd` and the rest.
- `Intents.DEFAULT` holds every intent but the privileged ones, `Intents.PRIVILEGED` those three and `Intents.ALL` both. Message text in guilds needs `Intents.MESSAGE_CONTENT` and member events need `Intents.GUILD_MEMBERS`; a privileged intent must also be switched on in the Developer Portal. bostie logs a warning that names an intent a listener is missing.
- Skip the bot's own messages and other bots' with `message.author.bot`.
- Inside a handler, `await bot.wait_for(ReactionAdd, check=...)` waits for one event and `async with bot.stream(ReactionAdd) as events:` for many. Neither has a timeout, so wrap them in `asyncio.timeout`.

## REST

- `bot.rest.<group>.<verb>(ids..., **fields)`: the ids in the URL come first and positional, then Discord's field names as keywords, and `reason=` for the audit log. The verbs are `fetch`, `list`, `create`, `edit` and `delete`, or the word Discord's docs use, as in `bot.rest.members.add_role(guild_id, user_id, role_id)`.
- A paged `list` is an async iterator: `async for message in bot.rest.messages.list(channel_id, limit=100):`.
- `await bot.rest.messages.purge(channel_id, limit=100, check=...)` deletes recent messages in bulk and older ones one by one, skipping system messages; do not loop over `delete` yourself.
- Failures raise `HTTPException` subclasses: `Forbidden`, `NotFound`, `BadRequest`, `RateLimited`. bostie waits out rate limits itself.
- `bot.rest` exists once the bot has started. A script without a gateway uses `async with RESTClient() as client:` and `client.rest`.
- Attachments and CDN images download without the token: `await bot.http.read(attachment)` returns the bytes, `bot.http.save(attachment, path)` writes a file, and `bot.http.to_file(attachment)` returns a `File` to upload again.
- For an endpoint `bot.rest` does not cover: `await bot.http.request("GET", "/channels/{channel_id}/pins", channel_id=channel_id)`.
- Reads that need no request go to the cache: `bot.cache.guild(guild_id)`, `bot.cache.channel(channel_id)`, `bot.cache.member(guild_id, user_id)`. All members are cached only with `bostie.Bot(members=True)`, which needs `Intents.GUILD_MEMBERS`.

## Checks, errors and structure

- `checks=[...]` on a command takes functions of the context and `bostie.checks` helpers: `has_permissions`, `bot_has_permissions`, `Cooldown`, `MaxConcurrency`. `default_permissions=Permissions.MANAGE_GUILD` hides a command from members without that permission.
- `@bot.on_error(Forbidden)`, or `.on_error` on one command, answers a failure. Without one, bostie logs the traceback and answers "Something went wrong.".
- Split a bot into modules with `Router()`, which has the bot's decorators; `bot.include(router)` adds one.
- `@bot.every(seconds=300)` runs a background task.

## Voice and tests

- Joining a voice channel, playing, queues and recording: read [references/voice.md](references/voice.md).
- Tests run the real bot against a fake Discord from `bostie.testing`: read [references/testing.md](references/testing.md) before writing or fixing a test.

## Habits that fail in bostie

- `ctx.send(...)` and `ctx.reply(...)` do not exist: answer with `ctx.respond(...)`.
- There is no prefix command system: write slash commands, or listen to `MessageCreate`.
- Component classes with callbacks do not exist: build components with `ui` and handle them with `@bot.on_component`.
- `Embed` is a frozen model built from keywords: `Embed(title="Status", fields=(EmbedField(name="Ping", value="41 ms"),))`.
- `ctx.channel` is the `PartialChannel` the interaction carries; fetch the full one with `bot.rest.channels.fetch(channel_id)`.
