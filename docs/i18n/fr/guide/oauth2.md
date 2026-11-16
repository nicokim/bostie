---
source: a95f1211f80aa1fe37d568699ddca680b3c3ed9d
description: "OAuth2 Discord avec bostie : URL d'autorisation avec PKCE, échange du code, rafraîchissement du token, appels API avec le token d'un utilisateur, rôles liés."
---

# OAuth2 et rôles liés

![Bostie brandissant une clé étincelante](../assets/mascot/bostie-oauth2.webp){ align=right width="180" loading=lazy }

OAuth2 permet à un utilisateur d'accorder à votre application l'accès à son compte : son profil, ses serveurs, ses comptes connectés, le droit de l'ajouter à un serveur, ou une connexion de rôle pour les rôles liés. bostie construit les URL d'autorisation et appelle l'API avec le token que l'utilisateur accorde.

```python
--8<-- "docs/snippets/oauth2/oauth_flow.py"
```

## URL d'autorisation { #authorization-urls }

`oauth2.authorize_url(client_id, scopes=[...], redirect_uri=..., state=...)` renvoie l'URL qui demande à l'utilisateur d'accorder les `scopes` et le redirige vers `redirect_uri` avec un `code`. Les scopes sont des membres d'`OAuth2Scope`, comme `OAuth2Scope.IDENTIFY` pour `identify`, un pour chaque scope que Discord documente ; une simple chaîne fonctionne pour un scope que Discord ajoute avant que bostie ne le nomme. Vérifiez que la redirection porte le `state` que vous avez envoyé, pour qu'un autre site ne puisse pas faire connecter l'utilisateur avec son propre code. La fonction accepte aussi :

- `code_challenge`, le challenge S256 d'un verifier PKCE, que `exchange_code` envoie ensuite comme `code_verifier` ; l'extrait en calcule un ;
- `prompt="none"` pour sauter l'écran de consentement pour un utilisateur qui a déjà accordé les scopes, ou `"consent"` pour l'afficher de nouveau ;
- `integration_type` pour installer l'application sur un serveur ou pour l'utilisateur.

bostie ne construit que l'autorisation par code (authorization code grant). Discord déconseille l'autorisation implicite (implicit grant), qui place le token dans l'URL de redirection et ne fournit pas de token de rafraîchissement, et recommande à la place l'autorisation par code avec PKCE.

`oauth2.invite_url(client_id, permissions=..., scopes=[...])` renvoie le lien d'installation de l'application, avec `integration_type`, `guild_id` pour présélectionner un serveur, et `disable_guild_select`. Tout ce qu'elle omet provient des paramètres d'installation de l'application dans le Developer Portal.

## Tokens { #tokens }

`BearerClient()` sans token n'atteint que `rest.oauth2` :

| Méthode | Effet |
|---|---|
| `exchange_code(client_id=, client_secret=, code=, redirect_uri=, code_verifier=None)` | échange le code de la redirection contre un `AccessToken` |
| `refresh(client_id=, client_secret=, refresh_token=)` | un nouveau token avant que l'ancien n'expire |
| `revoke(client_id=, client_secret=, token=)` | met fin à l'autorisation d'un token et de tous les tokens qu'il a émis |
| `exchange_client_credentials(client_id=, client_secret=, scopes=[...])` | un token pour le propriétaire de l'application, ou son équipe |

Un `AccessToken` a `access_token`, `token_type`, `expires_in` en secondes, `scope` (les scopes accordés, séparés par des espaces) et `refresh_token`. Une autorisation par code qui incluait le scope `bot` renvoie aussi le `guild` que le bot a rejoint, et une autorisation avec `webhook.incoming` le `webhook` qu'elle a créé. Le `repr` d'un token masque les secrets : le journaliser ne les divulgue donc pas.

## Appeler l'API au nom de l'utilisateur { #calling-the-api-as-the-user }

`async with BearerClient(access_token) as user:` atteint ce que permettent les scopes du token :

| `user.rest.` | Méthodes | Scope |
|---|---|---|
| `users` | `fetch_current` | `identify` |
| `users` | `list_connections` | `connections` |
| `users` | `fetch_role_connection`, `update_role_connection`, `delete_role_connection` | `role_connections.write` |
| `guilds` | `list` | `guilds` |
| `members` | `fetch_current(guild_id)` | `guilds.members.read` |
| `commands` | `fetch_permissions`, `list_permissions`, `edit_permissions` | `applications.commands.permissions.update` pour modifier |
| `oauth2` | `fetch_authorization`, qui indique ce que le token accorde et à qui | n'importe lequel |
| `lobbies` | le côté utilisateur des [lobbies du Social SDK](platform.md#social-sdk) | `sdk.social_layer` |

Avec le scope `guilds.join`, le bot peut ajouter l'utilisateur à un serveur dont il fait partie : `await bot.rest.members.add(guild_id, user_id, access_token=token.access_token)`, qui renvoie le nouveau `Member`, ou `None` si l'utilisateur était déjà membre.

## Rôles liés { #linked-roles }

Un rôle lié est un rôle de serveur que les utilisateurs obtiennent en remplissant des conditions sur une connexion à votre application, comme « niveau 10 au moins ». L'application déclare une fois les valeurs qu'elle rapporte, puis rapporte les valeurs de chaque utilisateur avec son token :

1. Enregistrez jusqu'à 5 entrées de métadonnées avec `bot.rest.application.update_role_connection_metadata(application_id, [...])`, chacune étant un `ApplicationRoleConnectionMetadata` avec un `type`, une `key`, un `name` et une `description`. `register_metadata`, dans l'extrait, le fait avec un `RESTClient`.
2. Dans le Developer Portal, réglez la **Linked Roles Verification URL** de l'application sur une page qui lance le flux OAuth2 avec le scope `role_connections.write`.
3. Après l'échange, appelez `user.rest.users.update_role_connection(application_id, platform_name=..., platform_username=..., metadata={"level": "12"})`. Les valeurs de métadonnées sont des chaînes, comme l'API de Discord les accepte.

L'[exemple de rôles liés](../examples.md#linked-roles) est une page de vérification complète. `fmt.guild_navigation("linked-roles", role_id)` crée un lien vers le rôle lié d'un serveur dans le chat.
