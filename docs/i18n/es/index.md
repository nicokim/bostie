---
render_macros: true
source: a3a1b83398a63bd2c49a018f148793d0781c22a0
---

<div class="bostie-hero" markdown>
<div class="bostie-hero__copy" markdown>

# bostie

bostie es una biblioteca de Discord para Python cuyo gateway, cliente HTTP y motor de voz corren en Rust. Tú escribes el bot en Python asíncrono y tipado.

</div>

![Bostie, un Boston Terrier, te da la bienvenida con los brazos abiertos](assets/mascot/bostie-welcoming.webp){ .bostie-hero__mascot width="200" }

</div>

!!! warning "Pre-alfa"
    La API de bostie todavía puede cambiar. Hoy ya funcionan los comandos slash, de usuario y de mensaje, los componentes, los modales, los eventos del gateway, la caché, REST, los webhooks, las interacciones por HTTP, OAuth2, y la reproducción y recepción de voz.

```python
--8<-- "docs/snippets/home/readme_example.py"
```

`/play` se une al canal de voz del usuario que lo ejecutó, transmite la URL y responde con un botón Stop. El custom id del botón lleva el id del servidor, y `@bot.on_component` lo vuelve a convertir en un `int`, así que el botón sigue funcionando después de reiniciar el bot. `bot.run()` lee el token de la variable de entorno `BOSTIE_TOKEN` y envía el comando a Discord cuando cambió.

{% include "callout.md" %}

## Para qué está hecha { #what-it-is-built-for }

### Voz { #voice }

Desde el 1 de marzo de 2026, Discord solo acepta llamadas con cifrado de extremo a extremo ([DAVE](https://docs.discord.com/developers/topics/voice-connections)). El motor de voz de bostie corre en hilos de Rust, fuera del GIL: el reloj de 20 ms, la decodificación, la mezcla, la codificación Opus, el cifrado del transporte y DAVE. Python solo tiene que mantener lleno el búfer de cada sink, 0.2 segundos de audio por defecto. bostie reproduce archivos, URL, streams HLS y tu propio PCM u Opus, mezcla varias fuentes, recibe a cada persona que habla por separado y graba llamadas.

### Bots que sobreviven a los cambios de Discord { #bots-that-survive-discords-changes }

Los campos, valores de enum, tipos de componente y tipos de interacción desconocidos nunca rompen la decodificación. Cada evento, y cada modelo decodificado a partir de un payload completo, conserva el JSON sin procesar, y `bot.http` y los listeners de eventos sin procesar llegan a lo que bostie todavía no modela.

### Tipos que coinciden con la documentación de Discord { #types-that-match-discords-documentation }

Un campo que Discord siempre envía nunca es `None`, y un objeto parcial tiene su propia clase: un miembro resuelto a partir de una opción de comando es un `ResolvedMember`, que no tiene `deaf` ni `mute`.

### Comandos a partir de firmas { #commands-from-signatures }

Las opciones, las choices, los rangos y el autocompletado salen de las anotaciones de tipo. Los nombres, las descripciones, la cantidad de opciones y las choices se comprueban contra los límites de Discord cuando se ejecuta el decorador, no cuando Discord rechaza la sincronización.

### Pruebas sin Discord { #tests-without-discord }

`bostie.testing.FakeDiscord` es un gateway y una API REST locales contra los que puedes ejecutar tu bot real.

## Por dónde seguir { #where-to-go-next }

- [Instala](install.md) bostie y sus requisitos de compilación.
- Escribe un primer bot en el [inicio rápido](quickstart.md).
- Las guías explican una función por página, desde los [comandos slash](guide/commands.md) hasta las [pruebas](guide/testing.md).
- La sección de [voz](voice/index.md) cubre las conexiones, la reproducción, el audio sin procesar y la recepción.
- La referencia enumera cada [opción de `Bot`](reference/bot-options.md), cada [evento](reference/event-list.md) y la [API](reference/api/bot.md).
