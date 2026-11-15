import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

from bostie._aio import Wake
from bostie.errors import StateError

if TYPE_CHECKING:
    from bostie.voice.connection import VoiceCore


class Handle:
    """The Python end of a native sink, receiver or decoder, which the core wakes."""

    def __init__(self, id: int, handles: dict[int, "Handle"]) -> None:
        self.id = id
        self.woken = Wake()
        self.closed = False
        self._handles = handles
        self._markers: dict[int, asyncio.Future[bool]] = {}

    def marker(self, id: int) -> asyncio.Future[bool]:
        """A future the core sets to whether the audio before marker `id` played."""
        future = self._markers[id] = asyncio.get_running_loop().create_future()
        return future

    def marked(self, id: int, *, reached: bool) -> None:
        """Settle the future of marker `id`."""
        future = self._markers.pop(id, None)
        if future is not None and not future.done():
            future.set_result(reached)

    def close(self) -> None:
        """Wake every waiter, settle every marker False and stop taking deliveries."""
        if self.closed:
            return
        self.closed = True
        del self._handles[self.id]
        self.woken.set()
        for future in self._markers.values():
            if not future.done():
                future.set_result(False)
        self._markers.clear()


class CoreLink:
    """The bot's running core, and the handles its wakes and markers go to."""

    def __init__(self, core: Callable[[], "VoiceCore | None"]) -> None:
        self._running = core
        self._handles: dict[int, Handle] = {}

    @property
    def running(self) -> bool:
        """Whether the bot runs, so it has a core."""
        return self._running() is not None

    def core(self) -> "VoiceCore":
        """The running core; StateError while the bot is not running."""
        core = self._running()
        if core is None:
            raise StateError("voice needs a running bot")
        return core

    def open(self, id: int) -> Handle:
        """A handle that receives the core's deliveries for `id` until it closes."""
        handle = self._handles[id] = Handle(id, self._handles)
        return handle

    def wake(self, id: int) -> None:
        """Wake whoever waits on handle `id`."""
        handle = self._handles.get(id)
        if handle is not None:
            handle.woken.set()

    def marked(self, id: int, marker: int, *, reached: bool) -> None:
        """Settle `marker` of handle `id`."""
        handle = self._handles.get(id)
        if handle is not None:
            handle.marked(marker, reached=reached)
