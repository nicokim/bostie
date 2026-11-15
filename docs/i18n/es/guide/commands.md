---
source: 7f5c86665191d303ab8ac2e965d43d553cee7953
---

# Comandos slash

![Bostie escribe un comando slash en un globo de diálogo](../assets/mascot/bostie-commands.webp){ align=right width="180" loading=lazy }

Un comando slash es una función asíncrona decorada con `@bot.command`. Su primer parámetro recibe el [`Context`](responses.md); cada parámetro que va después es una opción del comando.

```python
--8<-- "docs/snippets/commands/option_types.py"
```

El comando es `/report`. Su nombre es el nombre de la función y su descripción es la primera línea del docstring; `@bot.command(name=..., description=...)` los define en su lugar, y un comando sin ninguno de los dos recibe `…` como descripción. `@bot.command` y `@bot.command(...)` funcionan los dos, en el bot o en un [router](routers.md).

## Tipos de opción { #option-types }

La anotación de tipo de un parámetro elige el tipo de opción, y el handler recibe el valor ya convertido:

| Anotación | Opción de Discord | El handler recibe |
|---|---|---|
| `str` | cadena | el texto |
| `int` | entero | un `int` |
| `float` | número | un `float`, también cuando el usuario escribió un número entero |
| `bool` | booleano | un `bool` |
| `User` | usuario | el `User` |
| `ResolvedMember` | usuario | el miembro, con su `user`; solo en servidores |
| `Role` | rol | el `Role` |
| `PartialChannel` | canal | el canal tal como lo resolvió la interacción |
| `Attachment` | archivo adjunto | el `Attachment` del archivo subido |
| `User \| Role` o `ResolvedMember \| Role` | mencionable | lo que haya elegido el usuario |
| `Literal[...]` o un `Enum` | cadena, entero o número con choices | el literal o el miembro del enum |

Una anotación que no aparece en la tabla lanza `CommandDefinitionError` cuando se ejecuta el decorador. Para una opción de canal la anotación es `PartialChannel`, no `Channel`, y para un miembro es `ResolvedMember`, no `Member`: una interacción trae esos objetos parciales, y el mensaje de error lo dice.

## Obligatorias y opcionales { #required-and-optional }

Una opción sin valor por defecto es obligatoria. Un valor por defecto, o `| None` en la anotación, la vuelve opcional; una opción opcional que el usuario no completó recibe su valor por defecto, o `None`. Discord quiere las opciones obligatorias primero, y bostie las ordena así al construir el comando, sin importar el orden de los parámetros.

## Descripciones { #descriptions }

Una cadena dentro de `Annotated` es la descripción de la opción: `Annotated[str, "What happened"]`. El docstring también puede contener las descripciones:

```python
--8<-- "docs/snippets/commands/docstring_options.py"
```

bostie lee una sección `Args:`, como la de arriba, y una sección `Parameters` subrayada con guiones; una descripción puede ocupar varias líneas. Una cadena en `Annotated` tiene prioridad sobre `Option(description=)`, que a su vez tiene prioridad sobre el docstring. Una opción que no se describe en ningún lado recibe `…`.

## Rangos y ajustes de opción { #ranges-and-option-settings }

`Range[int, 1, 10]` es una opción `int` de 1 a 10, ambos incluidos. `Range` también acepta `float`, y para `str` los límites son la longitud: `Range[str, 1, 2000]`. `Option(...)` dentro de `Annotated` define todo lo demás, y tiene prioridad sobre lo que implica la anotación:

```python
--8<-- "docs/snippets/commands/option_settings.py"
```

| Campo de `Option` | Se aplica a | Significado |
|---|---|---|
| `name` | todas | el nombre de la opción en Discord; el parámetro conserva su nombre en Python |
| `description` | todas | la descripción, cuando `Annotated` no contiene ninguna cadena |
| `name_localizations`, `description_localizations` | todas | texto por idioma, como `{"fr": "salon"}` |
| `min`, `max` | entero, número | límites incluidos, dentro de ±2⁵³ |
| `min_length`, `max_length` | cadena | límites de longitud, de 0 a 6000 y de 1 a 6000 |
| `channel_types` | canal | los tipos de canal que el usuario puede elegir |
| `file_types` | archivo adjunto | hasta 10 entre `image`, `video`, `audio` o extensiones como `.pdf` |
| `choices` | cadena, entero, número | choices fijas; ver más abajo |
| `autocomplete` | cadena, entero, número | una función que sugiere valores mientras el usuario escribe |
| `extra` | todas | campos sin procesar que se combinan con el JSON de la opción, para un campo que bostie todavía no modela |

Un campo en un tipo de opción al que no se aplica lanza `CommandDefinitionError`.

## Choices { #choices }

Un `Literal` o un `Enum` ofrece una lista fija. Una choice de un `Literal` se llama como su valor; una choice de un `Enum` se llama como el miembro y envía el valor del miembro. `Option(choices=[...])` les cambia el nombre o las traduce, siempre que enumere los mismos valores en el mismo orden:

```python
--8<-- "docs/snippets/commands/option_choices.py"
```

El handler de `/order` recibe `"tea"`, `"coffee"` o `"water"` para `drink`, un miembro de `Size` para `size`, y el valor de la `Choice` elegida para `milk`. Con una anotación simple `str`, `int` o `float`, `Option(choices=[...])` por sí sola define las choices; cada una es una `Choice(name, value)` o un valor suelto, que se llama como él mismo. Los valores de las choices deben tener el tipo de la opción, y los valores de un `Literal` o de un `Enum` deben ser todos `str`, todos `int` o todos `float`. Una opción tiene como máximo 25 choices, el nombre de una choice tiene de 1 a 100 caracteres y un valor de cadena, como máximo 100.

