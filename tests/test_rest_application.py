from bostie import (
    ApplicationRoleConnectionMetadata,
    ApplicationRoleConnectionMetadataType,
    Bot,
)
from bostie.testing import FakeDiscord, payloads, run, running


def test_install_settings_and_role_connection_metadata_go_back_as_read() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            application = bot.rest.application
            app = await application.fetch()
            assert app.id == fake.application_id
            assert app.install_params is not None
            fake.fail("PATCH", "/applications/@me", 200, app.raw)
            await application.edit(
                install_params=app.install_params,
                integration_types_config=app.integration_types_config,
            )
            read = payloads.fixture("rest/application_me")
            assert fake.requests[-1].json == {
                "install_params": read["install_params"],
                "integration_types_config": read["integration_types_config"],
            }

            record = ApplicationRoleConnectionMetadata(
                type=ApplicationRoleConnectionMetadataType.INTEGER_EQUAL,
                key="level",
                name="Level",
                description="Your level",
            )
            route = f"/applications/{app.id}/role-connections/metadata"
            sent = {
                "type": 3,
                "key": "level",
                "name": "Level",
                "description": "Your level",
            }
            fake.fail("PUT", route, 200, [sent])
            updated = await application.update_role_connection_metadata(
                app.id, [record]
            )
            assert (fake.requests[-1].json, updated) == ([sent], (record,))

    run(scenario)


def test_the_oauth2_route_fetches_the_same_application() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            app = await bot.rest.application.fetch_bot_application()

        assert app.id == fake.application_id
        assert fake.requests[-1].path == "/oauth2/applications/@me"

    run(scenario)
