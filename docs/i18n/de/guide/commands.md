---
source: 620651abbb35486bdf47aba0669128b8bb1d0ad6
description: "Slash-Befehle per Python-Type-Hints: Optionstypen, Auswahlmöglichkeiten, Wertebereiche, Autovervollständigung, Konverter, Unterbefehle und Discords Limits."
---

# Slash-Befehle

![Bostie schreibt einen Slash-Befehl in eine Sprechblase](../assets/mascot/bostie-commands.webp){ align=right width="180" loading=lazy }

Ein Slash-Befehl ist eine asynchrone Funktion mit dem Decorator `@bot.command`. Ihr erster Parameter nimmt den [`Context`](responses.md) entgegen; jeder Parameter danach ist eine Option des Befehls.

```python
--8<-- "docs/snippets/commands/option_types.py"
```

Der Befehl heißt `/report`. Sein Name ist der Name der Funktion, seine Beschreibung die erste Zeile des Docstrings; `@bot.command(name=..., description=...)` legt beide stattdessen fest, und ein Befehl ohne beides bekommt `…` als Beschreibung. `@bot.command` und `@bot.command(...)` funktionieren beide, am Bot wie an einem [Router](routers.md).

## Optionstypen { #option-types }

Der Type Hint eines Parameters bestimmt den Optionstyp, und der Handler bekommt den Wert bereits umgewandelt:

| Hint | Discord-Option | Der Handler bekommt |
|---|---|---|
| `str` | Zeichenkette | den Text |
| `int` | Ganzzahl | einen `int` |
| `float` | Zahl | einen `float`, auch wenn der Benutzer eine ganze Zahl eingegeben hat |
| `bool` | Boolescher Wert | einen `bool` |
| `User` | Benutzer | den `User` |
| `ResolvedMember` | Benutzer | das Mitglied, mit seinem `user`; nur auf Servern |
| `Role` | Rolle | die `Role` |
| `PartialChannel` | Kanal | den Kanal, wie die Interaktion ihn aufgelöst hat |
| `Attachment` | Anhang | das `Attachment` der hochgeladenen Datei |
| `User \| Role` oder `ResolvedMember \| Role` | erwähnbar | das, was der Benutzer ausgewählt hat |
| `Literal[...]` oder ein `Enum` | Zeichenkette, Ganzzahl oder Zahl mit Auswahlmöglichkeiten | das Literal oder das Enum-Mitglied |

Ein Hint, der nicht in der Tabelle steht, löst beim Ausführen des Decorators `CommandDefinitionError` aus. Für eine Kanaloption lautet der Hint `PartialChannel`, nicht `Channel`, und für ein Mitglied `ResolvedMember`, nicht `Member`: Eine Interaktion enthält diese partiellen Objekte, und die Fehlermeldung sagt das auch.

## Erforderlich und optional { #required-and-optional }

Eine Option ohne Standardwert ist erforderlich. Ein Standardwert oder `| None` im Hint macht sie optional; eine optionale Option, die der Benutzer weggelassen hat, bekommt ihren Standardwert oder `None`. Discord will die erforderlichen Optionen zuerst, und bostie sortiert sie beim Bauen des Befehls entsprechend, egal in welcher Reihenfolge die Parameter stehen.

## Beschreibungen { #descriptions }

Ein String in `Annotated` ist die Beschreibung der Option: `Annotated[str, "What happened"]`. Stattdessen kann auch der Docstring die Beschreibungen enthalten:

```python
--8<-- "docs/snippets/commands/docstring_options.py"
```

bostie liest einen Abschnitt `Args:` wie oben und einen mit Bindestrichen unterstrichenen Abschnitt `Parameters`; eine Beschreibung darf über mehrere Zeilen gehen. Ein String in `Annotated` hat Vorrang vor `Option(description=)`, und das wiederum vor dem Docstring. Eine Option, die nirgends beschrieben ist, bekommt `…`.

## Wertebereiche und Optionseinstellungen { #ranges-and-option-settings }

`Range[int, 1, 10]` ist eine `int`-Option von 1 bis 10, beide Grenzen eingeschlossen. `Range` nimmt auch `float`, und bei `str` gelten die Grenzen für die Länge: `Range[str, 1, 2000]`. `Option(...)` in `Annotated` legt alles Übrige fest und hat Vorrang vor dem, was der Hint vorgibt:

```python
--8<-- "docs/snippets/commands/option_settings.py"
```

