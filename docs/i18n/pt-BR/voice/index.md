---
source: 5246991ba1478ea5d33d14802d1a46c835ecb91e
---

# Conexões de voz

![Bostie ouvindo com fones de ouvido](../assets/mascot/bostie-listen.webp){ align=right width="180" loading=lazy }

O motor de voz do bostie roda no seu núcleo em Rust: o WebSocket de voz, o transporte UDP, o relógio de envio de 20 ms, a decodificação e a reamostragem, o mixer, a codificação e a decodificação Opus, a criptografia de transporte e a criptografia de ponta a ponta DAVE. O Python pede a ele que entre, reproduza e receba; o tempo nunca depende do event loop, exceto que o Python precisa manter cheio o buffer de cada sink.

A voz precisa de `Intents.GUILD_VOICE_STATES`, que `Intents.DEFAULT` inclui. O bot tem no máximo uma conexão de voz por servidor.

```python
--8<-- "docs/snippets/voice/voice_connection.py"
```

## Conectando { #connecting }

`await ctx.voice.connect()` entra no canal de voz ou de palco do usuário que executou o comando e retorna assim que o áudio pode fluir nos dois sentidos. Ele encontra o canal do usuário no cache, ou pergunta ao Discord quando o servidor não está em cache, e levanta `NotInVoice` quando o usuário não está em nenhum canal de voz do servidor ou quando a interação veio de uma mensagem direta. `ctx.voice.current` é a conexão do bot naquele servidor, ou `None`.

Fora de um handler, `await bot.voice.connect(channel_id)` entra em um canal pelo ID. Em um servidor onde o bot já está conectado, ele move a conexão e aplica só as opções passadas. `bot.voice.get(guild_id)` e `bot.voice.connections` retornam as conexões abertas, e `await bot.voice.close()` sai de todas elas, como `bot.close()` faz.

Uma entrada que não fica pronta em até `timeout` segundos levanta `VoiceConnectError` com o motivo `TIMEOUT`; o Discord deixa sem resposta uma entrada em um canal cheio, ou em um ao qual o bot não pode se conectar. As outras falhas levantam `VoiceConnectError` com o motivo, o código de fechamento e a mensagem do Discord.

## Configurações { #settings }

`Bot(voice=voice.Defaults(...))` define com o que toda conexão começa, e `connect()` aceita as configurações de conexão para uma chamada:

