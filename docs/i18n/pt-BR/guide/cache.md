---
source: f23a479950d38c899e79ae34c9fa1854c7b6fd34
---

# Cache e permissões

![Bostie largando um osso dentro de uma caixa](../assets/mascot/bostie-cache.webp){ align=right width="180" loading=lazy }

`bot.cache` guarda o que o gateway contou ao bot, para que os handlers possam consultar coisas sem fazer uma requisição. Ele é preenchido a partir de eventos, nunca a partir das chamadas REST que você faz.

```python
--8<-- "docs/snippets/cache/cache_lookups.py"
```

## O que ele guarda { #what-it-keeps }

| Guardado | Por padrão | Opção |
|---|---|---|
| servidores, com os seus cargos, emojis e figurinhas | sim | |
| canais e tópicos ativos | sim | |
| estados de voz | sim | |
| eventos agendados, instâncias de palco e sons do painel de sons | sim | |
| o usuário do bot e o próprio membro dele em cada servidor | sim | |
| todos os membros | não | `members=True`, precisa de `Intents.GUILD_MEMBERS` |
| presenças dos membros que não estão offline | não | `presences=True`, precisa de `Intents.GUILD_PRESENCES` |
| as mensagens mais recentes | não | `max_messages=N` |
| os canais de mensagem direta mais recentes | não | `max_dm_channels=N` |

`Bot(cache=False)` não guarda nada, e as outras opções então levantam `ValueError`. Uma opção cuja intent está faltando levanta `MissingIntent` quando o `Bot` é criado. Os canais são guardados como o Discord os enviou, exceto os campos que `CHANNEL_UPDATE` não mantém atualizados (`last_message_id`, `message_count`, `member_count`, `last_pin_timestamp`), que ficam `None`.

Com `members=True`, o Discord envia só alguns membros dos servidores grandes quando o bot conecta. O bostie então pede o resto, um servidor de cada vez por shard, e um shard só conta como pronto quando os seus servidores estão completos. `chunk_members=False` pula isso, e o cache se preenche conforme os membros entram, mudam ou falam. As requisições que sofrem rate limit são repetidas depois do atraso do Discord.

## Consultas { #lookups }

| Método | Retorna |
|---|---|
| `guild(id)`, `guilds()` | um `Guild` ou todos eles |
| `channel(id)`, `guild_channels(guild_id)` | um `Channel`, ou os canais de um servidor com os seus tópicos ativos |
| `role(id)`, `guild_roles(guild_id)` | um `Role`, ou os cargos de um servidor |
| `voice_state(guild_id, user_id)`, `guild_voice_states(guild_id)` | onde os usuários estão na voz; `None` quando o usuário não está em nenhum canal de voz |
| `scheduled_event(id)`, `guild_scheduled_events(guild_id)` | um `ScheduledEvent`, ou os de um servidor |
| `stage_instance(id)`, `guild_stage_instances(guild_id)` | uma `StageInstance` ao vivo, ou as de um servidor |
| `soundboard_sound(sound_id)`, `guild_soundboard_sounds(guild_id)` | um `SoundboardSound`, ou os de um servidor |
| `me(guild_id)` | o próprio `Member` do bot em um servidor |
| `member(guild_id, user_id)`, `guild_members(guild_id)` | membros; só o do próprio bot, a menos que `members=True` |
| `presence(guild_id, user_id)`, `guild_presences(guild_id)` | presenças, com `presences=True`; `None` quando offline |
| `user(id)` | o usuário do bot, ou o usuário de um membro ou canal de mensagem direta em cache |
| `dm_channel(user_id)` | o canal de mensagem direta do bot com um usuário, se guardado |
| `message(id)` | uma das últimas `max_messages` mensagens |
| `permissions(channel_id, member=None)` | as permissões de um membro em um canal, as do bot quando `member` é omitido |
| `guild_permissions(guild_id, member=None)` | o mesmo, no servidor inteiro |

