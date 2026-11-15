---
source: f23a479950d38c899e79ae34c9fa1854c7b6fd34
---

# Cache und Berechtigungen

![Bostie lässt einen Knochen in eine Kiste fallen](../assets/mascot/bostie-cache.webp){ align=right width="180" loading=lazy }

`bot.cache` behält, was das Gateway dem Bot mitgeteilt hat, sodass Handler Dinge ohne Anfrage nachschlagen können. Er wird aus Events gefüllt, nie aus REST-Aufrufen, die du machst.

```python
--8<-- "docs/snippets/cache/cache_lookups.py"
```

## Was er behält { #what-it-keeps }

| Behalten | Standardmäßig | Option |
|---|---|---|
| Server, mit ihren Rollen, Emojis und Stickern | ja | |
| Kanäle und aktive Threads | ja | |
| Sprachzustände | ja | |
| geplante Events, Stage-Instanzen und Soundboard-Sounds | ja | |
| der Benutzer des Bots und sein eigenes Mitglied auf jedem Server | ja | |
| alle Mitglieder | nein | `members=True`, braucht `Intents.GUILD_MEMBERS` |
| Präsenzen von Mitgliedern, die nicht offline sind | nein | `presences=True`, braucht `Intents.GUILD_PRESENCES` |
| die neuesten Nachrichten | nein | `max_messages=N` |
| die neuesten DM-Kanäle | nein | `max_dm_channels=N` |

`Bot(cache=False)` behält nichts, und die übrigen Optionen lösen dann `ValueError` aus. Eine Option, deren Intent fehlt, löst beim Erstellen des `Bot` `MissingIntent` aus. Kanäle werden so gespeichert, wie Discord sie gesendet hat, außer den Feldern, die `CHANNEL_UPDATE` nicht aktuell hält (`last_message_id`, `message_count`, `member_count`, `last_pin_timestamp`); diese sind `None`.

Mit `members=True` sendet Discord beim Verbinden des Bots nur einen Teil der Mitglieder großer Server. bostie fragt dann den Rest an, pro Shard einen Server nach dem anderen, und ein Shard gilt erst als bereit, wenn seine Server vollständig sind. `chunk_members=False` überspringt das, und der Cache füllt sich, während Mitglieder beitreten, sich ändern oder sprechen. Anfragen, die ein Rate-Limit treffen, werden nach Discords Verzögerung wiederholt.

## Nachschlagen { #lookups }

| Methode | Gibt zurück |
|---|---|
| `guild(id)`, `guilds()` | einen `Guild` oder alle |
| `channel(id)`, `guild_channels(guild_id)` | einen `Channel`, oder die Kanäle eines Servers mit seinen aktiven Threads |
| `role(id)`, `guild_roles(guild_id)` | eine `Role`, oder die Rollen eines Servers |
| `voice_state(guild_id, user_id)`, `guild_voice_states(guild_id)` | wo Benutzer in Sprachkanälen sind; `None`, wenn der Benutzer in keinem Sprachkanal ist |
| `scheduled_event(id)`, `guild_scheduled_events(guild_id)` | ein `ScheduledEvent`, oder die eines Servers |
| `stage_instance(id)`, `guild_stage_instances(guild_id)` | eine laufende `StageInstance`, oder die eines Servers |
| `soundboard_sound(sound_id)`, `guild_soundboard_sounds(guild_id)` | einen `SoundboardSound`, oder die eines Servers |
| `me(guild_id)` | das eigene `Member` des Bots auf einem Server |
| `member(guild_id, user_id)`, `guild_members(guild_id)` | Mitglieder; nur das eigene des Bots, außer bei `members=True` |
| `presence(guild_id, user_id)`, `guild_presences(guild_id)` | Präsenzen, mit `presences=True`; `None`, wenn offline |
| `user(id)` | den Benutzer des Bots oder den Benutzer eines gecachten Mitglieds oder DM-Kanals |
| `dm_channel(user_id)` | den DM-Kanal des Bots mit einem Benutzer, falls behalten |
| `message(id)` | eine der neuesten `max_messages` Nachrichten |
| `permissions(channel_id, member=None)` | die Berechtigungen eines Mitglieds in einem Kanal, die des Bots, wenn `member` fehlt |
| `guild_permissions(guild_id, member=None)` | dasselbe, serverweit |

