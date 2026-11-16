---
source: 18ff3457c37f07dad7f0bcfc3aee4b5f5383fac0
description: "Rufe jeden Bot-Endpunkt der Discord-REST-API auf: Paginierung, Nachrichten aufräumen, Audit-Log, Fehler, Rate-Limits, Wiederholungen, Downloads, rohe Anfragen."
---

# REST-API

![Bostie springt nach einem Ball](../assets/mascot/bostie-jumping.webp){ align=right width="180" loading=lazy }

`bot.rest` hat eine Methode für jede Operation von Discords REST-API, die ein Bot aufrufen kann, gruppiert nach der Ressource, auf die sie wirkt: `bot.rest.messages.create`, `bot.rest.members.add_role`, `bot.rest.guilds.fetch`. Anfragen laufen über den Rust-HTTP-Client von bostie, der Discords Rate-Limits einhält.

```python
--8<-- "docs/snippets/rest/rest_calls.py"
```

## Konventionen { #conventions }

- Die IDs aus der URL kommen zuerst, rein positionell, in der Reihenfolge der URL: `bot.rest.members.add_role(guild_id, user_id, role_id)`.
- Alles andere ist ein Schlüsselwortargument, benannt wie in Discords Dokumentation und gegen ein `TypedDict` pro Operation geprüft; ein Tippfehler ist also ein Typfehler und zur Laufzeit ein `TypeError`.
- Operationen, die Discord im Audit-Log festhält, nehmen `reason=`, 1 bis 512 Zeichen, die den Moderatoren des Servers angezeigt werden.
- Ein Bild- oder Sound-Feld, etwa das `image` eines Emojis oder das `icon` eines Servers, nimmt ein `File`; bostie sendet es als die Data-URI, die Discord erwartet. Berechtigungsfelder nehmen `Permissions`.
- Ein Emoji, etwa das, das `reactions.create` hinzufügt, nimmt ein Unicode-Emoji, ein eigenes Emoji als `<:name:id>`, `<a:name:id>` oder `name:id`, oder ein `Emoji`- bzw. `PartialEmoji`-Modell.
- Methoden namens `fetch` holen ein Objekt, `list` iteriert über mehrere, `edit` ändert, `create` erstellt und `delete` löscht. Andere Verben behalten das Wort aus der Dokumentation, etwa `members.add_role` oder `guilds.begin_prune`.
- Methoden geben Modelle zurück, oder `None`, wenn Discord ohne Inhalt antwortet. Sie ändern `bot.cache` nicht, außer dass `channels.create_dm` den DM-Kanal behält, wenn `max_dm_channels` es erlaubt; die Gateway-Events, die auf eine Änderung folgen, aktualisieren den Cache.

## Seitenweises Abrufen { #paging }

`list`-Methoden von Endpunkten, die Discord seitenweise ausliefert, geben einen asynchronen Iterator zurück, der die nächste Seite holt, während du weiterläufst: `async for message in bot.rest.messages.list(channel_id, limit=500):`, wie `/today` oben. Ohne `limit` durchläuft er jedes Element. `before`, `after` und `around` beginnen woanders, höchstens eines davon; sie nehmen eine ID oder ein `datetime`, das bostie in die ID dieses Zeitpunkts umwandelt. Nachrichten kommen neueste zuerst, oder mit `after` älteste zuerst. Eine `list`-Methode eines Endpunkts, den Discord nicht seitenweise ausliefert, ist eine Coroutine, die ein Tupel zurückgibt. `audit_log.list` liefert ganze `AuditLogPage`-Objekte, mit den Benutzern, Webhooks und anderen Objekten, auf die die Einträge verweisen.

## Nachrichten aufräumen { #purging-messages }

`messages.purge(channel_id, limit=100, check=None)` durchläuft die letzten `limit` Nachrichten, neueste zuerst, und löscht die, für die `check` wahr zurückgibt, oder ohne `check` alle; `limit=None` durchläuft den ganzen Kanal, und `before` und `after` beginnen woanders, wie bei `list`. Nachrichten, die jünger als 14 Tage sind, gehen in Massenlöschungen von bis zu 100, ältere mit je einer Anfrage, da Discord nur junge Nachrichten in Massen löscht; ein Typ, den Discord nicht löschen lässt, etwa die Startnachricht eines Threads, wird übersprungen. `bulk=False` löscht eine nach der anderen, wie in einer DM, wo es keine Massenlöschung gibt. Es gibt die gelöschten Nachrichten zurück, und `reason` geht ins Audit-Log. Massenlöschungen und fremde Nachrichten brauchen die Berechtigung „Nachrichten verwalten“.

