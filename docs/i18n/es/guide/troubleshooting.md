---
source: 14575d90a6a022a3c0cbfce22c048b404bcf4957
---

# Solución de problemas

![Bostie piensa hasta que el signo de interrogación se convierte en una bombilla](../assets/mascot/bostie-thinking.webp){ align=right width="180" loading=lazy }

bostie deja una advertencia en el log por cada error que puede detectar. Esta página las enumera según lo que observas, con la línea del log que hay que buscar y la solución.

## Un comando dice que la aplicación no respondió { #a-command-says-the-application-did-not-respond }

| Línea del log | Causa | Solución |
|---|---|---|
| `nothing handles interaction ... (command 'name')` | Discord todavía tiene un comando que el bot en ejecución no define, a menudo después de un cambio de nombre con `auto_sync=False`, o el comando vive en otro proceso | sincroniza los comandos ([Registrar comandos](registration.md)) |
| `nothing handles interaction ... (custom_id '...')` | ninguna plantilla de `on_component` ni de `on_modal` coincide con el custom id | compara la plantilla con los ids que envías; constrúyelos con `route.id(...)` |
| `... returned without answering` | el handler terminó sin `respond`, `defer` ni `modal` | responde en todos los caminos, incluidos los `return` anticipados |
| `... returned after deferring without answering` | difirió y nunca respondió, así que el «está pensando…» sigue hasta que vence el token | llama a `respond` después del trabajo lento |
| `interaction ... reached its handler ... s after it was received; something is blocking the event loop` | código bloqueante en un handler o un listener retuvo el event loop más allá de los 3 segundos de Discord | mueve el trabajo a `asyncio.to_thread`, o baja `auto_defer` |
| `interaction ... was replayed ... s after it was created; its token is dead` | la interacción llegó en la reproducción de una sesión reanudada, demasiado tarde para responder | nada; el usuario puede volver a ejecutarla |

Una excepción sin manejar en un handler se registra en el log con su traceback, y el usuario ve "Something went wrong.": agrega un [hook de errores](checks.md#error-hooks) para responder algo mejor.

## Los eventos no llegan { #events-do-not-arrive }

- `... listens to EVENT, which needs the INTENT intent; add it to Bot(intents=...)`: el bot no pidió el intent. Agrégalo y, si es un intent privilegiado, actívalo también en el Developer Portal.
- `start()` lanza `PrivilegedIntentsRequired`: el bot pidió un intent privilegiado que el portal no permite. Actívalo en **Bot** → **Privileged Gateway Intents**, o quítalo de `intents`.
- `message.content` está vacío en los servidores: falta el intent `MESSAGE_CONTENT`.
- `EVENT has an unexpected shape (...); typed listeners skip it`: Discord envió un payload que no encaja con la clase del evento, registrado una vez por nombre de evento. Los listeners de `RawEvent` lo siguen recibiendo; por favor, reporta la línea.
- `old` es `None` en los eventos de actualización: la caché no tenía el objeto; ver [Caché](cache.md#what-it-keeps) para saber qué guarda.

## El gateway { #the-gateway }

- `shard N paused reading: ... bytes of events are waiting because the event loop is not keeping up`: algo bloquea el event loop; ver [Contrapresión](gateway.md#backpressure).
- `close code N (...) means Discord refused what bostie sent; this is likely a bostie bug, please report it`: los códigos 4001, 4002, 4005 y 4008 significan que bostie envió algo que Discord rechazó.
- `the members of guild N stopped arriving; the cache lacks some`: una solicitud de miembros para la caché superó el tiempo de espera de 30 segundos por bloque.
- `start()` lanza `InvalidToken`: el token es incorrecto o se restableció; vuelve a copiarlo de la página **Bot**.

## REST { #rest }

- `N invalid requests (401, 403, 429) in the last 10 minutes; Discord bans the IP at 10 000`: el bot sigue haciendo solicitudes que Discord rechaza, normalmente por falta de permisos o por un webhook muerto. Encuentra las llamadas que fallan en las excepciones que lanzan.
- `RateLimited` lanzado sin que ninguna solicitud llegue a Discord: la ruta sigue limitada durante más tiempo que `max_rate_limit_wait`; súbelo en [`HTTPSettings`](rest.md#rate-limits-retries-and-timeouts) o vuelve a intentarlo más tarde.

## Respuestas { #answers }

- `ephemeral=... is ignored because the message being edited is ...`: un mensaje no puede pasar de efímero a público, ni al revés, después de enviarse.
- `Discord allows 5 follow-ups to an interaction of an app installed only to the user`: el sexto mensaje de seguimiento de una aplicación instalada por un usuario falla.
- `the error hook ... left interaction ... unanswered; bostie answered 'Something went wrong.'`: haz que el hook responda.

## Voz { #voice }

- `nobody hears the bot in guild N: it is server muted`, o `it is suppressed on this stage; use vc.stage('request')`: un moderador silenció al bot, o el bot forma parte de la audiencia en un escenario.
- `a moderator deafened the bot in guild N, so Discord sends it no audio`: los receptores no reciben nada hasta que se le quite el ensordecimiento.
- `VoiceConnectError` con el motivo `TIMEOUT`: Discord no respondió a la unión en `timeout` segundos; puede que el canal esté lleno o que al bot le falte el permiso Conectar.
- `MissingIntent: voice needs Intents.GUILD_VOICE_STATES`: la voz necesita ese intent, que está incluido en `Intents.DEFAULT`.

## Reportar un error { #reporting-a-bug }

`python -m bostie` imprime las versiones que hay que incluir, y `bostie.voice.versions()` agrega libopus y DAVE para los problemas de voz. Pon el logger correspondiente en `DEBUG` antes de iniciar el bot, como muestra [Logging](running.md#logging), e incluye las líneas alrededor del problema, sin el token.
