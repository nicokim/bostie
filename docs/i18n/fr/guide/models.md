---
source: 8bfaf791038898b7b25fb584ff4496c345a14fcb
---

# Modèles et enums

Tout ce que bostie décode depuis Discord est un modèle : `Message`, `Guild`, `Member`, `Interaction` et environ deux cents autres, tous importables depuis `bostie`. Les modèles sont des structs `msgspec` figées, décodées directement depuis les octets JSON.

```python
--8<-- "docs/snippets/models/inspect_message.py"
```

## Les types suivent la documentation de Discord { #types-follow-discords-documentation }

Chaque champ est typé comme la documentation de Discord le décrit :

- Un champ que Discord envoie toujours n'a pas de valeur par défaut et ne manque jamais. Si la documentation indique qu'il peut valoir `null`, il est typé `T | None`.
- Un champ que la documentation marque comme optionnel a une valeur par défaut : `()` pour une liste, un ensemble de flags vide pour des flags, `False` pour un booléen dont l'absence signifie faux, et `None` sinon.
- Les id sont des `int`, les horodatages sont des objets `datetime` en UTC avec fuseau horaire, et les couleurs sont des `int`.
- Là où Discord envoie un objet partiel, le champ a un modèle partiel : `PartialUser`, `PartialMember`, `PartialChannel`, `PartialMessage`, `PartialEmoji`, `PartialApplication`, `PartialIntegration`, `PartialEntitlement`, `UnavailableGuild`. Un modèle partiel n'a que les champs du payload partiel, si bien qu'un champ absent est une erreur de type, et non un `None` à l'exécution.

Trois endroits où un même payload a deux formes reçoivent deux classes :

- Un message envoyé par un webhook, réponses aux interactions comprises, est un `WebhookMessage`, dont l'`author` est un `PartialUser`. Tout autre message est un `Message` avec un auteur `User` complet. Les champs qui acceptent les deux sont typés `Message | WebhookMessage`.
- Un membre résolu depuis une option, un menu de sélection ou la cible d'une commande est un `ResolvedMember`, qui porte son `user` et n'a ni `deaf` ni `mute`. Un `Member` venant de la gateway ou de REST a les deux.
- Un utilisateur de `message.mentions` est un `MentionedUser` : un `User` avec le `member` que Discord ajoute quand le message a été envoyé sur un serveur.

Les interactions sont des `CommandInteraction`, `ComponentInteraction`, `ModalSubmitInteraction` ou `AutocompleteInteraction` selon leur type, chacune avec des `data` typées.

## Résistance aux changements { #change-resilience }

Discord ajoute des champs, des valeurs d'enum et des types de composants sans prévenir, et un bot ne doit pas casser quand cela arrive :

- Les champs que bostie ne connaît pas sont ignorés au décodage.
- Les enums associent une valeur inconnue à un membre stable nommé `UNKNOWN_<value>`, égal à cette valeur : `MessageType(99).name == "UNKNOWN_99"`.
- Les flags conservent les bits que bostie ne connaît pas, à travers tous les opérateurs, `~` compris.
- Un type de composant inconnu se décode en `UnknownComponent`, qui garde son JSON brut et est renvoyé tel quel quand vous le passez dans `components` ; un type d'interaction inconnu se décode en `UnknownInteraction`, qui atteint les écouteurs de `InteractionCreate` mais aucune commande ni aucune route.
- Un message imbriqué, comme `message.referenced_message` ou le message d'une interaction de composant, n'est décodé que lorsque vous lisez la propriété, si bien qu'un message défectueux n'échoue que là où il est lu.

Un payload d'événement qui ne correspond pas du tout à sa classe, par exemple parce qu'un champ obligatoire manque, est journalisé une fois et livré uniquement aux écouteurs de `RawEvent`, comme l'explique [Événements](events.md#raw-events-and-events-bostie-does-not-model).

## Payloads bruts { #raw-payloads }

Un modèle décodé depuis un payload entier le conserve : `model.raw_json` contient les octets et `model.raw` les décode en un nouveau `dict`. Les modèles imbriqués, comme l'auteur d'un message, ont `raw_json = None`. Chaque événement a aussi `raw` et `raw_json`. Ils donnent accès à tout champ que Discord envoie avant que bostie ne le modélise.

Le `repr` d'un modèle omet `raw_json` et les secrets : `token`, `access_token`, `refresh_token` et `id_token`.

## Construire et copier des modèles { #building-and-copying-models }

Les modèles sont immuables et se construisent avec des arguments nommés. `msgspec.structs.replace(model, field=value)` renvoie une copie dont certains champs ont changé. Les modèles que vous envoyez à Discord, comme `Embed`, `AllowedMentions`, `PollCreateRequest`, `MessageReference`, `ForumTag`, `RecurrenceRule` et `AutoModerationAction`, omettent les champs que vous n'avez pas définis, si bien qu'un modèle décodé est renvoyé tel qu'il est arrivé.

## Utilitaires des modèles { #helpers-on-models }

| Utilitaire | Valeur |
|---|---|
| `user.mention`, `member.mention`, `role.mention`, `channel.mention` | le texte de la mention, comme `<@123>` |
| `user.display_name`, `member.display_name` | le nom global ou le nom d'utilisateur ; pour un membre, le pseudo d'abord |
| `message.jump_url` | le lien vers le message |
| `message.author_member` | l'auteur sous forme de `ResolvedMember`, quand le message porte le membre |
| `role.permissions`, `overwrite.allow`, `overwrite.deny` | des flags `Permissions` |
| `member.permissions` | les permissions d'un membre dans le salon de l'interaction, quand Discord les a envoyées |
| `webhook.url` | l'URL du webhook, quand il a un token |

Le [guide de mise en forme](formatting.md) couvre les mentions, les horodatages et les URL du CDN construites à partir d'id.

## Snowflakes { #snowflakes }

Les id Discord encodent le moment où l'objet a été créé. Chaque modèle doté d'un id snowflake a `created_at`, ce moment sous forme de `datetime` UTC : `message.created_at`, `member.created_at` (celui du compte), `emoji.created_at` (`None` pour un émoji Unicode). `bostie.snowflake_time(id)` fait de même pour un id seul, et `bostie.time_snowflake(when)` renvoie le plus petit id créé à un instant donné, ou avec `high=True` le plus grand, pour paginer par date. Un `datetime` naïf est lu comme une heure locale.

## Enums { #enums }

`bostie` exporte les enums et les flags de Discord : `ChannelType`, `MessageType`, `Permissions`, `Intents`, `MessageFlags`, `Locale`, `ButtonStyle` et les autres de la [référence des enums](../reference/api/enums.md). Les flags se combinent avec `|` et se testent avec `in` : `Permissions.SEND_MESSAGES in member_permissions`. `message.type.deletable` indique si Discord permet de supprimer un message de ce type : les notifications de groupe MP et d'appel, les changements de nom et d'icône de salon et le message de départ d'un fil ne peuvent pas l'être. `JSONErrorCode` nomme les codes d'erreur de Discord, tels que `HTTPException.code` les rapporte.
