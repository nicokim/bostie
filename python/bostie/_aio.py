import asyncio
from collections.abc import Coroutine
from typing import Any


def spawn(
    tasks: set[asyncio.Task[None]],
    coroutine: Coroutine[Any, Any, None],
    *,
    eager: bool = False,
) -> None:
    loop = asyncio.get_running_loop()
    task = (
        asyncio.Task(coroutine, loop=loop, eager_start=True)
        if eager
        else loop.create_task(coroutine)
    )
    if not task.done():
        tasks.add(task)
        task.add_done_callback(tasks.discard)


class Wake:
    def __init__(self) -> None:
        self._future: asyncio.Future[None] | None = None

    async def wait(self) -> None:
        if self._future is None:
            self._future = asyncio.get_running_loop().create_future()
        await asyncio.shield(self._future)

    def set(self) -> None:
        future, self._future = self._future, None
        if future is not None and not future.done():
            future.set_result(None)
