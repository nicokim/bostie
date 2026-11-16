---
source: b1ea93c8ecd81e5df88e6e3728f48038a3213951
description: "Responda a interações do Discord: responder, adiar, editar, acompanhar, mensagens efêmeras, adiamento automático, limites de 3 s e 15 min e menções permitidas."
---

# Respondendo a interações

Todo comando de barra, comando de menu de contexto, botão, menu de seleção e envio de modal é uma interação, e o Discord espera o app respondê-la. Um handler de comando recebe um `Context`, um handler de componente um `ComponentContext` e um handler de modal um `ModalContext`; eles compartilham os métodos de resposta abaixo.

## O que um contexto sabe { #what-a-context-knows }

| Atributo | Valor |
|---|---|
| `ctx.user` | o `User` que a disparou, em um servidor ou em uma mensagem direta |
| `ctx.member` | o `Member` dele em um servidor; caso contrário, `None` |
| `ctx.guild_id`, `ctx.channel_id` | onde aconteceu; `guild_id` é `None` em mensagens diretas |
| `ctx.channel` | o `PartialChannel` que a interação traz |
| `ctx.locale` | o idioma do cliente do usuário |
| `ctx.interaction` | o modelo completo da interação, para todo o resto, como `app_permissions` ou `guild_locale` |
| `ctx.bot` | o bot |
| `ctx.voice` | a [voz](../voice/index.md) para este servidor e usuário |
| `ctx.command_name` | em `Context`, o comando com os seus subcomandos, como `"roles color set"` |

## Responder, editar, acompanhar { #answer-edit-follow-up }

```python
--8<-- "docs/snippets/responses/answer_flow.py"
```

`ctx.respond(...)` envia a primeira resposta. Chamado de novo, ele envia uma mensagem de acompanhamento, então um handler pode chamá-lo quantas vezes precisar. Ele retorna a mensagem que o Discord criou, um `WebhookMessage`.

- `ctx.edit(**parts)` edita a primeira resposta, e `ctx.edit(message_id, **parts)` uma mensagem de acompanhamento.
- `ctx.followup(...)` sempre envia uma nova mensagem, exceto logo depois de `defer()`, quando ele preenche a resposta adiada.
- `ctx.fetch()` e `ctx.fetch(message_id)` retornam a resposta ou uma mensagem de acompanhamento como o Discord as tem agora.
- `ctx.delete()` e `ctx.delete(message_id)` as excluem.

`respond` e `followup` aceitam `content` e as partes listadas em [Conteúdo de mensagens](messages.md): `embeds`, `components`, `files`, `allowed_mentions`, `flags`, `tts`, `poll`, além de `ephemeral`. Uma edição aceita as mesmas partes por palavra-chave: uma parte que você deixa de fora fica como está, e `None` a limpa.

## Mensagens que se excluem sozinhas { #messages-that-delete-themselves }

```python
--8<-- "docs/snippets/responses/short_lived.py"
```

`delete_after=` em `respond`, `followup` e `bot.rest.messages.create` exclui a mensagem enviada depois de tantos segundos. O bot executa a exclusão em segundo plano e a cancela quando fecha, então uma mensagem cujo prazo vence depois que o bot para continua lá. As mensagens de uma interação são excluídas pelo token dela, o que funciona também para as efêmeras, mas termina 15 minutos depois da interação; uma exclusão com prazo posterior falha com um aviso no log. Uma mensagem que alguém já excluiu é ignorada em silêncio.

## Respostas efêmeras { #ephemeral-answers }

`ephemeral=True` em uma chamada torna aquela mensagem visível só para o usuário. `ephemeral=True` em um comando, uma rota de componente, uma rota de modal ou um router torna isso o padrão para as respostas deles, e uma chamada ainda pode passar `ephemeral=False`. Uma mensagem existente não pode mudar entre efêmera e pública: uma edição que pede isso escreve um aviso no log uma vez e mantém a mensagem como está.

