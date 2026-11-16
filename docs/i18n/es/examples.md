---
source: f54db29cb26a01ab63ca1c726a1815c968b1cf3a
description: "Ejemplos de bots con bostie: comandos slash, Components V2, modales, eventos, cola de música, grabación de voz, interacciones HTTP, webhooks y roles vinculados."
---

# Ejemplos

Estos son los archivos de la carpeta `examples/` del repositorio. La suite de pruebas comprueba sus tipos y carga cada uno, lo que construye su bot y verifica cada definición de comando. Cada uno se ejecuta con `BOSTIE_TOKEN` definido: `python examples/commands.py`.

## Comandos { #commands }

Opciones tipadas, un grupo, autocompletado, comandos de usuario y de mensaje, checks, un hook de errores y traducciones desde catálogos de gettext.

```python
--8<-- "examples/commands.py"
```

## Componentes { #components }

Un recorrido por Components V2 con un paginador, un menú de selección que cambia el color de un contenedor y una expulsión que espera un clic de confirmación.

```python
--8<-- "examples/components.py"
```

## Modales { #modals }

Un modal que se abre desde un botón y se lee al enviarse, y otro que un comando espera.

```python
--8<-- "examples/modals.py"
```

## Eventos { #events }

Listeners por anotación, una unión, un evento propio del bot, un listener de `ListenerError` y un juego de adivinanzas construido con `wait_for`.

```python
--8<-- "examples/events.py"
```

## Ciclo de vida { #lifecycle }

Una base de datos que se abre en un listener de `Starting` y se cierra en uno de `Stopped`, y dos tareas en segundo plano.

```python
--8<-- "examples/lifecycle.py"
```

## Música { #music }

Una cola por servidor con saltar, repetir y salir.

```python
--8<-- "examples/music.py"
```

## Grabación { #recording }

Un archivo Ogg Opus por cada persona que habla, hasta `/stop`.

```python
--8<-- "examples/recording.py"
```

## Actividad { #activity }

Un comando de punto de entrada y un comando slash que abren la actividad de la aplicación.

```python
--8<-- "examples/activity.py"
```

## Interacciones por HTTP { #http-interactions }

Comandos slash servidos por HTTP con `ASGIApp`.

```python
--8<-- "examples/http_interactions.py"
```

## Script de REST { #rest-script }

La API REST sin gateway.

```python
--8<-- "examples/rest_script.py"
```

## Aplicación web { #web-app }

Un bot iniciado dentro del lifespan de una aplicación web, con una ruta que publica a través del cliente REST del bot.

```python
--8<-- "examples/web_app.py"
```

## Webhook { #webhook }

Publicar a través de la URL de un webhook, desde código asíncrono y desde código normal.

```python
--8<-- "examples/webhook.py"
```

## Roles vinculados { #linked-roles }

Una página de verificación de roles vinculados: el inicio de sesión con OAuth2, el intercambio del código y la conexión de rol del usuario.

```python
--8<-- "examples/linked_roles.py"
```

## Bot modular { #modular-bot }

Un bot que incluye un módulo por nombre y lo recarga desde un comando.

```python
--8<-- "examples/modular/bot.py"
```

```python
--8<-- "examples/modular/greetings.py"
```