```python
--8<-- "docs/snippets/rest/purge.py"
```

## Das Audit-Log { #the-audit-log }

`audit_log.list(guild_id)` liefert `AuditLogPage`-Objekte, und das Gateway sendet jeden neuen Eintrag als `AuditLogEntryCreate`-Event. Die `changes` eines Eintrags enthalten ein `AuditLogChange` pro geändertem Feld, dessen `new_value` und `old_value` den Typ haben, den das geänderte Objekt diesem Feld gibt: `Permissions` für die `permissions` einer Rolle, ein `datetime` für das `communication_disabled_until` eines Mitglieds, ein Tupel aus `Overwrite` für die `permission_overwrites` eines Kanals. Discords Ausnahmen folgen seiner Dokumentation: `$add` und `$remove` bei den Rollen eines Mitglieds enthalten `AuditLogRole`-Objekte, der `avatar_hash` eines Webhooks und die `channel_id` einer Einladung haben den Typ von `avatar` bzw. einer ID, und eine Änderung einer Befehlsberechtigung ist nach der Rollen-, Benutzer- oder Kanal-ID geschlüsselt und enthält eine `CommandPermission`. Ein Schlüssel, den bostie nicht kennt, oder ein Wert in einer Form, die das Feld nicht annimmt, behält seinen JSON-Wert, und ein Wert, den Discord weglässt, ist `None`.

```python
--8<-- "docs/snippets/rest/audit_log.py"
```

## Fehler { #errors }

Eine Anfrage, die Discord mit einem Fehlerstatus beantwortet, löst eine `HTTPException` aus: `BadRequest` für 400, `Forbidden` für 403, `NotFound` für 404, `ServerError` für 5xx. Die Exception hat den `status`, Discords `code` als `JSONErrorCode`, seine `message`, die feldweisen `errors` und den rohen `body`; ihr Text listet jeden Feldfehler in einer eigenen Zeile. `UnexpectedResponse` bedeutet, dass Discord die Anfrage angenommen hat, seine Antwort sich aber nicht dekodieren ließ; ihre Meldung sagt, ob die Änderung wirksam wurde, und `error.raw` enthält die Antwort.

## Rate-Limits, Wiederholungen und Timeouts { #rate-limits-retries-and-timeouts }

Der Rust-Client verfolgt den Rate-Limit-Bucket jeder Route und Discords globales Limit und hält eine Anfrage zurück, bis in ihrem Bucket Platz ist. `Bot(http_settings=HTTPSettings(...))` ändert, wie:

| `HTTPSettings`-Feld | Standard | Bedeutung |
|---|---|---|
| `global_rate_limit` | 50 | Anfragen pro Sekunde über alle Routen; erhöhe den Wert, wenn Discord dem Bot ein höheres Limit gegeben hat |
| `max_rate_limit_wait` | 30.0 | die längste Wartezeit für ein Rate-Limit; eine Anfrage, die länger warten müsste, löst `RateLimited` aus, ohne gesendet zu werden |
| `retries` | 2 | wie oft eine fehlgeschlagene Anfrage wiederholt wird, mit exponentiellem Backoff, der höchstens `max_rate_limit_wait` wartet |
| `connect_timeout` | 10.0 | Sekunden zum Öffnen einer Verbindung |
| `read_timeout` | 60.0 | Sekunden, die auf eine Antwort gewartet wird |
| `rate_limiter` | `True` | `False` sendet sofort und wartet nur die zurückkommenden 429er ab, für den Einsatz hinter einem Rate-Limit-Proxy |
| `proxy` | `None` | eine HTTP- oder HTTPS-Proxy-URL für jede Anfrage, Audio aus `voice.Url` und CDN-Downloads eingeschlossen, Zugangsdaten eingeschlossen wie in `http://user:password@host:3128` |

Ein 429 wird nach der Verzögerung wiederholt, die Discord angibt, sofern sie innerhalb von `max_rate_limit_wait` liegt. Ein 500, 502, 503, 504 oder 524 oder eine abgebrochene Verbindung wird `retries`-mal wiederholt, bei jeder Methode außer `POST`, das bereits wirksam geworden sein könnte; eine Verbindung, die nie zustande kam, wird bei jeder Methode wiederholt.

