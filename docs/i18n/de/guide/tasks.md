---
source: b58df394d71cd32e4c45cccfdd0da40ddcb957c3
description: "Führe Hintergrund-Tasks alle paar Sekunden oder zu festen Uhrzeiten mit @bot.every aus und starte, stoppe, starte sie neu oder plane sie zur Laufzeit um."
---

# Hintergrund-Tasks

![Bostie schläft, während ein Wecker klingelt](../assets/mascot/bostie-sleeping.webp){ align=right width="180" loading=lazy }

`@bot.every` führt eine asynchrone Funktion ohne Argumente nach Zeitplan aus, solange der Bot bereit ist.

```python
--8<-- "docs/snippets/tasks/scheduled.py"
```

`every(seconds=300)` führt die Funktion aus, sobald der Bot bereit ist, und danach alle 300 Sekunden. Der Zeitplan driftet nicht: Jeder Lauf beginnt beim nächsten Vielfachen von `seconds` ab dem ersten, und ein Lauf, der länger als `seconds` dauert, überspringt die Takte, die er überlappt hat.

`every(time=...)` führt sie jeden Tag zu einer Uhrzeit aus, oder zu jeder von mehreren. Ein `datetime.time` ohne `tzinfo` gilt in UTC. Mit einem festen Offset, wie oben, bleibt es bei diesem Offset; mit einer `zoneinfo.ZoneInfo` folgt es den Sommerzeitwechseln der Zone. Übergib `seconds` oder `time`, nicht beides.

## Einen Task steuern { #controlling-a-task }

```python
--8<-- "docs/snippets/tasks/task_control.py"
```

`@bot.every` gibt einen `Task` zurück, und ein `await` auf `check_feeds()` führt die Funktion weiterhin einmal aus. Seine Methoden ändern den Zeitplan, während der Bot läuft:

| `Task` | Bewirkt |
|---|---|
| `stop()` | bricht den Task ab, während eines Laufs oder zwischen Läufen, und verhindert, dass er mit dem Bot startet, bis `start()` kommt; vor dem Start des Bots aufgerufen, wartet der Task auf `start()` |
| `start()` | führt den Task aus, solange der Bot bereit ist, ab sofort, wenn er es ist; ein laufender Task läuft weiter wie bisher |
| `restart()` | bricht den Task ab und startet ihn neu, sodass ein Intervall-Task sofort läuft |
| `change_interval(seconds=...)` oder `change_interval(time=...)` | wechselt zum neuen Zeitplan; das Warten beginnt ab der Änderung neu, oder ab dem Ende eines laufenden Durchgangs |
| `count` | wie viele Läufe seit dem letzten Start des Tasks beendet wurden |
| `seconds`, `times` | der Zeitplan, dem er folgt |
| `@task.before`, `@task.after` | registrieren `async def hook()`, das jedes Mal abgewartet wird, wenn der Task startet und wenn er stoppt, sei es durch `stop()`, `restart()`, einen ausgeschlossenen Router oder `close()` |

Eine Exception in einem Hook wird wie eine in einem Lauf als `TaskError` gesendet. Wirft der `before`-Hook eine Exception, bleibt der Task bis zu `restart()` gestoppt.

## Wann Tasks laufen { #when-tasks-run }

Tasks starten, wenn der Bot `Started` sendet, und `close()` bricht sie ab, bevor es den HTTP-Client schließt. Ein Task, der an einem später eingebundenen Router registriert ist, startet, sobald dieser eingebunden wird, und einer, dessen Router ausgeschlossen wird, wird abgebrochen. Eine App, die nur von [`ASGIApp`](http-interactions.md) bereitgestellt wird, startet ihre Tasks, wenn die App startet.

Eine Funktion lässt sich nur einmal als Task registrieren. Um denselben Code nach zwei Zeitplänen auszuführen, registriere zwei Funktionen.

## Wenn ein Task eine Exception wirft { #when-a-task-raises }

Eine Exception in einem Lauf wird als `TaskError`-Event gesendet, mit `task`, dem `Task`, der sie geworfen hat, und `error`, und der Task läuft zu seinem nächsten Zeitpunkt erneut. Ohne `TaskError`-Listener loggt bostie die Exception samt Traceback.
