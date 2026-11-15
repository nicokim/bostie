# Slash commands

A slash command is an async function decorated with `@bot.command`. Its first parameter takes the [`Context`](responses.md); every parameter after it is an option of the command.

```python
--8<-- "docs/snippets/commands/option_types.py"
```

The command is `/report`. Its name is the function's name and its description is the first line of the docstring; `@bot.command(name=..., description=...)` sets them instead, and a command with neither gets `…` as its description. `@bot.command` and `@bot.command(...)` both work, on the bot or on a [router](routers.md).

## Option types

The type hint of a parameter picks the option type, and the handler receives the value already converted:

| Hint | Discord option | The handler receives |
|---|---|---|
| `str` | string | the text |
| `int` | integer | an `int` |
| `float` | number | a `float`, also when the user typed a whole number |
| `bool` | boolean | a `bool` |
| `User` | user | the `User` |
| `ResolvedMember` | user | the member, with its `user`; only in guilds |
| `Role` | role | the `Role` |
| `PartialChannel` | channel | the channel as the interaction resolved it |
| `Attachment` | attachment | the uploaded file's `Attachment` |
| `User \| Role` or `ResolvedMember \| Role` | mentionable | whichever the user picked |
| `Literal[...]` or an `Enum` | string, integer or number with choices | the literal or the enum member |

A hint the table does not list raises `CommandDefinitionError` when the decorator runs. For a channel option the hint is `PartialChannel`, not `Channel`, and for a member it is `ResolvedMember`, not `Member`: an interaction carries those partial objects, and the error message says so.

## Required and optional

An option without a default is required. A default, or `| None` in the hint, makes it optional; an optional option that the user left out gets its default, or `None`. Discord wants required options first, and bostie sorts them that way when it builds the command, whatever order the parameters have.

## Descriptions

A string in `Annotated` is the option's description: `Annotated[str, "What happened"]`. The docstring can hold the descriptions instead:

```python
--8<-- "docs/snippets/commands/docstring_options.py"
```

bostie reads an `Args:` section, as above, and a `Parameters` section underlined with dashes; a description may run over several lines. A string in `Annotated` wins over `Option(description=)`, which wins over the docstring. An option described nowhere gets `…`.

## Ranges and option settings

`Range[int, 1, 10]` is an `int` option from 1 to 10, inclusive. `Range` also takes `float`, and for `str` the bounds are the length: `Range[str, 1, 2000]`. `Option(...)` in `Annotated` sets everything else, and it wins over what the hint implies:

```python
--8<-- "docs/snippets/commands/option_settings.py"
```

| `Option` field | Applies to | Meaning |
|---|---|---|
| `name` | all | the option's name on Discord; the parameter keeps its Python name |
| `description` | all | the description, when `Annotated` holds no string |
| `name_localizations`, `description_localizations` | all | text per locale, such as `{"fr": "salon"}` |
| `min`, `max` | integer, number | inclusive bounds, within ±2⁵³ |
| `min_length`, `max_length` | string | length bounds, 0 to 6000 and 1 to 6000 |
| `channel_types` | channel | the channel types the user may pick |
| `file_types` | attachment | up to 10 of `image`, `video`, `audio` or extensions such as `.pdf` |
| `choices` | string, integer, number | fixed choices; see below |
| `autocomplete` | string, integer, number | a function that suggests values as the user types |
| `extra` | all | raw fields merged into the option's JSON, for a field bostie does not model yet |

A field on an option type it does not apply to raises `CommandDefinitionError`.

## Choices

A `Literal` or an `Enum` offers a fixed list. A `Literal` choice is named after its value; an `Enum` choice is named after the member and sends the member's value. `Option(choices=[...])` renames or localizes them, as long as it lists the same values in the same order:

```python
--8<-- "docs/snippets/commands/option_choices.py"
```

The handler of `/order` gets `"tea"`, `"coffee"` or `"water"` for `drink`, a `Size` member for `size`, and the value of the picked `Choice` for `milk`. With a plain `str`, `int` or `float` hint, `Option(choices=[...])` alone defines the choices; each is a `Choice(name, value)` or a bare value, named after itself. Choice values must have the option's type, and the values of a `Literal` or an `Enum` must be all `str`, all `int` or all `float`. An option has at most 25 choices, a choice name is 1 to 100 characters and a string value at most 100.

