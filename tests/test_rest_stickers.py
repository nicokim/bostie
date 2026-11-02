import pytest

from bostie import Bot, File, UnexpectedResponse
from bostie.testing import FakeDiscord, Part, payloads, run, running


def test_a_sticker_is_created_from_form_fields_and_its_file() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            sticker = payloads.fixture("rest/sticker_pack")["stickers"][0]
            fake.fail("POST", "/guilds/1/stickers", 200, sticker)
            created = await bot.rest.stickers.create(
                1,
                name="Wave",
                tags="wave",
                file=File("wave.png", b"PNG"),
                reason="new",
            )
            sent = fake.requests[-1]
            assert sent.parts == [
                Part("name", None, None, b"Wave"),
                Part("tags", None, None, b"wave"),
                Part("file", "wave.png", "image/png", b"PNG"),
            ]
            assert sent.headers["x-audit-log-reason"] == "new"
            assert created.name == "Wave"

    run(scenario)


def test_sticker_packs_decode_from_the_array_under_their_key() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            pack = payloads.fixture("rest/sticker_pack")
            fake.fail("GET", "/sticker-packs", 200, {"sticker_packs": [pack]})
            (listed,) = await bot.rest.stickers.list_packs()
            assert listed.raw == pack
            assert listed.stickers[0].pack_id == listed.id
            fake.fail("GET", "/sticker-packs", 200, {"packs": [pack]})
            with pytest.raises(UnexpectedResponse, match="sticker_packs"):
                await bot.rest.stickers.list_packs()

    run(scenario)