| `Option`-Feld | Gilt für | Bedeutung |
|---|---|---|
| `name` | alle | der Name der Option bei Discord; der Parameter behält seinen Python-Namen |
| `description` | alle | die Beschreibung, wenn `Annotated` keinen String enthält |
| `name_localizations`, `description_localizations` | alle | Text pro Locale, etwa `{"fr": "salon"}` |
| `min`, `max` | Ganzzahl, Zahl | inklusive Grenzen, innerhalb von ±2⁵³ |
| `min_length`, `max_length` | Zeichenkette | Längengrenzen, 0 bis 6000 und 1 bis 6000 |
| `channel_types` | Kanal | die Kanaltypen, die der Benutzer auswählen darf |
| `file_types` | Anhang | bis zu 10 aus `image`, `video`, `audio` oder Dateiendungen wie `.pdf` |
| `choices` | Zeichenkette, Ganzzahl, Zahl | feste Auswahlmöglichkeiten; siehe unten |
| `autocomplete` | Zeichenkette, Ganzzahl, Zahl | eine Funktion, die Werte vorschlägt, während der Benutzer tippt |
| `extra` | alle | rohe Felder, die in das JSON der Option eingefügt werden, für ein Feld, das bostie noch nicht modelliert |

Ein Feld an einem Optionstyp, für den es nicht gilt, löst `CommandDefinitionError` aus.

## Auswahlmöglichkeiten { #choices }

Ein `Literal` oder ein `Enum` bietet eine feste Liste an. Eine `Literal`-Auswahlmöglichkeit ist nach ihrem Wert benannt; eine `Enum`-Auswahlmöglichkeit ist nach dem Mitglied benannt und sendet dessen Wert. `Option(choices=[...])` benennt sie um oder lokalisiert sie, solange es dieselben Werte in derselben Reihenfolge aufführt:

```python
--8<-- "docs/snippets/commands/option_choices.py"
```

Der Handler von `/order` bekommt für `drink` `"tea"`, `"coffee"` oder `"water"`, für `size` ein `Size`-Mitglied und für `milk` den Wert der ausgewählten `Choice`. Bei einem einfachen Hint `str`, `int` oder `float` legt `Option(choices=[...])` die Auswahlmöglichkeiten allein fest; jede ist eine `Choice(name, value)` oder ein bloßer Wert, der nach sich selbst benannt ist. Die Werte der Auswahlmöglichkeiten müssen den Typ der Option haben, und die Werte eines `Literal` oder `Enum` müssen alle `str`, alle `int` oder alle `float` sein. Eine Option hat höchstens 25 Auswahlmöglichkeiten, ein Name ist 1 bis 100 Zeichen lang und ein String-Wert höchstens 100.

## Autovervollständigung { #autocomplete }

`Option(autocomplete=function)` fragt `function` nach Vorschlägen, während der Benutzer tippt. Sie bekommt einen `AutocompleteContext` und den bisher eingegebenen Text und gibt Werte oder `Choice`-Objekte zurück:

```python
--8<-- "docs/snippets/commands/autocomplete_options.py"
```

