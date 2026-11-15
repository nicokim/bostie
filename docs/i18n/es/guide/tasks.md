---
source: 7cf12918970912a9363cc1aac58fb080c085fef9
---

# Tareas en segundo plano

![Bostie duerme mientras suena un despertador](../assets/mascot/bostie-sleeping.webp){ align=right width="180" loading=lazy }

`@bot.every` ejecuta una función asíncrona sin argumentos según un horario, mientras el bot está listo.

```python
--8<-- "docs/snippets/tasks/scheduled.py"
```

`every(seconds=300)` ejecuta la función en cuanto el bot está listo y luego cada 300 segundos. El horario no se desfasa: cada ejecución empieza en el siguiente múltiplo de `seconds` contado desde la primera, y una ejecución que tarda más que `seconds` se salta los ticks con los que se superpuso.

`every(time=...)` la ejecuta todos los días a una hora del día, o a cada una de varias. Un `datetime.time` sin `tzinfo` está en UTC. Con un desfase fijo, como arriba, se mantiene en ese desfase; con un `zoneinfo.ZoneInfo`, sigue los cambios de horario de verano de la zona. Pasa `seconds` o `time`, no los dos.

## Controlar una tarea { #controlling-a-task }

```python
--8<-- "docs/snippets/tasks/task_control.py"
```

`@bot.every` devuelve un `Task`, y esperar `check_feeds()` con `await` sigue ejecutando la función una vez. Sus métodos cambian el horario mientras el bot se ejecuta:

| `Task` | Qué hace |
|---|---|
| `stop()` | cancela la tarea, durante una ejecución o entre ejecuciones, y evita que arranque con el bot hasta `start()`; si se llama antes de que el bot se ejecute, la tarea espera a `start()` |
| `start()` | ejecuta la tarea mientras el bot está listo, desde ahora si lo está; una tarea en marcha sigue como estaba |
| `restart()` | cancela la tarea y la vuelve a iniciar, así que una tarea con intervalo se ejecuta de inmediato |
| `change_interval(seconds=...)` o `change_interval(time=...)` | pasa al nuevo horario; la espera vuelve a empezar desde el cambio, o desde el final de una ejecución en curso |
| `count` | cuántas ejecuciones terminaron desde que la tarea arrancó por última vez |
| `seconds`, `times` | el horario que sigue |
| `@task.before`, `@task.after` | registran un `async def hook()`, que se espera cada vez que la tarea arranca y cada vez que se detiene, ya sea por `stop()`, `restart()`, un router excluido o `close()` |

Una excepción en un hook se envía como un `TaskError`, igual que una en una ejecución. Cuando el hook `before` lanza una excepción, la tarea queda detenida hasta `restart()`.

## Cuándo se ejecutan las tareas { #when-tasks-run }

Las tareas arrancan cuando el bot envía `Started`, y `close()` las cancela antes de cerrar el cliente HTTP. Una tarea registrada en un router que se incluye más tarde arranca en cuanto se incluye, y una cuyo router se excluye se cancela. Una aplicación servida solo por [`ASGIApp`](http-interactions.md) arranca sus tareas cuando arranca la aplicación.

Una función se puede registrar como tarea una sola vez. Para ejecutar el mismo código con dos horarios, registra dos funciones.

## Cuando una tarea lanza una excepción { #when-a-task-raises }

Una excepción en una ejecución se envía como un evento `TaskError` con el `task`, el `Task` que la lanzó, y el `error`, y la tarea se vuelve a ejecutar en su siguiente horario. Sin un listener de `TaskError`, bostie registra la excepción en el log con su traceback.
