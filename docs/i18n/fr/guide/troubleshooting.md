---
source: 14575d90a6a022a3c0cbfce22c048b404bcf4957
---

# Dépannage

![Bostie réfléchissant jusqu'à ce que le point d'interrogation devienne une ampoule](../assets/mascot/bostie-thinking.webp){ align=right width="180" loading=lazy }

bostie consigne un avertissement pour chaque erreur qu'il peut détecter. Cette page les classe selon ce que vous observez, avec la ligne du journal à chercher et la correction.

## Une commande affiche « L'application n'a pas répondu » { #a-command-says-the-application-did-not-respond }

| Ligne du journal | Cause | Correction |
|---|---|---|
| `nothing handles interaction ... (command 'name')` | Discord a encore une commande que le bot en cours d'exécution ne définit pas, souvent après un renommage avec `auto_sync=False`, ou la commande vit dans un autre processus | synchronisez les commandes ([Enregistrer les commandes](registration.md)) |
| `nothing handles interaction ... (custom_id '...')` | aucun template `on_component` ou `on_modal` ne correspond au custom id | comparez le template aux id que vous envoyez ; construisez-les avec `route.id(...)` |
| `... returned without answering` | le gestionnaire s'est terminé sans `respond`, `defer` ni `modal` | répondez sur chaque chemin, y compris les retours anticipés |
| `... returned after deferring without answering` | il a différé sans jamais répondre, donc « réfléchit… » reste affiché jusqu'à l'expiration du token | appelez `respond` après le travail lent |
| `interaction ... reached its handler ... s after it was received; something is blocking the event loop` | du code bloquant dans un gestionnaire ou un écouteur a monopolisé la boucle d'événements au-delà des 3 secondes de Discord | déplacez le travail dans `asyncio.to_thread`, ou baissez `auto_defer` |
| `interaction ... was replayed ... s after it was created; its token is dead` | l'interaction est arrivée dans le rejeu d'une session reprise, trop tard pour y répondre | rien ; l'utilisateur peut la relancer |

Une exception non gérée dans un gestionnaire est consignée avec sa trace d'appels, et l'utilisateur voit « Something went wrong. » : ajoutez un [hook d'erreur](checks.md#error-hooks) pour répondre quelque chose de plus utile.

## Les événements n'arrivent pas { #events-do-not-arrive }

- `... listens to EVENT, which needs the INTENT intent; add it to Bot(intents=...)` : le bot n'a pas demandé l'intent. Ajoutez-le et, pour un intent privilégié, activez-le aussi dans le Developer Portal.
- `start()` lève `PrivilegedIntentsRequired` : le bot a demandé un intent privilégié que le portail n'autorise pas. Activez-le sous **Bot** → **Privileged Gateway Intents**, ou retirez-le de `intents`.
- `message.content` est vide sur les serveurs : il manque l'intent `MESSAGE_CONTENT`.
- `EVENT has an unexpected shape (...); typed listeners skip it` : Discord a envoyé un payload qui ne correspond pas à la classe de l'événement, consigné une fois par nom d'événement. Les écouteurs `RawEvent` le reçoivent quand même ; merci de signaler la ligne.
- `old` vaut `None` sur les événements de mise à jour : le cache ne contenait pas l'objet ; voyez [Cache](cache.md#what-it-keeps) pour ce qu'il conserve.

## La gateway { #the-gateway }

- `shard N paused reading: ... bytes of events are waiting because the event loop is not keeping up` : quelque chose bloque la boucle d'événements ; voyez [Contre-pression](gateway.md#backpressure).
- `close code N (...) means Discord refused what bostie sent; this is likely a bostie bug, please report it` : les codes 4001, 4002, 4005 et 4008 signifient que bostie a envoyé quelque chose que Discord a refusé.
- `the members of guild N stopped arriving; the cache lacks some` : une requête de membres pour le cache a expiré après 30 secondes par lot.
- `start()` lève `InvalidToken` : le token est faux ou a été réinitialisé ; copiez-le à nouveau depuis la page **Bot**.

## REST { #rest }

- `N invalid requests (401, 403, 429) in the last 10 minutes; Discord bans the IP at 10 000` : le bot continue de faire des requêtes que Discord refuse, en général à cause de permissions manquantes ou d'un webhook supprimé. Trouvez les appels qui échouent grâce aux exceptions qu'ils lèvent.
- `RateLimited` levé sans qu'aucune requête n'atteigne Discord : la route reste limitée plus longtemps que `max_rate_limit_wait` ; augmentez-le dans [`HTTPSettings`](rest.md#rate-limits-retries-and-timeouts) ou réessayez plus tard.

## Réponses { #answers }

- `ephemeral=... is ignored because the message being edited is ...` : un message ne peut pas passer d'éphémère à public, ou l'inverse, après son envoi.
- `Discord allows 5 follow-ups to an interaction of an app installed only to the user` : le sixième message de suivi d'une application installée par l'utilisateur échoue.
- `the error hook ... left interaction ... unanswered; bostie answered 'Something went wrong.'` : faites répondre le hook.

## Voix { #voice }

- `nobody hears the bot in guild N: it is server muted`, ou `it is suppressed on this stage; use vc.stage('request')` : un modérateur a rendu le bot muet, ou il fait partie du public d'un salon de conférence.
- `a moderator deafened the bot in guild N, so Discord sends it no audio` : les récepteurs ne reçoivent rien tant qu'il n'est pas sorti de la sourdine.
- `VoiceConnectError` avec la raison `TIMEOUT` : Discord n'a pas répondu à la connexion dans les `timeout` secondes ; le salon est peut-être plein, ou le bot n'a pas la permission Se connecter.
- `MissingIntent: voice needs Intents.GUILD_VOICE_STATES` : la voix a besoin de cet intent, que `Intents.DEFAULT` inclut.

## Signaler un bug { #reporting-a-bug }

`python -m bostie` affiche les versions à inclure, et `bostie.voice.versions()` ajoute libopus et DAVE pour les problèmes de voix. Réglez le logger concerné sur `DEBUG` avant de démarrer le bot, comme le montre [Journalisation](running.md#logging), et incluez les lignes autour du problème, sans le token.
