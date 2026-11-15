import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any, override

from bostie.testing import FakeDiscord

GUARD = 10.0


class SteppedLoop(asyncio.SelectorEventLoop):
    def __init__(self) -> None:
        super().__init__()
        self._offset = 0.0

    @override
    def time(self) -> float:
        return super().time() + self._offset

    def advance(self, seconds: float) -> None:
        self._offset += seconds


type SteppedScenario = Callable[[FakeDiscord, SteppedLoop], Coroutine[Any, Any, None]]


def run_stepped(scenario: SteppedScenario, **options: Any) -> None:
    async def main() -> None:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        assert isinstance(loop, SteppedLoop)
        assert task is not None
        guard = threading.Timer(GUARD, loop.call_soon_threadsafe, (task.cancel,))
        guard.start()
        try:
            async with FakeDiscord(**options) as fake:
                await scenario(fake, loop)
        finally:
            guard.cancel()

    asyncio.run(main(), loop_factory=SteppedLoop)
