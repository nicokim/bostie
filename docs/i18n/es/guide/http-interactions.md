---
source: d24ff7127c79eec4345b1d42ca0265273465a567
description: "Recibe interacciones y eventos de webhook de Discord por HTTP con una aplicación ASGI en lugar del gateway, verifica las firmas o usa tu propio servidor web."
---

# Interacciones por HTTP y eventos de webhook

Discord puede enviar las interacciones a una URL de tu aplicación en lugar de al gateway, y algunos eventos, los eventos de webhook, los envía solo a una URL. `ASGIApp` sirve los dos desde el mismo bot, con sus comandos, rutas y listeners sin cambios.

```python
--8<-- "docs/snippets/http/http_bot.py"
```

`bostie.ASGIApp(bot)` es una aplicación ASGI: sirve `app` con cualquier servidor ASGI, detrás de HTTPS. Luego, en el Developer Portal, configura la **Interactions Endpoint URL** de la aplicación y, para los eventos de webhook, su **Webhook Events URL**, con los tipos de evento que quieras, apuntando a donde escucha. Discord comprueba un endpoint cuando lo guardas, con un PING y una solicitud con una firma incorrecta, y `ASGIApp` responde a los dos como espera Discord.

## Qué hace { #what-it-does }

Cuando arranca el servidor, mediante el lifespan de ASGI, la aplicación llama a `bot.open()`, salvo que el bot ya esté ejecutándose en el gateway, y eso:

1. abre el cliente HTTP del bot;
2. obtiene la aplicación, para conocer su id y la clave pública que firma las solicitudes de Discord, y el usuario del bot;
3. envía `Starting`, sincroniza los comandos e inicia las [tareas en segundo plano](tasks.md).

Un fallo en ese punto detiene el arranque del servidor con el error. Cuando el servidor se apaga, la aplicación cierra el bot si fue ella quien lo inició.

Cada solicitud se comprueba antes que nada: solo se acepta `POST` (si no, 405), los cuerpos tienen un límite de 4 MiB (413), y una solicitud cuyo `X-Signature-Ed25519` no se verifica con la clave pública recibe 401. Después:

- Un PING de interacción recibe el PONG que espera Discord.
- Cualquier otra interacción se responde con 202 y se le pasa al bot, que ejecuta su handler de comando, de ruta o de modal como lo haría con el gateway; el handler responde a través de la API REST de Discord, con `ctx.respond` y los demás. El [diferimiento automático](responses.md#deferring) funciona igual.
- Un evento de webhook llega a los listeners del bot y recibe 204, igual que el PING que Discord envía a la Webhook Events URL.

`ASGIApp(bot, token=..., public_key=...)` acepta el token, o si no `BOSTIE_TOKEN`, y la clave pública, o si no la que informa la aplicación.

## Con o sin el gateway { #with-or-without-the-gateway }

Si se sirve sola, la aplicación no tiene conexión con el gateway: no hay eventos del gateway, ni actualizaciones de la caché, ni voz. Funcionan los comandos, los componentes, los modales, REST y los eventos de webhook. Para tener las dos cosas, inicia también el bot en el gateway, en el mismo event loop, y la aplicación usará entonces el bot en ejecución; las interacciones llegan por HTTP y el gateway sigue entregando eventos.

## Eventos de webhook { #webhook-events }

| Evento | Se envía cuando |
|---|---|
| `ApplicationAuthorized` | un usuario agregó la aplicación a un servidor o a su cuenta |
| `ApplicationDeauthorized` | un usuario quitó la aplicación, o vencieron sus tokens de OAuth2 |
| `EntitlementCreate`, `EntitlementUpdate`, `EntitlementDelete` | cambió un entitlement |
| `QuestUserEnrollment` | se agregó un usuario a una Misión; Discord lo documenta pero todavía no se lo envía a las aplicaciones |
| `LobbyMessageCreate`, `LobbyMessageUpdate`, `LobbyMessageDelete` | se envió, editó o eliminó un mensaje en un lobby del Social SDK |
| `GameDirectMessageCreate`, `GameDirectMessageUpdate`, `GameDirectMessageDelete` | cambió un mensaje directo en una sesión del Social SDK |

Escúchalos con `@bot.listen` igual que los eventos del gateway. Un tipo de evento de webhook que bostie no modela llega como un `RawEvent`.

## En tu propio servidor web { #in-your-own-web-server }

Las dos piezas que usa `ASGIApp` son públicas, para un framework web que ya tengas en marcha:

```python
--8<-- "docs/snippets/http/own_server.py"
```

`verify_signature(public_key, signature, timestamp, body)` comprueba las cabeceras `X-Signature-Ed25519` y `X-Signature-Timestamp` de una solicitud con la clave pública hexadecimal de la aplicación; responde 401 cuando devuelve `False`. `decode_webhook_event(body)` devuelve el evento tipado de una solicitud de Webhook Events, un `RawEvent` para un tipo que bostie no modela, o `None` para el PING. Para las interacciones en tu propio servidor, el [ejemplo de interacciones por HTTP](../examples.md#http-interactions) muestra la vía ASGI, que es la que admite bostie.