`ctx.options` enthält die bisher ausgefüllten Optionen nach Optionsnamen, umgewandelt, wie der Handler sie bekäme, sofern die Umwandlung gelingt; die Option, die gerade getippt wird, bleibt Text. bostie sendet die ersten 25 Vorschläge, kürzt Namen über 100 Zeichen und verwirft Vorschläge mit leerem Namen, mit einem Wert des falschen Typs oder mit einem String-Wert über 100 Zeichen, jeweils mit einer Warnung. Eine Option kann nicht gleichzeitig `choices` und `autocomplete` haben. Wirft die Funktion eine Exception, geht der Fehler an die [Fehler-Hooks](checks.md#error-hooks), und Discord bekommt eine leere Liste.

## Konverter { #converters }

`Annotated[T, Convert(function)]` wandelt den Wert der Option in ein `T` um, bevor der Handler läuft. `function(ctx, value)` darf asynchron sein, und der Type Hint ihres Parameters `value` bestimmt den Discord-Optionstyp, `Range` und `Annotated`-Beschreibungen eingeschlossen:

```python
--8<-- "docs/snippets/commands/convert_options.py"
```

Der Konverter läuft nur für eine Option, die der Benutzer angegeben hat; eine weggelassene optionale Option bekommt ihren Standardwert unverändert. Eine Exception aus dem Konverter geht an die Fehler-Hooks, genau wie eine Exception aus dem Handler.

## Unterbefehle und Gruppen { #subcommands-and-groups }

`bot.group(name, description)` registriert einen Befehl, der aus Unterbefehlen besteht, und `group.group(...)` fügt darin eine Gruppe von Unterbefehlen hinzu. Gruppen lassen sich eine Ebene tief verschachteln, und eine Gruppe enthält höchstens 25 Einträge:

```python
--8<-- "docs/snippets/commands/command_groups.py"
```

Das definiert `/roles give` und `/roles color set`. `@group.command(name=...)` gibt einem Unterbefehl einen anderen Namen als seiner Funktion. `ctx.command_name` ist der vollständige Name, der ausgeführt wurde, etwa `"roles color set"`, und `command.qualified_name` ist dasselbe für das Befehlsobjekt.

Eine Gruppe auf oberster Ebene nimmt die Einstellungen eines Befehls außer `name`, `ephemeral` und `auto_defer`, und sie gelten für alle ihre Unterbefehle: `default_permissions`, `nsfw`, `guilds`, `contexts` und `integration_types` gibt es bei Discord nur für Befehle auf oberster Ebene. Eine verschachtelte Gruppe nimmt die beiden Lokalisierungen, `checks` und `extra`. Ein Unterbefehl nimmt `name`, `description`, die beiden Lokalisierungen, `ephemeral`, `auto_defer`, `checks` und `extra`. Die Checks einer Gruppe laufen vor denen ihrer Unterbefehle.

## Befehlseinstellungen { #command-settings }

```python
--8<-- "docs/snippets/commands/command_settings.py"
```

| Einstellung | Bedeutung |
|---|---|
| `name`, `description` | der Name und die Beschreibung statt derer der Funktion |
| `name_localizations`, `description_localizations` | Text pro Locale; eine Übersetzungsfunktion zeigt [Befehle registrieren](registration.md#translations) |
| `ephemeral` | Antworten sind nur für den Benutzer sichtbar, sofern ein Aufruf nichts anderes sagt |
| `auto_defer` | Sekunden, nach denen eine unbeantwortete Verwendung aufgeschoben wird; `None` schaltet das ab ([Auf Interaktionen antworten](responses.md#deferring)) |
| `default_permissions` | die Berechtigungen, die ein Mitglied braucht, um den Befehl zu sehen und zu verwenden; Server-Admins können ändern, wer ihn verwenden darf |
| `nsfw` | nur in altersbeschränkten Kanälen angezeigt |
| `guilds` | Server-IDs, auf denen der Befehl registriert wird, statt global |
| `contexts` | wo ein globaler Befehl verwendet werden kann: auf Servern, in den DMs des Bots, in privaten Kanälen |
| `integration_types` | welche Installationen den Befehl mitbringen: Guild Install, User Install |
| `checks` | Checks, die vor dem Handler laufen ([Checks](checks.md)) |
| `extra` | rohe Felder, die in das JSON des Befehls eingefügt werden |

`ephemeral`, `auto_defer`, `default_permissions`, `nsfw`, `contexts` und `integration_types` lassen sich auch für jeden Befehl eines [Routers](routers.md#defaults) festlegen, und `contexts`, `integration_types` und `auto_defer` für jeden Befehl des Bots.

## Limits { #limits }

Der Decorator prüft, was Discord ablehnen würde, und löst `CommandDefinitionError` aus, mit der Funktion und dem Parameter im Text:

- Namen sind 1 bis 32 Zeichen lang und bestehen aus Buchstaben, Ziffern, `-` und `_`, ohne Großbuchstaben. Namen von Benutzer- und Nachrichtenbefehlen dürfen auch Großbuchstaben und Leerzeichen enthalten.
- Beschreibungen sind 1 bis 100 Zeichen lang.
- Ein Befehl hat höchstens 25 Optionen und eine Option höchstens 25 Auswahlmöglichkeiten.
- `*args`, `**kwargs`, rein positionelle Optionen und Optionen ohne Type Hint werden abgelehnt.

Eine Warnung, kein Fehler, meldet, wenn Namen, Beschreibungen und Auswahlwerte eines Befehls zusammen mehr als die 8000 Zeichen ergeben, die Discord erlaubt. Die Synchronisierung warnt, wenn ein Geltungsbereich mehr als 100 Slash-Befehle, 15 Benutzerbefehle oder 15 Nachrichtenbefehle enthält.

## Weiter { #next }

- [Benutzer-, Nachrichten- und Einstiegspunkt-Befehle](context-menus.md)
- [Befehle bei Discord registrieren](registration.md), auf einem Server oder global, und sie übersetzen
- [Auf Interaktionen antworten](responses.md)
