# REST API

![Bostie jumping for a ball](../assets/mascot/bostie-jumping.webp){ align=right width="180" loading=lazy }

`bot.rest` has a method for every operation of Discord's REST API that a bot can call, grouped by the resource it acts on: `bot.rest.messages.create`, `bot.rest.members.add_role`, `bot.rest.guilds.fetch`. Requests go through bostie's Rust HTTP client, which follows Discord's rate limits.

```python
--8<-- "docs/snippets/rest/rest_calls.py"
```

## Conventions

- The ids in the URL come first, positional-only, in the URL's order: `bot.rest.members.add_role(guild_id, user_id, role_id)`.
- Everything else is a keyword argument named as in Discord's documentation, checked against a `TypedDict` per operation, so a typo is a type error and a `TypeError` at run time.
- Operations that Discord records in the audit log take `reason=`, 1 to 512 characters, shown to the guild's moderators.
- An image or sound field, such as an emoji's `image` or a guild's `icon`, takes a `File`; bostie sends it as the data URI Discord expects. Permission fields take `Permissions`.
- An emoji, such as the one `reactions.create` adds, takes a Unicode emoji, a custom emoji as `<:name:id>`, `<a:name:id>` or `name:id`, or an `Emoji` or `PartialEmoji` model.
- Methods named `fetch` get one object, `list` iterates over several, `edit` modifies, `create` creates and `delete` deletes. Other verbs keep the documentation's word, such as `members.add_role` or `guilds.begin_prune`.
- Methods return models, or `None` when Discord answers with no content. They do not change `bot.cache`, except that `channels.create_dm` keeps the DM channel when `max_dm_channels` allows; the gateway events that follow a change update the cache.

## Paging

`list` methods of endpoints that Discord pages return an async iterator, which fetches the next page as you go: `async for message in bot.rest.messages.list(channel_id, limit=500):`, as `/today` does above. Without `limit` it walks every item. `before`, `after` and `around` start somewhere else, at most one of them; they take an id or a `datetime`, which bostie turns into the id of that moment. Messages come newest first, or oldest first with `after`. A `list` method of an endpoint that Discord does not page is a coroutine that returns a tuple. `audit_log.list` yields whole `AuditLogPage` objects, with the users, webhooks and other objects the entries refer to.

## Purging messages

`messages.purge(channel_id, limit=100, check=None)` walks the last `limit` messages, newest first, and deletes those for which `check` returns true, or all of them without one; `limit=None` walks the whole channel, and `before` and `after` start elsewhere, as in `list`. Messages younger than 14 days go in bulk deletes of up to 100, older ones one request each, since Discord bulk deletes only young messages; a type Discord does not let be deleted, such as a thread's starter message, is skipped. `bulk=False` deletes one at a time, as in a DM, where bulk deletes do not exist. It returns the deleted messages, and `reason` goes to the audit log. Bulk deletes and others' messages need Manage Messages.

```python
--8<-- "docs/snippets/rest/purge.py"
```

## The audit log

`audit_log.list(guild_id)` yields `AuditLogPage` objects, and the gateway sends each new entry as an `AuditLogEntryCreate` event. An entry's `changes` hold one `AuditLogChange` per changed field, whose `new_value` and `old_value` have the type the changed object gives that field: `Permissions` for a role's `permissions`, a `datetime` for a member's `communication_disabled_until`, a tuple of `Overwrite` for a channel's `permission_overwrites`. Discord's exceptions follow its documentation: `$add` and `$remove` on a member's roles hold `AuditLogRole` objects, a webhook's `avatar_hash` and an invite's `channel_id` take the type of `avatar` and of an id, and a command permission change is keyed by the role, user or channel id and holds a `CommandPermission`. A key bostie does not know, or a value in a shape the field does not take, keeps its JSON value, and a value Discord leaves out is `None`.

```python
--8<-- "docs/snippets/rest/audit_log.py"
```

## Errors

A request that Discord answers with an error status raises an `HTTPException`: `BadRequest` for 400, `Forbidden` for 403, `NotFound` for 404, `ServerError` for 5xx. The exception has the `status`, Discord's `code` as a `JSONErrorCode`, its `message`, the per-field `errors`, and the raw `body`; its text lists every field error on its own line. `UnexpectedResponse` means Discord accepted the request but its answer did not decode; its message says whether the change took effect, and `error.raw` holds the answer.

