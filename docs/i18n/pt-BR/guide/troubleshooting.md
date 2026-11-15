---
source: 14575d90a6a022a3c0cbfce22c048b404bcf4957
---

# Solução de problemas

![Bostie pensando até o ponto de interrogação virar uma lâmpada](../assets/mascot/bostie-thinking.webp){ align=right width="180" loading=lazy }

O bostie escreve um aviso no log para cada erro que consegue perceber. Esta página os lista pelo que você observa, com a linha de log a procurar e a correção.

## Um comando diz "O aplicativo não respondeu" { #a-command-says-the-application-did-not-respond }

| Linha de log | Causa | Correção |
|---|---|---|
| `nothing handles interaction ... (command 'name')` | o Discord ainda tem um comando que o bot em execução não define, muitas vezes depois de uma renomeação com `auto_sync=False`, ou o comando fica em outro processo | sincronize os comandos ([Registrando comandos](registration.md)) |
| `nothing handles interaction ... (custom_id '...')` | nenhum modelo de `on_component` ou `on_modal` corresponde ao custom id | confira o modelo com os IDs que você envia; monte-os com `route.id(...)` |
| `... returned without answering` | o handler terminou sem `respond`, `defer` ou `modal` | responda em todos os caminhos, incluindo os retornos antecipados |
| `... returned after deferring without answering` | ele adiou e nunca respondeu, então "pensando…" fica até o token expirar | chame `respond` depois do trabalho lento |
| `interaction ... reached its handler ... s after it was received; something is blocking the event loop` | código bloqueante em um handler ou listener segurou o event loop além dos 3 segundos do Discord | mova o trabalho para `asyncio.to_thread`, ou diminua `auto_defer` |
| `interaction ... was replayed ... s after it was created; its token is dead` | a interação veio na reprodução de uma sessão retomada, tarde demais para responder | nada; o usuário pode executá-la de novo |

Uma exceção não tratada em um handler vai para o log com o traceback, e o usuário vê "Something went wrong.": adicione um [hook de erro](checks.md#error-hooks) para responder algo melhor.

## Os eventos não chegam { #events-do-not-arrive }

- `... listens to EVENT, which needs the INTENT intent; add it to Bot(intents=...)`: o bot não pediu a intent. Adicione-a e, se for uma intent privilegiada, ative-a também no Developer Portal.
- `start()` levanta `PrivilegedIntentsRequired`: o bot pediu uma intent privilegiada que o portal não permite. Ative-a em **Bot** → **Privileged Gateway Intents**, ou remova-a de `intents`.
- `message.content` vem vazio em servidores: falta a intent `MESSAGE_CONTENT`.
- `EVENT has an unexpected shape (...); typed listeners skip it`: o Discord enviou um payload que não cabe na classe do evento, registrado no log uma vez por nome de evento. Os listeners de `RawEvent` ainda o recebem; por favor, informe a linha.
- `old` é `None` em eventos de atualização: o cache não tinha o objeto; veja [Cache](cache.md#what-it-keeps) para saber o que ele guarda.

## O gateway { #the-gateway }

- `shard N paused reading: ... bytes of events are waiting because the event loop is not keeping up`: algo bloqueia o event loop; veja [Contrapressão](gateway.md#backpressure).
- `close code N (...) means Discord refused what bostie sent; this is likely a bostie bug, please report it`: os códigos 4001, 4002, 4005 e 4008 significam que o bostie enviou algo que o Discord recusou.
- `the members of guild N stopped arriving; the cache lacks some`: uma requisição de membros para o cache esgotou o tempo depois de 30 segundos por bloco.
- `start()` levanta `InvalidToken`: o token está errado ou foi redefinido; copie-o de novo da página **Bot**.

## REST { #rest }

- `N invalid requests (401, 403, 429) in the last 10 minutes; Discord bans the IP at 10 000`: o bot continua fazendo requisições que o Discord recusa, geralmente por falta de permissões ou por um webhook morto. Encontre as chamadas que falham nas exceções que elas levantam.
- `RateLimited` levantada sem que uma requisição chegue ao Discord: a rota continua limitada por mais tempo que `max_rate_limit_wait`; aumente-o em [`HTTPSettings`](rest.md#rate-limits-retries-and-timeouts) ou tente de novo mais tarde.

## Respostas { #answers }

- `ephemeral=... is ignored because the message being edited is ...`: uma mensagem não pode mudar entre efêmera e pública depois de enviada.
- `Discord allows 5 follow-ups to an interaction of an app installed only to the user`: a sexta mensagem de acompanhamento de um app instalado no usuário falha.
- `the error hook ... left interaction ... unanswered; bostie answered 'Something went wrong.'`: faça o hook responder.

## Voz { #voice }

- `nobody hears the bot in guild N: it is server muted`, ou `it is suppressed on this stage; use vc.stage('request')`: um moderador silenciou o bot no servidor, ou ele é um ouvinte em um palco.
- `a moderator deafened the bot in guild N, so Discord sends it no audio`: os receptores não recebem nada até que o bot deixe de estar ensurdecido.
- `VoiceConnectError` com o motivo `TIMEOUT`: o Discord não respondeu à entrada em até `timeout` segundos; o canal pode estar cheio, ou falta ao bot a permissão Conectar.
- `MissingIntent: voice needs Intents.GUILD_VOICE_STATES`: a voz precisa dessa intent, que `Intents.DEFAULT` inclui.

## Relatando um bug { #reporting-a-bug }

`python -m bostie` imprime as versões a incluir, e `bostie.voice.versions()` acrescenta a libopus e o DAVE para problemas de voz. Defina o logger relevante como `DEBUG` antes de iniciar o bot, como [Logging](running.md#logging) mostra, e inclua as linhas em torno do problema, com o token removido.
