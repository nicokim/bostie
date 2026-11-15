import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest

from bostie import Bot, voice
from bostie.errors import SourceError
from bostie.testing import FakeDiscord, run, running, settle

MEDIA = Path(__file__).parents[1] / "crates/bostie-core/src/voice/testdata/media"
MP3 = (MEDIA / "tone.mp3").read_bytes()
SECOND_OF_F32_STEREO = 48_000 * 2 * 4
PCM_BYTES = bytes(range(256)) * 200

type Opening = Callable[[FakeDiscord], voice.Source]


@contextlib.asynccontextmanager
async def started(fake: FakeDiscord) -> AsyncIterator[voice.VoiceManager]:
    bot = Bot(api_url=fake.api_url)
    async with running(bot, fake):
        yield bot.voice


def python_ffmpeg(code: str) -> voice.FFmpeg:
    return voice.FFmpeg(["-c", code], executable=sys.executable)


async def collect(source: voice.Source) -> list[bytes]:
    return [chunk async for chunk in source]


def aiter_of(*items: bytes) -> AsyncIterator[bytes]:
    async def generate() -> AsyncIterator[bytes]:
        for item in items:
            yield item

    return generate()


def test_properties_need_open_first() -> None:
    for source in (
        voice.File("a.ogg"),
        voice.Stream(aiter_of(), voice.Opus()),
    ):
        for name in ("format", "duration", "seekable", "tags"):
            with pytest.raises(RuntimeError, match=r"open\(\) the source first"):
                getattr(source, name)


@pytest.mark.parametrize(
    ("name", "format", "tags"),
    [
        ("tone.mp3", voice.PCM(48_000, 2, "f32le"), voice.Tags(title="Tone")),
        ("tone.opus", voice.Opus(), voice.Tags()),
    ],
)
def test_open_reports_the_format_duration_seekability_and_tags(
    name: str, format: voice.Format, tags: voice.Tags
) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as manager:
            source = voice.File(MEDIA / name)
            await source.open(manager)
            await source.aclose()

        assert (source.format, source.seekable, source.tags) == (format, True, tags)
        assert source.duration == pytest.approx(1.0, abs=0.01)

    run(scenario)


def test_bytes_and_urls_decode_with_their_hint_and_headers() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        url = fake.cdn_file("media/tone.mp3", MP3)
        async with started(fake) as manager:
            in_memory = voice.Bytes(MP3, hint="mp3")
            remote = voice.Url(url, headers={"Authorization": "secret"})
            for source in (in_memory, remote):
                await source.open(manager)
                await source.aclose()

        assert in_memory.format == remote.format == voice.PCM(48_000, 2, "f32le")
        assert [request.headers["authorization"] for request in fake.cdn_requests] == [
            "secret"
        ]

    run(scenario)


def test_an_undecodable_source_raises_source_error_and_cannot_open_again() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as manager:
            source = voice.Bytes(b"not audio")
            with pytest.raises(SourceError) as raised:
                await source.open(manager)
            with pytest.raises(ValueError, match="a Source plays once"):
                await source.open(manager)

        assert raised.value.source == "Bytes(<9 bytes>)"
        assert raised.value.detail.startswith("no decoder")

    run(scenario)


def test_closing_a_source_while_it_opens_ends_the_open() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as manager:
            release = fake.hold("GET", "/media")
            source = voice.Url(f"{fake.api_url}/media/tone.mp3")
            opening = asyncio.create_task(source.open(manager))
            await fake.expect_request("GET", "/media")
            await source.aclose()

            with pytest.raises(ValueError, match="a Source plays once"):
                await opening
            release.set()

    run(scenario)


def test_iteration_reads_every_chunk_and_then_ends() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as manager:
            source = voice.File(MEDIA / "tone.flac")
            await source.open(manager)
            chunks = await collect(source)
            again = await collect(source)
            await source.aclose()

        assert sum(map(len, chunks)) == pytest.approx(SECOND_OF_F32_STEREO, rel=0.01)
        assert again == []

    run(scenario)


def test_open_twice_keeps_one_decoder_and_a_used_source_plays_once() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as manager:
            ended = voice.File(MEDIA / "tone.flac")
            await ended.open(manager)
            await ended.open(manager)
            chunks = await collect(ended)
            closed = voice.File(MEDIA / "tone.flac")
            await closed.aclose()
            stream = voice.Stream(aiter_of(b"x"), voice.Opus())
            await stream.open(manager)
            await collect(stream)

            assert sum(map(len, chunks)) == pytest.approx(
                SECOND_OF_F32_STEREO, rel=0.01
            )
            for source in (ended, closed, stream):
                with pytest.raises(ValueError, match="a Source plays once"):
                    await source.open(manager)

    run(scenario)


