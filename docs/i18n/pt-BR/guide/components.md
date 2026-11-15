---
source: d0cf09522c13a747c853db29f2b054fe520abbea
---

# Componentes

![Bostie apertando um botão grande](../assets/mascot/bostie-components.webp){ align=right width="180" loading=lazy }

Componentes são os botões, os menus de seleção e os blocos de layout de uma mensagem. `bostie.ui` os monta como dicionários simples no formato JSON do Discord e verifica os limites do Discord enquanto os monta. Um handler registrado com `@bot.on_component` responde aos usos deles.

`bostie.ui` é como você monta componentes. Os modelos de componente, como `bostie.Button` ou `bostie.ActionRow`, são o que os `components` de uma mensagem decodificada guardam; `components=` também os aceita, então os componentes de uma mensagem podem ser enviados de volta, editados com `msgspec.structs.replace`, sem serem montados de novo, mas eles não verificam os limites do Discord.

## Layout { #layout }

```python
--8<-- "docs/snippets/components/layout.py"
```

Estes são Components V2: os componentes formam a mensagem inteira, com `ui.text` no lugar de `content`, como [Conteúdo de mensagens](messages.md#components-v2) explica.

| Builder | O que mostra |
|---|---|
| `ui.text(markdown)` | texto, com o markdown do Discord |
| `ui.section(*texts, accessory=...)` | de um a três textos com um botão ou uma miniatura ao lado |
| `ui.thumbnail(url, description=None, spoiler=False)` | uma imagem pequena, só como acessório de uma seção |
| `ui.media_gallery(*items)` | de uma a dez imagens ou vídeos, cada um sendo um `ui.gallery_item(url, description=None, spoiler=False)` |
| `ui.file(filename, spoiler=False)` | um arquivo enviado com a mensagem sob esse nome |
| `ui.separator(divider=True, spacing=1)` | espaço entre componentes, `spacing=2` para um espaço grande, com uma linha a menos que `divider=False` |
| `ui.container(*components, accent_color=None, spoiler=False)` | uma caixa em volta de linhas, textos, seções, galerias, arquivos e separadores, com uma borda colorida |
| `ui.row(*components)` | até cinco botões, ou um menu de seleção |

Uma URL pode ser `attachment://name` para mostrar um arquivo enviado com a mensagem. Todo builder aceita `id=` para o ID numérico opcional do componente, e `ui.raw(dict)` passa sem mudanças um componente para o qual `ui` ainda não tem builder. Itens demais, um rótulo longo demais ou um menu de seleção dentro de uma linha com botões levantam `ValueError` quando você os monta. Cada builder retorna um dicionário tipado, como `ui.ButtonDict`, `ui.RowDict`, `ui.TextDict`, `ui.SectionDict`, `ui.ThumbnailDict`, `ui.MediaGalleryDict`, `ui.GalleryItemDict`, `ui.FileDict`, `ui.SeparatorDict`, `ui.ContainerDict`, `ui.StringSelectDict`, `ui.SelectOptionDict`, `ui.EntitySelectDict` e `ui.SelectDefaultDict`, e `ui.raw` retorna um `ui.RawComponent`; anote os seus próprios helpers com eles, como `ballot` faz abaixo.

## Botões { #buttons }

- `ui.button(label, custom_id=..., style=ButtonStyle.SECONDARY)` envia `custom_id` ao bot quando clicado. `style` é `PRIMARY`, `SECONDARY`, `SUCCESS` ou `DANGER`.
- `ui.link_button(label, url)` abre uma URL e nunca chega ao bot.
- `ui.premium_button(sku_id)` põe à venda um SKU do app e nunca chega ao bot.

Os botões aceitam `emoji=`: um emoji Unicode, um emoji personalizado escrito como o Discord o mostra (`<:name:id>`, ou `<a:name:id>` quando animado, como `fmt.emoji` o produz), ou um modelo `Emoji` ou `PartialEmoji`. `PartialEmoji.parse(text)` lê uma string assim. Os botões também aceitam `disabled=True`. Um rótulo tem no máximo 80 caracteres e um custom id de 1 a 100.

## Rotas { #routes }

Uma rota é um handler para todo custom id que se encaixa em um modelo:

```python
--8<-- "docs/snippets/components/routes.py"
```

`"vote:{side:int}:{topic}"` corresponde a `vote:0:Lunch at noon`. `{name}` captura um ou mais caracteres sem quebra de linha, e `{name:int}` um inteiro, incluindo negativos. Os valores chegam ao handler como argumentos nomeados, convertidos no caso de `int`. Os modelos precisam de texto literal entre dois marcadores, e um modelo sem marcadores corresponde exatamente ao seu custom id.

`vote.id(side=0, topic=topic)` monta o custom id a partir da própria rota, então o modelo é escrito uma única vez. Ele levanta `ValueError` se o ID passar dos 100 caracteres do Discord ou se não puder ser lido de volta com os mesmos valores.

O estado fica no custom id, não em um objeto Python, então um botão enviado na semana passada continua funcionando depois de um reinício, desde que a rota dele esteja registrada. `@bot.on_component` aceita `ephemeral`, `auto_defer` e `checks`, como um comando, e chamar a rota, como em `await vote(ctx, side=0, topic="x")`, chama o handler.

Um uso de componente que nenhuma rota reconhece não recebe resposta: o Discord mostra "Esta interação falhou" e o bostie escreve um aviso no log, a menos que você escute `InteractionCreate`.

## Respondendo a um componente { #answering-a-component }

Um `ComponentContext` tem os métodos de resposta do contexto de um comando, além de:

- `await ctx.update(**parts)` edita a mensagem em que o componente está. As partes omitidas permanecem, e `None` limpa uma delas.
- `await ctx.defer_update()` confirma o uso sem uma resposta visível; `update` pode editar a mensagem em até 15 minutos.
- `await ctx.modal(ui.modal(...))` abre um [modal](modals.md).

`respond` envia uma nova mensagem, como para um comando. Um uso que fica sem resposta por `auto_defer` segundos é adiado com `defer_update`.

## Menus de seleção { #select-menus }

```python
--8<-- "docs/snippets/components/selects.py"
```

| Builder | O usuário escolhe | Leia as escolhas com |
|---|---|---|
| `ui.string_select(custom_id, options)` | opções montadas com `ui.select_option(label, value)` | `ctx.values`, os valores |
| `ui.user_select(custom_id)` | usuários | `ctx.users`, e `ctx.members` em um servidor |
| `ui.role_select(custom_id)` | cargos | `ctx.roles` |
| `ui.mentionable_select(custom_id)` | usuários e cargos | `ctx.users`, `ctx.members`, `ctx.roles` |
| `ui.channel_select(custom_id, channel_types=...)` | canais, só dos tipos dados | `ctx.channels` |

`ctx.ids` guarda os IDs escolhidos de qualquer menu de seleção de entidades. Ler o tipo errado, como `ctx.values` em um menu de seleção de usuários, levanta `TypeError`.

Todo menu de seleção aceita `placeholder` (até 150 caracteres), `min_values` e `max_values` (1 por padrão, no máximo 25) e `disabled`. Um menu de seleção de strings tem de 1 a 25 opções; `select_option` aceita `description`, `emoji` como os botões, e `default=True` para mostrá-la como escolhida. Os menus de seleção de entidades aceitam `defaults`, os IDs mostrados como escolhidos, e o menu de seleção de mencionáveis aceita `default_users` e `default_roles`. `required` só importa em um [modal](modals.md).

## Páginas { #pages }

`ui.pager(route, page, count)` retorna uma linha de três botões para uma rota com um marcador `{page:int}`: anterior, um rótulo `page / count` desativado e próximo. A primeira página é a 0, e argumentos nomeados extras preenchem os outros marcadores da rota. Como o número da página está no custom id, a paginação sobrevive a reinícios. O comando `/tour` do [exemplo de componentes](../examples.md#components) a usa.

## Esperando um clique dentro de um handler { #waiting-for-a-click-inside-a-handler }

Um comando pode esperar um uso de um componente sem uma rota:

```python
--8<-- "docs/snippets/components/confirm.py"
```

`await bot.wait_for_component(*custom_ids, check=None)` retorna o `ComponentContext` do próximo uso cujo custom id é um de `custom_ids`, ou de qualquer componente quando nenhum é dado, e para o qual `check` retorna verdadeiro. Nenhuma rota roda para um uso que ele retorna. Ele espera para sempre, então limite-o com `asyncio.timeout()`; uma exceção levantada por `check` é levantada aqui. Ele aceita `ephemeral` e `auto_defer` para o contexto que retorna.
