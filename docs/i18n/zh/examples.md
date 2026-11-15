---
source: afe273b0bb674620ed6eaaab978ec64bbcb23c8e
---

# 示例

以下是仓库 `examples/` 文件夹中的文件。测试套件会对它们做类型检查并逐个加载，加载时会构建其中的机器人并检查每个命令定义。设置好 `BOSTIE_TOKEN` 后即可运行每个示例：`python examples/commands.py`。

## 命令 { #commands }

带类型的选项、一个命令组、自动补全、用户命令和消息命令、检查、一个错误钩子，以及来自 gettext 目录的翻译。

```python
--8<-- "examples/commands.py"
```

## 组件 { #components }

一次 Components V2 之旅：包含一个分页器、一个能给容器换颜色的选择菜单，以及一个等待点击确认后才执行的踢出操作。

```python
--8<-- "examples/components.py"
```

## 模态框 { #modals }

一个由按钮打开、在提交时读取的模态框，以及一个由命令等待其提交的模态框。

```python
--8<-- "examples/modals.py"
```

## 事件 { #events }

通过类型注解注册的监听器、一个联合类型、一个机器人自己的事件、一个 `ListenerError` 监听器，以及一个基于 `wait_for` 的猜数游戏。

```python
--8<-- "examples/events.py"
```

## 生命周期 { #lifecycle }

在 `Starting` 监听器中打开、在 `Stopped` 监听器中关闭的数据库，以及两个后台任务。

```python
--8<-- "examples/lifecycle.py"
```

## 音乐 { #music }

每个服务器一个队列，支持跳过、循环和离开。

```python
--8<-- "examples/music.py"
```

## 录音 { #recording }

每位说话者一个 Ogg Opus 文件，直到执行 `/stop`。

```python
--8<-- "examples/recording.py"
```

## Activity { #activity }

一个入口点命令和一个斜杠命令，二者都会打开应用的 Activity。

```python
--8<-- "examples/activity.py"
```

## HTTP 交互 { #http-interactions }

由 `ASGIApp` 通过 HTTP 提供服务的斜杠命令。

```python
--8<-- "examples/http_interactions.py"
```

## REST 脚本 { #rest-script }

不使用网关调用 REST API。

```python
--8<-- "examples/rest_script.py"
```

## Web 应用 { #web-app }

在 Web 应用的 lifespan 中启动的机器人，并带有一个通过机器人的 REST 客户端发消息的路由。

```python
--8<-- "examples/web_app.py"
```

## Webhook { #webhook }

通过 Webhook URL 发消息，分别演示异步代码和普通同步代码的写法。

```python
--8<-- "examples/webhook.py"
```

## 关联身份组 { #linked-roles }

一个关联身份组验证页面：OAuth2 登录、授权码交换，以及用户的身份组连接（role connection）。

```python
--8<-- "examples/linked_roles.py"
```

## 模块化机器人 { #modular-bot }

一个按名称引入模块、并能通过命令重新加载该模块的机器人。

```python
--8<-- "examples/modular/bot.py"
```

```python
--8<-- "examples/modular/greetings.py"
```
