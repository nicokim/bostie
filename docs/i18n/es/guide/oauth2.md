---
source: a95f1211f80aa1fe37d568699ddca680b3c3ed9d
description: "OAuth2 de Discord con bostie: URL de autorización con PKCE, intercambio del código y renovación, llamadas a la API con el token del usuario y roles vinculados."
---

# OAuth2 y roles vinculados

![Bostie sostiene en alto una llave brillante](../assets/mascot/bostie-oauth2.webp){ align=right width="180" loading=lazy }

OAuth2 permite que un usuario le dé a tu aplicación acceso a su cuenta: su perfil, sus servidores, sus cuentas vinculadas, el derecho a agregarlo a un servidor o una conexión de rol para los roles vinculados. bostie construye las URL de autorización y llama a la API con el token que otorga el usuario.

```python
--8<-- "docs/snippets/oauth2/oauth_flow.py"
```

## URL de autorización { #authorization-urls }

`oauth2.authorize_url(client_id, scopes=[...], redirect_uri=..., state=...)` devuelve la URL que le pide al usuario que otorgue los `scopes` y lo redirige de vuelta a `redirect_uri` con un `code`. Los scopes son miembros de `OAuth2Scope`, como `OAuth2Scope.IDENTIFY` para `identify`, uno por cada scope que documenta Discord; una cadena simple sirve para un scope que Discord agregue antes de que bostie lo nombre. Comprueba que la redirección trae el `state` que enviaste, para que otro sitio no pueda hacer que el usuario inicie sesión con su código. También acepta:

- `code_challenge`, el desafío S256 de un verificador PKCE, que luego `exchange_code` envía como `code_verifier`; el snippet calcula uno;
- `prompt="none"` para omitir la pantalla de consentimiento si el usuario ya otorgó los scopes, o `"consent"` para volver a mostrarla;
- `integration_type` para instalar la aplicación en un servidor o en el usuario.

bostie solo construye la concesión con código de autorización. Discord desaconseja la concesión implícita, que pone el token en la URL de redirección y no da refresh token, y recomienda en su lugar la concesión con código y PKCE.

`oauth2.invite_url(client_id, permissions=..., scopes=[...])` devuelve el enlace de instalación de la aplicación, con `integration_type`, `guild_id` para preseleccionar un servidor y `disable_guild_select`. Lo que no indique sale de los ajustes de instalación de la aplicación en el Developer Portal.

## Tokens { #tokens }

`BearerClient()` sin token solo llega a `rest.oauth2`:

| Método | Qué hace |
|---|---|
| `exchange_code(client_id=, client_secret=, code=, redirect_uri=, code_verifier=None)` | convierte el código de la redirección en un `AccessToken` |
| `refresh(client_id=, client_secret=, refresh_token=)` | un token nuevo antes de que venza el anterior |
| `revoke(client_id=, client_secret=, token=)` | termina la autorización de un token y de todos los tokens que emitió |
| `exchange_client_credentials(client_id=, client_secret=, scopes=[...])` | un token para el propietario de la aplicación, o para su equipo |

Un `AccessToken` tiene `access_token`, `token_type`, `expires_in` en segundos, `scope` (los scopes otorgados, separados por espacios) y `refresh_token`. Una concesión con código que incluyó el scope `bot` también devuelve el `guild` al que se unió el bot, y una con `webhook.incoming`, el `webhook` que creó. El `repr` de un token oculta los secretos, así que registrarlo en el log no los filtra.

## Llamar a la API como el usuario { #calling-the-api-as-the-user }

`async with BearerClient(access_token) as user:` llega a lo que permiten los scopes del token:

| `user.rest.` | Métodos | Scope |
|---|---|---|
| `users` | `fetch_current` | `identify` |
| `users` | `list_connections` | `connections` |
| `users` | `fetch_role_connection`, `update_role_connection`, `delete_role_connection` | `role_connections.write` |
| `guilds` | `list` | `guilds` |
| `members` | `fetch_current(guild_id)` | `guilds.members.read` |
| `commands` | `fetch_permissions`, `list_permissions`, `edit_permissions` | `applications.commands.permissions.update` para editar |
| `oauth2` | `fetch_authorization`, que dice qué otorga el token y a quién | cualquiera |
| `lobbies` | el lado del usuario de los [lobbies del Social SDK](platform.md#social-sdk) | `sdk.social_layer` |

Con el scope `guilds.join`, el bot puede agregar al usuario a un servidor en el que está: `await bot.rest.members.add(guild_id, user_id, access_token=token.access_token)`, que devuelve el nuevo `Member`, o `None` si el usuario ya era miembro.

## Roles vinculados { #linked-roles }

Un rol vinculado es un rol del servidor que los usuarios obtienen al cumplir condiciones sobre una conexión con tu aplicación, como "nivel 10 o más". La aplicación declara una vez los valores que informa y luego informa los valores de cada usuario con su token:

1. Registra hasta 5 registros de metadatos con `bot.rest.application.update_role_connection_metadata(application_id, [...])`, cada uno un `ApplicationRoleConnectionMetadata` con un `type`, `key`, `name` y `description`. `register_metadata`, en el snippet, lo hace con un `RESTClient`.
2. Configura la **Linked Roles Verification URL** de la aplicación en el Developer Portal para que apunte a una página que inicie el flujo de OAuth2 con el scope `role_connections.write`.
3. Después del intercambio, llama a `user.rest.users.update_role_connection(application_id, platform_name=..., platform_username=..., metadata={"level": "12"})`. Los valores de los metadatos son cadenas, tal como los acepta la API de Discord.

El [ejemplo de roles vinculados](../examples.md#linked-roles) es una página de verificación completa. `fmt.guild_navigation("linked-roles", role_id)` enlaza en el chat el rol vinculado de un servidor.
