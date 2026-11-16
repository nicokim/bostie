---
source: ceb5f752e899b0a321da19a1c2f2e69456a2455a
description: "Testez un bot Discord sans Discord : gateway, API REST et serveur vocal factices en local, envoi d'interactions et d'événements, contrôle de ce qu'il envoie."
---

# Tester votre bot

![Bostie cherchant avec une loupe jusqu'à ce qu'une coche apparaisse](../assets/mascot/bostie-detective.webp){ align=right width="180" loading=lazy }

`bostie.testing` lance un Discord factice sur localhost : une gateway, une API REST avec les routes qu'un bot utilise le plus, et en option un serveur vocal. Votre vrai bot s'y connecte, et le test pilote le côté Discord : il envoie des événements et des interactions, puis vérifie ce que le bot a renvoyé. Il nécessite l'extra `testing` ([Installation](../install.md)), et la suite de tests de bostie elle-même tourne dessus.

```python
--8<-- "docs/snippets/testing/test_greeter.py"
```

`run(scenario)` démarre un `FakeDiscord`, exécute `async def scenario(fake)` contre lui en 10 secondes maximum, puis l'arrête ; c'est une fonction ordinaire, donc pytest n'a besoin d'aucun plugin. `async with running(bot, fake):` démarre le bot sur le Discord factice, attend qu'il soit prêt et le ferme à la fin. Dirigez le bot vers le Discord factice avec `Bot(api_url=fake.api_url)`, ou définissez `BOSTIE_API_URL` quand le bot est construit ailleurs.

## Piloter le côté Discord { #driving-discords-side }

| Appel | Effet |
|---|---|
| `await fake.interaction(type, data)` | envoie un `INTERACTION_CREATE` et renvoie la première réponse du bot sous forme de requête `Recorded`, ou lève `TimeoutError` si aucune n'arrive dans les 3 secondes que Discord accorde |
| `await fake.dispatch(name, d, shard=0)` | envoie n'importe quel événement de gateway et renvoie son numéro de séquence |
| `fake.expect_callback(payload)` | pour une interaction que vous envoyez vous-même : un future pour sa première réponse |
| `await fake.send_op(op, d)` | envoie un opcode autre que dispatch, comme 7 (reconnexion) ou 9 (session invalide) |
| `await fake.close_gateway(code)`, `fake.drop_gateway()` | ferme la connexion avec un code de fermeture, ou la coupe sans code |
| `fake.hold_acks()` | cesse de répondre aux heartbeats, comme le ferait une connexion morte |
| `await fake.next_identify()`, `await fake.next_resume()` | attend le prochain IDENTIFY ou RESUME du bot |

Les types d'interaction sont les numéros de Discord : 2 pour une commande, 3 pour un composant, 4 pour l'autocomplétion, 5 pour l'envoi d'une fenêtre modale. Une commande enregistrée uniquement sur le serveur de l'interaction reçoit l'id de ce serveur dans son `data`, comme Discord l'envoie ; quand le nom est aussi enregistré globalement, `payloads.command(name, guild_id=...)` choisit la commande du serveur.

Après une fermeture qui démarre une nouvelle session, comme le code 4007, le bot ne procède à une nouvelle identification qu'après `HTTPSettings.identify_spacing` secondes, 5 par défaut comme l'exige Discord. `Bot(http_settings=HTTPSettings(identify_spacing=0))` supprime cette attente dans un test.

`bostie.testing.payloads` construit les payloads de Discord à partir des exemples de sa documentation, avec de nouveaux id :

- `command(name, *options, guild_id=None)`, `option(name, type, value, focused=False)`, `subcommand(...)`, `group(...)` et `resolved()` pour les commandes et l'autocomplétion ;
- `context_menu(type, name, target_id, resolved)` pour les commandes utilisateur et les commandes de message ;
- `button(custom_id)`, `select(custom_id, *values)` et `modal(custom_id)` pour les composants et les fenêtres modales ;
- `message(...)`, `guild(...)`, `member(...)`, `user(...)`, `role(...)`, `channel(...)`, `voice_channel(...)`, `stage_channel(...)` et `voice_state(...)` pour les événements, chacun avec des arguments nommés qui remplacent des champs ;
- `fixture(name)` pour n'importe quel payload documenté que bostie embarque, et `snowflake()` pour un nouvel id.

## Vérifier ce que le bot a envoyé { #checking-what-the-bot-sent }

