---
source: cbf9d472ef0e0c0f610b3352754ad6482b93f093
---

# Auf Interaktionen antworten

Jeder Slash-Befehl, jeder Kontextmenü-Befehl, jeder Button, jedes Auswahlmenü und jedes abgesendete Modal ist eine Interaktion, und Discord wartet darauf, dass die App antwortet. Ein Befehls-Handler bekommt einen `Context`, ein Komponenten-Handler einen `ComponentContext` und ein Modal-Handler einen `ModalContext`; alle drei teilen sich die Antwortmethoden unten.

## Was ein Kontext weiß { #what-a-context-knows }

| Attribut | Wert |
|---|---|
| `ctx.user` | der `User`, der sie ausgelöst hat, auf einem Server oder in einer DM |
| `ctx.member` | sein `Member` auf einem Server, sonst `None` |
| `ctx.guild_id`, `ctx.channel_id` | wo es passiert ist; `guild_id` ist in DMs `None` |
| `ctx.channel` | der `PartialChannel`, den die Interaktion mitbringt |
| `ctx.locale` | die Sprache des Clients des Benutzers |
| `ctx.interaction` | das vollständige Interaktionsmodell, für alles andere, etwa `app_permissions` oder `guild_locale` |
| `ctx.bot` | der Bot |
| `ctx.voice` | [Sprachverbindungen](../voice/index.md) für diesen Server und Benutzer |
| `ctx.command_name` | bei `Context` der Befehl mit seinen Unterbefehlen, etwa `"roles color set"` |

## Antworten, bearbeiten, nachlegen { #answer-edit-follow-up }

```python
--8<-- "docs/snippets/responses/answer_flow.py"
```

`ctx.respond(...)` sendet die erste Antwort. Ein weiterer Aufruf sendet stattdessen eine Folgenachricht, sodass ein Handler es so oft aufrufen kann, wie er braucht. Es gibt die Nachricht zurück, die Discord erstellt hat, eine `WebhookMessage`.

- `ctx.edit(**parts)` bearbeitet die erste Antwort, `ctx.edit(message_id, **parts)` eine Folgenachricht.
- `ctx.followup(...)` sendet immer eine neue Nachricht, außer direkt nach `defer()`, wo es die aufgeschobene Antwort ausfüllt.
- `ctx.fetch()` und `ctx.fetch(message_id)` geben die Antwort oder eine Folgenachricht so zurück, wie Discord sie gerade hat.
- `ctx.delete()` und `ctx.delete(message_id)` löschen sie.

`respond` und `followup` nehmen `content` und die Teile, die unter [Nachrichteninhalt](messages.md) aufgeführt sind: `embeds`, `components`, `files`, `allowed_mentions`, `flags`, `tts`, `poll`, dazu `ephemeral`. Eine Bearbeitung nimmt dieselben Teile als Schlüsselwortargumente: Ein Teil, den du weglässt, bleibt, wie er ist, und `None` leert ihn.

## Nachrichten, die sich selbst löschen { #messages-that-delete-themselves }

```python
--8<-- "docs/snippets/responses/short_lived.py"
```

`delete_after=` an `respond`, `followup` und `bot.rest.messages.create` löscht die gesendete Nachricht nach so vielen Sekunden. Der Bot führt das Löschen im Hintergrund aus und bricht es ab, wenn er beendet wird; eine Nachricht, die erst nach dem Stopp des Bots fällig wäre, bleibt also bestehen. Die Nachrichten einer Interaktion werden über ihr Token gelöscht, was auch bei ephemeren funktioniert, aber 15 Minuten nach der Interaktion endet; ein später fälliges Löschen schlägt mit einer geloggten Warnung fehl. Eine Nachricht, die schon jemand gelöscht hat, wird stillschweigend übersprungen.

## Ephemere Antworten { #ephemeral-answers }

`ephemeral=True` an einem Aufruf macht diese Nachricht nur für den Benutzer sichtbar („Nur du kannst das sehen“). `ephemeral=True` an einem Befehl, einer Komponentenroute, einer Modal-Route oder einem Router macht das zum Standard für deren Antworten, und ein Aufruf kann trotzdem `ephemeral=False` übergeben. Eine bestehende Nachricht kann nicht zwischen ephemer und öffentlich wechseln: Eine Bearbeitung, die das verlangt, loggt einmalig eine Warnung und lässt die Nachricht, wie sie ist.

## Aufschieben { #deferring }

