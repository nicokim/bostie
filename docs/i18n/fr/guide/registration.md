---
source: 9aa2f3cd3cfe588188668066e047191f9b207226
---

# Enregistrer les commandes

Discord n'affiche une commande qu'une fois que l'application l'a enregistrée. bostie s'en charge pour vous : au démarrage du bot, il compare les commandes que vous avez définies avec celles que Discord détient et ne les envoie que si quelque chose diffère.

## La synchronisation au démarrage { #the-sync-at-startup }

`bot.start()` et `bot.run()` synchronisent avant que la gateway se connecte. Pour chaque portée, globale ou un serveur, bostie récupère les commandes enregistrées avec leurs localisations, les compare champ par champ, et remplace les commandes de la portée en une seule requête quand elles diffèrent. Le journal affiche `commands up to date (global)` ou `commands synced (global): added [...], removed [...], changed [...]`.

Une commande que Discord détient mais que vous ne définissez plus est supprimée, sauf si son type est un type que vous ne définissez jamais : un bot sans `@bot.entry_point` garde la commande de point d'entrée que Discord a créée pour son Activité.

`Bot(auto_sync=False)` saute la synchronisation. `await bot.sync_commands()` remplace tout de suite les commandes de chaque portée, modifiées ou non, et renvoie un `SyncResult` par portée avec les noms `added`, `removed` et `changed`, et les `ids` que Discord a attribués aux commandes slash.

## Portées { #scopes }

Une commande sans `guilds` est globale : elle fonctionne sur chaque serveur qui a installé l'application et, si ses `contexts` le permettent, en messages privés. `guilds=[...]` ne l'enregistre que sur ces serveurs.

Pendant que vous travaillez sur un bot, enregistrez tout sur un serveur de test :

```python
--8<-- "docs/snippets/commands/command_sync.py"
```

`Bot(dev_guilds=[...])` enregistre chaque commande qui n'a pas ses propres `guilds` sur ces serveurs au lieu de l'enregistrer globalement. La commande de point d'entrée est toujours globale, donc la synchronisation l'omet et émet un avertissement.

### Un même nom dans plusieurs portées { #one-name-in-several-scopes }

```python
--8<-- "docs/snippets/commands/guild_variants.py"
```

Des commandes peuvent partager un nom tant qu'aucun serveur n'en a deux : un `/rules` global et un `/rules` pour un serveur, ou un `/rules` par serveur. Chacune est enregistrée dans sa propre portée, et une interaction exécute la commande enregistrée sur son serveur, sinon la commande globale. Sur ce serveur, Discord liste à la fois la commande globale et celle du serveur. Avec `dev_guilds`, la commande propre à un serveur prend la place de la commande globale sur ce serveur. Deux commandes du même nom dont les serveurs se recoupent, ou deux commandes globales, lèvent `CommandDefinitionError` à l'enregistrement de la seconde. Les commandes utilisateur et de message suivent les mêmes règles.

La synchronisation ne touche que les portées qui ont des commandes. Quand vous retirez `dev_guilds`, les commandes restent sur le serveur de test à côté des nouvelles commandes globales ; `await bot.rest.commands.bulk_overwrite_guild(bot.application_id, guild_id, [])` les efface.

## Où les commandes peuvent être utilisées { #where-commands-can-be-used }

`contexts` et `integration_types` s'appliquent aux commandes globales. Réglez-les sur une commande, un routeur ou le bot :

- `contexts` : `InteractionContextType.GUILD`, `BOT_DM` (les messages privés du bot) et `PRIVATE_CHANNEL` (les autres messages privés et les groupes privés, pour une application installée par l'utilisateur).
- `integration_types` : `ApplicationIntegrationType.GUILD_INSTALL` et `USER_INSTALL`.

`Bot(contexts=[...], integration_types=[...])` est la valeur par défaut de chaque commande globale qui ne règle pas la sienne. S'ils ne sont réglés nulle part, bostie n'envoie aucun des deux champs et Discord applique ses valeurs par défaut ; pour `integration_types`, ce sont les contextes d'installation configurés pour l'application. Retirer un réglage que vous aviez défini compte comme une modification, donc la synchronisation suivante redonne à la commande les valeurs par défaut de Discord.

## Mentions de commandes { #command-mentions }

`await bot.command_mention("roles color set")` renvoie `</roles color set:ID>`, que Discord affiche comme une commande cliquable. Elle utilise les ids de la dernière synchronisation, ou interroge Discord une fois par portée. Avec `guild_id=`, une commande enregistrée sur ce serveur l'emporte sur une commande globale du même nom. Un nom inconnu lève `LookupError`, et un appel avant le démarrage du bot lève `StateError`. `fmt.command(name, command_id)` construit le même texte à partir d'un id que vous avez déjà.

## Traductions { #translations }

`name_localizations` et `description_localizations` sur une commande, un groupe, une sous-commande, une `Option` ou un `Choice` traduisent un texte. Une fonction de traduction les traduit tous :

```python
--8<-- "docs/snippets/commands/command_translations.py"
```

`Bot(translate=function)` appelle `function(text, locale)` pendant la synchronisation pour chaque nom et chaque description de chaque commande, groupe, sous-commande et option, et pour chaque nom de choix, une fois par `Locale`. Un retour `None`, ou le texte inchangé, n'ajoute rien pour cette locale, et une localisation que vous avez écrite vous-même l'emporte sur la fonction. Le gestionnaire voit la langue de l'utilisateur dans `ctx.locale` et celle du serveur dans `ctx.interaction.guild_locale`.

## Quand Discord refuse les commandes { #when-discord-refuses-the-commands }

Une synchronisation que Discord rejette avec un 400 lève `CommandSyncError`. Son message a une ligne par problème, qui nomme la commande, le chemin de l'option et le message de Discord, et `error.problems` les contient sous forme d'objets `SyncProblem`. La plupart des problèmes sont détectés plus tôt : les décorateurs vérifient les noms, les descriptions et les limites à leur exécution.
