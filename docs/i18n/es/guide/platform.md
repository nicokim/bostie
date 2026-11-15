---
source: 03a349670a189a38d3b3491aba4b54e6af1ec4e9
---

# Actividades, monetización y el Social SDK

Las aplicaciones de Discord son más que bots en servidores. Esta página cubre las aplicaciones instaladas en la cuenta de los usuarios, las actividades, la venta de SKU y el lado de servidor del Social SDK.

```python
--8<-- "docs/snippets/apps/premium.py"
```

## Aplicaciones instaladas en la cuenta del usuario { #apps-installed-to-users }

Una aplicación se puede instalar en la cuenta de un usuario además de en un servidor, y entonces sus comandos funcionan dondequiera que esté ese usuario, incluidos los MD con amigos y los servidores sin el bot. Activa la instalación de usuario en la página **Installation** del Developer Portal y luego deja que los comandos la usen con `integration_types` y `contexts`, como hace el snippet para todo el bot ([Registrar comandos](registration.md#where-commands-can-be-used)).

En ese contexto el bot no está en el canal: no hay entrada en la caché ni eventos del gateway, y la API REST rechaza ahí la mayoría de las llamadas. `ctx.interaction.authorizing_integration_owners` indica qué instalaciones permitieron la interacción, `ctx.interaction.context` dónde ocurrió y `ctx.interaction.app_permissions` qué puede hacer la aplicación. Las respuestas funcionan como siempre, y Discord permite 5 mensajes de seguimiento para una aplicación instalada solo en la cuenta del usuario; bostie advierte en el sexto.

## Actividades { #activities }

Una actividad es una aplicación web que se ejecuta dentro de Discord. bostie cubre el lado del bot:

- `@bot.entry_point(name)` registra el comando de punto de entrada que muestra el App Launcher, como explica [Comandos de usuario, de mensaje y de punto de entrada](context-menus.md#the-entry-point-command).
- `await ctx.launch_activity()` abre la actividad desde un comando o un componente y devuelve el id de su instancia.
- `await bot.rest.application.fetch_activity_instance(application_id, instance_id)` devuelve una `ActivityInstance`: dónde se ejecuta y los ids de los usuarios que hay en ella.

El [ejemplo de actividad](../examples.md#activity) registra un punto de entrada y un comando slash que la abren los dos.

## Monetización { #monetization }

Una aplicación con la monetización habilitada vende SKU: suscripciones, artículos duraderos y consumibles. Las compras del usuario son entitlements.

- `ctx.interaction.entitlements` enumera los entitlements del usuario y del servidor de una interacción, así que un comando puede comprobar una compra sin hacer una solicitud.
- `ui.premium_button(sku_id)` muestra un botón que abre la compra de un SKU; nunca llega al bot. `fmt.store(application_id, sku_id=None)` enlaza la página de la tienda de la aplicación, o un SKU, que Discord muestra como una tarjeta.
- `bot.rest.monetization` enumera los SKU de la aplicación (`list_skus`), recorre los entitlements con filtros por usuario, servidor y SKU (`list_entitlements`), obtiene uno y marca un consumible como usado (`consume_entitlement`). También enumera y obtiene las suscripciones de un SKU.
- `create_test_entitlement(application_id, sku_id=..., owner_id=..., owner_type=1)` otorga un entitlement de prueba gratuito a un servidor (1) o a un usuario (2), y `delete_test_entitlement` lo quita.
- Los eventos `EntitlementCreate`, `EntitlementUpdate` y `EntitlementDelete`, y `SubscriptionCreate`, `SubscriptionUpdate` y `SubscriptionDelete`, informan los cambios por el gateway; los eventos de entitlements también llegan como [eventos de webhook](http-interactions.md#webhook-events). Un entitlement de prueba llega como un `PartialEntitlement`, sin las fechas de uno real.

## Social SDK { #social-sdk }

El Social SDK de Discord se ejecuta dentro de los juegos. Su lado de servidor es REST:

- `bot.rest.lobbies` crea lobbies, edita sus metadatos y sus miembros (`add_member`, `bulk_update_members` para hasta 25 a la vez, `remove_member`), los elimina, invita a un usuario al canal vinculado del lobby y define metadatos de moderación en los mensajes del lobby.
- `user.rest.lobbies`, en un [`BearerClient`](oauth2.md#calling-the-api-as-the-user) con el token de un jugador, crea un lobby o se une a uno por su secreto (`create_or_join`), lo abandona (`leave`), vincula y desvincula un canal de Discord (`link_channel`, `unlink_channel`), envía y enumera mensajes (`send_message`, `list_messages`) e invita al propio jugador al canal vinculado (`create_invite_for_self`).
- `bot.rest.partner_sdk` intercambia tokens: un token provisional para un jugador del sistema de cuentas propio del juego, un token a cambio del token de un proveedor de identidad externo, el token de un juego hijo a cambio del de un editor; también separa cuentas provisionales y define metadatos de moderación en los mensajes directos.
- `bot.rest.identities` enumera, obtiene y actualiza las identidades y los perfiles de aplicación de los usuarios que vincularon una cuenta externa.

Los mensajes de los lobbies y los mensajes directos dentro del juego llegan como los [eventos de webhook](http-interactions.md#webhook-events) `LobbyMessage*` y `GameDirectMessage*`.
