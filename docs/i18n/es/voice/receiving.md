---
source: c8644d4a54676ea108a34c06bd38c4e355260bc3
---

# Recibir y grabar

![Bostie sostiene un micrófono mientras llegan ondas de sonido](../assets/mascot/bostie-receiving.webp){ align=right width="180" loading=lazy }

`vc.receive()` abre un receptor: un iterador asíncrono acotado sobre el audio que escucha el bot. Cada persona que habla llega por separado, descifrada, reordenada y decodificada en Rust. Se pueden abrir varios receptores en una misma conexión, cada uno con su propio formato y sus propios filtros.

```python
--8<-- "docs/snippets/voice/listening.py"
```

## Formatos { #formats }

`vc.receive(format)` devuelve un `voice.Receiver` para un `voice.ReceiveFormat`:

| `vc.receive(format)` | Elementos | Para |
|---|---|---|
| `voice.PCM(rate=48000, channels=2, sample="s16le")` | `Frame` de muestras decodificadas en ese formato; la frecuencia es una de `voice.DECODE_RATES`, 8, 12, 16, 24 o 48 kHz | escuchar, reconocimiento de voz, niveles |
| `voice.Opus()` | `Frame` cuyo `data` es un paquete Opus | grabar o reenviar sin decodificar |
| `voice.Packets()` | `Packet`, descifrados, en orden de llegada, antes del jitter buffer | tu propio jitter buffer, extensiones de cabecera RTP |

Un `Frame` tiene el `user_id`, el `ssrc`, el `timestamp` RTP, `offset` y `samples` en el reloj de 48 kHz del receptor, `received_at`, el `data`, `lost` (verdadero para el audio que Opus inventó para ocultar un paquete perdido) y, en PCM, el `level`, el RMS del frame en dBFS. El reloj empieza cuando se abre el receptor, así que los frames de distintos usuarios se alinean por `offset`. Un `Packet` tiene el `user_id`, el `ssrc`, la `sequence`, el `timestamp`, `received_at`, el payload `opus` y las `extensions` de la cabecera RTP.

## Elegir a quién escuchar { #choosing-whom-to-hear }

- `users=[...]` conserva solo a esos usuarios, y `exclude=[...]` descarta a algunos.
- `include_self=True` agrega el audio propio del bot.
- `mix=True` (PCM y Opus) entrega un solo stream con todos mezclados, con `user_id` en `None`.
- `markers=True` pone eventos `SpeechStart`, `SpeechStop` y `UserLeave` entre los frames, cada uno con su `offset` en el mismo reloj, para que el consumidor sepa dónde empieza y termina el habla sin medirlo.

## Ajustes { #settings }

`vc.receive` acepta estos argumentos por palabra clave; `Packets()` no tiene jitter buffer, así que no acepta `jitter`.

| Ajuste | Por defecto | Significado |
|---|---|---|
| `buffer` | `Defaults.receive_buffer`, 2.0 | segundos de elementos que el receptor guarda para un consumidor lento, de 0.02 a 60 |
| `overflow` | `Defaults.overflow`, `"drop_oldest"` | cuando el búfer está lleno: descarta los elementos más antiguos, o `"drop_newest"` |
| `jitter` | `Defaults.jitter`, 0.1 | segundos de espera por los paquetes atrasados antes de seguir reproduciendo, de 0 a 1; solo PCM y Opus |
| `stall` | `Defaults.receive_stall`, `"wait"` | cuando la conexión se reconecta: `"wait"` mantiene abierto el receptor, `"end"` lo termina |

## Leer { #reading }

`async for item in rx` espera elementos y se detiene cuando termina el receptor. `async with rx:` lo cierra cuando termina el bloque.

