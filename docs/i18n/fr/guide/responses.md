---
source: cbf9d472ef0e0c0f610b3352754ad6482b93f093
---

# Répondre aux interactions

Chaque commande slash, commande de menu contextuel, bouton, menu de sélection et envoi de fenêtre modale est une interaction, et Discord attend que l'application y réponde. Un gestionnaire de commande reçoit un `Context`, un gestionnaire de composant un `ComponentContext` et un gestionnaire de fenêtre modale un `ModalContext` ; ils partagent les méthodes de réponse ci-dessous.

## Ce que sait un contexte { #what-a-context-knows }

| Attribut | Valeur |
|---|---|
| `ctx.user` | le `User` qui l'a déclenchée, sur un serveur ou en message privé |
| `ctx.member` | son `Member` sur un serveur, sinon `None` |
| `ctx.guild_id`, `ctx.channel_id` | où elle a eu lieu ; `guild_id` vaut `None` en message privé |
| `ctx.channel` | le `PartialChannel` que transporte l'interaction |
| `ctx.locale` | la langue du client de l'utilisateur |
| `ctx.interaction` | le modèle complet de l'interaction, pour tout le reste, comme `app_permissions` ou `guild_locale` |
| `ctx.bot` | le bot |
| `ctx.voice` | la [voix](../voice/index.md) pour ce serveur et cet utilisateur |
| `ctx.command_name` | sur `Context`, la commande avec ses sous-commandes, par exemple `"roles color set"` |

## Répondre, modifier, envoyer une suite { #answer-edit-follow-up }

```python
--8<-- "docs/snippets/responses/answer_flow.py"
```

`ctx.respond(...)` envoie la première réponse. Appelée de nouveau, elle envoie à la place un message de suivi, si bien qu'un gestionnaire peut l'appeler aussi souvent qu'il le faut. Elle renvoie le message que Discord a créé, un `WebhookMessage`.

- `ctx.edit(**parts)` modifie la première réponse, et `ctx.edit(message_id, **parts)` un message de suivi.
- `ctx.followup(...)` envoie toujours un nouveau message, sauf juste après `defer()`, où il complète la réponse différée.
- `ctx.fetch()` et `ctx.fetch(message_id)` renvoient la réponse ou un message de suivi tel que Discord l'a en ce moment.
- `ctx.delete()` et `ctx.delete(message_id)` les suppriment.

`respond` et `followup` acceptent `content` et les parties listées dans [Contenu des messages](messages.md) : `embeds`, `components`, `files`, `allowed_mentions`, `flags`, `tts`, `poll`, plus `ephemeral`. Une modification accepte les mêmes parties par mot-clé : une partie que vous omettez reste telle quelle, et `None` l'efface.

## Messages qui se suppriment eux-mêmes { #messages-that-delete-themselves }

```python
--8<-- "docs/snippets/responses/short_lived.py"
```

`delete_after=` sur `respond`, `followup` et `bot.rest.messages.create` supprime le message envoyé ce nombre de secondes plus tard. Le bot effectue la suppression en arrière-plan et l'annule à sa fermeture, si bien qu'un message dont la suppression tombe après l'arrêt du bot reste. Les messages d'une interaction sont supprimés via son token, ce qui fonctionne aussi pour les messages éphémères mais prend fin 15 minutes après l'interaction ; une suppression prévue plus tard échoue avec un avertissement dans le journal. Un message que quelqu'un a déjà supprimé est ignoré sans bruit.

## Réponses éphémères { #ephemeral-answers }

`ephemeral=True` sur un appel rend ce message visible uniquement par l'utilisateur. `ephemeral=True` sur une commande, une route de composant, une route de fenêtre modale ou un routeur en fait la valeur par défaut pour leurs réponses, et un appel peut toujours passer `ephemeral=False`. Un message existant ne peut pas passer d'éphémère à public ni l'inverse : une modification qui le demande consigne un avertissement une fois et laisse le message tel quel.

## Différer { #deferring }

