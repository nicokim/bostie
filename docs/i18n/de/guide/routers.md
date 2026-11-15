---
source: 54b3a6f7e7f026cf8621c75603332297ea4c5a00
---

# Router und Module

Ein `Router` enthält Befehle, Listener, Routen, Tasks, Checks, Hooks und Fehler-Hooks wie der Bot, ohne selbst einer zu sein. Router teilen einen Bot in Module auf, geben einer Gruppe von Befehlen gemeinsame Einstellungen und lassen sich austauschen, während der Bot läuft. Der Bot ist selbst ein Router, also funktioniert alles auf dieser Seite auch mit ihm.

```python
--8<-- "docs/snippets/routers/moderation_router.py"
```

Ein Router hat jeden Decorator des Bots: `command`, `group`, `user_command`, `message_command`, `entry_point`, `listen`, `listen_raw`, `on_component`, `on_modal`, `every`, `check`, `before_invoke`, `after_invoke` und `on_error`. `bot.include(router)` fügt das alles dem Bot hinzu, und ebenso alles, was später am Router registriert wird. Innerhalb seiner Handler ist `router.bot` der Bot, in den er eingebunden wurde; davor löst es `StateError` aus.

Router lassen sich verschachteln: `router.include(other)` funktioniert genauso, und wer den äußeren in den Bot einbindet, bringt beide mit.

## Standardwerte { #defaults }

`Router(...)` nimmt Standardwerte für die an ihm registrierten Befehle und Routen als Schlüsselwortargumente:

| Standardwert | Gilt für |
|---|---|
| `ephemeral` | Antworten seiner Befehle und Routen |
| `auto_defer` | seine Befehle und Routen |
| `default_permissions` | seine Befehle auf oberster Ebene |
| `nsfw` | seine Befehle auf oberster Ebene |
| `contexts`, `integration_types` | seine globalen Befehle |

Ein Befehl oder eine Route verwendet zuerst die eigene Einstellung, dann die Standardwerte seines Routers, dann die jedes Routers, der ihn einbindet, vom nächstliegenden nach außen, dann die des Bots. Im Beispiel ist `/ban` vor Mitgliedern ohne die Berechtigung „Mitglieder bannen“ verborgen, nur auf Servern verwendbar und wird ephemer beantwortet, sodass nur der Moderator, der ihn ausgeführt hat, den Button sieht und drücken kann.

Die Checks, Before-Hooks und After-Hooks eines Routers gelten für seine Handler; die der Router, die ihn einbinden, laufen ebenfalls, der äußerste zuerst. Fehler-Hooks werden vom nächstliegenden Router nach außen gesucht, wie [Checks, Hooks und Fehler](checks.md#error-hooks) erklärt.

## Module { #modules }

`bot.include("greetings")` importiert das Modul `greetings` und bindet den `Router` ein, den es `router` nennt. Ein Modul ohne einen solchen löst `TypeError` aus.

`bot.exclude("greetings")` entfernt alles, was der Router hinzugefügt hat, bricht seine Tasks ab und lässt Python das Modul und seine Untermodule vergessen, sodass ein erneutes Einbinden die Datei frisch importiert. `bot.reload("greetings")` erledigt beides in einem Aufruf: Schlägt der neue Import oder das Einbinden fehl, bleibt der alte Router eingebunden, und der Fehler wird ausgelöst. Neu laden ändert die Handler sofort; ruf danach `await bot.sync_commands()` auf, wenn sich Befehlsdefinitionen geändert haben, wie es das [modulare Beispiel](../examples.md#modular-bot) in seinem Befehl `/reload` tut.

`exclude` und `reload` nehmen auch ein `Router`-Objekt, für Router, die nicht der `router` eines Moduls sind.

## Konflikte { #conflicts }

Zwei Befehle mit demselben Namen, zwei Benutzer- oder Nachrichtenbefehle mit demselben Namen, zwei Routen mit derselben Vorlage oder eine Funktion, die zweimal als Task registriert wird, lösen beim Hinzufügen des zweiten eine Exception aus, auch wenn ein Router ihn mitbringt. Ein Router kann nur einmal eingebunden werden, und nie in sich selbst.

## Hineinschauen { #looking-inside }

`router.commands`, `router.context_menus`, `router.component_routes`, `router.modal_routes`, `router.tasks` und `router.listeners` enthalten, was registriert ist: Befehle nach Name und Servern, etwa `("rules", ())` für einen globalen, Benutzer- und Nachrichtenbefehle nach Typ, Name und Servern, Routen nach Vorlage, Tasks nach Funktion und Listener nach Event. `router.walk_commands()` liefert jeden Slash-Befehl und Unterbefehl, der einen Handler hat. `router.match_component(custom_id)` und `router.match_modal(custom_id)` geben die Route zurück, die eine Custom-ID erreicht, samt ihren Platzhalterwerten, oder `None`, und `router.find_error_handlers(error)` liefert die Fehler-Hooks, die eine Exception verarbeiten würden, den nächstliegenden zuerst.
