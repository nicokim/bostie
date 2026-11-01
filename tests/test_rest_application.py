from fakediscord import FakeDiscord, payloads, run, running

from bostie import (
    ActivityLocationKind,
    ApplicationRoleConnectionMetadata,
    ApplicationRoleConnectionMetadataType,
    Bot,
)


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
            install = payloads.fixture("rest/application_me")["install_params"]
            params = install | {"permissions": int(install["permissions"])}
            assert fake.requests[-1].json == {
                "install_params": params,
                "integration_types_config": {
                    "0": {"oauth2_install_params": params},
                    "1": {
                        "oauth2_install_params": {
                            "scopes": ["applications.commands"],
                            "permissions": 0,
                        }
                    },
                },
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


def test_an_activity_instance_decodes_its_location() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            instance = payloads.fixture("rest/activity_instance")
            fake.fail("GET", "/applications/1/activity-instances/", 200, instance)
            found = await bot.rest.application.fetch_activity_instance(1, "i-1")
            assert found.raw == instance
            assert found.location.kind is ActivityLocationKind.GUILD_CHANNEL
            assert found.users == (205519959982473217,)

    run(scenario)
