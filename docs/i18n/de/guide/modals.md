---
source: 542ddb2b37fd206f8480755434912ca6bbefa224
description: "Öffne Discord-Modals mit Textfeldern, Auswahlmenüs, Datei-Uploads, Radio-Buttons und Checkboxen, lies die Werte aus und warte in einem Handler auf das Absenden."
---

# Modals

![Bostie füllt ein Formular aus und schickt es ab](../assets/mascot/bostie-modals.webp){ align=right width="180" loading=lazy }

Ein Modal ist ein Popup-Formular. Ein Befehl oder eine Komponente antwortet mit einem, und das Absenden durch den Benutzer kommt als neue Interaktion an, die eine Modal-Route verarbeitet.

```python
--8<-- "docs/snippets/modals/application_form.py"
```

`ui.modal(custom_id, title, *components)` nimmt einen Titel von 1 bis 45 Zeichen und eine bis fünf Komponenten: Felder mit `ui.label(...)` und Absätze mit `ui.text(...)`. `await ctx.modal(...)` öffnet es; es muss die erste Antwort der Interaktion sein, und danach kann nichts anderes mehr auf diese Interaktion antworten.

`@bot.on_modal(template)` leitet das Absenden weiter, so wie [`on_component`](components.md#routes) Klicks weiterleitet: dieselben Vorlagen mit den Platzhaltern `{name}` und `{name:int}`, dasselbe `route.id(...)`, dieselben Einstellungen `ephemeral`, `auto_defer` und `checks`.

## Felder { #fields }

`ui.label(label, component, description=None)` setzt ein Label von bis zu 45 Zeichen und eine Beschreibung von bis zu 100 über eine Eingabe:

| Eingabe | Builder | Auslesen mit |
|---|---|---|
| ein Textfeld | `ui.text_input(custom_id, style=TextInputStyle.SHORT)` | `ctx.text(custom_id)`, ein `str` |
| ein Auswahlmenü mit Optionen | `ui.string_select(custom_id, options)` | `ctx.values(custom_id)` |
| ein Auswahlmenü für Benutzer, Rollen, beides oder Kanäle | `ui.user_select`, `ui.role_select`, `ui.mentionable_select`, `ui.channel_select` | `ctx.users`, `ctx.members`, `ctx.roles`, `ctx.channels` oder `ctx.ids` |
| Datei-Uploads | `ui.file_upload(custom_id)` | `ctx.attachments(custom_id)`, die hochgeladenen `Attachment`-Objekte |
| eine Wahl aus 2 bis 10 | `ui.radio_group(custom_id, options)` | `ctx.choice(custom_id)`, der Wert oder `None` |
| mehrere Wahlen aus 1 bis 10 | `ui.checkbox_group(custom_id, options)` | `ctx.values(custom_id)` |
| ein Ja-oder-Nein-Kästchen | `ui.checkbox(custom_id, default=False)` | `ctx.checked(custom_id)`, ein `bool` |

Jede Eingabe außer der Checkbox nimmt `required`, standardmäßig `True`, und `min_values=0` erfordert `required=False`. `text_input` nimmt `style=TextInputStyle.PARAGRAPH` für mehrere Zeilen, `placeholder`, `value` zum Vorausfüllen, `min_length` (0 bis 4000) und `max_length` (1 bis 4000). `file_upload` nimmt `min_values` und `max_values`, bis zu 10 Dateien, und `file_types` wie `["image", ".pdf"]`. Optionen für Radio-Buttons und Checkboxen sind `ui.group_option(label, value, description=None, default=False)`; eine Checkbox-Gruppe ohne `max_values` lässt den Benutzer alle ankreuzen.

Die Builder geben `ui.ModalDict`, `ui.LabelDict`, `ui.TextInputDict`, `ui.FileUploadDict`, `ui.RadioGroupDict`, `ui.CheckboxGroupDict`, `ui.CheckboxDict` und `ui.GroupOptionDict` zurück. Die Lesemethoden an `ModalContext` nehmen die Custom-ID der Eingabe. Eine ID, die das Modal nicht hat, löst `KeyError` aus, und das Lesen einer Eingabe anderer Art, etwa `ctx.text` bei einer Checkbox, löst `TypeError` aus.

## Die Nachricht aktualisieren, die das Modal geöffnet hat { #updating-the-message-that-opened-the-modal }

Ein Modal, das aus einer Komponente geöffnet wurde, kennt die Nachricht, an der die Komponente hängt, und sein Absenden kann diese Nachricht bearbeiten:

```python
--8<-- "docs/snippets/modals/settings_modal.py"
```

`await ctx.update(...)` und `await ctx.defer_update()` funktionieren beim Absenden genauso wie bei einem Klick. Bei einem Modal, das aus einem Befehl geöffnet wurde, gibt es keine Nachricht zum Aktualisieren, und sie lösen `InteractionResponded` aus; antworte stattdessen mit `respond`. Ein unbeantwortetes Absenden wird nach `auto_defer` Sekunden aufgeschoben, mit `defer_update`, wenn es eine Nachricht gibt.

## Innerhalb eines Handlers auf das Absenden warten { #waiting-for-the-submit-inside-a-handler }

```python
--8<-- "docs/snippets/modals/inline_modal.py"
```

`await bot.wait_for_modal(*custom_ids, check=None)` gibt den `ModalContext` des nächsten Absendens zurück, dessen Custom-ID eine der `custom_ids` ist, oder irgendeines Modals, wenn keine angegeben sind, und dafür läuft keine Route. Setz etwas Eindeutiges in die Custom-ID, etwa die ID der Interaktion, damit zwei Benutzer, die den Befehl gleichzeitig ausführen, jeweils ihr eigenes Absenden bekommen. Ein Benutzer kann ein Modal schließen, ohne es abzusenden, und Discord teilt dem Bot nichts mit; begrenze das Warten also mit `asyncio.timeout()`.
