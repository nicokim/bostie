---
source: 606da1a817f7cceca11df3edf6dda3b5da72000b
---

# Fenêtres modales

![Bostie remplissant un formulaire et l'envoyant](../assets/mascot/bostie-modals.webp){ align=right width="180" loading=lazy }

Une fenêtre modale est un formulaire qui s'ouvre par-dessus le salon. Une commande ou un composant répond en en ouvrant une, et l'envoi de l'utilisateur arrive sous forme d'une nouvelle interaction, que traite une route de fenêtre modale.

```python
--8<-- "docs/snippets/modals/application_form.py"
```

`ui.modal(custom_id, title, *components)` prend un titre de 1 à 45 caractères et un à cinq composants : des champs `ui.label(...)` et des paragraphes `ui.text(...)`. `await ctx.modal(...)` l'ouvre ; ce doit être la première réponse de l'interaction, et plus rien ne peut répondre à cette interaction ensuite.

`@bot.on_modal(template)` route l'envoi comme [`on_component`](components.md#routes) route les clics : mêmes templates avec les espaces réservés `{name}` et `{name:int}`, même `route.id(...)`, mêmes réglages `ephemeral`, `auto_defer` et `checks`.

## Champs { #fields }

`ui.label(label, component, description=None)` place un libellé d'au plus 45 caractères et une description d'au plus 100 au-dessus d'une saisie :

| Saisie | Constructeur | Lire avec |
|---|---|---|
| une zone de texte | `ui.text_input(custom_id, style=TextInputStyle.SHORT)` | `ctx.text(custom_id)`, un `str` |
| un menu de sélection d'options | `ui.string_select(custom_id, options)` | `ctx.values(custom_id)` |
| un menu de sélection d'utilisateurs, de rôles, des deux, ou de salons | `ui.user_select`, `ui.role_select`, `ui.mentionable_select`, `ui.channel_select` | `ctx.users`, `ctx.members`, `ctx.roles`, `ctx.channels`, ou `ctx.ids` |
| des envois de fichiers | `ui.file_upload(custom_id)` | `ctx.attachments(custom_id)`, les objets `Attachment` envoyés |
| un choix parmi 2 à 10 | `ui.radio_group(custom_id, options)` | `ctx.choice(custom_id)`, la valeur ou `None` |
| plusieurs choix parmi 1 à 10 | `ui.checkbox_group(custom_id, options)` | `ctx.values(custom_id)` |
| une case oui ou non | `ui.checkbox(custom_id, default=False)` | `ctx.checked(custom_id)`, un `bool` |

Toutes les saisies sauf la case à cocher acceptent `required`, `True` par défaut, et `min_values=0` exige `required=False`. `text_input` accepte `style=TextInputStyle.PARAGRAPH` pour plusieurs lignes, `placeholder`, `value` pour la préremplir, `min_length` (0 à 4000) et `max_length` (1 à 4000). `file_upload` accepte `min_values` et `max_values`, jusqu'à 10 fichiers, et `file_types` comme `["image", ".pdf"]`. Les options des groupes de boutons radio et de cases à cocher sont des `ui.group_option(label, value, description=None, default=False)` ; un groupe de cases à cocher sans `max_values` permet à l'utilisateur de toutes les cocher.

Les constructeurs renvoient `ui.ModalDict`, `ui.LabelDict`, `ui.TextInputDict`, `ui.FileUploadDict`, `ui.RadioGroupDict`, `ui.CheckboxGroupDict`, `ui.CheckboxDict` et `ui.GroupOptionDict`. Les méthodes de lecture de `ModalContext` prennent le custom id de la saisie. Un id que la fenêtre modale ne contient pas lève `KeyError`, et lire une saisie d'un autre type, comme `ctx.text` sur une case à cocher, lève `TypeError`.

## Mettre à jour le message qui a ouvert la fenêtre modale { #updating-the-message-that-opened-the-modal }

Une fenêtre modale ouverte depuis un composant connaît le message qui porte le composant, et son envoi peut modifier ce message :

```python
--8<-- "docs/snippets/modals/settings_modal.py"
```

`await ctx.update(...)` et `await ctx.defer_update()` fonctionnent sur l'envoi comme sur un clic. Sur une fenêtre modale ouverte depuis une commande, il n'y a aucun message à mettre à jour, et ils lèvent `InteractionResponded` ; répondez plutôt avec `respond`. Un envoi resté sans réponse est différé au bout de `auto_defer` secondes, avec `defer_update` quand il y a un message.

## Attendre l'envoi dans un gestionnaire { #waiting-for-the-submit-inside-a-handler }

```python
--8<-- "docs/snippets/modals/inline_modal.py"
```

`await bot.wait_for_modal(*custom_ids, check=None)` renvoie le `ModalContext` du prochain envoi dont le custom id fait partie de `custom_ids`, ou de n'importe quelle fenêtre modale si aucun n'est donné, et aucune route ne s'exécute pour lui. Mettez quelque chose d'unique dans le custom id, comme l'id de l'interaction, pour que deux utilisateurs qui lancent la commande en même temps reçoivent chacun leur propre envoi. Un utilisateur peut fermer une fenêtre modale sans l'envoyer, et Discord n'en dit rien au bot : bornez donc l'attente avec `asyncio.timeout()`.
