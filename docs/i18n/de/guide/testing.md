---
source: 573e06a9566af6c8a906b642d99c9a92e03ef67b
description: "Teste einen Discord-Bot ohne Discord: lokales Fake-Gateway, Fake-REST-API und Fake-Sprachserver, sende Interaktionen und Events und prüfe, was er sendet."
---

# Deinen Bot testen

![Bostie sucht mit einer Lupe, bis ein Häkchen erscheint](../assets/mascot/bostie-detective.webp){ align=right width="180" loading=lazy }

`bostie.testing` betreibt ein Fake-Discord auf localhost: ein Gateway, eine REST-API mit den Routen, die ein Bot am häufigsten nutzt, und optional einen Sprachserver. Dein echter Bot verbindet sich damit, und der Test steuert Discords Seite: Er sendet Events und Interaktionen und prüft dann, was der Bot zurückgesendet hat. Es braucht das Extra `testing` ([Installation](../install.md)), und bostie selbst lässt seine eigene Testsuite darauf laufen.

```python
--8<-- "docs/snippets/testing/test_greeter.py"
```

`run(scenario)` startet ein `FakeDiscord`, führt `async def scenario(fake)` innerhalb von 10 Sekunden dagegen aus und fährt es wieder herunter; es ist eine gewöhnliche Funktion, pytest braucht also kein Plugin. `async with running(bot, fake):` startet den Bot am Fake, wartet, bis er bereit ist, und schließt ihn am Ende. Richte den Bot mit `Bot(api_url=fake.api_url)` auf das Fake aus, oder setz `BOSTIE_API_URL`, wenn der Bot woanders gebaut wird.

## Discords Seite steuern { #driving-discords-side }

| Aufruf | Bewirkt |
|---|---|
| `await fake.interaction(type, data)` | sendet ein `INTERACTION_CREATE` und gibt die erste Antwort des Bots als `Recorded`-Anfrage zurück |
| `await fake.dispatch(name, d, shard=0)` | sendet ein beliebiges Gateway-Event und gibt seine Sequenznummer zurück |
| `fake.expect_callback(payload)` | für eine Interaktion, die du selbst sendest: ein Future für ihre erste Antwort |
| `await fake.send_op(op, d)` | sendet einen Opcode, der kein Dispatch ist, etwa 7 (Reconnect) oder 9 (ungültige Sitzung) |
| `await fake.close_gateway(code)`, `fake.drop_gateway()` | schließt die Verbindung mit einem Close-Code oder bricht sie ohne ab |
| `fake.hold_acks()` | beantwortet keine Heartbeats mehr, wie es eine tote Verbindung täte |
| `await fake.next_identify()`, `await fake.next_resume()` | wartet auf das nächste IDENTIFY oder RESUME des Bots |

Interaktionstypen sind Discords Nummern: 2 für einen Befehl, 3 für eine Komponente, 4 für die Autovervollständigung, 5 für ein abgesendetes Modal.

`bostie.testing.payloads` baut Discords Payloads aus den Beispielen seiner Dokumentation, mit frischen IDs:

- `command(name, *options)`, `option(name, type, value, focused=False)`, `subcommand(...)`, `group(...)` und `resolved()` für Befehle und die Autovervollständigung;
- `context_menu(type, name, target_id, resolved)` für Benutzer- und Nachrichtenbefehle;
- `button(custom_id)`, `select(custom_id, *values)` und `modal(custom_id)` für Komponenten und Modals;
- `message(...)`, `guild(...)`, `member(...)`, `user(...)`, `role(...)`, `channel(...)`, `voice_channel(...)`, `stage_channel(...)` und `voice_state(...)` für Events, jeweils mit Schlüsselwortargumenten, die Felder überschreiben;
- `fixture(name)` für jeden dokumentierten Payload, den bostie mitliefert, und `snowflake()` für eine neue ID.

## Prüfen, was der Bot gesendet hat { #checking-what-the-bot-sent }

`fake.requests` listet jede REST-Anfrage als `Recorded`, und `fake.expect_request(method, route_prefix)` gibt ein Future für die nächste passende zurück, für eine Anfrage, die der Bot von sich aus stellt, etwa aus einem Task. Ein `Recorded` hat `method`, `path` (relativ zur API-Basis), `query`, `headers`, `json` und bei Uploads `parts`. Eine `Recorded`-Antwort auf eine Interaktion hat den Callback-Body in `json`, wie oben. `fake.gateway_received` ist eine Warteschlange der Payloads, die der Bot über das Gateway gesendet hat, und `fake.connections` die Gateway-Verbindungen, `Connection`-Objekte mit ihren `received`-Payloads, `shard` und `close_code`.

