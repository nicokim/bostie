import json

from bostie import (
    ApplicationCommandOption,
    ApplicationCommandOptionChoice,
    Bot,
    CommandType,
    OptionType,
    Permissions,
)
from bostie.testing import FakeDiscord, payloads, run, running


def test_commands_go_out_with_option_builders_and_come_back_decoded() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            option = ApplicationCommandOption(
                type=OptionType.STRING,
                name="text",
                description="What to say",
                required=True,
                choices=(ApplicationCommandOptionChoice(name="Hi", value="hi"),),
            )
            commands = bot.rest.commands
            put = await commands.bulk_overwrite(
                fake.application_id,
                [
                    {
                        "name": "echo",
                        "description": "Echo",
                        "options": [option],
                        "default_member_permissions": Permissions.MANAGE_GUILD,
                    }
                ],
            )
            assert fake.requests[-1].json == [
                {
                    "name": "echo",
                    "description": "Echo",
                    "options": [
                        {
                            "type": 3,
                            "name": "text",
                            "description": "What to say",
                            "required": True,
                            "choices": [{"name": "Hi", "value": "hi"}],
                        }
                    ],
                    "default_member_permissions": "32",
                }
            ]
            (listed,) = await commands.list(
                fake.application_id, with_localizations=True
            )
            assert fake.requests[-1].query == {"with_localizations": "true"}
            assert (listed.id, listed.type, listed.options) == (
                put[0].id,
                CommandType.CHAT_INPUT,
                (option,),
            )
            assert listed.default_member_permissions == Permissions.MANAGE_GUILD

            remote = json.loads((payloads.FIXTURES / "rest/commands.json").read_text())
            route = f"/applications/{fake.application_id}/commands"
            fake.fail("GET", route, 200, remote)
            listed_remote = await commands.list(fake.application_id)
            assert [command.raw for command in listed_remote] == remote

    run(scenario)
