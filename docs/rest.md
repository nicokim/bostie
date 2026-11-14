# REST

`bot.rest` has every documented REST operation the bot token reaches, grouped by resource, once the bot has started. Those that need a user's OAuth2 token are on a `BearerClient`, below; interaction answers go through the contexts, and deprecated or legacy routes are left out.

- `await bot.rest.channels.edit(channel_id, name="news", topic=None)`: ids come first and positional, in the order of the URL. A keyword you leave out is left as it is, and `None` clears the field.
- `reason="..."` goes to the audit log on the operations that take one, such as `bot.rest.bans.create(guild_id, user_id, reason="spam")`. It takes 1 to 512 characters.
- A list Discord pages is an async iterator that pages for you: `async for message in bot.rest.messages.list(channel_id, limit=500)`. `bot.rest.audit_log.list(guild_id)` yields whole pages, with the users and webhooks the entries refer to.
- Image options such as `icon`, `avatar` and `banner` take a `File`.
- When an edit lists `attachments`, only those stay. A listed `Attachment` sends its `description` and spoiler flag too, so `msgspec.structs.replace(attachment, description="...")` changes its alt text.
- Results are bostie models, and they do not change `bot.cache`: the gateway events that follow do.
- `async with bostie.RESTClient(token) as client:` opens the same operations on `client.rest` without a gateway, for scripts and web backends. The token defaults to `BOSTIE_TOKEN`.
- `async with bostie.WebhookClient(url) as webhook:` posts through one webhook from its URL alone, with no bot token: `await webhook.execute("deploy done")`, and `edit_message`, `fetch_message` and `delete_message` for what it sent. `execute_slack` and `execute_github` take a payload in Slack's or GitHub's format. `bostie.SyncWebhookClient(url)` has the same methods for code without an event loop, each call running one.
- `bostie.oauth2.invite_url(client_id, permissions=..., scopes=["bot"])` builds an install link, and `oauth2.authorize_url(client_id, scopes=[...], redirect_uri=...)` the link that starts an OAuth2 code grant. `async with bostie.BearerClient() as oauth:` turns the code into a token with `oauth.rest.oauth2.exchange_code(...)`, and refreshes, revokes or gets one from client credentials. `BearerClient(token.access_token)` then reaches what the user granted on its `rest`: `users.fetch_current()`, `guilds.list()`, `users.list_connections()`, `members.fetch_current(guild_id)`, `commands.edit_permissions(...)`, and `users.update_role_connection(application_id, ...)` for linked roles.
- A Social SDK game backend manages lobbies with the bot token on `bot.rest.lobbies`, while a `BearerClient` with a player's token joins them and sends lobby messages. `bot.rest.identities` writes the stats Game Stats Widgets show, and `bot.rest.partner_sdk` gets provisional account tokens and moderates direct messages.

## What bostie does not model yet

Everything bostie does not model yet stays reachable:

- `bot.http.request("GET", "/guilds/{guild_id}/some-new-route", guild_id=...)` calls any endpoint, such as one Discord added after this release, and returns its JSON.
- `bot.gateway.send(op=..., d=...)` sends any opcode: op 3 to every shard, a payload with a `guild_id` to that guild's shard, one with `guild_ids` to each of their shards with its share of the ids, anything else to shard 0. `bot.gateway.shard(id).send(...)` picks the shard yourself.
- `@bot.listen_raw("EVENT_NAME")` receives any dispatch as a `RawEvent`.
- `ui.raw({...})` puts a component that `ui` has no builder for into a row, section, container, label or modal.
- An `InteractionCreate` listener gets every interaction, routed or not. A type bostie does not model arrives as an `UnknownInteraction`, with the payload in `raw`. An interaction that no command, route or wait takes logs a warning, as Discord then shows "The application did not respond", unless such a listener is registered.
