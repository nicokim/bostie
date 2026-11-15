---
source: 7f5c86665191d303ab8ac2e965d43d553cee7953
---

# Comandos de barra

![Bostie escrevendo um comando de barra em um balão de fala](../assets/mascot/bostie-commands.webp){ align=right width="180" loading=lazy }

Um comando de barra é uma função assíncrona decorada com `@bot.command`. O primeiro parâmetro recebe o [`Context`](responses.md); cada parâmetro depois dele é uma opção do comando.

```python
--8<-- "docs/snippets/commands/option_types.py"
```

O comando é `/report`. O nome dele é o nome da função e a descrição é a primeira linha da docstring; `@bot.command(name=..., description=...)` os define no lugar disso, e um comando sem nenhum dos dois recebe `…` como descrição. `@bot.command` e `@bot.command(...)` funcionam os dois, no bot ou em um [router](routers.md).

## Tipos de opção { #option-types }

A anotação de tipo de um parâmetro escolhe o tipo da opção, e o handler recebe o valor já convertido:

| Anotação | Opção no Discord | O handler recebe |
|---|---|---|
| `str` | string | o texto |
| `int` | integer | um `int` |
| `float` | number | um `float`, mesmo quando o usuário digitou um número inteiro |
| `bool` | boolean | um `bool` |
| `User` | user | o `User` |
| `ResolvedMember` | user | o membro, com o seu `user`; só em servidores |
| `Role` | role | o `Role` |
| `PartialChannel` | channel | o canal como a interação o resolveu |
| `Attachment` | attachment | o `Attachment` do arquivo enviado |
| `User \| Role` ou `ResolvedMember \| Role` | mentionable | o que o usuário tiver escolhido |
| `Literal[...]` ou um `Enum` | string, integer ou number com escolhas | o literal ou o membro do enum |

Uma anotação que a tabela não lista levanta `CommandDefinitionError` quando o decorador roda. Para uma opção de canal a anotação é `PartialChannel`, não `Channel`, e para um membro é `ResolvedMember`, não `Member`: uma interação traz esses objetos parciais, e a mensagem de erro diz isso.

## Obrigatórias e opcionais { #required-and-optional }

Uma opção sem valor padrão é obrigatória. Um valor padrão, ou `| None` na anotação, a torna opcional; uma opção opcional que o usuário deixou de fora recebe o seu padrão, ou `None`. O Discord quer as opções obrigatórias primeiro, e o bostie as ordena assim quando monta o comando, seja qual for a ordem dos parâmetros.

## Descrições { #descriptions }

Uma string em `Annotated` é a descrição da opção: `Annotated[str, "What happened"]`. A docstring pode guardar as descrições no lugar disso:

```python
--8<-- "docs/snippets/commands/docstring_options.py"
```

O bostie lê uma seção `Args:`, como acima, e uma seção `Parameters` sublinhada com hifens; uma descrição pode ocupar várias linhas. Uma string em `Annotated` tem prioridade sobre `Option(description=)`, que tem prioridade sobre a docstring. Uma opção sem descrição em lugar nenhum recebe `…`.

## Intervalos e configurações de opção { #ranges-and-option-settings }

`Range[int, 1, 10]` é uma opção `int` de 1 a 10, inclusive. `Range` também aceita `float`, e para `str` os limites são o comprimento: `Range[str, 1, 2000]`. `Option(...)` em `Annotated` define todo o resto, e tem prioridade sobre o que a anotação implica:

```python
--8<-- "docs/snippets/commands/option_settings.py"
```

| Campo de `Option` | Vale para | Significado |
|---|---|---|
| `name` | todas | o nome da opção no Discord; o parâmetro mantém o seu nome em Python |
| `description` | todas | a descrição, quando `Annotated` não tem nenhuma string |
| `name_localizations`, `description_localizations` | todas | texto por idioma, como `{"fr": "salon"}` |
| `min`, `max` | integer, number | limites inclusivos, dentro de ±2⁵³ |
| `min_length`, `max_length` | string | limites de comprimento, de 0 a 6000 e de 1 a 6000 |
| `channel_types` | channel | os tipos de canal que o usuário pode escolher |
| `file_types` | attachment | até 10 entre `image`, `video`, `audio` ou extensões como `.pdf` |
| `choices` | string, integer, number | escolhas fixas; veja abaixo |
| `autocomplete` | string, integer, number | uma função que sugere valores enquanto o usuário digita |
| `extra` | todas | campos brutos mesclados no JSON da opção, para um campo que o bostie ainda não modela |

Um campo em um tipo de opção ao qual ele não se aplica levanta `CommandDefinitionError`.

## Escolhas { #choices }

Um `Literal` ou um `Enum` oferece uma lista fixa. Uma escolha de `Literal` recebe o nome do seu valor; uma escolha de `Enum` recebe o nome do membro e envia o valor do membro. `Option(choices=[...])` as renomeia ou traduz, desde que liste os mesmos valores na mesma ordem:

```python
--8<-- "docs/snippets/commands/option_choices.py"
```

O handler de `/order` recebe `"tea"`, `"coffee"` ou `"water"` para `drink`, um membro de `Size` para `size` e o valor do `Choice` escolhido para `milk`. Com uma anotação simples `str`, `int` ou `float`, só `Option(choices=[...])` define as escolhas; cada uma é um `Choice(name, value)` ou um valor solto, que recebe o próprio valor como nome. Os valores das escolhas precisam ter o tipo da opção, e os valores de um `Literal` ou de um `Enum` precisam ser todos `str`, todos `int` ou todos `float`. Uma opção tem no máximo 25 escolhas, o nome de uma escolha tem de 1 a 100 caracteres e um valor string no máximo 100.

