---
source: 4108c4a61370aa8ef893c1d65371c3d654e43c09
description: "Conecta bostie a canales de voz y escenario: ajustes de conexión, cifrado de extremo a extremo DAVE, eventos de voz, medios detenidos y motores de voz externos."
---

# Conexiones de voz

![Bostie escucha con auriculares](../assets/mascot/bostie-listen.webp){ align=right width="180" loading=lazy }

El motor de voz de bostie corre en su núcleo en Rust: el WebSocket de voz, el transporte UDP, el reloj de envío de 20 ms, la decodificación y el remuestreo, el mezclador, la codificación y decodificación Opus, el cifrado del transporte y el cifrado de extremo a extremo DAVE. Python le pide que se una, reproduzca y reciba; los tiempos nunca dependen del event loop, salvo que Python tiene que mantener lleno el búfer de cada sink.

La voz necesita `Intents.GUILD_VOICE_STATES`, que está incluido en `Intents.DEFAULT`. El bot tiene como máximo una conexión de voz por servidor.

```python
--8<-- "docs/snippets/voice/voice_connection.py"
```

## Conectarse { #connecting }

`await ctx.voice.connect()` se une al canal de voz o canal escenario del usuario que ejecutó el comando, y retorna cuando el audio ya puede fluir en los dos sentidos. Busca el canal del usuario en la caché, o se lo pregunta a Discord cuando el servidor no está en caché, y lanza `NotInVoice` cuando el usuario no está en ningún canal de voz del servidor o la interacción vino de un MD. `ctx.voice.current` es la conexión del bot en ese servidor, o `None`.

Fuera de un handler, `await bot.voice.connect(channel_id)` se une a un canal por id. En un servidor donde el bot ya está conectado, mueve la conexión y aplica solo las opciones indicadas. `bot.voice.get(guild_id)` y `bot.voice.connections` devuelven las conexiones abiertas, y `await bot.voice.close()` sale de todas, como hace `bot.close()`.

Una unión que no está lista en `timeout` segundos lanza `VoiceConnectError` con el motivo `TIMEOUT`; Discord deja sin respuesta la unión a un canal lleno, o a uno al que el bot no tiene permiso para conectarse. Los demás fallos lanzan `VoiceConnectError` con el motivo, el código de cierre y el mensaje de Discord.

## Ajustes { #settings }

`Bot(voice=voice.Defaults(...))` define con qué arranca cada conexión, y `connect()` acepta los ajustes de conexión para una llamada:

