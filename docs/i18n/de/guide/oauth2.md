---
source: a95f1211f80aa1fe37d568699ddca680b3c3ed9d
description: "Discord-OAuth2 mit bostie: Autorisierungs-URLs mit PKCE, Code-Austausch und Token-Refresh, API-Aufrufe mit Benutzertoken und Metadaten verknüpfter Rollen."
---

# OAuth2 und verknüpfte Rollen

![Bostie hält einen glänzenden Schlüssel hoch](../assets/mascot/bostie-oauth2.webp){ align=right width="180" loading=lazy }

Mit OAuth2 gewährt ein Benutzer deiner App Zugriff auf sein Konto: sein Profil, seine Server, seine verknüpften Konten, das Recht, ihn einem Server hinzuzufügen, oder eine Rollenverbindung für verknüpfte Rollen. bostie baut die Autorisierungs-URLs und ruft die API mit dem Token auf, das der Benutzer gewährt.

```python
--8<-- "docs/snippets/oauth2/oauth_flow.py"
```

## Autorisierungs-URLs { #authorization-urls }

`oauth2.authorize_url(client_id, scopes=[...], redirect_uri=..., state=...)` gibt die URL zurück, die den Benutzer bittet, `scopes` zu gewähren, und mit einem `code` zurück zu `redirect_uri` weiterleitet. Scopes sind `OAuth2Scope`-Mitglieder, etwa `OAuth2Scope.IDENTIFY` für `identify`, eines für jeden Scope, den Discord dokumentiert; ein einfacher String funktioniert für einen Scope, den Discord hinzufügt, bevor bostie ihn benennt. Prüfe, dass die Weiterleitung den `state` mitbringt, den du gesendet hast, damit keine andere Seite den Benutzer mit ihrem Code anmelden kann. Es nimmt außerdem:

- `code_challenge`, die S256-Challenge eines PKCE-Verifiers, den `exchange_code` dann als `code_verifier` sendet; das Snippet berechnet einen;
- `prompt="none"`, um den Zustimmungsbildschirm für einen Benutzer zu überspringen, der die Scopes schon gewährt hat, oder `"consent"`, um ihn erneut anzuzeigen;
- `integration_type`, um die App auf einem Server oder für den Benutzer zu installieren.

bostie baut nur den Authorization Code Grant. Discord rät vom Implicit Grant ab, der das Token in die Weiterleitungs-URL setzt und kein Refresh-Token liefert, und empfiehlt stattdessen den Code Grant mit PKCE.

`oauth2.invite_url(client_id, permissions=..., scopes=[...])` gibt den Installationslink der App zurück, mit `integration_type`, `guild_id`, um einen Server vorauszuwählen, und `disable_guild_select`. Was es weglässt, kommt aus den Installationseinstellungen der App im Developer Portal.

## Tokens { #tokens }

`BearerClient()` ohne Token erreicht nur `rest.oauth2`:

| Methode | Bewirkt |
|---|---|
| `exchange_code(client_id=, client_secret=, code=, redirect_uri=, code_verifier=None)` | wandelt den Code aus der Weiterleitung in ein `AccessToken` um |
| `refresh(client_id=, client_secret=, refresh_token=)` | ein neues Token, bevor das alte abläuft |
| `revoke(client_id=, client_secret=, token=)` | beendet die Autorisierung eines Tokens und jedes Tokens, das es ausgestellt hat |
| `exchange_client_credentials(client_id=, client_secret=, scopes=[...])` | ein Token für den Eigentümer der App oder ihr Team |

Ein `AccessToken` hat `access_token`, `token_type`, `expires_in` in Sekunden, `scope` (die gewährten Scopes, durch Leerzeichen getrennt) und `refresh_token`. Ein Code Grant, der den Scope `bot` enthielt, gibt außerdem den `guild` zurück, dem der Bot beigetreten ist, und einer mit `webhook.incoming` den `webhook`, den er erstellt hat. Die `repr` eines Tokens verbirgt die Geheimnisse, sodass das Loggen eines Tokens es nicht preisgibt.

## Die API als der Benutzer aufrufen { #calling-the-api-as-the-user }

`async with BearerClient(access_token) as user:` erreicht, was die Scopes des Tokens erlauben:

| `user.rest.` | Methoden | Scope |
|---|---|---|
| `users` | `fetch_current` | `identify` |
| `users` | `list_connections` | `connections` |
| `users` | `fetch_role_connection`, `update_role_connection`, `delete_role_connection` | `role_connections.write` |
| `guilds` | `list` | `guilds` |
| `members` | `fetch_current(guild_id)` | `guilds.members.read` |
| `commands` | `fetch_permissions`, `list_permissions`, `edit_permissions` | `applications.commands.permissions.update` zum Bearbeiten |
| `oauth2` | `fetch_authorization`, das sagt, was das Token wem gewährt | beliebig |
| `lobbies` | die Benutzerseite von [Social-SDK-Lobbys](platform.md#social-sdk) | `sdk.social_layer` |

Mit dem Scope `guilds.join` kann der Bot den Benutzer einem Server hinzufügen, auf dem er selbst ist: `await bot.rest.members.add(guild_id, user_id, access_token=token.access_token)`, das das neue `Member` zurückgibt, oder `None`, wenn der Benutzer schon Mitglied war.

## Verknüpfte Rollen { #linked-roles }

Eine verknüpfte Rolle ist eine Serverrolle, die Benutzer bekommen, indem sie Bedingungen an eine Verbindung zu deiner App erfüllen, etwa „Level mindestens 10“. Die App deklariert einmal die Werte, die sie meldet, und meldet dann die Werte jedes Benutzers mit seinem Token:

1. Registriere bis zu 5 Metadatensätze mit `bot.rest.application.update_role_connection_metadata(application_id, [...])`, jeder ein `ApplicationRoleConnectionMetadata` mit `type`, `key`, `name` und `description`. `register_metadata` im Snippet tut das mit einem `RESTClient`.
2. Setz die **Linked Roles Verification URL** der App im Developer Portal auf eine Seite, die den OAuth2-Ablauf mit dem Scope `role_connections.write` startet.
3. Ruf nach dem Austausch `user.rest.users.update_role_connection(application_id, platform_name=..., platform_username=..., metadata={"level": "12"})` auf. Metadatenwerte sind Strings, so wie Discords API sie annimmt.

Das [Beispiel für verknüpfte Rollen](../examples.md#linked-roles) ist eine vollständige Verifizierungsseite. `fmt.guild_navigation("linked-roles", role_id)` verlinkt die verknüpften Rollen eines Servers im Chat.
