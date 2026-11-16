---
source: 620651abbb35486bdf47aba0669128b8bb1d0ad6
description: "Créez des commandes slash par annotations de type Python : types d'options, choix, plages, autocomplétion, convertisseurs, sous-commandes et limites Discord."
---

# Commandes slash

![Bostie écrivant une commande slash dans une bulle](../assets/mascot/bostie-commands.webp){ align=right width="180" loading=lazy }

Une commande slash est une fonction async décorée avec `@bot.command`. Son premier paramètre reçoit le [`Context`](responses.md) ; chaque paramètre suivant est une option de la commande.

```python
--8<-- "docs/snippets/commands/option_types.py"
```

La commande est `/report`. Son nom est celui de la fonction et sa description est la première ligne de la docstring ; `@bot.command(name=..., description=...)` les définit à la place, et une commande qui n'a ni l'un ni l'autre reçoit `…` comme description. `@bot.command` et `@bot.command(...)` fonctionnent tous les deux, sur le bot comme sur un [routeur](routers.md).

## Types d'options { #option-types }

L'annotation de type d'un paramètre choisit le type de l'option, et le gestionnaire reçoit la valeur déjà convertie :

| Annotation | Option Discord | Le gestionnaire reçoit |
|---|---|---|
| `str` | chaîne | le texte |
| `int` | entier | un `int` |
| `float` | nombre | un `float`, même quand l'utilisateur a tapé un nombre entier |
| `bool` | booléen | un `bool` |
| `User` | utilisateur | le `User` |
| `ResolvedMember` | utilisateur | le membre, avec son `user` ; uniquement sur un serveur |
| `Role` | rôle | le `Role` |
| `PartialChannel` | salon | le salon tel que l'interaction l'a résolu |
| `Attachment` | pièce jointe | l'`Attachment` du fichier envoyé |
| `User \| Role` ou `ResolvedMember \| Role` | mentionnable | celui que l'utilisateur a choisi |
| `Literal[...]` ou un `Enum` | chaîne, entier ou nombre avec des choix | le littéral ou le membre de l'enum |

Une annotation absente du tableau lève `CommandDefinitionError` à l'exécution du décorateur. Pour une option de salon, l'annotation est `PartialChannel`, pas `Channel`, et pour un membre, c'est `ResolvedMember`, pas `Member` : une interaction transporte ces objets partiels, et le message d'erreur le précise.

## Obligatoires et facultatives { #required-and-optional }

Une option sans valeur par défaut est obligatoire. Une valeur par défaut, ou `| None` dans l'annotation, la rend facultative ; une option facultative que l'utilisateur a omise reçoit sa valeur par défaut, ou `None`. Discord veut les options obligatoires en premier, et bostie les trie ainsi quand il construit la commande, quel que soit l'ordre des paramètres.

## Descriptions { #descriptions }

Une chaîne dans `Annotated` est la description de l'option : `Annotated[str, "What happened"]`. La docstring peut contenir les descriptions à la place :

```python
--8<-- "docs/snippets/commands/docstring_options.py"
```

bostie lit une section `Args:`, comme ci-dessus, et une section `Parameters` soulignée de tirets ; une description peut s'étendre sur plusieurs lignes. Une chaîne dans `Annotated` l'emporte sur `Option(description=)`, qui l'emporte sur la docstring. Une option décrite nulle part reçoit `…`.

## Plages et réglages d'option { #ranges-and-option-settings }

`Range[int, 1, 10]` est une option `int` de 1 à 10 inclus. `Range` accepte aussi `float`, et pour `str` les bornes portent sur la longueur : `Range[str, 1, 2000]`. `Option(...)` dans `Annotated` règle tout le reste, et l'emporte sur ce que l'annotation implique :

```python
--8<-- "docs/snippets/commands/option_settings.py"
```