| Campo de `voice.Defaults` | Padrão | Significado |
|---|---|---|
| `self_mute`, `self_deaf` | `False` | o próprio silenciamento e ensurdecimento do bot |
| `timeout` | 10.0 | segundos para esperar uma entrada, uma mudança de canal ou uma edição |
| `server_change_delay` | 0.0 | segundos para esperar antes de seguir o Discord para um novo servidor de voz |
| `encoder` | `EncoderSettings()` | o codificador Opus; veja [Reproduzindo áudio](playback.md#encoder) |
| `speaking_flags` | `MICROPHONE` | as `voice.SpeakingFlags` que o bot anuncia quando reproduz |
| `stage` | `"listen"` | em um palco: `"listen"`, `"request"` para pedir para falar, ou `"speak"` |
| `speech_hangover` | 0.2 | segundos de silêncio antes de um falante contar como parado, de 0.02 a 10 |
| `speech_activity` | `True` | se os eventos de início e fim de fala disparam |
| `stall_after` | 2.0 | segundos que a mídia pode ficar parada antes que `MediaStalled` o informe, no mínimo 0.02 |
| `sink_buffer`, `stall` | 0.2, `"hold"` | os padrões de todo [sink](sinks.md) |
| `receive_buffer`, `jitter`, `overflow`, `receive_stall` | 2.0, 0.1, `"drop_oldest"`, `"wait"` | os padrões de todo [receptor](receiving.md) |
| `crossfade`, `history` | 0.0, 0 | os padrões de toda [fila](playback.md#queues) |

`connect()` aceita `self_mute`, `self_deaf`, `timeout`, `server_change_delay`, `encoder`, `speaking_flags`, `stage`, `speech_hangover`, `speech_activity` e `stall_after` como argumentos nomeados; um omitido, ou `None`, mantém o padrão. `connect_external()` aceita os três primeiros, `move()` aceita `timeout`, e `edit()` aceita `self_mute`, `self_deaf` e `timeout`. Valores inválidos levantam `ValueError` quando o `Defaults` é montado. As configurações em string têm os tipos literais `voice.StageRole`, `voice.SinkStall`, `voice.ReceiveStall`, `voice.Overflow`, `voice.Sample`, `voice.OpusApplication`, `voice.OpusBandwidth` e `voice.OpusSignal`. `ctx.voice` é um `voice.ContextVoice` e `bot.voice` um `voice.VoiceManager`.

## A conexão { #the-connection }

| `VoiceConnection` | Valor |
|---|---|
| `guild_id`, `channel_id` | onde ela está |
| `state` | um `voice.State`: `CONNECTING`, `READY`, `RESUMING`, `RECONNECTING` ou `DISCONNECTED` |
| `members` | os IDs dos outros usuários na chamada |
| `speaking` | os usuários falando agora, mapeados para o `time.monotonic()` em que começaram |
| `ssrcs` | cada SSRC na chamada mapeado para o ID do seu usuário |
| `self_mute`, `self_deaf` | o próprio silenciamento e ensurdecimento do bot, como o Discord os aplicou |
| `mute`, `deaf`, `suppress` | se um moderador silenciou ou ensurdeceu o bot, ou se ele não pode falar neste palco |
| `latency` | uma `Latency` com os tempos de ida e volta do WebSocket e do UDP em segundos, `None` até serem medidos |
| `dave` | a criptografia de ponta a ponta da chamada; veja abaixo |
| `sinks` | os sinks abertos, do mais antigo para o mais novo |
| `encoder`, `speaking_flags`, `speech_hangover`, `speech_activity`, `stall_after` | podem ser definidos enquanto conectado |

- `await vc.move(channel_id)` passa para outro canal de voz ou de palco do servidor e retorna assim que a mídia flui lá.
- `await vc.edit(self_mute=..., self_deaf=...)` muda o próprio silenciamento ou ensurdecimento do bot, deixando como está o que não for passado, e retorna assim que o Discord o aplicou.
- `await vc.stage("request")` pede para falar em um palco, `"speak"` fala (com a permissão para isso), `"listen"` volta para a plateia.
- `await vc.wait_until(voice.State.READY)` espera um estado e levanta `VoiceNotConnected` se a conexão terminar antes.
- `vc.reconnect()` derruba o WebSocket de voz e retoma em um novo.
- `vc.stop()` fecha todos os sinks, então nada toca.
- `await vc.disconnect()` sai do canal e retorna assim que a conexão terminou, no máximo 2 segundos depois.

Chamadas em uma conexão que terminou levantam `VoiceNotConnected`.

A conexão se recupera sozinha. Um WebSocket que caiu é retomado, uma sessão perdida se reconecta e, quando um moderador move o bot, ele segue para o novo canal e envia um evento `Moved`. Quando um moderador o desconecta ou exclui o canal, ela termina com o motivo `REMOVED`. O bostie avisa quando o bot reproduz enquanto está silenciado no servidor ou suprimido em um palco, e quando os receptores esperam enquanto ele está ensurdecido.

## Criptografia de ponta a ponta { #end-to-end-encryption }

O bostie fala DAVE, a criptografia de ponta a ponta do Discord para voz, sem nada para configurar: ele entra no grupo de criptografia da chamada, criptografa o que envia e descriptografa o que recebe. `vc.dave` a informa como um `voice.Dave`:

- `protocol_version` é o protocolo DAVE em uso, 0 para nenhum;
- `epoch` conta as mudanças dos membros do grupo;
- `privacy_code` é o código que os clientes do Discord mostram para a chamada;
- `await vc.dave.verification_code(user_id)` retorna o código a comparar com outro usuário por fora do Discord, para verificar que ninguém mais está no meio.

Um evento `DaveTransition` informa cada mudança. Um canal que exige DAVE de um cliente que não o oferece é fechado com o motivo `DAVE_REQUIRED`, e uma etapa do DAVE que falha levanta `DaveError` ou encerra a conexão com `DAVE_FAILED`.

## Eventos { #events }

As conexões de voz enviam os seus eventos por `@bot.listen`. Cada um é um `voice.VoiceEvent` com o `guild_id`. Um `StateChange` para `DISCONNECTED` traz um `voice.DisconnectReason`: `REQUESTED` (`disconnect()` rodou), `REMOVED` (um moderador desconectou o bot ou excluiu o canal), `CALL_TERMINATED`, `RATE_LIMITED`, `TIMEOUT`, `SESSION_INVALID`, `AUTHENTICATION_FAILED`, `DAVE_REQUIRED`, `DAVE_FAILED`, `PROTOCOL_ERROR` ou `CLOSED` (o bot fechou).

| Evento | Campos | Enviado quando |
|---|---|---|
| `StateChange` | `old`, `new`, `reason`, `code`, `detail` | a conexão mudou de estado |
| `Moved` | `old_channel_id`, `new_channel_id`, `by_us` | o bot mudou de canal, por `move()` ou por um moderador |
| `DaveTransition` | `protocol_version`, `epoch`, `transition_id` | a criptografia de ponta a ponta mudou |
| `MediaStalled`, `MediaResumed` | `direction` (`voice.Direction.SEND` ou `RECEIVE`), `duration` | o envio ou a recepção parou, ou recomeçou |
| `UserJoin`, `UserLeave` | `user_id` | um usuário entrou na chamada ou saiu dela |
| `SpeakingUpdate` | `user_id`, `ssrc`, `flags` | um usuário anunciou o seu SSRC e as suas flags de fala |
| `SpeechStart`, `SpeechStop` | `user_id`, `ssrc`, `at` e, no fim, `duration` | um usuário começou ou parou de falar |
| `RtcpPacket` | `packet_type`, `count`, `ssrc`, `user_id`, `reports`, `data` | o servidor de voz enviou um pacote RTCP; cada `ReceptionReport` em `reports` tem o `fraction_lost`, o `lost`, o `highest_sequence`, o `jitter`, o `last_sender_report` e o `delay` de um stream |

Eles ficam em `bostie.voice`. `SpeechStart` e `SpeechStop` só disparam enquanto `speech_activity` está ativado.

## Mídia parada { #stalled-media }

```python
--8<-- "docs/snippets/voice/media_stalls.py"
```

`MediaStalled` com `direction` `SEND` dispara quando o áudio espera em um sink mas não consegue sair por `stall_after` segundos, durante uma reconexão ou uma transição do DAVE. Com `RECEIVE`, os pacotes chegam a um receptor aberto, mas nenhum pode ser aberto ou reproduzido por esse tempo. `MediaResumed` vem em seguida assim que a mídia volta a fluir, com o seu `duration` contado a partir do início da parada. `stall_after` é 2 segundos, a menos que `voice.Defaults`, `connect(stall_after=...)` ou `vc.stall_after` diga outra coisa.

## Saindo de um canal vazio { #leaving-an-empty-channel }

O bot fica em um canal até se desconectar. Para sair quando o último usuário sair:

```python
--8<-- "docs/snippets/voice/leave_when_alone.py"
```

## Erros { #errors }

Os erros de voz são `VoiceError`s: `VoiceConnectError` (uma entrada terminou antes de ficar pronta; `reason`, `code`, `detail`), `NotInVoice`, `VoiceNotConnected`, `AlreadyConnected`, `VoiceQueueFull` (comandos demais esperando pela conexão), `DaveError` e, para áudio, `SourceError`, `SinkClosed`, `BufferFull`, `Empty` e `ReceiverClosed`, que as próximas páginas explicam.

## Motores de voz externos { #external-voice-engines }

```python
--8<-- "docs/snippets/voice/external_engine.py"
```

`await bot.voice.connect_external(guild_id, channel_id)` entra em um canal para um motor de voz que roda em outro lugar e retorna um `ExternalVoice` com o `state` (`VoiceStateUpdate`) e o `server` (`VoiceServerUpdate`) com que o motor se conecta. O bostie então não envia áudio nesse servidor. `async with external.updates() as updates:` entrega os servidores de voz seguintes do servidor e os próprios estados de voz do bot, e `await external.disconnect()` sai do canal. Ele levanta `AlreadyConnected` em um servidor onde o bostie roda a sua própria conexão, e aceita `self_mute`, `self_deaf` e `timeout`.

## Versões { #versions }

`voice.versions()` retorna as versões do bostie, da libopus, da biblioteca DAVE e do protocolo DAVE mais alto compilados na build.