## Autocompletado { #autocomplete }

`Option(autocomplete=function)` le pide sugerencias a `function` mientras el usuario escribe. Recibe un `AutocompleteContext` y el texto escrito hasta el momento, y devuelve valores u objetos `Choice`:

```python
--8<-- "docs/snippets/commands/autocomplete_options.py"
```

`ctx.options` contiene las opciones completadas hasta el momento, por nombre de opción, convertidas como las recibiría el handler cuando se pueden convertir; la opción que se está escribiendo queda como texto. bostie envía las primeras 25 sugerencias, recorta los nombres de más de 100 caracteres y descarta las sugerencias con un nombre vacío, un valor del tipo incorrecto o un valor de cadena de más de 100 caracteres, con una advertencia para cada caso. Una opción no puede tener a la vez `choices` y `autocomplete`. Si la función lanza una excepción, el error va a los [hooks de errores](checks.md#error-hooks) y Discord recibe una lista vacía.

## Conversores { #converters }

`Annotated[T, Convert(function)]` convierte el valor de la opción en un `T` antes de que se ejecute el handler. `function(ctx, value)` puede ser asíncrona, y la anotación de tipo de su parámetro `value` decide el tipo de opción de Discord, incluidos `Range` y las descripciones de `Annotated`:

```python
--8<-- "docs/snippets/commands/convert_options.py"
```

El conversor solo se ejecuta para una opción que el usuario completó; una opción opcional que no se completó recibe su valor por defecto sin convertir. Una excepción del conversor va a los hooks de errores igual que una excepción del handler.

## Subcomandos y grupos { #subcommands-and-groups }

`bot.group(name, description)` registra un comando formado por subcomandos, y `group.group(...)` agrega dentro de él un grupo de subcomandos. Los grupos se anidan un solo nivel, y un grupo contiene como máximo 25 entradas:

```python
--8<-- "docs/snippets/commands/command_groups.py"
```

Esto define `/roles give` y `/roles color set`. `@group.command(name=...)` le da a un subcomando un nombre distinto del de su función. `ctx.command_name` es el nombre completo que se ejecutó, como `"roles color set"`, y `command.qualified_name` es lo mismo para el objeto del comando.

Un grupo de nivel superior acepta los ajustes de un comando salvo `name`, `ephemeral` y `auto_defer`, y se aplican a todos sus subcomandos: en Discord, `default_permissions`, `nsfw`, `guilds`, `contexts` e `integration_types` solo existen en los comandos de nivel superior. Un grupo anidado acepta las dos traducciones, `checks` y `extra`. Un subcomando acepta `name`, `description`, las dos traducciones, `ephemeral`, `auto_defer`, `checks` y `extra`. Los checks de un grupo se ejecutan antes que los de sus subcomandos.

## Ajustes del comando { #command-settings }

```python
--8<-- "docs/snippets/commands/command_settings.py"
```

| Ajuste | Significado |
|---|---|
| `name`, `description` | el nombre y la descripción, en lugar de los de la función |
| `name_localizations`, `description_localizations` | texto por idioma; ver [Registrar comandos](registration.md#translations) para una función de traducción |
| `ephemeral` | las respuestas solo las ve el usuario, salvo que una llamada indique otra cosa |
| `auto_defer` | segundos tras los cuales un uso sin responder se difiere; `None` lo desactiva ([Responder a interacciones](responses.md#deferring)) |
| `default_permissions` | los permisos que un miembro necesita para ver y usar el comando; los administradores del servidor pueden cambiar quién puede usarlo |
| `nsfw` | solo se muestra en canales restringidos por edad |
| `guilds` | ids de servidores donde registrar el comando en lugar de hacerlo de forma global |
| `contexts` | dónde se puede usar un comando global: servidores, los MD del bot, canales privados |
| `integration_types` | qué instalaciones incluyen el comando: instalación en servidor, instalación de usuario |
| `checks` | checks que se ejecutan antes del handler ([Checks](checks.md)) |
| `extra` | campos sin procesar que se combinan con el JSON del comando |

`ephemeral`, `auto_defer`, `default_permissions`, `nsfw`, `contexts` e `integration_types` también se pueden definir para todos los comandos de un [router](routers.md#defaults), y `contexts`, `integration_types` y `auto_defer` para todos los comandos del bot.

## Límites { #limits }

El decorador comprueba lo que Discord rechazaría y lanza `CommandDefinitionError` indicando la función y el parámetro:

- Los nombres tienen de 1 a 32 caracteres entre letras, dígitos, `-` y `_`, sin mayúsculas. Los nombres de los comandos de usuario y de mensaje también pueden tener mayúsculas y espacios.
- Las descripciones tienen de 1 a 100 caracteres.
- Un comando tiene como máximo 25 opciones y una opción, como máximo 25 choices.
- Se rechazan `*args`, `**kwargs`, las opciones solo posicionales y las opciones sin anotación de tipo.

Una advertencia, no un error, avisa cuando los nombres, las descripciones y los valores de las choices de un comando suman más de los 8000 caracteres que permite Discord. La sincronización advierte cuando un ámbito contiene más de 100 comandos slash, 15 comandos de usuario o 15 comandos de mensaje.

## Siguiente paso { #next }

- [Comandos de usuario, de mensaje y de punto de entrada](context-menus.md)
- [Registrar comandos](registration.md) en Discord, en un servidor o de forma global, y traducirlos
- [Responder a interacciones](responses.md)