`fake.requests` liste chaque requête REST sous forme de `Recorded`, et `fake.expect_request(method, route_prefix)` renvoie un future pour la prochaine requête correspondante, pour une requête que le bot fait de lui-même, par exemple depuis une tâche. Un `Recorded` a `method`, `path` (relatif à la base de l'API), `query`, `headers`, `json` et, pour les envois de fichiers, `parts`. Un `Recorded` qui répond à une interaction contient le corps du callback dans `json`, comme ci-dessus. `fake.gateway_received` est une file d'attente des payloads que le bot a envoyés sur la gateway, et `fake.connections` les connexions à la gateway, des objets `Connection` avec leurs payloads `received`, leur `shard` et leur `close_code`.

`fake.cdn_file(path, data)` sert `data` à une URL de CDN qu'il renvoie, pour une pièce jointe ou une ressource que le bot télécharge, et `fake.cdn_requests` enregistre ces téléchargements.

Le Discord factice garde l'état dont ces routes ont besoin, et les tests peuvent le lire ou le pré-remplir : `fake.guilds`, `fake.channels`, `fake.users`, `fake.members`, `fake.messages`, `fake.commands` et `fake.guild_commands`, avec `fake.put_count` pour les synchronisations de commandes. `fake.ready_guilds` définit les id de serveurs que `READY` liste.

## Échecs et timing { #failures-and-timing }

- `fake.fail(method, route_prefix, status, json)` répond à la prochaine requête correspondante par une erreur, et remplace une route que le Discord factice ne sert pas.
- `fake.rate_limit(method, route_prefix, scope="user", retry_after=0.1)` y répond par un 429.
- `fake.answer(method, route_prefix, body, content_type)` y répond par un corps qui n'est pas du JSON.
- `fake.hold(method, route_prefix)` la fait attendre jusqu'à ce que vous déclenchiez l'`asyncio.Event` renvoyé ; `fake.hold_identify(shard)` et `fake.hold_hello()` font de même pour la poignée de main de la gateway.
- `await settle()` rend la main à la boucle d'événements quelques fois, pour que les callbacks planifiés par ce que vous venez de faire s'exécutent.

Pour attendre quelque chose que le bot fait dans un écouteur, faites-lui [émettre un événement à lui](events.md#events-of-your-own) et attendez celui-ci, comme le fait le second test, plutôt que de faire un `sleep`.

`FakeDiscord(compress=True)` compresse la gateway avec zstd, ce qui nécessite le module `compression.zstd` de Python 3.14 ; `heartbeat_interval` définit l'intervalle qu'il demande, 41 250 ms par défaut comme chez Discord.

## Voix { #voice }

`FakeDiscord(voice=True)` ajoute un salon vocal et un salon de conférence, répond aux mises à jour d'état vocal et fait tourner une gateway vocale et un serveur UDP sous `fake.voice`, de sorte que `ctx.voice.connect()` et `vc.play(...)` fonctionnent contre lui. Il ne parle pas DAVE ; les tests Rust de bostie couvrent cette partie. Le test peut :

- attendre ce que le bot a envoyé avec `await fake.next_voice_state_update()`, `await fake.voice.next_identify()` et `await fake.voice.wait_for_packets(count)`, qui renvoie les paquets RTP déchiffrés sous forme d'objets `Packet` ; chaque connexion vocale est une `VoiceSession` dans `fake.voice.sessions`, sur un `VoiceSocket`, et `fake.voice.op(op, d)` envoie n'importe quel opcode vocal à la plus récente ;
- placer un utilisateur dans un salon vocal, le déplacer ou l'en sortir avec `None`, avec `await fake.move_user(guild_id, user_id, channel_id)` : il envoie le `VOICE_STATE_UPDATE` qu'enverrait Discord une fois le bot connecté, et avant cela il garde seulement l'état vocal, si bien que `ctx.voice.connect()` trouve le salon de l'utilisateur dans les deux cas ;
- jouer d'autres utilisateurs avec `fake.voice.clients_connect(user_ids)`, `fake.voice.client_disconnect(user_id)`, `fake.voice.speaking(user_id, ssrc, flags)`, `fake.voice.send_audio(...)`, qui envoie des paquets Opus au bot, `fake.voice.send_rtcp(packet)`, qui lui envoie un paquet RTCP, et `fake.voice.send_datagram(data)`, qui envoie des octets non chiffrés, comme le ferait un serveur défaillant ;
- déplacer ou déconnecter le bot comme le ferait un modérateur, avec `fake.move_bot(channel_id)` et `fake.kick_bot()` ;
- laisser la prochaine mise à jour d'état vocal sans réponse, comme le fait Discord quand le salon est plein, avec `fake.hold_voice_state()`, jusqu'à ce que l'`asyncio.Event` renvoyé soit déclenché ;
- définir `fake.mute`, `fake.deaf` et `fake.suppress` pour les états vocaux qu'il envoie à propos du bot, et `voice_order` pour l'ordre des deux événements que Discord envoie lors d'une connexion.
