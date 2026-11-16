---
source: d624adfef5ab8dd6ffcb780c8ed7ca6459e83b3f
description: "Кнопки, меню выбора и разметка Components V2 через bostie.ui, а также маршруты custom id к обработчикам, которые продолжают работать после перезапуска бота."
---

# Компоненты

![Бости нажимает большую кнопку](../assets/mascot/bostie-components.webp){ align=right width="180" loading=lazy }

Компоненты — это кнопки, меню выбора и блоки разметки сообщения. `bostie.ui` строит их как обычные словари в формате JSON Discord и при построении проверяет лимиты Discord. На их использование отвечает обработчик, зарегистрированный через `@bot.on_component`.

Компоненты строятся с помощью `bostie.ui`. Модели компонентов, например `bostie.Button` или `bostie.ActionRow`, — это то, что хранится в `components` декодированного сообщения; `components=` принимает и их, поэтому компоненты сообщения можно отправить обратно, изменив через `msgspec.structs.replace`, не собирая их заново, но они не проверяют лимиты Discord.

## Разметка { #layout }

```python
--8<-- "docs/snippets/components/layout.py"
```

Это Components V2: компоненты составляют всё сообщение, а вместо `content` используется `ui.text`, как объясняет раздел [«Содержимое сообщений»](messages.md#components-v2).

| Конструктор | Что показывает |
|---|---|
| `ui.text(markdown)` | текст с разметкой Markdown Discord |
| `ui.section(*texts, accessory=...)` | от одного до трёх текстов с кнопкой или миниатюрой рядом |
| `ui.thumbnail(url, description=None, spoiler=False)` | небольшое изображение, только как дополнение раздела |
| `ui.media_gallery(*items)` | от одного до десяти изображений или видео, каждое — `ui.gallery_item(url, description=None, spoiler=False)` |
| `ui.file(filename, spoiler=False)` | файл, загруженный вместе с сообщением под этим именем |
| `ui.separator(divider=True, spacing=1)` | отступ между компонентами, `spacing=2` — большой, с линией, если не указано `divider=False` |
| `ui.container(*components, accent_color=None, spoiler=False)` | рамка с цветным краем вокруг строк, текстов, разделов, галерей, файлов и разделителей |
| `ui.row(*components)` | до пяти кнопок или одно меню выбора |

URL может быть `attachment://name`, чтобы показать файл, загруженный вместе с сообщением. Каждый конструктор принимает `id=` для необязательного числового ID компонента, а `ui.raw(dict)` передаёт без изменений компонент, для которого в `ui` ещё нет конструктора. Слишком много элементов, слишком длинная подпись или меню выбора в строке с кнопками вызывают `ValueError` при построении. Каждый конструктор возвращает типизированный словарь, например `ui.ButtonDict`, `ui.RowDict`, `ui.TextDict`, `ui.SectionDict`, `ui.ThumbnailDict`, `ui.MediaGalleryDict`, `ui.GalleryItemDict`, `ui.FileDict`, `ui.SeparatorDict`, `ui.ContainerDict`, `ui.StringSelectDict`, `ui.SelectOptionDict`, `ui.EntitySelectDict` и `ui.SelectDefaultDict`, а `ui.raw` возвращает `ui.RawComponent`; аннотируйте ими свои вспомогательные функции, как это делает `ballot` ниже.

## Кнопки { #buttons }

- `ui.button(label, custom_id=..., style=ButtonStyle.SECONDARY)` при нажатии отправляет боту `custom_id`. `style` — это `PRIMARY`, `SECONDARY`, `SUCCESS` или `DANGER`.
- `ui.link_button(label, url)` открывает URL и никогда не доходит до бота.
- `ui.premium_button(sku_id)` предлагает купить SKU приложения и никогда не доходит до бота.

Кнопки принимают `emoji=`: эмодзи Unicode, собственный эмодзи в том виде, как его показывает Discord (`<:name:id>` или `<a:name:id>` для анимированного, как его делает `fmt.emoji`), или модель `Emoji` либо `PartialEmoji`. `PartialEmoji.parse(text)` читает такую строку. Кнопки также принимают `disabled=True`. Подпись — не длиннее 80 символов, custom id — от 1 до 100.

## Маршруты { #routes }

Маршрут — это обработчик для каждого custom id, подходящего под шаблон:

```python
--8<-- "docs/snippets/components/routes.py"
```

`"vote:{side:int}:{topic}"` совпадает с `vote:0:Lunch at noon`. `{name}` захватывает один или несколько символов без перевода строки, а `{name:int}` — целое число, включая отрицательные. Значения приходят в обработчик как именованные аргументы, преобразованные для `int`. Между двумя плейсхолдерами в шаблоне должен быть литеральный текст, а шаблон без плейсхолдеров совпадает со своим custom id в точности.

`vote.id(side=0, topic=topic)` строит custom id из самого маршрута, так что шаблон пишется один раз. Он вызывает `ValueError`, если ID получится длиннее 100 символов, разрешённых Discord, или не разберётся обратно в те же значения.

Состояние хранится в custom id, а не в объекте Python, поэтому кнопка, отправленная на прошлой неделе, работает и после перезапуска, пока её маршрут зарегистрирован. `@bot.on_component` принимает `ephemeral`, `auto_defer` и `checks`, как команда, а вызов маршрута, например `await vote(ctx, side=0, topic="x")`, вызывает обработчик.

Использование компонента, которому не подходит ни один маршрут, остаётся без ответа: Discord показывает «Ошибка взаимодействия», а bostie пишет предупреждение в лог, если только вы не слушаете `InteractionCreate`.

## Ответ на компонент { #answering-a-component }

У `ComponentContext` есть методы ответа контекста команды, а также:

- `await ctx.update(**parts)` изменяет сообщение, на котором находится компонент. Непереданные части остаются, а `None` очищает часть.
- `await ctx.defer_update()` подтверждает использование без видимого ответа; `update` может изменить сообщение в течение 15 минут.
- `await ctx.modal(ui.modal(...))` открывает [модальное окно](modals.md).

`respond` отправляет новое сообщение, как и для команды. На использование, оставшееся без ответа `auto_defer` секунд, ответ откладывается через `defer_update`.

## Меню выбора { #select-menus }

```python
--8<-- "docs/snippets/components/selects.py"
```

| Конструктор | Пользователь выбирает | Чем читать выбор |
|---|---|---|
| `ui.string_select(custom_id, options)` | варианты, построенные через `ui.select_option(label, value)` | `ctx.values`, значения |
| `ui.user_select(custom_id)` | пользователей | `ctx.users`, а на сервере и `ctx.members` |
| `ui.role_select(custom_id)` | роли | `ctx.roles` |
| `ui.mentionable_select(custom_id)` | пользователей и роли | `ctx.users`, `ctx.members`, `ctx.roles` |
| `ui.channel_select(custom_id, channel_types=...)` | каналы, только указанных типов | `ctx.channels` |

`ctx.ids` хранит выбранные ID любого меню выбора сущностей. Чтение не того вида, например `ctx.values` для меню выбора пользователей, вызывает `TypeError`.

Каждое меню выбора принимает `placeholder` (до 150 символов), `min_values` и `max_values` (по умолчанию 1, не больше 25) и `disabled`. В текстовом меню выбора от 1 до 25 вариантов; `select_option` принимает `description`, `emoji`, как у кнопок, и `default=True`, чтобы показать вариант выбранным. Меню выбора сущностей принимают `defaults` — ID, показанные выбранными, а меню выбора упоминаемых объектов принимает `default_users` и `default_roles`. `required` имеет значение только в [модальном окне](modals.md).

## Страницы { #pages }

`ui.pager(route, page, count)` возвращает строку из трёх кнопок для маршрута с плейсхолдером `{page:int}`: «назад», неактивную подпись `page / count` и «вперёд». Первая страница — 0, а дополнительные именованные аргументы заполняют остальные плейсхолдеры маршрута. Поскольку номер страницы хранится в custom id, листание переживает перезапуски. Его использует команда `/tour` из [примера с компонентами](../examples.md#components).

## Ожидание нажатия внутри обработчика { #waiting-for-a-click-inside-a-handler }

Команда может дождаться одного использования компонента без маршрута:

```python
--8<-- "docs/snippets/components/confirm.py"
```

`await bot.wait_for_component(*custom_ids, check=None)` возвращает `ComponentContext` следующего использования, чей custom id входит в `custom_ids` (или любого компонента, если они не указаны) и для которого `check` возвращает истину. Для использования, которое он вернул, никакой маршрут не запускается. Он ждёт бесконечно, поэтому ограничьте его с помощью `asyncio.timeout()`; исключение, выброшенное `check`, выбрасывается здесь. Он принимает `ephemeral` и `auto_defer` для возвращаемого контекста.
