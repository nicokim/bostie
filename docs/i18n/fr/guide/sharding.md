---
source: c45c92d8551bab86affc3711ec78970f48e886d8
---

# Sharding et reprise de session

![Trois Bostie qui courent à la file](../assets/mascot/bostie-running.webp){ align=right width="220" loading=lazy }

Discord répartit les serveurs d'un bot entre des connexions à la gateway appelées shards : le serveur `g` appartient au shard `(g >> 22) % shard_count`. Un bot présent sur plus de 2500 serveurs doit utiliser le sharding ; Discord ferme une connexion sans sharding avec le code 4011 et `start()` lève `ShardingRequired`.

## Shards dans un seul processus { #shards-in-one-process }

`Bot(shard_count="auto")`, la valeur par défaut, demande à Discord combien de shards utiliser et les fait tous tourner. Rien d'autre n'est nécessaire : bostie démarre les shards dans le respect des limites d'identification de Discord, et chaque shard envoie ses événements aux mêmes écouteurs, au même cache et aux mêmes gestionnaires. `event.shard_id` indique sur quel shard un événement est arrivé.

## Shards répartis entre processus { #shards-across-processes }

```python
--8<-- "docs/snippets/sharding/cluster.py"
```

`shard_count=16` fixe le total, et `shard_ids` choisit les shards que ce processus fait tourner ; `shard_ids` nécessite un `shard_count` explicite, et ses ids doivent être distincts et compris dans `range(shard_count)`.

Discord n'autorise qu'un nombre limité d'identifications simultanées par bot. bostie respecte cette limite au sein d'un processus ; entre processus, `before_identify` les coordonne. bostie attend `before_identify(shard_id)` avant chaque identification : le hook peut ainsi attendre une place dans un verrou que vos processus partagent. Dans l'exemple, `identify_lock` tient lieu de ce verrou partagé, conservé pendant les 5 secondes que Discord demande entre les identifications d'un même bucket. Si le hook lève une exception, bostie journalise l'erreur et procède quand même à l'identification.

## Événements de shard { #shard-events }

| Événement | Champs | Envoyé quand |
|---|---|---|
| `ShardReady` | `shard_id` | la nouvelle session d'un shard est prête et ses serveurs sont arrivés |
| `ShardDisconnect` | `shard_id`, `code`, `reason`, `resuming` | une connexion a été perdue ; avec `resuming`, le shard reprend sa session et rejoue les événements manqués, sinon il démarre une nouvelle session |
| `ShardStop` | `shard_id`, `code`, `reason` | un shard s'est arrêté définitivement, parce que `close()` a été appelé ou que Discord y a mis fin |
| `ShardBackpressure` | `shard_id`, `queued_bytes` | un shard a suspendu sa lecture parce que la boucle d'événements prend du retard |

Les reconnexions et reprises de session ne demandent rien de votre part. `bot.latency` est l'aller-retour moyen du heartbeat sur l'ensemble des shards, et `bot.gateway.shard(shard_id).latency` celui d'un shard.

## Reprendre la session après un redémarrage { #resuming-after-a-restart }

Un redémarrage démarre normalement une nouvelle session sur chaque shard, et les événements des minutes intermédiaires sont perdus. Avec `Bot(session_file="sessions.json")`, `close()` garde les sessions ouvertes et les écrit dans le fichier. Le `start()` suivant lit le fichier, le supprime, et reprend chaque session sauvegardée qui correspond au `shard_count` et aux ids de shard actuels : Discord rejoue alors les événements survenus pendant l'arrêt du bot. Un fichier illisible est journalisé et ignoré, et les shards sans session sauvegardée procèdent à une identification.

`bot.run()` se ferme sur Ctrl+C et SIGTERM : un redémarrage par un gestionnaire de processus ou un déploiement sauvegarde donc les sessions. Un plantage ne le fait pas, et le démarrage suivant procède à une identification. Une interaction rejouée plus de 5 secondes après sa création est ignorée avec un avertissement, car il n'est plus possible de répondre à son token à temps.
