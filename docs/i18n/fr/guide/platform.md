---
source: 03a349670a189a38d3b3491aba4b54e6af1ec4e9
---

# Activités, monétisation et Social SDK

Les applications Discord ne se limitent pas aux bots sur des serveurs. Cette page couvre les applications installées pour les utilisateurs, les Activités, la vente de SKU et le côté serveur du Social SDK.

```python
--8<-- "docs/snippets/apps/premium.py"
```

## Applications installées pour les utilisateurs { #apps-installed-to-users }

Une application peut s'installer sur le compte d'un utilisateur aussi bien que sur un serveur, et ses commandes fonctionnent alors partout où se trouve cet utilisateur, y compris dans les MP avec ses amis et sur les serveurs sans le bot. Activez l'installation utilisateur dans la page **Installation** du Developer Portal, puis laissez les commandes l'utiliser avec `integration_types` et `contexts`, comme le fait l'extrait pour tout le bot ([Enregistrer les commandes](registration.md#where-commands-can-be-used)).

Dans un tel contexte, le bot n'est pas dans le salon : il n'y a ni entrée de cache ni événement de la gateway, et l'API REST y refuse la plupart des appels. `ctx.interaction.authorizing_integration_owners` indique quelles installations ont permis l'interaction, `ctx.interaction.context` où elle a eu lieu, et `ctx.interaction.app_permissions` ce que l'application a le droit de faire. Les réponses fonctionnent comme d'habitude, et Discord autorise 5 messages de suivi pour une application installée uniquement pour l'utilisateur ; bostie avertit au sixième.

## Activités { #activities }

Une Activité est une application web qui s'exécute dans Discord. bostie couvre le côté bot :

- `@bot.entry_point(name)` enregistre la commande de point d'entrée qu'affiche l'App Launcher, comme l'explique [Commandes utilisateur, de message et de point d'entrée](context-menus.md#the-entry-point-command).
- `await ctx.launch_activity()` ouvre l'Activité depuis une commande ou un composant et renvoie l'id de son instance.
- `await bot.rest.application.fetch_activity_instance(application_id, instance_id)` renvoie une `ActivityInstance` : où elle s'exécute et les id des utilisateurs qui y participent.

L'[exemple d'Activité](../examples.md#activity) enregistre un point d'entrée et une commande slash qui la lancent tous les deux.

## Monétisation { #monetization }

Une application dont la monétisation est activée vend des SKU : des abonnements, des objets durables et des consommables. Les achats de l'utilisateur sont des entitlements.

- `ctx.interaction.entitlements` liste les entitlements de l'utilisateur et du serveur d'une interaction, si bien qu'une commande peut vérifier un achat sans requête.
- `ui.premium_button(sku_id)` affiche un bouton qui ouvre l'achat d'un SKU ; il n'atteint jamais le bot. `fmt.store(application_id, sku_id=None)` crée un lien vers la page de boutique de l'application, ou vers un SKU, que Discord affiche sous forme de carte.
- `bot.rest.monetization` liste les SKU de l'application (`list_skus`), parcourt les entitlements avec des filtres par utilisateur, serveur et SKU (`list_entitlements`), en récupère un, et marque un consommable comme utilisé (`consume_entitlement`). Il liste et récupère aussi les abonnements d'un SKU.
- `create_test_entitlement(application_id, sku_id=..., owner_id=..., owner_type=1)` accorde un entitlement de test gratuit à un serveur (1) ou à un utilisateur (2), et `delete_test_entitlement` le retire.
- Les événements `EntitlementCreate`, `EntitlementUpdate` et `EntitlementDelete`, ainsi que `SubscriptionCreate`, `SubscriptionUpdate` et `SubscriptionDelete`, signalent les changements par la gateway ; les événements d'entitlement arrivent aussi sous forme d'[événements webhook](http-interactions.md#webhook-events). Un entitlement de test arrive sous forme de `PartialEntitlement`, sans les dates d'un vrai.

## Social SDK { #social-sdk }

Le Social SDK de Discord s'exécute dans les jeux. Son côté serveur passe par REST :

- `bot.rest.lobbies` crée des lobbies, modifie leurs métadonnées et leurs membres (`add_member`, `bulk_update_members` pour 25 au plus à la fois, `remove_member`), les supprime, invite un utilisateur dans le salon lié au lobby, et définit des métadonnées de modération sur les messages du lobby.
- `user.rest.lobbies`, sur un [`BearerClient`](oauth2.md#calling-the-api-as-the-user) avec le token d'un joueur, crée ou rejoint un lobby par son secret (`create_or_join`), le quitte (`leave`), lie et délie un salon Discord (`link_channel`, `unlink_channel`), envoie et liste des messages (`send_message`, `list_messages`), et invite le joueur lui-même dans le salon lié (`create_invite_for_self`).
- `bot.rest.partner_sdk` échange des tokens : un token provisoire pour un joueur du propre système de comptes du jeu, un token contre celui d'un fournisseur d'identité externe, le token d'un jeu enfant contre celui d'un éditeur ; il défait aussi la fusion des comptes provisoires et définit des métadonnées de modération sur les messages privés.
- `bot.rest.identities` liste, récupère et met à jour les identités et profils d'application des utilisateurs qui ont lié un compte externe.

Les messages de lobby et les messages privés en jeu arrivent sous forme d'[événements webhook](http-interactions.md#webhook-events) `LobbyMessage*` et `GameDirectMessage*`.
