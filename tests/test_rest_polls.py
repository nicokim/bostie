from bostie import Bot, PollLayoutType
from bostie.testing import FakeDiscord, payloads, run, running


def test_ending_a_poll_returns_its_message_with_the_final_votes() -> None:
    async def scenario(fake: FakeDiscord) -> None:
        bot = Bot(api_url=fake.api_url)
        async with running(bot, fake):
            ended = payloads.fixture("rest/poll_message")
            fake.fail("POST", "/channels/1/polls/2/expire", 200, ended)
            poll = (await bot.rest.polls.end(1, 2)).poll
            assert poll is not None
            assert poll.question.text == "Which codec?"
            assert [answer.answer_id for answer in poll.answers] == [1, 2]
            emoji = poll.answers[1].poll_media.emoji
            assert emoji is not None
            assert (emoji.id, emoji.name) == (1345334752051331073, "lyre")
            assert poll.layout_type is PollLayoutType.DEFAULT
            assert poll.results is not None
            assert poll.results.is_finalized
            assert [
                (count.id, count.count) for count in poll.results.answer_counts
            ] == [(1, 3)]

    run(scenario)
