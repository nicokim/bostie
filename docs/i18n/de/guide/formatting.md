---
source: 12cb33d1448fc6df340b3c11d616bf17c9f5b58c
description: "Formatiere Discord-Text mit bostie.fmt: Erwähnungen, Zeitstempel, Markdown-Escaping, Farben mit bostie.color und CDN-URLs für Avatare, Icons und Emojis."
---

# Formatierung, Farben und CDN-URLs

Drei kleine Module bauen den Text und die URLs, die Discord darstellt: `bostie.fmt` für Markdown, Erwähnungen und Zeitstempel, `bostie.color` für Farben und `bostie.cdn` für Bild-URLs.

```python
--8<-- "docs/snippets/formatting/profile_card.py"
```

## Erwähnungen, Zeitstempel und Links { #mentions-timestamps-and-links }

| Funktion | Gibt zurück | Discord zeigt |
|---|---|---|
| `fmt.user(user_id)` | `<@id>` | den Benutzer, und pingt ihn an |
| `fmt.role(role_id)` | `<@&id>` | die Rolle, und pingt sie an |
| `fmt.channel(channel_id)` | `<#id>` | einen Link zum Kanal |
| `fmt.command(name, command_id)` | `</name:id>` | einen anklickbaren Befehl; `name` darf eine Gruppe und einen Unterbefehl enthalten |
| `fmt.emoji(name, emoji_id, animated=False)` | `<:name:id>` | ein eigenes Emoji |
| `fmt.game_profile(game_id)` | `<@$id>` | ein Spielprofil |
| `fmt.timestamp(when, style=None)` | `<t:seconds:style>` | eine Uhrzeit in der Zeitzone und Sprache jedes einzelnen Lesers |
| `fmt.guild_navigation(kind, role_id=None)` | `<id:kind>` | einen Link zu einem Server-Tab: `"customize"`, `"browse"`, `"guide"` oder `"linked-roles"`; nur `"linked-roles"` nimmt eine Rolle |
| `fmt.store(application_id, sku_id=None)` | eine Shop-URL | die Shop-Seite der App oder eine einzelne SKU, als Karte |

`fmt.timestamp` nimmt ein `datetime` oder Unix-Sekunden. Seine Stile sind die von Discord, hier in englischer Darstellung: `"t"` 16:20, `"T"` 16:20:30, `"d"` 20/04/2021, `"D"` April 20, 2021, `"f"` April 20, 2021 at 16:20 (Discords Standard, wenn kein Stil angegeben ist), `"F"` dasselbe mit Wochentag, `"s"` 20/04/2021, 16:20, `"S"` mit Sekunden und `"R"` relativ, etwa „in 5 minutes“. `user.mention`, `role.mention` und `channel.mention` an Modellen liefern denselben Text wie die Erwähnungsfunktionen, und `bot.command_mention` schlägt die ID eines Befehls für dich nach ([Befehle registrieren](registration.md#command-mentions)).

## Text von Benutzern { #text-from-users }

- `fmt.escape_markdown(text)` lässt Markdown so erscheinen, wie es getippt wurde, und lässt Links funktionieren.
- `fmt.remove_markdown(text)` entfernt es.
- `fmt.escape_mentions(text)` bricht `@everyone`, `@here` sowie Benutzer- und Rollenerwähnungen auf, sodass sie nicht anpingen können, indem es ein Leerzeichen ohne Breite einfügt.
- `fmt.mentioned_users(text)`, `fmt.mentioned_roles(text)` und `fmt.mentioned_channels(text)` geben die IDs zurück, die in einem Text erwähnt werden.

Escaping ist nur eine Schicht; [`allowed_mentions`](responses.md#allowed-mentions) entscheidet, was anpingen darf, egal was im Text steht.

## Farben { #colors }

`bostie.color` enthält Discords Markenfarben als `int`: `BLURPLE`, `GREEN`, `YELLOW`, `FUCHSIA` und `RED`. `color.rgb(red, green, blue)` nimmt drei Werte von 0 bis 255, `color.hsv(hue, saturation, value)` drei von 0 bis 1, und `color.parse("#5865F2")` liest sechs Hexadezimalziffern, mit `#`, `0x` oder ohne beides. Das `color` von Embeds, das `accent_color` von Containern und Rollen nehmen diese Werte.

## CDN-URLs { #cdn-urls }

`bostie.cdn` baut die URLs von Bildern, die Discord speichert. Jede Funktion nimmt die IDs und den Bild-Hash aus einem Modell, dazu `size`, eine Zweierpotenz von 16 bis 4096, und `format`, eines von `"png"`, `"jpg"`, `"jpeg"`, `"webp"` (der Standard) oder `"gif"`. Ein animiertes Bild, dessen Hash mit `a_` beginnt, bleibt in WebP oder GIF animiert; `"gif"` für ein Standbild oder eine Größe außerhalb der Liste löst `ValueError` aus. `await bot.http.read(url)` lädt eines herunter, wie die [REST-Anleitung](rest.md#downloading-files) erklärt.

| Funktion | Bild |
|---|---|
| `display_avatar(user)` | der Avatar des Benutzers, oder der Standard-Avatar, wenn er keinen hat |
| `user_avatar(user_id, avatar)`, `default_avatar(user_id)` | der eigene oder der Standard-Avatar eines Benutzers |
| `user_banner(user_id, banner)`, `avatar_decoration(asset)` | das Banner und die Avatar-Dekoration eines Benutzers |
| `member_avatar(guild_id, user_id, avatar)`, `member_banner(...)` | der serverspezifische Avatar und das Banner eines Mitglieds |
| `guild_icon`, `guild_splash`, `guild_discovery_splash`, `guild_banner` | die Bilder eines Servers, aus `guild_id` und dem Hash |
| `guild_tag_badge(guild_id, badge)` | das Abzeichen eines Server-Tags |
| `role_icon(role_id, icon)` | das Symbol einer Rolle |
| `emoji(emoji_id, animated=False)` | ein eigenes Emoji |
| `sticker(sticker_id, format_type)` | ein Sticker als PNG, Lottie-JSON oder GIF, je nach Formattyp |
| `sticker_pack_banner(banner_asset_id)` | das Banner eines Stickerpakets |
| `scheduled_event_cover(event_id, image)` | das Titelbild eines geplanten Events |
| `application_icon`, `application_cover`, `application_asset`, `store_page_asset` | das Symbol, Titelbild, die Rich-Presence-Assets und die Shop-Bilder einer App |
| `team_icon(team_id, icon)` | das Symbol eines Teams |
| `soundboard_sound(sound_id)` | das Audio eines Soundboard-Sounds |
