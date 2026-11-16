---
source: b9e5abe55a3d0d8dca78cd7fdbd13cbe0ba3c5fe
description: "Rode um bot do bostie: o token, o ciclo de vida de inicialização e desligamento, dentro de um programa asyncio ou app web, logging e vários bots em um processo."
---

# Rodando um bot

![Bostie comemorando com confete](../assets/mascot/bostie-celebrating.webp){ align=right width="180" loading=lazy }

## `bot.run()` { #botrun }

`bot.run()` é o programa inteiro para a maioria dos bots. Ele configura o logging em INFO no stderr, a menos que o logger raiz já tenha handlers, inicia um event loop e roda o bot até ele parar. Ctrl+C e, na thread principal, SIGTERM o encerram de forma limpa.

O token vem da variável de ambiente `BOSTIE_TOKEN`, ou de `bot.run(token)`. Um `Bot ` no início é removido, e um token ausente levanta `ValueError`. `run` também aceita `loop_factory`, uma função que cria o event loop, repassada para `asyncio.run`.

## O ciclo de vida { #the-lifecycle }

`bot.start()` faz o trabalho por trás de `run()`:

1. Ele envia o evento `Starting` e faz await de cada um dos listeners dele, um de cada vez. O REST funciona aqui, o gateway não; um listener que levanta uma exceção interrompe a inicialização.
2. Ele [sincroniza os comandos](registration.md), a menos que `auto_sync=False`.
3. Ele conecta os shards do gateway. Quando todos os shards estão prontos e os servidores deles chegaram, ele envia `Started` uma vez, inicia as [tarefas em segundo plano](tasks.md) e deixa `wait_until_ready()` retornar.
4. Ele trata eventos até `close()`, ou até o Discord fechar o gateway de vez, o que levanta uma exceção, por exemplo `InvalidToken` ou `PrivilegedIntentsRequired`.

```python
--8<-- "docs/snippets/running/lifecycle_events.py"
```

`await bot.close()` encerra nesta ordem:

1. Se `Started` foi enviado, ele envia `Stopping` e dá aos listeners dele até 5 segundos enquanto o gateway ainda está aberto.
2. Ele sai de todos os canais de voz e depois fecha cada shard, o que encerra a sessão dele com o código de fechamento 1000, ou a guarda para uma [retomada](sharding.md#resuming-after-a-restart).
3. Ele cancela as tarefas em segundo plano. Os handlers ainda em execução recebem `shutdown_grace` segundos, 0 por padrão, antes de serem cancelados também.
4. Ele fecha o cliente HTTP e envia `Stopped`, de cujos listeners faz await. `bot.http`, `bot.rest` e `bot.gateway` deixam de existir.

`close()` pode ser chamado de qualquer lugar, mais de uma vez, e enquanto `start()` roda; o bot pode ser iniciado de novo depois. `async with bot:` o fecha quando o bloco termina.

## Dentro de outro programa { #inside-another-program }

Quando o seu programa já roda um event loop, inicie o bot como uma tarefa:

```python
--8<-- "docs/snippets/running/inside_asyncio.py"
```

`await bot.wait_until_ready()` retorna assim que `Started` foi enviado, na hora se já tiver sido, e levanta `StateError` se o bot parar antes; fazer await da tarefa de `start()` então levanta o motivo. `bot.is_ready` diz o mesmo sem esperar. O [exemplo de app web](../examples.md#web-app) roda um bot dentro do lifespan de um servidor web e publica mensagens a partir de uma rota web.

Um bot que só responde a interações por HTTP não precisa de gateway: veja [Interações por HTTP](http-interactions.md). `await bot.open()` o inicia desse jeito e retorna: ele abre o cliente HTTP, busca o aplicativo e o usuário do bot, envia `Starting`, sincroniza os comandos, inicia as tarefas em segundo plano e retorna o aplicativo. `ASGIApp` o chama, e `close()` o encerra. Um script que só chama a API REST usa um [`RESTClient`](rest.md#without-a-bot).

## Enquanto ele roda { #while-it-runs }

| Atributo | Valor |
|---|---|
| `bot.user` | o `CurrentUser` do bot, depois de conectado |
| `bot.application_id` | o ID do app, depois de iniciado |
| `bot.latency` | a média do tempo de ida e volta do heartbeat dos shards em segundos, ou `None` |
| `bot.is_running` | se `start()` ou `open()` está rodando o bot, até `close()` encerrá-lo |
| `bot.is_ready` | se `Started` foi enviado desde o último `start()` |
| `bot.rest`, `bot.http` | a [API REST](rest.md), depois de iniciado |
| `bot.gateway` | os [shards do gateway](gateway.md#raw-gateway-commands), depois de conectado |
| `bot.cache` | o [cache](cache.md) |
| `bot.voice` | as [conexões de voz](../voice/index.md) |
| `bot.auto_defer` | o atraso padrão do [adiamento automático](responses.md#deferring); pode ser mudado a qualquer momento |

`bot.rest`, `bot.http` e `bot.gateway` levantam `StateError` antes de `start()`.

## Logging { #logging }

O bostie escreve logs no módulo padrão `logging` com estes nomes:

| Logger | O que ele informa |
|---|---|
| `bostie.bot` | handlers que falharam ou não responderam, intents de que um listener precisa, interações não tratadas |
| `bostie.gateway` | conexões, desconexões, retomadas, contrapressão, requisições inválidas |
| `bostie.rest` | rate limits e novas tentativas, vindos do cliente HTTP em Rust |
| `bostie.sync` | o que cada sincronização de comandos mudou |
| `bostie.context` | respostas que não puderam ser aplicadas, como uma flag efêmera em uma edição |
| `bostie.events` | payloads que não couberam no seu evento, uma vez por nome de evento |
| `bostie.voice` | conexões de voz, DAVE, sinks, receptores e players |
| `bostie.bridge` | a ligação entre o Python e o núcleo em Rust |

O núcleo em Rust escreve logs pelos mesmos loggers e lê os níveis deles quando o bot inicia: defina `logging.getLogger("bostie.gateway").setLevel(logging.DEBUG)` antes de `run()` para ver o gateway em detalhe.

## Apontando o bostie para outro lugar { #pointing-bostie-elsewhere }

`Bot(api_url=...)` envia as requisições REST para outra URL base, como um proxy de rate limit compartilhado por vários processos ou um [Discord falso](testing.md) em testes. Se for omitido, o bostie lê `BOSTIE_API_URL` quando o bot inicia, e usa `https://discord.com/api/v10` quando ela também não está definida. Atrás de um proxy de rate limit, passe `http_settings=HTTPSettings(rate_limiter=False)`, para que o bostie deixe a espera por conta do proxy. O [guia de REST](rest.md#rate-limits-retries-and-timeouts) cobre as outras configurações de HTTP.

## Vários bots em um processo { #several-bots-in-one-process }

Cada `Bot` tem a sua própria conexão com o núcleo em Rust, o seu próprio cache e o seu próprio cliente HTTP, então um processo pode rodar vários bots, cada um iniciado e fechado por conta própria, em um único event loop.

## Guardando estado { #keeping-state }

Um bot é um objeto comum: crie uma subclasse de `Bot` para dar atributos a ele, como o [exemplo de ciclo de vida](../examples.md#lifecycle) faz com uma conexão de banco de dados aberta em um listener de `Starting` e fechada em um de `Stopped`. Todas as opções de `Bot` estão listadas na [referência de opções do Bot](../reference/bot-options.md).
