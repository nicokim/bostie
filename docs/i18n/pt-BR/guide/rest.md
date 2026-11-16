---
source: 18ff3457c37f07dad7f0bcfc3aee4b5f5383fac0
description: "Chame a API REST do Discord para bots: paginação, limpeza de mensagens, registro de auditoria, erros, rate limits, retentativas, downloads e requisições brutas."
---

# API REST

![Bostie pulando para pegar uma bola](../assets/mascot/bostie-jumping.webp){ align=right width="180" loading=lazy }

`bot.rest` tem um método para cada operação da API REST do Discord que um bot pode chamar, agrupados pelo recurso sobre o qual agem: `bot.rest.messages.create`, `bot.rest.members.add_role`, `bot.rest.guilds.fetch`. As requisições passam pelo cliente HTTP em Rust do bostie, que segue os rate limits do Discord.

```python
--8<-- "docs/snippets/rest/rest_calls.py"
```

## Convenções { #conventions }

- Os IDs da URL vêm primeiro, somente posicionais, na ordem da URL: `bot.rest.members.add_role(guild_id, user_id, role_id)`.
- Todo o resto é um argumento nomeado como na documentação do Discord, verificado contra um `TypedDict` por operação, então um erro de digitação é um erro de tipo e um `TypeError` em tempo de execução.
- As operações que o Discord registra no registro de auditoria aceitam `reason=`, de 1 a 512 caracteres, mostrado aos moderadores do servidor.
- Um campo de imagem ou de som, como o `image` de um emoji ou o `icon` de um servidor, aceita um `File`; o bostie o envia como o data URI que o Discord espera. Os campos de permissão aceitam `Permissions`.
- Um emoji, como o que `reactions.create` adiciona, aceita um emoji Unicode, um emoji personalizado como `<:name:id>`, `<a:name:id>` ou `name:id`, ou um modelo `Emoji` ou `PartialEmoji`.
- Os métodos chamados `fetch` buscam um objeto, `list` percorre vários, `edit` modifica, `create` cria e `delete` exclui. Os outros verbos mantêm a palavra da documentação, como `members.add_role` ou `guilds.begin_prune`.
- Os métodos retornam modelos, ou `None` quando o Discord responde sem conteúdo. Eles não mudam `bot.cache`, exceto que `channels.create_dm` guarda o canal de mensagem direta quando `max_dm_channels` permite; os eventos do gateway que seguem uma mudança atualizam o cache.

## Paginação { #paging }

Os métodos `list` dos endpoints que o Discord pagina retornam um iterador assíncrono, que busca a página seguinte conforme você avança: `async for message in bot.rest.messages.list(channel_id, limit=500):`, como `/today` faz acima. Sem `limit`, ele percorre todos os itens. `before`, `after` e `around` começam em outro ponto, no máximo um deles; eles aceitam um ID ou um `datetime`, que o bostie transforma no ID daquele momento. As mensagens vêm das mais novas para as mais antigas, ou das mais antigas para as mais novas com `after`. Um método `list` de um endpoint que o Discord não pagina é uma corrotina que retorna uma tupla. `audit_log.list` produz objetos `AuditLogPage` inteiros, com os usuários, webhooks e outros objetos aos quais as entradas se referem.

## Limpando mensagens { #purging-messages }

`messages.purge(channel_id, limit=100, check=None)` percorre as últimas `limit` mensagens, das mais novas para as mais antigas, e exclui aquelas para as quais `check` retorna verdadeiro, ou todas, quando ele não é passado; `limit=None` percorre o canal inteiro, e `before` e `after` começam em outro ponto, como em `list`. Mensagens com menos de 14 dias vão em exclusões em massa de até 100, as mais antigas em uma requisição cada, já que o Discord só exclui em massa mensagens recentes; um tipo que o Discord não deixa excluir, como a mensagem inicial de um tópico, é pulado. `bulk=False` exclui uma de cada vez, como em uma mensagem direta, onde exclusões em massa não existem. Ele retorna as mensagens excluídas, e `reason` vai para o registro de auditoria. As exclusões em massa e as mensagens de outras pessoas precisam de Gerenciar mensagens.

