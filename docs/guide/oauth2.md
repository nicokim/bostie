# OAuth2 and linked roles

OAuth2 lets a user grant your app access to their account: their profile, their guilds, their linked accounts, the right to add them to a guild, or a role connection for linked roles. bostie builds the authorization URLs and calls the API with the token the user grants.

```python
--8<-- "docs/snippets/oauth2/oauth_flow.py"
```

## Authorization URLs

`oauth2.authorize_url(client_id, scopes=[...], redirect_uri=..., state=...)` returns the URL that asks the user to grant `scopes` and redirects back to `redirect_uri` with a `code`. Check that the redirect carries the `state` you sent, so another site cannot make the user log in with its code. It also takes:

- `code_challenge`, the S256 challenge of a PKCE verifier, which `exchange_code` then sends as `code_verifier`; the snippet computes one;
- `prompt="none"` to skip the consent screen for a user who already granted the scopes, or `"consent"` to show it again;
- `integration_type` to install the app to a guild or to the user;
- `response_type="token"` for the implicit grant, which redirects with a token instead of a code.

`oauth2.invite_url(client_id, permissions=..., scopes=[...])` returns the install link of the app, with `integration_type`, `guild_id` to preselect a guild, and `disable_guild_select`. Whatever it leaves out comes from the app's installation settings in the Developer Portal.

## Tokens

`BearerClient()` without a token reaches only `rest.oauth2`:

| Method | Does |
|---|---|
| `exchange_code(client_id=, client_secret=, code=, redirect_uri=, code_verifier=None)` | turns the code from the redirect into an `AccessToken` |
| `refresh(client_id=, client_secret=, refresh_token=)` | a new token before the old one expires |
| `revoke(client_id=, client_secret=, token=)` | ends the authorization of a token and every token it issued |
| `exchange_client_credentials(client_id=, client_secret=, scopes=[...])` | a token for the app's owner, or its team |

An `AccessToken` has `access_token`, `token_type`, `expires_in` in seconds, `scope` (the granted scopes, separated by spaces) and `refresh_token`. A code grant that included the `bot` scope also returns the `guild` the bot joined, and one with `webhook.incoming` the `webhook` it created. A token's `repr` hides the secrets, so logging one does not leak it.

## Calling the API as the user

`async with BearerClient(access_token) as user:` reaches what the token's scopes allow:

| `user.rest.` | Methods | Scope |
|---|---|---|
| `users` | `fetch_current` | `identify` |
| `users` | `list_connections` | `connections` |
| `users` | `fetch_role_connection`, `update_role_connection`, `delete_role_connection` | `role_connections.write` |
| `guilds` | `list` | `guilds` |
| `members` | `fetch_current(guild_id)` | `guilds.members.read` |
| `commands` | `fetch_permissions`, `list_permissions`, `edit_permissions` | `applications.commands.permissions.update` to edit |
| `oauth2` | `fetch_authorization`, which says what the token grants and to whom | any |
| `lobbies` | the user's side of [Social SDK lobbies](platform.md#social-sdk) | `sdk.social_layer` |

With the `guilds.join` scope, the bot can add the user to a guild it is in: `await bot.rest.members.add(guild_id, user_id, access_token=token.access_token)`, which returns the new `Member`, or `None` when the user was a member already.

## Linked roles

A linked role is a guild role that users get by meeting conditions on a connection to your app, such as "level at least 10". The app declares the values it reports once, then reports each user's values with their token:

1. Register up to 5 metadata records with `bot.rest.application.update_role_connection_metadata(application_id, [...])`, each an `ApplicationRoleConnectionMetadata` with a `type`, `key`, `name` and `description`. `register_metadata` in the snippet does that with a `RESTClient`.
2. Set the app's **Linked Roles Verification URL** in the Developer Portal to a page that starts the OAuth2 flow with the `role_connections.write` scope.
3. After the exchange, call `user.rest.users.update_role_connection(application_id, platform_name=..., platform_username=..., metadata={"level": "12"})`. Metadata values are strings, as Discord's API takes them.

The [linked roles example](../examples.md#linked-roles) is a complete verification page. `fmt.guild_navigation("linked-roles", role_id)` links a guild's linked role in chat.
