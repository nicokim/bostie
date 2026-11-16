---
source: 4108c4a61370aa8ef893c1d65371c3d654e43c09
description: "Подключение к голосовым каналам и трибунам Discord: настройки, сквозное шифрование DAVE, голосовые события, остановка медиапотока и внешние голосовые движки."
---

# Голосовые подключения

![Бости слушает в наушниках](../assets/mascot/bostie-listen.webp){ align=right width="180" loading=lazy }

Голосовой движок bostie работает в его ядре на Rust: голосовой WebSocket, транспорт UDP, такт отправки 20 мс, декодирование и ресемплинг, микшер, кодирование и декодирование Opus, транспортное шифрование и сквозное шифрование DAVE. Python просит его подключиться, проигрывать и принимать звук; тайминг никогда не зависит от цикла событий, кроме того, что Python должен держать буфер каждого синка заполненным.

Голосу нужен `Intents.GUILD_VOICE_STATES`, который входит в `Intents.DEFAULT`. У бота не больше одного голосового подключения на сервер.

```python
--8<-- "docs/snippets/voice/voice_connection.py"
```

## Подключение { #connecting }

`await ctx.voice.connect()` подключается к голосовому каналу или трибуне пользователя, который запустил команду, и возвращается, как только звук может идти в обе стороны. Он находит канал пользователя в кэше или спрашивает Discord, если сервера нет в кэше, и вызывает `NotInVoice`, если пользователь не находится ни в одном голосовом канале сервера или взаимодействие пришло из личных сообщений. `ctx.voice.current` — подключение бота на этом сервере или `None`.

Вне обработчика `await bot.voice.connect(channel_id)` подключается к каналу по ID. На сервере, где бот уже подключён, он переносит подключение и применяет только переданные параметры. `bot.voice.get(guild_id)` и `bot.voice.connections` возвращают открытые подключения, а `await bot.voice.close()` покидает их все, как это делает `bot.close()`.

Подключение, которое не стало готовым за `timeout` секунд, вызывает `VoiceConnectError` с причиной `TIMEOUT`; Discord оставляет без ответа подключение к заполненному каналу или к каналу, к которому боту нельзя подключаться. Другие сбои вызывают `VoiceConnectError` с причиной, кодом закрытия и сообщением Discord.

## Настройки { #settings }

`Bot(voice=voice.Defaults(...))` задаёт, с чем начинает каждое подключение, а `connect()` принимает настройки подключения для одного вызова:

