import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from voice_fakes import FakeBot, run, settle

from bostie import voice
from bostie.errors import SourceError

PCM_BYTES = bytes(range(256)) * 200


def python_ffmpeg(code: str) -> voice.FFmpeg:
    return voice.FFmpeg(["-c", code], executable=sys.executable)


async def collect(source: voice.Source) -> list[bytes]:
    return [chunk async for chunk in source]


def test_properties_need_open_first() -> None:
    for source in (
        voice.File("a.ogg"),
        voice.Stream(aiter_of(), voice.Opus()),
    ):
        for name in ("format", "duration", "seekable"):
            with pytest.raises(RuntimeError, match=r"open\(\) the source first"):
                getattr(source, name)


def test_open_waits_for_the_probe_and_reports_48_khz_stereo_f32() -> None:
    async def scenario(bot: FakeBot) -> None:
        bot.core.probe = None
        source = voice.File(Path("song.flac"))
        opening = asyncio.ensure_future(source.open(bot.voice))
        await settle()
        assert not opening.done()
        native = bot.core.sources[-1]
        native.probe = ("pcm", 12.5, True)
        native.feed()
        await opening
        assert (source.format, source.duration, source.seekable) == (
            voice.PCM(48_000, 2, "f32le"),
            12.5,
            True,
        )
        assert bot.core.calls[-1][:2] == ("voice_file", ("song.flac",))

    run(scenario)


def test_an_opus_track_plays_as_opus_packets() -> None:
    async def scenario(bot: FakeBot) -> None:
        bot.core.probe = ("opus", None, False)
        source = voice.Url("https://example.com/live.ogg")
        await source.open(bot.voice)
        assert (source.format, source.duration, source.seekable) == (
            voice.Opus(),
            None,
            False,
        )

    run(scenario)


def test_each_native_source_hands_its_arguments_to_the_core() -> None:
    async def scenario(bot: FakeBot) -> None:
        await voice.Bytes(b"OggS", hint="ogg").open(bot.voice)
        await voice.Url(
            "https://example.com/a.mp3", headers={"Authorization": "secret"}
        ).open(bot.voice)
        assert [source.arguments for source in bot.core.sources] == [
            (b"OggS", "ogg"),
            ("https://example.com/a.mp3", [("Authorization", "secret")]),
        ]

    run(scenario)


def test_a_probe_failure_raises_source_error_and_closes_the_handle() -> None:
    async def scenario(bot: FakeBot) -> None:
        bot.core.probe = None
        source = voice.File("clip.mp4")
        opening = asyncio.ensure_future(source.open(bot.voice))
        await settle()
        native = bot.core.sources[-1]
        native.failure = "no decoder for mp4"
        native.feed()
        with pytest.raises(SourceError) as raised:
            await opening
        assert (raised.value.source, raised.value.detail) == (
            "File('clip.mp4')",
            "no decoder for mp4",
        )
        assert native.closed
        assert bot.voice._handles == {}

    run(scenario)


def test_closing_a_source_while_it_opens_ends_the_open() -> None:
    async def scenario(bot: FakeBot) -> None:
        bot.core.probe = None
        source = voice.File("slow.ogg")
        opening = asyncio.ensure_future(source.open(bot.voice))
        await settle()
        await source.aclose()
        with pytest.raises(ValueError, match="a Source plays once"):
            await opening

    run(scenario)


def test_iteration_reads_chunks_waits_for_wakes_and_ends() -> None:
    async def scenario(bot: FakeBot) -> None:
        source = voice.File("a.wav")
        await source.open(bot.voice)
        native = bot.core.sources[-1]
        native.items.append([b"a", b"b"])
        reading = asyncio.ensure_future(collect(source))
        await settle()
        assert not reading.done()
        native.feed([b"c"])
        await settle()
        native.feed(None)
        assert await reading == [b"a", b"b", b"c"]
        assert await collect(source) == []

    run(scenario)


def test_iteration_raises_the_decoder_error() -> None:
    async def scenario(bot: FakeBot) -> None:
        source = voice.Bytes(b"junk")
        await source.open(bot.voice)
        native = bot.core.sources[-1]
        native.failure = "decoder panicked: index out of bounds"
        native.items.extend([[b"a"], None])
        with pytest.raises(SourceError, match="decoder panicked"):
            await collect(source)

    run(scenario)


def test_open_twice_keeps_one_handle_and_a_used_source_plays_once() -> None:
    async def scenario(bot: FakeBot) -> None:
        ended = voice.File("a.wav")
        await ended.open(bot.voice)
        await ended.open(bot.voice)
        assert len(bot.core.sources) == 1
        bot.core.sources[-1].items.append(None)
        assert await collect(ended) == []
        closed = voice.File("b.wav")
        await closed.aclose()
        stream = voice.Stream(aiter_of(b"x"), voice.Opus())
        await stream.open(bot.voice)
        await collect(stream)
        for source in (ended, closed, stream):
            with pytest.raises(ValueError, match="a Source plays once"):
                await source.open(bot.voice)

    run(scenario)


