---
source: 7da84385c00fe71bc6575b90de4a0a059883399e
---

# Sinks y audio sin procesar

Un sink es un búfer acotado en el que Python escribe audio y del que el mezclador de Rust lee, de a 20 ms. Cada pista y cada cola se reproduce a través de uno. Abre uno propio para el audio que generas o que recibes de otro lugar: un sintetizador, un motor de texto a voz, un stream de otro programa.

```python
--8<-- "docs/snippets/voice/tone_sink.py"
```

## Formatos { #formats }

`vc.sink(format)` devuelve un `voice.Sink` para lo que vas a escribir, un `voice.Format`:

- `voice.PCM(rate=48000, channels=2, sample="s16le")`: muestras intercaladas en little-endian, `"s16le"` (enteros de 16 bits) o `"f32le"` (flotantes de 32 bits), mono o estéreo, de 8000 a 192 000 Hz. bostie remuestrea a 48 kHz en Rust; una frecuencia tiene que dividir los 48 kHz en bloques de como máximo 960 muestras, algo que cumplen todas las frecuencias comunes.
- `voice.Opus()`: un paquete Opus por escritura. Los paquetes de 20 ms a 48 kHz estéreo se envían tal como se escribieron mientras el sink se reproduce solo, a volumen 1 y sin filtros; cualquier otra cosa se decodifica y se mezcla.

`voice.OpusEncoder(pcm_format, encoder=EncoderSettings())` codifica PCM en esos paquetes dentro del proceso de Python, para el audio que quieres codificar una vez y enviar a muchos sinks: `encode(data)` devuelve todos los paquetes completos hasta el momento, y `flush()` rellena el resto con silencio. Su tasa de bits es de 64 kbps por defecto, porque no tiene ningún canal al que seguir.

## Escribir { #writing }

- `await sink.write(data)` pone audio en la cola, y espera mientras el sink ya contiene `buffer` segundos. Esa espera es la contrapresión que mantiene a tu productor al ritmo del tiempo real.
- `sink.write_nowait(data)` pone en la cola el bloque entero de una vez, o lanza `BufferFull`.
- `async with sink:` espera al final del bloque hasta que todo lo escrito se reprodujo y luego cierra el sink; una excepción en el bloque descarta en cambio lo que está en la cola.
- `await sink.drain()` espera hasta que todo lo escrito hasta ahora se reprodujo; `False` si se descartó una parte.
- `sink.clear()` descarta lo que está en la cola; `clear(until=marker)` solo la parte anterior a un marcador, `clear(since=marker)` solo la parte posterior.
- `sink.close()` descarta lo que está en la cola y lo cierra. Escribir en un sink cerrado lanza `SinkClosed`.

`sink.mark()` devuelve un `Marker` ubicado después del audio escrito hasta el momento. `await marker` devuelve `True` cuando se reprodujo todo el audio anterior, o `False` si antes se descartó una parte; `marker.done` indica si eso ya se sabe. Con los marcadores, un reproductor sabe cuándo terminó de verdad una pista, no solo cuándo se escribió.

## Ajustes { #settings }

`vc.sink` acepta estos argumentos por palabra clave; `vc.play` y `voice.Queue` también aceptan `buffer` y `stall`.

| Ajuste | Por defecto | Significado |
|---|---|---|
| `buffer` | `Defaults.sink_buffer`, 0.2 | segundos que contiene el sink antes de que `write` espere, de 0.02 a 60 |
| `stall` | `Defaults.stall`, `"hold"` | cuando el audio no se puede enviar, durante una reconexión o una transición de DAVE: `"hold"` lo conserva para reproducirlo después, `"drop"` lo descarta en tiempo real, para el audio en vivo que no debe quedarse atrás |
| `volume` | 1.0 | multiplica las muestras; `sink.volume` lo cambia durante la reproducción |
| `filters` | `Filters()` | los [efectos](playback.md#volume-and-filters); `sink.filters` los cambia durante la reproducción |
| `after` | `None` | otro sink de esta conexión; este queda en silencio hasta que ese se cierre o termine de reproducir lo escrito antes de su último `drain()` |
| `crossfade` | 0.0 | con `after`, segundos para empezar antes y hacer un fundido entre los dos; el sink guarda entonces esa cantidad más en el búfer |

Con `after` y `crossfade`, una cola reproduce sin pausas: el sink de la pista siguiente se llena mientras suena la actual, y toma el relevo en la muestra exacta.

## Estado y contadores { #state-and-counters }

`sink.queued` son los segundos de audio en espera, `sink.passthrough` indica si el último paquete salió tal como se escribió, y `sink.closed` si está cerrado. `sink.stats()` devuelve un `SinkStats`:

| Campo | Valor |
|---|---|
| `sent` | segundos de audio enviados |
| `position` | el cabezal de reproducción en segundos, tal como lo cuentan los marcadores |
| `underruns` | cuántas veces el sink se quedó vacío mientras reproducía, fuera de un `drain()`: el productor se quedó atrás |
| `held_ticks` | ticks en los que el sink se retuvo porque el audio no se podía enviar |
| `dropped` | segundos descartados por una detención en modo `"drop"` |
| `passthrough_packets` | paquetes enviados tal como se escribieron, sin decodificar |

Los underruns son el número que hay que vigilar: un productor que no logra mantenerse `buffer` segundos por delante es demasiado lento, o el event loop está bloqueado. `vc.sinks` enumera los sinks abiertos de una conexión.
