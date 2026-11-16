---
source: 33cc84c2aaa4440f3880a38f7ef33c9dff405dec
description: "Wie bostie Discords JSON in typisierte Modelle dekodiert: partielle Objekte, unbekannte Felder und Werte, rohe Payloads, Snowflake-Zeitstempel, Enums und Flags."
---

# Modelle und Enums

Alles, was bostie von Discord dekodiert, ist ein Modell: `Message`, `Guild`, `Member`, `Interaction` und rund zweihundert weitere, alle aus `bostie` importierbar. Modelle sind eingefrorene `msgspec`-Structs, die direkt aus den JSON-Bytes dekodiert werden.

```python
--8<-- "docs/snippets/models/inspect_message.py"
```

## Typen folgen Discords Dokumentation { #types-follow-discords-documentation }

Jedes Feld ist so typisiert, wie Discords Dokumentation es beschreibt:

- Ein Feld, das Discord immer sendet, hat keinen Standardwert und fehlt nie. Sagt die Dokumentation, dass es `null` sein kann, ist es `T | None`.
- Ein Feld, das die Dokumentation als optional kennzeichnet, hat einen Standardwert: `()` für eine Liste, eine leere Flag-Menge für Flags, `False` für einen Boolean, dessen Fehlen falsch bedeutet, und sonst `None`.
- IDs sind `int`, Zeitstempel sind zeitzonenbewusste `datetime`-Objekte in UTC, und Farben sind `int`.
- Wo Discord ein partielles Objekt sendet, hat das Feld ein partielles Modell: `PartialUser`, `PartialMember`, `PartialChannel`, `PartialMessage`, `PartialEmoji`, `PartialApplication`, `PartialIntegration`, `PartialEntitlement`, `UnavailableGuild`. Ein partielles Modell hat nur die Felder, die der partielle Payload hat; ein fehlendes Feld ist also ein Typfehler und kein `None` zur Laufzeit.

An drei Stellen, an denen ein Payload zwei Formen hat, gibt es zwei Klassen:

- Eine Nachricht, die ein Webhook gesendet hat, Antworten auf Interaktionen eingeschlossen, ist eine `WebhookMessage`, deren `author` ein `PartialUser` ist. Jede andere Nachricht ist eine `Message` mit einem vollständigen `User` als Autor. Felder, die beides akzeptieren, sind als `Message | WebhookMessage` typisiert.
- Ein Mitglied, das aus einer Option, einem Auswahlmenü oder einem Befehlsziel aufgelöst wird, ist ein `ResolvedMember`, das seinen `user` mitbringt und weder `deaf` noch `mute` hat. Ein `Member` aus dem Gateway oder aus REST hat beides.
- Ein Benutzer in `message.mentions` ist ein `MentionedUser`: ein `User` mit dem `member`, das Discord hinzufügt, wenn die Nachricht auf einem Server gesendet wurde.

Interaktionen sind je nach Typ `CommandInteraction`, `ComponentInteraction`, `ModalSubmitInteraction` oder `AutocompleteInteraction`, jeweils mit typisiertem `data`.

## Robustheit gegenüber Änderungen { #change-resilience }

Discord fügt ohne Ankündigung Felder, Enum-Werte und Komponententypen hinzu, und ein Bot darf dann nicht kaputtgehen:

- Felder, die bostie nicht kennt, werden beim Dekodieren ignoriert.
- Enums bilden einen unbekannten Wert auf ein stabiles Mitglied namens `UNKNOWN_<value>` ab, das gleich dem Wert ist: `MessageType(99).name == "UNKNOWN_99"`.
- Flags behalten Bits, die bostie nicht kennt, bei jedem Operator, `~` eingeschlossen.
- Ein unbekannter Komponententyp wird zu `UnknownComponent` dekodiert, das sein rohes JSON behält und unverändert zurückgesendet wird, wenn du es in `components` übergibst; ein unbekannter Interaktionstyp wird zu `UnknownInteraction` dekodiert, das `InteractionCreate`-Listener erreicht, aber keinen Befehl und keine Route.
- Eine verschachtelte Nachricht, etwa `message.referenced_message` oder die Nachricht einer Komponenten-Interaktion, wird erst dekodiert, wenn du die Eigenschaft liest; eine defekte schlägt also nur dort fehl, wo sie gelesen wird.

