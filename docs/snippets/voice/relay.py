from bostie import voice


async def relay_call(
    source: voice.VoiceConnection, target: voice.VoiceConnection
) -> None:
    async with source.receive(voice.PCM(), mix=True) as rx, target.sink() as out:
        async for frame in rx:
            await out.write(frame.data)


async def relay_speaker(
    source: voice.VoiceConnection, target: voice.VoiceConnection, user_id: int
) -> None:
    async with (
        source.receive(voice.Opus(), users=[user_id]) as rx,
        target.sink(voice.Opus()) as out,
    ):
        async for frame in rx:
            if not frame.lost:
                await out.write(frame.data)
