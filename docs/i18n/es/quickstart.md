---
render_macros: true
source: 879f90299466f4c668efe8cb4ef1edb576a11bc2
description: "Crea una aplicación de Discord, agrégala a un servidor y ejecuta tu primer bot en Python con un comando slash y una opción tipada, paso a paso con bostie."
---

# Inicio rápido

![Bostie escribe código en una terminal](assets/mascot/bostie-quickstart.webp){ align=right width="180" loading=lazy }

Esta página crea una aplicación de Discord, la agrega a un servidor y ejecuta un bot con un comando slash. Da por hecho que bostie ya está [instalado](install.md).

{% include "callout.md" %}

## Crear la aplicación { #create-the-application }

1. Abre el [Developer Portal](https://discord.com/developers/applications) y crea una aplicación.
2. En su página **Bot**, restablece el token y cópialo. Cualquiera que tenga el token controla el bot, así que mantenlo fuera de tu código y de tu repositorio.
3. En su página **Installation**, agrega los scopes `applications.commands` y `bot` a la instalación en servidor (Guild Install), luego abre el enlace de instalación y agrega la aplicación a un servidor que administres.

## Escribir el bot { #write-the-bot }

Guarda esto como `bot.py`:

```python
--8<-- "docs/snippets/quickstart/hello_bot.py"
```

`@bot.command` convierte la función en el comando slash `/hello`. Su nombre es el nombre de la función y su descripción es la primera línea del docstring. `ctx` es el [`Context`](guide/responses.md) de un uso del comando: quién lo ejecutó, dónde, y los métodos para responder.

## Ejecutarlo { #run-it }

Pasa el token con la variable de entorno `BOSTIE_TOKEN` y ejecuta el archivo:

```sh
export BOSTIE_TOKEN="your token"
python bot.py
```

`bot.run()` configura el logging en INFO, se conecta y compara los comandos que definiste con los registrados en Discord. El primer arranque registra `/hello` y escribe en el log `commands synced (global): added ['hello'], ...`; los arranques siguientes no envían nada a menos que un comando haya cambiado. Escribe `/hello` en tu servidor y el bot responde.

Ctrl+C, o un SIGTERM de un gestor de procesos, cierra el bot de forma limpia.

## Agregar una opción { #add-an-option }

Los parámetros que van después de `ctx` se convierten en las opciones del comando, con el tipo que indican sus anotaciones:

```python
--8<-- "docs/snippets/quickstart/roll_bot.py"
```

`sides` es una opción entera de 2 a 100 que vale 6 por defecto, así que Discord la muestra como opcional y rechaza los valores fuera del rango antes de que el bot los vea. Reinicia el bot y enviará el comando modificado.

## Siguiente paso { #next }

- [Comandos slash](guide/commands.md) cubre cada tipo de opción, las choices, el autocompletado y los subcomandos.
- [Responder a interacciones](guide/responses.md) explica `respond`, las respuestas diferidas, los mensajes de seguimiento y las respuestas efímeras.
- [Registrar comandos](guide/registration.md) muestra cómo registrar los comandos en un servidor de pruebas mientras trabajas en ellos.
- [Ejecutar un bot](guide/running.md) cubre el token, el logging, el ciclo de vida del bot y cómo ejecutarlo dentro de otro programa.
