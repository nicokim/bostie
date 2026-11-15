---
source: d0cf09522c13a747c853db29f2b054fe520abbea
---

# Composants

![Bostie appuyant sur un gros bouton](../assets/mascot/bostie-components.webp){ align=right width="180" loading=lazy }

Les composants sont les boutons, les menus de sélection et les blocs de mise en page d'un message. `bostie.ui` les construit sous forme de simples dictionnaires au format JSON de Discord, et vérifie les limites de Discord au moment de les construire. Un gestionnaire enregistré avec `@bot.on_component` répond à leurs utilisations.

`bostie.ui` est la façon de construire des composants. Les modèles de composants, comme `bostie.Button` ou `bostie.ActionRow`, sont ce que contiennent les `components` d'un message décodé ; `components=` les accepte aussi, si bien que les composants d'un message peuvent être renvoyés, modifiés avec `msgspec.structs.replace`, sans être reconstruits, mais ils ne vérifient pas les limites de Discord.

## Mise en page { #layout }

```python
--8<-- "docs/snippets/components/layout.py"
```

Ce sont des Components V2 : les composants forment le message entier, avec `ui.text` à la place de `content`, comme l'explique [Contenu des messages](messages.md#components-v2).

| Constructeur | Ce qu'il affiche |
|---|---|
| `ui.text(markdown)` | du texte, avec le markdown de Discord |
| `ui.section(*texts, accessory=...)` | un à trois textes avec un bouton ou une miniature à côté |
| `ui.thumbnail(url, description=None, spoiler=False)` | une petite image, uniquement comme accessoire d'une section |
| `ui.media_gallery(*items)` | une à dix images ou vidéos, chacune un `ui.gallery_item(url, description=None, spoiler=False)` |
| `ui.file(filename, spoiler=False)` | un fichier envoyé avec le message sous ce nom |
| `ui.separator(divider=True, spacing=1)` | un espace entre composants, `spacing=2` pour un grand, avec une ligne sauf si `divider=False` |
| `ui.container(*components, accent_color=None, spoiler=False)` | un conteneur autour de rangées, textes, sections, galeries, fichiers et séparateurs, avec un bord coloré |
| `ui.row(*components)` | jusqu'à cinq boutons, ou un menu de sélection |

Une URL peut être `attachment://name` pour afficher un fichier envoyé avec le message. Chaque constructeur accepte `id=` pour l'id numérique facultatif du composant, et `ui.raw(dict)` transmet tel quel un composant pour lequel `ui` n'a pas encore de constructeur. Trop d'éléments, un libellé trop long ou un menu de sélection dans une rangée avec des boutons lèvent `ValueError` dès la construction. Chaque constructeur renvoie un dictionnaire typé, comme `ui.ButtonDict`, `ui.RowDict`, `ui.TextDict`, `ui.SectionDict`, `ui.ThumbnailDict`, `ui.MediaGalleryDict`, `ui.GalleryItemDict`, `ui.FileDict`, `ui.SeparatorDict`, `ui.ContainerDict`, `ui.StringSelectDict`, `ui.SelectOptionDict`, `ui.EntitySelectDict` et `ui.SelectDefaultDict`, et `ui.raw` renvoie un `ui.RawComponent` ; annotez vos propres utilitaires avec eux, comme le fait `ballot` ci-dessous.

## Boutons { #buttons }

- `ui.button(label, custom_id=..., style=ButtonStyle.SECONDARY)` envoie `custom_id` au bot lors d'un clic. `style` vaut `PRIMARY`, `SECONDARY`, `SUCCESS` ou `DANGER`.
- `ui.link_button(label, url)` ouvre une URL et n'atteint jamais le bot.
- `ui.premium_button(sku_id)` met en vente un SKU de l'application et n'atteint jamais le bot.

Les boutons acceptent `emoji=` : un émoji Unicode, un émoji personnalisé écrit comme Discord l'affiche (`<:name:id>`, ou `<a:name:id>` s'il est animé, tel que `fmt.emoji` le produit), ou un modèle `Emoji` ou `PartialEmoji`. `PartialEmoji.parse(text)` lit une telle chaîne. Les boutons acceptent aussi `disabled=True`. Un libellé fait au plus 80 caractères, et un custom id de 1 à 100.

## Routes { #routes }

Une route est un gestionnaire pour tous les custom ids qui correspondent à un template :

```python
--8<-- "docs/snippets/components/routes.py"
```

