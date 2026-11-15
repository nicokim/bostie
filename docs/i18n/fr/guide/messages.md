---
source: 9f6f43faa726c1342aeebb13122e1615b76fb827
---

# Contenu des messages

Un message se compose des mêmes parties, où que vous l'envoyiez : `ctx.respond`, `ctx.followup`, `ctx.edit` et `ctx.update`, `bot.rest.messages.create` et `edit`, et le `execute` d'un webhook. Chaque partie est un argument nommé, et une partie omise n'est pas envoyée.

| Partie | Type | Remarques |
|---|---|---|
| `content` | `str` | le premier argument positionnel de `respond`, `followup`, `messages.create` et `execute` |
| `embeds` | objets `Embed` | jusqu'à 10 embeds enrichis |
| `components` | constructeurs `ui` | boutons, menus de sélection et composants de mise en page ; voir [Composants](components.md) |
| `files` | objets `File` | fichiers envoyés ; voir plus bas |
| `allowed_mentions` | `AllowedMentions` | les mentions qui peuvent notifier ; voir [Mentions autorisées](responses.md#allowed-mentions) |
| `flags` | `MessageFlags` | comme `SUPPRESS_EMBEDS` et `SUPPRESS_NOTIFICATIONS` |
| `tts` | `bool` | lit le contenu à voix haute pour quiconque a le salon ouvert |
| `poll` | `PollCreateRequest` | un sondage ; voir plus bas |
| `ephemeral` | `bool` | réponses aux interactions uniquement |

`bot.rest.messages.create` accepte aussi `reply_to`, `message_reference`, `sticker_ids`, `nonce`, `enforce_nonce` et `shared_client_theme`, ainsi que `delete_after`, comme les [réponses aux interactions](responses.md#messages-that-delete-themselves).

## Embeds et fichiers { #embeds-and-files }

```python
--8<-- "docs/snippets/responses/rich_messages.py"
```

`Embed` et ses parties (`EmbedField`, `EmbedFooter`, `EmbedAuthor`, `EmbedMedia`) sont des modèles figés à arguments nommés ; un simple `dict` au format JSON de Discord fonctionne aussi. `color` prend un `int` comme `0x5865F2`, et les utilitaires de [`bostie.color`](formatting.md#colors) en construisent un.

`File(name, data)` envoie `data` sous le nom `name`. `description` est son texte alternatif, `spoiler=True` le floute, et `title` définit le titre que Discord affiche. Un embed ou un composant désigne un fichier envoyé par `attachment://name`, comme le fait le graphique ci-dessus. bostie ne vérifie pas les tailles : la limite d'envoi de Discord dépend des boosts du serveur et du Nitro de l'utilisateur.

## Sondages { #polls }

`PollCreateRequest` prend une `question` et des `answers`, tous deux des `PollMedia` avec `text` et, pour les réponses, un `emoji` facultatif. `duration` s'exprime en heures, 24 si elle est omise, et `allow_multiselect=True` permet à un utilisateur de choisir plusieurs réponses. `bot.rest.polls.end(channel_id, message_id)` clôt un sondage avant l'heure, `bot.rest.polls.list_answer_voters(...)` liste qui a choisi une réponse, et les [événements](events.md) `PollVoteAdd` et `PollVoteRemove` signalent les votes au moment où ils ont lieu.

## Modification et pièces jointes { #editing-and-attachments }

Une modification prend les parties par mot-clé, plus `attachments`. Une partie omise reste en place, et `None` l'efface : `ctx.edit(embeds=None)` retire les embeds et garde le contenu. Quand une modification passe `files` ou `attachments`, le message ne garde que les pièces jointes listées dans `attachments`, données sous forme d'objets `Attachment` ou d'ids, plus les nouveaux fichiers.

## Components V2 { #components-v2 }

Un message dont les `components` contiennent un composant de mise en page (`ui.text`, `ui.section`, `ui.container`, `ui.media_gallery`, `ui.file`, `ui.separator` ou `ui.thumbnail`) est un message Components V2 : bostie active le flag `IS_COMPONENTS_V2` et les composants forment le message entier. Un tel message ne peut avoir ni `content`, ni `embeds`, ni autocollants, ni sondage, et bostie lève `ValueError` si vous les passez ; mettez plutôt le texte dans `ui.text`. Les boutons et menus de sélection placés seuls dans `ui.row` fonctionnent aussi dans les messages classiques avec du contenu.

## Réponses { #replies }

```python
--8<-- "docs/snippets/responses/replies.py"
```

`reply_to` prend l'id d'un message du même salon. Pour transférer un message, passez plutôt `message_reference=MessageReference(type=MessageReferenceType.FORWARD, message_id=..., channel_id=...)` ; les deux ne peuvent pas être combinés. Lire `message.content` dans les serveurs nécessite l'intent privilégié Message Content, comme l'explique le [guide de la gateway](gateway.md#intents).

## Indicateur d'écriture { #typing-indicator }

```python
--8<-- "docs/snippets/responses/typing_indicator.py"
```

`async with ctx.typing():` montre le bot en train d'écrire dans le salon de l'interaction pendant que le bloc s'exécute. bostie envoie l'indicateur au début du bloc, puis de nouveau toutes les 9 secondes, puisque Discord l'affiche pendant 10, et s'arrête à la fin du bloc, y compris quand il se termine par une exception. `await ctx.typing()` l'affiche une seule fois. Hors d'une interaction, `bot.rest.channels.trigger_typing_indicator(channel_id)` fait de même, avec `await` ou dans `async with`. Un message que le bot envoie dans le salon efface aussitôt l'indicateur.

## Messages vocaux { #voice-messages }

```python
--8<-- "docs/snippets/responses/voice_memo.py"
```

`await voice.message(audio)` lit un chemin de fichier ou des octets, mesure la durée, calcule la forme d'onde que Discord dessine, et renvoie un `File` prêt à envoyer. L'extension du nom indique le format ; les clients de Discord enregistrent en Ogg Opus mono à 48 kHz. Un message vocal se limite à ce seul fichier : ni contenu, ni embeds, ni composants, ni sondage, ni autocollants. bostie active le flag `IS_VOICE_MESSAGE`, et lève `ValueError` si le message contient quoi que ce soit d'autre. Un `File` que vous construisez vous-même devient un message vocal quand il a `duration_secs` et `waveform`, les échantillons sur 8 bits que Discord dessine.
