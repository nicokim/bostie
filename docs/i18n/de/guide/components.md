---
source: d0cf09522c13a747c853db29f2b054fe520abbea
---

# Komponenten

![Bostie drückt einen großen Button](../assets/mascot/bostie-components.webp){ align=right width="180" loading=lazy }

Komponenten sind die Buttons, Auswahlmenüs und Layout-Blöcke einer Nachricht. `bostie.ui` baut sie als einfache Dictionaries in Discords JSON-Form und prüft dabei Discords Limits. Ein Handler, der mit `@bot.on_component` registriert ist, beantwortet ihre Verwendung.

Mit `bostie.ui` baust du Komponenten. Die Komponentenmodelle wie `bostie.Button` oder `bostie.ActionRow` sind das, was die `components` einer dekodierten Nachricht enthalten; `components=` nimmt auch sie, sodass sich die Komponenten einer Nachricht zurücksenden lassen, mit `msgspec.structs.replace` bearbeitet, ohne sie neu zu bauen. Sie prüfen aber nicht Discords Limits.

## Layout { #layout }

```python
--8<-- "docs/snippets/components/layout.py"
```

Das sind Components V2: Die Komponenten bilden die gesamte Nachricht, mit `ui.text` anstelle von `content`, wie [Nachrichteninhalt](messages.md#components-v2) erklärt.

| Builder | Was er anzeigt |
|---|---|
| `ui.text(markdown)` | Text, mit Discords Markdown |
| `ui.section(*texts, accessory=...)` | ein bis drei Texte mit einem Button oder Vorschaubild daneben |
| `ui.thumbnail(url, description=None, spoiler=False)` | ein kleines Bild, nur als Zubehör einer Section |
| `ui.media_gallery(*items)` | ein bis zehn Bilder oder Videos, jeweils ein `ui.gallery_item(url, description=None, spoiler=False)` |
| `ui.file(filename, spoiler=False)` | eine Datei, die mit der Nachricht unter diesem Namen hochgeladen wurde |
| `ui.separator(divider=True, spacing=1)` | Abstand zwischen Komponenten, `spacing=2` für einen großen, mit einer Linie, außer bei `divider=False` |
| `ui.container(*components, accent_color=None, spoiler=False)` | ein Kasten um Zeilen, Texte, Sections, Galerien, Dateien und Trenner, mit farbigem Rand |
| `ui.row(*components)` | bis zu fünf Buttons oder ein Auswahlmenü |

Eine URL kann `attachment://name` sein, um eine mit der Nachricht hochgeladene Datei anzuzeigen. Jeder Builder nimmt `id=` für die optionale numerische Komponenten-ID, und `ui.raw(dict)` reicht eine Komponente, für die `ui` noch keinen Builder hat, unverändert durch. Zu viele Elemente, ein zu langes Label oder ein Auswahlmenü in einer Zeile mit Buttons lösen beim Bauen `ValueError` aus. Jeder Builder gibt ein typisiertes Dictionary zurück, etwa `ui.ButtonDict`, `ui.RowDict`, `ui.TextDict`, `ui.SectionDict`, `ui.ThumbnailDict`, `ui.MediaGalleryDict`, `ui.GalleryItemDict`, `ui.FileDict`, `ui.SeparatorDict`, `ui.ContainerDict`, `ui.StringSelectDict`, `ui.SelectOptionDict`, `ui.EntitySelectDict` und `ui.SelectDefaultDict`, und `ui.raw` gibt ein `ui.RawComponent` zurück; annotiere deine eigenen Helfer damit, wie es `ballot` unten tut.

## Buttons { #buttons }

- `ui.button(label, custom_id=..., style=ButtonStyle.SECONDARY)` sendet beim Klick `custom_id` an den Bot. `style` ist `PRIMARY`, `SECONDARY`, `SUCCESS` oder `DANGER`.
- `ui.link_button(label, url)` öffnet eine URL und erreicht den Bot nie.
- `ui.premium_button(sku_id)` bietet eine SKU der App zum Kauf an und erreicht den Bot nie.

Buttons nehmen `emoji=`: ein Unicode-Emoji, ein eigenes Emoji so geschrieben, wie Discord es anzeigt (`<:name:id>`, oder `<a:name:id>`, wenn es animiert ist, so wie `fmt.emoji` es erzeugt), oder ein `Emoji`- bzw. `PartialEmoji`-Modell. `PartialEmoji.parse(text)` liest einen solchen String. Buttons nehmen außerdem `disabled=True`. Ein Label ist höchstens 80 Zeichen lang und eine Custom-ID 1 bis 100.

## Routen { #routes }

Eine Route ist ein Handler für jede Custom-ID, die zu einer Vorlage passt:

```python
--8<-- "docs/snippets/components/routes.py"
```

`"vote:{side:int}:{topic}"` passt auf `vote:0:Lunch at noon`. `{name}` erfasst ein oder mehrere Zeichen ohne Zeilenumbruch, `{name:int}` eine Ganzzahl, negative eingeschlossen. Die Werte erreichen den Handler als Schlüsselwortargumente, bei `int` umgewandelt. Vorlagen brauchen festen Text zwischen zwei Platzhaltern, und eine Vorlage ohne Platzhalter passt genau auf ihre Custom-ID.

`vote.id(side=0, topic=topic)` baut die Custom-ID aus der Route selbst, sodass die Vorlage nur einmal geschrieben wird. Es löst `ValueError` aus, wenn die ID länger als Discords 100 Zeichen würde oder sich nicht wieder in dieselben Werte zerlegen ließe.

Der Zustand steckt in der Custom-ID, nicht in einem Python-Objekt. Ein Button, der letzte Woche gesendet wurde, funktioniert also auch nach einem Neustart, solange seine Route registriert ist. `@bot.on_component` nimmt `ephemeral`, `auto_defer` und `checks` wie ein Befehl, und ein Aufruf der Route, etwa `await vote(ctx, side=0, topic="x")`, ruft den Handler auf.

Eine Komponentenverwendung, auf die keine Route passt, bekommt keine Antwort: Discord zeigt „Diese Interaktion ist fehlgeschlagen“, und bostie loggt eine Warnung, es sei denn, du hörst auf `InteractionCreate`.

## Auf eine Komponente antworten { #answering-a-component }

Ein `ComponentContext` hat die Antwortmethoden des Kontexts eines Befehls, dazu:

- `await ctx.update(**parts)` bearbeitet die Nachricht, an der die Komponente hängt. Weggelassene Teile bleiben, und `None` leert einen.
- `await ctx.defer_update()` bestätigt die Verwendung ohne sichtbare Antwort; `update` kann die Nachricht innerhalb von 15 Minuten bearbeiten.
- `await ctx.modal(ui.modal(...))` öffnet ein [Modal](modals.md).

`respond` sendet wie bei einem Befehl eine neue Nachricht. Eine Verwendung, die `auto_defer` Sekunden lang unbeantwortet bleibt, wird mit `defer_update` aufgeschoben.

## Auswahlmenüs { #select-menus }

```python
--8<-- "docs/snippets/components/selects.py"
```

| Builder | Der Benutzer wählt | Die Auswahl liest du mit |
|---|---|---|
| `ui.string_select(custom_id, options)` | Optionen, gebaut mit `ui.select_option(label, value)` | `ctx.values`, die Werte |
| `ui.user_select(custom_id)` | Benutzer | `ctx.users`, und auf einem Server `ctx.members` |
| `ui.role_select(custom_id)` | Rollen | `ctx.roles` |
| `ui.mentionable_select(custom_id)` | Benutzer und Rollen | `ctx.users`, `ctx.members`, `ctx.roles` |
| `ui.channel_select(custom_id, channel_types=...)` | Kanäle, nur der angegebenen Typen | `ctx.channels` |

`ctx.ids` enthält die ausgewählten IDs jedes Entitäts-Auswahlmenüs. Liest du die falsche Art, etwa `ctx.values` bei einem Benutzer-Auswahlmenü, wird `TypeError` ausgelöst.

Jedes Auswahlmenü nimmt `placeholder` (bis zu 150 Zeichen), `min_values` und `max_values` (standardmäßig 1, höchstens 25) und `disabled`. Ein String-Auswahlmenü hat 1 bis 25 Optionen; `select_option` nimmt `description`, `emoji` wie bei Buttons und `default=True`, um die Option als ausgewählt anzuzeigen. Entitäts-Auswahlmenüs nehmen `defaults`, die IDs, die als ausgewählt angezeigt werden, und das Auswahlmenü für Erwähnbares nimmt `default_users` und `default_roles`. `required` spielt nur in einem [Modal](modals.md) eine Rolle.

## Seiten { #pages }

`ui.pager(route, page, count)` gibt für eine Route mit dem Platzhalter `{page:int}` eine Zeile aus drei Buttons zurück: zurück, ein deaktiviertes Label `page / count` und weiter. Die erste Seite ist 0, und zusätzliche Schlüsselwortargumente füllen die übrigen Platzhalter der Route. Da die Seitenzahl in der Custom-ID steht, übersteht das Blättern Neustarts. Der Befehl `/tour` aus dem [Komponenten-Beispiel](../examples.md#components) verwendet es.

## Innerhalb eines Handlers auf einen Klick warten { #waiting-for-a-click-inside-a-handler }

Ein Befehl kann ohne Route auf eine Verwendung einer Komponente warten:

```python
--8<-- "docs/snippets/components/confirm.py"
```

`await bot.wait_for_component(*custom_ids, check=None)` gibt den `ComponentContext` der nächsten Verwendung zurück, deren Custom-ID eine der `custom_ids` ist, oder irgendeiner Komponente, wenn keine angegeben sind, und für die `check` wahr zurückgibt. Für eine Verwendung, die es zurückgibt, läuft keine Route. Es wartet unbegrenzt, also begrenze es mit `asyncio.timeout()`; eine Exception, die `check` auslöst, wird hier ausgelöst. Es nimmt `ephemeral` und `auto_defer` für den Kontext, den es zurückgibt.