```python
--8<-- "docs/snippets/rest/purge.py"
```

## O registro de auditoria { #the-audit-log }

`audit_log.list(guild_id)` produz objetos `AuditLogPage`, e o gateway envia cada nova entrada como um evento `AuditLogEntryCreate`. As `changes` de uma entrada guardam um `AuditLogChange` por campo alterado, cujos `new_value` e `old_value` têm o tipo que o objeto alterado dá àquele campo: `Permissions` para o `permissions` de um cargo, um `datetime` para o `communication_disabled_until` de um membro, uma tupla de `Overwrite` para o `permission_overwrites` de um canal. As exceções do Discord seguem a documentação dele: `$add` e `$remove` nos cargos de um membro guardam objetos `AuditLogRole`, o `avatar_hash` de um webhook e o `channel_id` de um convite têm o tipo de `avatar` e de um ID, e uma mudança de permissão de comando tem como chave o ID do cargo, do usuário ou do canal e guarda um `CommandPermission`. Uma chave que o bostie não conhece, ou um valor num formato que o campo não aceita, mantém o seu valor JSON, e um valor que o Discord omite é `None`.

```python
--8<-- "docs/snippets/rest/audit_log.py"
```

## Erros { #errors }

Uma requisição que o Discord responde com um status de erro levanta uma `HTTPException`: `BadRequest` para 400, `Forbidden` para 403, `NotFound` para 404, `ServerError` para 5xx. A exceção tem o `status`, o `code` do Discord como um `JSONErrorCode`, a `message` dele, os `errors` por campo e o `body` bruto; o texto dela lista cada erro de campo em uma linha própria. `UnexpectedResponse` significa que o Discord aceitou a requisição, mas a resposta dele não decodificou; a mensagem diz se a mudança teve efeito, e `error.raw` guarda a resposta.

## Rate limits, novas tentativas e tempos limite { #rate-limits-retries-and-timeouts }

O cliente em Rust acompanha o bucket de rate limit de cada rota e o limite global do Discord, e segura uma requisição até que o bucket dela tenha espaço. `Bot(http_settings=HTTPSettings(...))` muda como:

| Campo de `HTTPSettings` | Padrão | Significado |
|---|---|---|
| `global_rate_limit` | 50 | requisições por segundo somando todas as rotas; aumente-o se o Discord deu ao bot um limite maior |
| `max_rate_limit_wait` | 30.0 | a espera mais longa por um rate limit; uma requisição que esperaria mais levanta `RateLimited` sem ser enviada |
| `retries` | 2 | quantas vezes uma requisição que falhou é repetida, com backoff exponencial que espera no máximo `max_rate_limit_wait` |
| `connect_timeout` | 10.0 | segundos para abrir uma conexão |
| `read_timeout` | 60.0 | segundos para esperar uma resposta |
| `rate_limiter` | `True` | `False` envia na hora e só espera passar os 429 que voltam, para uso atrás de um proxy de rate limit |
| `proxy` | `None` | uma URL de proxy HTTP ou HTTPS para todas as requisições, incluindo o áudio de `voice.Url` e os downloads da CDN, com credenciais incluídas como em `http://user:password@host:3128` |

Um 429 é repetido depois do atraso que o Discord indica, se ele estiver dentro de `max_rate_limit_wait`. Um 500, 502, 503, 504 ou 524, ou uma conexão que caiu, é repetido `retries` vezes em todos os métodos menos `POST`, que pode ter tido efeito; uma conexão que nunca abriu é repetida em todos os métodos.

O Discord bane um IP que envia 10 000 requisições inválidas (401, 403, 429) em 10 minutos. O bostie as conta e escreve um aviso no log em 1000, 5000 e 9000. Em 9500, ele recusa toda requisição com `RequestBudgetExhausted` em vez de enviá-la, até que a contagem dos últimos 11 minutos caia abaixo de 9000.

