---
source: 3337dc9b9c1508c500eac3639bbfb9fc948f4a27
---

# Intents, presença e o gateway

O gateway é o WebSocket pelo qual o Discord envia eventos e o bot envia a sua presença e as suas requisições. O núcleo em Rust do bostie o mantém conectado: heartbeats, retomadas, reconexões, compressão e o rate limit de envio. Esta página cobre o que o bot pede a ele.

## Intents { #intents }

O Discord só envia um evento a um bot que pediu a intent dele. `Bot(intents=...)` aceita uma flag `Intents`, `Intents.DEFAULT` quando omitida: todas as intents exceto as três privilegiadas.

| Intent privilegiada | Necessária para |
|---|---|
| `GUILD_MEMBERS` | eventos de entrada, atualização e saída de membros, todos os membros no cache, pedir todos os membros |
| `GUILD_PRESENCES` | atualizações de presença e presenças no cache |
| `MESSAGE_CONTENT` | o conteúdo, as incorporações, os anexos e os componentes de mensagens em servidores, exceto mensagens que mencionam o bot; mensagens diretas não precisam de intent para isso |

Uma intent privilegiada também precisa ser ativada na página **Bot** do Developer Portal. Sem isso, o Discord fecha a conexão com o código 4014 e `start()` levanta `PrivilegedIntentsRequired`, cuja mensagem diz onde ativá-la. Adicione intents com `|`: `Intents.DEFAULT | Intents.MESSAGE_CONTENT`. O bostie avisa quando um listener precisa de uma intent que o bot não tem, e levanta `MissingIntent` quando uma opção ou uma requisição precisa de uma.

A voz precisa de `GUILD_VOICE_STATES`, que `DEFAULT` inclui.

```python
--8<-- "docs/snippets/gateway/intent_presets.py"
```

Três predefinições ficam ao lado das intents individuais: `Intents.DEFAULT`, também chamada `Intents.UNPRIVILEGED`, tem todas as intents exceto as privilegiadas, `Intents.PRIVILEGED` as três privilegiadas e `Intents.ALL` as duas coisas. `& ~` tira intents de uma predefinição, como acima.

## Presença { #presence }

```python
--8<-- "docs/snippets/gateway/presence_and_requests.py"
```

`Bot(status=..., activities=[...], afk=False, idle_since=None)` define o que o bot mostra quando conecta. `await bot.update_presence(...)` muda isso em todos os shards, e nos shards que conectarem depois; uma opção omitida mantém o seu valor.

`status` é um `Status`: `ONLINE`, `IDLE`, `DND` ou `INVISIBLE`. `bostie.activity` monta as atividades:

| Builder | Mostra |
|---|---|
| `activity.playing(name)` | Jogando *name* |
| `activity.listening(name)` | Ouvindo *name* |
| `activity.watching(name)` | Assistindo *name* |
| `activity.competing(name)` | Competindo em *name* |
| `activity.streaming(name, url)` | Transmitindo *name*, para os serviços de transmissão que o Discord suporta |
| `activity.custom(text)` | um status personalizado com *text* |

Todos, exceto `custom`, aceitam `state=`, uma segunda linha de texto, e retornam um `activity.ActivityDict`.

## Requisições { #requests }

Alguns dados só vêm pelo gateway, como resposta a uma requisição:

- `await bot.request_members(guild_id, query="ab", limit=10)` retorna os membros cujo nome de usuário ou apelido começa com `query`, no máximo `limit` (de 0 a 100). `user_ids=[...]` pede até 100 membros por ID no lugar disso. Sem nenhum dos dois, ele pede todos os membros, o que precisa de `GUILD_MEMBERS`. `presences=True` adiciona as presenças deles e precisa de `GUILD_PRESENCES`. A resposta é um `RequestedMembers` com `members`, `presences` e `not_found`, os IDs que o Discord não encontrou. O Discord aplica rate limit a essa requisição: uma recusada levanta `MemberRequestRateLimited` com `retry_after`.
- `await bot.request_soundboard_sounds(guild_id)` retorna os sons do painel de sons do servidor.
- `await bot.request_channel_info(guild_id)` retorna o status de voz e o horário de início da voz dos canais do servidor; `fields=["status"]` pede menos.

Cada uma espera até 30 segundos por cada parte da resposta, `timeout=` para membros, e levanta `TimeoutError` depois disso. Uma requisição a um shard que está iniciando uma nova sessão levanta `StateError`; tente de novo quando ele estiver pronto.

## Comandos brutos do gateway { #raw-gateway-commands }

`bot.gateway.send(op, d)` envia qualquer comando do gateway. O opcode 3 (presença) vai para todos os shards; um payload com `guild_id` vai para o shard desse servidor, um com `guild_ids` para cada shard com a sua parte dos IDs, e qualquer outra coisa para o shard 0. As respostas do Discord chegam como eventos, e só para nomes de evento que algo escuta, então combine um comando bruto com um listener `listen_raw` ou um `wait_for`.

`bot.gateway` também oferece `shard_ids`, os shards que este processo roda; `shard_for(guild_id)`, o shard para o qual o Discord envia os eventos de um servidor; `shard(shard_id)`, com o seu próprio `send`, `reconnect` e `latency`; `reconnect()`, que derruba todas as conexões e retoma as sessões em conexões novas; e `latency`, a média do tempo de ida e volta do heartbeat. Um comando para um shard que este processo não roda levanta `ShardNotRunning`, e um que fica na fila atrás de muitos outros levanta `GatewayQueueFull`.

## Contrapressão { #backpressure }

O núcleo em Rust lê os eventos tão rápido quanto o Discord os envia e os entrega ao Python. Quando o event loop fica para trás, um shard para de ler até o Python alcançá-lo, escreve um aviso no log e envia `ShardBackpressure` com os bytes em espera. Trabalho bloqueante longo em um handler ou listener causa isso; mova esse trabalho para uma thread com `asyncio.to_thread`.