def test_seek_clears_what_was_read_ahead_and_continues_after_the_end() -> None:
    async def scenario(bot: FakeBot) -> None:
        source = voice.File("a.flac")
        await source.open(bot.voice)
        native = bot.core.sources[-1]
        native.items.extend([[b"a", b"b"]])
        assert await anext(source) == b"a"
        await source.seek(30.0)
        native.items.extend([[b"c"], None])
        assert await collect(source) == [b"c"]
        native.items.clear()
        await source.seek(1.5)
        native.items.extend([[b"d"], None])
        assert await collect(source) == [b"d"]
        assert native.seeks == [30.0, 1.5]

    run(scenario)


def test_seek_on_a_source_that_cannot_seek_raises_source_error() -> None:
    async def scenario(bot: FakeBot) -> None:
        bot.core.probe = ("opus", None, False)
        source = voice.Url("https://example.com/radio")
        await source.open(bot.voice)
        with pytest.raises(SourceError, match="not seekable"):
            await source.seek(10.0)
        stream = voice.Stream(aiter_of(b"x"), voice.Opus())
        await stream.open(bot.voice)
        with pytest.raises(SourceError, match="not seekable"):
            await stream.seek(1.0)

    run(scenario)


def test_aclose_closes_the_handle_and_ends_a_waiting_reader() -> None:
    async def scenario(bot: FakeBot) -> None:
        source = voice.File("a.ogg")
        await source.open(bot.voice)
        reading = asyncio.ensure_future(collect(source))
        await settle()
        await source.aclose()
        assert await reading == []
        assert bot.core.sources[-1].closed
        assert bot.voice._handles == {}

    run(scenario)


def test_url_repr_leaves_out_credentials_and_query() -> None:
    url = voice.Url("https://user:pass@cdn.example.com/a/b.mp3?signature=secret#t=1")
    assert repr(url) == "Url('https://cdn.example.com/a/b.mp3')"


def aiter_of(*items: bytes) -> AsyncIterator[bytes]:
    async def generate() -> AsyncIterator[bytes]:
        for item in items:
            yield item

    return generate()


def test_stream_yields_its_items_in_its_format_and_closes_its_generator() -> None:
    async def scenario(bot: FakeBot) -> None:
        closed = asyncio.Event()

        async def chunks() -> AsyncIterator[bytes]:
            try:
                yield b"one"
                yield b"two"
                yield b"three"
            finally:
                closed.set()

        stream = voice.Stream(chunks(), voice.PCM(24_000, 1))
        await stream.open(bot.voice)
        assert stream.format == voice.PCM(24_000, 1)
        assert (stream.duration, stream.seekable) == (None, False)
        assert await anext(stream) == b"one"
        await stream.aclose()
        assert closed.is_set()
        assert await collect(stream) == []

    run(scenario)


def test_ffmpeg_missing_from_path_raises_source_error() -> None:
    async def scenario(bot: FakeBot) -> None:
        source = voice.FFmpeg(["-i", "a.mp3"], executable="bostie-no-such-ffmpeg")
        with pytest.raises(SourceError, match="bostie-no-such-ffmpeg not on PATH"):
            await source.open(bot.voice)

    run(scenario)


def test_ffmpeg_asks_for_48_khz_stereo_s16le_and_yields_100_ms_reads() -> None:
    code = (
        "import sys; "
        "assert sys.argv[1:] == ['-f', 's16le', '-ar', '48000', '-ac', '2', "
        "'-loglevel', 'error', 'pipe:1'], sys.argv; "
        "sys.stdout.buffer.write(bytes(range(256)) * 200)"
    )

    async def scenario(bot: FakeBot) -> None:
        source = python_ffmpeg(code)
        await source.open(bot.voice)
        assert source.format == voice.PCM(48_000, 2, "s16le")
        chunks = await collect(source)
        await source.aclose()
        assert [len(chunk) for chunk in chunks] == [19_200, 19_200, 12_800]
        assert b"".join(chunks) == PCM_BYTES

    run(scenario)


def test_ffmpeg_failing_raises_its_last_stderr_line() -> None:
    code = (
        "import sys; "
        "sys.stderr.write('starting\\nclip.mp3: Invalid data found\\n\\n'); "
        "sys.exit(1)"
    )

    async def scenario(bot: FakeBot) -> None:
        source = python_ffmpeg(code)
        await source.open(bot.voice)
        with pytest.raises(SourceError) as raised:
            await collect(source)
        await source.aclose()
        assert raised.value.detail == "clip.mp3: Invalid data found"

    run(scenario)


def test_ffmpeg_aclose_kills_the_process() -> None:
    async def scenario(bot: FakeBot) -> None:
        source = python_ffmpeg("import time; time.sleep(60)")
        await source.open(bot.voice)
        reading = asyncio.ensure_future(collect(source))
        await settle()
        await source.aclose()
        assert await reading == []
        assert source._process is not None
        assert source._process.returncode is not None

    run(scenario)
