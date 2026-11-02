from bostie import (
    BearerClient,
    LobbyMemberFlags,
    PrimaryProfileData,
    ProfileData,
    ProfileField,
    ProfileFieldType,
    ProfileMedia,
    RESTClient,
)
from bostie.testing import FakeDiscord, payloads, run

LOBBY = {
    "id": "5",
    "application_id": "1",
    "metadata": {"topic": "raid"},
    "members": [{"id": "7", "metadata": None, "flags": 1, "additional_name": "Elf"}],
}


def test_a_bot_creates_a_lobby_that_a_player_messages_with_a_bearer() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        author = payloads.user()
        message = {
            "id": "9",
            "type": 0,
            "content": "ready",
            "lobby_id": "5",
            "channel_id": "5",
            "author": author,
            "flags": 0,
        }
        async with RESTClient(fake.token, api_url=fake.api_url) as client:
            fake.fail("POST", "/lobbies", 200, LOBBY)
            lobby = await client.rest.lobbies.create(
                metadata={"topic": "raid"},
                members=[
                    {
                        "id": 7,
                        "flags": LobbyMemberFlags.CAN_LINK_LOBBY,
                        "additional_name": "Elf",
                    }
                ],
            )
        async with BearerClient("player", api_url=fake.api_url) as player:
            fake.fail("POST", "/lobbies/5/messages", 200, message)
            sent = await player.rest.lobbies.send_message(lobby.id, "ready")
        created, posted = fake.requests[-2:]
        assert created.json == {
            "metadata": {"topic": "raid"},
            "members": [{"id": 7, "flags": 1, "additional_name": "Elf"}],
        }
        assert lobby.members[0].flags is LobbyMemberFlags.CAN_LINK_LOBBY
        assert (posted.headers["authorization"], posted.json) == (
            "Bearer player",
            {"content": "ready"},
        )
        assert (sent.lobby_id, sent.author.id) == (5, int(author["id"]))

    run(scenario)


def test_an_identity_profile_comes_back_only_from_its_first_write() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        route = "/applications/1/users/2/identities/player-3/profile"
        data = ProfileData(
            primary=PrimaryProfileData(
                rank_name="Silver", rank_image=ProfileMedia(url="https://x/s.png")
            ),
            dynamic=(ProfileField(type=ProfileFieldType.NUMBER, name="elo", value=12),),
        )
        stored = {"username": "p3", "metadata": None, "data": None}
        async with RESTClient(fake.token, api_url=fake.api_url) as client:
            identities = client.rest.identities
            fake.fail("PATCH", route, 201, stored | {"data": {"primary": {}}})
            first = await identities.update_profile(1, 2, "player-3", data=data)
            fake.fail("PATCH", route, 204, None)
            later = await identities.update_profile(1, 2, "player-3", username="p3")
        assert fake.requests[-2].json == {
            "data": {
                "primary": {
                    "rank_name": "Silver",
                    "rank_image": {"url": "https://x/s.png"},
                },
                "dynamic": [{"type": 2, "name": "elo", "value": 12}],
            }
        }
        assert first is not None
        assert (first.username, first.data) == (
            "p3",
            ProfileData(primary=PrimaryProfileData()),
        )
        assert later is None

    run(scenario)
