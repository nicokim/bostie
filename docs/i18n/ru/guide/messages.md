---
source: 9f6f43faa726c1342aeebb13122e1615b76fb827
---

# Содержимое сообщений

Сообщение собирается из одних и тех же частей, куда бы вы его ни отправляли: `ctx.respond`, `ctx.followup`, `ctx.edit` и `ctx.update`, `bot.rest.messages.create` и `edit`, а также `execute` вебхука. Каждая часть — именованный аргумент, а часть, которую вы не передали, не отправляется.

| Часть | Тип | Примечания |
|---|---|---|
| `content` | `str` | первый позиционный аргумент `respond`, `followup`, `messages.create` и `execute` |
| `embeds` | объекты `Embed` | до 10 эмбедов |
| `components` | конструкторы `ui` | кнопки, меню выбора и компоненты разметки; см. [«Компоненты»](components.md) |
| `files` | объекты `File` | загрузки; см. ниже |
| `allowed_mentions` | `AllowedMentions` | какие упоминания могут пинговать; см. [«Разрешённые упоминания»](responses.md#allowed-mentions) |
| `flags` | `MessageFlags` | например `SUPPRESS_EMBEDS` и `SUPPRESS_NOTIFICATIONS` |
| `tts` | `bool` | зачитать содержимое вслух всем, у кого открыт канал |
| `poll` | `PollCreateRequest` | опрос; см. ниже |
| `ephemeral` | `bool` | только для ответов на взаимодействия |

`bot.rest.messages.create` также принимает `reply_to`, `message_reference`, `sticker_ids`, `nonce`, `enforce_nonce` и `shared_client_theme`, а ещё `delete_after`, как и [ответы на взаимодействия](responses.md#messages-that-delete-themselves).

## Эмбеды и файлы { #embeds-and-files }

```python
--8<-- "docs/snippets/responses/rich_messages.py"
```

`Embed` и его части (`EmbedField`, `EmbedFooter`, `EmbedAuthor`, `EmbedMedia`) — неизменяемые модели с именованными аргументами; подходит и обычный `dict` в формате JSON Discord. `color` принимает `int`, например `0x5865F2`, а собрать его помогают функции из [`bostie.color`](formatting.md#colors).

`File(name, data)` загружает `data` под именем `name`. `description` — его альтернативный текст, `spoiler=True` размывает его, а `title` задаёт заголовок, который показывает Discord. Эмбед или компонент ссылается на загрузку как `attachment://name`, как диаграмма в примере выше. bostie не проверяет размеры: лимит загрузки в Discord зависит от бустов сервера и Nitro пользователя.

## Опросы { #polls }

`PollCreateRequest` принимает `question` и `answers` — оба типа `PollMedia` с `text` и, для ответов, необязательным `emoji`. `duration` задаётся в часах, по умолчанию 24, а `allow_multiselect=True` позволяет пользователю выбрать несколько ответов. `bot.rest.polls.end(channel_id, message_id)` досрочно завершает опрос, `bot.rest.polls.list_answer_voters(...)` перечисляет, кто выбрал ответ, а [события](events.md) `PollVoteAdd` и `PollVoteRemove` сообщают о голосах по мере их появления.

## Изменение и вложения { #editing-and-attachments }

Изменение принимает части как именованные аргументы, а также `attachments`. Часть, которую вы не передали, остаётся, а `None` её очищает: `ctx.edit(embeds=None)` удаляет эмбеды и оставляет содержимое. Если изменение передаёт `files` или `attachments`, в сообщении остаются только вложения, перечисленные в `attachments` (объектами `Attachment` или ID), плюс новые файлы.

## Components V2 { #components-v2 }

Сообщение, в `components` которого есть компонент разметки (`ui.text`, `ui.section`, `ui.container`, `ui.media_gallery`, `ui.file`, `ui.separator` или `ui.thumbnail`), — это сообщение Components V2: bostie ставит флаг `IS_COMPONENTS_V2`, и компоненты составляют всё сообщение. У такого сообщения не может быть `content`, `embeds`, стикеров или опроса, и bostie вызывает `ValueError`, если вы их передаёте; поместите текст в `ui.text`. Кнопки и меню выбора, находящиеся только в `ui.row`, работают и в классических сообщениях с содержимым.

## Ответы { #replies }

```python
--8<-- "docs/snippets/responses/replies.py"
```

`reply_to` принимает ID сообщения в том же канале. Чтобы переслать сообщение, передайте вместо этого `message_reference=MessageReference(type=MessageReferenceType.FORWARD, message_id=..., channel_id=...)`; одновременно их использовать нельзя. Чтение `message.content` на серверах требует привилегированного интента Message Content, как объясняет [руководство по шлюзу](gateway.md#intents).

## Индикатор набора текста { #typing-indicator }

```python
--8<-- "docs/snippets/responses/typing_indicator.py"
```

`async with ctx.typing():` показывает, что бот печатает в канале взаимодействия, пока выполняется блок. bostie отправляет индикатор в начале блока и затем каждые 9 секунд, поскольку Discord показывает его 10 секунд, и прекращает, когда блок заканчивается, в том числе из-за исключения. `await ctx.typing()` показывает его один раз. Вне взаимодействия то же самое делает `bot.rest.channels.trigger_typing_indicator(channel_id)` — при ожидании или в `async with`. Сообщение, которое бот отправляет в канал, сразу убирает индикатор.

## Голосовые сообщения { #voice-messages }

```python
--8<-- "docs/snippets/responses/voice_memo.py"
```

`await voice.message(audio)` читает путь к файлу или байты, измеряет длительность, вычисляет волновую форму, которую рисует Discord, и возвращает `File`, готовый к отправке. Формат определяется по расширению имени; клиенты Discord записывают моно Ogg Opus 48 кГц. Голосовое сообщение — это только этот файл и ничего больше: ни содержимого, ни эмбедов, ни компонентов, ни опроса, ни стикеров. bostie ставит флаг `IS_VOICE_MESSAGE` и вызывает `ValueError`, если в сообщении есть что-то ещё. `File`, который вы собираете сами, становится голосовым сообщением, если у него есть `duration_secs` и `waveform` — 8-битные отсчёты, которые рисует Discord.