Discord sperrt eine IP, die in 10 Minuten 10 000 ungültige Anfragen (401, 403, 429) sendet. bostie zählt sie und loggt bei 1000, 5000 und 9000 eine Warnung. Ab 9500 lehnt es jede Anfrage mit `RequestBudgetExhausted` ab, statt sie zu senden, bis die Zahl der letzten 11 Minuten unter 9000 fällt.

## Beliebige Endpunkte { #any-endpoint }

`bot.http.request(method, route, **params)` sendet eine beliebige Anfrage über denselben Client und gibt das dekodierte JSON zurück, oder `None` bei einer leeren Antwort. Die Route behält ihre Platzhalter, die Schlüsselwortargumente füllen: `await bot.http.request("GET", "/channels/{channel_id}/pins", channel_id=123)`. Es nimmt `json=`, `files=` für einen Multipart-Upload, `form=` für benannte Formularfelder, `urlencoded=` für einen form-urlencoded Body, `query=` und `reason=`, und löst dieselben Exceptions aus. `bot.http.send(...)` nimmt dieselben Argumente und gibt die rohen Bytes der Antwort zurück, für einen Endpunkt, der nicht mit JSON antwortet.

## Dateien herunterladen { #downloading-files }

Anhänge und Bilder liegen auf Discords CDN, nicht hinter der API; ein Download sendet also kein Token und verbraucht kein Rate-Limit. `bot.http` holt sie über denselben Rust-Client, mit dessen Timeouts, Wiederholungen und `proxy`:

| Methode | Bewirkt |
|---|---|
| `read(source)` | gibt die Bytes zurück |
| `save(source, path)` | schreibt sie in die Datei unter `path` |
| `to_file(source)` | gibt ein `File` zum erneuten Hochladen zurück; aus einem `Attachment` behält es Dateiname, Beschreibung, Titel, Spoiler und die Angaben einer Sprachnachricht, bei einer URL benennt es der letzte Teil ihres Pfads |

`source` ist ein `Attachment` oder eine URL, wie sie die Helfer in [`bostie.cdn`](formatting.md) bauen. Eine fehlende Datei löst `NotFound` aus.

```python
--8<-- "docs/snippets/rest/downloads.py"
```

## Ohne Bot { #without-a-bot }

Ein Skript oder Web-Backend, das nur REST braucht, verwendet einen `RESTClient`:

```python
--8<-- "examples/rest_script.py"
```

`async with RESTClient(token) as client:` öffnet den HTTP-Client; `client.rest` hat jede Gruppe von `bot.rest`, und `client.http` den rohen Client. Das Token ist standardmäßig `BOSTIE_TOKEN`, und der Client nimmt `api_url`, `http_settings` und `allowed_mentions` wie der Bot. Die [OAuth2-Anleitung](oauth2.md) behandelt `BearerClient`, das die API mit dem Token eines Benutzers aufruft, und die [Webhook-Anleitung](webhooks.md) die Clients, die gar kein Token brauchen.

## Die Gruppen { #the-groups }