`fake.cdn_file(path, data)` stellt `data` unter einer CDN-URL bereit, die es zurückgibt, für einen Anhang oder ein Asset, das der Bot herunterlädt, und `fake.cdn_requests` zeichnet diese Downloads auf.

Das Fake hält den Zustand, den diese Routen brauchen, und Tests können ihn lesen oder vorbelegen: `fake.guilds`, `fake.channels`, `fake.users`, `fake.members`, `fake.messages`, `fake.commands` und `fake.guild_commands`, dazu `fake.put_count` für Befehlssynchronisierungen. `fake.ready_guilds` legt die Server-IDs fest, die `READY` aufführt.

## Fehler und Timing { #failures-and-timing }

- `fake.fail(method, route_prefix, status, json)` beantwortet die nächste passende Anfrage mit einem Fehler und ersetzt eine Route, die das Fake nicht bedient.
- `fake.rate_limit(method, route_prefix, scope="user", retry_after=0.1)` beantwortet sie mit einem 429.
- `fake.answer(method, route_prefix, body, content_type)` beantwortet sie mit einem Body, der kein JSON ist.
- `fake.hold(method, route_prefix)` lässt sie warten, bis du das zurückgegebene `asyncio.Event` setzt; `fake.hold_identify(shard)` und `fake.hold_hello()` tun dasselbe für den Gateway-Handshake.
- `await settle()` gibt einige Male an die Event-Loop ab, damit Callbacks laufen, die durch das, was du gerade getan hast, eingeplant wurden.

Um auf etwas zu warten, das der Bot in einem Listener tut, lass ihn [ein eigenes Event senden](events.md#events-of-your-own) und warte darauf, wie es der zweite Test tut, statt zu schlafen.

`FakeDiscord(compress=True)` komprimiert das Gateway mit zstd, was `compression.zstd` aus Python 3.14 braucht; `heartbeat_interval` legt das Intervall fest, das es verlangt, standardmäßig Discords 41 250 ms.

## Sprachkanäle { #voice }

`FakeDiscord(voice=True)` fügt einen Sprachkanal und einen Stage-Kanal hinzu, beantwortet Updates des Sprachzustands und betreibt ein Sprach-Gateway und einen UDP-Server als `fake.voice`, sodass `ctx.voice.connect()` und `vc.play(...)` dagegen funktionieren. Es spricht kein DAVE; das decken die Rust-Tests von bostie ab. Der Test kann:

- mit `await fake.next_voice_state_update()`, `await fake.voice.next_identify()` und `await fake.voice.wait_for_packets(count)` auf das warten, was der Bot gesendet hat; Letzteres gibt die entschlüsselten RTP-Pakete als `Packet`-Objekte zurück. Jede Sprachverbindung ist eine `VoiceSession` in `fake.voice.sessions`, auf einem `VoiceSocket`, und `fake.voice.op(op, d)` sendet der neuesten einen beliebigen Sprach-Opcode;
- als andere Benutzer handeln, mit `fake.voice.clients_connect(user_ids)`, `fake.voice.client_disconnect(user_id)`, `fake.voice.speaking(user_id, ssrc, flags)`, `fake.voice.send_audio(...)`, das Opus-Pakete an den Bot sendet, `fake.voice.send_rtcp(packet)`, das ihm ein RTCP-Paket sendet, und `fake.voice.send_datagram(data)`, das Bytes unversiegelt sendet, wie es ein defekter Server täte;
- den Bot verschieben oder trennen, wie es ein Moderator täte, mit `fake.move_bot(channel_id)` und `fake.kick_bot()`;
- mit `fake.hold_voice_state()` das nächste Update des Sprachzustands unbeantwortet lassen, wie Discord es bei einem vollen Kanal tut, bis das zurückgegebene `asyncio.Event` gesetzt wird;
- `fake.mute`, `fake.deaf` und `fake.suppress` für die Sprachzustände setzen, die es über den Bot sendet, und `voice_order` für die Reihenfolge der beiden Events, die Discord beim Beitreten sendet.