Für alles, was der Cache nicht enthält, geben Abfragen `None` oder eine leere Liste zurück. `bot.cache.clear()` leert ihn. Update- und Delete-Events enthalten das gecachte Objekt von vor der Änderung, wie [Events](events.md#old-values-and-the-cache) erklärt.

## Der Cache, sonst REST { #the-cache-else-rest }

```python
--8<-- "docs/snippets/cache/lookups_with_rest.py"
```

`bot.lookup` gibt ein Objekt aus dem Cache zurück, wenn er es enthält, und fragt sonst per REST danach: `user(user_id)`, `member(guild_id, user_id)`, `channel(channel_id)`, `guild(guild_id)`, `role(guild_id, role_id)` und `message(channel_id, message_id)`. Was REST zurückgibt, wird nicht gespeichert: Der Cache folgt weiterhin nur dem Gateway, sodass eine zweite Abfrage desselben fehlenden Objekts erneut anfragt. Die REST-Fehler werden durchgereicht, etwa `NotFound` für eine ID, die nicht existiert.

## Geplante Events, Stages und Sounds { #scheduled-events-stages-and-sounds }

```python
--8<-- "docs/snippets/cache/guild_events.py"
```

Sie kommen mit jedem Server an und folgen ihren Create-, Update- und Delete-Events, die für geplante Events `Intents.GUILD_SCHEDULED_EVENTS` und für Soundboard-Sounds `Intents.GUILD_EXPRESSIONS` brauchen; beide sind in `Intents.DEFAULT` enthalten. Die Antwort auf `bot.request_soundboard_sounds` ersetzt die Sounds eines Servers. Ihre Update-Events enthalten `old`, und `SoundboardSoundDelete` den entfernten `sound`.

## Start { #startup }

Ein Shard ist bereit, sobald jeder Server, den sein `READY` aufgeführt hat, als `GuildCreate` angekommen ist, oder sobald `guild_ready_timeout` Sekunden lang, standardmäßig 2, kein Server mehr angekommen ist. `Started` wartet auf jeden Shard. Ein `GuildCreate` mit `joined=True` ist ein Server, dem der Bot gerade hinzugefügt wurde; die anderen sind Server, die verfügbar werden.

`large_threshold`, 50 bis 250 und standardmäßig 50, ist die Mitgliederzahl, ab der Discord einen Server ohne seine Offline-Mitglieder sendet.

## Berechtigungen { #permissions }

`cache.permissions(channel_id, member)` wendet Discords Regeln an: Der Eigentümer des Servers und Administratoren haben alles; ansonsten zählen die Rolle `@everyone`, dann die Rollen des Mitglieds, dann die Überschreibungen des Kanals für `@everyone`, für die Rollen und für das Mitglied; ein Mitglied im Timeout behält nur „Kanal ansehen“ und „Nachrichtenverlauf anzeigen“; ohne „Kanal ansehen“ gibt es gar nichts; ohne „Nachrichten senden“ entfallen die Berechtigungen, die sie voraussetzen; in einem Sprachkanal ohne „Verbinden“ entfallen die Sprachberechtigungen. Ein Thread verwendet die Überschreibungen seines übergeordneten Kanals. Es gibt `None` zurück, wenn dem Cache der Kanal, der Server oder der übergeordnete Kanal eines Threads fehlt.

Dieselbe Berechnung gibt es auch ohne Cache: `bostie.permissions.compute_permissions(guild, member, channel=None, *, parent=None)`, wobei `parent` der übergeordnete Kanal eines Threads ist. Dieses Modul enthält außerdem `Permissions`-Mengen aus Discords Dokumentation: `ALL`, `NONE`, `TEXT`, `VOICE` und `STAGE` (die Berechtigungen, die für die jeweilige Kanalart gelten), `ELEVATED` (die, die Zwei-Faktor-Authentifizierung auf dem Konto des Moderators erfordern, wenn der Server sie verlangt) und `TIMED_OUT` (die, die ein Mitglied im Timeout behält).

Innerhalb einer Interaktion brauchst du den Cache nicht: `ctx.interaction.app_permissions` enthält die Berechtigungen des Bots im Kanal, und `ctx.member.permissions` die des Benutzers.

## Rollenhierarchie { #role-hierarchy }

Ein Mitglied kann nur Mitglieder kicken, bannen, in den Timeout schicken oder bearbeiten, deren höchste Rolle niedriger ist als die eigene, und Discord zeigt den Namen eines Mitglieds in der Farbe seiner höchsten farbigen Rolle. `bostie.permissions` hat dafür drei Helfer, die den `Guild` und die Mitglieder aus dem Cache, einer Interaktion oder REST nehmen:

| Helfer | Gibt zurück |
|---|---|
| `top_role(guild, member)` | die höchste `Role` des Mitglieds, oder `None`, wenn es nur `@everyone` hat |
| `display_colors(guild, member)` | die `RoleColors` seiner höchsten Rolle mit Farbe, oder `None` |
| `can_act_on(guild, actor, target)` | ob `actor` über `target` steht, einem Mitglied oder einer Rolle: Der Eigentümer steht über allen, niemand steht über dem Eigentümer, und ansonsten wird die höchste Rolle des Akteurs nach Position mit der höchsten Rolle des Ziels verglichen, oder mit der Rolle selbst, so wie Discord es bei der Rollenverwaltung tut |

Rollen auf derselben Position werden nach ID geordnet, die ältere zuerst, so wie Discord sie sortiert.

```python
--8<-- "docs/snippets/cache/role_hierarchy.py"
```