Eine erste Antwort muss Discord innerhalb von 3 Sekunden erreichen. `await ctx.defer()` zeigt an ihrer Stelle „denkt nach …“ an, und der Handler hat dann 15 Minuten Zeit für `respond`; `defer(ephemeral=True)` macht die kommende Antwort ephemer.

```python
--8<-- "docs/snippets/responses/deferring.py"
```

bostie schiebt auch von selbst auf: Eine Verwendung, die `auto_defer` Sekunden nach ihrem Eintreffen noch unbeantwortet ist, wird aufgeschoben, standardmäßig nach 2,5 Sekunden. Eine Komponente oder ein Modal, das aus einer Nachricht geöffnet wurde, wird mit `defer_update` aufgeschoben, was die Nachricht unverändert lässt, bis `update` sie bearbeitet. `auto_defer` wird am Bot, an einem Router, einem Befehl oder einer Route gesetzt, und `None` schaltet es dort ab. Der Timer läuft ab dem Moment, in dem das Gateway die Interaktion zugestellt hat; eine blockierte Event-Loop verzögert also den Handler, aber nicht die Frist. bostie loggt eine Warnung, wenn ein Handler mehr als eine Sekunde zu spät startet.

## Modals und Aktivitäten { #modals-and-activities }

`await ctx.modal(ui.modal(...))` antwortet mit einem Popup-Formular; siehe [Modals](modals.md). `await ctx.launch_activity()` öffnet die Aktivität der App und gibt die ID ihrer Instanz zurück. Beide müssen die erste Antwort sein, und nach einem Modal kann nichts mehr auf die Interaktion antworten: Das Absenden des Modals ist eine neue Interaktion.

## Die Regeln und die Fehler bei ihrer Verletzung { #the-rules-and-the-errors-for-breaking-them }

Jeder Aufruf prüft zuerst den Zustand der Interaktion:

| Aufruf | Vor jeder Antwort | Nach `defer()` | Nach `defer_update()` | Nach einer Antwort |
|---|---|---|---|---|
| `respond` | antwortet | füllt die Antwort aus | sendet eine Folgenachricht | sendet eine Folgenachricht |
| `defer` | schiebt auf | nichts | nichts | `InteractionResponded` |
| `edit`, `delete` | `InteractionNotAnswered` | bearbeitet, löscht die Antwort | bearbeitet, löscht die Nachricht | bearbeitet, löscht die Antwort |
| `followup` | `InteractionNotAnswered` | füllt die Antwort aus | sendet eine Folgenachricht | sendet eine Folgenachricht |
| `update` | bearbeitet die Nachricht | `InteractionResponded` | bearbeitet die Nachricht | `InteractionResponded` |
| `modal`, `launch_activity` | antwortet | `InteractionResponded` | `InteractionResponded` | `InteractionResponded` |

`update` und `defer_update` gibt es nur an Komponenten- und Modal-Kontexten. Jeder Aufruf nach einem Modal löst `InteractionResponded` aus.

Ein Interaktions-Token lebt 15 Minuten. Jeder Aufruf danach, oder nachdem Discord das Token für ungültig erklärt hat, löst `InteractionExpired` aus, ohne etwas zu senden.

Ein Handler, der zurückkehrt, ohne zu antworten, hinterlässt beim Benutzer „Die Anwendung hat nicht geantwortet“, und bostie loggt eine Warnung mit dem Namen des Handlers. Einer, der aufgeschoben und nie geantwortet hat, hinterlässt „denkt nach …“, bis das Token abläuft, ebenfalls mit einer Warnung. Wirft ein Handler eine Exception, bevor er geantwortet hat, antwortet bostie mit „Something went wrong.“, ephemer, sofern der Handler nicht aufgeschoben hatte, es sei denn, ein [Fehler-Hook](checks.md#error-hooks) antwortet.

Discord erlaubt 5 Folgenachrichten für eine Interaktion einer App, die nur für den Benutzer installiert ist, und bostie warnt bei der sechsten.

## Erlaubte Erwähnungen { #allowed-mentions }

Standardmäßig kann eine Antwort Benutzer und Rollen anpingen, aber nicht `@everyone` oder `@here`: `Bot(allowed_mentions=...)` ist `AllowedMentions(parse=("users", "roles"))`. Ein `allowed_mentions`, das mit einer Nachricht übergeben wird, ändert nur die Arten von Erwähnungen, die es setzt; `AllowedMentions(users=(123,))` beschränkt also die Pings von Benutzern und lässt die Einstellung für Rollen unberührt. `AllowedMentions(parse=())` schaltet jede Erwähnung stumm, und `replied_user=False` verhindert, dass eine Antwort den Autor der Nachricht anpingt, auf die sie antwortet.