@pytest.mark.parametrize(
    "opening",
    [
        lambda fake: voice.File(MEDIA / "tone.mp3"),
        lambda fake: voice.Bytes(MP3, hint="mp3"),
        lambda fake: voice.Url(fake.cdn_file("media/tone.mp3", MP3)),
    ],
    ids=["file", "bytes", "url"],
)
def test_again_opens_a_fresh_source_of_the_same_audio(opening: Opening) -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as manager:
            source = opening(fake)
            await source.open(manager)
            first = await collect(source)
            await source.aclose()
            again = source.again()
            await again.open(manager)
            second = await collect(again)
            await again.aclose()

        assert type(again) is type(source)
        assert second == first

    run(scenario)


def test_seek_clears_what_was_read_ahead_and_continues_after_the_end() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        async with started(fake) as manager:
            source = voice.File(MEDIA / "tone.flac")
            await source.open(manager)
            await anext(source)
            await source.seek(0.5)
            half = await collect(source)
            await source.seek(0.75)
            quarter = await collect(source)
            await source.aclose()

        assert sum(map(len, half)) == pytest.approx(SECOND_OF_F32_STEREO / 2, rel=0.02)
        assert sum(map(len, quarter)) == pytest.approx(
            SECOND_OF_F32_STEREO / 4, rel=0.02
        )

    run(scenario)


def test_seek_on_a_source_that_cannot_seek_raises_source_error() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        url = fake.cdn_file("media/tone.mp3", MP3)
        async with started(fake) as manager:
            for source in (voice.Url(url), voice.Stream(aiter_of(b"x"), voice.Opus())):
                await source.open(manager)
                with pytest.raises(SourceError, match="not seekable"):
                    await source.seek(0.5)
                await source.aclose()

    run(scenario)


def test_url_repr_leaves_out_credentials_and_query() -> None:
    url = voice.Url("https://user:pass@cdn.example.com/a/b.mp3?signature=secret#t=1")
    assert repr(url) == "Url('https://cdn.example.com/a/b.mp3')"


def test_stream_yields_its_items_in_its_format_and_closes_its_generator() -> None:
    async def scenario() -> None:
        closed = asyncio.Event()

        async def chunks() -> AsyncIterator[bytes]:
            try:
                yield b"one"
                yield b"two"
                yield b"three"
            finally:
                closed.set()

        stream = voice.Stream(chunks(), voice.PCM(24_000, 1))
        await stream.open(Bot().voice)
        assert stream.format == voice.PCM(24_000, 1)
        assert (stream.duration, stream.seekable) == (None, False)
        assert await anext(stream) == b"one"
        await stream.aclose()
        assert closed.is_set()
        assert await collect(stream) == []

    asyncio.run(scenario())


def test_ffmpeg_missing_from_path_raises_source_error() -> None:
    async def scenario() -> None:
        source = voice.FFmpeg(["-i", "a.mp3"], executable="bostie-no-such-ffmpeg")
        with pytest.raises(SourceError, match="bostie-no-such-ffmpeg not on PATH"):
            await source.open(Bot().voice)

    asyncio.run(scenario())


def test_ffmpeg_asks_for_48_khz_stereo_s16le_and_yields_100_ms_reads() -> None:
    code = (
        "import sys; "
        "assert sys.argv[1:] == ['-f', 's16le', '-ar', '48000', '-ac', '2', "
        "'-loglevel', 'error', 'pipe:1'], sys.argv; "
        "sys.stdout.buffer.write(bytes(range(256)) * 200)"
    )

    async def scenario() -> None:
        source = python_ffmpeg(code)
        await source.open(Bot().voice)
        assert source.format == voice.PCM(48_000, 2, "s16le")
        chunks = await collect(source)
        await source.aclose()
        assert [len(chunk) for chunk in chunks] == [19_200, 19_200, 12_800]
        assert b"".join(chunks) == PCM_BYTES

    asyncio.run(scenario())


def test_ffmpeg_again_runs_the_same_command_from_the_start() -> None:
    async def scenario() -> None:
        manager = Bot().voice
        source = python_ffmpeg("import sys; sys.stdout.buffer.write(b'pcm' * 4000)")
        await source.open(manager)
        played = await collect(source)
        await source.aclose()
        again = source.again()
        await again.open(manager)
        assert await collect(again) == played
        await again.aclose()

    asyncio.run(scenario())


def test_ffmpeg_failing_raises_its_last_stderr_line() -> None:
    code = (
        "import sys; "
        "sys.stderr.write('starting\\nclip.mp3: Invalid data found\\n\\n'); "
        "sys.exit(1)"
    )

    async def scenario() -> None:
        source = python_ffmpeg(code)
        await source.open(Bot().voice)
        with pytest.raises(SourceError) as raised:
            await collect(source)
        await source.aclose()
        assert raised.value.detail == "clip.mp3: Invalid data found"

    asyncio.run(scenario())


def test_ffmpeg_aclose_kills_the_process() -> None:
    async def scenario() -> None:
        source = python_ffmpeg("import time; time.sleep(60)")
        await source.open(Bot().voice)
        reading = asyncio.create_task(collect(source))
        await settle()
        await source.aclose()
        assert await reading == []
        assert source._process is not None
        assert source._process.returncode is not None

    asyncio.run(scenario())
