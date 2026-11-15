---
source: 5246991ba1478ea5d33d14802d1a46c835ecb91e
---

# Connexions vocales

![Bostie qui écoute avec un casque](../assets/mascot/bostie-listen.webp){ align=right width="180" loading=lazy }

Le moteur vocal de bostie tourne dans son cœur Rust : le WebSocket vocal, le transport UDP, l'horloge d'envoi de 20 ms, le décodage et le rééchantillonnage, le mixeur, l'encodage et le décodage Opus, le chiffrement du transport et le chiffrement de bout en bout DAVE. Python lui demande de rejoindre, de lire et de recevoir ; la cadence ne dépend jamais de la boucle d'événements, sauf que Python doit garder rempli le tampon de chaque sink.

La voix nécessite `Intents.GUILD_VOICE_STATES`, que `Intents.DEFAULT` inclut. Le bot a au plus une connexion vocale par serveur.

```python
--8<-- "docs/snippets/voice/voice_connection.py"
```

## Se connecter { #connecting }

`await ctx.voice.connect()` rejoint le salon vocal ou le salon de conférence de l'utilisateur qui a lancé la commande, et rend la main dès que l'audio peut circuler dans les deux sens. Il trouve le salon de l'utilisateur dans le cache, ou le demande à Discord quand le serveur n'est pas en cache, et lève `NotInVoice` quand l'utilisateur n'est dans aucun salon vocal du serveur ou que l'interaction vient d'un message privé. `ctx.voice.current` est la connexion du bot dans ce serveur, ou `None`.

En dehors d'un gestionnaire, `await bot.voice.connect(channel_id)` rejoint un salon par son id. Dans un serveur où le bot est déjà connecté, il déplace la connexion et n'applique que les options données. `bot.voice.get(guild_id)` et `bot.voice.connections` renvoient les connexions ouvertes, et `await bot.voice.close()` les quitte toutes, comme le fait `bot.close()`.

Une connexion qui n'est pas prête au bout de `timeout` secondes lève `VoiceConnectError` avec la raison `TIMEOUT` ; Discord laisse sans réponse une demande pour rejoindre un salon plein, ou un salon auquel le bot n'a pas le droit de se connecter. Les autres échecs lèvent `VoiceConnectError` avec la raison, le code de fermeture et le message de Discord.

## Réglages { #settings }

`Bot(voice=voice.Defaults(...))` définit les réglages avec lesquels chaque connexion démarre, et `connect()` prend les réglages de connexion pour un seul appel :

