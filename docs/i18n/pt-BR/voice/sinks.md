---
source: e89423f86355edd642290a66c8608f48553e0ff6
description: "Escreva áudio PCM ou Opus bruto em uma chamada de voz do Discord com um sink: formatos, contrapressão, marcadores, crossfades, buffer e contadores de underrun."
---

# Sinks e áudio bruto

Um sink é um buffer limitado em que o Python escreve áudio e do qual o mixer em Rust lê, 20 ms de cada vez. Toda faixa e toda fila tocam por um sink. Abra o seu próprio para áudio que você gera ou recebe de outro lugar: um sintetizador, um motor de conversão de texto em fala, um stream vindo de outro programa.

```python
--8<-- "docs/snippets/voice/tone_sink.py"
```

## Formatos { #formats }

`vc.sink(format)` retorna um `voice.Sink` para o que você vai escrever, um `voice.Format`:

- `voice.PCM(rate=48000, channels=2, sample="s16le")`: amostras intercaladas little-endian, `"s16le"` (inteiros de 16 bits) ou `"f32le"` (floats de 32 bits), mono ou estéreo, de 8000 a 192 000 Hz. O bostie reamostra para 48 kHz em Rust; uma taxa precisa se dividir em 48 kHz em blocos de no máximo 960 amostras, o que todas as taxas comuns fazem.
- `voice.Opus()`: um pacote Opus por escrita. Pacotes de 20 ms a 48 kHz estéreo são enviados como foram escritos enquanto o sink toca sozinho no volume 1 sem filtros; qualquer outra coisa é decodificada e mixada.

`voice.OpusEncoder(pcm_format, encoder=EncoderSettings())` codifica PCM nesses pacotes no processo do Python, para áudio que você quer codificar uma vez e enviar para muitos sinks: `encode(data)` retorna todos os pacotes completos até o momento, e `flush()` completa o resto com silêncio. O bitrate dele tem como padrão 64 kbps, já que ele não tem um canal para seguir.

## Escrevendo { #writing }

- `await sink.write(data)` enfileira áudio e espera enquanto o sink já guarda `buffer` segundos. Essa espera é a contrapressão que mantém o seu produtor no ritmo do tempo real.
- `sink.write_nowait(data)` enfileira o bloco inteiro de uma vez, ou levanta `BufferFull`.
- `async with sink:` espera no fim do bloco até que tudo o que foi escrito tenha tocado e depois fecha o sink; uma exceção no bloco descarta o que está na fila no lugar disso.
- `await sink.drain()` espera até que tudo o que foi escrito até agora tenha tocado; `False` se parte disso foi descartada.
- `sink.clear()` descarta o que está na fila; `clear(until=marker)` só a parte antes de um marcador, `clear(since=marker)` só a parte depois dele.
- `sink.close()` descarta o que está na fila e fecha. Escrever em um sink fechado levanta `SinkClosed`.

`sink.mark()` retorna um `Marker` depois do áudio escrito até o momento. `await marker` retorna `True` assim que todo o áudio antes dele tocou, ou `False` se parte dele foi descartada antes; `marker.done` diz se isso já se sabe. Os marcadores são o jeito de um player saber quando uma faixa realmente terminou, e não só quando foi escrita.

## Configurações { #settings }

`vc.sink` aceita estes argumentos nomeados; `vc.play` e `voice.Queue` também aceitam `buffer` e `stall`.

| Configuração | Padrão | Significado |
|---|---|---|
| `buffer` | `Defaults.sink_buffer`, 0.2 | segundos que o sink guarda antes que `write` espere, de 0.02 a 60 |
| `stall` | `Defaults.stall`, `"hold"` | quando o áudio não pode ser enviado, durante uma reconexão ou uma transição do DAVE: `"hold"` o guarda para tocar depois, `"drop"` o descarta em tempo real, para áudio ao vivo que não pode ficar para trás |
| `volume` | 1.0 | multiplica as amostras; `sink.volume` o muda enquanto toca |
| `filters` | `Filters()` | os [efeitos](playback.md#volume-and-filters); `sink.filters` os muda enquanto toca |
| `after` | `None` | outro sink desta conexão; este fica em silêncio até que aquele feche ou termine de tocar o que foi escrito antes do seu último `drain()` |
| `crossfade` | 0.0 | com `after`, segundos para começar antes e fazer a transição gradual; o sink então guarda esse tanto a mais no buffer |

`after` e `crossfade` são como uma fila toca sem intervalos: o sink da próxima faixa se enche enquanto a atual toca e assume na amostra exata.

## Estado e contadores { #state-and-counters }

`sink.queued` é quantos segundos de áudio estão esperando, `sink.passthrough` diz se o último pacote saiu como foi escrito, e `sink.closed` se ele está fechado. `sink.stats()` retorna um `SinkStats`:

| Campo | Valor |
|---|---|
| `sent` | segundos de áudio enviados |
| `position` | a posição de reprodução em segundos, como os marcadores a contam |
| `underruns` | quantas vezes o sink ficou vazio enquanto tocava, fora de um `drain()`: o produtor ficou para trás |
| `held_ticks` | ticks em que o sink foi segurado porque o áudio não podia ser enviado |
| `dropped` | segundos descartados por um stall `"drop"` |
| `passthrough_packets` | pacotes enviados como foram escritos, sem decodificação |

Os underruns são o número a observar: um produtor que não consegue se manter `buffer` segundos à frente é lento demais, ou o event loop está bloqueado. `vc.sinks` lista os sinks abertos de uma conexão.
