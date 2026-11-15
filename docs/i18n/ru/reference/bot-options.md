---
source: 104f50c5749c73fc5952a47fa18309cb11805eb3
---

# Параметры Bot

Все параметры `Bot(...)`, каждый только именованный. Руководства объясняют каждый из них в контексте.

## Шлюз и идентификация { #gateway-and-identity }

| Параметр | По умолчанию | Значение |
|---|---|---|
| `intents` | `Intents.DEFAULT` | [интенты](../guide/gateway.md#intents), которые нужно запросить; значение по умолчанию включает все интенты, кроме трёх привилегированных |
| `status` | `Status.ONLINE` | статус [присутствия](../guide/gateway.md#presence) при входе |
| `activities` | `()` | активности при входе, построенные через `bostie.activity` |
| `afk` | `False` | сообщает ли бот, что он отошёл |
| `idle_since` | `None` | `datetime`, с которого бот неактивен |
| `large_threshold` | 50 | от 50 до 250: число участников, выше которого Discord не включает участников не в сети в полезную нагрузку сервера |
| `shard_count` | `"auto"` | число [шардов](../guide/sharding.md) или `"auto"` для рекомендации Discord |
| `shard_ids` | `"all"` | шарды, которые запускает этот процесс; требует явного `shard_count` |
| `before_identify` | `None` | `async def f(shard_id)`, которую дожидаются перед каждым identify, чтобы согласовать процессы |
| `session_file` | `None` | путь, по которому `close()` сохраняет сессии, а следующий `start()` их [возобновляет](../guide/sharding.md#resuming-after-a-restart) |
| `guild_ready_timeout` | 2.0 | через сколько секунд без нового сервера шард считается [готовым](../guide/cache.md#startup) |

## Команды и взаимодействия { #commands-and-interactions }

| Параметр | По умолчанию | Значение |
|---|---|---|
| `auto_sync` | `True` | [синхронизировать команды](../guide/registration.md) при запуске, если они отличаются от тех, что в Discord |
| `dev_guilds` | `()` | ID серверов, на которых команды регистрируются вместо глобальной регистрации |
| `contexts` | `None` | где можно использовать глобальные команды, если команда или роутер не указывают иное |
| `integration_types` | `None` | в каких установках есть глобальные команды, если команда или роутер не указывают иное |
| `translate` | `None` | `f(text, locale)`, возвращающая [перевод](../guide/registration.md#translations) или `None` |
| `auto_defer` | 2.5 | через сколько секунд ответ на неотвеченное взаимодействие [откладывается](../guide/responses.md#deferring); `None` отключает это |
| `allowed_mentions` | `AllowedMentions(parse=("users", "roles"))` | какие упоминания по умолчанию могут [пинговать](../guide/responses.md#allowed-mentions) |
| `owner_ids` | `None` | пользователи, которых пропускает [`is_owner`](../guide/checks.md#roles-and-owners); `None` читает владельца приложения или его команду |

## Кэш { #cache }

| Параметр | По умолчанию | Значение |
|---|---|---|
| `cache` | `True` | вести [кэш](../guide/cache.md); `False` ничего не хранит |
| `members` | `False` | хранить всех участников; нужен `Intents.GUILD_MEMBERS` |
| `chunk_members` | `True` | при `members=True` запрашивать участников, которых Discord не присылает при запуске |
| `presences` | `False` | хранить присутствие; нужен `Intents.GUILD_PRESENCES` |
| `max_messages` | 0 | сколько последних сообщений хранить |
| `max_dm_channels` | 0 | сколько последних личных каналов хранить |

## HTTP, голос и завершение работы { #http-voice-and-shutdown }

| Параметр | По умолчанию | Значение |
|---|---|---|
| `http_settings` | `HTTPSettings()` | [лимиты запросов, повторы, тайм-ауты и прокси](../guide/rest.md#rate-limits-retries-and-timeouts) для REST-запросов |
| `api_url` | `None` | базовый URL REST; иначе `BOSTIE_API_URL`, иначе URL Discord |
| `voice` | `voice.Defaults()` | [голосовые настройки](../voice/index.md#settings), с которых начинает каждое подключение |
| `shutdown_grace` | 0.0 | сколько секунд [`close()`](../guide/running.md#the-lifecycle) даёт выполняющимся обработчикам, прежде чем их отменить |

`run(token=None, loop_factory=None)` и `start(token=None)` принимают токен, по умолчанию — `BOSTIE_TOKEN`.
