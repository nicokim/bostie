---
source: f23a479950d38c899e79ae34c9fa1854c7b6fd34
---

# Cache et permissions

![Bostie dépose un os dans une boîte](../assets/mascot/bostie-cache.webp){ align=right width="180" loading=lazy }

`bot.cache` conserve ce que la gateway a dit au bot, pour que les gestionnaires puissent consulter des données sans requête. Il est rempli à partir des événements, jamais à partir des appels REST que vous faites.

```python
--8<-- "docs/snippets/cache/cache_lookups.py"
```

## Ce qu'il conserve { #what-it-keeps }

| Conservé | Par défaut | Option |
|---|---|---|
| les serveurs, avec leurs rôles, émojis et autocollants | oui | |
| les salons et les fils actifs | oui | |
| les états vocaux | oui | |
| les événements programmés, les instances de conférence et les sons du soundboard | oui | |
| l'utilisateur du bot et son propre membre dans chaque serveur | oui | |
| tous les membres | non | `members=True`, nécessite `Intents.GUILD_MEMBERS` |
| les présences des membres qui ne sont pas hors ligne | non | `presences=True`, nécessite `Intents.GUILD_PRESENCES` |
| les messages les plus récents | non | `max_messages=N` |
| les salons de messages privés les plus récents | non | `max_dm_channels=N` |

`Bot(cache=False)` ne conserve rien, et les autres options lèvent alors `ValueError`. Une option dont l'intent manque lève `MissingIntent` à la création du `Bot`. Les salons sont stockés tels que Discord les a envoyés, sauf les champs que `CHANNEL_UPDATE` ne tient pas à jour (`last_message_id`, `message_count`, `member_count`, `last_pin_timestamp`), qui valent `None`.

Avec `members=True`, Discord n'envoie qu'une partie des membres des grands serveurs à la connexion du bot. bostie demande alors le reste, un serveur à la fois par shard, et un shard n'est considéré comme prêt qu'une fois ses serveurs complets. `chunk_members=False` saute cette étape, et le cache se remplit à mesure que les membres rejoignent le serveur, changent ou parlent. Les requêtes soumises à une limite de débit sont réessayées après le délai indiqué par Discord.

## Consultations { #lookups }

| Méthode | Renvoie |
|---|---|
| `guild(id)`, `guilds()` | un `Guild` ou tous |
| `channel(id)`, `guild_channels(guild_id)` | un `Channel`, ou les salons d'un serveur avec ses fils actifs |
| `role(id)`, `guild_roles(guild_id)` | un `Role`, ou les rôles d'un serveur |
| `voice_state(guild_id, user_id)`, `guild_voice_states(guild_id)` | où se trouvent les utilisateurs en vocal ; `None` quand l'utilisateur n'est dans aucun salon vocal |
| `scheduled_event(id)`, `guild_scheduled_events(guild_id)` | un `ScheduledEvent`, ou ceux d'un serveur |
| `stage_instance(id)`, `guild_stage_instances(guild_id)` | une `StageInstance` en cours, ou celles d'un serveur |
| `soundboard_sound(sound_id)`, `guild_soundboard_sounds(guild_id)` | un `SoundboardSound`, ou ceux d'un serveur |
| `me(guild_id)` | le propre `Member` du bot dans un serveur |
| `member(guild_id, user_id)`, `guild_members(guild_id)` | les membres ; uniquement celui du bot sauf avec `members=True` |
| `presence(guild_id, user_id)`, `guild_presences(guild_id)` | les présences, avec `presences=True` ; `None` si hors ligne |
| `user(id)` | l'utilisateur du bot, ou l'utilisateur d'un membre ou d'un salon de messages privés en cache |
| `dm_channel(user_id)` | le salon de messages privés du bot avec un utilisateur, s'il est conservé |
| `message(id)` | l'un des `max_messages` messages les plus récents |
| `permissions(channel_id, member=None)` | les permissions d'un membre dans un salon, celles du bot quand `member` est omis |
| `guild_permissions(guild_id, member=None)` | la même chose, à l'échelle du serveur |

Les consultations renvoient `None`, ou une liste vide, pour ce que le cache ne contient pas. `bot.cache.clear()` le vide. Les événements de mise à jour et de suppression portent l'objet mis en cache d'avant le changement, comme l'explique [Événements](events.md#old-values-and-the-cache).

## Le cache, sinon REST { #the-cache-else-rest }

```python
--8<-- "docs/snippets/cache/lookups_with_rest.py"
```

