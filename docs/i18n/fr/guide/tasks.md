---
source: b58df394d71cd32e4c45cccfdd0da40ddcb957c3
description: "Exécutez des tâches de fond toutes les quelques secondes ou à heure fixe avec @bot.every, à démarrer, arrêter, relancer ou replanifier en cours de route."
---

# Tâches de fond

![Bostie endormi pendant qu'un réveil sonne](../assets/mascot/bostie-sleeping.webp){ align=right width="180" loading=lazy }

`@bot.every` exécute une fonction asynchrone sans arguments selon un calendrier, tant que le bot est prêt.

```python
--8<-- "docs/snippets/tasks/scheduled.py"
```

`every(seconds=300)` exécute la fonction dès que le bot est prêt, puis toutes les 300 secondes. Le calendrier ne dérive pas : chaque exécution démarre au multiple suivant de `seconds` à partir de la première, et une exécution qui dure plus de `seconds` saute les échéances qu'elle a chevauchées.

`every(time=...)` l'exécute chaque jour à une heure donnée, ou à chacune de plusieurs heures. Un `datetime.time` sans `tzinfo` est en UTC. Avec un décalage fixe, comme ci-dessus, il reste à ce décalage ; avec un `zoneinfo.ZoneInfo`, il suit les changements d'heure d'été de la zone. Passez `seconds` ou `time`, pas les deux.

## Contrôler une tâche { #controlling-a-task }

```python
--8<-- "docs/snippets/tasks/task_control.py"
```

`@bot.every` renvoie une `Task`, et attendre `check_feeds()` exécute toujours la fonction une fois. Ses méthodes modifient le calendrier pendant que le bot tourne :

| `Task` | Effet |
|---|---|
| `stop()` | annule la tâche, pendant une exécution ou entre deux, et l'empêche de démarrer avec le bot jusqu'à `start()` ; appelée avant que le bot tourne, la tâche attend `start()` |
| `start()` | exécute la tâche tant que le bot est prêt, dès maintenant s'il l'est ; une tâche en cours continue comme avant |
| `restart()` | annule la tâche et la redémarre de zéro : une tâche à intervalle s'exécute donc immédiatement |
| `change_interval(seconds=...)` ou `change_interval(time=...)` | passe au nouveau calendrier ; l'attente repart de zéro à partir du changement, ou de la fin d'une exécution en cours |
| `count` | le nombre d'exécutions terminées depuis le dernier démarrage de la tâche |
| `seconds`, `times` | le calendrier qu'elle suit |
| `@task.before`, `@task.after` | enregistrent `async def hook()`, attendu à chaque démarrage de la tâche et à chaque arrêt, que ce soit par `stop()`, `restart()`, un routeur exclu ou `close()` |

Une exception dans un hook est envoyée sous forme de `TaskError`, comme une exception dans une exécution. Quand le hook `before` lève une exception, la tâche reste arrêtée jusqu'à `restart()`.

## Quand les tâches s'exécutent { #when-tasks-run }

Les tâches démarrent quand le bot envoie `Started`, et `close()` les annule avant de fermer le client HTTP. Une tâche enregistrée sur un routeur inclus plus tard démarre dès son inclusion, et une tâche dont le routeur est exclu est annulée. Une application servie uniquement par [`ASGIApp`](http-interactions.md) démarre ses tâches au démarrage de l'application.

Une fonction ne peut être enregistrée comme tâche qu'une seule fois. Pour exécuter le même code selon deux calendriers, enregistrez deux fonctions.

## Quand une tâche lève une exception { #when-a-task-raises }

Une exception dans une exécution est envoyée sous forme d'événement `TaskError` avec la `task`, la `Task` qui a levé l'exception, et l'`error`, et la tâche s'exécute de nouveau à sa prochaine échéance. Sans écouteur `TaskError`, bostie journalise l'exception avec sa trace d'appels.
