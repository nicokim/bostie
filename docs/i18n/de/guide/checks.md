---
source: 029b6094446c707896abd078da818c4746bbebec
---

# Checks, Hooks und Fehler

![Bostie mit verschränkten Armen, wütend](../assets/mascot/bostie-angry.webp){ align=right width="180" loading=lazy }

Ein Check entscheidet, ob ein Handler laufen darf. Hooks führen Code vor und nach Handlern aus. Fehler-Hooks antworten, wenn etwas eine Exception wirft.

## Checks { #checks }

```python
--8<-- "docs/snippets/checks/command_checks.py"
```

Ein Check ist eine Funktion, die den Kontext entgegennimmt und einen `bool` zurückgibt oder per `await` liefert. `False` verweigert den Lauf mit `CheckFailure`, und ein Check kann auch selbst eine `CheckFailure` auslösen: Ihre Meldung ist das, was der Benutzer zu sehen bekommt. Checks gehören in die Einstellung `checks=[...]` eines Befehls, einer Gruppe, eines Unterbefehls, eines Benutzer- oder Nachrichtenbefehls, eines Einstiegspunkts oder einer Komponenten- oder Modal-Route, und `@router.check` registriert einen für jeden Handler eines Routers; der Bot ist ein Router.

Zuerst laufen die Checks des Bots, dann die jedes Routers von außen nach innen, dann die der Gruppen von oben nach unten, dann die des Befehls selbst. Die erste Ablehnung beendet den Lauf. Bei der Autovervollständigung laufen keine Checks.

`bostie.checks` enthält die gängigen:

| Check | Lehnt ab mit | Wann |
|---|---|---|
| `has_permissions(permissions)` | `MissingPermissions` | dem Benutzer fehlt im Kanal eine der `permissions`, oder er ist in einer DM |
| `bot_has_permissions(permissions)` | `BotMissingPermissions` | dem Bot fehlt im Kanal eine davon |
| `Cooldown(rate, seconds, per="user")` | `CooldownActive` | der Schlüssel hat ihn in den letzten `seconds` schon `rate`-mal verwendet |
| `MaxConcurrency(number, per="global")` | `MaxConcurrencyReached` | für den Schlüssel laufen noch `number` Ausführungen |
| `has_role(role)` | `MissingRole` | dem Benutzer fehlt die Rolle, angegeben per ID oder Name, oder er ist in einer DM |
| `has_any_role(*roles)` | `MissingRole` | der Benutzer hat keine der Rollen |
| `is_owner` | `NotOwner` | der Benutzer gehört nicht zu `bot.owner_ids` |

Ein Check-Objekt, das zusätzlich eine Methode `release(ctx)` hat, ein `checks.ReleasingCheck` wie `MaxConcurrency`, wird freigegeben, sobald der Handler endet, egal wie er endet.

`per` ist `"user"`, `"guild"` (in DMs der Benutzer), `"channel"`, `"global"` oder eine Funktion des Kontexts, die einen beliebigen hashbaren Schlüssel zurückgibt. Die Berechtigungs-Checks lesen die Berechtigungen, die Discord mit der Interaktion sendet, und brauchen daher weder Cache noch Anfrage. Ihre Fehler enthalten `missing`, die fehlenden `Permissions`, und `CooldownActive` enthält `retry_after` in Sekunden.

## Rollen und Eigentümer { #roles-and-owners }

```python
--8<-- "docs/snippets/checks/staff_checks.py"
```

`has_role` und `has_any_role` nehmen Rollen-IDs oder -Namen. Eine ID wird mit den Rollen abgeglichen, die Discord mit der Interaktion sendet, und braucht daher keinen Cache. Ein Name wird unter den Rollen des gecachten Servers gesucht; ein Bot ohne Cache oder eine [reine HTTP-App](http-interactions.md) gibt Rollen deshalb per ID an. `MissingRole` enthält die verlangten `roles`, und seine Meldung erwähnt eine per ID angegebene Rolle, die Discord als Namen der Rolle anzeigt.

