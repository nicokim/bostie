---
render_macros: true
source: dc795d93f8d81e572155a4937bfd0da9add197bf
description: "bostie betreibt Discord-Gateway, HTTP-Client und DAVE-Sprach-Engine in Rust, du schreibst typisierte async Python-Bots mit Slash-Befehlen und Sprachkanälen."
---

<div class="bostie-hero" markdown>
<div class="bostie-hero__copy" markdown>

# bostie

bostie ist eine Discord-Bibliothek für Python, deren Gateway, HTTP-Client und Sprach-Engine in Rust laufen. Den Bot selbst schreibst du in typisiertem, asynchronem Python.

</div>

![Bostie, ein Boston Terrier, begrüßt dich mit offenen Armen](assets/mascot/bostie-welcoming.webp){ .bostie-hero__mascot width="200" }

</div>

!!! warning "Pre-Alpha"
    Die API von bostie kann sich noch ändern. Slash-, Benutzer- und Nachrichtenbefehle, Komponenten, Modals, Gateway-Events, der Cache, REST, Webhooks, HTTP-Interaktionen, OAuth2 sowie Wiedergabe und Empfang in Sprachkanälen funktionieren schon heute.

```python
--8<-- "docs/snippets/home/readme_example.py"
```

`/play` tritt dem Sprachkanal des Benutzers bei, der den Befehl ausgeführt hat, streamt die URL und antwortet mit einem Stopp-Button. Die Custom-ID des Buttons trägt die Server-ID, und `@bot.on_component` liest sie wieder als `int` aus. Deshalb funktioniert der Button auch nach einem Neustart des Bots. `bot.run()` liest das Token aus der Umgebungsvariable `BOSTIE_TOKEN` und sendet den Befehl an Discord, wenn er sich geändert hat.

{% include "callout.md" %}

## Wofür es gebaut ist { #what-it-is-built-for }

### Sprachkanäle { #voice }

Seit dem 1. März 2026 akzeptiert Discord nur noch Ende-zu-Ende-verschlüsselte Anrufe ([DAVE](https://docs.discord.com/developers/topics/voice-connections)). Die Sprach-Engine von bostie läuft auf Rust-Threads, außerhalb des GIL: der 20-ms-Takt, das Dekodieren, das Mischen, die Opus-Kodierung, die Transportverschlüsselung und DAVE. Python muss nur den Puffer jedes Sinks gefüllt halten, standardmäßig 0,2 Sekunden Audio. bostie spielt Dateien, URLs, HLS-Streams und dein eigenes PCM oder Opus ab, mischt mehrere Quellen, empfängt jeden Sprecher einzeln und nimmt Anrufe auf.

### Bots, die Discords Änderungen überstehen { #bots-that-survive-discords-changes }

Unbekannte Felder, Enum-Werte, Komponententypen und Interaktionstypen bringen das Dekodieren nie zum Scheitern. Jedes Event und jedes Modell, das aus einem vollständigen Payload dekodiert wird, behält das rohe JSON, und mit `bot.http` und Roh-Event-Listenern erreichst du alles, was bostie noch nicht modelliert.

### Typen, die zu Discords Dokumentation passen { #types-that-match-discords-documentation }

Ein Feld, das Discord immer sendet, ist nie `None`, und ein partielles Objekt hat eine eigene Klasse: Ein Mitglied, das aus einer Befehlsoption aufgelöst wird, ist ein `ResolvedMember` und hat weder `deaf` noch `mute`.

### Befehle aus Signaturen { #commands-from-signatures }

Optionen, Auswahlmöglichkeiten, Wertebereiche und Autovervollständigung ergeben sich aus den Type Hints. Namen, Beschreibungen, die Anzahl der Optionen und die Auswahlmöglichkeiten werden schon beim Ausführen des Decorators gegen Discords Limits geprüft, nicht erst, wenn Discord die Synchronisierung ablehnt.

### Tests ohne Discord { #tests-without-discord }

`bostie.testing.FakeDiscord` ist ein lokales Gateway samt REST-API, gegen das du deinen echten Bot laufen lässt.

## Wie es weitergeht { #where-to-go-next }

- [Installiere](install.md) bostie und seine Build-Voraussetzungen.
- Schreib im [Schnellstart](quickstart.md) einen ersten Bot.
- Die Anleitungen erklären je eine Funktion pro Seite, von [Slash-Befehlen](guide/commands.md) bis zum [Testen](guide/testing.md).
- Der Abschnitt [Sprachkanäle](voice/index.md) behandelt Verbindungen, Wiedergabe, rohes Audio und Empfang.
- Die Referenz listet jede [`Bot`-Option](reference/bot-options.md), jedes [Event](reference/event-list.md) und die [API](reference/api/bot.md) auf.
