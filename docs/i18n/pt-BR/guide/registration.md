---
source: 9aa2f3cd3cfe588188668066e047191f9b207226
---

# Registrando comandos

O Discord só mostra um comando depois que o app o registrou. O bostie faz isso por você: quando o bot inicia, ele compara os comandos que você definiu com os que o Discord tem e só os envia se algo for diferente.

## A sincronização na inicialização { #the-sync-at-startup }

`bot.start()` e `bot.run()` sincronizam antes de o gateway conectar. Para cada escopo, global ou um servidor, o bostie busca os comandos registrados com as suas traduções, compara-os campo a campo e substitui os comandos do escopo em uma única requisição quando eles diferem. O log diz `commands up to date (global)` ou `commands synced (global): added [...], removed [...], changed [...]`.

Um comando que o Discord tem mas que você não define mais é removido, a menos que o tipo dele seja um que você nunca define: um bot sem `@bot.entry_point` mantém o comando de ponto de entrada que o Discord criou para a Atividade dele.

`Bot(auto_sync=False)` pula a sincronização. `await bot.sync_commands()` substitui agora os comandos de todos os escopos, tenham mudado ou não, e retorna um `SyncResult` por escopo com os nomes que ele adicionou (`added`), removeu (`removed`) e alterou (`changed`), e os `ids` que o Discord deu aos comandos de barra.

## Escopos { #scopes }

Um comando sem `guilds` é global: ele funciona em todo servidor que instalou o app e, conforme os seus `contexts` permitirem, em mensagens diretas. `guilds=[...]` o registra só nesses servidores.

Enquanto você trabalha em um bot, registre tudo em um servidor de teste:

```python
--8<-- "docs/snippets/commands/command_sync.py"
```

`Bot(dev_guilds=[...])` registra nesses servidores, em vez de globalmente, todo comando que não tem `guilds` próprio. O comando de ponto de entrada é sempre global, então a sincronização o deixa de fora e avisa.

### Um nome em vários escopos { #one-name-in-several-scopes }

```python
--8<-- "docs/snippets/commands/guild_variants.py"
```

Comandos podem compartilhar um nome quando nenhum servidor tem dois deles: um `/rules` global e um `/rules` para um servidor, ou um `/rules` por servidor. Cada um é registrado no seu próprio escopo, e uma interação roda o comando registrado no servidor dela; se não houver, o global. Nesse servidor, o Discord lista tanto o comando global quanto o do servidor. Com `dev_guilds`, o comando próprio de um servidor toma o lugar do global nesse servidor. Dois comandos com o mesmo nome cujos servidores se sobrepõem, ou dois globais, levantam `CommandDefinitionError` quando o segundo é registrado. Comandos de usuário e de mensagem seguem as mesmas regras.

A sincronização só mexe em escopos que têm comandos. Quando você remove `dev_guilds`, os comandos continuam no servidor de teste ao lado dos novos globais; `await bot.rest.commands.bulk_overwrite_guild(bot.application_id, guild_id, [])` os apaga.

## Onde os comandos podem ser usados { #where-commands-can-be-used }

`contexts` e `integration_types` valem para comandos globais. Defina-os em um comando, em um router ou no bot:

- `contexts`: `InteractionContextType.GUILD`, `BOT_DM` (as mensagens diretas do bot) e `PRIVATE_CHANNEL` (outras mensagens diretas e mensagens diretas em grupo, para um app instalado no usuário).
- `integration_types`: `ApplicationIntegrationType.GUILD_INSTALL` e `USER_INSTALL`.

`Bot(contexts=[...], integration_types=[...])` é o padrão para todo comando global que não define os seus. Se não forem definidos em lugar nenhum, o bostie não envia nenhum dos dois campos e o Discord aplica os seus padrões; para `integration_types`, isso são os contextos de instalação configurados para o app. Remover uma configuração que você tinha definido conta como mudança, então a próxima sincronização devolve ao comando os padrões do Discord.

## Menções a comandos { #command-mentions }

`await bot.command_mention("roles color set")` retorna `</roles color set:ID>`, que o Discord mostra como um comando clicável. Ele usa os IDs da última sincronização, ou pergunta ao Discord uma vez por escopo. Com `guild_id=`, um comando registrado nesse servidor tem prioridade sobre um global de mesmo nome. Um nome desconhecido levanta `LookupError`, e chamá-lo antes de o bot iniciar levanta `StateError`. `fmt.command(name, command_id)` monta o mesmo texto a partir de um ID que você já tem.

## Traduções { #translations }

`name_localizations` e `description_localizations` em um comando, um grupo, um subcomando, uma `Option` ou um `Choice` traduzem um texto. Uma função de tradução traduz todos eles:

```python
--8<-- "docs/snippets/commands/command_translations.py"
```

`Bot(translate=function)` chama `function(text, locale)` durante a sincronização para cada nome e descrição de cada comando, grupo, subcomando e opção, e para cada nome de escolha, uma vez por `Locale`. Um retorno `None`, ou o texto sem mudança, não adiciona nada para esse idioma, e uma tradução que você mesmo escreveu tem prioridade sobre a função. O handler vê o idioma do usuário em `ctx.locale` e o do servidor em `ctx.interaction.guild_locale`.

## Quando o Discord recusa os comandos { #when-discord-refuses-the-commands }

Uma sincronização que o Discord rejeita com 400 levanta `CommandSyncError`. A mensagem dela tem uma linha por problema, nomeando o comando, o caminho da opção e a mensagem do Discord, e `error.problems` os guarda como objetos `SyncProblem`. A maioria dos problemas é pega antes: os decoradores verificam nomes, descrições e limites quando rodam.
