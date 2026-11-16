---
source: 0d5db0165c75f4671bf7c09cfef1394b9bfa9188
description: "Teste um bot do Discord sem o Discord: rode-o com gateway, API REST e servidor de voz falsos e locais, envie interações e eventos e confira o que ele enviou."
---

# Testando o seu bot

![Bostie procurando com uma lupa até aparecer um sinal de visto](../assets/mascot/bostie-detective.webp){ align=right width="180" loading=lazy }

`bostie.testing` roda um Discord falso no localhost: um gateway, uma API REST com as rotas que um bot mais usa e, opcionalmente, um servidor de voz. O seu bot de verdade se conecta a ele, e o teste conduz o lado do Discord: envia eventos e interações e depois confere o que o bot mandou de volta. Ele precisa do extra `testing` ([Instalação](../install.md)), e a própria suíte de testes do bostie roda nele.

```python
--8<-- "docs/snippets/testing/test_greeter.py"
```

`run(scenario)` inicia um `FakeDiscord`, roda `async def scenario(fake)` contra ele em até 10 segundos e o encerra; é uma função comum, então o pytest não precisa de plugin. `async with running(bot, fake):` inicia o bot no Discord falso, espera até ele ficar pronto e o fecha no final. Aponte o bot para o Discord falso com `Bot(api_url=fake.api_url)`, ou defina `BOSTIE_API_URL` quando o bot é montado em outro lugar.

## Conduzindo o lado do Discord { #driving-discords-side }

| Chamada | Faz |
|---|---|
| `await fake.interaction(type, data)` | envia um `INTERACTION_CREATE` e retorna a primeira resposta do bot como uma requisição `Recorded` |
| `await fake.dispatch(name, d, shard=0)` | envia qualquer evento do gateway e retorna o número de sequência dele |
| `fake.expect_callback(payload)` | para uma interação que você mesmo envia: um future para a primeira resposta dela |
| `await fake.send_op(op, d)` | envia um opcode que não é dispatch, como 7 (reconectar) ou 9 (sessão inválida) |
| `await fake.close_gateway(code)`, `fake.drop_gateway()` | fecha a conexão com um código de fechamento, ou a derruba sem um |
| `fake.hold_acks()` | para de responder aos heartbeats, como uma conexão morta faria |
| `await fake.next_identify()`, `await fake.next_resume()` | espera o próximo IDENTIFY ou RESUME do bot |

Os tipos de interação são os números do Discord: 2 para um comando, 3 para um componente, 4 para preenchimento automático, 5 para o envio de um modal.

Depois de um fechamento que inicia uma nova sessão, como o 4007, o bot só faz identify de novo após `HTTPSettings.identify_spacing` segundos, 5 por padrão, como o Discord exige. `Bot(http_settings=HTTPSettings(identify_spacing=0))` pula essa espera em um teste.

`bostie.testing.payloads` monta os payloads do Discord a partir dos exemplos da documentação dele, com IDs novos:

- `command(name, *options)`, `option(name, type, value, focused=False)`, `subcommand(...)`, `group(...)` e `resolved()` para comandos e preenchimento automático;
- `context_menu(type, name, target_id, resolved)` para comandos de usuário e de mensagem;
- `button(custom_id)`, `select(custom_id, *values)` e `modal(custom_id)` para componentes e modais;
- `message(...)`, `guild(...)`, `member(...)`, `user(...)`, `role(...)`, `channel(...)`, `voice_channel(...)`, `stage_channel(...)` e `voice_state(...)` para eventos, cada um com argumentos nomeados que sobrescrevem campos;
- `fixture(name)` para qualquer payload documentado que o bostie traz, e `snowflake()` para um ID novo.

## Conferindo o que o bot enviou { #checking-what-the-bot-sent }