## Preenchimento automático { #autocomplete }

`Option(autocomplete=function)` pede sugestões a `function` enquanto o usuário digita. Ela recebe um `AutocompleteContext` e o texto digitado até o momento, e retorna valores ou objetos `Choice`:

```python
--8<-- "docs/snippets/commands/autocomplete_options.py"
```

`ctx.options` guarda as opções preenchidas até agora, pelo nome da opção, convertidas como o handler as receberia quando a conversão dá certo; a opção sendo digitada fica como texto. O bostie envia as 25 primeiras sugestões, corta nomes com mais de 100 caracteres e descarta sugestões com nome vazio, valor do tipo errado ou valor string com mais de 100 caracteres, com um aviso para cada caso. Uma opção não pode ter `choices` e `autocomplete` ao mesmo tempo. Se a função levantar uma exceção, o erro vai para os [hooks de erro](checks.md#error-hooks) e o Discord recebe uma lista vazia.

## Conversores { #converters }

`Annotated[T, Convert(function)]` transforma o valor da opção em um `T` antes que o handler rode. `function(ctx, value)` pode ser assíncrona, e a anotação de tipo do seu parâmetro `value` decide o tipo da opção no Discord, incluindo `Range` e as descrições em `Annotated`:

```python
--8<-- "docs/snippets/commands/convert_options.py"
```

O conversor só roda para uma opção que o usuário informou; uma opção opcional deixada de fora recebe o seu padrão sem conversão. Uma exceção vinda do conversor vai para os hooks de erro como uma exceção vinda do handler.

## Subcomandos e grupos { #subcommands-and-groups }

`bot.group(name, description)` registra um comando feito de subcomandos, e `group.group(...)` adiciona dentro dele um grupo de subcomandos. Os grupos se aninham em um só nível, e um grupo tem no máximo 25 entradas:

```python
--8<-- "docs/snippets/commands/command_groups.py"
```

Isso define `/roles give` e `/roles color set`. `@group.command(name=...)` dá a um subcomando um nome diferente do da sua função. `ctx.command_name` é o nome completo que foi executado, como `"roles color set"`, e `command.qualified_name` é o mesmo para o objeto do comando.

Um grupo de nível superior aceita as configurações de um comando, exceto `name`, `ephemeral` e `auto_defer`, e elas valem para todos os seus subcomandos: `default_permissions`, `nsfw`, `guilds`, `contexts` e `integration_types` só existem em comandos de nível superior no Discord. Um grupo aninhado aceita as duas traduções, `checks` e `extra`. Um subcomando aceita `name`, `description`, as duas traduções, `ephemeral`, `auto_defer`, `checks` e `extra`. As verificações de um grupo rodam antes das dos seus subcomandos.

## Configurações do comando { #command-settings }

```python
--8<-- "docs/snippets/commands/command_settings.py"
```

| Configuração | Significado |
|---|---|
| `name`, `description` | o nome e a descrição, no lugar dos da função |
| `name_localizations`, `description_localizations` | texto por idioma; veja [Registrando comandos](registration.md#translations) para uma função de tradução |
| `ephemeral` | as respostas ficam visíveis só para o usuário, a menos que uma chamada diga o contrário |
| `auto_defer` | segundos após os quais um uso sem resposta tem a resposta adiada; `None` desativa ([Respondendo a interações](responses.md#deferring)) |
| `default_permissions` | as permissões de que um membro precisa para ver e usar o comando; os administradores do servidor podem mudar quem pode usá-lo |
| `nsfw` | mostrado só em canais com restrição de idade |
| `guilds` | IDs dos servidores onde registrar o comando, em vez de globalmente |
| `contexts` | onde um comando global pode ser usado: servidores, as mensagens diretas do bot, canais privados |
| `integration_types` | quais instalações trazem o comando: instalação no servidor, instalação no usuário |
| `checks` | verificações que rodam antes do handler ([Verificações](checks.md)) |
| `extra` | campos brutos mesclados no JSON do comando |

`ephemeral`, `auto_defer`, `default_permissions`, `nsfw`, `contexts` e `integration_types` também podem ser definidos para todos os comandos de um [router](routers.md#defaults), e `contexts`, `integration_types` e `auto_defer` para todos os comandos do bot.

## Limites { #limits }

O decorador verifica o que o Discord rejeitaria e levanta `CommandDefinitionError` nomeando a função e o parâmetro:

- Os nomes têm de 1 a 32 caracteres entre letras, dígitos, `-` e `_`, sem nenhuma letra maiúscula. Os nomes de comandos de usuário e de mensagem também podem ter letras maiúsculas e espaços.
- As descrições têm de 1 a 100 caracteres.
- Um comando tem no máximo 25 opções e uma opção no máximo 25 escolhas.
- `*args`, `**kwargs`, opções somente posicionais e opções sem anotação de tipo são recusadas.

Um aviso, e não um erro, informa quando os nomes, as descrições e os valores das escolhas de um comando somam mais que os 8000 caracteres que o Discord permite. A sincronização avisa quando um escopo tem mais de 100 comandos de barra, 15 comandos de usuário ou 15 comandos de mensagem.

## Próximos passos { #next }

- [Comandos de usuário, de mensagem e de ponto de entrada](context-menus.md)
- [Registrando comandos](registration.md) no Discord, em um servidor ou globalmente, e traduzindo-os
- [Respondendo a interações](responses.md)
