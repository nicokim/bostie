---
source: 8bfaf791038898b7b25fb584ff4496c345a14fcb
---

# Modelos e enums

Tudo o que o bostie decodifica do Discord é um modelo: `Message`, `Guild`, `Member`, `Interaction` e cerca de duzentos outros, todos importáveis de `bostie`. Os modelos são structs `msgspec` imutáveis, decodificados direto dos bytes do JSON.

```python
--8<-- "docs/snippets/models/inspect_message.py"
```

## Os tipos seguem a documentação do Discord { #types-follow-discords-documentation }

Cada campo é tipado como a documentação do Discord o descreve:

- Um campo que o Discord sempre envia não tem valor padrão e nunca falta. Se a documentação diz que ele pode ser `null`, ele é `T | None`.
- Um campo que a documentação marca como opcional tem um valor padrão: `()` para uma lista, um conjunto de flags vazio para flags, `False` para um booleano cuja ausência significa falso e `None` nos outros casos.
- IDs são `int`, timestamps são objetos `datetime` com fuso horário em UTC e cores são `int`.
- Onde o Discord envia um objeto parcial, o campo tem um modelo parcial: `PartialUser`, `PartialMember`, `PartialChannel`, `PartialMessage`, `PartialEmoji`, `PartialApplication`, `PartialIntegration`, `PartialEntitlement`, `UnavailableGuild`. Um modelo parcial só tem os campos que o payload parcial tem, então um campo ausente é um erro de tipo, e não um `None` em tempo de execução.

Três lugares em que um payload tem dois formatos ganham duas classes:

- Uma mensagem enviada por um webhook, incluindo respostas a interações, é uma `WebhookMessage`, cujo `author` é um `PartialUser`. Qualquer outra mensagem é uma `Message` com um autor `User` completo. Os campos que aceitam as duas são tipados como `Message | WebhookMessage`.
- Um membro resolvido a partir de uma opção, de um menu de seleção ou do alvo de um comando é um `ResolvedMember`, que traz o seu `user` e não tem `deaf` nem `mute`. Um `Member` vindo do gateway ou do REST tem os dois.
- Um usuário em `message.mentions` é um `MentionedUser`: um `User` com o `member` que o Discord adiciona quando a mensagem foi enviada em um servidor.

As interações são `CommandInteraction`, `ComponentInteraction`, `ModalSubmitInteraction` ou `AutocompleteInteraction` de acordo com o tipo delas, cada uma com `data` tipado.

## Resiliência a mudanças { #change-resilience }

O Discord adiciona campos, valores de enum e tipos de componente sem aviso, e um bot não pode quebrar quando isso acontece:

- Os campos que o bostie não conhece são ignorados na decodificação.
- Os enums mapeiam um valor desconhecido para um membro estável chamado `UNKNOWN_<value>`, que é igual ao valor numa comparação: `MessageType(99).name == "UNKNOWN_99"`.
- As flags mantêm os bits que o bostie não conhece, em todos os operadores, incluindo `~`.
- Um tipo de componente desconhecido é decodificado como `UnknownComponent`, que guarda o seu JSON bruto e é enviado de volta sem mudanças quando você o passa em `components`; um tipo de interação desconhecido é decodificado como `UnknownInteraction`, que chega aos listeners de `InteractionCreate`, mas a nenhum comando ou rota.
- Uma mensagem aninhada, como `message.referenced_message` ou a mensagem de uma interação de componente, só é decodificada quando você lê a propriedade, então uma mensagem quebrada só falha onde é lida.

Um payload de evento que não cabe de jeito nenhum na sua classe, por exemplo porque falta um campo obrigatório, vai para o log uma vez e é entregue só aos listeners de `RawEvent`, como [Eventos](events.md#raw-events-and-events-bostie-does-not-model) explica.

## Payloads brutos { #raw-payloads }

Um modelo decodificado a partir de um payload inteiro o guarda: `model.raw_json` guarda os bytes e `model.raw` os decodifica em um `dict` novo. Os modelos aninhados, como o autor de uma mensagem, têm `raw_json = None`. Todo evento também tem `raw` e `raw_json`. Eles alcançam qualquer campo que o Discord envia antes que o bostie o modele.

O `repr` de um modelo deixa de fora `raw_json` e os segredos: `token`, `access_token`, `refresh_token` e `id_token`.

## Montando e copiando modelos { #building-and-copying-models }

Os modelos são imutáveis e montados com argumentos nomeados. `msgspec.structs.replace(model, field=value)` retorna uma cópia com alguns campos alterados. Os modelos que você envia ao Discord, como `Embed`, `AllowedMentions`, `PollCreateRequest`, `MessageReference`, `ForumTag`, `RecurrenceRule` e `AutoModerationAction`, deixam de fora os campos que você não definiu, então um modelo decodificado é enviado de volta do jeito que chegou.

## Helpers nos modelos { #helpers-on-models }

| Helper | Valor |
|---|---|
| `user.mention`, `member.mention`, `role.mention`, `channel.mention` | o texto da menção, como `<@123>` |
| `user.display_name`, `member.display_name` | o nome global ou o nome de usuário; para um membro, o apelido primeiro |
| `message.jump_url` | o link para a mensagem |
| `message.author_member` | o autor como um `ResolvedMember`, quando a mensagem traz o membro |
| `role.permissions`, `overwrite.allow`, `overwrite.deny` | flags `Permissions` |
| `member.permissions` | as permissões de um membro no canal da interação, quando o Discord as enviou |
| `webhook.url` | a URL do webhook, quando ele tem um token |

O [guia de formatação](formatting.md) cobre menções, timestamps e URLs da CDN montados a partir de IDs.

## Snowflakes { #snowflakes }

Os IDs do Discord codificam quando o objeto foi criado. Todo modelo com um ID snowflake tem `created_at`, esse momento como um `datetime` em UTC: `message.created_at`, `member.created_at` (o da conta), `emoji.created_at` (`None` para um emoji Unicode). `bostie.snowflake_time(id)` faz o mesmo para um ID solto, e `bostie.time_snowflake(when)` retorna o menor ID criado em um momento ou, com `high=True`, o maior, para paginar por data. Um `datetime` sem fuso horário é lido como horário local.

## Enums { #enums }

`bostie` exporta os enums e as flags do Discord: `ChannelType`, `MessageType`, `Permissions`, `Intents`, `MessageFlags`, `Locale`, `ButtonStyle` e os outros da [referência de enums](../reference/api/enums.md). As flags se combinam com `|` e se testam com `in`: `Permissions.SEND_MESSAGES in member_permissions`. `message.type.deletable` diz se o Discord deixa excluir uma mensagem daquele tipo: os avisos de mensagem direta em grupo e de chamada, as mudanças de nome e de ícone do canal e a mensagem inicial de um tópico não podem ser excluídos. `JSONErrorCode` dá nome aos códigos de erro do Discord, como `HTTPException.code` os informa.