- `rx.get_nowait()` devuelve el siguiente elemento, o lanza `voice.Empty` cuando no hay nada en la cola y `voice.ReceiverClosed` cuando el receptor terminó.
- `rx.queued` es cuánto lleva esperando el elemento más antiguo de la cola: el retraso del consumidor.
- `rx.clear()` descarta todo lo que está en la cola y sigue en vivo.
- `await rx.mark()` devuelve `True` cuando el audio recibido antes de la llamada ya está en la cola o se descartó, o `False` después de 1.2 segundos más el doble del jitter; cancelar una espera deja la marca para las demás.
- `rx.stats()` devuelve un `ReceiverStats`: los elementos entregados (`delivered`), los descartados por desbordamiento (`dropped`) y los paquetes perdidos en el camino (`lost`).
- `rx.close()` deja de recibir. `rx.closed_reason`, un `voice.ClosedReason`, indica por qué terminó un receptor: `CLOSED`, `DISCONNECTED`, `MOVED` (el bot cambió de canal) o `RECONNECTING` (con `stall="end"`).

Un moderador puede ensordecer al bot, y entonces Discord no le envía audio; bostie deja una advertencia en el log cuando se abre un receptor mientras el bot está ensordecido, y cuando el bot queda ensordecido mientras hay receptores abiertos.

## Retransmitir { #relaying }

Un receptor y un sink se conectan directamente, para llevar audio de una llamada a otra:

```python
--8<-- "docs/snippets/voice/relay.py"
```

Un frame mezclado no tiene `user_id`, `ssrc` ni `timestamp`, y una mezcla no puede llevar marcadores. Un frame Opus perdido tiene `data` vacío, porque no hay ningún paquete que pasar; sáltalo antes de escribir en un sink Opus, como hace `relay_speaker`. Los dos extremos usan por defecto PCM estéreo de 16 bits a 48 kHz, así que un receptor PCM escribe directamente en un sink con los valores por defecto.

## Habla { #speech }

La detección de habla corre en Rust en cada conexión mientras `speech_activity` está activado: un usuario empieza a hablar con su primer audio y deja de hablar después de `speech_hangover` segundos de silencio, 0.2 por defecto. `vc.speaking` contiene quién habla ahora, los eventos `SpeechStart` y `SpeechStop` se lo informan a los listeners, y los receptores abiertos con `markers=True` lo llevan en su stream. Con `speech_activity` desactivado y sin ningún receptor abierto, bostie ni siquiera descifra el audio entrante.

`voice.utterances(rx, min_silence=0.8, max_length=30.0)` agrupa los frames de un receptor PCM en `Utterance`: el habla de un usuario de principio a fin, con `data` en el formato del receptor, su `offset`, su `duration` y los segundos perdidos (`lost`). Una intervención termina después de `min_silence` segundos sin habla, o a los `max_length` segundos. Necesita un receptor PCM sin `mix`, abierto con `markers=True`, o un `level` en dBFS, que entonces decide qué cuenta como habla en lugar de los marcadores.

## Grabar { #recording }

```python
--8<-- "docs/snippets/voice/call_recording.py"
```

`voice.Recorder(vc, path)` graba a cada usuario en su propio archivo o, con `mix=True`, toda la llamada en uno solo. La ruta es una plantilla con `{guild_id}` y `{user_id}`, obligatoria para un archivo por usuario y rechazada con `mix=True`, y su extensión elige el contenedor:

- `.ogg` u `.opus` escribe Ogg Opus. Por usuario, los paquetes Opus recibidos se escriben tal cual, sin decodificar.
- `.wav` escribe PCM de 16 bits en el `format` del grabador, 48 kHz estéreo por defecto; su frecuencia es una de `voice.DECODE_RATES`.

Con `align=True`, el valor por defecto, el silencio rellena los huecos, así que cada archivo empieza cuando empezó la grabación y los archivos quedan alineados. `users`, `exclude` e `include_self` eligen a quién grabar, como en un receptor, y `buffer` (30 segundos) y `jitter` (0.3 segundos) son mayores que los valores por defecto de un receptor, porque un disco se puede trabar. La grabación termina sola después de `max_duration` segundos, cuando sus archivos llegan a `max_size` bytes o cuando termina la conexión.

`await recorder.stop()` la termina una vez escrito el audio ya escuchado, y devuelve los archivos como un dict del id de usuario a `Path`, con `None` como clave de un archivo mezclado. `await recorder.wait()` espera a que termine sola. `recorder.files` enumera los archivos escritos hasta el momento, y `recorder.stats()` los contadores del receptor, donde `dropped` significa que el disco se quedó atrás durante más de `buffer`.

Avísales a las personas de la llamada que el bot las está grabando, como hace el snippet.
