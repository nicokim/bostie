---
source: d85e30b6bb45eff282f70f86375dc49e252a5387
description: "Lisez fichiers, URL et flux HLS en salon vocal Discord : pistes, volume, égaliseur, vitesse, files d'attente sans blanc avec fondu enchaîné, encodeur Opus."
---

# Lecture audio

![Bostie qui hoche la tête en rythme](../assets/mascot/bostie-playback.webp){ align=right width="180" loading=lazy }

`await vc.play(source)` lit une source sur une [connexion vocale](index.md) et renvoie immédiatement une `Track`. Plusieurs pistes jouent en même temps et sont mixées ; une `Queue` les joue l'une après l'autre.

```python
--8<-- "docs/snippets/voice/tracks.py"
```

## Sources { #sources }

Une source est de l'audio à lire. Chacune ne sert qu'une fois : `play` l'ouvre, l'ouvrir après sa fin lève `StateError`, et `source.again()` en renvoie une nouvelle avec le même audio, pour la lire deux fois.

| Source | Lit | Positionnable |
|---|---|---|
| `voice.File(path)` | un fichier local | oui |
| `voice.Bytes(data, hint=None)` | le contenu d'un fichier en mémoire ; `hint` est une extension comme `"ogg"` | oui |
| `voice.Url(url, headers=None)` | une URL de média HTTP ou HTTPS directe, diffusée au fil de l'eau, ou une playlist HLS | quand le serveur accepte les requêtes de plage ; HLS non |
| `voice.FFmpeg(args, executable="ffmpeg")` | la sortie d'une commande ffmpeg, pour les formats que bostie ne décode pas | non |
| `voice.Stream(items, format)` | un itérable asynchrone de morceaux PCM ou de paquets Opus au `format` donné | non |

`File`, `Bytes` et `Url` sont décodés en Rust : Ogg (Opus, Vorbis), WebM et Matroska, MP4 et M4A (AAC), MP3, FLAC et WAV. Une URL doit pointer vers le média lui-même, pas vers une page web qui l'intègre. Quand la connexion au serveur se coupe, `Url` se reconnecte jusqu'à trois fois, après 0,5, 1 et 2 secondes, et reprend là où elle s'était coupée si le serveur accepte les requêtes de plage. Une playlist HLS maîtresse lit son rendu audio, une playlist en direct démarre trois segments avant sa fin, et les playlists chiffrées et les segments en plage d'octets sont refusés avec `SourceError`. `FFmpeg` lance le programme avec vos arguments d'entrée et lit du PCM 48 kHz stéréo 16 bits sur sa sortie ; il lève `SourceError` quand le programme n'est pas dans le `PATH` ou se termine sur une erreur. Une source qui ne peut pas être ouverte, décodée ou positionnée lève `SourceError`, ou termine sa piste avec la raison `ERROR`.

Après `open()`, qui définit `opened`, une source a un `format`, une `duration` (en secondes, `None` si elle est inconnue), `seekable` et `tags`, un `voice.Tags` avec le `title`, l'`artist` et l'`album` que le fichier indique.

```python
--8<-- "docs/snippets/voice/other_sources.py"
```

L'audio Opus à 48 kHz stéréo en paquets de 20 ms, comme la plupart des fichiers Ogg Opus et WebM, est envoyé tel quel, sans être décodé puis réencodé, tant qu'il joue seul au volume 1 et sans filtres. `track.passthrough` indique quand c'est le cas.

## Pistes { #tracks }

`vc.play(source, volume=1.0, filters=Filters(), meta=None)` prend le volume et les filtres de départ, ainsi que `meta`, n'importe quelle valeur à garder avec la piste, comme la personne qui l'a demandée. Il prend aussi `buffer` et `stall` pour le [sink](sinks.md) de la piste.

| `Track` | Rôle |
|---|---|
| `await track.wait()` | attend la fin et renvoie un `TrackEnd` ; annuler l'attente n'arrête pas la piste |
| `await track.started()` | attend que la lecture commence ; `False` si la piste s'est terminée avant |
| `track.ended` | le `TrackEnd`, ou `None` pendant la lecture |
| `track.position` | les secondes de la source lues jusqu'ici |
| `track.pause()`, `track.resume()`, `track.paused` | arrêtent et relancent son alimentation ; l'audio déjà en tampon continue de jouer |
| `await track.seek(seconds)` | reprend à une position ; `SourceError` quand la source n'est pas positionnable |
| `track.volume`, `track.filters` | changent immédiatement pendant la lecture |
| `track.stop()` | la termine |
| `track.source`, `track.meta`, `track.sink` | ce qu'elle lit, votre valeur, et le sink dans lequel elle écrit |

Un `TrackEnd` a la `track`, la `reason` (`FINISHED`, `STOPPED`, `ERROR` avec l'`error`, ou `CLEANUP` quand la connexion s'est terminée) et `may_start_next`, vrai après `FINISHED` ou `ERROR`, qui indique si votre propre lecteur doit passer à sa piste suivante. `vc.stop()` termine toutes les pistes.

