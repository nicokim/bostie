---
source: 606da1a817f7cceca11df3edf6dda3b5da72000b
---

# Модальные окна

![Бости заполняет форму и отправляет её](../assets/mascot/bostie-modals.webp){ align=right width="180" loading=lazy }

Модальное окно — это всплывающая форма. Команда или компонент отвечает им, а отправка формы пользователем приходит как новое взаимодействие, которое обрабатывает маршрут модального окна.

```python
--8<-- "docs/snippets/modals/application_form.py"
```

`ui.modal(custom_id, title, *components)` принимает заголовок от 1 до 45 символов и от одного до пяти компонентов: поля `ui.label(...)` и абзацы `ui.text(...)`. `await ctx.modal(...)` открывает его; это должен быть первый ответ на взаимодействие, и после него на это взаимодействие уже ничем не ответить.

`@bot.on_modal(template)` направляет отправку так же, как [`on_component`](components.md#routes) направляет нажатия: те же шаблоны с плейсхолдерами `{name}` и `{name:int}`, тот же `route.id(...)`, те же настройки `ephemeral`, `auto_defer` и `checks`.

## Поля { #fields }

`ui.label(label, component, description=None)` ставит над одним полем ввода подпись длиной до 45 символов и описание длиной до 100:

| Поле ввода | Конструктор | Чем читать |
|---|---|---|
| текстовое поле | `ui.text_input(custom_id, style=TextInputStyle.SHORT)` | `ctx.text(custom_id)`, `str` |
| меню выбора из вариантов | `ui.string_select(custom_id, options)` | `ctx.values(custom_id)` |
| меню выбора пользователей, ролей, тех и других или каналов | `ui.user_select`, `ui.role_select`, `ui.mentionable_select`, `ui.channel_select` | `ctx.users`, `ctx.members`, `ctx.roles`, `ctx.channels` или `ctx.ids` |
| загрузка файлов | `ui.file_upload(custom_id)` | `ctx.attachments(custom_id)`, загруженные объекты `Attachment` |
| один выбор из 2–10 | `ui.radio_group(custom_id, options)` | `ctx.choice(custom_id)`, значение или `None` |
| несколько выборов из 1–10 | `ui.checkbox_group(custom_id, options)` | `ctx.values(custom_id)` |
| один флажок «да или нет» | `ui.checkbox(custom_id, default=False)` | `ctx.checked(custom_id)`, `bool` |

Каждое поле ввода, кроме флажка, принимает `required`, по умолчанию `True`, а `min_values=0` требует `required=False`. `text_input` принимает `style=TextInputStyle.PARAGRAPH` для нескольких строк, `placeholder`, `value` для заранее заполненного значения, `min_length` (от 0 до 4000) и `max_length` (от 1 до 4000). `file_upload` принимает `min_values` и `max_values`, до 10 файлов, и `file_types`, например `["image", ".pdf"]`. Варианты радиокнопок и флажков — это `ui.group_option(label, value, description=None, default=False)`; группа флажков без `max_values` позволяет пользователю отметить все.

Конструкторы возвращают `ui.ModalDict`, `ui.LabelDict`, `ui.TextInputDict`, `ui.FileUploadDict`, `ui.RadioGroupDict`, `ui.CheckboxGroupDict`, `ui.CheckboxDict` и `ui.GroupOptionDict`. Методы чтения в `ModalContext` принимают custom id поля ввода. ID, которого нет в модальном окне, вызывает `KeyError`, а чтение поля другого вида, например `ctx.text` для флажка, вызывает `TypeError`.

## Изменение сообщения, которое открыло модальное окно { #updating-the-message-that-opened-the-modal }

Модальное окно, открытое из компонента, знает сообщение, на котором находится компонент, и его отправка может изменить это сообщение:

```python
--8<-- "docs/snippets/modals/settings_modal.py"
```

`await ctx.update(...)` и `await ctx.defer_update()` работают при отправке так же, как при нажатии. У модального окна, открытого из команды, нет сообщения, которое можно изменить, и они вызывают `InteractionResponded`; отвечайте вместо этого через `respond`. На отправку без ответа ответ откладывается через `auto_defer` секунд — через `defer_update`, если есть сообщение.

## Ожидание отправки внутри обработчика { #waiting-for-the-submit-inside-a-handler }

```python
--8<-- "docs/snippets/modals/inline_modal.py"
```

`await bot.wait_for_modal(*custom_ids, check=None)` возвращает `ModalContext` следующей отправки, чей custom id входит в `custom_ids`, или любого модального окна, если они не указаны, и никакой маршрут для неё не запускается. Добавьте в custom id что-нибудь уникальное, например ID взаимодействия, чтобы два пользователя, одновременно запустившие команду, получили каждый свою отправку. Пользователь может закрыть модальное окно, не отправив его, и Discord ничего не сообщит боту, поэтому ограничьте ожидание с помощью `asyncio.timeout()`.
