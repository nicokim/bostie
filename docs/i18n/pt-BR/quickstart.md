---
render_macros: true
source: 4004163cb60675c429361e3c0739c96ffdfbc70f
---

# Início rápido

![Bostie digitando código em um terminal](assets/mascot/bostie-quickstart.webp){ align=right width="180" loading=lazy }

Esta página cria um aplicativo no Discord, adiciona-o a um servidor e roda um bot com um comando de barra. Ela supõe que o bostie já está [instalado](install.md).

{% include "callout.md" %}

## Crie o aplicativo { #create-the-application }

1. Abra o [Developer Portal](https://discord.com/developers/applications) e crie um aplicativo.
2. Na página **Bot** dele, redefina o token e copie-o. Quem tem o token controla o bot, então mantenha-o fora do seu código e do seu repositório.
3. Na página **Installation** dele, adicione os escopos `applications.commands` e `bot` à instalação no servidor, depois abra o link de instalação e adicione o app a um servidor que você administra.

## Escreva o bot { #write-the-bot }

Salve isto como `bot.py`:

```python
--8<-- "docs/snippets/quickstart/hello_bot.py"
```

`@bot.command` transforma a função no comando de barra `/hello`. O nome dele é o nome da função e a descrição é a primeira linha da docstring. `ctx` é o [`Context`](guide/responses.md) de um uso do comando: quem o executou, onde, e os métodos que respondem.

## Rode o bot { #run-it }

Passe o token pela variável de ambiente `BOSTIE_TOKEN` e execute o arquivo:

```sh
export BOSTIE_TOKEN="your token"
python bot.py
```

`bot.run()` configura o logging em INFO, conecta e compara os comandos que você definiu com os registrados no Discord. A primeira execução registra `/hello` e escreve no log `commands synced (global): added ['hello'], ...`; as execuções seguintes não enviam nada, a menos que um comando tenha mudado. Digite `/hello` no seu servidor e o bot responde.

Ctrl+C, ou um SIGTERM vindo de um gerenciador de processos, encerra o bot de forma limpa.

## Adicione uma opção { #add-an-option }

Os parâmetros depois de `ctx` viram as opções do comando, com o tipo dado pelas anotações:

```python
--8<-- "docs/snippets/quickstart/roll_bot.py"
```

`sides` é uma opção inteira de 2 a 100 com padrão 6, então o Discord a mostra como opcional e recusa valores fora do intervalo antes que o bot os veja. Reinicie o bot e ele envia o comando alterado.

## Próximos passos { #next }

- [Comandos de barra](guide/commands.md) cobre todos os tipos de opção, escolhas, preenchimento automático e subcomandos.
- [Respondendo a interações](guide/responses.md) explica `respond`, como adiar a resposta, mensagens de acompanhamento e respostas efêmeras.
- [Registrando comandos](guide/registration.md) mostra como registrar comandos em um servidor de teste enquanto você trabalha neles.
- [Rodando um bot](guide/running.md) cobre o token, o logging, o ciclo de vida do bot e como rodá-lo dentro de outro programa.