## Adiando a resposta { #deferring }

A primeira resposta precisa chegar ao Discord em até 3 segundos. `await ctx.defer()` mostra "pensando…" no lugar dela, e o handler passa a ter 15 minutos para chamar `respond`; `defer(ephemeral=True)` torna efêmera a resposta que virá.

```python
--8<-- "docs/snippets/responses/deferring.py"
```

O bostie também adia sozinho: um uso ainda sem resposta `auto_defer` segundos depois de chegar tem a resposta adiada, 2,5 segundos por padrão. Um componente ou um modal aberto a partir de uma mensagem é adiado com `defer_update`, que mantém a mensagem como está até que `update` a edite. `auto_defer` é definido no bot, em um router, em um comando ou em uma rota, e `None` o desativa ali. O cronômetro conta a partir do momento em que o gateway entregou a interação, então um event loop bloqueado atrasa o handler, mas não o prazo; o bostie escreve um aviso no log quando um handler começa com mais de um segundo de atraso.

## Modais e Atividades { #modals-and-activities }

`await ctx.modal(ui.modal(...))` responde com um formulário em janela; veja [Modais](modals.md). `await ctx.launch_activity()` abre a Atividade do app e retorna o ID da instância dela. Os dois precisam ser a primeira resposta, e nada pode responder à interação depois de um modal: o envio do modal é uma nova interação.

## As regras, e os erros por quebrá-las { #the-rules-and-the-errors-for-breaking-them }

Cada chamada verifica primeiro o estado da interação:

| Chamada | Antes de qualquer resposta | Depois de `defer()` | Depois de `defer_update()` | Depois de uma resposta |
|---|---|---|---|---|
| `respond` | responde | preenche a resposta | envia uma mensagem de acompanhamento | envia uma mensagem de acompanhamento |
| `defer` | adia | nada | nada | `InteractionResponded` |
| `edit`, `delete` | `InteractionNotAnswered` | edita, exclui a resposta | edita, exclui a mensagem | edita, exclui a resposta |
| `followup` | `InteractionNotAnswered` | preenche a resposta | envia uma mensagem de acompanhamento | envia uma mensagem de acompanhamento |
| `update` | edita a mensagem | `InteractionResponded` | edita a mensagem | `InteractionResponded` |
| `modal`, `launch_activity` | responde | `InteractionResponded` | `InteractionResponded` | `InteractionResponded` |

`update` e `defer_update` só existem nos contextos de componente e de modal. Toda chamada feita depois de um modal levanta `InteractionResponded`.

Um token de interação vive 15 minutos. Qualquer chamada depois disso, ou depois que o Discord disse que o token é inválido, levanta `InteractionExpired` sem enviar nada.

Um handler que retorna sem responder deixa o usuário com "O aplicativo não respondeu", e o bostie escreve no log um aviso que nomeia o handler. Um que adiou e nunca respondeu deixa "pensando…" até o token expirar, também com um aviso. Quando um handler levanta uma exceção antes de responder, o bostie responde "Something went wrong.", de forma efêmera a menos que o handler tenha adiado, a não ser que um [hook de erro](checks.md#error-hooks) responda.

O Discord permite 5 mensagens de acompanhamento para uma interação de um app instalado só no usuário, e o bostie avisa na sexta.

## Menções permitidas { #allowed-mentions }

Por padrão, uma resposta pode mencionar usuários e cargos, mas não `@everyone` nem `@here`: `Bot(allowed_mentions=...)` é `AllowedMentions(parse=("users", "roles"))`. Um `allowed_mentions` passado com uma mensagem muda só os tipos de menção que ele define, então `AllowedMentions(users=(123,))` limita as menções a usuários e deixa a configuração de cargos como está. `AllowedMentions(parse=())` silencia todas as menções, e `replied_user=False` impede que uma resposta mencione o autor da mensagem à qual ela responde.