`fake.requests` lista cada requisição REST como um `Recorded`, e `fake.expect_request(method, route_prefix)` retorna um future para a próxima que corresponder, para uma requisição que o bot faz por conta própria, como a partir de uma tarefa. Um `Recorded` tem `method`, `path` (relativo à base da API), `query`, `headers`, `json` e, para uploads, `parts`. Um `Recorded` que responde a uma interação tem o corpo do callback em `json`, como acima. `fake.gateway_received` é uma fila dos payloads que o bot enviou pelo gateway, e `fake.connections` as conexões do gateway, objetos `Connection` com os seus payloads `received`, `shard` e `close_code`.

`fake.cdn_file(path, data)` serve `data` em uma URL da CDN que ele retorna, para um anexo ou asset que o bot baixa, e `fake.cdn_requests` registra esses downloads.

O Discord falso guarda o estado de que essas rotas precisam, e os testes podem lê-lo ou populá-lo: `fake.guilds`, `fake.channels`, `fake.users`, `fake.members`, `fake.messages`, `fake.commands` e `fake.guild_commands`, com `fake.put_count` para as sincronizações de comandos. `fake.ready_guilds` define os IDs de servidor que o `READY` lista.

## Falhas e tempo { #failures-and-timing }

- `fake.fail(method, route_prefix, status, json)` responde à próxima requisição correspondente com um erro, e simula uma rota que o Discord falso não serve.
- `fake.rate_limit(method, route_prefix, scope="user", retry_after=0.1)` responde a ela com um 429.
- `fake.answer(method, route_prefix, body, content_type)` responde a ela com um corpo que não é JSON.
- `fake.hold(method, route_prefix)` a deixa esperando até você definir o `asyncio.Event` retornado; `fake.hold_identify(shard)` e `fake.hold_hello()` fazem o mesmo para o handshake do gateway.
- `await settle()` cede ao event loop algumas vezes, para que os callbacks agendados pelo que você acabou de fazer rodem.

Para esperar algo que o bot faz em um listener, faça-o [enviar um evento próprio](events.md#events-of-your-own) e espere por ele, como o segundo teste faz, em vez de usar sleep.

`FakeDiscord(compress=True)` comprime o gateway com zstd, o que precisa do `compression.zstd` do Python 3.14; `heartbeat_interval` define o intervalo que ele pede, os 41 250 ms do Discord por padrão.

## Voz { #voice }

`FakeDiscord(voice=True)` adiciona um canal de voz e um canal de palco, responde às atualizações de estado de voz e roda um gateway de voz e um servidor UDP como `fake.voice`, para que `ctx.voice.connect()` e `vc.play(...)` funcionem contra ele. Ele não fala DAVE; os testes em Rust do bostie cobrem isso. O teste pode:

- esperar o que o bot enviou com `await fake.next_voice_state_update()`, `await fake.voice.next_identify()` e `await fake.voice.wait_for_packets(count)`, que retorna os pacotes RTP descriptografados como objetos `Packet`; cada conexão de voz é uma `VoiceSession` em `fake.voice.sessions`, sobre um `VoiceSocket`, e `fake.voice.op(op, d)` envia à mais recente qualquer opcode de voz;
- agir como outros usuários com `fake.voice.clients_connect(user_ids)`, `fake.voice.client_disconnect(user_id)`, `fake.voice.speaking(user_id, ssrc, flags)`, `fake.voice.send_audio(...)`, que envia pacotes Opus ao bot, `fake.voice.send_rtcp(packet)`, que envia a ele um pacote RTCP, e `fake.voice.send_datagram(data)`, que envia bytes sem criptografia, como um servidor quebrado faria;
- mover ou desconectar o bot como um moderador faria, com `fake.move_bot(channel_id)` e `fake.kick_bot()`;
- deixar sem resposta a próxima atualização de estado de voz, como o Discord faz quando o canal está cheio, com `fake.hold_voice_state()`, até o `asyncio.Event` retornado ser definido;
- definir `fake.mute`, `fake.deaf` e `fake.suppress` para os estados de voz que ele envia sobre o bot, e `voice_order` para a ordem dos dois eventos que o Discord envia ao entrar.
