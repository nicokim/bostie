import asyncio
import contextlib
import os
import shutil
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import AsyncIterable, AsyncIterator, Mapping, Sequence
from typing import TYPE_CHECKING, Literal, Protocol, Self
from urllib.parse import urlsplit

from bostie._aio import Wake
from bostie.errors import SourceError
from bostie.voice.formats import PCM, SAMPLE_RATE, Format, Opus

if TYPE_CHECKING:
    from bostie.voice.connection import VoiceCore, VoiceManager

PLAYS_ONCE = "a Source plays once; create a new one"
NATIVE_PCM = PCM(SAMPLE_RATE, 2, "f32le")
FFMPEG_PCM = PCM(SAMPLE_RATE, 2, "s16le")
FFMPEG_READ = 19_200
FFMPEG_OUTPUT = ("-f", "s16le", "-ar", "48000", "-ac", "2", "-loglevel", "error")


class NativeSource(Protocol):
    @property
    def id(self) -> int: ...
    def info(self) -> tuple[Literal["pcm", "opus"], float | None, bool] | None: ...
    def read(self) -> list[bytes] | None: ...
    def error(self) -> str | None: ...
    def seek(self, position: float) -> None: ...
    def close(self) -> None: ...


class Source(ABC):
    """Audio to play, single use: `open()` once, iterate, `aclose()`.

    Items are Opus packets or PCM chunks in `format`.
    """

    _format: Format | None = None
    _duration: float | None = None
    _seekable = False
    _done = False

    @property
    def format(self) -> Format:
        if self._format is None:
            raise RuntimeError("open() the source first")
        return self._format

    @property
    def duration(self) -> float | None:
        """Length in seconds, or None when unknown."""
        self._check_opened()
        return self._duration

    @property
    def seekable(self) -> bool:
        self._check_opened()
        return self._seekable

    @abstractmethod
    async def open(self, manager: "VoiceManager") -> None:
        """Prepare the source; `vc.play()` calls it, or pass `bot.voice` yourself."""

    def __aiter__(self) -> Self:
        return self

    @abstractmethod
    async def __anext__(self) -> bytes: ...

    async def seek(self, position: float) -> None:
        """Continue from `position` seconds."""
        raise SourceError(repr(self), "not seekable")

    async def aclose(self) -> None:
        self._done = True

    def _check_opened(self) -> None:
        if self._format is None:
            raise RuntimeError("open() the source first")

    def _check_unused(self) -> None:
        if self._done:
            raise ValueError(PLAYS_ONCE)


class _DecodedSource(Source):
    def __init__(self) -> None:
        self._native: NativeSource | None = None
        self._manager: VoiceManager | None = None
        self._chunks: deque[bytes] = deque()
        self._woken = Wake()
        self._closed = False

    @abstractmethod
    def _create(self, core: "VoiceCore") -> NativeSource: ...

    async def open(self, manager: "VoiceManager") -> None:
        self._check_unused()
        native = self._native
        if native is None:
            native = self._native = self._create(manager._core())
            self._manager = manager
            manager._handles[native.id] = self
        while (info := native.info()) is None:
            if (error := native.error()) is not None:
                await self.aclose()
                raise SourceError(repr(self), error)
            await self._woken.wait()
            self._check_unused()
        kind, self._duration, self._seekable = info
        self._format = Opus() if kind == "opus" else NATIVE_PCM

    async def __anext__(self) -> bytes:
        native = self._opened()
        while not self._chunks:
            if self._done:
                raise StopAsyncIteration
            chunks = native.read()
            if chunks is None:
                self._done = True
                error = native.error()
                if error is not None:
                    raise SourceError(repr(self), error)
                raise StopAsyncIteration
            if chunks:
                self._chunks.extend(chunks)
            else:
                await self._woken.wait()
        return self._chunks.popleft()

    async def seek(self, position: float) -> None:
        native = self._opened()
        if self._closed:
            raise RuntimeError("the source is closed")
        if not self._seekable:
            raise SourceError(repr(self), "not seekable")
        native.seek(position)
        self._chunks.clear()
        self._done = False

    async def aclose(self) -> None:
        self._done = self._closed = True
        self._chunks.clear()
        if self._native is not None:
            self._native.close()
        if self._manager is not None and self._native is not None:
            self._manager._handles.pop(self._native.id, None)
        self._wake()

    def _opened(self) -> NativeSource:
        if self._native is None or self._format is None:
            raise RuntimeError("open() the source first")
        return self._native

    def _wake(self) -> None:
        self._woken.set()


