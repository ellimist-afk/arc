"""The Sept 12 filler must not turn ordinary viewer chat into a reply chain."""
import asyncio
import logging
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from test_screen_people import _chat_bot, _clean, _msg


def quiet_bot():
    bot = _chat_bot()
    bot._apply_chat_pacing_settings({})
    return bot


async def test_reported_exchange_stays_viewer_conversation(caplog):
    bot = quiet_bot()
    bot._filler_screen_topic_at = None
    bot._filler_screen_topic_text = None
    bot.last_bot_message_at = None
    bot._on_filler_sent('blizzcon would not survive this room sigaren')
    assert bot._room_followup_until == 0.0
    assert bot.last_bot_message_at is not None
    with caplog.at_level(logging.INFO):
        for text in ['goodluck Amish lol',
                     'meta abt to get fucked again w that sombra and hog rework',
                     'its madibased lol']:
            await bot._handle_chat_message(_msg(text, username='zattaran'))
    _clean(caplog)
    assert bot.generated == []
    assert bot.sent == []
    assert bot._replied_to == {}


@pytest.mark.parametrize('streamed', [False, True])
async def test_direct_address_gets_one_followup_then_leaves_room(streamed, caplog):
    bot = quiet_bot()
    if streamed:
        bot._try_streamed_reply = AsyncMock(return_value=(True, 'that was the plan'))
    with caplog.at_level(logging.INFO):
        await bot._handle_chat_message(_msg('elimist_ what do you think'))
        assert bot._is_followup('motherchandelure')
        await bot._handle_chat_message(_msg('why would you do that?'))
        assert not bot._is_followup('motherchandelure')
        await bot._handle_chat_message(_msg('its madibased lol'))
        await bot._handle_chat_message(_msg('elimist_ one more question'))
    _clean(caplog)
    assert bot._is_followup('motherchandelure'), 'a new direct address resets the cap'
    if streamed:
        assert bot._try_streamed_reply.await_count == 3
    else:
        assert len(bot.sent) == 3


@pytest.mark.parametrize('streamed', [False, True])
async def test_unsolicited_chat_reply_never_creates_a_followup_window(streamed, caplog):
    bot = quiet_bot()
    bot.last_bot_message_at = datetime.now() - timedelta(seconds=100)
    if streamed:
        bot._try_streamed_reply = AsyncMock(return_value=(True, 'a rare interjection'))
    await bot._handle_chat_message(_msg('ordinary chat'))
    _clean(caplog)
    assert not bot._is_followup('motherchandelure')
    assert bot._replied_to == {}


async def test_twitch_reply_to_bot_is_an_address_without_retyping_name(caplog):
    bot = quiet_bot()
    msg = _msg('what do you mean?')
    msg['tags'] = {'reply-parent-user-login': 'ELIMIST_'}
    await bot._handle_chat_message(msg)
    _clean(caplog)
    assert bot.sent == ['that was the plan']
    assert bot._is_followup('motherchandelure')


async def test_reply_to_another_viewer_does_not_use_the_bot_window(caplog):
    bot = quiet_bot()
    bot._note_replied_to('motherchandelure', explicitly_addressed=True)
    msg = _msg('good luck!')
    msg['tags'] = {'reply-parent-user-login': 'someone_else'}
    await bot._handle_chat_message(msg)
    _clean(caplog)
    assert bot.sent == []


def test_followup_deadline_is_not_extended_by_a_reply():
    bot = quiet_bot()
    before = time.monotonic() - 25
    bot._replied_to['viewer'] = (before, 0)
    assert bot._is_followup('viewer', consume=True)
    bot._note_replied_to('viewer')
    assert bot._replied_to['viewer'] == (before, 1)
    bot._replied_to['viewer'] = (time.monotonic() - 31, 0)
    assert not bot._is_followup('viewer')
    bot._note_replied_to('viewer')
    assert not bot._is_followup('viewer'), 'an optional reply cannot revive an expired exchange'


def test_settings_disable_an_already_open_room_window():
    bot = _chat_bot(unsolicited_ago_s=5)
    bot._apply_chat_pacing_settings({'conversation': {
        'unsolicited_gap_seconds': '120', 'followup_window_seconds': '20',
        'max_followups': 0}, 'screen_awareness': {'room_followup_seconds': 0}})
    assert bot._room_followup_until == 0
    assert bot.unsolicited_gap_s == 120
    assert bot.followup_window_s == 20
    bot._note_replied_to('viewer', explicitly_addressed=True)
    assert not bot._is_followup('viewer')


def test_pacing_settings_tolerate_invalid_values():
    bot = quiet_bot()
    bot._apply_chat_pacing_settings({'conversation': {
        'unsolicited_gap_seconds': '90s', 'followup_window_seconds': None,
        'max_followups': float('nan')}})
    assert (bot.unsolicited_gap_s, bot.followup_window_s, bot.max_followups) == (90, 30, 1)


async def test_two_messages_in_flight_cannot_both_claim_one_followup(caplog):
    bot = quiet_bot()
    bot._note_replied_to('motherchandelure', explicitly_addressed=True)
    original = bot.personality_engine.generate_response

    async def slow_response(**kwargs):
        await asyncio.sleep(0.01)
        return await original(**kwargs)

    bot.personality_engine.generate_response = slow_response
    await asyncio.gather(
        bot._handle_chat_message(_msg('why would you do that?')),
        bot._handle_chat_message(_msg('its madibased lol')),
    )
    _clean(caplog)
    assert len(bot.generated) == len(bot.sent) == 1
    assert not bot._is_followup('motherchandelure')


async def test_explicit_mention_in_a_viewer_thread_still_gets_answered(caplog):
    bot = quiet_bot()
    msg = _msg('@elimist_ what do you think?')
    msg['tags'] = {'reply-parent-user-login': 'someone_else'}
    await bot._handle_chat_message(msg)
    _clean(caplog)
    assert len(bot.sent) == 1


def test_settings_update_existing_chat_velocity_without_losing_history():
    from features.chat_velocity import ChatVelocity
    bot = quiet_bot()
    velocity = bot.chat_velocity = ChatVelocity(quiet_boost=2.5)
    velocity.note_message()
    bot._apply_chat_pacing_settings({'chat_velocity': {'quiet_boost': 1.0}})
    assert bot.chat_velocity is velocity
    assert velocity.quiet_boost == 1.0
    assert velocity.messages_seen == 1
