---
source: fa47131472a60ef5dbaa1eef411b4ec762c85146
---

# Reproduzindo áudio

![Bostie balançando a cabeça ao som da música](../assets/mascot/bostie-playback.webp){ align=right width="180" loading=lazy }

`await vc.play(source)` reproduz uma fonte em uma [conexão de voz](index.md) e retorna uma `Track` na hora. Várias faixas tocam ao mesmo tempo e são mixadas; uma `Queue` as toca uma depois da outra.

```python
--8<-- "docs/snippets/voice/tracks.py"
```

## Fontes { #sources }

Uma fonte é um áudio a reproduzir. Cada uma é usada uma vez: `play` a abre, abri-la depois que terminou levanta `StateError`, e `source.again()` retorna uma nova com o mesmo áudio, para tocá-lo duas vezes.

| Fonte | Reproduz | Permite busca |
|---|---|---|
| `voice.File(path)` | um arquivo local | sim |
| `voice.Bytes(data, hint=None)` | o conteúdo de um arquivo em memória; `hint` é uma extensão como `"ogg"` | sim |
| `voice.Url(url, headers=None)` | uma URL HTTP ou HTTPS direta de mídia, transmitida em stream, ou uma playlist HLS | quando o servidor aceita requisições de intervalo; HLS não |
| `voice.FFmpeg(args, executable="ffmpeg")` | a saída de um comando do ffmpeg, para formatos que o bostie não decodifica | não |
| `voice.Stream(items, format)` | um iterável assíncrono de blocos PCM ou pacotes Opus em `format` | não |

`File`, `Bytes` e `Url` são decodificados em Rust: Ogg (Opus, Vorbis), WebM e Matroska, MP4 e M4A (AAC), MP3, FLAC e WAV. Uma URL precisa apontar para a própria mídia, não para uma página web que a incorpora. Quando a conexão com o servidor cai, `Url` se reconecta até três vezes, depois de 0,5, 1 e 2 segundos, e continua de onde parou se o servidor aceitar requisições de intervalo. Uma playlist mestre HLS toca a sua versão de áudio, uma playlist ao vivo começa três segmentos antes do fim, e as playlists criptografadas e os segmentos por intervalo de bytes são recusados com `SourceError`. `FFmpeg` roda o programa com os seus argumentos de entrada e lê da saída dele PCM estéreo de 16 bits a 48 kHz; ele levanta `SourceError` quando o programa não está no `PATH` ou sai com erro. Uma fonte que não pode ser aberta, decodificada ou buscada levanta `SourceError`, ou encerra a sua faixa com o motivo `ERROR`.

Depois de `open()`, que define `opened`, uma fonte tem `format`, `duration` (segundos, `None` quando desconhecida), `seekable` e `tags`, um `voice.Tags` com o `title`, o `artist` e o `album` que o arquivo informa.

```python
--8<-- "docs/snippets/voice/other_sources.py"
```

Áudio Opus a 48 kHz estéreo em pacotes de 20 ms, como a maioria dos arquivos Ogg Opus e WebM, é enviado do jeito que está, sem decodificar e codificar de novo, enquanto toca sozinho no volume 1 sem filtros. `track.passthrough` diz quando isso acontece.

## Faixas { #tracks }

`vc.play(source, volume=1.0, filters=Filters(), meta=None)` aceita o volume e os filtros iniciais, e `meta`, qualquer valor a guardar com a faixa, como quem a pediu. Ele também aceita `buffer` e `stall` para o [sink](sinks.md) da faixa.

| `Track` | Faz |
|---|---|
| `await track.wait()` | espera o fim e retorna um `TrackEnd`; cancelar a espera não para a faixa |
| `await track.started()` | espera até ela começar a tocar; `False` se ela terminou antes |
| `track.ended` | o `TrackEnd`, ou `None` enquanto ela toca |
| `track.position` | os segundos da fonte tocados até agora |
| `track.pause()`, `track.resume()`, `track.paused` | param e retomam a alimentação dela; o áudio que já está no buffer continua tocando |
| `await track.seek(seconds)` | continua a partir de uma posição; `SourceError` quando a fonte não permite busca |
| `track.volume`, `track.filters` | mudam na hora enquanto ela toca |
| `track.stop()` | a encerra |
| `track.source`, `track.meta`, `track.sink` | o que ela toca, o seu valor e o sink em que ela escreve |

Um `TrackEnd` tem a `track`, o `reason` (`FINISHED`, `STOPPED`, `ERROR` com o `error`, ou `CLEANUP` quando a conexão terminou) e `may_start_next`, verdadeiro depois de `FINISHED` ou `ERROR`, que diz se um player seu deve passar para a próxima faixa. `vc.stop()` encerra todas as faixas.

