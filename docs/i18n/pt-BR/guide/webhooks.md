---
source: e4d65a29086e1f3e4e82a64b8aa6c42d29627ef0
---

# Webhooks

Um webhook publica mensagens em um canal com um nome e um avatar próprios. A URL dele guarda um ID e um token, e o token basta para publicar, então um script de deploy ou uma rotina de monitoramento pode enviar mensagens sem um bot.

```python
--8<-- "docs/snippets/webhooks/deploy_notice.py"
```

`WebhookClient(url)` extrai o ID e o token de uma URL de webhook e levanta `ValueError` para qualquer outra coisa. Use-o dentro de `async with`, que abre e fecha o cliente HTTP dele. `SyncWebhookClient(url)` tem os mesmos métodos para código sem event loop; cada chamada roda o seu próprio.

| Método | Faz |
|---|---|
| `execute(content, **parts)` | publica uma mensagem e a retorna assim que o Discord a salvou |
| `edit_message(message_id, **parts)` | edita uma mensagem que o webhook enviou; uma parte omitida permanece, `None` a limpa |
| `fetch_message(message_id)`, `delete_message(message_id)` | buscam ou excluem uma mensagem |
| `fetch()`, `edit(name=..., avatar=...)`, `delete()` | o próprio webhook |
| `execute_slack(payload)`, `execute_github(payload)` | publicam um payload nos formatos que os endpoints compatíveis com Slack e com GitHub do Discord aceitam |

`execute` aceita as [partes da mensagem](messages.md) e também:

- `username` e `avatar_url`, mostrados no lugar dos do próprio webhook nesta mensagem;
- `thread_id`, para publicar em um tópico do canal; os métodos de mensagem também o aceitam;
- `thread_name`, para iniciar um tópico em um canal de fórum ou de mídia, com `applied_tags`;
- `with_components=True`, necessário para componentes em um webhook que nenhum app possui.

Os clientes aceitam `api_url`, `http_settings` e `allowed_mentions` como o bot; por padrão, um webhook pode mencionar usuários e cargos, mas não `@everyone`.

Quando o Discord responde 404 Unknown Webhook, o webhook foi excluído. O bostie então responde a todas as requisições seguintes para ele com o mesmo 404 sem enviá-las, para que um webhook morto não conte para o [limite de requisições inválidas](rest.md#rate-limits-retries-and-timeouts).

## Gerenciando webhooks com um bot { #managing-webhooks-with-a-bot }

`bot.rest.webhooks` cria e lista os webhooks de um canal ou de um servidor, o que precisa da permissão Gerenciar webhooks, e também tem os métodos com token:

- `create(channel_id, name=..., avatar=File(...))` retorna um `Webhook` cuja `url` você pode passar a um `WebhookClient`.
- `list(channel_id)`, `list_guild(guild_id)`, `fetch(webhook_id)`, `edit(webhook_id, ...)` e `delete(webhook_id)` funcionam com o token do bot.
- `execute(webhook_id, token, ...)` e os outros métodos `_with_token` e de mensagem são o que o `WebhookClient` chama.

O evento `WebhooksUpdate` diz que os webhooks de um canal mudaram. Uma mensagem que um webhook enviou chega como uma `WebhookMessage`, cujo `author` é um `PartialUser` e cujo `webhook_id` está definido.