| Campo de `voice.Defaults` | Por defecto | Significado |
|---|---|---|
| `self_mute`, `self_deaf` | `False` | el silencio y el ensordecimiento propios del bot |
| `timeout` | 10.0 | segundos de espera para unirse, moverse o editar |
| `server_change_delay` | 0.0 | segundos de espera antes de seguir a Discord a un nuevo servidor de voz |
| `encoder` | `EncoderSettings()` | el codificador Opus; ver [Reproducir audio](playback.md#encoder) |
| `speaking_flags` | `MICROPHONE` | los `voice.SpeakingFlags` que el bot anuncia cuando reproduce |
| `stage` | `"listen"` | en un escenario: `"listen"`, `"request"` para pedir la palabra, o `"speak"` |
| `speech_hangover` | 0.2 | segundos de silencio antes de que se considere que alguien dejó de hablar, de 0.02 a 10 |
| `speech_activity` | `True` | si se disparan los eventos de inicio y fin de habla |
| `stall_after` | 2.0 | segundos que el flujo de medios puede detenerse antes de que `MediaStalled` lo informe, como mínimo 0.02 |
| `sink_buffer`, `stall` | 0.2, `"hold"` | los valores por defecto de cada [sink](sinks.md) |
| `receive_buffer`, `jitter`, `overflow`, `receive_stall` | 2.0, 0.1, `"drop_oldest"`, `"wait"` | los valores por defecto de cada [receptor](receiving.md) |
| `crossfade`, `history` | 0.0, 0 | los valores por defecto de cada [cola](playback.md#queues) |

`connect()` acepta `self_mute`, `self_deaf`, `timeout`, `server_change_delay`, `encoder`, `speaking_flags`, `stage`, `speech_hangover`, `speech_activity` y `stall_after` como argumentos por palabra clave; uno que no se pasa, o `None`, conserva el valor por defecto. `connect_external()` acepta los tres primeros, `move()` acepta `timeout`, y `edit()` acepta `self_mute`, `self_deaf` y `timeout`. Los valores inválidos lanzan `ValueError` cuando se construye el `Defaults`. Los ajustes de tipo cadena tienen los tipos literales `voice.StageRole`, `voice.SinkStall`, `voice.ReceiveStall`, `voice.Overflow`, `voice.Sample`, `voice.OpusApplication`, `voice.OpusBandwidth` y `voice.OpusSignal`. `ctx.voice` es un `voice.ContextVoice` y `bot.voice` un `voice.VoiceManager`.

## La conexión { #the-connection }

| `VoiceConnection` | Valor |
|---|---|
| `guild_id`, `channel_id` | dónde está |
| `state` | un `voice.State`: `CONNECTING`, `READY`, `RESUMING`, `RECONNECTING` o `DISCONNECTED` |
| `members` | los ids de los demás usuarios de la llamada |
| `speaking` | los usuarios que están hablando ahora, asociados al `time.monotonic()` en que empezaron |
| `ssrcs` | cada SSRC de la llamada asociado al id de su usuario |
| `self_mute`, `self_deaf` | el silencio y el ensordecimiento propios del bot, tal como los aplicó Discord |
| `mute`, `deaf`, `suppress` | si un moderador silenció o ensordeció al bot, o si no puede hablar en este escenario |
| `latency` | una `Latency` con los tiempos de ida y vuelta del WebSocket y de UDP en segundos, `None` hasta que se miden |
| `dave` | el cifrado de extremo a extremo de la llamada; ver abajo |
| `sinks` | los sinks abiertos, del más antiguo al más nuevo |
| `encoder`, `speaking_flags`, `speech_hangover`, `speech_activity`, `stall_after` | se pueden cambiar mientras está conectado |

- `await vc.move(channel_id)` se mueve a otro canal de voz o canal escenario del servidor y retorna cuando los medios ya fluyen ahí.
- `await vc.edit(self_mute=..., self_deaf=...)` cambia el silencio o el ensordecimiento propios del bot, deja como está el que no se indica y retorna cuando Discord lo aplicó.
- `await vc.stage("request")` pide la palabra en un escenario, `"speak"` habla (si tiene el permiso) y `"listen"` vuelve a la audiencia.
- `await vc.wait_until(voice.State.READY)` espera un estado, y lanza `VoiceNotConnected` si la conexión termina antes.
- `vc.reconnect()` corta el WebSocket de voz y reanuda en uno nuevo.
- `vc.stop()` cierra todos los sinks, así que no se reproduce nada.
- `await vc.disconnect()` sale del canal y retorna cuando terminó la conexión, como máximo 2 segundos después.

Las llamadas sobre una conexión que terminó lanzan `VoiceNotConnected`.

La conexión se repara sola. Un WebSocket que se cae se reanuda, una sesión perdida se reconecta y, cuando un moderador mueve al bot, este lo sigue al nuevo canal y envía un evento `Moved`. Cuando un moderador lo desconecta o elimina el canal, termina con el motivo `REMOVED`. bostie advierte cuando el bot reproduce mientras está silenciado por el servidor o sin permiso para hablar en un escenario, y cuando los receptores esperan mientras está ensordecido.

## Cifrado de extremo a extremo { #end-to-end-encryption }

bostie habla DAVE, el cifrado de extremo a extremo de Discord para la voz, sin nada que configurar: se une al grupo de cifrado de la llamada, cifra lo que envía y descifra lo que recibe. `vc.dave` lo informa como un `voice.Dave`:

- `protocol_version` es el protocolo DAVE en uso, 0 si no hay ninguno;
- `epoch` cuenta los cambios de los miembros del grupo;
- `privacy_code` es el código que muestran los clientes de Discord para la llamada;
- `await vc.dave.verification_code(user_id)` devuelve el código que hay que comparar con otro usuario por un canal aparte, para verificar que nadie más está en el medio.

Un evento `DaveTransition` informa cada cambio. Un canal que exige DAVE a un cliente que no lo ofrece se cierra con el motivo `DAVE_REQUIRED`, y un paso de DAVE fallido lanza `DaveError` o termina la conexión con `DAVE_FAILED`.

## Eventos { #events }

Las conexiones de voz envían sus eventos por `@bot.listen`. Cada uno es un `voice.VoiceEvent` con el `guild_id`. Un `StateChange` hacia `DISCONNECTED` trae un `voice.DisconnectReason`: `REQUESTED` (se ejecutó `disconnect()`), `REMOVED` (un moderador desconectó al bot o eliminó el canal), `CALL_TERMINATED`, `RATE_LIMITED`, `TIMEOUT`, `SESSION_INVALID`, `AUTHENTICATION_FAILED`, `DAVE_REQUIRED`, `DAVE_FAILED`, `PROTOCOL_ERROR` o `CLOSED` (el bot se cerró).

| Evento | Campos | Se envía cuando |
|---|---|---|
| `StateChange` | `old`, `new`, `reason`, `code`, `detail` | la conexión cambió de estado |
| `Moved` | `old_channel_id`, `new_channel_id`, `by_us` | el bot se movió, por `move()` o por un moderador |
| `DaveTransition` | `protocol_version`, `epoch`, `transition_id` | cambió el cifrado de extremo a extremo |
| `MediaStalled`, `MediaResumed` | `direction` (`voice.Direction.SEND` o `RECEIVE`), `duration` | el envío o la recepción se detuvo, o se reanudó |
| `UserJoin`, `UserLeave` | `user_id` | un usuario se unió a la llamada o salió de ella |
| `SpeakingUpdate` | `user_id`, `ssrc`, `flags` | un usuario anunció su SSRC y sus flags de habla |
| `SpeechStart`, `SpeechStop` | `user_id`, `ssrc`, `at`, y `duration` al terminar | un usuario empezó o dejó de hablar |
| `RtcpPacket` | `packet_type`, `count`, `ssrc`, `user_id`, `reports`, `data` | el servidor de voz envió un paquete RTCP; cada `ReceptionReport` de `reports` tiene el `fraction_lost`, `lost`, `highest_sequence`, `jitter`, `last_sender_report` y `delay` de un stream |

Están en `bostie.voice`. `SpeechStart` y `SpeechStop` solo se disparan mientras `speech_activity` está activado.

## Medios detenidos { #stalled-media }

```python
--8<-- "docs/snippets/voice/media_stalls.py"
```

`MediaStalled` con `direction` `SEND` se dispara cuando hay audio esperando en un sink pero no puede salir durante `stall_after` segundos, en una reconexión o en una transición de DAVE. Con `RECEIVE`, llegan paquetes a un receptor abierto pero ninguno se puede abrir ni reproducir durante ese tiempo. `MediaResumed` sigue cuando los medios vuelven a fluir, y su `duration` cuenta desde el inicio de la detención. `stall_after` es de 2 segundos salvo que `voice.Defaults`, `connect(stall_after=...)` o `vc.stall_after` indiquen otra cosa.

## Salir de un canal vacío { #leaving-an-empty-channel }

El bot se queda en un canal hasta que se desconecta. Para salir cuando sale el último usuario:

```python
--8<-- "docs/snippets/voice/leave_when_alone.py"
```

## Errores { #errors }

Los errores de voz son `VoiceError`: `VoiceConnectError` (una unión terminó antes de estar lista; `reason`, `code`, `detail`), `NotInVoice`, `VoiceNotConnected`, `AlreadyConnected`, `VoiceQueueFull` (demasiados comandos esperando a la conexión), `DaveError` y, para el audio, `SourceError`, `SinkClosed`, `BufferFull`, `Empty` y `ReceiverClosed`, que explican las páginas siguientes.

## Motores de voz externos { #external-voice-engines }

```python
--8<-- "docs/snippets/voice/external_engine.py"
```

`await bot.voice.connect_external(guild_id, channel_id)` se une a un canal para un motor de voz que corre en otro lugar, y devuelve un `ExternalVoice` con el `state` (`VoiceStateUpdate`) y el `server` (`VoiceServerUpdate`) con los que se conecta el motor. bostie no envía entonces ningún audio en ese servidor. `async with external.updates() as updates:` entrega los servidores de voz posteriores del servidor y los estados de voz propios del bot, y `await external.disconnect()` sale del canal. Lanza `AlreadyConnected` en un servidor donde bostie ejecuta su propia conexión, y acepta `self_mute`, `self_deaf` y `timeout`.

## Versiones { #versions }

`voice.versions()` devuelve las versiones de bostie, de libopus, de la biblioteca DAVE y del protocolo DAVE más alto compiladas en la build.
