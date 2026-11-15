---
source: c8644d4a54676ea108a34c06bd38c4e355260bc3
---

# Réception et enregistrement

![Bostie qui tient un micro pendant que des ondes sonores arrivent](../assets/mascot/bostie-receiving.webp){ align=right width="180" loading=lazy }

`vc.receive()` ouvre un récepteur : un itérateur asynchrone borné sur l'audio que le bot entend. Chaque locuteur arrive séparément, déchiffré, remis dans l'ordre et décodé en Rust. Plusieurs récepteurs peuvent être ouverts sur une même connexion, chacun avec son propre format et ses propres filtres.

```python
--8<-- "docs/snippets/voice/listening.py"
```

## Formats { #formats }

`vc.receive(format)` renvoie un `voice.Receiver` pour un `voice.ReceiveFormat` :

| `vc.receive(format)` | Éléments | Usage |
|---|---|---|
| `voice.PCM(rate=48000, channels=2, sample="s16le")` | des `Frame` d'échantillons décodés dans ce format ; la fréquence est l'une des `voice.DECODE_RATES`, 8, 12, 16, 24 ou 48 kHz | l'écoute, la reconnaissance vocale, les niveaux |
| `voice.Opus()` | des `Frame` dont les `data` sont un paquet Opus | enregistrer ou retransmettre sans décoder |
| `voice.Packets()` | des `Packet` déchiffrés, dans l'ordre d'arrivée, avant le tampon de gigue | votre propre tampon de gigue, les extensions d'en-tête RTP |

Une `Frame` a le `user_id`, le `ssrc`, le `timestamp` RTP, `offset` et `samples` sur l'horloge 48 kHz du récepteur, `received_at`, les `data`, `lost` (vrai pour l'audio qu'Opus a inventé pour masquer un paquet perdu), et pour le PCM le `level`, la valeur RMS de la trame en dBFS. L'horloge démarre à l'ouverture du récepteur, si bien que les trames de différents utilisateurs s'alignent par `offset`. Un `Packet` a le `user_id`, le `ssrc`, la `sequence`, le `timestamp`, `received_at`, le payload `opus` et les `extensions` de l'en-tête RTP.

## Choisir qui entendre { #choosing-whom-to-hear }

- `users=[...]` ne garde que ces utilisateurs, et `exclude=[...]` en écarte certains.
- `include_self=True` ajoute l'audio du bot lui-même.
- `mix=True` (PCM et Opus) fournit un seul flux où tout le monde est mixé, avec `user_id` à `None`.
- `markers=True` insère les événements `SpeechStart`, `SpeechStop` et `UserLeave` entre les trames, chacun avec son `offset` sur la même horloge, pour qu'un consommateur sache où la parole commence et finit sans avoir à la chronométrer.

## Réglages { #settings }

`vc.receive` prend ces arguments nommés ; `Packets()` n'a pas de tampon de gigue, il ne prend donc pas `jitter`.

| Réglage | Défaut | Signification |
|---|---|---|
| `buffer` | `Defaults.receive_buffer`, 2.0 | secondes d'éléments que le récepteur garde pour un consommateur lent, de 0.02 à 60 |
| `overflow` | `Defaults.overflow`, `"drop_oldest"` | quand le tampon est plein : abandonner les éléments les plus anciens, ou `"drop_newest"` |
| `jitter` | `Defaults.jitter`, 0.1 | secondes d'attente des paquets en retard avant de continuer la lecture, de 0 à 1 ; PCM et Opus uniquement |
| `stall` | `Defaults.receive_stall`, `"wait"` | quand la connexion se reconnecte : `"wait"` garde le récepteur ouvert, `"end"` le termine |

## Lire le récepteur { #reading }

`async for item in rx` attend des éléments et s'arrête quand le récepteur se termine. `async with rx:` le ferme à la fin du bloc.

- `rx.get_nowait()` renvoie l'élément suivant, ou lève `voice.Empty` quand rien n'est en file d'attente et `voice.ReceiverClosed` quand le récepteur s'est terminé.
- `rx.queued` est le temps d'attente du plus ancien élément en file d'attente : le retard du consommateur.
- `rx.clear()` abandonne tout ce qui est en file d'attente et continue en direct.
- `await rx.mark()` renvoie `True` dès que l'audio reçu avant l'appel est en file d'attente ou abandonné, ou `False` au bout de 1,2 seconde plus deux fois la gigue ; annuler une attente laisse la marque aux autres.
- `rx.stats()` renvoie un `ReceiverStats` : les éléments `delivered` (livrés), `dropped` (abandonnés par débordement) et les paquets `lost` (perdus en route).
- `rx.close()` arrête la réception. `rx.closed_reason`, une `voice.ClosedReason`, indique pourquoi un récepteur s'est terminé : `CLOSED`, `DISCONNECTED`, `MOVED` (le bot a changé de salon) ou `RECONNECTING` (avec `stall="end"`).