Une première réponse doit parvenir à Discord dans les 3 secondes. `await ctx.defer()` affiche « réfléchit… » à sa place, et le gestionnaire a ensuite 15 minutes pour appeler `respond` ; `defer(ephemeral=True)` rend éphémère la réponse à venir.

```python
--8<-- "docs/snippets/responses/deferring.py"
```

bostie diffère aussi de lui-même : une utilisation toujours sans réponse `auto_defer` secondes après son arrivée est différée, 2,5 secondes par défaut. Un composant ou une fenêtre modale ouverte depuis un message est différé avec `defer_update`, qui laisse le message tel quel jusqu'à ce que `update` le modifie. `auto_defer` se règle sur le bot, un routeur, une commande ou une route, et `None` le désactive à cet endroit. Le minuteur part du moment où la gateway a livré l'interaction, si bien qu'une boucle d'événements bloquée retarde le gestionnaire mais pas l'échéance ; bostie consigne un avertissement quand un gestionnaire démarre avec plus d'une seconde de retard.

## Fenêtres modales et Activités { #modals-and-activities }

`await ctx.modal(ui.modal(...))` répond par un formulaire dans une fenêtre modale ; voir [Fenêtres modales](modals.md). `await ctx.launch_activity()` ouvre l'Activité de l'application et renvoie l'id de son instance. Les deux doivent être la première réponse, et plus rien ne peut répondre à l'interaction après une fenêtre modale : l'envoi de la fenêtre modale est une nouvelle interaction.

## Les règles, et les erreurs quand on les enfreint { #the-rules-and-the-errors-for-breaking-them }

Chaque appel vérifie d'abord l'état de l'interaction :

| Appel | Avant toute réponse | Après `defer()` | Après `defer_update()` | Après une réponse |
|---|---|---|---|---|
| `respond` | répond | complète la réponse | envoie un message de suivi | envoie un message de suivi |
| `defer` | diffère | rien | rien | `InteractionResponded` |
| `edit`, `delete` | `InteractionNotAnswered` | modifie, supprime la réponse | modifie, supprime le message | modifie, supprime la réponse |
| `followup` | `InteractionNotAnswered` | complète la réponse | envoie un message de suivi | envoie un message de suivi |
| `update` | modifie le message | `InteractionResponded` | modifie le message | `InteractionResponded` |
| `modal`, `launch_activity` | répond | `InteractionResponded` | `InteractionResponded` | `InteractionResponded` |

`update` et `defer_update` n'existent que sur les contextes de composant et de fenêtre modale. Tout appel fait après une fenêtre modale lève `InteractionResponded`.

Un token d'interaction vit 15 minutes. Tout appel passé ce délai, ou après que Discord a déclaré le token invalide, lève `InteractionExpired` sans rien envoyer.

Un gestionnaire qui se termine sans répondre laisse l'utilisateur face à « L'application n'a pas répondu », et bostie consigne un avertissement qui nomme le gestionnaire. Un gestionnaire qui a différé sans jamais répondre laisse « réfléchit… » jusqu'à l'expiration du token, avec un avertissement lui aussi. Quand un gestionnaire lève une exception avant d'avoir répondu, bostie répond "Something went wrong.", en éphémère sauf si le gestionnaire avait différé, à moins qu'un [hook d'erreur](checks.md#error-hooks) ne réponde.

Discord autorise 5 messages de suivi pour une interaction d'une application installée uniquement par l'utilisateur, et bostie avertit au sixième.

## Mentions autorisées { #allowed-mentions }

Par défaut, une réponse peut notifier des utilisateurs et des rôles mais pas `@everyone` ni `@here` : `Bot(allowed_mentions=...)` vaut `AllowedMentions(parse=("users", "roles"))`. Un `allowed_mentions` passé avec un message ne change que les types de mention qu'il règle, donc `AllowedMentions(users=(123,))` limite les notifications d'utilisateurs et laisse le réglage des rôles intact. `AllowedMentions(parse=())` rend toutes les mentions silencieuses, et `replied_user=False` empêche une réponse de notifier l'auteur du message auquel elle répond.
