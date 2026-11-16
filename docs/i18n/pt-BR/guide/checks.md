---
source: 63c601a21b6ce6311976189053f39ff84cf42f5d
description: "Restrinja comandos com verificações de permissão, cargo, dono, cooldown e concorrência, rode hooks antes e depois dos handlers e trate erros com hooks de erro."
---

# Verificações, hooks e erros

![Bostie de braços cruzados, soltando fumaça](../assets/mascot/bostie-angry.webp){ align=right width="180" loading=lazy }

Uma verificação decide se um handler pode rodar. Os hooks rodam código antes e depois dos handlers. Os hooks de erro respondem quando algo levanta uma exceção.

## Verificações { #checks }

```python
--8<-- "docs/snippets/checks/command_checks.py"
```

Uma verificação é uma função que recebe o contexto e retorna um `bool`, e que também pode ser assíncrona. `False` recusa a execução com `CheckFailure`, e uma verificação pode levantar uma `CheckFailure` própria: a mensagem dela é o que o usuário recebe. As verificações vão na configuração `checks=[...]` de um comando, grupo, subcomando, comando de usuário ou de mensagem, ponto de entrada, ou rota de componente e de modal, e `@router.check` registra uma para todos os handlers de um router; o bot é um router.

As verificações do bot rodam primeiro, depois as de cada router, do mais externo para dentro, depois as dos grupos, de cima para baixo, e por fim as do próprio comando. A primeira recusa interrompe a execução. O preenchimento automático não roda verificações.

`bostie.checks` tem as mais comuns:

| Verificação | Recusa com | Quando |
|---|---|---|
| `has_permissions(permissions)` | `MissingPermissions` | falta ao usuário alguma de `permissions` no canal, ou ele está em uma mensagem direta |
| `bot_has_permissions(permissions)` | `BotMissingPermissions` | falta ao bot alguma delas no canal |
| `Cooldown(rate, seconds, per="user")` | `CooldownActive` | a chave já a usou `rate` vezes nos últimos `seconds` |
| `MaxConcurrency(number, per="global")` | `MaxConcurrencyReached` | `number` execuções para a chave ainda estão em andamento |
| `has_role(role)` | `MissingRole` | falta ao usuário o cargo, dado por ID ou nome, ou ele está em uma mensagem direta |
| `has_any_role(*roles)` | `MissingRole` | o usuário não tem nenhum dos cargos |
| `is_owner` | `NotOwner` | o usuário não é um dos `bot.owner_ids` |

Um objeto de verificação que também tem um método `release(ctx)`, um `checks.ReleasingCheck` como `MaxConcurrency`, é liberado assim que o handler termina, seja como for que termine.

`per` é `"user"`, `"guild"` (o usuário, em mensagens diretas), `"channel"`, `"global"` ou uma função do contexto que retorna qualquer chave hashable. As verificações de permissão leem as permissões que o Discord envia com a interação, então não precisam de cache nem de requisição. Os erros delas trazem `missing`, as `Permissions` que faltam, e `CooldownActive` traz `retry_after` em segundos.

## Cargos e donos { #roles-and-owners }

```python
--8<-- "docs/snippets/checks/staff_checks.py"
```

`has_role` e `has_any_role` aceitam IDs ou nomes de cargos. Um ID é comparado com os cargos que o Discord envia com a interação, então não precisa de cache. Um nome é procurado entre os cargos do servidor em cache, então um bot sem o cache, ou um [app só por HTTP](http-interactions.md), nomeia os cargos por ID. `MissingRole` traz os `roles` pedidos, e a mensagem dela menciona um cargo dado por ID, que o Discord mostra como o nome do cargo.

`is_owner` é uma verificação do jeito que está, sem parênteses. `bot.owner_ids` guarda os usuários que ela deixa passar: os `owner_ids` passados para `Bot(...)` ou, se não houver, o dono do app, lido do aplicativo quando o bot inicia. Para um app que pertence a uma equipe, eles são o dono da equipe e os administradores e desenvolvedores que aceitaram o convite; os membros somente leitura ficam de fora.

## O que o usuário recebe { #what-the-user-is-told }

Quando nenhum hook de erro trata uma `CheckFailure`, o bostie responde com a mensagem dela, de forma efêmera: "You need the Manage Messages permission.", "The bot needs the Manage Messages permission.", "Try again" com um timestamp relativo como "in 42 seconds", "This is already running; try again once it is done.", "You need the Moderator role.", "Only the bot's owners can use this." ou "You cannot use this." para um `False` simples.

## Hooks antes e depois { #hooks-before-and-after }

```python
--8<-- "docs/snippets/checks/error_hooks.py"
```

`@router.before_invoke` registra um `async def hook(ctx)` que roda depois que as verificações passaram e antes do handler; `@router.after_invoke`, um que roda depois do handler, também quando ele levantou uma exceção. Eles rodam para todos os comandos, rotas de componente e rotas de modal do router, então um hook que pode ver um componente não deve supor um `Context`. Os hooks "antes" do bot rodam primeiro e os hooks "depois" dele por último. Uma exceção vinda de um hook "depois" vai para o log e não muda a resposta.