Un modérateur peut mettre le bot en sourdine, et Discord ne lui envoie alors plus aucun audio ; bostie consigne un avertissement quand un récepteur s'ouvre alors que le bot est en sourdine, et quand le bot est mis en sourdine alors que des récepteurs sont ouverts.

## Relais { #relaying }

Un récepteur et un sink se branchent directement l'un sur l'autre, pour transporter l'audio d'un appel à un autre :

```python
--8<-- "docs/snippets/voice/relay.py"
```

Une trame mixée n'a ni `user_id`, ni `ssrc`, ni `timestamp`, et un mixage ne peut pas porter de marqueurs. Une trame Opus perdue a des `data` vides, car il n'y a aucun paquet à transmettre ; ignorez-la avant d'écrire dans un sink Opus, comme le fait `relay_speaker`. Les deux bouts utilisent par défaut du PCM 48 kHz stéréo 16 bits, donc un récepteur PCM écrit directement dans un sink par défaut.

## Parole { #speech }

La détection de la parole tourne en Rust sur chaque connexion tant que `speech_activity` est activé : un utilisateur commence à parler avec son premier audio et s'arrête après `speech_hangover` secondes de silence, 0,2 par défaut. `vc.speaking` contient qui parle en ce moment, les événements `SpeechStart` et `SpeechStop` le signalent aux écouteurs, et les récepteurs ouverts avec `markers=True` le portent dans leur flux. Avec `speech_activity` désactivé et aucun récepteur ouvert, bostie ne déchiffre même pas l'audio entrant.

`voice.utterances(rx, min_silence=0.8, max_length=30.0)` regroupe les trames d'un récepteur PCM en `Utterance` : la parole d'un utilisateur du début à la fin, avec des `data` au format du récepteur, son `offset`, sa `duration` et les secondes perdues (`lost`). Une prise de parole se termine après `min_silence` secondes sans parole, ou au bout de `max_length` secondes. Il faut un récepteur PCM sans `mix`, ouvert avec `markers=True`, ou un `level` en dBFS, qui décide alors à la place des marqueurs de ce qui compte comme parole.

## Enregistrement { #recording }

```python
--8<-- "docs/snippets/voice/call_recording.py"
```

`voice.Recorder(vc, path)` enregistre chaque utilisateur dans son propre fichier, ou, avec `mix=True`, tout l'appel dans un seul. Le chemin est un template avec `{guild_id}` et `{user_id}`, obligatoire pour un fichier par utilisateur et refusé avec `mix=True`, et son extension choisit le conteneur :

- `.ogg` ou `.opus` écrit de l'Ogg Opus. Par utilisateur, les paquets Opus reçus sont écrits tels quels, sans décodage.
- `.wav` écrit du PCM 16 bits au `format` de l'enregistreur, 48 kHz stéréo par défaut ; sa fréquence est l'une des `voice.DECODE_RATES`.

Avec `align=True`, le réglage par défaut, du silence comble les trous, si bien que chaque fichier commence au début de l'enregistrement et que les fichiers s'alignent. `users`, `exclude` et `include_self` choisissent qui enregistrer, comme pour un récepteur, et `buffer` (30 secondes) et `jitter` (0,3 seconde) sont plus grands que les valeurs par défaut d'un récepteur, parce qu'un disque peut se bloquer. L'enregistrement se termine de lui-même après `max_duration` secondes, dès que ses fichiers atteignent `max_size` octets, ou quand la connexion se termine.

`await recorder.stop()` le termine une fois l'audio déjà entendu écrit, et renvoie les fichiers sous la forme d'un dict qui associe l'id d'utilisateur à un `Path`, avec `None` comme clé d'un fichier mixé. `await recorder.wait()` attend qu'il se termine de lui-même. `recorder.files` liste les fichiers écrits jusque-là, et `recorder.stats()` les compteurs du récepteur, où `dropped` signifie que le disque a pris du retard pendant plus de `buffer`.

Prévenez les personnes présentes dans l'appel que le bot les enregistre, comme le fait l'extrait.
