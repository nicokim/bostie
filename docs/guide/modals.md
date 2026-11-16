---
description: "Open Discord modal forms with text inputs, selects, file uploads, radio groups and checkboxes, read the values and wait for a submit in a handler."
---

# Modals

![Bostie filling in a form and sending it](../assets/mascot/bostie-modals.webp){ align=right width="180" loading=lazy }

A modal is a popup form. A command or a component answers with one, and the user's submit arrives as a new interaction that a modal route handles.

```python
--8<-- "docs/snippets/modals/application_form.py"
```

`ui.modal(custom_id, title, *components)` takes a title of 1 to 45 characters and one to five components: `ui.label(...)` fields and `ui.text(...)` paragraphs. `await ctx.modal(...)` opens it; it has to be the interaction's first answer, and nothing else can answer that interaction afterwards.

`@bot.on_modal(template)` routes the submit like [`on_component`](components.md#routes) routes clicks: same templates with `{name}` and `{name:int}` placeholders, same `route.id(...)`, same `ephemeral`, `auto_defer` and `checks` settings.

## Fields

`ui.label(label, component, description=None)` puts a label of up to 45 characters and a description of up to 100 above one input:

| Input | Builder | Read it with |
|---|---|---|
| a text box | `ui.text_input(custom_id, style=TextInputStyle.SHORT)` | `ctx.text(custom_id)`, a `str` |
| a select of options | `ui.string_select(custom_id, options)` | `ctx.values(custom_id)` |
| a select of users, roles, both, or channels | `ui.user_select`, `ui.role_select`, `ui.mentionable_select`, `ui.channel_select` | `ctx.users`, `ctx.members`, `ctx.roles`, `ctx.channels`, or `ctx.ids` |
| file uploads | `ui.file_upload(custom_id)` | `ctx.attachments(custom_id)`, the uploaded `Attachment` objects |
| one choice of 2 to 10 | `ui.radio_group(custom_id, options)` | `ctx.choice(custom_id)`, the value or `None` |
| several choices of 1 to 10 | `ui.checkbox_group(custom_id, options)` | `ctx.values(custom_id)` |
| one yes or no box | `ui.checkbox(custom_id, default=False)` | `ctx.checked(custom_id)`, a `bool` |

Every input but the checkbox takes `required`, `True` by default, and `min_values=0` needs `required=False`. `text_input` takes `style=TextInputStyle.PARAGRAPH` for several lines, `placeholder`, `value` to fill it in beforehand, `min_length` (0 to 4000) and `max_length` (1 to 4000). `file_upload` takes `min_values` and `max_values`, up to 10 files, and `file_types` such as `["image", ".pdf"]`. Radio and checkbox options are `ui.group_option(label, value, description=None, default=False)`; a checkbox group without `max_values` lets the user tick them all.

The builders return `ui.ModalDict`, `ui.LabelDict`, `ui.TextInputDict`, `ui.FileUploadDict`, `ui.RadioGroupDict`, `ui.CheckboxGroupDict`, `ui.CheckboxDict` and `ui.GroupOptionDict`. The readers on `ModalContext` take the custom id of the input. An id the modal does not have raises `KeyError`, and reading an input of another kind, such as `ctx.text` on a checkbox, raises `TypeError`.

## Updating the message that opened the modal

A modal opened from a component knows the message the component is on, and its submit can edit that message:

```python
--8<-- "docs/snippets/modals/settings_modal.py"
```

`await ctx.update(...)` and `await ctx.defer_update()` work on the submit as on a click. On a modal opened from a command there is no message to update, and they raise `InteractionResponded`; answer with `respond` instead. An unanswered submit is deferred after `auto_defer` seconds, with `defer_update` when there is a message.

## Waiting for the submit inside a handler

```python
--8<-- "docs/snippets/modals/inline_modal.py"
```

`await bot.wait_for_modal(*custom_ids, check=None)` returns the `ModalContext` of the next submit whose custom id is one of `custom_ids`, or of any modal when none are given, and no route runs for it. Put something unique in the custom id, such as the interaction id, so two users running the command at once each get their own submit. A user can close a modal without submitting it, and Discord tells the bot nothing, so bound the wait with `asyncio.timeout()`.