## Volume e filtros { #volume-and-filters }

`volume` multiplica as amostras: 0.5 é metade do volume, 1.0 as deixa sem mudança. `voice.Filters` define efeitos que o Rust aplica enquanto o áudio toca:

| Campo de `Filters` | Padrão | Faixa de valores |
|---|---|---|
| `equalizer` | `{}` | até 16 bandas, mapeando uma frequência central de 20 a 20 000 Hz para um ganho em dB; cada banda tem uma oitava de largura |
| `bass` | 0.0 | ganho em dB abaixo de 100 Hz |
| `speed` | 1.0 | de 0.5 a 2.0, mantendo a altura |
| `pitch` | 1.0 | de 0.5 a 2.0, mantendo o andamento |

Os ganhos vão de -24 a 24 dB, e valores fora da faixa levantam `ValueError`.

## Filas { #queues }

```python
--8<-- "docs/snippets/voice/queue_player.py"
```

`voice.Queue(vc)` toca as faixas uma atrás da outra sem intervalos, ou sobrepostas por `crossfade` segundos. Ela aceita `volume`, `filters`, `crossfade` e `history`, que têm como padrão as configurações da conexão, e o `buffer` e o `stall` do sink.

- `queue.add(source, meta=None, index=None)` coloca uma fonte no fim da fila, ou em `index` entre as próximas faixas, e retorna a `Track` dela.
- `queue.now`, `queue.upcoming` e `queue.history` são a faixa atual, as próximas e até `history` faixas que já tocaram.
- `queue.skip()` encerra a faixa atual; `queue.back()` toca de novo a última faixa do histórico e depois a atual do começo.
- `queue.move(track, index)`, `queue.remove(track)`, `queue.shuffle()` e `queue.clear()` reorganizam as próximas faixas.
- `queue.loop = "track"` repete a faixa atual, `"queue"` a fila inteira, `None` para de repetir; o tipo é `voice.Loop`. As repetições usam `source.again()`.
- `queue.pause()`, `queue.resume()`, `queue.paused`, `queue.volume` e `queue.filters` agem sobre o que estiver tocando.
- `queue.stop()` encerra todas as faixas; as faixas adicionadas depois voltam a tocar.
- `await queue.join()` espera até não haver nada na fila nem tocando.

Uma fila em uma conexão que terminou fica `closed` e não aceita mais faixas; crie uma nova depois de reconectar, como o trecho de código faz.

## Sons do painel de sons { #soundboard-sounds }

`await bot.rest.soundboard.send(vc.channel_id, sound_id=...)` toca um som do painel de sons no canal em que o bot está conectado. O próprio Discord o toca na chamada, junto com o que o bot envia; passe `source_guild_id` para um som de outro servidor. `bot.rest.soundboard.list_default()` lista os sons do próprio Discord e `list(guild_id)` os de um servidor, e `bot.request_soundboard_sounds(guild_id)` os pede ao gateway.

## O mesmo áudio em vários servidores { #the-same-audio-in-several-guilds }

`await voice.broadcast(source, connections)` toca uma fonte em várias conexões, decodificando-a e codificando-a uma única vez. As conexões que terminam saem do conjunto; as que entram depois não são adicionadas. Ele aceita `encoder` e `stall`, `"drop"` por padrão, para que uma conexão parada não segure as outras.

## Codificador { #encoder }

`voice.EncoderSettings` configura o codificador Opus de uma conexão, por `Defaults(encoder=...)`, `connect(encoder=...)` ou `vc.encoder = ...` enquanto conectado:

| Campo | Padrão | Significado |
|---|---|---|
| `bitrate` | `None` | bits por segundo, de 500 a 512 000; `None` segue o bitrate do canal, ou 64 000 |
| `application` | `"audio"` | `"audio"` para música, `"voip"` para fala, `"lowdelay"` para a menor latência |
| `fec` | `True` | correção de erros antecipada (FEC), que disfarça pacotes perdidos |
| `packet_loss` | 0.1 | a perda esperada, de 0 a 1, para a qual `fec` se prepara |
| `complexity` | 10 | de 0 a 10; menos usa menos CPU |
| `bandwidth` | `"auto"` | a banda de áudio mais alta: `"narrow"`, `"medium"`, `"wide"`, `"superwide"` ou `"full"` |
| `signal_type` | `"auto"` | ajusta para `"voice"` ou `"music"` |

`vc.speaking_flags` define as flags de fala que o bot anuncia, `voice.SpeakingFlags.MICROPHONE` por padrão, `SOUNDSHARE` para áudio compartilhado, `PRIORITY` para voz prioritária.