`bot.lookup` renvoie un objet depuis le cache quand il le contient, et le demande à REST sinon : `user(user_id)`, `member(guild_id, user_id)`, `channel(channel_id)`, `guild(guild_id)`, `role(guild_id, role_id)` et `message(channel_id, message_id)`. Ce que REST renvoie n'est pas stocké : le cache continue de ne suivre que la gateway, si bien qu'une deuxième consultation du même objet absent refait la demande. Les erreurs REST sont transmises telles quelles, comme `NotFound` pour un id qui n'existe pas.

## Événements programmés, conférences et sons { #scheduled-events-stages-and-sounds }

```python
--8<-- "docs/snippets/cache/guild_events.py"
```

Ils arrivent avec chaque serveur et suivent leurs événements de création, de mise à jour et de suppression, qui nécessitent `Intents.GUILD_SCHEDULED_EVENTS` pour les événements programmés et `Intents.GUILD_EXPRESSIONS` pour les sons du soundboard ; les deux font partie de `Intents.DEFAULT`. La réponse à `bot.request_soundboard_sounds` remplace les sons d'un serveur. Leurs événements de mise à jour portent `old`, et `SoundboardSoundDelete` porte le `sound` supprimé.

## Démarrage { #startup }

Un shard est prêt une fois que chaque serveur listé dans son `READY` est arrivé sous forme de `GuildCreate`, ou une fois qu'aucun serveur n'est arrivé depuis `guild_ready_timeout` secondes, 2 par défaut. `Started` attend chaque shard. Un `GuildCreate` avec `joined=True` est un serveur auquel le bot vient d'être ajouté ; les autres sont des serveurs qui deviennent disponibles.

`large_threshold`, de 50 à 250 et 50 par défaut, est le nombre de membres au-delà duquel Discord envoie un serveur sans ses membres hors ligne.

## Permissions { #permissions }

`cache.permissions(channel_id, member)` applique les règles de Discord : le propriétaire du serveur et les administrateurs ont tout ; sinon le rôle `@everyone`, puis les rôles du membre, puis les surcharges de permissions du salon pour `@everyone`, les rôles et le membre ; un membre exclu temporairement ne garde que Voir les salons et Voir les anciens messages ; sans Voir les salons, il n'y a rien ; sans Envoyer des messages, les permissions qui en dépendent disparaissent ; dans un salon vocal sans Se connecter, les permissions vocales disparaissent. Un fil utilise les surcharges de permissions de son salon parent. La méthode renvoie `None` quand le cache n'a pas le salon, le serveur ou le parent d'un fil.

Le même calcul est disponible sans le cache : `bostie.permissions.compute_permissions(guild, member, channel=None, *, parent=None)`, où `parent` est le salon parent d'un fil. Ce module contient aussi des ensembles `Permissions` tirés de la documentation de Discord : `ALL`, `NONE`, `TEXT`, `VOICE` et `STAGE` (les permissions qui s'appliquent à chaque type de salon), `ELEVATED` (celles qui exigent l'authentification à deux facteurs sur le compte du modérateur quand le serveur l'impose) et `TIMED_OUT` (celles qu'un membre exclu temporairement conserve).

Dans une interaction, le cache est inutile : `ctx.interaction.app_permissions` contient les permissions du bot dans le salon, et `ctx.member.permissions` celles de l'utilisateur.

## Hiérarchie des rôles { #role-hierarchy }

Un membre ne peut expulser, bannir, exclure temporairement ou modifier que les membres dont le rôle le plus élevé est inférieur au sien, et Discord affiche le nom d'un membre dans la couleur de son rôle coloré le plus élevé. `bostie.permissions` propose trois fonctions utilitaires, qui prennent le `Guild` et les membres depuis le cache, une interaction ou REST :

| Fonction | Renvoie |
|---|---|
| `top_role(guild, member)` | le `Role` le plus élevé du membre, ou `None` quand il n'a que `@everyone` |
| `display_colors(guild, member)` | les `RoleColors` de son rôle le plus élevé qui a une couleur, ou `None` |
| `can_act_on(guild, actor, target)` | si `actor` est au-dessus de `target`, un membre ou un rôle : le propriétaire est au-dessus de tout le monde, personne n'est au-dessus du propriétaire, et sinon le rôle le plus élevé de l'acteur est comparé par position au rôle le plus élevé de la cible, ou au rôle lui-même, comme le fait Discord pour la gestion des rôles |

Les rôles à la même position sont classés par id, le plus ancien en premier, comme Discord les ordonne.

```python
--8<-- "docs/snippets/cache/role_hierarchy.py"
```
