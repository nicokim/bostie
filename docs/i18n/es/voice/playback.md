---
source: fa47131472a60ef5dbaa1eef411b4ec762c85146
---

# Reproducir audio

![Bostie mueve la cabeza al ritmo de la música](../assets/mascot/bostie-playback.webp){ align=right width="180" loading=lazy }

`await vc.play(source)` reproduce una fuente en una [conexión de voz](index.md) y devuelve un `Track` de inmediato. Varias pistas se reproducen al mismo tiempo y se mezclan; una `Queue` las reproduce una tras otra.

```python
--8<-- "docs/snippets/voice/tracks.py"
```

## Fuentes { #sources }

Una fuente es audio para reproducir. Cada una se usa una sola vez: `play` la abre, abrirla después de que terminó lanza `StateError`, y `source.again()` devuelve una nueva con el mismo audio, para reproducirlo dos veces.

| Fuente | Reproduce | Permite saltar |
|---|---|---|
| `voice.File(path)` | un archivo local | sí |
| `voice.Bytes(data, hint=None)` | el contenido de un archivo en memoria; `hint` es una extensión como `"ogg"` | sí |
| `voice.Url(url, headers=None)` | una URL directa de medios por HTTP o HTTPS, en streaming, o una playlist HLS | cuando el servidor acepta solicitudes de rango; HLS no |
| `voice.FFmpeg(args, executable="ffmpeg")` | la salida de un comando de ffmpeg, para formatos que bostie no decodifica | no |
| `voice.Stream(items, format)` | un iterable asíncrono de bloques PCM o paquetes Opus en `format` | no |

`File`, `Bytes` y `Url` se decodifican en Rust: Ogg (Opus, Vorbis), WebM y Matroska, MP4 y M4A (AAC), MP3, FLAC y WAV. Una URL debe apuntar al medio en sí, no a una página web que lo inserta. Cuando se corta la conexión con el servidor, `Url` se reconecta hasta tres veces, después de 0.5, 1 y 2 segundos, y continúa donde se cortó si el servidor acepta solicitudes de rango. Una playlist maestra HLS reproduce su variante de audio, una playlist en vivo empieza tres segmentos antes de su final, y las playlists cifradas y los segmentos por rango de bytes se rechazan con `SourceError`. `FFmpeg` ejecuta el programa con tus argumentos de entrada y lee de su salida PCM estéreo de 16 bits a 48 kHz; lanza `SourceError` cuando el programa no está en el `PATH` o termina con un error. Una fuente que no se puede abrir, decodificar o en la que no se puede saltar lanza `SourceError`, o termina su pista con el motivo `ERROR`.

Después de `open()`, que activa `opened`, una fuente tiene `format`, `duration` (segundos, `None` si se desconoce), `seekable` y `tags`, un `voice.Tags` con el `title`, el `artist` y el `album` que indica el archivo.

```python
--8<-- "docs/snippets/voice/other_sources.py"
```

El audio Opus a 48 kHz estéreo en paquetes de 20 ms, como la mayoría de los archivos Ogg Opus y WebM, se envía tal cual, sin decodificarlo y volver a codificarlo, mientras se reproduce solo, a volumen 1 y sin filtros. `track.passthrough` indica cuándo pasa eso.

## Pistas { #tracks }

`vc.play(source, volume=1.0, filters=Filters(), meta=None)` acepta el volumen y los filtros iniciales, y `meta`, cualquier valor que quieras guardar con la pista, como quién la pidió. También acepta `buffer` y `stall` para el [sink](sinks.md) de la pista.

| `Track` | Qué hace |
|---|---|
| `await track.wait()` | espera el final y devuelve un `TrackEnd`; cancelar la espera no detiene la pista |
| `await track.started()` | espera hasta que empieza a reproducirse; `False` si terminó antes |
| `track.ended` | el `TrackEnd`, o `None` mientras se reproduce |
| `track.position` | los segundos de la fuente reproducidos hasta ahora |
| `track.pause()`, `track.resume()`, `track.paused` | dejan de alimentarla y la vuelven a alimentar; el audio que ya está en el búfer se sigue reproduciendo |
| `await track.seek(seconds)` | continúa desde una posición; `SourceError` cuando la fuente no permite saltar |
| `track.volume`, `track.filters` | cambian de inmediato mientras se reproduce |
| `track.stop()` | la termina |
| `track.source`, `track.meta`, `track.sink` | lo que reproduce, tu valor y el sink en el que escribe |

Un `TrackEnd` tiene el `track`, el `reason` (`FINISHED`, `STOPPED`, `ERROR` con el `error`, o `CLEANUP` cuando terminó la conexión) y `may_start_next`, verdadero después de `FINISHED` o `ERROR`, que indica si un reproductor propio debería pasar a su siguiente pista. `vc.stop()` termina todas las pistas.

