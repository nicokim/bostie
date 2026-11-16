---
render_macros: true
source: dc795d93f8d81e572155a4937bfd0da9add197bf
description: "bostie exécute en Rust la gateway Discord, le client HTTP et le moteur vocal DAVE ; vous écrivez des bots Python typés et async avec commandes slash et voix."
---

<div class="bostie-hero" markdown>
<div class="bostie-hero__copy" markdown>

# bostie

bostie est une bibliothèque Discord pour Python dont la gateway, le client HTTP et le moteur vocal tournent en Rust. Vous écrivez le bot en Python typé et asynchrone.

</div>

![Bostie, un Boston Terrier, vous accueille à bras ouverts](assets/mascot/bostie-welcoming.webp){ .bostie-hero__mascot width="200" }

</div>

!!! warning "Pré-alpha"
    L'API de bostie peut encore changer. Les commandes slash, utilisateur et de message, les composants, les fenêtres modales, les événements de la gateway, le cache, REST, les webhooks, les interactions HTTP, OAuth2, ainsi que la lecture et la réception audio fonctionnent dès aujourd'hui.

```python
--8<-- "docs/snippets/home/readme_example.py"
```

`/play` rejoint le salon vocal de l'utilisateur qui l'a lancée, diffuse l'URL et répond avec un bouton Stop. Le custom id du bouton contient l'id du serveur, et `@bot.on_component` le reconvertit en `int` : le bouton fonctionne donc toujours après un redémarrage du bot. `bot.run()` lit le token dans la variable d'environnement `BOSTIE_TOKEN` et envoie la commande à Discord si elle a changé.

{% include "callout.md" %}

## Ce pour quoi il est conçu { #what-it-is-built-for }

### La voix { #voice }

Depuis le 1er mars 2026, Discord n'accepte plus que les appels chiffrés de bout en bout ([DAVE](https://docs.discord.com/developers/topics/voice-connections)). Le moteur vocal de bostie tourne sur des threads Rust, hors du GIL : l'horloge de 20 ms, le décodage, le mixage, l'encodage Opus, le chiffrement du transport et DAVE. Python n'a qu'à garder rempli le tampon de chaque sink, 0,2 seconde d'audio par défaut. bostie lit des fichiers, des URL, des flux HLS et votre propre PCM ou Opus, mixe plusieurs sources, reçoit chaque personne qui parle séparément et enregistre les appels.

### Des bots qui survivent aux changements de Discord { #bots-that-survive-discords-changes }

Les champs, valeurs d'enum, types de composants et types d'interactions inconnus ne cassent jamais le décodage. Chaque événement, et chaque modèle décodé à partir d'un payload complet, conserve le JSON brut, et `bot.http` ainsi que les écouteurs d'événements bruts atteignent ce que bostie ne modélise pas encore.

### Des types conformes à la documentation de Discord { #types-that-match-discords-documentation }

Un champ que Discord envoie toujours n'est jamais `None`, et un objet partiel a sa propre classe : un membre résolu à partir d'une option de commande est un `ResolvedMember`, qui n'a ni `deaf` ni `mute`.

### Des commandes à partir des signatures { #commands-from-signatures }

Les options, les choix, les plages et l'autocomplétion viennent des annotations de type. Les noms, les descriptions, le nombre d'options et les choix sont vérifiés par rapport aux limites de Discord au moment où le décorateur s'exécute, et non quand Discord refuse la synchronisation.

### Des tests sans Discord { #tests-without-discord }

`bostie.testing.FakeDiscord` est une gateway et une API REST locales contre lesquelles exécuter votre vrai bot.

## Pour aller plus loin { #where-to-go-next }

- [Installez](install.md) bostie et ses prérequis de compilation.
- Écrivez un premier bot avec le [démarrage rapide](quickstart.md).
- Les guides expliquent une fonctionnalité par page, des [commandes slash](guide/commands.md) aux [tests](guide/testing.md).
- La section [voix](voice/index.md) couvre les connexions, la lecture, l'audio brut et la réception.
- La référence liste chaque [option de `Bot`](reference/bot-options.md), chaque [événement](reference/event-list.md) et l'[API](reference/api/bot.md).
