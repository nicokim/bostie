---
source: e89423f86355edd642290a66c8608f48553e0ff6
description: "Envoyez de l'audio PCM ou Opus brut en salon vocal Discord via un sink : formats, contre-pression, marqueurs, fondus enchaînés, tampon et compteurs d'underruns."
---

# Sinks et audio brut

Un sink est un tampon borné dans lequel Python écrit de l'audio et que le mixeur Rust lit, 20 ms à la fois. Chaque piste et chaque file d'attente jouent à travers un sink. Ouvrez le vôtre pour de l'audio que vous générez ou que vous recevez d'ailleurs : un synthétiseur, un moteur de synthèse vocale, un flux venant d'un autre programme.

```python
--8<-- "docs/snippets/voice/tone_sink.py"
```

## Formats { #formats }

`vc.sink(format)` renvoie un `voice.Sink` pour ce que vous allez écrire, un `voice.Format` :

- `voice.PCM(rate=48000, channels=2, sample="s16le")` : des échantillons entrelacés en little-endian, `"s16le"` (entiers 16 bits) ou `"f32le"` (flottants 32 bits), mono ou stéréo, de 8000 à 192 000 Hz. bostie rééchantillonne en 48 kHz en Rust ; une fréquence doit se diviser en 48 kHz par blocs d'au plus 960 échantillons, ce que fait toute fréquence courante.
- `voice.Opus()` : un paquet Opus par écriture. Les paquets de 20 ms à 48 kHz stéréo sont envoyés tels qu'écrits tant que le sink joue seul au volume 1 et sans filtres ; tout le reste est décodé et mixé.

`voice.OpusEncoder(pcm_format, encoder=EncoderSettings())` encode du PCM en ces paquets dans le processus Python, pour de l'audio que vous voulez encoder une seule fois et envoyer à plusieurs sinks : `encode(data)` renvoie tous les paquets complets obtenus jusque-là, et `flush()` complète le reste avec du silence. Son débit est de 64 kbps par défaut, puisqu'il n'a aucun salon à suivre.

## Écriture { #writing }

- `await sink.write(data)` met de l'audio en file d'attente, et attend tant que le sink contient déjà `buffer` secondes. Cette attente est la contre-pression qui maintient votre producteur au rythme du temps réel.
- `sink.write_nowait(data)` met tout le morceau en file d'attente d'un coup, ou lève `BufferFull`.
- `async with sink:` attend à la fin du bloc que tout ce qui a été écrit ait joué, puis ferme le sink ; une exception dans le bloc jette plutôt ce qui est en file d'attente.
- `await sink.drain()` attend que tout ce qui a été écrit jusque-là ait joué ; `False` si une partie a été abandonnée.
- `sink.clear()` abandonne ce qui est en file d'attente ; `clear(until=marker)` seulement la partie avant un marqueur, `clear(since=marker)` seulement la partie après.
- `sink.close()` jette ce qui est en file d'attente et ferme le sink. Écrire dans un sink fermé lève `SinkClosed`.

`sink.mark()` renvoie un `Marker` placé après l'audio écrit jusque-là. `await marker` renvoie `True` dès que tout l'audio qui le précède a joué, ou `False` si une partie a été abandonnée avant ; `marker.done` indique si c'est déjà connu. Les marqueurs permettent à un lecteur de savoir quand une piste s'est vraiment terminée, et pas seulement quand elle a été écrite.

## Réglages { #settings }

`vc.sink` prend ces arguments nommés ; `vc.play` et `voice.Queue` prennent aussi `buffer` et `stall`.

| Réglage | Défaut | Signification |
|---|---|---|
| `buffer` | `Defaults.sink_buffer`, 0.2 | secondes que le sink contient avant que `write` attende, de 0.02 à 60 |
| `stall` | `Defaults.stall`, `"hold"` | quand l'audio ne peut pas être envoyé, pendant une reconnexion ou une transition DAVE : `"hold"` le garde pour le jouer ensuite, `"drop"` le jette en temps réel, pour de l'audio en direct qui ne doit pas prendre de retard |
| `volume` | 1.0 | multiplie les échantillons ; `sink.volume` le change pendant la lecture |
| `filters` | `Filters()` | les [effets](playback.md#volume-and-filters) ; `sink.filters` les change pendant la lecture |
| `after` | `None` | un autre sink de cette connexion ; celui-ci reste silencieux jusqu'à ce que l'autre se ferme ou ait fini de jouer ce qui a été écrit avant son dernier `drain()` |
| `crossfade` | 0.0 | avec `after`, secondes d'avance au démarrage pour un fondu enchaîné ; le sink met alors en tampon d'autant plus |

`after` et `crossfade` permettent à une file d'attente de jouer sans blanc : le sink de la piste suivante se remplit pendant que la piste en cours joue, et prend le relais à l'échantillon près.

## État et compteurs { #state-and-counters }

`sink.queued` est le nombre de secondes d'audio en attente, `sink.passthrough` indique si le dernier paquet est parti tel qu'écrit, et `sink.closed` si le sink est fermé. `sink.stats()` renvoie un `SinkStats` :

| Champ | Valeur |
|---|---|
| `sent` | secondes d'audio envoyées |
| `position` | la tête de lecture en secondes, telle que les marqueurs la comptent |
| `underruns` | combien de fois le sink s'est vidé pendant la lecture, en dehors d'un `drain()` : le producteur a pris du retard |
| `held_ticks` | ticks pendant lesquels le sink a été retenu parce que l'audio ne pouvait pas être envoyé |
| `dropped` | secondes jetées par un blocage en mode `"drop"` |
| `passthrough_packets` | paquets envoyés tels qu'écrits, sans décodage |

Les `underruns` sont le chiffre à surveiller : un producteur qui n'arrive pas à garder `buffer` secondes d'avance est trop lent, ou la boucle d'événements est bloquée. `vc.sinks` liste les sinks ouverts d'une connexion.
