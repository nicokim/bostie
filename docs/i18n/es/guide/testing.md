---
source: ceb5f752e899b0a321da19a1c2f2e69456a2455a
description: "Prueba un bot de Discord sin Discord: ejecútalo con gateway, API REST y servidor de voz falsos y locales, envía interacciones y eventos y revisa lo enviado."
---

# Probar tu bot

![Bostie busca con una lupa hasta que aparece una marca de verificación](../assets/mascot/bostie-detective.webp){ align=right width="180" loading=lazy }

`bostie.testing` ejecuta un Discord falso en localhost: un gateway, una API REST con las rutas que más usa un bot y, de forma opcional, un servidor de voz. Tu bot real se conecta a él, y la prueba maneja el lado de Discord: envía eventos e interacciones y luego comprueba lo que el bot envió de vuelta. Necesita el extra `testing` ([Instalación](../install.md)), y la propia suite de pruebas de bostie corre sobre él.

```python
--8<-- "docs/snippets/testing/test_greeter.py"
```

`run(scenario)` inicia un `FakeDiscord`, ejecuta `async def scenario(fake)` contra él en un máximo de 10 segundos y lo apaga; es una función común, así que pytest no necesita ningún plugin. `async with running(bot, fake):` inicia el bot sobre el Discord falso, espera a que esté listo y lo cierra al final. Apunta el bot al Discord falso con `Bot(api_url=fake.api_url)`, o define `BOSTIE_API_URL` cuando el bot se construye en otro lugar.

## Manejar el lado de Discord { #driving-discords-side }

| Llamada | Qué hace |
|---|---|
| `await fake.interaction(type, data)` | envía un `INTERACTION_CREATE` y devuelve la primera respuesta del bot como una solicitud `Recorded`, o lanza `TimeoutError` si no llega ninguna en los 3 segundos que da Discord |
| `await fake.dispatch(name, d, shard=0)` | envía cualquier evento del gateway y devuelve su número de secuencia |
| `fake.expect_callback(payload)` | para una interacción que despachas tú mismo: un future para su primera respuesta |
| `await fake.send_op(op, d)` | envía un opcode que no es dispatch, como 7 (reconectar) o 9 (sesión inválida) |
| `await fake.close_gateway(code)`, `fake.drop_gateway()` | cierra la conexión con un código de cierre, o la corta sin ninguno |
| `fake.hold_acks()` | deja de responder a los heartbeats, como haría una conexión muerta |
| `await fake.next_identify()`, `await fake.next_resume()` | espera el siguiente IDENTIFY o RESUME del bot |

Los tipos de interacción son los números de Discord: 2 para un comando, 3 para un componente, 4 para el autocompletado, 5 para el envío de un modal. Un comando registrado solo en el servidor de la interacción recibe el ID de ese servidor en su `data`, como lo envía Discord; si el nombre también está registrado de forma global, `payloads.command(name, guild_id=...)` elige el comando del servidor.

Tras un cierre que inicia una sesión nueva, como el 4007, el bot vuelve a identificarse solo después de `HTTPSettings.identify_spacing` segundos, 5 por defecto, como exige Discord. `Bot(http_settings=HTTPSettings(identify_spacing=0))` se salta esa espera en una prueba.

`bostie.testing.payloads` construye los payloads de Discord a partir de los ejemplos de su documentación, con ids nuevos:

- `command(name, *options, guild_id=None)`, `option(name, type, value, focused=False)`, `subcommand(...)`, `group(...)` y `resolved()` para los comandos y el autocompletado;
- `context_menu(type, name, target_id, resolved)` para los comandos de usuario y de mensaje;
- `button(custom_id)`, `select(custom_id, *values)` y `modal(custom_id)` para los componentes y los modales;
- `message(...)`, `guild(...)`, `member(...)`, `user(...)`, `role(...)`, `channel(...)`, `voice_channel(...)`, `stage_channel(...)` y `voice_state(...)` para los eventos, cada uno con argumentos por palabra clave que reemplazan campos;
- `fixture(name)` para cualquier payload documentado que incluye bostie, y `snowflake()` para un id nuevo.

## Comprobar lo que envió el bot { #checking-what-the-bot-sent }