| Champ de `Option` | S'applique à | Signification |
|---|---|---|
| `name` | tous | le nom de l'option sur Discord ; le paramètre garde son nom Python |
| `description` | tous | la description, quand `Annotated` ne contient pas de chaîne |
| `name_localizations`, `description_localizations` | tous | le texte par locale, par exemple `{"fr": "salon"}` |
| `min`, `max` | entier, nombre | bornes incluses, dans ±2⁵³ |
| `min_length`, `max_length` | chaîne | bornes de longueur, de 0 à 6000 et de 1 à 6000 |
| `channel_types` | salon | les types de salons que l'utilisateur peut choisir |
| `file_types` | pièce jointe | jusqu'à 10 valeurs parmi `image`, `video`, `audio` ou des extensions comme `.pdf` |
| `choices` | chaîne, entier, nombre | des choix fixes ; voir plus bas |
| `autocomplete` | chaîne, entier, nombre | une fonction qui suggère des valeurs pendant que l'utilisateur tape |
| `extra` | tous | des champs bruts fusionnés dans le JSON de l'option, pour un champ que bostie ne modélise pas encore |

Un champ placé sur un type d'option auquel il ne s'applique pas lève `CommandDefinitionError`.

## Choix { #choices }

Un `Literal` ou un `Enum` propose une liste fixe. Un choix de `Literal` porte le nom de sa valeur ; un choix d'`Enum` porte le nom du membre et envoie la valeur du membre. `Option(choices=[...])` les renomme ou les localise, à condition de lister les mêmes valeurs dans le même ordre :

```python
--8<-- "docs/snippets/commands/option_choices.py"
```

Le gestionnaire de `/order` reçoit `"tea"`, `"coffee"` ou `"water"` pour `drink`, un membre de `Size` pour `size`, et la valeur du `Choice` choisi pour `milk`. Avec une simple annotation `str`, `int` ou `float`, `Option(choices=[...])` définit à lui seul les choix ; chacun est un `Choice(name, value)` ou une valeur nue, qui porte son propre nom. Les valeurs des choix doivent avoir le type de l'option, et les valeurs d'un `Literal` ou d'un `Enum` doivent être toutes des `str`, toutes des `int` ou toutes des `float`. Une option a au plus 25 choix, un nom de choix fait de 1 à 100 caractères et une valeur de type chaîne au plus 100.

## Autocomplétion { #autocomplete }

`Option(autocomplete=function)` demande des suggestions à `function` pendant que l'utilisateur tape. Elle reçoit un `AutocompleteContext` et le texte tapé jusque-là, et renvoie des valeurs ou des objets `Choice` :

```python
--8<-- "docs/snippets/commands/autocomplete_options.py"
```

