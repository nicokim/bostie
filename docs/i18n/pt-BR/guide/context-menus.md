---
source: a9ef813b389718cd5d31ae958953b39416d1a494
---

# Comandos de usuário, de mensagem e de ponto de entrada

Além dos comandos de barra, o Discord tem comandos que rodam a partir do menu de contexto de um usuário ou de uma mensagem, e o comando de ponto de entrada, que abre a Atividade de um app.

## Comandos de usuário e de mensagem { #user-and-message-commands }

```python
--8<-- "docs/snippets/commands/context_menus.py"
```

`@bot.user_command(name)` e `@bot.message_command(name)` registram uma função que recebe o contexto e o alvo. O nome é o que o menu mostra: de 1 a 32 caracteres e, ao contrário dos nomes de comandos de barra, ele pode ter letras maiúsculas e espaços. Esses comandos não têm descrição nem opções.

A anotação de tipo do alvo diz o que o handler recebe:

| Comando | Anotação | O handler recebe |
|---|---|---|
| usuário | `User` | o usuário |
| usuário | `ResolvedMember` | o membro, com o seu `user`; só em servidores |
| usuário | `User \| ResolvedMember` | o membro em um servidor, o usuário nos outros lugares |
| mensagem | `Message` | uma mensagem que um usuário ou bot enviou |
| mensagem | `WebhookMessage` | uma mensagem que um webhook enviou, incluindo respostas a interações |
| mensagem | `Message \| WebhookMessage` | qualquer uma das duas |

Um alvo que não se resolve no tipo anotado levanta `OptionConversionError`, que vai para os [hooks de erro](checks.md#error-hooks). Anote um alvo de mensagem como `Message | WebhookMessage`, a menos que o comando só faça sentido para um dos dois.

Eles aceitam as configurações de um comando de barra, exceto `description`, `description_localizations` e as configurações de opção: `name_localizations`, `ephemeral`, `auto_defer`, `default_permissions`, `nsfw`, `guilds`, `contexts`, `integration_types`, `checks` e `extra`. O Discord permite 15 comandos de usuário e 15 de mensagem por escopo, e a sincronização avisa acima disso.

## O comando de ponto de entrada { #the-entry-point-command }

Um app com Atividades tem um comando de ponto de entrada, que o App Launcher mostra. `@bot.entry_point(name)` o registra com uma função que recebe só o contexto:

```python
--8<-- "docs/snippets/commands/entry_point.py"
```

O handler responde como qualquer comando: `ctx.launch_activity()` abre a Atividade e retorna o ID da instância dela, e `ctx.respond(...)` responde com uma mensagem no lugar disso. O Discord recusa o comando vindo de um app sem Atividades habilitadas no Developer Portal.

O ponto de entrada é sempre global. Com `dev_guilds` definido, a sincronização o deixa de fora e avisa. Quando o Discord já tem o seu ponto de entrada padrão com outro nome, a sincronização mantém o ID desse comando, para que o Discord o substitua em vez de manter os dois. `launch_activity()` também existe nos contextos de componente e de modal, mas precisa ser a primeira resposta.

`entry_point` aceita `description`, `name_localizations`, `description_localizations`, `ephemeral`, `auto_defer`, `default_permissions`, `nsfw`, `contexts`, `integration_types`, `checks` e `extra`. [Atividades, monetização e o Social SDK](platform.md#activities) cobre o resto do lado da Atividade.