## Hooks de erro { #error-hooks }

Uma exceção vinda de uma verificação, de um conversor, de um hook "antes" ou do handler vai para um hook de erro. `@x.on_error` registra um `async def hook(ctx, error)` para toda `Exception`, e `@x.on_error(NotFound, ValueError)` para esses tipos e as suas subclasses. `x` é um comando, um grupo, uma rota de componente ou de modal, um router ou o bot, e cada um pode ter um hook por tipo de exceção.

O bostie procura um hook nesta ordem, e o primeiro que encontra trata o erro:

1. o comando, depois o grupo dele e o grupo de nível superior;
2. o router ao qual o comando pertence, depois cada router em que ele está incluído, até o bot.

Dentro de um mesmo lugar, vence o hook para a classe mais próxima do erro: um hook para `NotFound` ganha de um para `HTTPException`. Um hook que levanta o mesmo erro o repassa ao próximo hook nessa ordem, como `slow_down` faz acima. Um hook que levanta outro erro faz com que esse erro vá para o log.

O tipo do contexto é `ErrorContext`: um `Context`, `ComponentContext`, `ModalContext` ou `AutocompleteContext`. O contexto de preenchimento automático não pode chamar `respond`, então verifique esse caso como acima.

Quando um hook retorna sem responder, o bostie escreve um aviso no log e responde "Something went wrong.". Quando nenhum hook trata o erro, o bostie responde a uma `CheckFailure` com a mensagem dela e escreve qualquer outra coisa no log com o traceback antes de responder "Something went wrong.". Nada é respondido quando o handler já tinha respondido, e um preenchimento automático recebe uma lista vazia de sugestões.

## Erros fora dos handlers { #errors-outside-handlers }

Listeners e tarefas não têm usuário a quem responder. Uma exceção em um listener é enviada como um evento `ListenerError`, e uma em uma tarefa como um `TaskError`; sem um listener para eles, o bostie os escreve no log. Veja [Eventos](events.md#when-a-listener-raises) e [Tarefas em segundo plano](tasks.md).

## Tipos de exceção { #exception-types }

Tudo o que o bostie levanta sobre o Discord, a rede ou o estado do bot é um `BostieError`. Um argumento inválido levanta `ValueError` ou `TypeError` no lugar disso. Alguns erros também são uma exceção padrão: `StateError` é um `RuntimeError`, `MissingIntent` um `ValueError`, `StartupError` um `OSError` e `CommandDefinitionError` um `TypeError`. Os erros sobre comandos e interações compartilham a base `CommandError`, e os erros de voz a base `VoiceError`.

| Exceção | Levantada quando |
|---|---|
| `HTTPException` | uma requisição REST terminou com um status de erro; `status`, `code` (um `JSONErrorCode`), `message`, `errors` e o `body` bruto |
| `BadRequest`, `Forbidden`, `NotFound`, `ServerError` | 400, 403, 404 e 5xx |
| `RateLimited` | 429, ou o bostie segurou uma requisição que esperaria mais que `max_rate_limit_wait`; `retry_after`, `is_global` |
| `InvalidToken` | o Discord rejeitou o token, no REST ou no gateway |
| `TransportError` | uma requisição falhou antes de o Discord responder |
| `UnexpectedResponse` | o Discord aceitou uma requisição, mas a resposta não decodifica; `raw` a guarda |
| `RequestBudgetExhausted` | requisições inválidas demais recentemente, e o bostie recusou uma para proteger o IP |
| `GatewayClosed`, `PrivilegedIntentsRequired`, `ShardingRequired` | o Discord fechou o gateway de vez; as duas últimas para os códigos 4014 e 4011 |
| `ShardNotRunning`, `GatewayQueueFull` | um comando do gateway para um shard que este processo não roda, ou com comandos demais esperando |
| `StateError` | uma chamada que o estado do objeto não permite, como `bot.rest` antes de `start()` |
| `MissingIntent` | um recurso precisa de uma intent que o bot não pediu |
| `MemberRequestRateLimited` | o Discord recusou uma requisição de membros; `retry_after` |
| `StartupError` | o bostie não conseguiu preparar um recurso do sistema de que precisa |
| `CommandDefinitionError` | um comando, grupo ou opção quebra as regras do Discord; levantada pelo decorador |
| `CommandSyncError` | o Discord rejeitou os comandos durante uma sincronização |
| `OptionConversionError` | uma opção ou um alvo não foi convertido para a sua anotação |
| `InteractionResponded`, `InteractionNotAnswered`, `InteractionExpired` | uma chamada de resposta que o estado da interação não permite |
| `CheckFailure` e as suas subclasses | uma verificação recusou uma execução |

Os erros de voz estão na [página de voz](../voice/index.md#errors). A [referência de erros](../reference/api/errors.md) lista cada classe com os seus atributos.
