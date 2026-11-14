# Models and enums

Everything bostie decodes from Discord is a model: `Message`, `Guild`, `Member`, `Interaction` and about two hundred more, all importable from `bostie`. Models are frozen `msgspec` structs, decoded straight from the JSON bytes.

```python
--8<-- "docs/snippets/models/inspect_message.py"
```

## Types follow Discord's documentation

Each field is typed as Discord's documentation describes it:

- A field Discord always sends has no default and is never missing. If the docs say it can be `null`, it is `T | None`.
- A field the docs mark optional has a default: `()` for a list, an empty flag set for flags, `False` for a boolean whose absence means false, and `None` otherwise.
- Ids are `int`, timestamps are timezone-aware `datetime` objects in UTC, and colors are `int`.
- Where Discord sends a partial object, the field has a partial model: `PartialUser`, `PartialMember`, `PartialChannel`, `PartialMessage`, `PartialEmoji`, `PartialApplication`, `PartialIntegration`, `PartialEntitlement`, `UnavailableGuild`. A partial model has only the fields the partial payload has, so a missing field is a type error, not a `None` at run time.

Two places where one payload has two shapes get two classes:

- A message sent by a webhook, interaction answers included, is a `WebhookMessage`, whose `author` is a `PartialUser`. Any other message is a `Message` with a full `User` author. Fields that accept both are typed `Message | WebhookMessage`.
- A member resolved from an option, a select or a command target is a `ResolvedMember`, which carries its `user` and has no `deaf` and `mute`. A `Member` from the gateway or REST has both.

Interactions are `CommandInteraction`, `ComponentInteraction`, `ModalSubmitInteraction` or `AutocompleteInteraction` by their type, each with typed `data`.

## Change resilience

Discord adds fields, enum values and component types without notice, and a bot must not break when it does:

- Fields bostie does not know are ignored when decoding.
- Enums map an unknown value to a stable member named `UNKNOWN_<value>`, which compares equal to the value: `MessageType(99).name == "UNKNOWN_99"`.
- Flags keep bits bostie does not know, through every operator, `~` included.
- An unknown component type decodes to `UnknownComponent`, which keeps its raw JSON and is sent back unchanged when you pass it in `components`; an unknown interaction type decodes to `UnknownInteraction`, which reaches `InteractionCreate` listeners but no command or route.
- A nested message, such as `message.referenced_message` or the message of a component interaction, is decoded only when you read the property, so a broken one fails only where it is read.

An event payload that does not fit its class at all, for example because a required field is missing, is logged once and delivered only to `RawEvent` listeners, as [Events](events.md#raw-events-and-events-bostie-does-not-model) explains.

## Raw payloads

A model decoded from a whole payload keeps it: `model.raw_json` holds the bytes and `model.raw` decodes them into a new `dict`. Nested models, such as the author of a message, have `raw_json = None`. Every event has `raw` and `raw_json` too. They reach any field Discord sends before bostie models it.

A model's `repr` leaves out `raw_json` and secrets: `token`, `access_token`, `refresh_token` and `id_token`.

## Building and copying models

Models are immutable, and built with keyword arguments. `msgspec.structs.replace(model, field=value)` returns a copy with some fields changed. The models you send to Discord, such as `Embed`, `AllowedMentions`, `PollCreateRequest`, `MessageReference`, `ForumTag`, `RecurrenceRule` and `AutoModerationAction`, leave out the fields you did not set, so a decoded one is sent back as it came.

## Helpers on models

| Helper | Value |
|---|---|
| `user.mention`, `member.mention`, `role.mention`, `channel.mention` | the mention text, such as `<@123>` |
| `user.display_name`, `member.display_name` | the global name or username; for a member, the nickname first |
| `message.jump_url` | the link to the message |
| `message.author_member` | the author as a `ResolvedMember`, when the message carries the member |
| `role.permissions`, `overwrite.allow`, `overwrite.deny` | `Permissions` flags |
| `member.permissions` | a member's permissions in the interaction's channel, when Discord sent them |
| `webhook.url` | the webhook's URL, when it has a token |

The [formatting guide](formatting.md) covers mentions, timestamps and CDN URLs built from ids.

## Snowflakes

Discord ids encode when the object was created. `bostie.snowflake_time(id)` returns that moment as a UTC `datetime`, and `bostie.time_snowflake(when)` the lowest id created at a moment, or with `high=True` the highest, for paging by date. A naive `datetime` is read as local time.

## Enums

`bostie` exports Discord's enums and flags: `ChannelType`, `MessageType`, `Permissions`, `Intents`, `MessageFlags`, `Locale`, `ButtonStyle` and the others in the [enums reference](../reference/api/enums.md). Flags combine with `|` and test with `in`: `Permissions.SEND_MESSAGES in member_permissions`. `JSONErrorCode` names Discord's error codes, as `HTTPException.code` reports them.
