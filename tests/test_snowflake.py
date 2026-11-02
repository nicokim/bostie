from datetime import UTC, datetime

from bostie import snowflake_time, time_snowflake


def test_an_id_gives_its_creation_time_and_a_time_the_ids_around_it() -> None:
    created = snowflake_time(175928847299117063)

    assert created == datetime(2016, 4, 30, 11, 18, 25, 796000, tzinfo=UTC)
    assert time_snowflake(created) == 175928847298985984
    assert time_snowflake(created, high=True) == 175928847303180287
