---
source: 04dd5c7d848eed6b7fe26c0af24d76fdfca1a0d2
---

# Interactions HTTP et événements webhook

Discord peut envoyer les interactions à une URL de votre application plutôt qu'à la gateway, et il envoie certains événements, les événements webhook, uniquement à une URL. `ASGIApp` sert les deux depuis le même bot, avec ses commandes, ses routes et ses écouteurs inchangés.

```python
--8<-- "docs/snippets/http/http_bot.py"
```

`bostie.ASGIApp(bot)` est une application ASGI : servez `app` avec n'importe quel serveur ASGI, derrière HTTPS. Ensuite, dans le Developer Portal, réglez l'**Interactions Endpoint URL** de l'application et, pour les événements webhook, sa **Webhook Events URL**, avec les types d'événements voulus, sur l'adresse où elle écoute. Discord vérifie un endpoint quand vous l'enregistrez, avec un PING et une requête dotée d'une signature erronée, et `ASGIApp` répond aux deux comme Discord l'attend.

## Ce qu'elle fait { #what-it-does }

Au démarrage du serveur, via le lifespan ASGI, l'application appelle `bot.open()`, sauf si le bot tourne déjà sur la gateway, ce qui :

1. ouvre le client HTTP du bot ;
2. récupère l'application, pour son id et la clé publique qui signe les requêtes de Discord, ainsi que l'utilisateur du bot ;
3. envoie `Starting`, synchronise les commandes et lance les [tâches de fond](tasks.md).

Un échec à ce stade arrête le démarrage du serveur avec l'erreur. À l'arrêt du serveur, l'application ferme le bot si c'est elle qui l'a démarré.

Chaque requête est vérifiée avant toute autre chose : seul `POST` est accepté (405 sinon), les corps sont plafonnés à 4 Mio (413), et une requête dont la `X-Signature-Ed25519` ne se vérifie pas avec la clé publique reçoit 401. Ensuite :

- Un PING d'interaction reçoit le PONG que Discord attend.
- Toute autre interaction reçoit 202 et est confiée au bot, qui exécute son gestionnaire de commande, de route ou de fenêtre modale comme il le ferait pour la gateway ; le gestionnaire répond par l'API REST de Discord, avec `ctx.respond` et les autres méthodes. La [réponse différée automatique](responses.md#deferring) fonctionne de la même façon.
- Un événement webhook atteint les écouteurs du bot et reçoit 204, tout comme le PING que Discord envoie à la Webhook Events URL.

`ASGIApp(bot, token=..., public_key=...)` accepte le token, sinon `BOSTIE_TOKEN`, et la clé publique, sinon celle que l'application déclare.

## Avec ou sans la gateway { #with-or-without-the-gateway }

Servie seule, l'application n'a pas de connexion à la gateway : pas d'événements de la gateway, pas de mises à jour du cache, pas de voix. Les commandes, les composants, les fenêtres modales, REST et les événements webhook fonctionnent. Pour avoir les deux, démarrez aussi le bot sur la gateway, dans la même boucle d'événements : l'application utilise alors le bot en cours d'exécution ; les interactions arrivent par HTTP et la gateway livre toujours les événements.

## Événements webhook { #webhook-events }

| Événement | Envoyé quand |
|---|---|
| `ApplicationAuthorized` | un utilisateur a ajouté l'application à un serveur ou à son compte |
| `ApplicationDeauthorized` | un utilisateur a retiré l'application, ou ses tokens OAuth2 ont expiré |
| `EntitlementCreate`, `EntitlementUpdate`, `EntitlementDelete` | un entitlement a changé |
| `QuestUserEnrollment` | un utilisateur a été inscrit à une Quête ; Discord le documente mais ne l'envoie pas encore aux applications |
| `LobbyMessageCreate`, `LobbyMessageUpdate`, `LobbyMessageDelete` | un message a été envoyé, modifié ou supprimé dans un lobby du Social SDK |
| `GameDirectMessageCreate`, `GameDirectMessageUpdate`, `GameDirectMessageDelete` | un message privé d'une session du Social SDK a changé |

Écoutez-les avec `@bot.listen`, comme les événements de la gateway. Un type d'événement webhook que bostie ne modélise pas arrive sous forme de `RawEvent`.

## Dans votre propre serveur web { #in-your-own-web-server }

Les deux briques qu'utilise `ASGIApp` sont publiques, pour un framework web que vous faites déjà tourner :

```python
--8<-- "docs/snippets/http/own_server.py"
```

`verify_signature(public_key, signature, timestamp, body)` vérifie les en-têtes `X-Signature-Ed25519` et `X-Signature-Timestamp` d'une requête avec la clé publique hexadécimale de l'application ; répondez 401 quand elle renvoie `False`. `decode_webhook_event(body)` renvoie l'événement typé d'une requête Webhook Events, un `RawEvent` pour un type que bostie ne modélise pas, ou `None` pour le PING. Pour des interactions dans votre propre serveur, l'[exemple d'interactions HTTP](../examples.md#http-interactions) montre la voie ASGI, celle que bostie prend en charge.