`ctx.options` contient les options déjà remplies, par nom d'option, converties comme le gestionnaire les recevrait quand elles se convertissent ; l'option en cours de saisie reste du texte. bostie envoie les 25 premières suggestions, tronque les noms de plus de 100 caractères, et écarte les suggestions au nom vide, à la valeur du mauvais type ou à la valeur de type chaîne de plus de 100 caractères, avec un avertissement pour chaque cas. Une option ne peut pas avoir à la fois `choices` et `autocomplete`. Si la fonction lève une exception, l'erreur part vers les [hooks d'erreur](checks.md#error-hooks) et Discord reçoit une liste vide.

## Convertisseurs { #converters }

`Annotated[T, Convert(function)]` transforme la valeur de l'option en `T` avant l'exécution du gestionnaire. `function(ctx, value)` peut être async, et l'annotation de type de son paramètre `value` décide du type d'option Discord, descriptions `Range` et `Annotated` comprises :

```python
--8<-- "docs/snippets/commands/convert_options.py"
```

Le convertisseur ne s'exécute que pour une option que l'utilisateur a fournie ; une option facultative omise reçoit sa valeur par défaut sans conversion. Une exception levée par le convertisseur part vers les hooks d'erreur comme une exception du gestionnaire.

## Sous-commandes et groupes { #subcommands-and-groups }

`bot.group(name, description)` enregistre une commande faite de sous-commandes, et `group.group(...)` y ajoute un groupe de sous-commandes. Les groupes s'imbriquent sur un seul niveau, et un groupe contient au plus 25 entrées :

```python
--8<-- "docs/snippets/commands/command_groups.py"
```

Ce code définit `/roles give` et `/roles color set`. `@group.command(name=...)` donne à une sous-commande un autre nom que celui de sa fonction. `ctx.command_name` est le nom complet qui s'est exécuté, par exemple `"roles color set"`, et `command.qualified_name` donne la même chose pour l'objet commande.

Un groupe de premier niveau accepte les réglages d'une commande sauf `name`, `ephemeral` et `auto_defer`, et ils s'appliquent à toutes ses sous-commandes : `default_permissions`, `nsfw`, `guilds`, `contexts` et `integration_types` n'existent que sur les commandes de premier niveau chez Discord. Un groupe imbriqué accepte les deux localisations, `checks` et `extra`. Une sous-commande accepte `name`, `description`, les deux localisations, `ephemeral`, `auto_defer`, `checks` et `extra`. Les vérifications d'un groupe s'exécutent avant celles de ses sous-commandes.

## Réglages de commande { #command-settings }

```python
--8<-- "docs/snippets/commands/command_settings.py"
```

| Réglage | Signification |
|---|---|
| `name`, `description` | le nom et la description, à la place de ceux de la fonction |
| `name_localizations`, `description_localizations` | le texte par locale ; voir [Enregistrer les commandes](registration.md#translations) pour une fonction de traduction |
| `ephemeral` | les réponses ne sont visibles que par l'utilisateur, sauf si un appel en décide autrement |
| `auto_defer` | le nombre de secondes après lequel une utilisation sans réponse est différée ; `None` le désactive ([Répondre aux interactions](responses.md#deferring)) |
| `default_permissions` | les permissions dont un membre a besoin pour voir et utiliser la commande ; les administrateurs du serveur peuvent changer qui peut l'utiliser |
| `nsfw` | affichée uniquement dans les salons avec restriction d'âge |
| `guilds` | les ids des serveurs où enregistrer la commande au lieu de l'enregistrer globalement |
| `contexts` | où une commande globale peut être utilisée : serveurs, messages privés du bot, salons privés |
| `integration_types` | quelles installations portent la commande : installation sur un serveur, installation utilisateur |
| `checks` | les vérifications qui s'exécutent avant le gestionnaire ([Vérifications](checks.md)) |
| `extra` | des champs bruts fusionnés dans le JSON de la commande |

`ephemeral`, `auto_defer`, `default_permissions`, `nsfw`, `contexts` et `integration_types` peuvent aussi être réglés pour toutes les commandes d'un [routeur](routers.md#defaults), et `contexts`, `integration_types` et `auto_defer` pour toutes les commandes du bot.

## Limites { #limits }

Le décorateur vérifie ce que Discord refuserait et lève `CommandDefinitionError` en nommant la fonction et le paramètre :

- Les noms font de 1 à 32 caractères parmi les lettres, les chiffres, `-` et `_`, sans majuscule. Les noms de commandes utilisateur et de commandes de message peuvent aussi contenir des majuscules et des espaces.
- Les descriptions font de 1 à 100 caractères.
- Une commande a au plus 25 options et une option au plus 25 choix.
- `*args`, `**kwargs`, les options positionnelles uniquement et les options sans annotation de type sont refusés.

Un avertissement, et non une erreur, signale quand les noms, descriptions et valeurs de choix d'une commande dépassent au total les 8000 caractères que Discord autorise. La synchronisation avertit quand une même portée contient plus de 100 commandes slash, 15 commandes utilisateur ou 15 commandes de message.

## Ensuite { #next }

- [Commandes utilisateur, de message et de point d'entrée](context-menus.md)
- [Enregistrer les commandes](registration.md) auprès de Discord, sur un serveur ou globalement, et les traduire
- [Répondre aux interactions](responses.md)
