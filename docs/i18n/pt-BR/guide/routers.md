---
source: b30ad56d0f8b63739574acff05fa92b323c4d3d9
description: "Divida um bot do Discord em routers e módulos, dê padrões comuns a grupos de comandos e inclua, exclua ou recarregue módulos enquanto o bot está rodando."
---

# Routers e módulos

Um `Router` guarda comandos, listeners, rotas, tarefas, verificações, hooks e hooks de erro, como o bot, sem ser um bot. Os routers dividem um bot em módulos, dão configurações compartilhadas a um grupo de comandos e podem ser trocados enquanto o bot roda. O próprio bot é um router, então tudo nesta página funciona nele também.

```python
--8<-- "docs/snippets/routers/moderation_router.py"
```

Um router tem todos os decoradores do bot: `command`, `group`, `user_command`, `message_command`, `entry_point`, `listen`, `listen_raw`, `on_component`, `on_modal`, `every`, `check`, `before_invoke`, `after_invoke` e `on_error`. `bot.include(router)` adiciona tudo isso ao bot, e também o que for registrado no router depois. Dentro dos handlers dele, `router.bot` é o bot no qual ele foi incluído; antes disso, levanta `StateError`.

Os routers se aninham: `router.include(other)` funciona do mesmo jeito, e incluir o externo no bot traz os dois.

## Padrões { #defaults }

`Router(...)` aceita, como argumentos nomeados, padrões para os comandos e rotas registrados nele:

| Padrão | Vale para |
|---|---|
| `ephemeral` | as respostas dos seus comandos e rotas |
| `auto_defer` | os seus comandos e rotas |
| `default_permissions` | os seus comandos de nível superior |
| `nsfw` | os seus comandos de nível superior |
| `contexts`, `integration_types` | os seus comandos globais |

Um comando ou rota usa primeiro a sua própria configuração, depois os padrões do seu router, depois os de cada router que o inclui, do mais próximo para fora, e por fim os do bot. No exemplo, `/ban` fica oculto para membros sem Banir membros, só pode ser usado em servidores e é respondido de forma efêmera, então só o moderador que o executou vê o botão e pode apertá-lo.

As verificações, os hooks "antes" e os hooks "depois" de um router valem para os handlers dele; os dos routers que o incluem também rodam, do mais externo primeiro. Os hooks de erro são procurados do router mais próximo para fora, como [Verificações, hooks e erros](checks.md#error-hooks) explica.

## Módulos { #modules }

`bot.include("greetings")` importa o módulo `greetings` e inclui o `Router` que ele chama de `router`. Um módulo sem um levanta `TypeError`.

`bot.exclude("greetings")` remove tudo o que o router adicionou, cancela as tarefas dele e faz o Python esquecer o módulo e os seus submódulos, então incluí-lo de novo importa o arquivo outra vez. `bot.reload("greetings")` faz as duas coisas em uma chamada: se a nova importação ou inclusão falhar, o router antigo continua incluído e o erro é levantado. Recarregar troca os handlers na hora; chame `await bot.sync_commands()` depois quando as definições de comandos mudarem, como o [exemplo modular](../examples.md#modular-bot) faz no seu comando `/reload`.

`exclude` e `reload` também aceitam um objeto `Router`, para routers que não são o `router` de um módulo.

## Conflitos { #conflicts }

Dois comandos com o mesmo nome, dois comandos de usuário ou de mensagem com o mesmo nome, duas rotas com o mesmo modelo, ou uma função registrada duas vezes como tarefa levantam uma exceção quando o segundo é adicionado, inclusive quando é um router que o traz. Um router pode ser incluído uma vez, e nunca em si mesmo.

## Olhando por dentro { #looking-inside }

`router.commands`, `router.context_menus`, `router.component_routes`, `router.modal_routes`, `router.tasks` e `router.listeners` guardam o que está registrado: comandos por nome e servidores, como `("rules", ())` para um global, comandos de usuário e de mensagem por tipo, nome e servidores, rotas por modelo, tarefas por função e listeners por evento. `router.walk_commands()` produz cada comando de barra e subcomando que tem um handler. `router.match_component(custom_id)` e `router.match_modal(custom_id)` retornam a rota a que um custom id chega e os valores dos marcadores dela, ou `None`, e `router.find_error_handlers(error)` produz os hooks de erro que tratariam uma exceção, do mais próximo primeiro.