class File(_DecodedSource):
    """A local audio file, decoded in Rust."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        super().__init__()
        self._path = os.fspath(path)

    def _create(self, core: "VoiceCore") -> NativeSource:
        return core.voice_file(self._path)

    def __repr__(self) -> str:
        return f"File({self._path!r})"


class Bytes(_DecodedSource):
    """Audio file contents in memory; `hint` is a file extension such as "ogg"."""

    def __init__(self, data: bytes, *, hint: str | None = None) -> None:
        super().__init__()
        self._data = data
        self._hint = hint

    def _create(self, core: "VoiceCore") -> NativeSource:
        return core.voice_bytes(self._data, self._hint)

    def __repr__(self) -> str:
        return f"Bytes(<{len(self._data)} bytes>)"


class Url(_DecodedSource):
    """A direct HTTP(S) media URL, streamed and decoded in Rust."""

    def __init__(self, url: str, *, headers: Mapping[str, str] | None = None) -> None:
        super().__init__()
        self._url = url
        self._headers = dict(headers or {})

    def _create(self, core: "VoiceCore") -> NativeSource:
        return core.voice_url(self._url, list(self._headers.items()))

    def __repr__(self) -> str:
        parts = urlsplit(self._url)
        return f"Url('{parts.scheme}://{parts.hostname}{parts.path}')"


class Stream(Source):
    """Chunks of any async iterable, already in `format`; not seekable."""

    def __init__(self, items: AsyncIterable[bytes], format: Format) -> None:
        self._items = items
        self._items_format = format
        self._iterator: AsyncIterator[bytes] | None = None

    async def open(self, manager: "VoiceManager") -> None:
        self._check_unused()
        if self._iterator is None:
            self._iterator = aiter(self._items)
            self._format = self._items_format

    async def __anext__(self) -> bytes:
        if self._iterator is None:
            raise RuntimeError("open() the source first")
        if self._done:
            raise StopAsyncIteration
        try:
            return await anext(self._iterator)
        except StopAsyncIteration:
            self._done = True
            raise

    async def aclose(self) -> None:
        self._done = True
        close = getattr(self._iterator, "aclose", None)
        if close is not None:
            await close()

    def __repr__(self) -> str:
        return f"Stream({self._items!r})"


class FFmpeg(Source):
    """The output of an ffmpeg command, read as 48 kHz stereo s16le; not seekable."""

    def __init__(self, args: Sequence[str], *, executable: str = "ffmpeg") -> None:
        self._args = tuple(args)
        self._executable = executable
        self._process: asyncio.subprocess.Process | None = None
        self._errors: asyncio.Task[str] | None = None

    async def open(self, manager: "VoiceManager") -> None:
        self._check_unused()
        if self._process is not None:
            return
        path = shutil.which(self._executable)
        if path is None:
            raise SourceError(repr(self), f"{self._executable} not on PATH")
        process = self._process = await asyncio.create_subprocess_exec(
            path,
            *self._args,
            *FFMPEG_OUTPUT,
            "pipe:1",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process.stderr is not None:
            self._errors = asyncio.ensure_future(_last_line(process.stderr))
        self._format = FFMPEG_PCM

    async def __anext__(self) -> bytes:
        process = self._process
        if process is None or process.stdout is None:
            raise RuntimeError("open() the source first")
        if self._done:
            raise StopAsyncIteration
        try:
            return await process.stdout.readexactly(FFMPEG_READ)
        except asyncio.IncompleteReadError as end:
            if end.partial:
                return end.partial
        if self._done:
            raise StopAsyncIteration
        self._done = True
        code = await process.wait()
        if code != 0:
            line = "" if self._errors is None else await self._errors
            raise SourceError(repr(self), line or f"exited with code {code}")
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self._done = True
        process = self._process
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        if self._errors is not None:
            self._errors.cancel()

    def __repr__(self) -> str:
        return f"FFmpeg({self._executable!r})"


async def _last_line(stream: asyncio.StreamReader) -> str:
    last = ""
    async for line in stream:
        if text := line.decode(errors="replace").strip():
            last = text
    return last
