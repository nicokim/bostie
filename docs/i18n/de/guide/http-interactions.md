---
source: d24ff7127c79eec4345b1d42ca0265273465a567
description: "Empfange Discord-Interaktionen und Webhook-Events per HTTP mit einer ASGI-App statt über das Gateway, prüfe Signaturen oder nutze deinen eigenen Webserver."
---

# HTTP-Interaktionen und Webhook-Events

Discord kann Interaktionen statt über das Gateway an eine URL deiner App senden, und manche Events, die Webhook-Events, sendet es nur an eine URL. `ASGIApp` bedient beides aus demselben Bot, mit unveränderten Befehlen, Routen und Listenern.

```python
--8<-- "docs/snippets/http/http_bot.py"
```

`bostie.ASGIApp(bot)` ist eine ASGI-Anwendung: Stelle `app` mit einem beliebigen ASGI-Server bereit, hinter HTTPS. Trag dann im Developer Portal die **Interactions Endpoint URL** der App und, für Webhook-Events, ihre **Webhook Events URL** mit den gewünschten Event-Typen dort ein, wo sie lauscht. Discord prüft einen Endpunkt beim Speichern, mit einem PING und einer Anfrage mit falscher Signatur, und `ASGIApp` beantwortet beides so, wie Discord es erwartet.

## Was es tut { #what-it-does }

Beim Start des Servers ruft die App über den ASGI-Lifespan `bot.open()` auf, sofern der Bot nicht schon am Gateway läuft. Das:

1. öffnet den HTTP-Client des Bots;
2. holt die Anwendung, für ihre ID und den öffentlichen Schlüssel, der Discords Anfragen signiert, sowie den Benutzer des Bots;
3. sendet `Starting`, synchronisiert die Befehle und startet die [Hintergrund-Tasks](tasks.md).

Ein Fehler dabei bricht den Start des Servers mit diesem Fehler ab. Fährt der Server herunter, schließt die App den Bot, wenn sie ihn gestartet hat.

Jede Anfrage wird vor allem anderen geprüft: Nur `POST` wird angenommen (sonst 405), Bodys sind auf 4 MiB begrenzt (413), und eine Anfrage, deren `X-Signature-Ed25519` sich mit dem öffentlichen Schlüssel nicht verifizieren lässt, bekommt 401. Danach:

- Ein Interaktions-PING bekommt das PONG, das Discord erwartet.
- Jede andere Interaktion wird mit 202 beantwortet und an den Bot übergeben, der ihren Befehls-, Routen- oder Modal-Handler genauso ausführt wie beim Gateway; der Handler antwortet über Discords REST-API, mit `ctx.respond` und dem Rest. [Automatisches Aufschieben](responses.md#deferring) funktioniert genauso.
- Ein Webhook-Event erreicht die Listener des Bots und bekommt 204, ebenso das PING, das Discord an die Webhook Events URL sendet.

`ASGIApp(bot, token=..., public_key=...)` nimmt das Token, sonst `BOSTIE_TOKEN`, und den öffentlichen Schlüssel, sonst den, den die Anwendung meldet.

## Mit oder ohne Gateway { #with-or-without-the-gateway }

Allein betrieben hat die App keine Gateway-Verbindung: keine Gateway-Events, keine Cache-Updates, keine Sprachverbindungen. Befehle, Komponenten, Modals, REST und Webhook-Events funktionieren. Um beides zu haben, starte den Bot zusätzlich am Gateway, in derselben Event-Loop, und die App verwendet dann den laufenden Bot; Interaktionen kommen dann über HTTP an, und das Gateway liefert weiterhin Events.

## Webhook-Events { #webhook-events }

| Event | Gesendet, wenn |
|---|---|
| `ApplicationAuthorized` | ein Benutzer die App einem Server oder seinem Konto hinzugefügt hat |
| `ApplicationDeauthorized` | ein Benutzer die App entfernt hat oder ihre OAuth2-Tokens abgelaufen sind |
| `EntitlementCreate`, `EntitlementUpdate`, `EntitlementDelete` | sich ein Entitlement geändert hat |
| `QuestUserEnrollment` | ein Benutzer zu einer Quest hinzugefügt wurde; Discord dokumentiert es, sendet es aber noch nicht an Apps |
| `LobbyMessageCreate`, `LobbyMessageUpdate`, `LobbyMessageDelete` | eine Nachricht in einer Social-SDK-Lobby gesendet, bearbeitet oder gelöscht wurde |
| `GameDirectMessageCreate`, `GameDirectMessageUpdate`, `GameDirectMessageDelete` | sich eine Direktnachricht in einer Social-SDK-Sitzung geändert hat |

Hör mit `@bot.listen` auf sie wie auf Gateway-Events. Ein Webhook-Event-Typ, den bostie nicht modelliert, kommt als `RawEvent` an.

## In deinem eigenen Webserver { #in-your-own-web-server }

Die beiden Bausteine, die `ASGIApp` verwendet, sind öffentlich, für ein Web-Framework, das du schon betreibst:

```python
--8<-- "docs/snippets/http/own_server.py"
```

`verify_signature(public_key, signature, timestamp, body)` prüft die Header `X-Signature-Ed25519` und `X-Signature-Timestamp` einer Anfrage gegen den hexadezimalen öffentlichen Schlüssel der App; antworte mit 401, wenn es `False` zurückgibt. `decode_webhook_event(body)` gibt das typisierte Event einer Webhook-Events-Anfrage zurück, ein `RawEvent` für einen Typ, den bostie nicht modelliert, oder `None` für das PING. Für Interaktionen in deinem eigenen Server zeigt das [Beispiel für HTTP-Interaktionen](../examples.md#http-interactions) den ASGI-Weg, den bostie unterstützt.
