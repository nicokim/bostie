---
source: 9f6f43faa726c1342aeebb13122e1615b76fb827
---

# Nachrichteninhalt

Eine Nachricht besteht aus denselben Teilen, egal wohin du sie sendest: `ctx.respond`, `ctx.followup`, `ctx.edit` und `ctx.update`, `bot.rest.messages.create` und `edit` sowie das `execute` eines Webhooks. Jeder Teil ist ein Schlüsselwortargument, und ein Teil, den du weglässt, wird nicht gesendet.

| Teil | Typ | Hinweise |
|---|---|---|
| `content` | `str` | das erste Positionsargument von `respond`, `followup`, `messages.create` und `execute` |
| `embeds` | `Embed`-Objekte | bis zu 10 Rich Embeds |
| `components` | `ui`-Builder | Buttons, Auswahlmenüs und die Layout-Komponenten; siehe [Komponenten](components.md) |
| `files` | `File`-Objekte | Uploads; siehe unten |
| `allowed_mentions` | `AllowedMentions` | welche Erwähnungen anpingen dürfen; siehe [Erlaubte Erwähnungen](responses.md#allowed-mentions) |
| `flags` | `MessageFlags` | etwa `SUPPRESS_EMBEDS` und `SUPPRESS_NOTIFICATIONS` |
| `tts` | `bool` | den Inhalt jedem vorlesen, der den Kanal geöffnet hat |
| `poll` | `PollCreateRequest` | eine Umfrage; siehe unten |
| `ephemeral` | `bool` | nur bei Antworten auf Interaktionen |

`bot.rest.messages.create` nimmt außerdem `reply_to`, `message_reference`, `sticker_ids`, `nonce`, `enforce_nonce` und `shared_client_theme`, sowie `delete_after`, wie es auch [Antworten auf Interaktionen](responses.md#messages-that-delete-themselves) tun.

## Embeds und Dateien { #embeds-and-files }

```python
--8<-- "docs/snippets/responses/rich_messages.py"
```

`Embed` und seine Teile (`EmbedField`, `EmbedFooter`, `EmbedAuthor`, `EmbedMedia`) sind eingefrorene Modelle mit Schlüsselwortargumenten; ein einfaches `dict` in Discords JSON-Form funktioniert ebenfalls. `color` nimmt einen `int` wie `0x5865F2`, und die Helfer in [`bostie.color`](formatting.md#colors) bauen einen.

`File(name, data)` lädt `data` unter `name` hoch. `description` ist der Alternativtext, `spoiler=True` macht die Datei unscharf, und `title` legt den Titel fest, den Discord anzeigt. Ein Embed oder eine Komponente verweist auf einen Upload als `attachment://name`, wie oben das Diagramm. bostie prüft keine Größen: Discords Upload-Limit hängt von den Boosts des Servers und vom Nitro des Benutzers ab.

## Umfragen { #polls }

`PollCreateRequest` nimmt eine `question` und `answers`, beide `PollMedia` mit `text` und bei Antworten einem optionalen `emoji`. `duration` ist in Stunden angegeben, 24, wenn weggelassen, und `allow_multiselect=True` lässt einen Benutzer mehrere Antworten wählen. `bot.rest.polls.end(channel_id, message_id)` beendet eine Umfrage vorzeitig, `bot.rest.polls.list_answer_voters(...)` listet, wer eine Antwort gewählt hat, und die [Events](events.md) `PollVoteAdd` und `PollVoteRemove` melden Stimmen, sobald sie abgegeben werden.

## Bearbeiten und Anhänge { #editing-and-attachments }

Eine Bearbeitung nimmt die Teile als Schlüsselwortargumente, dazu `attachments`. Ein Teil, den du weglässt, bleibt, und `None` leert ihn: `ctx.edit(embeds=None)` entfernt die Embeds und behält den Inhalt. Übergibt eine Bearbeitung `files` oder `attachments`, behält die Nachricht nur die Anhänge, die in `attachments` aufgeführt sind, als `Attachment`-Objekte oder IDs, plus die neuen Dateien.

## Components V2 { #components-v2 }

Eine Nachricht, deren `components` eine Layout-Komponente enthalten (`ui.text`, `ui.section`, `ui.container`, `ui.media_gallery`, `ui.file`, `ui.separator` oder `ui.thumbnail`), ist eine Components-V2-Nachricht: bostie setzt das Flag `IS_COMPONENTS_V2`, und die Komponenten sind die gesamte Nachricht. Eine solche Nachricht kann weder `content` noch `embeds`, Sticker oder eine Umfrage haben, und bostie löst `ValueError` aus, wenn du sie übergibst; schreib den Text stattdessen in `ui.text`. Buttons und Auswahlmenüs allein in `ui.row` funktionieren auch in klassischen Nachrichten mit Inhalt.

## Antworten { #replies }

```python
--8<-- "docs/snippets/responses/replies.py"
```

`reply_to` nimmt die ID einer Nachricht im selben Kanal. Um eine Nachricht weiterzuleiten, übergib stattdessen `message_reference=MessageReference(type=MessageReferenceType.FORWARD, message_id=..., channel_id=...)`; beides lässt sich nicht kombinieren. Um `message.content` auf Servern zu lesen, brauchst du den privilegierten Message Content Intent, wie die [Gateway-Anleitung](gateway.md#intents) erklärt.

## Tipp-Anzeige { #typing-indicator }

```python
--8<-- "docs/snippets/responses/typing_indicator.py"
```

`async with ctx.typing():` zeigt im Kanal der Interaktion an, dass der Bot schreibt, solange der Block läuft. bostie sendet die Anzeige, wenn der Block beginnt, und danach alle 9 Sekunden erneut, da Discord sie 10 Sekunden lang anzeigt, und hört auf, wenn der Block endet, auch durch eine Exception. `await ctx.typing()` zeigt sie einmal an. Außerhalb einer Interaktion tut `bot.rest.channels.trigger_typing_indicator(channel_id)` dasselbe, mit `await` oder in `async with`. Eine Nachricht, die der Bot im Kanal sendet, beendet die Anzeige sofort.

## Sprachnachrichten { #voice-messages }

```python
--8<-- "docs/snippets/responses/voice_memo.py"
```

`await voice.message(audio)` liest einen Dateipfad oder Bytes, misst die Dauer, berechnet die Wellenform, die Discord zeichnet, und gibt ein sendefertiges `File` zurück. Die Dateiendung des Namens bestimmt das Format; Discords Clients nehmen 48 kHz Mono Ogg Opus auf. Eine Sprachnachricht besteht nur aus dieser einen Datei und sonst nichts: kein Inhalt, keine Embeds, Komponenten, Umfrage oder Sticker. bostie setzt das Flag `IS_VOICE_MESSAGE` und löst `ValueError` aus, wenn noch etwas anderes in der Nachricht steht. Ein `File`, das du selbst baust, wird zur Sprachnachricht, wenn es `duration_secs` und `waveform` hat, die 8-Bit-Samples, die Discord zeichnet.