## Volume et filtres { #volume-and-filters }

`volume` multiplie les échantillons : 0.5 joue deux fois moins fort, 1.0 les laisse inchangés. `voice.Filters` définit des effets que Rust applique pendant la lecture :

| Champ de `Filters` | Défaut | Plage |
|---|---|---|
| `equalizer` | `{}` | jusqu'à 16 bandes, qui associent une fréquence centrale de 20 à 20 000 Hz à un gain en dB ; chaque bande a une largeur d'une octave |
| `bass` | 0.0 | gain en dB sous 100 Hz |
| `speed` | 1.0 | de 0.5 à 2.0, en gardant la hauteur |
| `pitch` | 1.0 | de 0.5 à 2.0, en gardant le tempo |

Les gains vont de -24 à 24 dB, et les valeurs hors plage lèvent `ValueError`.

## Files d'attente { #queues }

```python
--8<-- "docs/snippets/voice/queue_player.py"
```

`voice.Queue(vc)` enchaîne les pistes sans blanc, ou en les superposant pendant `crossfade` secondes. Elle prend `volume`, `filters`, `crossfade` et `history`, qui reprennent par défaut les réglages de la connexion, ainsi que le `buffer` et le `stall` du sink.

- `queue.add(source, meta=None, index=None)` met une source en file d'attente à la fin, ou à la position `index` parmi les pistes à venir, et renvoie sa `Track`.
- `queue.now`, `queue.upcoming` et `queue.history` sont la piste en cours, les suivantes, et jusqu'à `history` pistes déjà jouées.
- `queue.skip()` termine la piste en cours ; `queue.back()` rejoue la dernière piste de l'historique, puis à nouveau la piste en cours.
- `queue.move(track, index)`, `queue.remove(track)`, `queue.shuffle()` et `queue.clear()` réorganisent les pistes à venir.
- `queue.loop = "track"` répète la piste en cours, `"queue"` toute la file d'attente, `None` arrête la répétition ; le type est `voice.Loop`. Les répétitions utilisent `source.again()`.
- `queue.pause()`, `queue.resume()`, `queue.paused`, `queue.volume` et `queue.filters` agissent sur ce qui joue.
- `queue.stop()` termine toutes les pistes ; les pistes ajoutées ensuite jouent de nouveau.
- `await queue.join()` attend que plus rien ne soit en file d'attente ni en lecture.

Une file d'attente sur une connexion terminée est `closed` et n'accepte plus de pistes ; créez-en une nouvelle après la reconnexion, comme le fait l'extrait.

## Sons du soundboard { #soundboard-sounds }

`await bot.rest.soundboard.send(vc.channel_id, sound_id=...)` joue un son du soundboard dans le salon auquel le bot est connecté. Discord le joue directement dans l'appel, à côté de ce que le bot envoie ; passez `source_guild_id` pour un son d'un autre serveur. `bot.rest.soundboard.list_default()` liste les sons propres à Discord et `list(guild_id)` ceux d'un serveur, et `bot.request_soundboard_sounds(guild_id)` les demande à la gateway.

## Le même audio sur plusieurs serveurs { #the-same-audio-in-several-guilds }

`await voice.broadcast(source, connections)` lit une source sur plusieurs connexions, en ne la décodant et ne l'encodant qu'une fois. Les connexions qui se terminent quittent l'ensemble ; celles qui se connectent plus tard ne sont pas ajoutées. Elle prend `encoder` et `stall`, `"drop"` par défaut, pour qu'une connexion bloquée ne retienne pas les autres.

## Encodeur { #encoder }

`voice.EncoderSettings` configure l'encodeur Opus d'une connexion, via `Defaults(encoder=...)`, `connect(encoder=...)` ou `vc.encoder = ...` pendant la connexion :

| Champ | Défaut | Signification |
|---|---|---|
| `bitrate` | `None` | bits par seconde, de 500 à 512 000 ; `None` suit le débit du salon, ou 64 000 |
| `application` | `"audio"` | `"audio"` pour la musique, `"voip"` pour la parole, `"lowdelay"` pour la latence la plus faible |
| `fec` | `True` | la correction d'erreurs anticipée, qui masque les paquets perdus |
| `packet_loss` | 0.1 | la perte attendue, de 0 à 1, que `fec` anticipe |
| `complexity` | 10 | de 0 à 10 ; plus bas utilise moins de CPU |
| `bandwidth` | `"auto"` | la bande audio la plus haute : `"narrow"`, `"medium"`, `"wide"`, `"superwide"` ou `"full"` |
| `signal_type` | `"auto"` | optimise pour `"voice"` ou `"music"` |

`vc.speaking_flags` définit les flags de parole que le bot annonce : `voice.SpeakingFlags.MICROPHONE` par défaut, `SOUNDSHARE` pour l'audio partagé, `PRIORITY` pour l'intervenant prioritaire.