`fake.requests` enumera cada solicitud REST como un `Recorded`, y `fake.expect_request(method, route_prefix)` devuelve un future para la siguiente que coincida, para una solicitud que el bot hace por su cuenta, por ejemplo desde una tarea. Un `Recorded` tiene `method`, `path` (relativo a la base de la API), `query`, `headers`, `json` y, para las subidas, `parts`. Un `Recorded` que responde a una interacción tiene el cuerpo del callback en `json`, como arriba. `fake.gateway_received` es una cola de los payloads que el bot envió por el gateway, y `fake.connections` son las conexiones al gateway, objetos `Connection` con sus payloads `received`, su `shard` y su `close_code`.

`fake.cdn_file(path, data)` sirve `data` en una URL de la CDN que devuelve, para un archivo adjunto o un recurso que descarga el bot, y `fake.cdn_requests` registra esas descargas.

El Discord falso mantiene el estado que necesitan esas rutas, y las pruebas lo pueden leer o precargar: `fake.guilds`, `fake.channels`, `fake.users`, `fake.members`, `fake.messages`, `fake.commands` y `fake.guild_commands`, con `fake.put_count` para las sincronizaciones de comandos. `fake.ready_guilds` define los ids de servidor que enumera `READY`.

## Fallos y tiempos { #failures-and-timing }

- `fake.fail(method, route_prefix, status, json)` responde con un error a la siguiente solicitud que coincida, y sirve como stub de una ruta que el Discord falso no atiende.
- `fake.rate_limit(method, route_prefix, scope="user", retry_after=0.1)` la responde con un 429.
- `fake.answer(method, route_prefix, body, content_type)` la responde con un cuerpo que no es JSON.
- `fake.hold(method, route_prefix)` la mantiene en espera hasta que actives el `asyncio.Event` que devuelve; `fake.hold_identify(shard)` y `fake.hold_hello()` hacen lo mismo con el handshake del gateway.
- `await settle()` le cede el control al event loop unas cuantas veces, para que se ejecuten los callbacks programados por lo que acabas de hacer.

Para esperar algo que el bot hace en un listener, haz que [despache un evento propio](events.md#events-of-your-own) y espera ese evento, como hace la segunda prueba, en lugar de dormir.

`FakeDiscord(compress=True)` comprime el gateway con zstd, lo que necesita el `compression.zstd` de Python 3.14; `heartbeat_interval` define el intervalo que pide, los 41 250 ms de Discord por defecto.

## Voz { #voice }

`FakeDiscord(voice=True)` agrega un canal de voz y un canal escenario, responde a las actualizaciones de estado de voz y ejecuta un gateway de voz y un servidor UDP como `fake.voice`, así que `ctx.voice.connect()` y `vc.play(...)` funcionan contra él. No habla DAVE; eso lo cubren las pruebas de Rust de bostie. La prueba puede:

- esperar lo que envió el bot con `await fake.next_voice_state_update()`, `await fake.voice.next_identify()` y `await fake.voice.wait_for_packets(count)`, que devuelve los paquetes RTP descifrados como objetos `Packet`; cada conexión de voz es una `VoiceSession` en `fake.voice.sessions`, sobre un `VoiceSocket`, y `fake.voice.op(op, d)` le envía a la más reciente cualquier opcode de voz;
- poner a un usuario en un canal de voz, moverlo o sacarlo con `None`, con `await fake.move_user(guild_id, user_id, channel_id)`: envía el `VOICE_STATE_UPDATE` que enviaría Discord una vez que el bot está conectado, y antes de eso solo guarda el estado, así `ctx.voice.connect()` encuentra el canal del usuario en ambos casos;
- actuar como otros usuarios con `fake.voice.clients_connect(user_ids)`, `fake.voice.client_disconnect(user_id)`, `fake.voice.speaking(user_id, ssrc, flags)`, `fake.voice.send_audio(...)`, que le envía paquetes Opus al bot, `fake.voice.send_rtcp(packet)`, que le envía un paquete RTCP, y `fake.voice.send_datagram(data)`, que envía bytes sin sellar, como haría un servidor roto;
- mover o desconectar al bot como lo haría un moderador, con `fake.move_bot(channel_id)` y `fake.kick_bot()`;
- dejar sin respuesta la siguiente actualización de estado de voz, como hace Discord cuando el canal está lleno, con `fake.hold_voice_state()`, hasta que se active el `asyncio.Event` que devuelve;
- definir `fake.mute`, `fake.deaf` y `fake.suppress` para los estados de voz que envía sobre el bot, y `voice_order` para el orden de los dos eventos que Discord envía al unirse.
