from typing import TYPE_CHECKING

from fakediscord import FakeDiscord, payloads, run, running

from bostie import Bot, ChannelType, ForumTag, OverwriteType, Permissions

if TYPE_CHECKING:
    from bostie.rest.channels import PermissionOverwrite

GUILD = "/guilds/1/channels"


def test_overwrites_go_out_as_permission_strings_and_positions_as_a_list() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        forum = payloads.fixture("rest/forum_channel")
        overwrite: PermissionOverwrite = {
            "id": 2,
            "type": OverwriteType.ROLE,
            "allow": Permissions.VIEW_CHANNEL | Permissions.SEND_MESSAGES,
            "deny": None,
        }
        async with running(bot, fake):
            channels = bot.rest.channels
            fake.fail("POST", GUILD, 200, forum)
            created = await channels.create(
                1,
                name="bug-reports",
                type=ChannelType.GUILD_FORUM,
                permission_overwrites=[overwrite],
                available_tags=[ForumTag(name="voice", emoji_name="🔊")],
            )
            fake.fail("PUT", "/channels/1/permissions/2", 204, None)
            await channels.edit_permissions(
                1, 2, type=OverwriteType.MEMBER, deny=Permissions.ADD_REACTIONS
            )
            fake.fail("PATCH", GUILD, 204, None)
            await channels.edit_positions(1, [{"id": 3, "position": 0}, {"id": 4}])
            create, edit, positions = fake.requests[-3:]
            assert create.json["permission_overwrites"] == [
                {"id": 2, "type": 0, "allow": "3072", "deny": None}
            ]
            assert create.json["available_tags"] == [
                {"name": "voice", "emoji_name": "🔊"}
            ]
            assert edit.json == {"type": 1, "deny": "64"}
            assert positions.json == [{"id": 3, "position": 0}, {"id": 4}]
            assert created.available_tags[1].moderated
            assert created.default_reaction_emoji is not None
            assert created.default_reaction_emoji.emoji_name == "👍"

    run(scenario)


def test_channel_permissions_come_back_as_permission_flags() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            answer = {"permissions": str(1 << 70 | Permissions.VIEW_CHANNEL)}
            fake.fail("GET", "/channels/1/permissions/member/2", 200, answer)
            permissions = await bot.rest.channels.fetch_member_permissions(
                1, 2, implicit=False
            )
            assert permissions == 1 << 70 | Permissions.VIEW_CHANNEL
            assert fake.requests[-1].query == {"implicit": "false"}

    run(scenario)