## Rate limits, retries and timeouts

The Rust client tracks every route's rate limit bucket and Discord's global limit, and holds a request until its bucket has room. `Bot(http_settings=HTTPSettings(...))` changes how:

| `HTTPSettings` field | Default | Meaning |
|---|---|---|
| `global_rate_limit` | 50 | requests per second across all routes; raise it if Discord gave the bot a higher limit |
| `max_rate_limit_wait` | 30.0 | the longest wait for a rate limit; a request that would wait longer raises `RateLimited` without being sent |
| `retries` | 2 | how often a failed request is retried, with exponential backoff that waits at most `max_rate_limit_wait` |
| `connect_timeout` | 10.0 | seconds to open a connection |
| `read_timeout` | 60.0 | seconds to wait for an answer |
| `rate_limiter` | `True` | `False` sends at once and only waits out the 429s that come back, for use behind a rate-limit proxy |
| `proxy` | `None` | an HTTP or HTTPS proxy URL for every request, `voice.Url` audio and CDN downloads included, credentials included as in `http://user:password@host:3128` |

A 429 is retried after the delay Discord gives, if that is within `max_rate_limit_wait`. A 500, 502, 503, 504 or 524, or a connection that broke, is retried `retries` times for every method but `POST`, which might have taken effect; a connection that never opened is retried for every method.

Discord bans an IP that sends 10 000 invalid requests (401, 403, 429) in 10 minutes. bostie counts them and logs a warning at 1000, 5000 and 9000. At 9500 it refuses every request with `RequestBudgetExhausted` instead of sending it, until the count of the last 11 minutes drops below 9000.

## Any endpoint

`bot.http.request(method, route, **params)` sends any request through the same client and returns the decoded JSON, or `None` for an empty answer. The route keeps its placeholders, which keyword arguments fill: `await bot.http.request("GET", "/channels/{channel_id}/pins", channel_id=123)`. It takes `json=`, `files=` for a multipart upload, `form=` for named form fields, `urlencoded=` for a form-urlencoded body, `query=` and `reason=`, and raises the same exceptions. `bot.http.send(...)` takes the same arguments and returns the answer's raw bytes, for an endpoint that does not answer with JSON.

## Downloading files

Attachments and images live on Discord's CDN, not behind the API, so a download sends no token and spends no rate limit. `bot.http` fetches them through the same Rust client, with its timeouts, retries and `proxy`:

| Method | Does |
|---|---|
| `read(source)` | returns the bytes |
| `save(source, path)` | writes them to the file at `path` |
| `to_file(source)` | returns a `File` to upload again; from an `Attachment` it keeps the file name, description, title, spoiler and voice message details, from a URL the last part of its path names it |

`source` is an `Attachment`, or a URL such as the [`bostie.cdn`](formatting.md) helpers build. A missing file raises `NotFound`.

```python
--8<-- "docs/snippets/rest/downloads.py"
```

## Without a bot

A script or a web backend that needs only REST uses a `RESTClient`:

```python
--8<-- "examples/rest_script.py"
```

`async with RESTClient(token) as client:` opens the HTTP client; `client.rest` has every group of `bot.rest`, and `client.http` the raw client. The token defaults to `BOSTIE_TOKEN`, and the client takes `api_url`, `http_settings` and `allowed_mentions` like the bot. The [OAuth2 guide](oauth2.md) covers `BearerClient`, which calls the API with a user's token, and the [webhooks guide](webhooks.md) the clients that need no token at all.

## The groups

