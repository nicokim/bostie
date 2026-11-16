---
source: db591175a7dec7d094226c92a4855de730089f1d
description: "Benutzer- und Nachrichtenbefehle im Kontextmenü und der Einstiegspunkt-Befehl, der eine Aktivität startet, mit typisierten Zielen und ihren Einstellungen."
---

# Benutzer-, Nachrichten- und Einstiegspunkt-Befehle

Neben Slash-Befehlen kennt Discord Befehle, die aus dem Kontextmenü eines Benutzers oder einer Nachricht ausgeführt werden, und den Einstiegspunkt-Befehl, der die Aktivität einer App öffnet.

## Benutzer- und Nachrichtenbefehle { #user-and-message-commands }

```python
--8<-- "docs/snippets/commands/context_menus.py"
```

`@bot.user_command(name)` und `@bot.message_command(name)` registrieren eine Funktion, die den Kontext und das Ziel entgegennimmt. Der Name ist das, was das Menü anzeigt: 1 bis 32 Zeichen, und anders als bei Slash-Befehlen darf er Großbuchstaben und Leerzeichen enthalten. Diese Befehle haben keine Beschreibung und keine Optionen.

Der Type Hint des Ziels legt fest, was der Handler bekommt:

| Befehl | Hint | Der Handler bekommt |
|---|---|---|
| Benutzer | `User` | den Benutzer |
| Benutzer | `ResolvedMember` | das Mitglied, mit seinem `user`; nur auf Servern |
| Benutzer | `User \| ResolvedMember` | auf einem Server das Mitglied, sonst den Benutzer |
| Nachricht | `Message` | eine Nachricht, die ein Benutzer oder Bot gesendet hat |
| Nachricht | `WebhookMessage` | eine Nachricht, die ein Webhook gesendet hat, Antworten auf Interaktionen eingeschlossen |
| Nachricht | `Message \| WebhookMessage` | beides |

Ein Ziel, das sich nicht in den angegebenen Typ auflösen lässt, löst `OptionConversionError` aus, das an die [Fehler-Hooks](checks.md#error-hooks) geht. Gib einem Nachrichtenziel den Hint `Message | WebhookMessage`, es sei denn, der Befehl ergibt nur für eine der beiden Arten Sinn.

Sie nehmen die Einstellungen eines Slash-Befehls außer `description`, `description_localizations` und den Optionseinstellungen: `name_localizations`, `ephemeral`, `auto_defer`, `default_permissions`, `nsfw`, `guilds`, `contexts`, `integration_types`, `checks` und `extra`. Discord erlaubt 15 Benutzer- und 15 Nachrichtenbefehle pro Geltungsbereich, und die Synchronisierung warnt, wenn es mehr sind.

## Der Einstiegspunkt-Befehl { #the-entry-point-command }

Eine App mit Aktivitäten hat genau einen Einstiegspunkt-Befehl, den der App Launcher anzeigt. `@bot.entry_point(name)` registriert ihn mit einer Funktion, die nur den Kontext entgegennimmt:

```python
--8<-- "docs/snippets/commands/entry_point.py"
```

Der Handler antwortet wie bei jedem Befehl: `ctx.launch_activity()` öffnet die Aktivität und gibt die ID ihrer Instanz zurück, und `ctx.respond(...)` antwortet stattdessen mit einer Nachricht. Discord lehnt den Befehl ab, wenn für die App im Developer Portal keine Aktivitäten aktiviert sind.

Der Einstiegspunkt ist immer global. Ist `dev_guilds` gesetzt, lässt die Synchronisierung ihn weg und warnt. Hat Discord bereits seinen Standard-Einstiegspunkt unter einem anderen Namen, behält die Synchronisierung die ID dieses Befehls, damit Discord ihn ersetzt, statt beide zu behalten. `launch_activity()` gibt es auch an Komponenten- und Modal-Kontexten, dort muss es aber die erste Antwort sein.

`entry_point` nimmt `description`, `name_localizations`, `description_localizations`, `ephemeral`, `auto_defer`, `default_permissions`, `nsfw`, `contexts`, `integration_types`, `checks` und `extra`. [Aktivitäten, Monetarisierung und das Social SDK](platform.md#activities) behandelt den Rest der Aktivitäten-Seite.
