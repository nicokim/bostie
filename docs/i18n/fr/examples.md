---
source: f54db29cb26a01ab63ca1c726a1815c968b1cf3a
description: "Exemples de bots bostie : commandes slash, Components V2, modales, événements, file musicale, enregistrement vocal, interactions HTTP, webhooks et rôles liés."
---

# Exemples

Voici les fichiers du dossier `examples/` du dépôt. La suite de tests vérifie leurs types et charge chacun d'eux, ce qui construit son bot et vérifie chaque définition de commande. Chacun s'exécute avec `BOSTIE_TOKEN` défini : `python examples/commands.py`.

## Commandes { #commands }

Des options typées, un groupe, l'autocomplétion, des commandes utilisateur et de message, des vérifications, un hook d'erreur et des traductions issues de catalogues gettext.

```python
--8<-- "examples/commands.py"
```

## Composants { #components }

Un tour des Components V2 avec un pagineur, un menu de sélection qui change la couleur d'un conteneur, et une expulsion qui attend un clic de confirmation.

```python
--8<-- "examples/components.py"
```

## Fenêtres modales { #modals }

Une fenêtre modale ouverte depuis un bouton et lue à l'envoi, et une autre qu'une commande attend.

```python
--8<-- "examples/modals.py"
```

## Événements { #events }

Des écouteurs par annotation, une union, un événement propre au bot, un écouteur de `ListenerError` et un jeu de devinettes construit sur `wait_for`.

```python
--8<-- "examples/events.py"
```

## Cycle de vie { #lifecycle }

Une base de données ouverte dans un écouteur de `Starting` et fermée dans un écouteur de `Stopped`, et deux tâches de fond.

```python
--8<-- "examples/lifecycle.py"
```

## Musique { #music }

Une file d'attente par serveur, avec passer, boucler et quitter.

```python
--8<-- "examples/music.py"
```

## Enregistrement { #recording }

Un fichier Ogg Opus par personne qui parle, jusqu'à `/stop`.

```python
--8<-- "examples/recording.py"
```

## Activité { #activity }

Une commande de point d'entrée et une commande slash qui ouvrent l'Activité de l'application.

```python
--8<-- "examples/activity.py"
```

## Interactions HTTP { #http-interactions }

Des commandes slash servies en HTTP par `ASGIApp`.

```python
--8<-- "examples/http_interactions.py"
```

## Script REST { #rest-script }

L'API REST sans gateway.

```python
--8<-- "examples/rest_script.py"
```

## Application web { #web-app }

Un bot démarré dans le lifespan d'une application web, avec une route qui publie via le client REST du bot.

```python
--8<-- "examples/web_app.py"
```

## Webhook { #webhook }

Publier via l'URL d'un webhook, depuis du code asynchrone et depuis du code ordinaire.

```python
--8<-- "examples/webhook.py"
```

## Rôles liés { #linked-roles }

Une page de vérification des rôles liés : la connexion OAuth2, l'échange du code et la connexion de rôle de l'utilisateur.

```python
--8<-- "examples/linked_roles.py"
```

## Bot modulaire { #modular-bot }

Un bot qui inclut un module par son nom et le recharge depuis une commande.

```python
--8<-- "examples/modular/bot.py"
```

```python
--8<-- "examples/modular/greetings.py"
```