## Volumen y filtros { #volume-and-filters }

`volume` multiplica las muestras: 0.5 suena a la mitad, 1.0 las deja sin cambios. `voice.Filters` define efectos que Rust aplica mientras se reproduce el audio:

| Campo de `Filters` | Por defecto | Rango |
|---|---|---|
| `equalizer` | `{}` | hasta 16 bandas, que asocian una frecuencia central de 20 a 20 000 Hz con una ganancia en dB; cada banda tiene una octava de ancho |
| `bass` | 0.0 | ganancia en dB por debajo de 100 Hz |
| `speed` | 1.0 | de 0.5 a 2.0, conservando el tono |
| `pitch` | 1.0 | de 0.5 a 2.0, conservando el tempo |

Las ganancias van de -24 a 24 dB, y los valores fuera de rango lanzan `ValueError`.

## Colas { #queues }

```python
--8<-- "docs/snippets/voice/queue_player.py"
```

`voice.Queue(vc)` reproduce las pistas una detrás de otra sin pausas, o superpuestas durante `crossfade` segundos. Acepta `volume`, `filters`, `crossfade` y `history`, que por defecto toman los ajustes de la conexión, y el `buffer` y el `stall` del sink.

- `queue.add(source, meta=None, index=None)` pone una fuente en la cola al final, o en `index` entre las próximas pistas, y devuelve su `Track`.
- `queue.now`, `queue.upcoming` y `queue.history` son la pista actual, las siguientes y hasta `history` de las que ya se reprodujeron.
- `queue.skip()` termina la pista actual; `queue.back()` vuelve a reproducir la última pista del historial y luego la actual desde el principio.
- `queue.move(track, index)`, `queue.remove(track)`, `queue.shuffle()` y `queue.clear()` reorganizan las próximas pistas.
- `queue.loop = "track"` repite la pista actual, `"queue"` la cola entera y `None` deja de repetir; el tipo es `voice.Loop`. Las repeticiones usan `source.again()`.
- `queue.pause()`, `queue.resume()`, `queue.paused`, `queue.volume` y `queue.filters` actúan sobre lo que se esté reproduciendo.
- `queue.stop()` termina todas las pistas; las pistas que se agreguen después se vuelven a reproducir.
- `await queue.join()` espera hasta que no quede nada en la cola ni reproduciéndose.

Una cola sobre una conexión que terminó está cerrada (`closed`) y no acepta más pistas; crea una nueva después de reconectar, como hace el snippet.

## Sonidos del panel de sonidos { #soundboard-sounds }

`await bot.rest.soundboard.send(vc.channel_id, sound_id=...)` reproduce un sonido del panel de sonidos en el canal al que está conectado el bot. Discord lo reproduce en la propia llamada, junto a lo que envíe el bot; pasa `source_guild_id` para un sonido de otro servidor. `bot.rest.soundboard.list_default()` enumera los sonidos propios de Discord y `list(guild_id)` los de un servidor, y `bot.request_soundboard_sounds(guild_id)` se los pide al gateway.

## El mismo audio en varios servidores { #the-same-audio-in-several-guilds }

`await voice.broadcast(source, connections)` reproduce una fuente en varias conexiones, decodificándola y codificándola una sola vez. Las conexiones que terminan salen del conjunto; las que se unen después no se agregan. Acepta `encoder` y `stall`, `"drop"` por defecto, para que una conexión detenida no frene a las demás.

## Codificador { #encoder }

`voice.EncoderSettings` configura el codificador Opus de una conexión, mediante `Defaults(encoder=...)`, `connect(encoder=...)` o `vc.encoder = ...` mientras está conectada:

| Campo | Por defecto | Significado |
|---|---|---|
| `bitrate` | `None` | bits por segundo, de 500 a 512 000; `None` sigue la tasa de bits del canal, o 64 000 |
| `application` | `"audio"` | `"audio"` para música, `"voip"` para voz, `"lowdelay"` para la menor latencia |
| `fec` | `True` | corrección de errores hacia adelante, que oculta los paquetes perdidos |
| `packet_loss` | 0.1 | la pérdida esperada, de 0 a 1, para la que se prepara `fec` |
| `complexity` | 10 | de 0 a 10; un valor menor usa menos CPU |
| `bandwidth` | `"auto"` | la banda de audio más alta: `"narrow"`, `"medium"`, `"wide"`, `"superwide"` o `"full"` |
| `signal_type` | `"auto"` | ajusta para `"voice"` o `"music"` |

`vc.speaking_flags` define los flags de habla que anuncia el bot: `voice.SpeakingFlags.MICROPHONE` por defecto, `SOUNDSHARE` para audio compartido, `PRIORITY` para la prioridad de palabra.
