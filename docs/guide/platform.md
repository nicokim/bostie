# Activities, monetization and the Social SDK

Discord apps are more than bots in guilds. This page covers apps installed to users, Activities, selling SKUs, and the Social SDK's server side.

```python
--8<-- "docs/snippets/apps/premium.py"
```

## Apps installed to users

An app can be installed to a user's account as well as to a guild, and its commands then work wherever that user is, including DMs with friends and guilds without the bot. Turn on the user install in the Developer Portal's **Installation** page, then let commands use it with `integration_types` and `contexts`, as the snippet does for the whole bot ([Registering commands](registration.md#where-commands-can-be-used)).

In such a context the bot is not in the channel: there is no cache entry, no gateway event, and the REST API refuses most calls there. `ctx.interaction.authorizing_integration_owners` says which installs let the interaction happen, `ctx.interaction.context` where it happened, and `ctx.interaction.app_permissions` what the app may do. Answers work as usual, and Discord allows 5 follow-ups for an app installed only to the user; bostie warns at the sixth.

## Activities

An Activity is a web app that runs inside Discord. bostie covers the bot side:

- `@bot.entry_point(name)` registers the entry point command the App Launcher shows, as [User, message and entry point commands](context-menus.md#the-entry-point-command) explains.
- `await ctx.launch_activity()` opens the Activity from a command or a component and returns the id of its instance.
- `await bot.rest.application.fetch_activity_instance(application_id, instance_id)` returns an `ActivityInstance`: where it runs and the ids of the users in it.

The [Activity example](../examples.md#activity) registers an entry point and a slash command that both launch it.

## Monetization

An app with monetization enabled sells SKUs: subscriptions, durable items and consumables. The user's purchases are entitlements.

- `ctx.interaction.entitlements` lists the entitlements of the user and guild of an interaction, so a command can check for a purchase without a request.
- `ui.premium_button(sku_id)` shows a button that opens the purchase of an SKU; it never reaches the bot. `fmt.store(application_id, sku_id=None)` links the app's store page, or one SKU, which Discord shows as a card.
- `bot.rest.monetization` lists the app's SKUs (`list_skus`), walks entitlements with filters by user, guild and SKU (`list_entitlements`), fetches one, and marks a consumable used (`consume_entitlement`). It also lists and fetches an SKU's subscriptions.
- `create_test_entitlement(application_id, sku_id=..., owner_id=..., owner_type=1)` grants a free test entitlement to a guild (1) or a user (2), and `delete_test_entitlement` removes it.
- The `EntitlementCreate`, `EntitlementUpdate` and `EntitlementDelete` events, and `SubscriptionCreate`, `SubscriptionUpdate` and `SubscriptionDelete`, report changes over the gateway; the entitlement events also arrive as [webhook events](http-interactions.md#webhook-events). A test entitlement arrives as a `PartialEntitlement`, without the dates of a real one.

## Social SDK

The Discord Social SDK runs inside games. Its server side is REST:

- `bot.rest.lobbies` creates lobbies, edits their metadata and members (`add_member`, `bulk_update_members` for up to 25 at once, `remove_member`), deletes them, invites a user into the lobby's linked channel, and sets moderation metadata on lobby messages.
- `user.rest.lobbies`, on a [`BearerClient`](oauth2.md#calling-the-api-as-the-user) with a player's token, creates or joins a lobby by secret (`create_or_join`), leaves it (`leave`), links and unlinks a Discord channel (`link_channel`, `unlink_channel`), sends and lists messages (`send_message`, `list_messages`), and invites the player themselves into the linked channel (`create_invite_for_self`).
- `bot.rest.partner_sdk` exchanges tokens: a provisional token for a player of the game's own account system, a token for an external identity provider's token, a child game's token for a publisher's; it also unmerges provisional accounts and sets moderation metadata on direct messages.
- `bot.rest.identities` lists, fetches and updates the application identities and profiles of users who linked an external account.

Lobby messages and in-game direct messages arrive as the `LobbyMessage*` and `GameDirectMessage*` [webhook events](http-interactions.md#webhook-events).