| Champ de `voice.Defaults` | Défaut | Signification |
|---|---|---|
| `self_mute`, `self_deaf` | `False` | le micro coupé et la sourdine du bot lui-même |
| `timeout` | 10.0 | secondes d'attente pour rejoindre un salon, s'y déplacer ou modifier la connexion |
| `server_change_delay` | 0.0 | secondes d'attente avant de suivre Discord vers un nouveau serveur vocal |
| `encoder` | `EncoderSettings()` | l'encodeur Opus ; voir [Lecture audio](playback.md#encoder) |
| `speaking_flags` | `MICROPHONE` | les `voice.SpeakingFlags` que le bot annonce quand il joue |
| `stage` | `"listen"` | dans un salon de conférence : `"listen"`, `"request"` pour demander la parole, ou `"speak"` |
| `speech_hangover` | 0.2 | secondes de silence avant qu'un locuteur soit considéré comme arrêté, de 0.02 à 10 |
| `speech_activity` | `True` | si les événements de début et de fin de parole se déclenchent |
| `stall_after` | 2.0 | secondes pendant lesquelles le média peut cesser de circuler avant que `MediaStalled` le signale, au moins 0.02 |
| `sink_buffer`, `stall` | 0.2, `"hold"` | les valeurs par défaut de chaque [sink](sinks.md) |
| `receive_buffer`, `jitter`, `overflow`, `receive_stall` | 2.0, 0.1, `"drop_oldest"`, `"wait"` | les valeurs par défaut de chaque [récepteur](receiving.md) |
| `crossfade`, `history` | 0.0, 0 | les valeurs par défaut de chaque [file d'attente](playback.md#queues) |

`connect()` prend `self_mute`, `self_deaf`, `timeout`, `server_change_delay`, `encoder`, `speaking_flags`, `stage`, `speech_hangover`, `speech_activity` et `stall_after` comme arguments nommés ; un argument omis, ou `None`, garde la valeur par défaut. `connect_external()` prend les trois premiers, `move()` prend `timeout`, et `edit()` prend `self_mute`, `self_deaf` et `timeout`. Les valeurs invalides lèvent `ValueError` à la construction du `Defaults`. Les réglages de type chaîne ont les types littéraux `voice.StageRole`, `voice.SinkStall`, `voice.ReceiveStall`, `voice.Overflow`, `voice.Sample`, `voice.OpusApplication`, `voice.OpusBandwidth` et `voice.OpusSignal`. `ctx.voice` est un `voice.ContextVoice` et `bot.voice` un `voice.VoiceManager`.

## La connexion { #the-connection }

| `VoiceConnection` | Valeur |
|---|---|
| `guild_id`, `channel_id` | où elle se trouve |
| `state` | un `voice.State` : `CONNECTING`, `READY`, `RESUMING`, `RECONNECTING` ou `DISCONNECTED` |
| `members` | les id des autres utilisateurs de l'appel |
| `speaking` | les utilisateurs qui parlent en ce moment, associés au `time.monotonic()` où ils ont commencé |
| `ssrcs` | chaque SSRC de l'appel, associé à son id d'utilisateur |
| `self_mute`, `self_deaf` | le micro coupé et la sourdine du bot lui-même, tels que Discord les a appliqués |
| `mute`, `deaf`, `suppress` | si un modérateur a rendu le bot muet ou l'a mis en sourdine, ou si le bot n'a pas la parole dans ce salon de conférence |
| `latency` | une `Latency` avec les allers-retours WebSocket et UDP en secondes, `None` tant qu'ils ne sont pas mesurés |
| `dave` | le chiffrement de bout en bout de l'appel ; voir plus bas |
| `sinks` | les sinks ouverts, du plus ancien au plus récent |
| `encoder`, `speaking_flags`, `speech_hangover`, `speech_activity`, `stall_after` | modifiables pendant la connexion |

- `await vc.move(channel_id)` se déplace vers un autre salon vocal ou salon de conférence du serveur et rend la main dès que le média y circule.
- `await vc.edit(self_mute=..., self_deaf=...)` change le micro coupé ou la sourdine du bot lui-même, laisse tel quel celui qui n'est pas donné, et rend la main dès que Discord l'a appliqué.
- `await vc.stage("request")` demande la parole dans un salon de conférence, `"speak"` prend la parole (si le bot en a la permission), `"listen"` retourne dans le public.
- `await vc.wait_until(voice.State.READY)` attend un état, et lève `VoiceNotConnected` si la connexion se termine avant.
- `vc.reconnect()` abandonne le WebSocket vocal et reprend la session sur un nouveau.
- `vc.stop()` ferme chaque sink, si bien que plus rien ne joue.
- `await vc.disconnect()` quitte le salon et rend la main dès que la connexion est terminée, au plus 2 secondes plus tard.

Les appels sur une connexion terminée lèvent `VoiceNotConnected`.

La connexion se répare d'elle-même. Un WebSocket coupé reprend sa session, une session perdue se reconnecte, et quand un modérateur déplace le bot, la connexion le suit dans le nouveau salon et envoie un événement `Moved`. Quand un modérateur le déconnecte ou supprime le salon, elle se termine avec la raison `REMOVED`. bostie avertit quand le bot joue alors qu'il est rendu muet sur le serveur ou qu'il n'a pas la parole dans un salon de conférence, et quand des récepteurs attendent alors qu'il est en sourdine.

## Chiffrement de bout en bout { #end-to-end-encryption }

bostie parle DAVE, le chiffrement de bout en bout de Discord pour la voix, sans rien à configurer : il rejoint le groupe de chiffrement de l'appel, chiffre ce qu'il envoie et déchiffre ce qu'il reçoit. `vc.dave` le décrit sous la forme d'un `voice.Dave` :

- `protocol_version` est le protocole DAVE utilisé, 0 pour aucun ;
- `epoch` compte les changements de membres du groupe ;
- `privacy_code` est le code que les clients Discord affichent pour l'appel ;
- `await vc.dave.verification_code(user_id)` renvoie le code à comparer avec un autre utilisateur par un autre canal, pour vérifier que personne d'autre ne s'est interposé.

Un événement `DaveTransition` signale chaque changement. Un salon qui exige DAVE d'un client qui ne le propose pas ferme la connexion avec la raison `DAVE_REQUIRED`, et une étape DAVE ratée lève `DaveError` ou termine la connexion avec `DAVE_FAILED`.

## Événements { #events }

Les connexions vocales envoient leurs événements via `@bot.listen`. Chacun est un `voice.VoiceEvent` avec le `guild_id`. Un `StateChange` vers `DISCONNECTED` porte une `voice.DisconnectReason` : `REQUESTED` (`disconnect()` a été appelé), `REMOVED` (un modérateur a déconnecté le bot ou supprimé le salon), `CALL_TERMINATED`, `RATE_LIMITED`, `TIMEOUT`, `SESSION_INVALID`, `AUTHENTICATION_FAILED`, `DAVE_REQUIRED`, `DAVE_FAILED`, `PROTOCOL_ERROR` ou `CLOSED` (le bot s'est fermé).

| Événement | Champs | Envoyé quand |
|---|---|---|
| `StateChange` | `old`, `new`, `reason`, `code`, `detail` | la connexion a changé d'état |
| `Moved` | `old_channel_id`, `new_channel_id`, `by_us` | le bot s'est déplacé, via `move()` ou par un modérateur |
| `DaveTransition` | `protocol_version`, `epoch`, `transition_id` | le chiffrement de bout en bout a changé |
| `MediaStalled`, `MediaResumed` | `direction` (`voice.Direction.SEND` ou `RECEIVE`), `duration` | l'envoi ou la réception s'est arrêté, ou a repris |
| `UserJoin`, `UserLeave` | `user_id` | un utilisateur a rejoint ou quitté l'appel |
| `SpeakingUpdate` | `user_id`, `ssrc`, `flags` | un utilisateur a annoncé son SSRC et ses flags de parole |
| `SpeechStart`, `SpeechStop` | `user_id`, `ssrc`, `at`, et `duration` à l'arrêt | un utilisateur a commencé ou cessé de parler |
| `RtcpPacket` | `packet_type`, `count`, `ssrc`, `user_id`, `reports`, `data` | le serveur vocal a envoyé un paquet RTCP ; chaque `ReceptionReport` de `reports` contient le `fraction_lost`, le `lost`, le `highest_sequence`, le `jitter`, le `last_sender_report` et le `delay` d'un flux |

Ils se trouvent dans `bostie.voice`. `SpeechStart` et `SpeechStop` ne se déclenchent que lorsque `speech_activity` est activé.

## Média bloqué { #stalled-media }

```python
--8<-- "docs/snippets/voice/media_stalls.py"
```

`MediaStalled` avec la `direction` `SEND` se déclenche quand de l'audio attend dans un sink mais ne peut pas partir pendant `stall_after` secondes, lors d'une reconnexion ou d'une transition DAVE. Avec `RECEIVE`, des paquets atteignent un récepteur ouvert mais aucun ne peut être ouvert ni lu pendant cette durée. `MediaResumed` suit dès que le média circule de nouveau, sa `duration` comptant depuis le début du blocage. `stall_after` vaut 2 secondes, sauf si `voice.Defaults`, `connect(stall_after=...)` ou `vc.stall_after` en décide autrement.

## Quitter un salon vide { #leaving-an-empty-channel }

Le bot reste dans un salon jusqu'à ce qu'il se déconnecte. Pour partir quand le dernier utilisateur s'en va :

```python
--8<-- "docs/snippets/voice/leave_when_alone.py"
```

## Erreurs { #errors }

Les erreurs vocales sont des `VoiceError` : `VoiceConnectError` (une connexion s'est terminée avant d'être prête ; `reason`, `code`, `detail`), `NotInVoice`, `VoiceNotConnected`, `AlreadyConnected`, `VoiceQueueFull` (trop de commandes attendent la connexion), `DaveError`, et pour l'audio `SourceError`, `SinkClosed`, `BufferFull`, `Empty` et `ReceiverClosed`, que les pages suivantes expliquent.

## Moteurs vocaux externes { #external-voice-engines }

```python
--8<-- "docs/snippets/voice/external_engine.py"
```

`await bot.voice.connect_external(guild_id, channel_id)` rejoint un salon pour un moteur vocal qui tourne ailleurs, et renvoie un `ExternalVoice` avec le `state` (`VoiceStateUpdate`) et le `server` (`VoiceServerUpdate`) avec lesquels le moteur se connecte. bostie n'envoie alors aucun audio dans ce serveur. `async with external.updates() as updates:` fournit les serveurs vocaux attribués ensuite au serveur et les états vocaux du bot lui-même, et `await external.disconnect()` quitte le salon. Il lève `AlreadyConnected` dans un serveur où bostie gère sa propre connexion, et prend `self_mute`, `self_deaf` et `timeout`.

## Versions { #versions }

`voice.versions()` renvoie les versions de bostie, de libopus, de la bibliothèque DAVE et du protocole DAVE le plus élevé compilé dans le build.
