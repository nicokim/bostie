# Webhooks

A webhook posts messages into one channel under a name and avatar of its own. Its URL holds an id and a token, and the token is all it takes to post, so a deploy script or a monitoring job can send messages without a bot.

```python
--8<-- "docs/snippets/webhooks/deploy_notice.py"
```

`WebhookClient(url)` parses the id and token from a webhook URL, and raises `ValueError` for anything else. Use it inside `async with`, which opens and closes its HTTP client. `SyncWebhookClient(url)` has the same methods for code without an event loop; each call runs its own.

| Method | Does |
|---|---|
| `execute(content, **parts)` | posts a message and returns it once Discord saved it |
| `edit_message(message_id, **parts)` | edits a message the webhook sent; a part left out stays, `None` clears it |
| `fetch_message(message_id)`, `delete_message(message_id)` | fetch or delete one |
| `fetch()`, `edit(name=..., avatar=...)`, `delete()` | the webhook itself |
| `execute_slack(payload)`, `execute_github(payload)` | post a payload in the formats Discord's Slack-compatible and GitHub-compatible endpoints accept |

`execute` takes the [message parts](messages.md) and also:

- `username` and `avatar_url`, shown instead of the webhook's own for this message;
- `thread_id`, to post in a thread of the channel; the message methods take it too;
- `thread_name`, to start a thread in a forum or media channel, with `applied_tags`;
- `with_components=True`, needed for components on a webhook that no app owns.

The clients take `api_url`, `http_settings` and `allowed_mentions` like the bot; by default a webhook can ping users and roles but not `@everyone`.

When Discord answers 404 Unknown Webhook, the webhook was deleted. bostie then answers every later request for it with the same 404 without sending it, so a dead webhook does not count against the [invalid request limit](rest.md#rate-limits-retries-and-timeouts).

## Managing webhooks with a bot

`bot.rest.webhooks` creates and lists a channel's or guild's webhooks, which needs the Manage Webhooks permission, and has the token methods too:

- `create(channel_id, name=..., avatar=File(...))` returns a `Webhook` whose `url` you can hand to a `WebhookClient`.
- `list(channel_id)`, `list_guild(guild_id)`, `fetch(webhook_id)`, `edit(webhook_id, ...)` and `delete(webhook_id)` work with the bot's token.
- `execute(webhook_id, token, ...)` and the other `_with_token` and message methods are what `WebhookClient` calls.

The `WebhooksUpdate` event says that a channel's webhooks changed. A message a webhook sent arrives as a `WebhookMessage`, whose `author` is a `PartialUser` and whose `webhook_id` is set.
