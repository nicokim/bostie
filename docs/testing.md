# Testing

`pip install "path/to/bostie[testing]"` adds `bostie.testing`: a fake Discord gateway, REST API and voice server that runs in your test process. bostie's own tests run on it.

```python
import pytest

from bostie.testing import FakeDiscord, payloads, run, running
from mybot import bot


def test_ping_answers_pong(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        monkeypatch.setenv("BOSTIE_API_URL", fake.api_url)
        async with running(bot, fake):
            reply = await fake.interaction(2, payloads.command("ping"))
            assert reply.json["data"]["content"] == "pong"

    run(scenario)
```

Here `mybot.py` holds `bot = Bot()` and a `/ping` command that answers pong.

- `run(scenario)` runs `async def scenario(fake)` against a new `FakeDiscord` within 10 s. `running(bot, fake)` starts the bot with the fake's token, waits until it is ready, and closes it at the end.
- `BOSTIE_API_URL` is the `api_url` the bot and the clients use when you pass none, so setting it to `fake.api_url` points a bot built at import time at the fake without changing how it is built. A bot made inside the test can take `Bot(api_url=fake.api_url)` instead.
- `fake.dispatch(name, d)` sends a gateway event, and `fake.interaction(type, data)` sends an interaction and returns the bot's answer. `payloads` builds both from Discord's documented examples, and `payloads.fixture("events/MESSAGE_CREATE")` returns any of them.
- `fake.requests` records every REST request. `fake.fail(method, route_prefix, status, json)` answers the next matching request with that error, and `fake.hold(method, route_prefix)` keeps it waiting until you set the event it returns.
- `FakeDiscord(voice=True)` adds a voice gateway and a UDP server that decrypts every RTP packet into `fake.packets`, and `fake.move_bot()` and `fake.kick_bot()` move the bot around. It does not speak DAVE.
