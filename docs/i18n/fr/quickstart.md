---
render_macros: true
source: 879f90299466f4c668efe8cb4ef1edb576a11bc2
description: "Créez une application Discord, ajoutez-la à un serveur et lancez votre premier bot Python avec une commande slash et une option typée, pas à pas avec bostie."
---

# Démarrage rapide

![Bostie tape du code dans un terminal](assets/mascot/bostie-quickstart.webp){ align=right width="180" loading=lazy }

Cette page crée une application Discord, l'ajoute à un serveur et lance un bot avec une commande slash. Elle suppose que bostie est [installé](install.md).

{% include "callout.md" %}

## Créer l'application { #create-the-application }

1. Ouvrez le [Developer Portal](https://discord.com/developers/applications) et créez une application.
2. Sur sa page **Bot**, réinitialisez le token et copiez-le. Quiconque possède le token contrôle le bot : gardez-le hors de votre code et de votre dépôt.
3. Sur sa page **Installation**, ajoutez les scopes `applications.commands` et `bot` à l'installation sur un serveur (Guild Install), puis ouvrez le lien d'installation et ajoutez l'application à un serveur que vous gérez.

## Écrire le bot { #write-the-bot }

Enregistrez ceci sous le nom `bot.py` :

```python
--8<-- "docs/snippets/quickstart/hello_bot.py"
```

`@bot.command` transforme la fonction en commande slash `/hello`. Son nom est celui de la fonction et sa description est la première ligne de la docstring. `ctx` est le [`Context`](guide/responses.md) d'une utilisation de la commande : qui l'a lancée, où, et les méthodes qui y répondent.

## Le lancer { #run-it }

Passez le token par la variable d'environnement `BOSTIE_TOKEN` et lancez le fichier :

```sh
export BOSTIE_TOKEN="your token"
python bot.py
```

`bot.run()` configure la journalisation au niveau INFO, se connecte et compare les commandes que vous avez définies avec celles enregistrées sur Discord. Le premier démarrage enregistre `/hello` et journalise `commands synced (global): added ['hello'], ...` ; les démarrages suivants n'envoient rien, sauf si une commande a changé. Tapez `/hello` dans votre serveur et le bot répond.

Ctrl+C, ou un SIGTERM envoyé par un gestionnaire de processus, ferme proprement le bot.

## Ajouter une option { #add-an-option }

Les paramètres qui suivent `ctx` deviennent les options de la commande, typées par leurs annotations :

```python
--8<-- "docs/snippets/quickstart/roll_bot.py"
```

`sides` est une option entière de 2 à 100 qui vaut 6 par défaut : Discord l'affiche donc comme facultative et refuse les valeurs hors de la plage avant même que le bot ne les voie. Redémarrez le bot et il envoie la commande modifiée.

## Et ensuite { #next }

- [Commandes slash](guide/commands.md) couvre tous les types d'options, les choix, l'autocomplétion et les sous-commandes.
- [Répondre aux interactions](guide/responses.md) explique `respond`, les réponses différées, les messages de suivi et les réponses éphémères.
- [Enregistrer les commandes](guide/registration.md) montre comment enregistrer les commandes dans un serveur de test pendant que vous travaillez dessus.
- [Lancer un bot](guide/running.md) couvre le token, la journalisation, le cycle de vie du bot et son exécution au sein d'un autre programme.
