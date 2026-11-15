---
source: f4afb2ae41854fb4e97a63590ca4fa1760172438
---

# Mise en forme, couleurs et URL du CDN

Trois petits modules construisent le texte et les URL que Discord affiche : `bostie.fmt` pour le markdown, les mentions et les horodatages, `bostie.color` pour les couleurs, et `bostie.cdn` pour les URL d'images.

```python
--8<-- "docs/snippets/formatting/profile_card.py"
```

## Mentions, horodatages et liens { #mentions-timestamps-and-links }

| Fonction | Renvoie | Ce que Discord affiche |
|---|---|---|
| `fmt.user(user_id)` | `<@id>` | l'utilisateur, en le notifiant |
| `fmt.role(role_id)` | `<@&id>` | le rôle, en le notifiant |
| `fmt.channel(channel_id)` | `<#id>` | un lien vers le salon |
| `fmt.command(name, command_id)` | `</name:id>` | une commande cliquable ; `name` peut contenir un groupe et une sous-commande |
| `fmt.emoji(name, emoji_id, animated=False)` | `<:name:id>` | un émoji personnalisé |
| `fmt.game_profile(game_id)` | `<@$id>` | un profil de jeu |
| `fmt.timestamp(when, style=None)` | `<t:seconds:style>` | une heure dans le fuseau horaire et la langue de chaque lecteur |
| `fmt.guild_navigation(kind, role_id=None)` | `<id:kind>` | un lien vers un onglet du serveur : `"customize"`, `"browse"`, `"guide"` ou `"linked-roles"` ; seul `"linked-roles"` prend un rôle |
| `fmt.store(application_id, sku_id=None)` | une URL de boutique | la page de boutique de l'application, ou un seul SKU, sous forme de carte |

`fmt.timestamp` prend un `datetime` ou des secondes Unix. Ses styles sont ceux de Discord : `"t"` 16:20, `"T"` 16:20:30, `"d"` 20/04/2021, `"D"` April 20, 2021, `"f"` April 20, 2021 at 16:20 (le style par défaut de Discord quand aucun n'est donné), `"F"` la même chose avec le jour de la semaine, `"s"` 20/04/2021, 16:20, `"S"` avec les secondes, et `"R"` relatif, par exemple « dans 5 minutes ». Sur les modèles, `user.mention`, `role.mention` et `channel.mention` donnent le même texte que les fonctions de mention, et `bot.command_mention` cherche l'id d'une commande pour vous ([Enregistrer les commandes](registration.md#command-mentions)).

## Texte venant des utilisateurs { #text-from-users }

- `fmt.escape_markdown(text)` fait apparaître le markdown tel qu'il a été tapé, en gardant les liens fonctionnels.
- `fmt.remove_markdown(text)` le retire.
- `fmt.escape_mentions(text)` neutralise `@everyone`, `@here` et les mentions d'utilisateurs et de rôles pour qu'elles ne notifient personne, en insérant une espace sans chasse.
- `fmt.mentioned_users(text)`, `fmt.mentioned_roles(text)` et `fmt.mentioned_channels(text)` renvoient les id mentionnés dans un texte.

L'échappement n'est qu'une couche ; [`allowed_mentions`](responses.md#allowed-mentions) décide de ce qui peut notifier, quoi que dise le texte.

## Couleurs { #colors }

`bostie.color` contient les couleurs de la marque Discord sous forme d'`int` : `BLURPLE`, `GREEN`, `YELLOW`, `FUCHSIA` et `RED`. `color.rgb(red, green, blue)` prend trois valeurs de 0 à 255, `color.hsv(hue, saturation, value)` trois valeurs de 0 à 1, et `color.parse("#5865F2")` lit six chiffres hexadécimaux, avec `#`, `0x` ou aucun des deux. Le `color` des embeds, l'`accent_color` des conteneurs et les rôles acceptent ces valeurs.

## URL du CDN { #cdn-urls }

`bostie.cdn` construit les URL des images que Discord stocke. Chaque fonction prend les id et le hash de l'image depuis un modèle, plus `size`, une puissance de deux de 16 à 4096, et `format`, parmi `"png"`, `"jpg"`, `"jpeg"`, `"webp"` (la valeur par défaut) ou `"gif"`. Une image animée, dont le hash commence par `a_`, reste animée en WebP ou en GIF ; `"gif"` pour une image fixe, ou une taille hors de la liste, lève `ValueError`. `await bot.http.read(url)` en télécharge une, comme l'explique le [guide REST](rest.md#downloading-files).

| Fonction | Image |
|---|---|
| `display_avatar(user)` | l'avatar de l'utilisateur, ou l'avatar par défaut s'il n'en a pas |
| `user_avatar(user_id, avatar)`, `default_avatar(user_id)` | l'avatar propre ou par défaut d'un utilisateur |
| `user_banner(user_id, banner)`, `avatar_decoration(asset)` | la bannière et la décoration d'avatar d'un utilisateur |
| `member_avatar(guild_id, user_id, avatar)`, `member_banner(...)` | l'avatar et la bannière propres au serveur d'un membre |
| `guild_icon`, `guild_splash`, `guild_discovery_splash`, `guild_banner` | les images d'un serveur, à partir de `guild_id` et du hash |
| `guild_tag_badge(guild_id, badge)` | le badge d'un tag de serveur |
| `role_icon(role_id, icon)` | l'icône d'un rôle |
| `emoji(emoji_id, animated=False)` | un émoji personnalisé |
| `sticker(sticker_id, format_type)` | un autocollant en PNG, Lottie JSON ou GIF, selon son type de format |
| `sticker_pack_banner(banner_asset_id)` | la bannière d'un pack d'autocollants |
| `scheduled_event_cover(event_id, image)` | la couverture d'un événement programmé |
| `application_icon`, `application_cover`, `application_asset`, `store_page_asset` | l'icône, la couverture, les ressources Rich Presence et les images de boutique d'une application |
| `team_icon(team_id, icon)` | l'icône d'une équipe |
| `soundboard_sound(sound_id)` | l'audio d'un son de la soundboard |
