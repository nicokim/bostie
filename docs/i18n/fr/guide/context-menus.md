---
source: db591175a7dec7d094226c92a4855de730089f1d
description: "Ajoutez des commandes utilisateur et de message au menu contextuel et la commande de point d'entrée qui lance une Activité, avec cibles typées et réglages."
---

# Commandes utilisateur, de message et de point d'entrée

En plus des commandes slash, Discord propose des commandes qui se lancent depuis le menu contextuel d'un utilisateur ou d'un message, et la commande de point d'entrée qui ouvre l'Activité d'une application.

## Commandes utilisateur et de message { #user-and-message-commands }

```python
--8<-- "docs/snippets/commands/context_menus.py"
```

`@bot.user_command(name)` et `@bot.message_command(name)` enregistrent une fonction qui reçoit le contexte et la cible. Le nom est ce qu'affiche le menu : de 1 à 32 caractères, et contrairement aux noms de commandes slash, il peut contenir des majuscules et des espaces. Ces commandes n'ont ni description ni options.

L'annotation de type de la cible indique ce que reçoit le gestionnaire :

| Commande | Annotation | Le gestionnaire reçoit |
|---|---|---|
| utilisateur | `User` | l'utilisateur |
| utilisateur | `ResolvedMember` | le membre, avec son `user` ; uniquement sur un serveur |
| utilisateur | `User \| ResolvedMember` | le membre sur un serveur, l'utilisateur ailleurs |
| message | `Message` | un message envoyé par un utilisateur ou un bot |
| message | `WebhookMessage` | un message envoyé par un webhook, réponses aux interactions comprises |
| message | `Message \| WebhookMessage` | l'un ou l'autre |

Une cible qui ne se résout pas vers le type annoté lève `OptionConversionError`, qui part vers les [hooks d'erreur](checks.md#error-hooks). Annotez une cible de message avec `Message | WebhookMessage`, sauf si la commande n'a de sens que pour l'un des deux.

Elles acceptent les réglages d'une commande slash sauf `description`, `description_localizations` et les réglages d'option : `name_localizations`, `ephemeral`, `auto_defer`, `default_permissions`, `nsfw`, `guilds`, `contexts`, `integration_types`, `checks` et `extra`. Discord autorise 15 commandes utilisateur et 15 commandes de message par portée, et la synchronisation avertit au-delà.

## La commande de point d'entrée { #the-entry-point-command }

Une application avec des Activités a une seule commande de point d'entrée, que l'App Launcher affiche. `@bot.entry_point(name)` l'enregistre avec une fonction qui ne reçoit que le contexte :

```python
--8<-- "docs/snippets/commands/entry_point.py"
```

Le gestionnaire répond comme pour n'importe quelle commande : `ctx.launch_activity()` ouvre l'Activité et renvoie l'id de son instance, et `ctx.respond(...)` répond plutôt par un message. Discord refuse la commande venant d'une application dont les Activités ne sont pas activées dans le Developer Portal.

Le point d'entrée est toujours global. Quand `dev_guilds` est défini, la synchronisation l'omet et émet un avertissement. Quand Discord a déjà son point d'entrée par défaut sous un autre nom, la synchronisation garde l'id de cette commande, si bien que Discord la remplace au lieu de garder les deux. `launch_activity()` existe aussi sur les contextes de composant et de fenêtre modale, mais ce doit être la première réponse.

`entry_point` accepte `description`, `name_localizations`, `description_localizations`, `ephemeral`, `auto_defer`, `default_permissions`, `nsfw`, `contexts`, `integration_types`, `checks` et `extra`. [Activités, monétisation et Social SDK](platform.md#activities) couvre le reste du côté Activité.
