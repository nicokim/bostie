---
source: b58df394d71cd32e4c45cccfdd0da40ddcb957c3
description: "Rode tarefas em segundo plano a cada poucos segundos ou em horários fixos com @bot.every e inicie, pare, reinicie ou reagende essas tarefas com o bot rodando."
---

# Tarefas em segundo plano

![Bostie dormindo enquanto um despertador toca](../assets/mascot/bostie-sleeping.webp){ align=right width="180" loading=lazy }

`@bot.every` roda uma função assíncrona sem argumentos em um cronograma, enquanto o bot está pronto.

```python
--8<-- "docs/snippets/tasks/scheduled.py"
```

`every(seconds=300)` roda a função assim que o bot fica pronto e depois a cada 300 segundos. O cronograma não se desloca: cada execução começa no próximo múltiplo de `seconds` a partir da primeira, e uma execução que leva mais que `seconds` pula os intervalos com que se sobrepôs.

`every(time=...)` a roda todo dia em um horário, ou em cada um de vários. Um `datetime.time` sem `tzinfo` está em UTC. Com um deslocamento fixo, como acima, ele fica nesse deslocamento; com um `zoneinfo.ZoneInfo`, ele acompanha as mudanças de horário de verão do fuso. Passe `seconds` ou `time`, não os dois.

## Controlando uma tarefa { #controlling-a-task }

```python
--8<-- "docs/snippets/tasks/task_control.py"
```

`@bot.every` retorna uma `Task`, e fazer await de `check_feeds()` ainda roda a função uma vez. Os métodos dela mudam o cronograma enquanto o bot roda:

| `Task` | Faz |
|---|---|
| `stop()` | cancela a tarefa, durante uma execução ou entre execuções, e impede que ela comece junto com o bot até `start()`; chamado antes de o bot rodar, a tarefa espera por `start()` |
| `start()` | roda a tarefa enquanto o bot está pronto, a partir de agora se ele estiver; uma tarefa em execução continua como estava |
| `restart()` | cancela a tarefa e a inicia de novo, então uma tarefa de intervalo roda na hora |
| `change_interval(seconds=...)` ou `change_interval(time=...)` | passa para o novo cronograma; a espera recomeça a partir da mudança, ou do fim de uma execução em andamento |
| `count` | quantas execuções terminaram desde a última vez que a tarefa começou |
| `seconds`, `times` | o cronograma que ela segue |
| `@task.before`, `@task.after` | registram um `async def hook()`, com await a cada vez que a tarefa começa e a cada vez que para, seja por `stop()`, `restart()`, um router excluído ou `close()` |

Uma exceção em um hook é enviada como um `TaskError`, como uma em uma execução. Quando o hook `before` levanta uma exceção, a tarefa fica parada até `restart()`.

## Quando as tarefas rodam { #when-tasks-run }

As tarefas começam quando o bot envia `Started`, e `close()` as cancela antes de fechar o cliente HTTP. Uma tarefa registrada em um router incluído depois começa assim que ele é incluído, e uma cujo router é excluído é cancelada. Um app servido só pelo [`ASGIApp`](http-interactions.md) inicia as suas tarefas quando o app inicia.

Uma função só pode ser registrada como tarefa uma vez. Para rodar o mesmo código em dois cronogramas, registre duas funções.

## Quando uma tarefa levanta uma exceção { #when-a-task-raises }

Uma exceção em uma execução é enviada como um evento `TaskError` com a `task`, a `Task` que levantou a exceção, e o `error`, e a tarefa roda de novo no seu próximo horário. Sem um listener de `TaskError`, o bostie escreve a exceção no log com o traceback.