`"vote:{side:int}:{topic}"` correspond à `vote:0:Lunch at noon`. `{name}` capture un ou plusieurs caractères sans saut de ligne, et `{name:int}` un entier, négatifs compris. Les valeurs arrivent au gestionnaire en arguments nommés, converties pour `int`. Un template exige du texte littéral entre deux espaces réservés, et un template sans espace réservé correspond exactement à son custom id.

`vote.id(side=0, topic=topic)` construit le custom id à partir de la route elle-même, si bien que le template n'est écrit qu'une fois. Il lève `ValueError` si l'id dépasse les 100 caractères de Discord, ou si sa relecture ne redonne pas les mêmes valeurs.

L'état réside dans le custom id, pas dans un objet Python : un bouton envoyé la semaine dernière fonctionne donc encore après un redémarrage, tant que sa route est enregistrée. `@bot.on_component` accepte `ephemeral`, `auto_defer` et `checks`, comme une commande, et appeler la route, comme dans `await vote(ctx, side=0, topic="x")`, appelle le gestionnaire.

Une utilisation de composant qu'aucune route ne reconnaît ne reçoit aucune réponse : Discord affiche « Cette interaction a échoué » et bostie consigne un avertissement, sauf si vous écoutez `InteractionCreate`.

## Répondre à un composant { #answering-a-component }

Un `ComponentContext` dispose des méthodes de réponse du contexte d'une commande, plus :

- `await ctx.update(**parts)` modifie le message qui porte le composant. Les parties omises restent, et `None` en efface une.
- `await ctx.defer_update()` accuse réception de l'utilisation sans réponse visible ; `update` peut modifier le message pendant 15 minutes.
- `await ctx.modal(ui.modal(...))` ouvre une [fenêtre modale](modals.md).

`respond` envoie un nouveau message, comme pour une commande. Une utilisation restée sans réponse pendant `auto_defer` secondes est différée avec `defer_update`.

## Menus de sélection { #select-menus }

```python
--8<-- "docs/snippets/components/selects.py"
```

| Constructeur | L'utilisateur choisit | Lire les choix avec |
|---|---|---|
| `ui.string_select(custom_id, options)` | des options construites avec `ui.select_option(label, value)` | `ctx.values`, les valeurs |
| `ui.user_select(custom_id)` | des utilisateurs | `ctx.users`, et `ctx.members` dans un serveur |
| `ui.role_select(custom_id)` | des rôles | `ctx.roles` |
| `ui.mentionable_select(custom_id)` | des utilisateurs et des rôles | `ctx.users`, `ctx.members`, `ctx.roles` |
| `ui.channel_select(custom_id, channel_types=...)` | des salons, des types donnés uniquement | `ctx.channels` |

`ctx.ids` contient les ids choisis de n'importe quel menu de sélection d'entités. Lire le mauvais type, comme `ctx.values` sur un menu de sélection d'utilisateurs, lève `TypeError`.

Chaque menu de sélection accepte `placeholder` (jusqu'à 150 caractères), `min_values` et `max_values` (1 par défaut, 25 au plus) et `disabled`. Un menu de sélection de chaînes a de 1 à 25 options ; `select_option` accepte `description`, `emoji` comme les boutons, et `default=True` pour l'afficher comme choisie. Les menus de sélection d'entités acceptent `defaults`, les ids affichés comme choisis, et le menu de sélection de mentionnables accepte `default_users` et `default_roles`. `required` ne compte que dans une [fenêtre modale](modals.md).

## Pages { #pages }

`ui.pager(route, page, count)` renvoie une rangée de trois boutons pour une route avec un espace réservé `{page:int}` : précédent, un libellé désactivé `page / count`, et suivant. La première page est 0, et les arguments nommés supplémentaires remplissent les autres espaces réservés de la route. Comme le numéro de page est dans le custom id, la pagination survit aux redémarrages. La commande `/tour` de l'[exemple de composants](../examples.md#components) l'utilise.

## Attendre un clic dans un gestionnaire { #waiting-for-a-click-inside-a-handler }

Une commande peut attendre une utilisation d'un composant sans route :

```python
--8<-- "docs/snippets/components/confirm.py"
```

`await bot.wait_for_component(*custom_ids, check=None)` renvoie le `ComponentContext` de la prochaine utilisation dont le custom id fait partie de `custom_ids`, ou de n'importe quel composant si aucun n'est donné, et pour laquelle `check` renvoie vrai. Aucune route ne s'exécute pour une utilisation qu'il renvoie. Il attend indéfiniment, alors bornez-le avec `asyncio.timeout()` ; une exception levée par `check` est levée ici. Il accepte `ephemeral` et `auto_defer` pour le contexte qu'il renvoie.
