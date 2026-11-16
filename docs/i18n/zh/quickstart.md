---
render_macros: true
source: 879f90299466f4c668efe8cb4ef1edb576a11bc2
description: "用 bostie 一步步创建 Discord 应用、把它添加到服务器，并运行你的第一个带斜杠命令和类型化选项的 Python 机器人。"
---

# 快速入门

![Bostie 在终端里敲代码](assets/mascot/bostie-quickstart.webp){ align=right width="180" loading=lazy }

本页会创建一个 Discord 应用，把它添加到服务器，并运行一个带有一条斜杠命令的机器人。这里假设你已[安装](install.md)好 bostie。

{% include "callout.md" %}

## 创建应用 { #create-the-application }

1. 打开 [Developer Portal](https://discord.com/developers/applications)，创建一个应用。
2. 在它的 **Bot** 页面上重置令牌并复制下来。任何拿到令牌的人都能控制这个机器人，所以不要把它写进代码或提交到仓库。
3. 在它的 **Installation** 页面上，为服务器安装添加 `applications.commands` 和 `bot` 两个授权范围，然后打开安装链接，把应用添加到你管理的某个服务器。

## 编写机器人 { #write-the-bot }

把下面的代码保存为 `bot.py`：

```python
--8<-- "docs/snippets/quickstart/hello_bot.py"
```

`@bot.command` 把这个函数变成斜杠命令 `/hello`。命令名就是函数名，描述是文档字符串的第一行。`ctx` 是该命令某一次使用的 [`Context`](guide/responses.md)：包括谁执行了命令、在哪里执行，以及用来回应的方法。

## 运行 { #run-it }

通过 `BOSTIE_TOKEN` 环境变量传入令牌，然后运行这个文件：

```sh
export BOSTIE_TOKEN="your token"
python bot.py
```

`bot.run()` 会把日志级别设为 INFO，建立连接，并将你定义的命令与 Discord 上已注册的命令进行比较。第一次启动会注册 `/hello` 并输出日志 `commands synced (global): added ['hello'], ...`；之后的启动只有在命令有变化时才会发送内容。在你的服务器里输入 `/hello`，机器人就会回应。

按 Ctrl+C，或由进程管理器发送 SIGTERM，都会干净地关闭机器人。

## 添加选项 { #add-an-option }

`ctx` 之后的参数会成为命令的选项，类型由类型提示决定：

```python
--8<-- "docs/snippets/quickstart/roll_bot.py"
```

`sides` 是一个取值 2 到 100、默认值为 6 的整数选项，因此 Discord 会把它显示为可选项，并在机器人收到之前就拒绝超出范围的值。重启机器人，它就会发送修改后的命令。

## 下一步 { #next }

- [斜杠命令](guide/commands.md)介绍了所有选项类型、候选项、自动补全和子命令。
- [回应交互](guide/responses.md)讲解了 `respond`、延迟回应、后续消息和仅自己可见的回应。
- [注册命令](guide/registration.md)展示了开发期间如何只在一个测试服务器中注册命令。
- [运行机器人](guide/running.md)介绍了令牌、日志、机器人的生命周期，以及如何在其他程序中运行它。