As consultas retornam `None`, ou uma lista vazia, para o que o cache não tem. `bot.cache.clear()` o esvazia. Os eventos de atualização e exclusão trazem o objeto em cache de antes da mudança, como [Eventos](events.md#old-values-and-the-cache) explica.

## O cache e, se não houver, o REST { #the-cache-else-rest }

```python
--8<-- "docs/snippets/cache/lookups_with_rest.py"
```

`bot.lookup` retorna um objeto do cache quando ele o tem, e o pede ao REST caso contrário: `user(user_id)`, `member(guild_id, user_id)`, `channel(channel_id)`, `guild(guild_id)`, `role(guild_id, role_id)` e `message(channel_id, message_id)`. O que o REST retorna não é guardado: o cache continua seguindo só o gateway, então uma segunda consulta do mesmo objeto ausente pergunta de novo. Os erros do REST passam adiante, como `NotFound` para um ID que não existe.

## Eventos agendados, palcos e sons { #scheduled-events-stages-and-sounds }

```python
--8<-- "docs/snippets/cache/guild_events.py"
```

Eles chegam com cada servidor e acompanham os seus eventos de criação, atualização e exclusão, que precisam de `Intents.GUILD_SCHEDULED_EVENTS` para eventos agendados e de `Intents.GUILD_EXPRESSIONS` para sons do painel de sons; as duas estão em `Intents.DEFAULT`. A resposta a `bot.request_soundboard_sounds` substitui os sons de um servidor. Os eventos de atualização deles trazem `old`, e `SoundboardSoundDelete` traz o `sound` removido.

## Inicialização { #startup }

Um shard fica pronto assim que cada servidor listado no seu `READY` chegou como um `GuildCreate`, ou assim que nenhum servidor chega por `guild_ready_timeout` segundos, 2 por padrão. `Started` espera todos os shards. Um `GuildCreate` com `joined=True` é um servidor ao qual o bot acabou de ser adicionado; os outros são servidores que ficam disponíveis.

`large_threshold`, de 50 a 250 e 50 por padrão, é a quantidade de membros acima da qual o Discord envia um servidor sem os seus membros offline.

## Permissões { #permissions }

`cache.permissions(channel_id, member)` aplica as regras do Discord: o dono do servidor e os administradores têm tudo; caso contrário, o cargo `@everyone`, depois os cargos do membro, depois as substituições de permissão do canal para `@everyone`, para os cargos e para o membro; um membro de castigo mantém só Ver canal e Ler histórico de mensagens; sem Ver canal não há nada; sem Enviar mensagens, as permissões que dependem dela somem; em um canal de voz sem Conectar, as permissões de voz somem. Um tópico usa as substituições de permissão do seu canal pai. Ele retorna `None` quando o cache não tem o canal, o servidor ou o pai de um tópico.

O mesmo cálculo está disponível sem o cache: `bostie.permissions.compute_permissions(guild, member, channel=None, *, parent=None)`, em que `parent` é o canal pai de um tópico. Esse módulo também guarda conjuntos de `Permissions` tirados da documentação do Discord: `ALL`, `NONE`, `TEXT`, `VOICE` e `STAGE` (as permissões que se aplicam a cada tipo de canal), `ELEVATED` (as que exigem autenticação em duas etapas na conta do moderador quando o servidor a exige) e `TIMED_OUT` (as que um membro de castigo mantém).

Dentro de uma interação, o cache não é necessário: `ctx.interaction.app_permissions` guarda as permissões do bot no canal, e `ctx.member.permissions` as do usuário.

## Hierarquia de cargos { #role-hierarchy }

Um membro só pode expulsar, banir, castigar ou editar membros cujo cargo mais alto está abaixo do seu, e o Discord mostra o nome de um membro na cor do seu cargo colorido mais alto. `bostie.permissions` tem os três helpers, que recebem o `Guild` e os membros vindos do cache, de uma interação ou do REST:

| Helper | Retorna |
|---|---|
| `top_role(guild, member)` | o `Role` mais alto do membro, ou `None` quando ele só tem `@everyone` |
| `display_colors(guild, member)` | as `RoleColors` do seu cargo mais alto com uma cor, ou `None` |
| `can_act_on(guild, actor, target)` | se `actor` está acima de `target`, um membro ou um cargo: o dono está acima de todos, ninguém está acima do dono e, nos outros casos, o cargo mais alto do ator é comparado por posição com o cargo mais alto do alvo, ou com o próprio cargo, como o Discord faz no gerenciamento de cargos |

Cargos na mesma posição são ordenados pelo ID, o cargo mais antigo primeiro, como o Discord os ordena.

```python
--8<-- "docs/snippets/cache/role_hierarchy.py"
```
