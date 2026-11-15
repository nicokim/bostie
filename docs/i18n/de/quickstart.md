---
render_macros: true
source: 4004163cb60675c429361e3c0739c96ffdfbc70f
---

# Schnellstart

![Bostie tippt Code in ein Terminal](assets/mascot/bostie-quickstart.webp){ align=right width="180" loading=lazy }

Auf dieser Seite erstellst du eine Discord-Anwendung, fügst sie einem Server hinzu und startest einen Bot mit einem Slash-Befehl. Sie setzt voraus, dass bostie [installiert](install.md) ist.

{% include "callout.md" %}

## Die Anwendung erstellen { #create-the-application }

1. Öffne das [Developer Portal](https://discord.com/developers/applications) und erstelle eine Anwendung.
2. Setze auf ihrer Seite **Bot** das Token zurück und kopiere es. Wer das Token hat, steuert den Bot, also halte es aus deinem Code und deinem Repository heraus.
3. Füge auf ihrer Seite **Installation** unter Guild Install die Scopes `applications.commands` und `bot` hinzu, öffne dann den Installationslink und füge die App einem Server hinzu, den du verwaltest.

## Den Bot schreiben { #write-the-bot }

Speichere das als `bot.py`:

```python
--8<-- "docs/snippets/quickstart/hello_bot.py"
```

`@bot.command` macht aus der Funktion den Slash-Befehl `/hello`. Sein Name ist der Name der Funktion, seine Beschreibung die erste Zeile des Docstrings. `ctx` ist der [`Context`](guide/responses.md) einer einzelnen Verwendung des Befehls: wer ihn ausgeführt hat, wo, und die Methoden, mit denen du antwortest.

## Starten { #run-it }

Übergib das Token über die Umgebungsvariable `BOSTIE_TOKEN` und starte die Datei:

```sh
export BOSTIE_TOKEN="your token"
python bot.py
```

`bot.run()` richtet das Logging auf INFO ein, verbindet sich und vergleicht die Befehle, die du definiert hast, mit denen, die bei Discord registriert sind. Der erste Start registriert `/hello` und loggt `commands synced (global): added ['hello'], ...`; spätere Starts senden nichts, solange sich kein Befehl geändert hat. Tippe `/hello` in deinem Server, und der Bot antwortet.

Strg+C oder ein SIGTERM von einem Prozessmanager beendet den Bot sauber.

## Eine Option hinzufügen { #add-an-option }

Parameter nach `ctx` werden zu den Optionen des Befehls, typisiert durch ihre Type Hints:

```python
--8<-- "docs/snippets/quickstart/roll_bot.py"
```

`sides` ist eine Ganzzahl-Option von 2 bis 100 mit dem Standardwert 6. Discord zeigt sie deshalb als optional an und lehnt Werte außerhalb des Bereichs ab, bevor der Bot sie sieht. Starte den Bot neu, und er sendet den geänderten Befehl.

## Weiter { #next }

- [Slash-Befehle](guide/commands.md) behandelt jeden Optionstyp, Auswahlmöglichkeiten, Autovervollständigung und Unterbefehle.
- [Auf Interaktionen antworten](guide/responses.md) erklärt `respond`, das Aufschieben, Folgenachrichten und ephemere Antworten.
- [Befehle registrieren](guide/registration.md) zeigt, wie du Befehle in einem einzelnen Testserver registrierst, solange du an ihnen arbeitest.
- [Einen Bot betreiben](guide/running.md) behandelt das Token, das Logging, den Lebenszyklus des Bots und wie du ihn innerhalb eines anderen Programms laufen lässt.
