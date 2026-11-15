---
source: c8644d4a54676ea108a34c06bd38c4e355260bc3
---

# Recebendo e gravando

![Bostie segurando um microfone enquanto chegam ondas sonoras](../assets/mascot/bostie-receiving.webp){ align=right width="180" loading=lazy }

`vc.receive()` abre um receptor: um iterador assíncrono limitado sobre o áudio que o bot ouve. Cada falante chega separadamente, descriptografado, reordenado e decodificado em Rust. Vários receptores podem estar abertos em uma conexão, cada um com o seu próprio formato e os seus próprios filtros.

```python
--8<-- "docs/snippets/voice/listening.py"
```

## Formatos { #formats }

`vc.receive(format)` retorna um `voice.Receiver` para um `voice.ReceiveFormat`:

| `vc.receive(format)` | Itens | Para |
|---|---|---|
| `voice.PCM(rate=48000, channels=2, sample="s16le")` | `Frame`s de amostras decodificadas nesse formato; a taxa é uma de `voice.DECODE_RATES`, 8, 12, 16, 24 ou 48 kHz | ouvir, reconhecimento de fala, níveis |
| `voice.Opus()` | `Frame`s cujo `data` é um pacote Opus | gravar ou encaminhar sem decodificar |
| `voice.Packets()` | `Packet`s, descriptografados, na ordem de chegada, antes do buffer de jitter | o seu próprio buffer de jitter, extensões de cabeçalho RTP |

Um `Frame` tem o `user_id`, o `ssrc`, o `timestamp` RTP, `offset` e `samples` no relógio de 48 kHz do receptor, `received_at`, o `data`, `lost` (verdadeiro para áudio que o Opus inventou para disfarçar um pacote perdido) e, para PCM, o `level`, o RMS do frame em dBFS. O relógio começa quando o receptor abre, então os frames de usuários diferentes se alinham pelo `offset`. Um `Packet` tem o `user_id`, o `ssrc`, o `sequence`, o `timestamp`, o `received_at`, o payload `opus` e as `extensions` do cabeçalho RTP.

## Escolhendo quem ouvir { #choosing-whom-to-hear }

- `users=[...]` mantém só esses usuários, e `exclude=[...]` descarta alguns.
- `include_self=True` adiciona o próprio áudio do bot.
- `mix=True` (PCM e Opus) entrega um único stream com todos mixados, com `user_id` definido como `None`.
- `markers=True` coloca eventos `SpeechStart`, `SpeechStop` e `UserLeave` entre os frames, cada um com o seu `offset` no mesmo relógio, para que um consumidor saiba onde a fala começa e termina sem cronometrá-la.

## Configurações { #settings }

`vc.receive` aceita estes argumentos nomeados; `Packets()` não tem buffer de jitter, então não aceita `jitter`.

| Configuração | Padrão | Significado |
|---|---|---|
| `buffer` | `Defaults.receive_buffer`, 2.0 | segundos de itens que o receptor guarda para um consumidor lento, de 0.02 a 60 |
| `overflow` | `Defaults.overflow`, `"drop_oldest"` | quando o buffer está cheio: descarta os itens mais antigos, ou `"drop_newest"` |
| `jitter` | `Defaults.jitter`, 0.1 | segundos para esperar pacotes atrasados antes de seguir tocando, de 0 a 1; só PCM e Opus |
| `stall` | `Defaults.receive_stall`, `"wait"` | quando a conexão se reconecta: `"wait"` mantém o receptor aberto, `"end"` o encerra |

## Lendo { #reading }

`async for item in rx` espera os itens e para quando o receptor termina. `async with rx:` o fecha quando o bloco termina.

- `rx.get_nowait()` retorna o próximo item, ou levanta `voice.Empty` quando não há nada na fila e `voice.ReceiverClosed` quando o receptor terminou.
- `rx.queued` é quanto tempo o item mais antigo da fila esperou: o atraso do consumidor.
- `rx.clear()` descarta tudo o que está na fila e continua ao vivo.
- `await rx.mark()` retorna `True` assim que o áudio recebido antes da chamada está na fila ou foi descartado, ou `False` depois de 1,2 segundo mais duas vezes o jitter; cancelar uma espera deixa a marca para as outras.
- `rx.stats()` retorna um `ReceiverStats`: os itens entregues (`delivered`), os descartados por estouro (`dropped`) e os pacotes perdidos no caminho (`lost`).
- `rx.close()` para de receber. `rx.closed_reason`, um `voice.ClosedReason`, diz por que um receptor terminou: `CLOSED`, `DISCONNECTED`, `MOVED` (o bot mudou de canal) ou `RECONNECTING` (com `stall="end"`).

