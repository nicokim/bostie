---
source: 0c49168c9e0add898da9cb6bf25e6b0a9fb68ecd
description: "Crie apps do Discord instaláveis por usuários, abra Atividades, venda SKUs com entitlements e assinaturas e gerencie lobbies do Social SDK pela API REST."
---

# Atividades, monetização e o Social SDK

Os apps do Discord são mais do que bots em servidores. Esta página cobre os apps instalados em usuários, as Atividades, a venda de SKUs e o lado do servidor do Social SDK.

```python
--8<-- "docs/snippets/apps/premium.py"
```

## Apps instalados em usuários { #apps-installed-to-users }

Um app pode ser instalado na conta de um usuário, além de em um servidor, e os comandos dele passam a funcionar onde quer que esse usuário esteja, incluindo mensagens diretas com amigos e servidores sem o bot. Ative a instalação no usuário na página **Installation** do Developer Portal e depois deixe os comandos usá-la com `integration_types` e `contexts`, como o trecho de código faz para o bot inteiro ([Registrando comandos](registration.md#where-commands-can-be-used)).

Num contexto assim, o bot não está no canal: não há entrada no cache nem evento do gateway, e a API REST recusa a maioria das chamadas ali. `ctx.interaction.authorizing_integration_owners` diz quais instalações permitiram a interação, `ctx.interaction.context` onde ela aconteceu, e `ctx.interaction.app_permissions` o que o app pode fazer. As respostas funcionam como sempre, e o Discord permite 5 mensagens de acompanhamento para um app instalado só no usuário; o bostie avisa na sexta.

## Atividades { #activities }

Uma Atividade é um app web que roda dentro do Discord. O bostie cobre o lado do bot:

- `@bot.entry_point(name)` registra o comando de ponto de entrada que o App Launcher mostra, como [Comandos de usuário, de mensagem e de ponto de entrada](context-menus.md#the-entry-point-command) explica.
- `await ctx.launch_activity()` abre a Atividade a partir de um comando ou de um componente e retorna o ID da instância dela.
- `await bot.rest.application.fetch_activity_instance(application_id, instance_id)` retorna uma `ActivityInstance`: onde ela roda e os IDs dos usuários nela.

O [exemplo de Atividade](../examples.md#activity) registra um ponto de entrada e um comando de barra que a abrem.

## Monetização { #monetization }

Um app com monetização habilitada vende SKUs: assinaturas, itens duráveis e consumíveis. As compras do usuário são entitlements.

- `ctx.interaction.entitlements` lista os entitlements do usuário e do servidor de uma interação, então um comando pode verificar uma compra sem fazer uma requisição.
- `ui.premium_button(sku_id)` mostra um botão que abre a compra de um SKU; ele nunca chega ao bot. `fmt.store(application_id, sku_id=None)` cria um link para a página do app na loja, ou para um SKU, que o Discord mostra como um cartão.
- `bot.rest.monetization` lista os SKUs do app (`list_skus`), percorre os entitlements com filtros por usuário, servidor e SKU (`list_entitlements`), busca um e marca um consumível como usado (`consume_entitlement`). Ele também lista e busca as assinaturas de um SKU.
- `create_test_entitlement(application_id, sku_id=..., owner_id=..., owner_type=1)` concede um entitlement de teste gratuito a um servidor (1) ou a um usuário (2), e `delete_test_entitlement` o remove.
- Os eventos `EntitlementCreate`, `EntitlementUpdate` e `EntitlementDelete`, e `SubscriptionCreate`, `SubscriptionUpdate` e `SubscriptionDelete`, informam as mudanças pelo gateway; os eventos de entitlement também chegam como [eventos de webhook](http-interactions.md#webhook-events). Um entitlement de teste chega como um `PartialEntitlement`, sem as datas de um real.

## Social SDK { #social-sdk }

O Discord Social SDK roda dentro de jogos. O lado do servidor dele é REST:

- `bot.rest.lobbies` cria lobbies, edita os metadados e os membros deles (`add_member`, `bulk_update_members` para até 25 de uma vez, `remove_member`), os exclui, convida um usuário para o canal vinculado ao lobby e define metadados de moderação nas mensagens do lobby.
- `user.rest.lobbies`, em um [`BearerClient`](oauth2.md#calling-the-api-as-the-user) com o token de um jogador, cria um lobby ou entra nele por um segredo (`create_or_join`), sai dele (`leave`), vincula e desvincula um canal do Discord (`link_channel`, `unlink_channel`), envia e lista mensagens (`send_message`, `list_messages`) e convida o próprio jogador para o canal vinculado (`create_invite_for_self`).
- `bot.rest.partner_sdk` troca tokens: um token provisório para um jogador do sistema de contas próprio do jogo, um token pelo token de um provedor de identidade externo, o token de um jogo filho pelo de uma publicadora; ele também desfaz a mesclagem de contas provisórias e define metadados de moderação em mensagens diretas.
- `bot.rest.identities` lista, busca e atualiza as identidades e os perfis de aplicativo dos usuários que vincularam uma conta externa.

As mensagens de lobby e as mensagens diretas dentro do jogo chegam como os [eventos de webhook](http-interactions.md#webhook-events) `LobbyMessage*` e `GameDirectMessage*`.