## Qualquer endpoint { #any-endpoint }

`bot.http.request(method, route, **params)` envia qualquer requisição pelo mesmo cliente e retorna o JSON decodificado, ou `None` para uma resposta vazia. A rota mantém os seus marcadores, que os argumentos nomeados preenchem: `await bot.http.request("GET", "/channels/{channel_id}/pins", channel_id=123)`. Ele aceita `json=`, `files=` para um upload multipart, `form=` para campos de formulário nomeados, `urlencoded=` para um corpo form-urlencoded, `query=` e `reason=`, e levanta as mesmas exceções. `bot.http.send(...)` aceita os mesmos argumentos e retorna os bytes brutos da resposta, para um endpoint que não responde com JSON.

## Baixando arquivos { #downloading-files }

Os anexos e as imagens ficam na CDN do Discord, não atrás da API, então um download não envia token nem gasta rate limit. `bot.http` os busca pelo mesmo cliente em Rust, com os seus tempos limite, novas tentativas e `proxy`:

| Método | Faz |
|---|---|
| `read(source)` | retorna os bytes |
| `save(source, path)` | grava-os no arquivo em `path` |
| `to_file(source)` | retorna um `File` para enviar de novo; a partir de um `Attachment`, ele mantém o nome do arquivo, a descrição, o título, o spoiler e os detalhes de mensagem de voz, e a partir de uma URL, a última parte do caminho dá o nome |

`source` é um `Attachment`, ou uma URL como as que os helpers de [`bostie.cdn`](formatting.md) montam. Um arquivo ausente levanta `NotFound`.

```python
--8<-- "docs/snippets/rest/downloads.py"
```

## Sem um bot { #without-a-bot }

Um script ou um backend web que só precisa de REST usa um `RESTClient`:

```python
--8<-- "examples/rest_script.py"
```

`async with RESTClient(token) as client:` abre o cliente HTTP; `client.rest` tem todos os grupos de `bot.rest`, e `client.http` o cliente bruto. O token tem como padrão `BOSTIE_TOKEN`, e o cliente aceita `api_url`, `http_settings` e `allowed_mentions` como o bot. O [guia de OAuth2](oauth2.md) cobre o `BearerClient`, que chama a API com o token de um usuário, e o [guia de webhooks](webhooks.md) os clientes que não precisam de token nenhum.

## Os grupos { #the-groups }

