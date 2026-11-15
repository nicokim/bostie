---
source: c45c92d8551bab86affc3711ec78970f48e886d8
---

# Sharding e retomada

![Três Bosties correndo em fila](../assets/mascot/bostie-running.webp){ align=right width="220" loading=lazy }

O Discord divide os servidores de um bot entre conexões do gateway chamadas shards: o servidor `g` pertence ao shard `(g >> 22) % shard_count`. Um bot em mais de 2500 servidores precisa usar shards; o Discord fecha uma conexão sem shards com o código 4011 e `start()` levanta `ShardingRequired`.

## Shards em um processo { #shards-in-one-process }

`Bot(shard_count="auto")`, o padrão, pergunta ao Discord quantos shards usar e roda todos eles. Nada mais é necessário: o bostie inicia os shards dentro dos limites de identify do Discord, e cada shard envia os seus eventos aos mesmos listeners, cache e handlers. `event.shard_id` diz por qual shard um evento chegou.

## Shards em vários processos { #shards-across-processes }

```python
--8<-- "docs/snippets/sharding/cluster.py"
```

`shard_count=16` fixa o total, e `shard_ids` escolhe os shards que este processo roda; `shard_ids` precisa de um `shard_count` explícito, e os seus IDs precisam ser distintos e estar dentro de `range(shard_count)`.

O Discord permite um número limitado de identifies ao mesmo tempo por bot. O bostie respeita esse limite dentro de um processo; entre processos, `before_identify` os coordena. O bostie faz await de `before_identify(shard_id)` antes de cada identify, para que ele possa esperar uma vaga em um lock que os seus processos compartilham. No exemplo, `identify_lock` faz o papel desse lock compartilhado, mantido pelos 5 segundos que o Discord pede entre identifies de um mesmo bucket. Se o hook levantar uma exceção, o bostie escreve o erro no log e faz o identify mesmo assim.

## Eventos de shard { #shard-events }

| Evento | Campos | Enviado quando |
|---|---|---|
| `ShardReady` | `shard_id` | a nova sessão de um shard está pronta e os servidores dele chegaram |
| `ShardDisconnect` | `shard_id`, `code`, `reason`, `resuming` | uma conexão caiu; com `resuming`, o shard retoma a sua sessão e reproduz os eventos que perdeu, caso contrário inicia uma nova sessão |
| `ShardStop` | `shard_id`, `code`, `reason` | um shard parou de vez, porque `close()` rodou ou o Discord o encerrou |
| `ShardBackpressure` | `shard_id`, `queued_bytes` | um shard parou de ler porque o event loop ficou para trás |

Reconexões e retomadas não precisam de nada da sua parte. `bot.latency` é a média do tempo de ida e volta do heartbeat entre os shards, e `bot.gateway.shard(shard_id).latency` a de um shard.

## Retomando depois de um reinício { #resuming-after-a-restart }

Um reinício normalmente inicia uma nova sessão em cada shard, e os eventos dos minutos entre um e outro se perdem. Com `Bot(session_file="sessions.json")`, `close()` mantém as sessões abertas e as grava no arquivo. O próximo `start()` lê o arquivo, o apaga e retoma cada sessão salva que corresponde ao `shard_count` e aos IDs de shard atuais, para que o Discord reproduza os eventos que aconteceram enquanto o bot estava fora do ar. Um arquivo que não pode ser lido vai para o log e é ignorado, e os shards sem uma sessão salva fazem identify.

`bot.run()` fecha com Ctrl+C e SIGTERM, então um reinício feito por um gerenciador de processos ou por um deploy salva as sessões. Um crash não salva, e a próxima inicialização faz identify. Uma interação reproduzida mais de 5 segundos depois de ter sido criada é pulada com um aviso, porque o token dela já não pode ser respondido a tempo.