Um moderador pode ensurdecer o bot, e o Discord então não envia áudio a ele; o bostie escreve um aviso no log quando um receptor abre enquanto o bot está ensurdecido, e quando o bot é ensurdecido enquanto há receptores abertos.

## Retransmitindo { #relaying }

Um receptor e um sink se conectam diretamente, para levar áudio de uma chamada para outra:

```python
--8<-- "docs/snippets/voice/relay.py"
```

Um frame mixado não tem `user_id`, `ssrc` nem `timestamp`, e uma mixagem não pode trazer marcadores. Um frame Opus perdido tem `data` vazio, porque não há pacote para repassar; pule-o antes de escrever em um sink Opus, como `relay_speaker` faz. As duas pontas têm como padrão PCM estéreo de 16 bits a 48 kHz, então um receptor PCM escreve direto em um sink padrão.

## Fala { #speech }

A detecção de fala roda em Rust em toda conexão enquanto `speech_activity` está ativado: um usuário começa a falar com o seu primeiro áudio e para depois de `speech_hangover` segundos de silêncio, 0.2 por padrão. `vc.speaking` guarda quem está falando agora, os eventos `SpeechStart` e `SpeechStop` informam isso aos listeners, e os receptores abertos com `markers=True` o trazem no seu stream. Com `speech_activity` desativado e nenhum receptor aberto, o bostie nem descriptografa o áudio que chega.

`voice.utterances(rx, min_silence=0.8, max_length=30.0)` agrupa os frames de um receptor PCM em `Utterance`s: a fala de um usuário do início ao fim, com `data` no formato do receptor, o seu `offset`, a `duration` e os segundos perdidos (`lost`). Um enunciado termina depois de `min_silence` segundos sem fala, ou em `max_length` segundos. Ele precisa de um receptor PCM sem `mix`, aberto com `markers=True`, ou de um `level` em dBFS, que então decide o que conta como fala no lugar dos marcadores.

## Gravação { #recording }

```python
--8<-- "docs/snippets/voice/call_recording.py"
```

`voice.Recorder(vc, path)` grava cada usuário no seu próprio arquivo ou, com `mix=True`, a chamada inteira em um só. O caminho é um modelo com `{guild_id}` e `{user_id}`, obrigatório para um arquivo por usuário e recusado com `mix=True`, e a extensão dele escolhe o contêiner:

- `.ogg` ou `.opus` grava Ogg Opus. Por usuário, os pacotes Opus recebidos são gravados como estão, sem decodificação.
- `.wav` grava PCM de 16 bits no `format` do gravador, 48 kHz estéreo por padrão; a taxa dele é uma de `voice.DECODE_RATES`.

Com `align=True`, o padrão, o silêncio preenche os intervalos, então cada arquivo começa quando a gravação começou e os arquivos ficam alinhados. `users`, `exclude` e `include_self` escolhem quem gravar, como para um receptor, e `buffer` (30 segundos) e `jitter` (0,3 segundo) são maiores que os padrões de um receptor, porque um disco pode travar. A gravação termina sozinha depois de `max_duration` segundos, ou assim que os arquivos dela atingem `max_size` bytes, ou quando a conexão termina.

`await recorder.stop()` a encerra assim que o áudio já ouvido estiver gravado e retorna os arquivos como um dict do ID do usuário para `Path`, com `None` como a chave de um arquivo mixado. `await recorder.wait()` espera que ela termine sozinha. `recorder.files` lista os arquivos gravados até agora, e `recorder.stats()` os contadores do receptor, em que `dropped` significa que o disco ficou para trás por mais tempo que `buffer`.

Avise as pessoas na chamada que o bot as está gravando, como o trecho de código faz.