`is_owner` ist ein Check, so wie er ist, ohne Klammern. `bot.owner_ids` enthält die Benutzer, die er durchlässt: die `owner_ids`, die `Bot(...)` übergeben wurden, sonst den Eigentümer der App, der beim Start des Bots aus der Anwendung gelesen wird. Bei einer App, die einem Team gehört, sind das der Eigentümer des Teams sowie die Admins und Entwickler, die die Einladung angenommen haben; Mitglieder mit reinem Lesezugriff bleiben außen vor.

## Was der Benutzer zu sehen bekommt { #what-the-user-is-told }

Wenn kein Fehler-Hook eine `CheckFailure` verarbeitet, antwortet bostie mit ihrer Meldung, ephemer: „You need the Manage Messages permission.“, „The bot needs the Manage Messages permission.“, „Try again“ mit einem relativen Zeitstempel wie „in 42 seconds“, „This is already running; try again once it is done.“, „You need the Moderator role.“, „Only the bot's owners can use this.“ oder bei einem einfachen `False` „You cannot use this.“.

## Hooks davor und danach { #hooks-before-and-after }

```python
--8<-- "docs/snippets/checks/error_hooks.py"
```

`@router.before_invoke` registriert ein `async def hook(ctx)`, das läuft, nachdem die Checks bestanden sind, und vor dem Handler; `@router.after_invoke` eines, das nach dem Handler läuft, auch wenn dieser eine Exception geworfen hat. Sie laufen für jeden Befehl, jede Komponentenroute und jede Modal-Route des Routers; ein Hook, der eine Komponente sehen kann, sollte also nicht von einem `Context` ausgehen. Die Before-Hooks des Bots laufen zuerst, seine After-Hooks zuletzt. Eine Exception aus einem After-Hook wird geloggt und ändert die Antwort nicht.

## Fehler-Hooks { #error-hooks }

Eine Exception aus einem Check, einem Konverter, einem Before-Hook oder dem Handler geht an einen Fehler-Hook. `@x.on_error` registriert `async def hook(ctx, error)` für jede `Exception`, und `@x.on_error(NotFound, ValueError)` für diese Typen und ihre Unterklassen. `x` ist ein Befehl, eine Gruppe, eine Komponenten- oder Modal-Route, ein Router oder der Bot, und jedes davon kann einen Hook pro Exception-Typ haben.

bostie sucht in dieser Reihenfolge nach einem Hook, und der erste, den es findet, verarbeitet den Fehler:

1. der Befehl, dann seine Gruppe und die Gruppe auf oberster Ebene;
2. der Router, zu dem der Befehl gehört, dann jeder Router, in den er eingebunden ist, bis hinauf zum Bot.

An einer Stelle gewinnt der Hook für die nächstliegende Klasse des Fehlers: Ein Hook für `NotFound` schlägt einen für `HTTPException`. Ein Hook, der denselben Fehler erneut auslöst, reicht ihn in dieser Reihenfolge an den nächsten Hook weiter, wie es `slow_down` oben tut. Löst ein Hook einen anderen Fehler aus, wird dieser geloggt.

Der Kontexttyp ist `ErrorContext`: ein `Context`, `ComponentContext`, `ModalContext` oder `AutocompleteContext`. Der Autovervollständigungs-Kontext kann nicht `respond`, prüfe also wie oben darauf.

Kehrt ein Hook zurück, ohne zu antworten, loggt bostie eine Warnung und antwortet mit „Something went wrong.“. Verarbeitet kein Hook den Fehler, beantwortet bostie eine `CheckFailure` mit ihrer Meldung und loggt alles andere samt Traceback, bevor es mit „Something went wrong.“ antwortet. Hatte der Handler bereits geantwortet, wird nichts geantwortet, und eine Autovervollständigung bekommt eine leere Vorschlagsliste.

