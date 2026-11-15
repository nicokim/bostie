---
source: f4afb2ae41854fb4e97a63590ca4fa1760172438
---

# Formatação, cores e URLs da CDN

Três módulos pequenos montam o texto e as URLs que o Discord renderiza: `bostie.fmt` para markdown, menções e timestamps, `bostie.color` para cores e `bostie.cdn` para URLs de imagens.

```python
--8<-- "docs/snippets/formatting/profile_card.py"
```

## Menções, timestamps e links { #mentions-timestamps-and-links }

| Função | Retorna | O Discord mostra |
|---|---|---|
| `fmt.user(user_id)` | `<@id>` | o usuário, e o notifica |
| `fmt.role(role_id)` | `<@&id>` | o cargo, e o notifica |
| `fmt.channel(channel_id)` | `<#id>` | um link para o canal |
| `fmt.command(name, command_id)` | `</name:id>` | um comando clicável; `name` pode conter um grupo e um subcomando |
| `fmt.emoji(name, emoji_id, animated=False)` | `<:name:id>` | um emoji personalizado |
| `fmt.game_profile(game_id)` | `<@$id>` | um perfil de jogo |
| `fmt.timestamp(when, style=None)` | `<t:seconds:style>` | um horário no fuso horário e no idioma de cada leitor |
| `fmt.guild_navigation(kind, role_id=None)` | `<id:kind>` | um link para uma aba do servidor: `"customize"`, `"browse"`, `"guide"` ou `"linked-roles"`; só `"linked-roles"` aceita um cargo |
| `fmt.store(application_id, sku_id=None)` | uma URL da loja | a página do app na loja, ou um SKU, como um cartão |

`fmt.timestamp` aceita um `datetime` ou segundos Unix. Os estilos são os do Discord: `"t"` 16:20, `"T"` 16:20:30, `"d"` 20/04/2021, `"D"` 20 de abril de 2021, `"f"` 20 de abril de 2021 às 16:20 (o padrão do Discord quando nenhum estilo é dado), `"F"` o mesmo com o dia da semana, `"s"` 20/04/2021, 16:20, `"S"` com segundos, e `"R"` relativo, como "em 5 minutos". `user.mention`, `role.mention` e `channel.mention` nos modelos dão o mesmo texto que as funções de menção, e `bot.command_mention` busca o ID de um comando para você ([Registrando comandos](registration.md#command-mentions)).

## Texto vindo de usuários { #text-from-users }

- `fmt.escape_markdown(text)` faz o markdown aparecer como foi digitado, mantendo os links funcionando.
- `fmt.remove_markdown(text)` o remove.
- `fmt.escape_mentions(text)` quebra `@everyone`, `@here` e as menções a usuários e cargos para que não possam notificar, inserindo um espaço de largura zero.
- `fmt.mentioned_users(text)`, `fmt.mentioned_roles(text)` e `fmt.mentioned_channels(text)` retornam os IDs mencionados em um texto.

O escape é uma camada; [`allowed_mentions`](responses.md#allowed-mentions) decide o que pode notificar, diga o texto o que disser.

## Cores { #colors }

`bostie.color` tem as cores da marca do Discord como `int`: `BLURPLE`, `GREEN`, `YELLOW`, `FUCHSIA` e `RED`. `color.rgb(red, green, blue)` aceita três valores de 0 a 255, `color.hsv(hue, saturation, value)` três de 0 a 1, e `color.parse("#5865F2")` lê seis dígitos hexadecimais, com `#`, `0x` ou nenhum dos dois. O `color` das incorporações, o `accent_color` dos contêineres e os cargos aceitam esses valores.

## URLs da CDN { #cdn-urls }

`bostie.cdn` monta as URLs das imagens que o Discord guarda. Cada função recebe os IDs e o hash da imagem de um modelo, além de `size`, uma potência de dois de 16 a 4096, e `format`, um entre `"png"`, `"jpg"`, `"jpeg"`, `"webp"` (o padrão) ou `"gif"`. Uma imagem animada, cujo hash começa com `a_`, continua animada em WebP ou GIF; `"gif"` para uma imagem estática, ou um tamanho fora da lista, levanta `ValueError`. `await bot.http.read(url)` baixa uma delas, como o [guia de REST](rest.md#downloading-files) explica.

| Função | Imagem |
|---|---|
| `display_avatar(user)` | o avatar do usuário, ou o avatar padrão quando ele não tem um |
| `user_avatar(user_id, avatar)`, `default_avatar(user_id)` | o avatar próprio ou padrão de um usuário |
| `user_banner(user_id, banner)`, `avatar_decoration(asset)` | o banner e a decoração de avatar de um usuário |
| `member_avatar(guild_id, user_id, avatar)`, `member_banner(...)` | o avatar e o banner de um membro específicos de um servidor |
| `guild_icon`, `guild_splash`, `guild_discovery_splash`, `guild_banner` | as imagens de um servidor, a partir de `guild_id` e do hash |
| `guild_tag_badge(guild_id, badge)` | o emblema de uma tag de servidor |
| `role_icon(role_id, icon)` | o ícone de um cargo |
| `emoji(emoji_id, animated=False)` | um emoji personalizado |
| `sticker(sticker_id, format_type)` | uma figurinha como PNG, Lottie JSON ou GIF, de acordo com o tipo de formato dela |
| `sticker_pack_banner(banner_asset_id)` | o banner de um pacote de figurinhas |
| `scheduled_event_cover(event_id, image)` | a capa de um evento agendado |
| `application_icon`, `application_cover`, `application_asset`, `store_page_asset` | o ícone, a capa, os assets de Rich Presence e as imagens da loja de um app |
| `team_icon(team_id, icon)` | o ícone de uma equipe |
| `soundboard_sound(sound_id)` | o áudio de um som do painel de sons |
