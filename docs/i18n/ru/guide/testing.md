---
source: a5646223563b83584ded4c6da42da70ad55b98c7
---

# Тестирование бота

![Бости ищет с лупой, пока не появляется галочка](../assets/mascot/bostie-detective.webp){ align=right width="180" loading=lazy }

`bostie.testing` запускает фейковый Discord на localhost: шлюз, REST API с маршрутами, которыми боты пользуются чаще всего, и, по желанию, голосовой сервер. Ваш настоящий бот подключается к нему, а тест управляет стороной Discord: отправляет события и взаимодействия, а затем проверяет, что бот прислал в ответ. Для него нужна дополнительная зависимость `testing` ([«Установка»](../install.md)), и на нём работает собственный набор тестов bostie.

```python
--8<-- "docs/snippets/testing/test_greeter.py"
```

`run(scenario)` запускает `FakeDiscord`, выполняет на нём `async def scenario(fake)` в пределах 10 секунд и останавливает его; это обычная функция, поэтому pytest не нужен плагин. `async with running(bot, fake):` запускает бота на фейке, ждёт, пока он будет готов, и закрывает его в конце. Направьте бота на фейк через `Bot(api_url=fake.api_url)` или задайте `BOSTIE_API_URL`, если бот создаётся в другом месте.

## Управление стороной Discord { #driving-discords-side }

| Вызов | Что делает |
|---|---|
| `await fake.interaction(type, data)` | отправляет `INTERACTION_CREATE` и возвращает первый ответ бота как запрос `Recorded` |
| `await fake.dispatch(name, d, shard=0)` | отправляет любое событие шлюза и возвращает его порядковый номер |
| `fake.expect_callback(payload)` | для взаимодействия, которое вы отправляете сами: future для его первого ответа |
| `await fake.send_op(op, d)` | отправляет опкод, не являющийся диспетчеризацией, например 7 (reconnect) или 9 (invalid session) |
| `await fake.close_gateway(code)`, `fake.drop_gateway()` | закрывает соединение с кодом закрытия или обрывает его без кода |
| `fake.hold_acks()` | перестаёт отвечать на heartbeat, как мёртвое соединение |
| `await fake.next_identify()`, `await fake.next_resume()` | ждёт следующего IDENTIFY или RESUME от бота |

Типы взаимодействий — это числа Discord: 2 для команды, 3 для компонента, 4 для автодополнения, 5 для отправки модального окна.

`bostie.testing.payloads` строит полезные нагрузки Discord по примерам из его документации, со свежими ID:

- `command(name, *options)`, `option(name, type, value, focused=False)`, `subcommand(...)`, `group(...)` и `resolved()` — для команд и автодополнения;
- `context_menu(type, name, target_id, resolved)` — для команд пользователя и сообщения;
- `button(custom_id)`, `select(custom_id, *values)` и `modal(custom_id)` — для компонентов и модальных окон;
- `message(...)`, `guild(...)`, `member(...)`, `user(...)`, `role(...)`, `channel(...)`, `voice_channel(...)`, `stage_channel(...)` и `voice_state(...)` — для событий, каждая с именованными аргументами, переопределяющими поля;
- `fixture(name)` — для любой задокументированной полезной нагрузки, которая есть в bostie, и `snowflake()` — для нового ID.

## Проверка того, что отправил бот { #checking-what-the-bot-sent }

`fake.requests` перечисляет каждый REST-запрос как `Recorded`, а `fake.expect_request(method, route_prefix)` возвращает future для следующего подходящего запроса — для запроса, который бот делает сам, например из задачи. У `Recorded` есть `method`, `path` (относительно базового URL API), `query`, `headers`, `json`, а для загрузок — `parts`. В ответе `Recorded` на взаимодействие тело обратного вызова лежит в `json`, как выше. `fake.gateway_received` — очередь полезных нагрузок, которые бот отправил через шлюз, а `fake.connections` — соединения со шлюзом, объекты `Connection` с их полученными полезными нагрузками `received`, `shard` и `close_code`.

`fake.cdn_file(path, data)` отдаёт `data` по URL в CDN, который возвращает, — для вложения или ресурса, который скачивает бот, а `fake.cdn_requests` записывает эти скачивания.

Фейк хранит состояние, нужное этим маршрутам, и тесты могут его читать или заполнять: `fake.guilds`, `fake.channels`, `fake.users`, `fake.members`, `fake.messages`, `fake.commands` и `fake.guild_commands`, а также `fake.put_count` для синхронизаций команд. `fake.ready_guilds` задаёт ID серверов, которые перечисляет `READY`.

## Сбои и тайминг { #failures-and-timing }

- `fake.fail(method, route_prefix, status, json)` отвечает на следующий подходящий запрос ошибкой и подменяет маршрут, который фейк не обслуживает.
- `fake.rate_limit(method, route_prefix, scope="user", retry_after=0.1)` отвечает на него 429.
- `fake.answer(method, route_prefix, body, content_type)` отвечает на него телом не в формате JSON.
- `fake.hold(method, route_prefix)` держит его в ожидании, пока вы не установите возвращённый `asyncio.Event`; `fake.hold_identify(shard)` и `fake.hold_hello()` делают то же для рукопожатия шлюза.
- `await settle()` несколько раз уступает управление циклу событий, чтобы выполнились обратные вызовы, запланированные вашими действиями.

Чтобы дождаться того, что бот делает в слушателе, пусть он [отправит собственное событие](events.md#events-of-your-own), и ждите его, как делает второй тест, а не засыпайте.

`FakeDiscord(compress=True)` сжимает шлюз через zstd, для чего нужен `compression.zstd` из Python 3.14; `heartbeat_interval` задаёт интервал, который он запрашивает, по умолчанию 41 250 мс, как у Discord.

## Голос { #voice }

`FakeDiscord(voice=True)` добавляет голосовой канал и трибуну, отвечает на обновления голосового состояния и запускает голосовой шлюз и UDP-сервер как `fake.voice`, так что `ctx.voice.connect()` и `vc.play(...)` работают с ним. DAVE он не поддерживает; это покрывают тесты bostie на Rust. Тест может:

- ждать того, что отправил бот, через `await fake.next_voice_state_update()`, `await fake.voice.next_identify()` и `await fake.voice.wait_for_packets(count)`, который возвращает расшифрованные RTP-пакеты как объекты `Packet`; каждое голосовое подключение — это `VoiceSession` в `fake.voice.sessions` на `VoiceSocket`, а `fake.voice.op(op, d)` отправляет последнему любой голосовой опкод;
- действовать от имени других пользователей через `fake.voice.clients_connect(user_ids)`, `fake.voice.client_disconnect(user_id)`, `fake.voice.speaking(user_id, ssrc, flags)`, `fake.voice.send_audio(...)`, который отправляет боту пакеты Opus, `fake.voice.send_rtcp(packet)`, который отправляет ему пакет RTCP, и `fake.voice.send_datagram(data)`, который отправляет байты без шифрования, как это сделал бы неисправный сервер;
- перемещать или отключать бота, как это сделал бы модератор, через `fake.move_bot(channel_id)` и `fake.kick_bot()`;
- оставить следующее обновление голосового состояния без ответа, как Discord делает, когда канал заполнен, через `fake.hold_voice_state()`, пока не будет установлен возвращённый `asyncio.Event`;
- задать `fake.mute`, `fake.deaf` и `fake.suppress` для голосовых состояний, которые он отправляет о боте, и `voice_order` для порядка двух событий, которые Discord отправляет при подключении.