## Fehler außerhalb von Handlern { #errors-outside-handlers }

Listener und Tasks haben keinen Benutzer, dem sie antworten könnten. Eine Exception in einem Listener wird als `ListenerError`-Event gesendet, eine in einem Task als `TaskError`; ohne Listener dafür loggt bostie sie. Siehe [Events](events.md#when-a-listener-raises) und [Hintergrund-Tasks](tasks.md).

## Exception-Typen { #exception-types }

Alles, was bostie zu Discord, zum Netzwerk oder zum Zustand des Bots auslöst, ist ein `BostieError`. Ein falsches Argument löst stattdessen `ValueError` oder `TypeError` aus. Manche Fehler sind zugleich eine Standard-Exception: `StateError` ist ein `RuntimeError`, `MissingIntent` ein `ValueError`, `StartupError` ein `OSError` und `CommandDefinitionError` ein `TypeError`. Die Fehler zu Befehlen und Interaktionen teilen sich die Basis `CommandError`, die Fehler zu Sprachverbindungen `VoiceError`.

| Exception | Ausgelöst, wenn |
|---|---|
| `HTTPException` | eine REST-Anfrage mit einem Fehlerstatus endete; `status`, `code` (ein `JSONErrorCode`), `message`, `errors` und der rohe `body` |
| `BadRequest`, `Forbidden`, `NotFound`, `ServerError` | 400, 403, 404 und 5xx |
| `RateLimited` | 429, oder bostie hat eine Anfrage zurückgehalten, die länger als `max_rate_limit_wait` warten müsste; `retry_after`, `is_global` |
| `InvalidToken` | Discord das Token abgelehnt hat, bei REST oder am Gateway |
| `TransportError` | eine Anfrage fehlschlug, bevor Discord geantwortet hat |
| `UnexpectedResponse` | Discord eine Anfrage angenommen hat, seine Antwort sich aber nicht dekodieren lässt; `raw` enthält sie |
| `RequestBudgetExhausted` | zu viele der letzten Anfragen ungültig waren und bostie eine abgelehnt hat, um die IP zu schützen |
| `GatewayClosed`, `PrivilegedIntentsRequired`, `ShardingRequired` | Discord das Gateway endgültig geschlossen hat; die letzten beiden für die Codes 4014 und 4011 |
| `ShardNotRunning`, `GatewayQueueFull` | ein Gateway-Befehl für einen Shard, den dieser Prozess nicht betreibt, oder mit zu vielen wartenden Befehlen |
| `StateError` | ein Aufruf, den der Zustand des Objekts nicht erlaubt, etwa `bot.rest` vor `start()` |
| `MissingIntent` | eine Funktion einen Intent braucht, den der Bot nicht angefordert hat |
| `MemberRequestRateLimited` | Discord eine Mitgliederanfrage abgelehnt hat; `retry_after` |
| `StartupError` | bostie eine benötigte Systemressource nicht einrichten konnte |
| `CommandDefinitionError` | ein Befehl, eine Gruppe oder eine Option gegen Discords Regeln verstößt; vom Decorator ausgelöst |
| `CommandSyncError` | Discord die Befehle bei einer Synchronisierung abgelehnt hat |
| `OptionConversionError` | eine Option oder ein Ziel sich nicht in seinen Hint umwandeln ließ |
| `InteractionResponded`, `InteractionNotAnswered`, `InteractionExpired` | ein Antwortaufruf, den der Zustand der Interaktion nicht erlaubt |
| `CheckFailure` und seine Unterklassen | ein Check einen Lauf verweigert hat |

Die Fehler zu Sprachverbindungen stehen auf der [Seite zu Sprachverbindungen](../voice/index.md#errors). Die [Fehler-Referenz](../reference/api/errors.md) listet jede Klasse mit ihren Attributen auf.