Ein Event-Payload, der überhaupt nicht zu seiner Klasse passt, etwa weil ein erforderliches Feld fehlt, wird einmal geloggt und nur an `RawEvent`-Listener zugestellt, wie [Events](events.md#raw-events-and-events-bostie-does-not-model) erklärt.

## Rohe Payloads { #raw-payloads }

Ein Modell, das aus einem vollständigen Payload dekodiert wurde, behält ihn: `model.raw_json` enthält die Bytes, und `model.raw` dekodiert sie in ein neues `dict`. Verschachtelte Modelle, etwa der Autor einer Nachricht, haben `raw_json = None`. Jedes Event hat ebenfalls `raw` und `raw_json`. Damit erreichst du jedes Feld, das Discord sendet, bevor bostie es modelliert.

Die `repr` eines Modells lässt `raw_json` und Geheimnisse weg: `token`, `access_token`, `refresh_token` und `id_token`.

## Modelle bauen und kopieren { #building-and-copying-models }

Modelle sind unveränderlich und werden mit Schlüsselwortargumenten gebaut. `msgspec.structs.replace(model, field=value)` gibt eine Kopie mit einigen geänderten Feldern zurück. Die Modelle, die du an Discord sendest, etwa `Embed`, `AllowedMentions`, `PollCreateRequest`, `MessageReference`, `ForumTag`, `RecurrenceRule` und `AutoModerationAction`, lassen die Felder weg, die du nicht gesetzt hast, sodass ein dekodiertes Modell so zurückgesendet wird, wie es kam.

## Helfer an Modellen { #helpers-on-models }

| Helfer | Wert |
|---|---|
| `user.mention`, `member.mention`, `role.mention`, `channel.mention` | der Erwähnungstext, etwa `<@123>` |
| `user.display_name`, `member.display_name` | der globale Name oder Benutzername; bei einem Mitglied zuerst der Nickname |
| `message.jump_url` | der Link zur Nachricht |
| `message.author_member` | der Autor als `ResolvedMember`, wenn die Nachricht das Mitglied mitbringt |
| `role.permissions`, `overwrite.allow`, `overwrite.deny` | `Permissions`-Flags |
| `member.permissions` | die Berechtigungen eines Mitglieds im Kanal der Interaktion, wenn Discord sie gesendet hat |
| `webhook.url` | die URL des Webhooks, wenn er ein Token hat |

Die [Anleitung zur Formatierung](formatting.md) behandelt Erwähnungen, Zeitstempel und CDN-URLs, die aus IDs gebaut werden.

## Snowflakes { #snowflakes }

Discord-IDs kodieren, wann das Objekt erstellt wurde. Jedes Modell mit einer Snowflake-ID hat `created_at`, diesen Zeitpunkt als `datetime` in UTC: `message.created_at`, `member.created_at` (der des Kontos), `emoji.created_at` (`None` bei einem Unicode-Emoji). `bostie.snowflake_time(id)` tut dasselbe für eine bloße ID, und `bostie.time_snowflake(when)` liefert die niedrigste ID, die zu einem Zeitpunkt erstellt wurde, oder mit `high=True` die höchste, zum Blättern nach Datum. Ein naives `datetime` wird als Ortszeit gelesen.

## Enums { #enums }

`bostie` exportiert Discords Enums und Flags: `ChannelType`, `MessageType`, `Permissions`, `Intents`, `MessageFlags`, `Locale`, `ButtonStyle` und die übrigen aus der [Enum-Referenz](../reference/api/enums.md). Flags kombinierst du mit `|` und prüfst sie mit `in`: `Permissions.SEND_MESSAGES in member_permissions`. `message.type.deletable` sagt, ob Discord eine Nachricht dieses Typs löschen lässt: Hinweise zu Gruppen-DMs und Anrufen, Änderungen von Kanalname und -symbol sowie die Startnachricht eines Threads lassen sich nicht löschen. `JSONErrorCode` benennt Discords Fehlercodes, so wie `HTTPException.code` sie meldet.
