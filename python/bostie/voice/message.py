import asyncio
import os
from pathlib import Path

from bostie import _native
from bostie.errors import SourceError
from bostie.file import File


async def message(
    audio: str | os.PathLike[str] | bytes, *, name: str | None = None
) -> File:
    """A File that sends `audio` as a voice message, with its duration and waveform.

    Discord clients record 48 kHz mono Ogg Opus; the name's extension says the format.
    """
    if isinstance(audio, bytes):
        data, name = audio, name or "voice-message.ogg"
    else:
        path = Path(audio)
        data, name = await asyncio.to_thread(path.read_bytes), name or path.name
    hint = Path(name).suffix.removeprefix(".") or None
    try:
        duration, waveform = await asyncio.to_thread(_native.voice_message, data, hint)
    except ValueError as error:
        raise SourceError(name, str(error)) from None
    return File(name, data, duration_secs=duration, waveform=waveform)
