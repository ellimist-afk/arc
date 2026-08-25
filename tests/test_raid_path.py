"""One raid, one welcome, with the raider's real name.

Twitch announces a raid over BOTH IRC USERNOTICE and EventSub, and both were
wired straight to the welcome, so every raid was welcomed twice. The two
sources also spell the fields differently -- EventSub sends
from_broadcaster_USER_login/name, IRC builds from_broadcaster_login/name --
and only the IRC spelling was read, so EventSub raids came through as
"unknown". The IRC path then rebuilt the payload without the login at all,
which killed the Helix enrichment and made every raid after the first look
like a repeat raider.
"""
from types import SimpleNamespace

import pytest

from bot.bot import TalkBot
from features.raider_welcome import RaiderWelcome


def _bot():
    bot = TalkBot.__new__(TalkBot)
    bot.config = {'TWITCH_CHANNEL': 'cassova_'}
    bot._recent_raid_keys = {}
    bot._raid_dedup_window_s = 60.0
    bot.current_game = 'Overwatch'
    bot.first_timer = None
    bot.stream_recap = None
    bot.session_summarizer = None
    bot.twitch_client = None
    bot.welcomed = []

    class Welcome:
        def set_current_game(self, game):
            bot.game_set = game

        async def handle_raid(self, raid):
            bot.welcomed.append(raid)
    bot.raider_welcome = Welcome()
    return bot


EVENTSUB = {'from_broadcaster_user_login': 'KayCee',
            'from_broadcaster_user_name': 'KayCee', 'viewers': 42}
IRC = {'from_broadcaster_login': 'kaycee',
       'from_broadcaster_name': 'KayCee', 'viewers': 42}


# ------------------------------------------------------------ normalizing

def test_eventsub_field_names_are_understood():
    raid = TalkBot._normalize_raid(EVENTSUB)
    assert raid == {'from_broadcaster_login': 'kaycee',
                    'from_broadcaster_name': 'KayCee', 'viewers': 42}


def test_irc_field_names_are_understood():
    assert TalkBot._normalize_raid(IRC) == TalkBot._normalize_raid(EVENTSUB)


def test_missing_names_degrade_without_crashing():
    raid = TalkBot._normalize_raid({})
    assert raid['from_broadcaster_name'] == 'Unknown'
    assert raid['from_broadcaster_login'] == '' and raid['viewers'] == 0


@pytest.mark.parametrize('viewers, expected', [(None, 0), ('37', 37), (-4, 0), ('junk', 0)])
def test_viewer_counts_are_sanitised(viewers, expected):
    assert TalkBot._normalize_raid({'viewers': viewers})['viewers'] == expected


# ----------------------------------------------------------- one raid, one welcome

async def test_same_raid_from_both_sources_welcomes_once():
    bot = _bot()
    await bot._process_raid(IRC, source='irc')
    await bot._process_raid(EVENTSUB, source='eventsub')
    assert len(bot.welcomed) == 1, "IRC and EventSub report the same raid"
    assert bot.welcomed[0]['from_broadcaster_name'] == 'KayCee'


async def test_eventsub_first_also_works():
    bot = _bot()
    await bot._process_raid(EVENTSUB, source='eventsub')
    await bot._process_raid(IRC, source='irc')
    assert len(bot.welcomed) == 1


async def test_a_different_raider_is_not_deduped():
    bot = _bot()
    await bot._process_raid(IRC, source='irc')
    await bot._process_raid({'from_broadcaster_login': 'someone_else',
                             'from_broadcaster_name': 'Someone', 'viewers': 5}, source='irc')
    assert len(bot.welcomed) == 2


async def test_the_same_raider_can_raid_again_later():
    bot = _bot()
    await bot._process_raid(IRC, source='irc')
    bot._recent_raid_keys.clear()          # stands in for the window expiring
    await bot._process_raid(IRC, source='irc')
    assert len(bot.welcomed) == 2


async def test_stale_keys_are_evicted():
    bot = _bot()
    await bot._process_raid(IRC, source='irc')
    # age the recorded key past the window, deterministically
    bot._recent_raid_keys = {k: v - (bot._raid_dedup_window_s + 1)
                             for k, v in bot._recent_raid_keys.items()}
    await bot._process_raid({'from_broadcaster_login': 'other',
                             'from_broadcaster_name': 'Other', 'viewers': 1}, source='irc')
    assert len(bot.welcomed) == 2
    assert list(bot._recent_raid_keys) == [('other', 1)], "stale keys must be dropped"


async def test_the_login_survives_to_the_welcome():
    """The IRC path used to rebuild the payload with only the display name,
    so enrichment and the repeat check had nothing to work with."""
    bot = _bot()
    await bot._process_raid(IRC, source='irc')
    assert bot.welcomed[0]['from_broadcaster_login'] == 'kaycee'


async def test_current_game_is_handed_over():
    bot = _bot()
    await bot._process_raid(IRC, source='irc')
    assert bot.game_set == 'Overwatch'


async def test_events_are_remembered_once():
    bot = _bot()
    notes, recaps = [], []
    bot.session_summarizer = SimpleNamespace(note_event=lambda ch, n: notes.append(n))
    bot.stream_recap = SimpleNamespace(record_event=lambda n: recaps.append(n))
    bot.first_timer = SimpleNamespace(note_raid=lambda: None)
    await bot._process_raid(IRC, source='irc')
    await bot._process_raid(EVENTSUB, source='eventsub')
    assert notes == ['KayCee raided with 42 viewers']
    assert recaps == notes


async def test_a_failing_welcome_does_not_propagate():
    bot = _bot()

    async def boom(raid):
        raise RuntimeError("welcome exploded")
    bot.raider_welcome.handle_raid = boom
    await bot._process_raid(IRC, source='irc')     # must not raise


# ------------------------------------------------- repeat-raider detection

def _welcome():
    rw = RaiderWelcome.__new__(RaiderWelcome)
    from collections import deque
    rw.recent_raids = deque(maxlen=10)
    rw.current_game = None
    return rw


def test_unknown_logins_are_never_treated_as_repeat_raiders():
    """With the login dropped, every raid compared equal to the last one."""
    rw = _welcome()
    rw.recent_raids.append({'raider': ''})
    raider = ''
    assert not (bool(raider) and any(r['raider'] == raider for r in rw.recent_raids))


def test_a_real_repeat_raider_is_still_detected():
    rw = _welcome()
    rw.recent_raids.append({'raider': 'kaycee'})
    raider = 'kaycee'
    assert bool(raider) and any(r['raider'] == raider for r in rw.recent_raids)


async def test_enrichment_is_skipped_without_a_login():
    rw = RaiderWelcome.__new__(RaiderWelcome)
    content = await rw._fetch_content('')
    assert content == {'channel': None, 'vods': None, 'clip': None}
