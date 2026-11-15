---
render_macros: true
source: a3a1b83398a63bd2c49a018f148793d0781c22a0
---

<div class="bostie-hero" markdown>
<div class="bostie-hero__copy" markdown>

# bostie

bostie é uma biblioteca de Discord para Python cujo gateway, cliente HTTP e motor de voz rodam em Rust. Você escreve o bot em Python assíncrono e tipado.

</div>

![Bostie, um Boston Terrier, recebendo você de braços abertos](assets/mascot/bostie-welcoming.webp){ .bostie-hero__mascot width="200" }

</div>

!!! warning "Pré-alfa"
    A API do bostie ainda pode mudar. Comandos de barra, de usuário e de mensagem, componentes, modais, eventos do gateway, o cache, REST, webhooks, interações por HTTP, OAuth2 e a reprodução e recepção de voz já funcionam hoje.

```python
--8<-- "docs/snippets/home/readme_example.py"
```

`/play` entra no canal de voz do usuário que o executou, transmite a URL e responde com um botão Stop. O custom id do botão leva o ID do servidor, e `@bot.on_component` o converte de volta em um `int`, então o botão continua funcionando depois que o bot reinicia. `bot.run()` lê o token da variável de ambiente `BOSTIE_TOKEN` e envia o comando ao Discord quando ele mudou.

{% include "callout.md" %}

## Para que ele foi feito { #what-it-is-built-for }

### Voz { #voice }

Desde 1º de março de 2026, o Discord só aceita chamadas com criptografia de ponta a ponta ([DAVE](https://docs.discord.com/developers/topics/voice-connections)). O motor de voz do bostie roda em threads Rust, fora do GIL: o relógio de 20 ms, a decodificação, a mixagem, a codificação Opus, a criptografia de transporte e o DAVE. O Python só precisa manter cheio o buffer de cada sink, 0,2 segundo de áudio por padrão. O bostie reproduz arquivos, URLs, streams HLS e o seu próprio PCM ou Opus, mixa várias fontes, recebe cada falante separadamente e grava chamadas.

### Bots que sobrevivem às mudanças do Discord { #bots-that-survive-discords-changes }

Campos, valores de enum, tipos de componente e tipos de interação desconhecidos nunca quebram a decodificação. Todo evento, e todo modelo decodificado a partir de um payload inteiro, guarda o JSON bruto, e `bot.http` e os listeners de eventos brutos alcançam o que o bostie ainda não modela.

### Tipos que batem com a documentação do Discord { #types-that-match-discords-documentation }

Um campo que o Discord sempre envia nunca é `None`, e um objeto parcial tem a sua própria classe: um membro resolvido a partir de uma opção de comando é um `ResolvedMember`, que não tem `deaf` nem `mute`.

### Comandos a partir de assinaturas { #commands-from-signatures }

Opções, escolhas, intervalos e preenchimento automático vêm das anotações de tipo. Nomes, descrições, quantidade de opções e escolhas são verificados contra os limites do Discord quando o decorador roda, e não quando o Discord rejeita a sincronização.

### Testes sem o Discord { #tests-without-discord }

`bostie.testing.FakeDiscord` é um gateway e uma API REST locais contra os quais você roda o seu bot de verdade.

## Para onde ir agora { #where-to-go-next }

- [Instale](install.md) o bostie e os requisitos para compilá-lo.
- Escreva um primeiro bot no [início rápido](quickstart.md).
- Os guias explicam um recurso por página, dos [comandos de barra](guide/commands.md) aos [testes](guide/testing.md).
- A seção de [voz](voice/index.md) cobre conexões, reprodução, áudio bruto e recepção.
- A referência lista cada [opção do `Bot`](reference/bot-options.md), cada [evento](reference/event-list.md) e a [API](reference/api/bot.md).
