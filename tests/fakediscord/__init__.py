import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import TYPE_CHECKING, Any

from fakediscord import payloads
from fakediscord.server import Connection, FakeDiscord, Order, Part, Recorded
from fakediscord.voice import Packet, VoiceSession, VoiceSocket

if TYPE_CHECKING:
    from bostie import Bot

__all__ = [
    "Connection",
    "FakeDiscord",
    "Order",
    "Packet",
    "Part",
    "Recorded",
    "VoiceSession",
    "VoiceSocket",
    "payloads",
    "run",
    "running",
]

type Scenario = Callable[[FakeDiscord], Coroutine[Any, Any, None]]


def run(scenario: Scenario, **options: Any) -> None:
    """Run ``async def scenario(fake)`` against a new FakeDiscord, within 10 s."""

    async def main() -> None:
        async with FakeDiscord(**options) as fake, asyncio.timeout(10):
            await scenario(fake)

    asyncio.run(main())


@contextlib.asynccontextmanager
async def running(bot: "Bot", fake: FakeDiscord) -> AsyncIterator[None]:
    """Start ``bot`` on ``fake``, wait for READY, and close the bot on exit."""
    started = asyncio.create_task(bot.start(fake.token))
    ready = asyncio.create_task(bot.wait_until_ready())
    await asyncio.wait([started, ready], return_when=asyncio.FIRST_COMPLETED)
    ready.cancel()
    if started.done():
        await started
    try:
        yield
    finally:
        await bot.close()
        await started
