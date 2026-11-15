---
source: 04dd5c7d848eed6b7fe26c0af24d76fdfca1a0d2
---

# Interações por HTTP e eventos de webhook

O Discord pode enviar as interações para uma URL do seu app em vez do gateway, e envia alguns eventos, os eventos de webhook, só para uma URL. `ASGIApp` serve os dois a partir do mesmo bot, com os seus comandos, rotas e listeners sem mudanças.

```python
--8<-- "docs/snippets/http/http_bot.py"
```

`bostie.ASGIApp(bot)` é uma aplicação ASGI: sirva `app` com qualquer servidor ASGI, atrás de HTTPS. Depois, no Developer Portal, defina a **Interactions Endpoint URL** do app e, para eventos de webhook, a **Webhook Events URL** dele, com os tipos de evento que você quer, apontando para onde ele escuta. O Discord verifica um endpoint quando você o salva, com um PING e uma requisição com uma assinatura errada, e `ASGIApp` responde aos dois como o Discord espera.

## O que ele faz { #what-it-does }

Quando o servidor inicia, pelo lifespan do ASGI, o app chama `bot.open()`, a menos que o bot já esteja rodando no gateway, o que:

1. abre o cliente HTTP do bot;
2. busca o aplicativo, pelo ID dele e pela chave pública que assina as requisições do Discord, e o usuário do bot;
3. envia `Starting`, sincroniza os comandos e inicia as [tarefas em segundo plano](tasks.md).

Uma falha ali interrompe a inicialização do servidor com o erro. Quando o servidor desliga, o app fecha o bot se foi ele que o iniciou.

Cada requisição é verificada antes de qualquer outra coisa: só `POST` é aceito (405 caso contrário), os corpos têm limite de 4 MiB (413), e uma requisição cuja `X-Signature-Ed25519` não confere com a chave pública recebe 401. Depois:

- Um PING de interação recebe o PONG que o Discord espera.
- Qualquer outra interação é respondida com 202 e entregue ao bot, que roda o seu handler de comando, de rota ou de modal como faria pelo gateway; o handler responde pela API REST do Discord, com `ctx.respond` e o resto. O [adiamento automático](responses.md#deferring) funciona do mesmo jeito.
- Um evento de webhook chega aos listeners do bot e recebe 204, assim como o PING que o Discord envia para a Webhook Events URL.

`ASGIApp(bot, token=..., public_key=...)` aceita o token, ou então `BOSTIE_TOKEN`, e a chave pública, ou então a que o aplicativo informa.

## Com ou sem o gateway { #with-or-without-the-gateway }

Servido sozinho, o app não tem conexão com o gateway: nada de eventos do gateway, de atualizações do cache nem de voz. Comandos, componentes, modais, REST e eventos de webhook funcionam. Para ter os dois, inicie o bot também no gateway, no mesmo event loop, e o app passa a usar o bot em execução; as interações então chegam por HTTP e o gateway continua entregando eventos.

## Eventos de webhook { #webhook-events }

| Evento | Enviado quando |
|---|---|
| `ApplicationAuthorized` | um usuário adicionou o app a um servidor ou à sua conta |
| `ApplicationDeauthorized` | um usuário removeu o app, ou os tokens OAuth2 dele terminaram |
| `EntitlementCreate`, `EntitlementUpdate`, `EntitlementDelete` | um entitlement mudou |
| `QuestUserEnrollment` | um usuário foi adicionado a uma Missão; o Discord o documenta, mas ainda não o envia aos apps |
| `LobbyMessageCreate`, `LobbyMessageUpdate`, `LobbyMessageDelete` | uma mensagem foi enviada, editada ou excluída em um lobby do Social SDK |
| `GameDirectMessageCreate`, `GameDirectMessageUpdate`, `GameDirectMessageDelete` | uma mensagem direta em uma sessão do Social SDK mudou |

Escute-os com `@bot.listen` como os eventos do gateway. Um tipo de evento de webhook que o bostie não modela chega como um `RawEvent`.

## No seu próprio servidor web { #in-your-own-web-server }

As duas peças que `ASGIApp` usa são públicas, para um framework web que você já roda:

```python
--8<-- "docs/snippets/http/own_server.py"
```

`verify_signature(public_key, signature, timestamp, body)` confere os cabeçalhos `X-Signature-Ed25519` e `X-Signature-Timestamp` de uma requisição com a chave pública em hexadecimal do app; responda 401 quando ela retornar `False`. `decode_webhook_event(body)` retorna o evento tipado de uma requisição de Webhook Events, um `RawEvent` para um tipo que o bostie não modela, ou `None` para o PING. Para interações no seu próprio servidor, o [exemplo de interações por HTTP](../examples.md#http-interactions) mostra o caminho ASGI, que é o que o bostie suporta.