## Autocomplete

`Option(autocomplete=function)` asks `function` for suggestions while the user types. It gets an `AutocompleteContext` and the text typed so far, and returns values or `Choice` objects:

```python
--8<-- "docs/snippets/commands/autocomplete_options.py"
```

`ctx.options` holds the options filled in so far, by option name, converted as the handler would get them when they convert; the option being typed stays as text. bostie sends the first 25 suggestions, cuts names longer than 100 characters, and drops suggestions with an empty name, a value of the wrong type or a string value longer than 100 characters, with a warning for each case. An option cannot have both `choices` and `autocomplete`. If the function raises, the error goes to the [error hooks](checks.md#error-hooks) and Discord gets an empty list.

## Converters

`Annotated[T, Convert(function)]` turns the option's value into a `T` before the handler runs. `function(ctx, value)` may be async, and the type hint of its `value` parameter decides the Discord option type, `Range` and `Annotated` descriptions included:

```python
--8<-- "docs/snippets/commands/convert_options.py"
```

The converter runs only for an option the user gave; an optional option left out gets its default unconverted. An exception from the converter goes to the error hooks like an exception from the handler.

## Subcommands and groups

`bot.group(name, description)` registers a command made of subcommands, and `group.group(...)` adds a group of subcommands inside it. Groups nest one level deep, and a group holds at most 25 entries:

```python
--8<-- "docs/snippets/commands/command_groups.py"
```

This defines `/roles give` and `/roles color set`. `@group.command(name=...)` names a subcommand differently from its function. `ctx.command_name` is the full name that ran, such as `"roles color set"`, and `command.qualified_name` is the same for the command object.

A top-level group takes the settings of a command except `name`, `ephemeral` and `auto_defer`, and they apply to all its subcommands: `default_permissions`, `nsfw`, `guilds`, `contexts` and `integration_types` exist only on top-level commands in Discord. A nested group takes the two localizations, `checks` and `extra`. A subcommand takes `name`, `description`, the two localizations, `ephemeral`, `auto_defer`, `checks` and `extra`. The checks of a group run before those of its subcommands.

## Command settings

```python
--8<-- "docs/snippets/commands/command_settings.py"
```

| Setting | Meaning |
|---|---|
| `name`, `description` | the name and description, instead of the function's |
| `name_localizations`, `description_localizations` | text per locale; see [Registering commands](registration.md#translations) for a translation function |
| `ephemeral` | answers are visible only to the user, unless a call says otherwise |
| `auto_defer` | seconds after which an unanswered use is deferred; `None` turns it off ([Answering interactions](responses.md#deferring)) |
| `default_permissions` | the permissions a member needs to see and use the command; server admins can change who may use it |
| `nsfw` | shown only in age-restricted channels |
| `guilds` | guild ids to register the command in instead of globally |
| `contexts` | where a global command can be used: guilds, the bot's DMs, private channels |
| `integration_types` | which installs carry the command: guild install, user install |
| `checks` | checks that run before the handler ([Checks](checks.md)) |
| `extra` | raw fields merged into the command's JSON |

`ephemeral`, `auto_defer`, `default_permissions`, `nsfw`, `contexts` and `integration_types` can also be set for every command of a [router](routers.md#defaults), and `contexts`, `integration_types` and `auto_defer` for every command of the bot.

## Limits

The decorator checks what Discord would reject and raises `CommandDefinitionError` naming the function and the parameter:

- Names are 1 to 32 characters of letters, digits, `-` and `_`, with no uppercase letter. User and message command names may also hold uppercase letters and spaces.
- Descriptions are 1 to 100 characters.
- A command has at most 25 options and an option at most 25 choices.
- `*args`, `**kwargs`, positional-only options and options without a type hint are refused.

A warning, not an error, says when a command's names, descriptions and choice values add up to more than the 8000 characters Discord allows. The sync warns when one scope holds more than 100 slash commands, 15 user commands or 15 message commands.

## Next

- [User, message and entry point commands](context-menus.md)
- [Registering commands](registration.md) with Discord, in one guild or globally, and translating them
- [Answering interactions](responses.md)
