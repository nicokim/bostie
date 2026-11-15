---
source: afe273b0bb674620ed6eaaab978ec64bbcb23c8e
---

# Beispiele

Das sind die Dateien aus dem Ordner `examples/` des Repositorys. Die Testsuite prüft ihre Typen und lädt jede einzelne, wodurch ihr Bot gebaut und jede Befehlsdefinition geprüft wird. Jede läuft, wenn `BOSTIE_TOKEN` gesetzt ist: `python examples/commands.py`.

## Befehle { #commands }

Typisierte Optionen, eine Gruppe, Autovervollständigung, Benutzer- und Nachrichtenbefehle, Checks, ein Fehler-Hook und Übersetzungen aus gettext-Katalogen.

```python
--8<-- "examples/commands.py"
```

## Komponenten { #components }

Ein Rundgang durch Components V2 mit einem Seitenumschalter, einem Auswahlmenü, das einen Container umfärbt, und einem Kick, der auf einen bestätigenden Klick wartet.

```python
--8<-- "examples/components.py"
```

## Modals { #modals }

Ein Modal, das von einem Button geöffnet und beim Absenden ausgelesen wird, und eines, auf das ein Befehl wartet.

```python
--8<-- "examples/modals.py"
```

## Events { #events }

Listener per Annotation, eine Union, ein eigenes Event des Bots, ein `ListenerError`-Listener und ein Ratespiel, das auf `wait_for` aufbaut.

```python
--8<-- "examples/events.py"
```

## Lebenszyklus { #lifecycle }

Eine Datenbank, die in einem `Starting`-Listener geöffnet und in einem `Stopped`-Listener geschlossen wird, und zwei Hintergrund-Tasks.

```python
--8<-- "examples/lifecycle.py"
```

## Musik { #music }

Eine Warteschlange pro Server mit Überspringen, Wiederholen und Verlassen.

```python
--8<-- "examples/music.py"
```

## Aufnahme { #recording }

Eine Ogg-Opus-Datei pro Sprecher bis `/stop`.

```python
--8<-- "examples/recording.py"
```

## Aktivität { #activity }

Ein Einstiegspunkt-Befehl und ein Slash-Befehl, die die Aktivität der App öffnen.

```python
--8<-- "examples/activity.py"
```

## HTTP-Interaktionen { #http-interactions }

Slash-Befehle, die `ASGIApp` über HTTP bereitstellt.

```python
--8<-- "examples/http_interactions.py"
```

## REST-Skript { #rest-script }

Die REST-API ohne Gateway.

```python
--8<-- "examples/rest_script.py"
```

## Web-App { #web-app }

Ein Bot, der innerhalb des Lifespans einer Web-App gestartet wird, mit einer Route, die über den REST-Client des Bots postet.

```python
--8<-- "examples/web_app.py"
```

## Webhook { #webhook }

Über eine Webhook-URL posten, aus asynchronem und aus gewöhnlichem Code.

```python
--8<-- "examples/webhook.py"
```

## Verknüpfte Rollen { #linked-roles }

Eine Verifizierungsseite für verknüpfte Rollen: der OAuth2-Login, der Austausch des Codes und die Rollenverbindung des Benutzers.

```python
--8<-- "examples/linked_roles.py"
```

## Modularer Bot { #modular-bot }

Ein Bot, der ein Modul per Name einbindet und es über einen Befehl neu lädt.

```python
--8<-- "examples/modular/bot.py"
```

```python
--8<-- "examples/modular/greetings.py"
```
