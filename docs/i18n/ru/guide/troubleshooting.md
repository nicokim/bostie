---
source: e7704fd7192c52cc55342cad633590bd6f185904
description: "Решение частых проблем bostie по строке лога: «Приложение не ответило», пропавшие события, предупреждения шлюза и REST, голос и сообщения об ошибках."
---

# Решение проблем

![Бости думает, пока вопросительный знак не превращается в лампочку](../assets/mascot/bostie-thinking.webp){ align=right width="180" loading=lazy }

bostie пишет в лог предупреждение о каждой ошибке, которую может заметить. На этой странице они сгруппированы по тому, что вы наблюдаете, вместе со строкой лога, которую стоит искать, и способом исправления.

## Команда пишет «Приложение не ответило» { #a-command-says-the-application-did-not-respond }

| Строка лога | Причина | Решение |
|---|---|---|
| `nothing handles interaction ... (command 'name')` | в Discord всё ещё есть команда, которую работающий бот не определяет, — часто после переименования с `auto_sync=False`, или команда находится в другом процессе | синхронизируйте команды ([«Регистрация команд»](registration.md)) |
| `nothing handles interaction ... (custom_id '...')` | ни один шаблон `on_component` или `on_modal` не совпадает с custom id | сверьте шаблон с ID, которые вы отправляете; стройте их через `route.id(...)` |
| `... returned without answering` | обработчик завершился без `respond`, `defer` или `modal` | отвечайте на каждом пути выполнения, включая ранние возвраты |
| `... returned after deferring without answering` | он отложил ответ и так и не ответил, поэтому «думает…» висит до истечения токена | вызовите `respond` после медленной работы |
| `interaction ... reached its handler ... s after it was received; something is blocking the event loop` | блокирующий код в обработчике или слушателе держал цикл событий дольше 3 секунд, которые даёт Discord | перенесите работу в `asyncio.to_thread` или уменьшите `auto_defer` |
| `interaction ... was replayed ... s after it was created; its token is dead` | взаимодействие пришло при повторной доставке событий возобновлённой сессии, слишком поздно, чтобы ответить | ничего; пользователь может запустить его снова |

Необработанное исключение в обработчике пишется в лог с трассировкой, а пользователь видит «Something went wrong.»: добавьте [хук ошибок](checks.md#error-hooks), чтобы отвечать чем-то получше.

## События не приходят { #events-do-not-arrive }

- `... listens to EVENT, which needs the INTENT intent; add it to Bot(intents=...)`: бот не запросил интент. Добавьте его, а привилегированный интент ещё и включите в Developer Portal.
- `start()` вызывает `PrivilegedIntentsRequired`: бот запросил привилегированный интент, который портал не разрешает. Включите его в разделе **Bot** → **Privileged Gateway Intents** или уберите из `intents`.
- `message.content` пустое на серверах: не хватает интента `MESSAGE_CONTENT`.
- `EVENT has an unexpected shape (...); typed listeners skip it`: Discord прислал полезную нагрузку, которая не подходит к классу события; это пишется в лог один раз на имя события. Слушатели `RawEvent` всё равно её получают; пожалуйста, сообщите об этой строке.
- `old` равен `None` в событиях обновления: объекта не было в кэше; что он хранит, см. в разделе [«Кэш»](cache.md#what-it-keeps).

## Шлюз { #the-gateway }

- `shard N paused reading: ... bytes of events are waiting because the event loop is not keeping up`: что-то блокирует цикл событий; см. [«Обратное давление»](gateway.md#backpressure).
- `close code N (...) means Discord refused what bostie sent; this is likely a bostie bug, please report it`: коды 4001, 4002, 4005 и 4008 означают, что bostie отправил то, что Discord отклонил.
- `the members of guild N stopped arriving; the cache lacks some`: запрос участников для кэша превысил тайм-аут в 30 секунд на порцию.
- `start()` вызывает `InvalidToken`: токен неверный или был сброшен; скопируйте его заново со страницы **Bot**.

## REST { #rest }

- `N invalid requests (401, 403, 429) in the last 10 minutes; Discord bans the IP at 10 000`: бот продолжает делать запросы, которые Discord отклоняет, обычно из-за нехватки прав или мёртвого вебхука. Найдите неудачные вызовы по исключениям, которые они выбрасывают.
- `RateLimited` выброшен, хотя запрос не дошёл до Discord: маршрут остаётся ограниченным дольше, чем `max_rate_limit_wait`; увеличьте его в [`HTTPSettings`](rest.md#rate-limits-retries-and-timeouts) или повторите позже.

## Ответы { #answers }

- `ephemeral=... is ignored because the message being edited is ...`: после отправки сообщение не может стать из эфемерного публичным или наоборот.
- `Discord allows 5 follow-ups to an interaction of an app installed only to the user`: шестое последующее сообщение приложения, установленного для пользователя, не отправляется.
- `the error hook ... left interaction ... unanswered; bostie answered 'Something went wrong.'`: сделайте так, чтобы хук отвечал.

## Голос { #voice }

- `nobody hears the bot in guild N: it is server muted` или `it is suppressed on this stage; use vc.stage('request')`: модератор отключил боту микрофон на сервере, или бот — слушатель на трибуне.
- `a moderator deafened the bot in guild N, so Discord sends it no audio`: приёмники ничего не получают, пока боту не включат звук обратно.
- `VoiceConnectError` с причиной `TIMEOUT`: Discord не ответил на подключение за `timeout` секунд; возможно, канал заполнен или у бота нет права «Подключаться».
- `MissingIntent: voice needs Intents.GUILD_VOICE_STATES`: голосу нужен этот интент, который входит в `Intents.DEFAULT`.

## Сообщение об ошибке { #reporting-a-bug }

`python -m bostie` печатает версии, которые нужно приложить, а `bostie.voice.versions()` добавляет libopus и DAVE для проблем с голосом. Установите нужному логгеру уровень `DEBUG` до запуска бота, как показано в разделе [«Логирование»](running.md#logging), и приложите строки вокруг проблемы, убрав из них токен.
