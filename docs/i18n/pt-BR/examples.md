---
source: afe273b0bb674620ed6eaaab978ec64bbcb23c8e
---

# Exemplos

Estes são os arquivos da pasta `examples/` do repositório. A suíte de testes verifica os tipos deles e carrega cada um, o que monta o bot e verifica cada definição de comando. Cada um roda com a variável `BOSTIE_TOKEN` definida: `python examples/commands.py`.

## Comandos { #commands }

Opções tipadas, um grupo, preenchimento automático, comandos de usuário e de mensagem, verificações, um hook de erro e traduções a partir de catálogos do gettext.

```python
--8<-- "examples/commands.py"
```

## Componentes { #components }

Um tour pelos Components V2 com um paginador, um menu de seleção que muda a cor de um contêiner e uma expulsão que espera um clique de confirmação.

```python
--8<-- "examples/components.py"
```

## Modais { #modals }

Um modal aberto a partir de um botão e lido no envio, e outro pelo qual um comando espera.

```python
--8<-- "examples/modals.py"
```

## Eventos { #events }

Listeners por anotação, uma união, um evento do próprio bot, um listener de `ListenerError` e um jogo de adivinhação construído com `wait_for`.

```python
--8<-- "examples/events.py"
```

## Ciclo de vida { #lifecycle }

Um banco de dados aberto em um listener de `Starting` e fechado em um de `Stopped`, e duas tarefas em segundo plano.

```python
--8<-- "examples/lifecycle.py"
```

## Música { #music }

Uma fila por servidor com pular, repetir e sair.

```python
--8<-- "examples/music.py"
```

## Gravação { #recording }

Um arquivo Ogg Opus por falante até `/stop`.

```python
--8<-- "examples/recording.py"
```

## Atividade { #activity }

Um comando de ponto de entrada e um comando de barra que abrem a Atividade do app.

```python
--8<-- "examples/activity.py"
```

## Interações por HTTP { #http-interactions }

Comandos de barra servidos por HTTP pelo `ASGIApp`.

```python
--8<-- "examples/http_interactions.py"
```

## Script REST { #rest-script }

A API REST sem um gateway.

```python
--8<-- "examples/rest_script.py"
```

## App web { #web-app }

Um bot iniciado dentro do lifespan de um app web, com uma rota que publica pelo cliente REST do bot.

```python
--8<-- "examples/web_app.py"
```

## Webhook { #webhook }

Publicando por uma URL de webhook, a partir de código assíncrono e de código comum.

```python
--8<-- "examples/webhook.py"
```

## Cargos vinculados { #linked-roles }

Uma página de verificação de cargos vinculados: o login OAuth2, a troca do código e a conexão de cargo do usuário.

```python
--8<-- "examples/linked_roles.py"
```

## Bot modular { #modular-bot }

Um bot que inclui um módulo pelo nome e o recarrega a partir de um comando.

```python
--8<-- "examples/modular/bot.py"
```

```python
--8<-- "examples/modular/greetings.py"
```
