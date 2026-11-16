---
source: 542ddb2b37fd206f8480755434912ca6bbefa224
description: "Abra modais do Discord com campos de texto, menus de seleção, arquivos, grupos de escolhas e caixas de seleção, leia os valores e espere o envio em um handler."
---

# Modais

![Bostie preenchendo um formulário e enviando](../assets/mascot/bostie-modals.webp){ align=right width="180" loading=lazy }

Um modal é um formulário em janela. Um comando ou um componente responde com um, e o envio do usuário chega como uma nova interação, que uma rota de modal trata.

```python
--8<-- "docs/snippets/modals/application_form.py"
```

`ui.modal(custom_id, title, *components)` aceita um título de 1 a 45 caracteres e de um a cinco componentes: campos `ui.label(...)` e parágrafos `ui.text(...)`. `await ctx.modal(...)` o abre; ele precisa ser a primeira resposta da interação, e nada mais pode responder a essa interação depois.

`@bot.on_modal(template)` roteia o envio como [`on_component`](components.md#routes) roteia cliques: os mesmos modelos com marcadores `{name}` e `{name:int}`, o mesmo `route.id(...)`, as mesmas configurações `ephemeral`, `auto_defer` e `checks`.

## Campos { #fields }

`ui.label(label, component, description=None)` coloca um rótulo de até 45 caracteres e uma descrição de até 100 acima de uma entrada:

| Entrada | Builder | Leia com |
|---|---|---|
| uma caixa de texto | `ui.text_input(custom_id, style=TextInputStyle.SHORT)` | `ctx.text(custom_id)`, uma `str` |
| um menu de seleção de opções | `ui.string_select(custom_id, options)` | `ctx.values(custom_id)` |
| um menu de seleção de usuários, cargos, ambos, ou canais | `ui.user_select`, `ui.role_select`, `ui.mentionable_select`, `ui.channel_select` | `ctx.users`, `ctx.members`, `ctx.roles`, `ctx.channels` ou `ctx.ids` |
| uploads de arquivo | `ui.file_upload(custom_id)` | `ctx.attachments(custom_id)`, os objetos `Attachment` enviados |
| uma escolha entre 2 e 10 | `ui.radio_group(custom_id, options)` | `ctx.choice(custom_id)`, o valor ou `None` |
| várias escolhas entre 1 e 10 | `ui.checkbox_group(custom_id, options)` | `ctx.values(custom_id)` |
| uma caixa de sim ou não | `ui.checkbox(custom_id, default=False)` | `ctx.checked(custom_id)`, um `bool` |

Toda entrada, exceto a caixa de seleção, aceita `required`, `True` por padrão, e `min_values=0` exige `required=False`. `text_input` aceita `style=TextInputStyle.PARAGRAPH` para várias linhas, `placeholder`, `value` para preenchê-la de antemão, `min_length` (de 0 a 4000) e `max_length` (de 1 a 4000). `file_upload` aceita `min_values` e `max_values`, até 10 arquivos, e `file_types` como `["image", ".pdf"]`. As opções de botões de opção e de caixas de seleção são `ui.group_option(label, value, description=None, default=False)`; um grupo de caixas de seleção sem `max_values` deixa o usuário marcar todas.

Os builders retornam `ui.ModalDict`, `ui.LabelDict`, `ui.TextInputDict`, `ui.FileUploadDict`, `ui.RadioGroupDict`, `ui.CheckboxGroupDict`, `ui.CheckboxDict` e `ui.GroupOptionDict`. Os leitores de `ModalContext` recebem o custom id da entrada. Um ID que o modal não tem levanta `KeyError`, e ler uma entrada de outro tipo, como `ctx.text` em uma caixa de seleção, levanta `TypeError`.

## Atualizando a mensagem que abriu o modal { #updating-the-message-that-opened-the-modal }

Um modal aberto a partir de um componente conhece a mensagem em que o componente está, e o envio dele pode editar essa mensagem:

```python
--8<-- "docs/snippets/modals/settings_modal.py"
```

`await ctx.update(...)` e `await ctx.defer_update()` funcionam no envio como em um clique. Em um modal aberto a partir de um comando não há mensagem para atualizar, e eles levantam `InteractionResponded`; responda com `respond` no lugar disso. Um envio sem resposta é adiado depois de `auto_defer` segundos, com `defer_update` quando há uma mensagem.

## Esperando o envio dentro de um handler { #waiting-for-the-submit-inside-a-handler }

```python
--8<-- "docs/snippets/modals/inline_modal.py"
```

`await bot.wait_for_modal(*custom_ids, check=None)` retorna o `ModalContext` do próximo envio cujo custom id é um de `custom_ids`, ou de qualquer modal quando nenhum é dado, e nenhuma rota roda para ele. Coloque algo único no custom id, como o ID da interação, para que dois usuários que rodam o comando ao mesmo tempo recebam cada um o seu próprio envio. Um usuário pode fechar um modal sem enviá-lo, e o Discord não avisa nada ao bot, então limite a espera com `asyncio.timeout()`.
