---
description: "Build Discord messages with embeds, file uploads, polls, replies, forwards, Components V2, a typing indicator and voice messages, from one set of parts."
---

# Message content

A message is built from the same parts wherever you send it: `ctx.respond`, `ctx.followup`, `ctx.edit` and `ctx.update`, `bot.rest.messages.create` and `edit`, and a webhook's `execute`. Each part is a keyword argument, and a part you leave out is not sent.

| Part | Type | Notes |
|---|---|---|
| `content` | `str` | the first positional argument of `respond`, `followup`, `messages.create` and `execute` |
| `embeds` | `Embed` objects | up to 10 rich embeds |
| `components` | `ui` builders | buttons, selects and the layout components; see [Components](components.md) |
| `files` | `File` objects | uploads; see below |
| `allowed_mentions` | `AllowedMentions` | which mentions may ping; see [Allowed mentions](responses.md#allowed-mentions) |
| `flags` | `MessageFlags` | such as `SUPPRESS_EMBEDS` and `SUPPRESS_NOTIFICATIONS` |
| `tts` | `bool` | read the content aloud to whoever has the channel open |
| `poll` | `PollCreateRequest` | a poll; see below |
| `ephemeral` | `bool` | interaction answers only |

`bot.rest.messages.create` also takes `reply_to`, `message_reference`, `sticker_ids`, `nonce`, `enforce_nonce` and `shared_client_theme`, and `delete_after`, as [interaction answers](responses.md#messages-that-delete-themselves) do.

## Embeds and files

```python
--8<-- "docs/snippets/responses/rich_messages.py"
```

`Embed` and its parts (`EmbedField`, `EmbedFooter`, `EmbedAuthor`, `EmbedMedia`) are frozen models with keyword arguments; a plain `dict` in Discord's JSON shape works too. `color` takes an `int` such as `0x5865F2`, and the [`bostie.color`](formatting.md#colors) helpers build one.

`File(name, data)` uploads `data` under `name`. `description` is its alt text, `spoiler=True` blurs it, and `title` sets the title Discord shows. An embed or a component refers to an upload as `attachment://name`, as the chart does above. bostie does not check sizes: Discord's upload limit depends on the guild's boosts and the user's Nitro.

## Polls

`PollCreateRequest` takes a `question` and `answers`, both `PollMedia` with `text` and, for answers, an optional `emoji`. `duration` is in hours, 24 when left out, and `allow_multiselect=True` lets a user pick several answers. `bot.rest.polls.end(channel_id, message_id)` closes a poll early, `bot.rest.polls.list_answer_voters(...)` lists who picked an answer, and the `PollVoteAdd` and `PollVoteRemove` [events](events.md) report votes as they happen.

## Editing and attachments

An edit takes the parts by keyword, plus `attachments`. A part you leave out stays, and `None` clears it: `ctx.edit(embeds=None)` removes the embeds and keeps the content. When an edit passes `files` or `attachments`, the message keeps only the attachments listed in `attachments`, given as `Attachment` objects or ids, plus the new files.

## Components V2

A message whose `components` include a layout component (`ui.text`, `ui.section`, `ui.container`, `ui.media_gallery`, `ui.file`, `ui.separator` or `ui.thumbnail`) is a Components V2 message: bostie sets the `IS_COMPONENTS_V2` flag and the components are the whole message. Such a message cannot have `content`, `embeds`, stickers or a poll, and bostie raises `ValueError` when you pass them; put the text in `ui.text` instead. Buttons and selects in `ui.row` alone work in classic messages with content too.

## Replies

```python
--8<-- "docs/snippets/responses/replies.py"
```

`reply_to` takes the id of a message in the same channel. To forward a message, pass `message_reference=MessageReference(type=MessageReferenceType.FORWARD, message_id=..., channel_id=...)` instead; the two cannot be combined. Reading `message.content` in guilds needs the privileged Message Content intent, as the [gateway guide](gateway.md#intents) explains.

## Typing indicator

```python
--8<-- "docs/snippets/responses/typing_indicator.py"
```

`async with ctx.typing():` shows the bot typing in the interaction's channel while the block runs. bostie sends the indicator when the block starts and again every 9 seconds, since Discord shows it for 10, and stops when the block ends, by an exception too. `await ctx.typing()` shows it once. Outside an interaction, `bot.rest.channels.trigger_typing_indicator(channel_id)` does the same, awaited or in `async with`. A message the bot sends in the channel clears the indicator at once.

## Voice messages

```python
--8<-- "docs/snippets/responses/voice_memo.py"
```

`await voice.message(audio)` reads a file path or bytes, measures the duration, computes the waveform Discord draws, and returns a `File` ready to send. The name's extension tells the format; Discord's clients record 48 kHz mono Ogg Opus. A voice message is that one file and nothing else: no content, embeds, components, poll or stickers. bostie sets the `IS_VOICE_MESSAGE` flag, and raises `ValueError` if anything else is in the message. A `File` you build yourself becomes a voice message when it has `duration_secs` and `waveform`, the 8-bit samples Discord draws.
