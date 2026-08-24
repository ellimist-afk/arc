import pytest

from bot.bot import is_bot_addressed, is_spoken_bot_addressed


@pytest.mark.parametrize("text", [
    "@elimist_ roast the streamer",
    "elimist_ roast the streamer",
    "hey elimist_ are you awake",
    "what was that, elimist_?",
    "hey bot, wake up",
    "hey talkbot roast that whiff",
])
def test_direct_addresses_are_detected(text):
    assert is_bot_addressed(text, "elimist_", "elimist_")


@pytest.mark.parametrize("text", [
    "that character arc was good",
    "the projectile made an arc across the screen",
    "Arc, what was that play?",
    "hey arc are you awake",
    "hello chat",
])
def test_non_bot_names_are_not_a_direct_address(text):
    assert not is_bot_addressed(text, "elimist_", "elimist_")


@pytest.mark.parametrize("text", [
    "elimist that is not an answer",
    "that is not an answer elimist",
    "that is not an answer elements",
])
def test_spoken_account_name_addresses_are_detected(text):
    assert is_spoken_bot_addressed(text, "elimist_", "elimist_")


def test_elements_is_not_a_wake_name_in_ordinary_context():
    assert not is_spoken_bot_addressed(
        "the game elements are confusing today", "elimist_", "elimist_"
    )
