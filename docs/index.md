---
render_macros: true
---

# bostie

![Bostie, a Boston Terrier, welcoming you with open arms](assets/mascot/bostie-welcoming.webp){ align=right width="180" loading=lazy }

bostie is a Discord library for Python whose gateway, HTTP client and voice engine run in Rust. You write the bot in typed, async Python.

!!! warning "Pre-alpha"
    bostie's API can still change. Slash, user and message commands, components, modals, gateway events, the cache, REST, webhooks, HTTP interactions, OAuth2, and voice playback and receiving work today.

```python
--8<-- "docs/snippets/home/readme_example.py"
```

`/play` joins the voice channel of the user who ran it, streams the URL and answers with a Stop button. The button's custom id carries the guild id, and `@bot.on_component` parses it back into an `int`, so the button still works after the bot restarts. `bot.run()` reads the token from the `BOSTIE_TOKEN` environment variable and sends the command to Discord when it changed.

{% include "callout.md" %}

## What it is built for

### Voice

Discord has accepted only end-to-end encrypted calls ([DAVE](https://docs.discord.com/developers/topics/voice-connections)) since March 1, 2026. bostie's voice engine runs on Rust threads, outside the GIL: the 20 ms clock, decoding, mixing, Opus encoding, transport encryption and DAVE. Python only has to keep each sink's buffer filled, 0.2 seconds of audio by default. bostie plays files, URLs, HLS streams and your own PCM or Opus, mixes several sources, receives each speaker on their own and records calls.

### Bots that survive Discord's changes

Unknown fields, enum values, component types and interaction types never break decoding. Every event, and every model decoded from a whole payload, keeps the raw JSON, and `bot.http` and raw event listeners reach what bostie does not model yet.

### Types that match Discord's documentation

A field Discord always sends is never `None`, and a partial object has its own class: a member resolved from a command option is a `ResolvedMember`, which has no `deaf` or `mute`.

### Commands from signatures

Options, choices, ranges and autocomplete come from type hints. Names, descriptions, option counts and choices are checked against Discord's limits when the decorator runs, not when Discord rejects the sync.

### Tests without Discord

`bostie.testing.FakeDiscord` is a local gateway and REST API to run your real bot against.

## Where to go next

- [Install](install.md) bostie and its build requirements.
- Write a first bot in the [quickstart](quickstart.md).
- The guides explain one feature per page, from [slash commands](guide/commands.md) to [testing](guide/testing.md).
- The [voice](voice/index.md) section covers connections, playback, raw audio and receiving.
- The reference lists every [`Bot` option](reference/bot-options.md), every [event](reference/event-list.md) and the [API](reference/api/bot.md).
