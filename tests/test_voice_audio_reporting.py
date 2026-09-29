"""A muted voice reply still reaches chat, without claiming audio was queued."""
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from audio.optimized_queue import OptimizedAudioQueue
from bot.bot import TalkBot


@pytest.mark.parametrize('coordinated', [False, True])
@pytest.mark.parametrize('tts_enabled, gate_enabled', [
    (False, False), (True, True), (True, False),
])
async def test_voice_audio_count_and_log_follow_queue_acceptance(
    coordinated, tts_enabled, gate_enabled, caplog,
):
    caplog.set_level(logging.INFO)
    bot = TalkBot.__new__(TalkBot)
    bot.config = {'TWITCH_CHANNEL': 'cassova_', 'TWITCH_BOT_USERNAME': 'elimist_',
                  'TTS_ENABLED': tts_enabled}
    bot.message_count = bot.audio_count = 0
    bot.response_times = []
    bot._note_engaged = Mock()
    bot._note_bot_line = Mock()
    bot._schedule_summary = Mock()
    bot._with_room_reaction = lambda c: c
    bot.memory_system = SimpleNamespace(store_message=AsyncMock())
    bot.context_builder = SimpleNamespace(build_context=AsyncMock(return_value={}))
    bot.chat_buffer = Mock()
    bot.audio_queue = OptimizedAudioQueue(openai_api_key='test', enable_pre_buffering=False)
    # A false gate with TTS_ENABLED true reproduces a live settings change
    # between choosing delivery and trying to enqueue the audio.
    bot.audio_queue.tts_gate = lambda: gate_enabled
    bot.twitch_client = SimpleNamespace(is_connected=lambda: True, send_message=AsyncMock())
    bot._try_streamed_reply = AsyncMock(return_value=(False, None))
    bot.personality_engine = SimpleNamespace(generate_response=AsyncMock(
        return_value={'text': 'which map?', 'speech_text': 'Which map?'}))

    async def coordinate_response(chat_msg, audio_task, **kwargs):
        await bot.twitch_client.send_message(chat_msg)
        if audio_task:
            await audio_task()

    bot.response_coordinator = (SimpleNamespace(coordinate_response=AsyncMock(
        side_effect=coordinate_response)) if coordinated else None)
    await bot._handle_voice_message({'username': 'cassova_', 'text': 'hey bot'})

    assert 'Error handling voice message' not in caplog.text
    bot.twitch_client.send_message.assert_awaited_once_with('which map?')
    accepted = tts_enabled and gate_enabled
    assert bot.audio_count == int(accepted)
    assert len(bot.audio_queue.queue) == int(accepted)
    assert ('Voice response queued:' in caplog.text) is accepted
    if accepted:
        assert bot.audio_queue.queue[0].text == 'Which map?'
    elif not tts_enabled:
        assert 'Voice response is chat-only' in caplog.text
        if coordinated:
            assert bot.response_coordinator.coordinate_response.call_args.kwargs['audio_task'] is None
    else:
        assert 'Voice response audio not queued' in caplog.text