| Поле `voice.Defaults` | По умолчанию | Значение |
|---|---|---|
| `self_mute`, `self_deaf` | `False` | отключение микрофона и звука самим ботом |
| `timeout` | 10.0 | сколько секунд ждать подключения, перемещения или изменения |
| `server_change_delay` | 0.0 | сколько секунд ждать, прежде чем последовать за Discord на новый голосовой сервер |
| `encoder` | `EncoderSettings()` | кодировщик Opus; см. [«Воспроизведение звука»](playback.md#encoder) |
| `speaking_flags` | `MICROPHONE` | `voice.SpeakingFlags`, которые бот объявляет, когда проигрывает звук |
| `stage` | `"listen"` | на трибуне: `"listen"`, `"request"`, чтобы попросить слово, или `"speak"` |
| `speech_hangover` | 0.2 | сколько секунд тишины должно пройти, чтобы говорящий считался замолчавшим, от 0,02 до 10 |
| `speech_activity` | `True` | срабатывают ли события начала и конца речи |
| `stall_after` | 2.0 | сколько секунд медиапоток может стоять, прежде чем об этом сообщит `MediaStalled`, не меньше 0,02 |
| `sink_buffer`, `stall` | 0.2, `"hold"` | значения по умолчанию для каждого [синка](sinks.md) |
| `receive_buffer`, `jitter`, `overflow`, `receive_stall` | 2.0, 0.1, `"drop_oldest"`, `"wait"` | значения по умолчанию для каждого [приёмника](receiving.md) |
| `crossfade`, `history` | 0.0, 0 | значения по умолчанию для каждой [очереди](playback.md#queues) |

`connect()` принимает `self_mute`, `self_deaf`, `timeout`, `server_change_delay`, `encoder`, `speaking_flags`, `stage`, `speech_hangover`, `speech_activity` и `stall_after` как именованные аргументы; непереданный аргумент или `None` сохраняет значение по умолчанию. `connect_external()` принимает первые три, `move()` — `timeout`, а `edit()` — `self_mute`, `self_deaf` и `timeout`. Недопустимые значения вызывают `ValueError` при создании `Defaults`. У строковых настроек литеральные типы `voice.StageRole`, `voice.SinkStall`, `voice.ReceiveStall`, `voice.Overflow`, `voice.Sample`, `voice.OpusApplication`, `voice.OpusBandwidth` и `voice.OpusSignal`. `ctx.voice` — это `voice.ContextVoice`, а `bot.voice` — `voice.VoiceManager`.

## Подключение как объект { #the-connection }

| `VoiceConnection` | Значение |
|---|---|
| `guild_id`, `channel_id` | где оно находится |
| `state` | `voice.State`: `CONNECTING`, `READY`, `RESUMING`, `RECONNECTING` или `DISCONNECTED` |
| `members` | ID других пользователей в звонке |
| `speaking` | пользователи, которые сейчас говорят, с моментом `time.monotonic()`, когда они начали |
| `ssrcs` | каждый SSRC в звонке с ID его пользователя |
| `self_mute`, `self_deaf` | отключение микрофона и звука самим ботом в том виде, в каком их применил Discord |
| `mute`, `deaf`, `suppress` | отключил ли модератор боту микрофон или звук и запрещено ли ему говорить на этой трибуне |
| `latency` | `Latency` со временем кругового пути WebSocket и UDP в секундах; `None`, пока не измерено |
| `dave` | сквозное шифрование звонка; см. ниже |
| `sinks` | открытые синки, начиная со старейшего |
| `encoder`, `speaking_flags`, `speech_hangover`, `speech_activity`, `stall_after` | можно менять во время подключения |

- `await vc.move(channel_id)` переходит в другой голосовой канал или трибуну сервера и возвращается, как только там пошёл медиапоток.
- `await vc.edit(self_mute=..., self_deaf=...)` меняет отключение микрофона или звука самим ботом, оставляя непереданное как есть, и возвращается, когда Discord это применил.
- `await vc.stage("request")` просит слово на трибуне, `"speak"` начинает говорить (при наличии на это права), `"listen"` возвращает к слушателям.
- `await vc.wait_until(voice.State.READY)` ждёт состояния и вызывает `VoiceNotConnected`, если подключение завершится раньше.
- `vc.reconnect()` обрывает голосовой WebSocket и возобновляет сессию на новом.
- `vc.stop()` закрывает все синки, так что ничего не играет.
- `await vc.disconnect()` покидает канал и возвращается, когда подключение завершилось, — не позже чем через 2 секунды.

Вызовы на завершённом подключении вызывают `VoiceNotConnected`.

Подключение восстанавливается само. Оборвавшийся WebSocket возобновляет сессию, потерянная сессия переподключается, а когда модератор перемещает бота, тот следует в новый канал и отправляет событие `Moved`. Когда модератор отключает бота или удаляет канал, подключение завершается с причиной `REMOVED`. bostie предупреждает, когда бот проигрывает звук, хотя ему отключили микрофон на сервере или запретили говорить на трибуне, и когда приёмники ждут, хотя боту отключили звук.

## Сквозное шифрование { #end-to-end-encryption }

bostie поддерживает DAVE — сквозное шифрование голоса в Discord, и ничего настраивать не нужно: он входит в группу шифрования звонка, шифрует то, что отправляет, и расшифровывает то, что получает. `vc.dave` описывает его как `voice.Dave`:

- `protocol_version` — используемый протокол DAVE, 0 — если никакой;
- `epoch` считает изменения состава участников группы;
- `privacy_code` — код, который клиенты Discord показывают для звонка;
- `await vc.dave.verification_code(user_id)` возвращает код, который нужно сверить с другим пользователем по отдельному каналу связи, чтобы убедиться, что посередине никого нет.

Событие `DaveTransition` сообщает о каждом изменении. Канал, который требует DAVE от клиента, не предлагающего его, закрывается с причиной `DAVE_REQUIRED`, а неудавшийся шаг DAVE вызывает `DaveError` или завершает подключение с `DAVE_FAILED`.

## События { #events }

Голосовые подключения отправляют свои события через `@bot.listen`. Каждое — это `voice.VoiceEvent` с `guild_id`. `StateChange` в `DISCONNECTED` несёт `voice.DisconnectReason`: `REQUESTED` (был вызван `disconnect()`), `REMOVED` (модератор отключил бота или удалил канал), `CALL_TERMINATED`, `RATE_LIMITED`, `TIMEOUT`, `SESSION_INVALID`, `AUTHENTICATION_FAILED`, `DAVE_REQUIRED`, `DAVE_FAILED`, `PROTOCOL_ERROR` или `CLOSED` (бот закрылся).

| Событие | Поля | Когда отправляется |
|---|---|---|
| `StateChange` | `old`, `new`, `reason`, `code`, `detail` | подключение сменило состояние |
| `Moved` | `old_channel_id`, `new_channel_id`, `by_us` | бот переместился — через `move()` или по воле модератора |
| `DaveTransition` | `protocol_version`, `epoch`, `transition_id` | изменилось сквозное шифрование |
| `MediaStalled`, `MediaResumed` | `direction` (`voice.Direction.SEND` или `RECEIVE`), `duration` | отправка или приём остановились или возобновились |
| `UserJoin`, `UserLeave` | `user_id` | пользователь присоединился к звонку или покинул его |
| `SpeakingUpdate` | `user_id`, `ssrc`, `flags` | пользователь объявил свой SSRC и флаги речи |
| `SpeechStart`, `SpeechStop` | `user_id`, `ssrc`, `at`, а при остановке — `duration` | пользователь начал или перестал говорить |
| `RtcpPacket` | `packet_type`, `count`, `ssrc`, `user_id`, `reports`, `data` | голосовой сервер прислал пакет RTCP; каждый `ReceptionReport` в `reports` содержит `fraction_lost`, `lost`, `highest_sequence`, `jitter`, `last_sender_report` и `delay` одного потока |

Они находятся в `bostie.voice`. `SpeechStart` и `SpeechStop` срабатывают, только пока включён `speech_activity`.

## Остановка медиапотока { #stalled-media }

```python
--8<-- "docs/snippets/voice/media_stalls.py"
```

`MediaStalled` с `direction` `SEND` срабатывает, когда звук ждёт в синке, но не может уйти в течение `stall_after` секунд, — во время переподключения или перехода DAVE. С `RECEIVE` — когда пакеты доходят до открытого приёмника, но ни один из них столько времени не удаётся открыть или проиграть. Когда медиапоток снова идёт, следует `MediaResumed`, чья `duration` отсчитывается от начала остановки. `stall_after` равен 2 секундам, если `voice.Defaults`, `connect(stall_after=...)` или `vc.stall_after` не задают иное.

## Выход из пустого канала { #leaving-an-empty-channel }

Бот остаётся в канале, пока не отключится. Чтобы он уходил вслед за последним пользователем:

```python
--8<-- "docs/snippets/voice/leave_when_alone.py"
```

## Ошибки { #errors }

Ошибки голоса — это `VoiceError`: `VoiceConnectError` (подключение завершилось, не став готовым; `reason`, `code`, `detail`), `NotInVoice`, `VoiceNotConnected`, `AlreadyConnected`, `VoiceQueueFull` (слишком много команд ждут подключения), `DaveError`, а для звука — `SourceError`, `SinkClosed`, `BufferFull`, `Empty` и `ReceiverClosed`, которые объясняются на следующих страницах.

## Внешние голосовые движки { #external-voice-engines }

```python
--8<-- "docs/snippets/voice/external_engine.py"
```

`await bot.voice.connect_external(guild_id, channel_id)` подключается к каналу для голосового движка, который работает где-то ещё, и возвращает `ExternalVoice` с `state` (`VoiceStateUpdate`) и `server` (`VoiceServerUpdate`), с которыми подключается движок. После этого bostie не отправляет звук на этом сервере. `async with external.updates() as updates:` доставляет последующие голосовые серверы сервера и собственные голосовые состояния бота, а `await external.disconnect()` покидает канал. Метод вызывает `AlreadyConnected` на сервере, где bostie держит собственное подключение, и принимает `self_mute`, `self_deaf` и `timeout`.

## Версии { #versions }

`voice.versions()` возвращает версии bostie, libopus, библиотеки DAVE и наивысшего протокола DAVE, скомпилированных в сборку.
