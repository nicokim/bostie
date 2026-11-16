---
source: 16b527ff6747318f04713be223db608d76440776
description: "Monte mensagens do Discord com incorporações, arquivos, enquetes, respostas, encaminhamentos, Components V2, indicador de digitação e mensagens de voz."
---

# Conteúdo de mensagens

Uma mensagem é montada a partir das mesmas partes onde quer que você a envie: `ctx.respond`, `ctx.followup`, `ctx.edit` e `ctx.update`, `bot.rest.messages.create` e `edit`, e o `execute` de um webhook. Cada parte é um argumento nomeado, e uma parte que você deixa de fora não é enviada.

| Parte | Tipo | Observações |
|---|---|---|
| `content` | `str` | o primeiro argumento posicional de `respond`, `followup`, `messages.create` e `execute` |
| `embeds` | objetos `Embed` | até 10 incorporações |
| `components` | builders de `ui` | botões, menus de seleção e os componentes de layout; veja [Componentes](components.md) |
| `files` | objetos `File` | uploads; veja abaixo |
| `allowed_mentions` | `AllowedMentions` | quais menções podem notificar; veja [Menções permitidas](responses.md#allowed-mentions) |
| `flags` | `MessageFlags` | como `SUPPRESS_EMBEDS` e `SUPPRESS_NOTIFICATIONS` |
| `tts` | `bool` | lê o conteúdo em voz alta para quem estiver com o canal aberto |
| `poll` | `PollCreateRequest` | uma enquete; veja abaixo |
| `ephemeral` | `bool` | só em respostas a interações |

`bot.rest.messages.create` também aceita `reply_to`, `message_reference`, `sticker_ids`, `nonce`, `enforce_nonce` e `shared_client_theme`, e `delete_after`, como as [respostas a interações](responses.md#messages-that-delete-themselves).

## Incorporações e arquivos { #embeds-and-files }

```python
--8<-- "docs/snippets/responses/rich_messages.py"
```

`Embed` e as suas partes (`EmbedField`, `EmbedFooter`, `EmbedAuthor`, `EmbedMedia`) são modelos imutáveis com argumentos nomeados; um `dict` simples no formato JSON do Discord também funciona. `color` aceita um `int` como `0x5865F2`, e os helpers de [`bostie.color`](formatting.md#colors) montam um.

`File(name, data)` envia `data` com o nome `name`. `description` é o texto alternativo, `spoiler=True` o desfoca e `title` define o título que o Discord mostra. Uma incorporação ou um componente se refere a um upload como `attachment://name`, como o gráfico faz acima. O bostie não verifica tamanhos: o limite de upload do Discord depende dos impulsos do servidor e do Nitro do usuário.

## Enquetes { #polls }

`PollCreateRequest` aceita uma `question` e `answers`, ambos `PollMedia` com `text` e, nas respostas, um `emoji` opcional. `duration` é em horas, 24 quando omitido, e `allow_multiselect=True` permite que um usuário escolha várias respostas. `bot.rest.polls.end(channel_id, message_id)` encerra uma enquete antes da hora, `bot.rest.polls.list_answer_voters(...)` lista quem escolheu uma resposta, e os [eventos](events.md) `PollVoteAdd` e `PollVoteRemove` informam os votos conforme acontecem.

## Edição e anexos { #editing-and-attachments }

Uma edição aceita as partes por palavra-chave, além de `attachments`. Uma parte que você deixa de fora permanece, e `None` a limpa: `ctx.edit(embeds=None)` remove as incorporações e mantém o conteúdo. Quando uma edição passa `files` ou `attachments`, a mensagem mantém só os anexos listados em `attachments`, dados como objetos `Attachment` ou IDs, mais os arquivos novos.

## Components V2 { #components-v2 }

Uma mensagem cujos `components` incluem um componente de layout (`ui.text`, `ui.section`, `ui.container`, `ui.media_gallery`, `ui.file`, `ui.separator` ou `ui.thumbnail`) é uma mensagem Components V2: o bostie define a flag `IS_COMPONENTS_V2` e os componentes são a mensagem inteira. Uma mensagem assim não pode ter `content`, `embeds`, figurinhas nem enquete, e o bostie levanta `ValueError` quando você os passa; coloque o texto em `ui.text`. Botões e menus de seleção sozinhos em `ui.row` também funcionam em mensagens clássicas com conteúdo.

## Respostas { #replies }

```python
--8<-- "docs/snippets/responses/replies.py"
```

`reply_to` aceita o ID de uma mensagem no mesmo canal. Para encaminhar uma mensagem, passe `message_reference=MessageReference(type=MessageReferenceType.FORWARD, message_id=..., channel_id=...)` no lugar disso; os dois não podem ser combinados. Ler `message.content` em servidores exige a intent privilegiada Message Content, como o [guia do gateway](gateway.md#intents) explica.

## Indicador de digitação { #typing-indicator }

```python
--8<-- "docs/snippets/responses/typing_indicator.py"
```

`async with ctx.typing():` mostra o bot digitando no canal da interação enquanto o bloco roda. O bostie envia o indicador quando o bloco começa e de novo a cada 9 segundos, já que o Discord o mostra por 10, e para quando o bloco termina, inclusive por uma exceção. `await ctx.typing()` o mostra uma vez. Fora de uma interação, `bot.rest.channels.trigger_typing_indicator(channel_id)` faz o mesmo, com await ou em `async with`. Uma mensagem que o bot envia no canal limpa o indicador na hora.

## Mensagens de voz { #voice-messages }

```python
--8<-- "docs/snippets/responses/voice_memo.py"
```

`await voice.message(audio)` lê um caminho de arquivo ou bytes, mede a duração, calcula a forma de onda que o Discord desenha e retorna um `File` pronto para enviar. A extensão do nome indica o formato; os clientes do Discord gravam Ogg Opus mono a 48 kHz. Uma mensagem de voz é esse arquivo e nada mais: sem conteúdo, incorporações, componentes, enquete ou figurinhas. O bostie define a flag `IS_VOICE_MESSAGE` e levanta `ValueError` se houver qualquer outra coisa na mensagem. Um `File` que você mesmo monta vira uma mensagem de voz quando tem `duration_secs` e `waveform`, as amostras de 8 bits que o Discord desenha.
