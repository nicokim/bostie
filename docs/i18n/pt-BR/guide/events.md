---
source: 0402229d2b242ba10aed929b9dedd0087f57b406
description: "Ouça eventos do gateway do Discord com listeners tipados, espere eventos ou receba-os em stream, use eventos brutos e próprios e veja as intents de cada um."
---

# Eventos

![Bostie cumprimentando um sino que toca](../assets/mascot/bostie-events.webp){ align=right width="180" loading=lazy }

O Discord conta ao bot o que acontece por meio de eventos do gateway: uma mensagem foi enviada, um membro entrou, um cargo mudou. Um listener é uma função assíncrona que recebe um evento.

```python
--8<-- "docs/snippets/events/listeners.py"
```

`@bot.listen` lê a anotação de tipo do primeiro parâmetro e registra a função para aquela classe de evento, ou para cada classe de uma união. `@bot.listen(MessageUpdate)` nomeia as classes no lugar disso, para uma função sem anotação ou com uma mais ampla. As classes ficam em `bostie.events`, e a [lista de eventos](../reference/event-list.md) nomeia cada uma com as suas intents e campos. Um [router](routers.md) tem o mesmo decorador.

Cada listener roda como uma tarefa própria, então um listener lento não segura os outros nem o próximo evento. A tarefa começa na hora: o código de um listener até o seu primeiro `await` roda antes que o próximo evento seja tratado. Os listeners de `Starting` e `Stopped` são a exceção: `start()` e `close()` fazem await deles um depois do outro. `bot.remove_listener(function)` cancela o registro de uma função em todos os eventos que ela escuta.

## Só o que você escuta { #only-what-you-listen-to }

O bostie pede ao seu núcleo em Rust que encaminhe só os nomes de dispatch que algo no bot usa: os seus listeners, o cache, o roteamento de comandos e componentes e as esperas pendentes. Todos os outros eventos são descartados antes de chegar ao Python, então não escutar nada não custa nada. Os eventos tipados são decodificados uma vez por dispatch, e só quando alguém precisa deles.

## Intents { #intents }

O Discord só envia a maioria dos eventos a um bot que pediu as intents deles. `Bot(intents=...)` tem como padrão `Intents.DEFAULT`, todas as intents exceto as três privilegiadas. Quando um listener se registra para um evento que nenhuma das intents do bot entrega, o bostie escreve um aviso no log nomeando a intent a adicionar, e lembra que uma intent privilegiada também precisa ser ativada no Developer Portal. [Intents](gateway.md#intents) as cobre em detalhe.

## Valores antigos e o cache { #old-values-and-the-cache }

Os eventos de atualização trazem `old`, o objeto como o [cache](cache.md) o tinha antes da mudança, e os eventos de exclusão trazem o objeto removido: `RoleUpdate.old`, `ScheduledEventUpdate.old`, `MessageDelete.message`, `MemberRemove.member`. Eles são `None` quando o cache não tinha o objeto. As mensagens só ficam em cache com `Bot(max_messages=...)`, os membros só com `members=True` e as presenças só com `presences=True`.

## Esperando um evento { #waiting-for-an-event }

```python
--8<-- "docs/snippets/events/waiting.py"
```

`await bot.wait_for(Event, check=None)` retorna o próximo evento daquela classe para o qual `check` retorna verdadeiro. `async with bot.stream(Event, check=None) as events:` enfileira cada evento correspondente enquanto o bloco roda, para `async for`; os eventos que chegam enquanto o corpo do laço roda não se perdem. Nenhum dos dois tem um tempo limite próprio, então limite-os com `asyncio.timeout()`. Uma exceção levantada por `check` é levantada pela espera. `bot.wait_for_component` e `bot.wait_for_modal` fazem o mesmo para [usos de componentes](components.md#waiting-for-a-click-inside-a-handler) e [envios de modais](modals.md#waiting-for-the-submit-inside-a-handler).

## Eventos brutos e eventos que o bostie não modela { #raw-events-and-events-bostie-does-not-model }

```python
--8<-- "docs/snippets/events/custom_events.py"
```

`@bot.listen_raw("NAME", ...)` entrega um `RawEvent` para cada nome de dispatch que você passa, modelado ou não, e um listener anotado com `RawEvent` recebe todos os dispatches. Um `RawEvent` tem o `name` do dispatch, o seu número de sequência `seq` e o payload como `raw` (um `dict` novo a cada vez) ou `raw_json` (bytes). Todo evento tipado também tem `raw` e `raw_json`.

Quando o Discord envia um payload que não cabe na sua classe, por exemplo sem um campo que a documentação diz ser obrigatório, o bostie escreve um aviso no log uma vez por nome de evento, e os listeners tipados pulam esse evento enquanto os listeners de `RawEvent` ainda o recebem.

## Eventos próprios { #events-of-your-own }

Crie uma subclasse de `BotEvent`, com `kw_only=True`, e envie uma instância com `bot.dispatch(event)`: os listeners, as esperas e os streams dela a recebem como qualquer outro evento. Uma subclasse sem `NAME` recebe o nome do seu módulo e da sua classe. As classes de evento são structs `msgspec` imutáveis, então os campos delas são declarados como anotações.

## Eventos de ciclo de vida e de shard { #lifecycle-and-shard-events }

O bostie envia eventos próprios sobre o bot: `Starting`, `Started`, `Stopping` e `Stopped` em torno do [ciclo de vida](running.md#the-lifecycle); `ShardReady`, `ShardDisconnect`, `ShardStop` e `ShardBackpressure` sobre cada [shard](sharding.md); `ListenerError` e `TaskError` sobre falhas. As conexões de voz enviam [eventos de voz](../voice/index.md#events) pelo mesmo `@bot.listen`.

## Quando um listener levanta uma exceção { #when-a-listener-raises }

Uma exceção em um listener é enviada como um evento `ListenerError` com o `listener`, o `event` e o `error`. Sem um listener de `ListenerError`, o bostie escreve a exceção no log com o traceback; com um, o logging fica por sua conta. Uma exceção em um listener de `ListenerError` só vai para o log.