| `bot.rest.` | Métodos |
|---|---|
| `application` | `edit`, `fetch`, `fetch_activity_instance`, `fetch_bot_application`, `list_role_connection_metadata`, `update_role_connection_metadata` |
| `audit_log` | `list` |
| `auto_moderation` | `create`, `delete`, `edit`, `fetch`, `list` |
| `bans` | `bulk_create`, `create`, `fetch`, `list`, `remove` |
| `channels` | `add_recipient`, `create`, `create_dm`, `create_group_dm`, `delete`, `delete_permission`, `edit`, `edit_permissions`, `edit_positions`, `fetch`, `fetch_member_permissions`, `fetch_role_permissions`, `follow`, `list`, `remove_recipient`, `set_voice_status`, `trigger_typing_indicator` |
| `commands` | `bulk_overwrite`, `bulk_overwrite_guild`, `create`, `create_guild`, `delete`, `delete_guild`, `edit`, `edit_guild`, `fetch`, `fetch_guild`, `fetch_permissions`, `list`, `list_guild`, `list_permissions` |
| `emojis` | `create`, `create_application`, `delete`, `delete_application`, `edit`, `edit_application`, `fetch`, `fetch_application`, `list`, `list_application` |
| `gateway` | `fetch`, `fetch_bot` |
| `guilds` | `begin_prune`, `edit`, `edit_incident_actions`, `edit_welcome_screen`, `edit_widget`, `fetch`, `fetch_preview`, `fetch_prune_count`, `fetch_vanity_url`, `fetch_welcome_screen`, `fetch_widget`, `fetch_widget_image`, `fetch_widget_settings`, `leave`, `list` |
| `identities` | `delete`, `fetch_profile`, `list`, `list_by_external_id`, `update_profile` |
| `integrations` | `delete`, `list` |
| `invites` | `add_target_user`, `bulk_add_target_users`, `bulk_delete_target_users`, `create`, `delete`, `fetch`, `fetch_target_users_job_status`, `list`, `list_guild`, `list_target_users`, `remove_target_user`, `update_target_users` |
| `lobbies` | `add_member`, `bulk_update_members`, `create`, `create_invite_for_user`, `delete`, `edit`, `fetch`, `remove_member`, `update_message_moderation_metadata` |
| `members` | `add`, `add_role`, `edit`, `edit_current`, `fetch`, `fetch_permissions`, `list`, `remove`, `remove_role`, `search` |
| `messages` | `bulk_delete`, `create`, `crosspost`, `delete`, `edit`, `fetch`, `list`, `list_pins`, `pin`, `purge`, `search`, `unpin` |
| `monetization` | `consume_entitlement`, `create_test_entitlement`, `delete_test_entitlement`, `fetch_entitlement`, `fetch_subscription`, `list_entitlements`, `list_skus`, `list_subscriptions` |
| `onboarding` | `edit`, `fetch` |
| `partner_sdk` | `create_provisional_token`, `exchange_child_token`, `exchange_external_token`, `unmerge_external_account`, `unmerge_provisional_account`, `update_dm_moderation_metadata` |
| `polls` | `end`, `list_answer_voters` |
| `reactions` | `create`, `delete`, `delete_all`, `delete_all_for_emoji`, `delete_own`, `list` |
| `roles` | `create`, `delete`, `edit`, `edit_positions`, `fetch`, `fetch_member_counts`, `fetch_permissions`, `list` |
| `scheduled_events` | `create`, `delete`, `edit`, `fetch`, `list`, `list_users` |
| `soundboard` | `create`, `delete`, `edit`, `fetch`, `list`, `list_default`, `send` |
| `stage_instances` | `create`, `delete`, `edit`, `fetch` |
| `stickers` | `create`, `delete`, `edit`, `fetch`, `fetch_guild`, `fetch_pack`, `list`, `list_packs` |
| `templates` | `create`, `delete`, `edit`, `fetch`, `list`, `sync` |
| `threads` | `add_member`, `fetch_member`, `join`, `leave`, `list_active`, `list_joined_private_archived`, `list_members`, `list_private_archived`, `list_public_archived`, `remove_member`, `start`, `start_from_message`, `start_in_forum` |
| `users` | `edit_current`, `fetch`, `fetch_current` |
| `voice` | `edit_current_state`, `edit_state`, `fetch_current_state`, `fetch_state`, `list_guild_regions`, `list_regions` |
| `webhooks` | `create`, `delete`, `delete_message`, `delete_with_token`, `edit`, `edit_message`, `edit_with_token`, `execute`, `execute_github`, `execute_slack`, `fetch`, `fetch_message`, `fetch_with_token`, `list`, `list_guild` |

Os parâmetros e o tipo de retorno de cada método estão na [referência da API REST](../reference/api/rest.md). Algumas operações ficam de propósito para `bot.http.request`: endpoints que o Discord descontinuou ou nunca documentou, e os endpoints de callback de interação e de resposta original, que os [contextos de interação](responses.md) cobrem.

Alguns grupos atendem a recursos do Discord além dos bots em servidores: `monetization` e `application` para [monetização e Atividades](platform.md), `lobbies`, `identities` e `partner_sdk` para o [Social SDK](platform.md#social-sdk), e `application.update_role_connection_metadata` para os [cargos vinculados](oauth2.md#linked-roles).
