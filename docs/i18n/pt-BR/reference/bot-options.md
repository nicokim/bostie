---
source: 104f50c5749c73fc5952a47fa18309cb11805eb3
---

# Opções do Bot

Todas as opções de `Bot(...)`, todas somente nomeadas. Os guias explicam cada uma no seu contexto.

## Gateway e identidade { #gateway-and-identity }

| Opção | Padrão | Significado |
|---|---|---|
| `intents` | `Intents.DEFAULT` | as [intents](../guide/gateway.md#intents) a pedir; o padrão tem todas as intents exceto as três privilegiadas |
| `status` | `Status.ONLINE` | o status de [presença](../guide/gateway.md#presence) ao fazer login |
| `activities` | `()` | as atividades ao fazer login, montadas com `bostie.activity` |
| `afk` | `False` | se o bot se apresenta como ausente |
| `idle_since` | `None` | o `datetime` em que o bot ficou ocioso |
| `large_threshold` | 50 | de 50 a 250: a quantidade de membros acima da qual o Discord deixa os membros offline fora do payload de um servidor |
| `shard_count` | `"auto"` | o número de [shards](../guide/sharding.md), ou `"auto"` para a recomendação do Discord |
| `shard_ids` | `"all"` | os shards que este processo roda; precisa de um `shard_count` explícito |
| `before_identify` | `None` | um `async def f(shard_id)` com await antes de cada identify, para coordenar processos |
| `session_file` | `None` | um caminho onde `close()` salva as sessões e o próximo `start()` as [retoma](../guide/sharding.md#resuming-after-a-restart) |
| `guild_ready_timeout` | 2.0 | segundos sem um servidor novo depois dos quais um shard conta como [pronto](../guide/cache.md#startup) |

## Comandos e interações { #commands-and-interactions }

| Opção | Padrão | Significado |
|---|---|---|
| `auto_sync` | `True` | [sincroniza os comandos](../guide/registration.md) na inicialização quando eles diferem dos do Discord |
| `dev_guilds` | `()` | IDs dos servidores onde registrar os comandos, em vez de globalmente |
| `contexts` | `None` | onde os comandos globais podem ser usados, a menos que um comando ou router diga outra coisa |
| `integration_types` | `None` | quais instalações trazem os comandos globais, a menos que um comando ou router diga outra coisa |
| `translate` | `None` | um `f(text, locale)` que retorna uma [tradução](../guide/registration.md#translations) ou `None` |
| `auto_defer` | 2.5 | segundos depois dos quais uma interação sem resposta tem a resposta [adiada](../guide/responses.md#deferring); `None` desativa |
| `allowed_mentions` | `AllowedMentions(parse=("users", "roles"))` | quais menções podem [notificar](../guide/responses.md#allowed-mentions) por padrão |
| `owner_ids` | `None` | os usuários que [`is_owner`](../guide/checks.md#roles-and-owners) deixa passar; `None` lê o dono ou a equipe do app |

## Cache { #cache }

| Opção | Padrão | Significado |
|---|---|---|
| `cache` | `True` | mantém o [cache](../guide/cache.md); `False` não guarda nada |
| `members` | `False` | guarda todos os membros; precisa de `Intents.GUILD_MEMBERS` |
| `chunk_members` | `True` | com `members=True`, pede na inicialização os membros que o Discord deixa de fora |
| `presences` | `False` | guarda as presenças; precisa de `Intents.GUILD_PRESENCES` |
| `max_messages` | 0 | quantas das mensagens mais recentes guardar |
| `max_dm_channels` | 0 | quantos dos canais de mensagem direta mais recentes guardar |

## HTTP, voz e encerramento { #http-voice-and-shutdown }

| Opção | Padrão | Significado |
|---|---|---|
| `http_settings` | `HTTPSettings()` | [rate limits, novas tentativas, tempos limite e proxy](../guide/rest.md#rate-limits-retries-and-timeouts) das requisições REST |
| `api_url` | `None` | a URL base do REST; senão `BOSTIE_API_URL`, senão a do Discord |
| `voice` | `voice.Defaults()` | as [configurações de voz](../voice/index.md#settings) das quais toda conexão parte |
| `shutdown_grace` | 0.0 | segundos que [`close()`](../guide/running.md#the-lifecycle) dá aos handlers em execução antes de cancelá-los |

`run(token=None, loop_factory=None)` e `start(token=None)` aceitam o token, `BOSTIE_TOKEN` por padrão.