| `bot.rest.` | Methods |
|---|---|
| `application` | `edit`, `fetch`, `fetch_activity_instance`, `fetch_bot_application`, `list_role_connection_metadata`, `update_role_connection_metadata` |
| `audit_log` | `list` |
| `auto_moderation` | `create`, `delete`, `edit`, `fetch`, `list` |
| `bans` | `bulk_create`, `create`, `fetch`, `list`, `remove` |
| `channels` | `add_recipient`, `create`, `create_dm`, `create_group_dm`, `delete`, `delete_permission`, `edit`, `edit_permissions`, `edit_positions`, `fetch`, `fetch_member_permissions`, `fetch_role_permissions`, `follow`, `list`, `remove_recipient`, `set_voice_status`, `trigger_typing_indicator` |
| `commands` | `bulk_overwrite`, `bulk_overwrite_guild`, `create`, `create_guild`, `delete`, `delete_guild`, `edit`, `edit_guild`, `fetch`, `fetch_guild`, `fetch_permissions`, `list`, `list_guild`, `list_permissions` |
| `emojis` | `create`, `create_application`, `delete`, `delete_application`, `edit`, `edit_application`, `fetch`, `fetch_application`, `list`, `list_application` |
| `gateway` | `fetch`, `fetch_bot` |
| `guilds` | `begin_prune`, `edit`, `edit_incident_actions`, `edit_welcome_screen`, `edit_widget`, `fetch`, `fetch_preview`, `fetch_prune_count`, `fetch_vanity_url`, `fetch_welcome_screen`, `fetch_widget`, `fetch_widget_image`, `fetch_widget_settings`, `leave`, `list` |
| `identities` | `delete`, `fetch_profile`, `list`, `list_by_external_id`, `update_profile` |
| `integrations` | `delete`, `list` |
| `invites` | `add_target_user`, `bulk_add_target_users`, `bulk_delete_target_users`, `create`, `delete`, `fetch`, `fetch_target_users_job_status`, `list`, `list_guild`, `list_target_users`, `remove_target_user`, `update_target_users` |
| `lobbies` | `add_member`, `bulk_update_members`, `create`, `create_invite_for_user`, `delete`, `edit`, `fetch`, `remove_member`, `update_message_moderation_metadata` |
| `members` | `add`, `add_role`, `edit`, `edit_current`, `fetch`, `fetch_permissions`, `list`, `remove`, `remove_role`, `search` |
| `messages` | `bulk_delete`, `create`, `crosspost`, `delete`, `edit`, `fetch`, `list`, `list_pins`, `pin`, `purge`, `search`, `unpin` |
| `monetization` | `consume_entitlement`, `create_test_entitlement`, `delete_test_entitlement`, `fetch_entitlement`, `fetch_subscription`, `list_entitlements`, `list_skus`, `list_subscriptions` |
| `onboarding` | `edit`, `fetch` |
| `partner_sdk` | `create_provisional_token`, `exchange_child_token`, `exchange_external_token`, `unmerge_external_account`, `unmerge_provisional_account`, `update_dm_moderation_metadata` |
| `polls` | `end`, `list_answer_voters` |
| `reactions` | `create`, `delete`, `delete_all`, `delete_all_for_emoji`, `delete_own`, `list` |
| `roles` | `create`, `delete`, `edit`, `edit_positions`, `fetch`, `fetch_member_counts`, `fetch_permissions`, `list` |
| `scheduled_events` | `create`, `delete`, `edit`, `fetch`, `list`, `list_users` |
| `soundboard` | `create`, `delete`, `edit`, `fetch`, `list`, `list_default`, `send` |
| `stage_instances` | `create`, `delete`, `edit`, `fetch` |
| `stickers` | `create`, `delete`, `edit`, `fetch`, `fetch_guild`, `fetch_pack`, `list`, `list_packs` |
| `templates` | `create`, `delete`, `edit`, `fetch`, `list`, `sync` |
| `threads` | `add_member`, `fetch_member`, `join`, `leave`, `list_active`, `list_joined_private_archived`, `list_members`, `list_private_archived`, `list_public_archived`, `remove_member`, `start`, `start_from_message`, `start_in_forum` |
| `users` | `edit_current`, `fetch`, `fetch_current` |
| `voice` | `edit_current_state`, `edit_state`, `fetch_current_state`, `fetch_state`, `list_guild_regions`, `list_regions` |
| `webhooks` | `create`, `delete`, `delete_message`, `delete_with_token`, `edit`, `edit_message`, `edit_with_token`, `execute`, `execute_github`, `execute_slack`, `fetch`, `fetch_message`, `fetch_with_token`, `list`, `list_guild` |

Each method's parameters and return type are in the [REST API reference](../reference/api/rest.md). A few operations are left to `bot.http.request` on purpose: endpoints Discord deprecated or never documented, and the interaction callback and original-response endpoints, which the [interaction contexts](responses.md) cover.

Some groups serve Discord features beyond bots in guilds: `monetization` and `application` for [monetization and Activities](platform.md), `lobbies`, `identities` and `partner_sdk` for the [Social SDK](platform.md#social-sdk), and `application.update_role_connection_metadata` for [linked roles](oauth2.md#linked-roles).