| `bot.rest.` | Methoden |
|---|---|
| `application` | `edit`, `fetch`, `fetch_activity_instance`, `fetch_bot_application`, `list_role_connection_metadata`, `update_role_connection_metadata` |
| `audit_log` | `list` |
| `auto_moderation` | `create`, `delete`, `edit`, `fetch`, `list` |
| `bans` | `bulk_create`, `create`, `fetch`, `list`, `remove` |
| `channels` | `add_recipient`, `create`, `create_dm`, `create_group_dm`, `delete`, `delete_permission`, `edit`, `edit_permissions`, `edit_positions`, `fetch`, `fetch_member_permissions`, `fetch_role_permissions`, `follow`, `list`, `remove_recipient`, `set_voice_status`, `trigger_typing_indicator` |
| `commands` | `bulk_overwrite`, `bulk_overwrite_guild`, `create`, `create_guild`, `delete`, `delete_guild`, `edit`, `edit_guild`, `fetch`, `fetch_guild`, `fetch_permissions`, `list`, `list_guild`, `list_permissions` |
| `emojis` | `create`, `create_application`, `delete`, `delete_application`, `edit`, `edit_application`, `fetch`, `fetch_application`, `list`, `list_application` |
| `gateway` | `fetch`, `fetch_bot` |
| `guilds` | `begin_prune`, `edit`, `edit_incident_actions`, `edit_welcome_screen`, `edit_widget`, `fetch`, `fetch_preview`, `fetch_prune_count`, `fetch_vanity_url`, `fetch_welcome_screen`, `fetch_widget`, `fetch_widget_image`, `fetch_widget_settings`, `leave`, `list` |
| `identities` | `delete`, `fetch_profile`, `list`, `list_by_external_id`, `update_profile` |
| `integrations` | `delete`, `list` |
| `invites` | `add_target_user`, `bulk_add_target_users`, `bulk_delete_target_users`, `create`, `delete`, `fetch`, `fetch_target_users_job_status`, `list`, `list_guild`, `list_target_users`, `remove_target_user`, `update_target_users` |
| `lobbies` | `add_member`, `bulk_update_members`, `create`, `create_invite_for_user`, `delete`, `edit`, `fetch`, `remove_member`, `update_message_moderation_metadata` |
| `members` | `add`, `add_role`, `edit`, `edit_current`, `fetch`, `fetch_permissions`, `list`, `remove`, `remove_role`, `search` |
| `messages` | `bulk_delete`, `create`, `crosspost`, `delete`, `edit`, `fetch`, `list`, `list_pins`, `pin`, `purge`, `search`, `unpin` |
| `monetization` | `consume_entitlement`, `create_test_entitlement`, `delete_test_entitlement`, `fetch_entitlement`, `fetch_subscription`, `list_entitlements`, `list_skus`, `list_subscriptions` |
| `onboarding` | `edit`, `fetch` |
| `partner_sdk` | `create_provisional_token`, `exchange_child_token`, `exchange_external_token`, `unmerge_external_account`, `unmerge_provisional_account`, `update_dm_moderation_metadata` |
| `polls` | `end`, `list_answer_voters` |
| `reactions` | `create`, `delete`, `delete_all`, `delete_all_for_emoji`, `delete_own`, `list` |
| `roles` | `create`, `delete`, `edit`, `edit_positions`, `fetch`, `fetch_member_counts`, `fetch_permissions`, `list` |
| `scheduled_events` | `create`, `delete`, `edit`, `fetch`, `list`, `list_users` |
| `soundboard` | `create`, `delete`, `edit`, `fetch`, `list`, `list_default`, `send` |
| `stage_instances` | `create`, `delete`, `edit`, `fetch` |
| `stickers` | `create`, `delete`, `edit`, `fetch`, `fetch_guild`, `fetch_pack`, `list`, `list_packs` |
| `templates` | `create`, `delete`, `edit`, `fetch`, `list`, `sync` |
| `threads` | `add_member`, `fetch_member`, `join`, `leave`, `list_active`, `list_joined_private_archived`, `list_members`, `list_private_archived`, `list_public_archived`, `remove_member`, `start`, `start_from_message`, `start_in_forum` |
| `users` | `edit_current`, `fetch`, `fetch_current` |
| `voice` | `edit_current_state`, `edit_state`, `fetch_current_state`, `fetch_state`, `list_guild_regions`, `list_regions` |
| `webhooks` | `create`, `delete`, `delete_message`, `delete_with_token`, `edit`, `edit_message`, `edit_with_token`, `execute`, `execute_github`, `execute_slack`, `fetch`, `fetch_message`, `fetch_with_token`, `list`, `list_guild` |

Die Parameter und der Rückgabetyp jeder Methode stehen in der [Referenz der REST-API](../reference/api/rest.md). Ein paar Operationen bleiben absichtlich `bot.http.request` überlassen: Endpunkte, die Discord als veraltet markiert oder nie dokumentiert hat, und die Endpunkte für Interaktions-Callbacks und die ursprüngliche Antwort, die die [Interaktionskontexte](responses.md) abdecken.

Manche Gruppen bedienen Discord-Funktionen jenseits von Bots auf Servern: `monetization` und `application` für [Monetarisierung und Aktivitäten](platform.md), `lobbies`, `identities` und `partner_sdk` für das [Social SDK](platform.md#social-sdk) und `application.update_role_connection_metadata` für [verknüpfte Rollen](oauth2.md#linked-roles).
