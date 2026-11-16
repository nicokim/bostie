---
source: a95f1211f80aa1fe37d568699ddca680b3c3ed9d
description: "OAuth2 do Discord com o bostie: URLs de autorização com PKCE, troca do código e renovação, chamadas à API com o token de um usuário e cargos vinculados."
---

# OAuth2 e cargos vinculados

![Bostie erguendo uma chave brilhante](../assets/mascot/bostie-oauth2.webp){ align=right width="180" loading=lazy }

O OAuth2 permite que um usuário dê ao seu app acesso à conta dele: o perfil, os servidores, as contas conectadas, o direito de adicioná-lo a um servidor ou uma conexão de cargo para cargos vinculados. O bostie monta as URLs de autorização e chama a API com o token que o usuário concede.

```python
--8<-- "docs/snippets/oauth2/oauth_flow.py"
```

## URLs de autorização { #authorization-urls }

`oauth2.authorize_url(client_id, scopes=[...], redirect_uri=..., state=...)` retorna a URL que pede ao usuário que conceda `scopes` e o redireciona de volta para `redirect_uri` com um `code`. Os escopos são membros de `OAuth2Scope`, como `OAuth2Scope.IDENTIFY` para `identify`, um para cada escopo que o Discord documenta; uma string simples funciona para um escopo que o Discord adicionar antes que o bostie lhe dê um nome. Confira se o redirecionamento traz o `state` que você enviou, para que outro site não possa fazer o usuário entrar com o código dele. A função também aceita:

- `code_challenge`, o desafio S256 de um verificador PKCE, que `exchange_code` depois envia como `code_verifier`; o trecho de código calcula um;
- `prompt="none"` para pular a tela de consentimento para um usuário que já concedeu os escopos, ou `"consent"` para mostrá-la de novo;
- `integration_type` para instalar o app em um servidor ou no usuário.

O bostie só monta o fluxo de código de autorização. O Discord desaconselha o fluxo implícito, que coloca o token na URL de redirecionamento e não dá refresh token, e recomenda no lugar dele o fluxo de código com PKCE.

`oauth2.invite_url(client_id, permissions=..., scopes=[...])` retorna o link de instalação do app, com `integration_type`, `guild_id` para pré-selecionar um servidor e `disable_guild_select`. O que ele deixa de fora vem das configurações de instalação do app no Developer Portal.

## Tokens { #tokens }

`BearerClient()` sem um token só alcança `rest.oauth2`:

| Método | Faz |
|---|---|
| `exchange_code(client_id=, client_secret=, code=, redirect_uri=, code_verifier=None)` | transforma o código do redirecionamento em um `AccessToken` |
| `refresh(client_id=, client_secret=, refresh_token=)` | um token novo antes que o antigo expire |
| `revoke(client_id=, client_secret=, token=)` | encerra a autorização de um token e de todos os tokens que ele emitiu |
| `exchange_client_credentials(client_id=, client_secret=, scopes=[...])` | um token para o dono do app, ou para a equipe dele |

Um `AccessToken` tem `access_token`, `token_type`, `expires_in` em segundos, `scope` (os escopos concedidos, separados por espaços) e `refresh_token`. Uma troca de código que incluiu o escopo `bot` também retorna o `guild` em que o bot entrou, e uma com `webhook.incoming` o `webhook` que ela criou. O `repr` de um token esconde os segredos, então escrever um no log não o vaza.

## Chamando a API como o usuário { #calling-the-api-as-the-user }

`async with BearerClient(access_token) as user:` alcança o que os escopos do token permitem:

| `user.rest.` | Métodos | Escopo |
|---|---|---|
| `users` | `fetch_current` | `identify` |
| `users` | `list_connections` | `connections` |
| `users` | `fetch_role_connection`, `update_role_connection`, `delete_role_connection` | `role_connections.write` |
| `guilds` | `list` | `guilds` |
| `members` | `fetch_current(guild_id)` | `guilds.members.read` |
| `commands` | `fetch_permissions`, `list_permissions`, `edit_permissions` | `applications.commands.permissions.update` para editar |
| `oauth2` | `fetch_authorization`, que diz o que o token concede e a quem | qualquer um |
| `lobbies` | o lado do usuário dos [lobbies do Social SDK](platform.md#social-sdk) | `sdk.social_layer` |

Com o escopo `guilds.join`, o bot pode adicionar o usuário a um servidor em que ele está: `await bot.rest.members.add(guild_id, user_id, access_token=token.access_token)`, que retorna o novo `Member`, ou `None` quando o usuário já era membro.

## Cargos vinculados { #linked-roles }

Um cargo vinculado é um cargo de servidor que os usuários ganham ao cumprir condições em uma conexão com o seu app, como "nível pelo menos 10". O app declara uma vez os valores que informa e depois informa os valores de cada usuário com o token dele:

1. Registre até 5 registros de metadados com `bot.rest.application.update_role_connection_metadata(application_id, [...])`, cada um sendo um `ApplicationRoleConnectionMetadata` com `type`, `key`, `name` e `description`. `register_metadata` no trecho de código faz isso com um `RESTClient`.
2. Defina a **Linked Roles Verification URL** do app no Developer Portal como uma página que inicia o fluxo OAuth2 com o escopo `role_connections.write`.
3. Depois da troca, chame `user.rest.users.update_role_connection(application_id, platform_name=..., platform_username=..., metadata={"level": "12"})`. Os valores dos metadados são strings, como a API do Discord os aceita.

O [exemplo de cargos vinculados](../examples.md#linked-roles) é uma página de verificação completa. `fmt.guild_navigation("linked-roles", role_id)` cria no chat um link para o cargo vinculado de um servidor.
