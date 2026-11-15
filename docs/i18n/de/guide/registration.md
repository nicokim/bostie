---
source: 9aa2f3cd3cfe588188668066e047191f9b207226
---

# Befehle registrieren

Discord zeigt einen Befehl erst an, wenn die App ihn registriert hat. bostie erledigt das für dich: Beim Start des Bots vergleicht es die Befehle, die du definiert hast, mit denen, die Discord kennt, und sendet sie nur, wenn sich etwas unterscheidet.

## Die Synchronisierung beim Start { #the-sync-at-startup }

`bot.start()` und `bot.run()` synchronisieren, bevor sich das Gateway verbindet. Für jeden Geltungsbereich, global oder ein einzelner Server, holt bostie die registrierten Befehle samt Lokalisierungen, vergleicht sie Feld für Feld und ersetzt die Befehle des Geltungsbereichs mit einer einzigen Anfrage, wenn sie sich unterscheiden. Das Log meldet `commands up to date (global)` oder `commands synced (global): added [...], removed [...], changed [...]`.

Ein Befehl, den Discord kennt, den du aber nicht mehr definierst, wird entfernt, es sei denn, sein Typ ist einer, den du nie definierst: Ein Bot ohne `@bot.entry_point` behält den Einstiegspunkt-Befehl, den Discord für seine Aktivität angelegt hat.

`Bot(auto_sync=False)` überspringt die Synchronisierung. `await bot.sync_commands()` ersetzt sofort die Befehle jedes Geltungsbereichs, ob geändert oder nicht, und gibt pro Geltungsbereich ein `SyncResult` zurück, mit den Namen, die `added`, `removed` und `changed` wurden, und den `ids`, die Discord den Slash-Befehlen gegeben hat.

## Geltungsbereiche { #scopes }

Ein Befehl ohne `guilds` ist global: Er funktioniert auf jedem Server, der die App installiert hat, und, soweit seine `contexts` es erlauben, in DMs. `guilds=[...]` registriert ihn nur auf diesen Servern.

Solange du an einem Bot arbeitest, registriere alles auf einem Testserver:

```python
--8<-- "docs/snippets/commands/command_sync.py"
```

`Bot(dev_guilds=[...])` registriert jeden Befehl, der keine eigenen `guilds` hat, auf diesen Servern statt global. Der Einstiegspunkt-Befehl ist immer global, deshalb lässt die Synchronisierung ihn weg und warnt.

### Ein Name in mehreren Geltungsbereichen { #one-name-in-several-scopes }

```python
--8<-- "docs/snippets/commands/guild_variants.py"
```

Befehle dürfen sich einen Namen teilen, solange kein Server zwei davon hat: ein globales `/rules` und ein `/rules` für einen Server, oder ein `/rules` pro Server. Jeder wird in seinem eigenen Geltungsbereich registriert, und eine Interaktion führt den Befehl aus, der auf ihrem Server registriert ist, sonst den globalen. Auf diesem Server listet Discord sowohl den globalen als auch den Server-Befehl. Mit `dev_guilds` tritt der eigene Befehl eines Servers dort an die Stelle des globalen. Zwei gleichnamige Befehle, deren Server sich überschneiden, oder zwei globale lösen beim Registrieren des zweiten `CommandDefinitionError` aus. Für Benutzer- und Nachrichtenbefehle gelten dieselben Regeln.

Die Synchronisierung berührt nur Geltungsbereiche, die Befehle haben. Wenn du `dev_guilds` entfernst, bleiben die Befehle auf dem Testserver neben den neuen globalen bestehen; `await bot.rest.commands.bulk_overwrite_guild(bot.application_id, guild_id, [])` räumt sie weg.

## Wo Befehle verwendet werden können { #where-commands-can-be-used }

`contexts` und `integration_types` gelten für globale Befehle. Du setzt sie an einem Befehl, einem Router oder am Bot:

- `contexts`: `InteractionContextType.GUILD`, `BOT_DM` (die DMs des Bots) und `PRIVATE_CHANNEL` (andere DMs und Gruppen-DMs, für eine App mit User Install).
- `integration_types`: `ApplicationIntegrationType.GUILD_INSTALL` und `USER_INSTALL`.

`Bot(contexts=[...], integration_types=[...])` ist der Standard für jeden globalen Befehl, der keine eigenen Werte setzt. Ist nirgends etwas gesetzt, sendet bostie keines der beiden Felder, und Discord wendet seine Standardwerte an; für `integration_types` sind das die Installationskontexte, die für die App eingestellt sind. Eine Einstellung zu entfernen, die du gesetzt hattest, zählt als Änderung, sodass die nächste Synchronisierung dem Befehl wieder Discords Standardwerte gibt.

## Befehlserwähnungen { #command-mentions }

`await bot.command_mention("roles color set")` gibt `</roles color set:ID>` zurück, was Discord als anklickbaren Befehl anzeigt. Es nutzt die IDs der letzten Synchronisierung oder fragt Discord einmal pro Geltungsbereich. Mit `guild_id=` hat ein auf diesem Server registrierter Befehl Vorrang vor einem gleichnamigen globalen. Ein unbekannter Name löst `LookupError` aus, und ein Aufruf vor dem Start des Bots löst `StateError` aus. `fmt.command(name, command_id)` baut denselben Text aus einer ID, die du schon hast.

## Übersetzungen { #translations }

`name_localizations` und `description_localizations` an einem Befehl, einer Gruppe, einem Unterbefehl, einer `Option` oder einer `Choice` übersetzen jeweils einen Text. Eine Übersetzungsfunktion übersetzt sie alle:

```python
--8<-- "docs/snippets/commands/command_translations.py"
```

`Bot(translate=function)` ruft während der Synchronisierung `function(text, locale)` für jeden Namen und jede Beschreibung jedes Befehls, jeder Gruppe, jedes Unterbefehls und jeder Option sowie für jeden Namen einer Auswahlmöglichkeit auf, einmal pro `Locale`. Gibt sie `None` oder den unveränderten Text zurück, kommt für diese Locale nichts hinzu, und eine Lokalisierung, die du selbst geschrieben hast, hat Vorrang vor der Funktion. Der Handler sieht die Sprache des Benutzers in `ctx.locale` und die des Servers in `ctx.interaction.guild_locale`.

## Wenn Discord die Befehle ablehnt { #when-discord-refuses-the-commands }

Eine Synchronisierung, die Discord mit 400 ablehnt, löst `CommandSyncError` aus. Seine Meldung enthält eine Zeile pro Problem, mit dem Befehl, dem Pfad der Option und Discords Meldung, und `error.problems` enthält sie als `SyncProblem`-Objekte. Die meisten Probleme fallen schon früher auf: Die Decorators prüfen Namen, Beschreibungen und Limits, sobald sie ausgeführt werden.
