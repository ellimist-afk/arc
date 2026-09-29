"""Voice wakeups must answer the current spoken turn, not revive old chat."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from bot.bot import TalkBot
from personality.personality_engine import PersonalityEngine


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("cached", [False, True])
async def test_voice_source_reaches_both_paths_without_mutating_cache(streamed, cached, caplog):
    context = {'recent_messages': [{'username': 'LimoneneTTV',
                                   'message': 'ong which sentence'}]}
    bot = TalkBot.__new__(TalkBot)
    bot.config = {'TWITCH_CHANNEL': 'cassova_', 'TWITCH_BOT_USERNAME': 'elimist_'}
    bot.message_count = 0
    bot.response_times = []
    bot._note_engaged = Mock()
    bot._note_bot_line = Mock()
    bot._schedule_summary = Mock()
    bot._with_room_reaction = lambda c: c
    bot.memory_system = SimpleNamespace(store_message=AsyncMock(),
        get_context_optimized=AsyncMock(return_value=context))
    bot.context_builder = (SimpleNamespace(build_context=AsyncMock(return_value=context))
                           if cached else None)
    bot.chat_buffer = Mock()
    bot.audio_queue = None
    bot.response_coordinator = SimpleNamespace(coordinate_response=AsyncMock())
    bot._try_streamed_reply = AsyncMock(return_value=(streamed, 'which map?' if streamed else None))
    bot.personality_engine = SimpleNamespace(generate_response=AsyncMock(
        return_value={'text': 'which map?'}))
    await bot._handle_voice_message({'username': 'cassova_', 'text': 'Vote for your map Hey bot'})
    assert 'Error handling voice message' not in caplog.text
    passed = bot._try_streamed_reply.call_args.kwargs['context']
    assert passed['input_source'] == 'voice'
    assert 'input_source' not in context
    if not streamed:
        assert bot.personality_engine.generate_response.call_args.kwargs['context'] is passed
    else:
        bot.personality_engine.generate_response.assert_not_called()


@pytest.mark.parametrize('utterance', ['Vote for your map Hey bot', 'hey bot'])
def test_current_voice_turn_stays_last_and_history_is_only_background(utterance):
    engine = PersonalityEngine.__new__(PersonalityEngine)
    engine._format_context_knowledge = lambda context, user: ''
    context = {'input_source': 'voice', 'recent_messages': [
        {'username': 'LimoneneTTV', 'message': 'ong which sentence'},
        {'username': 'elimist_', 'role': 'assistant', 'message': 'the belly one'},
    ]}
    messages = engine._build_messages(utterance, context, 'cassova_', 'Be conversational.')
    assert messages[-1] == {'role': 'user', 'content': f'cassova_: {utterance}'}
    assert 'CURRENT spoken turn' in messages[0]['content']
    assert 'briefly ask what they mean' in messages[0]['content']
    assert any('ong which sentence' in m['content'] for m in messages)
    context.pop('input_source')
    chat = engine._build_messages('hello', context, 'LimoneneTTV', 'Be conversational.')
    assert 'CURRENT spoken turn' not in chat[0]['content']


def test_stream_voice_turn_quotes_previous_viewer_exchange_as_background():
    engine = PersonalityEngine.__new__(PersonalityEngine)
    engine._format_context_knowledge = lambda context, user: ''
    turn = 'hey bot I also eat a lot of mostly just protein'
    context = {'input_source': 'voice', 'recent_messages': [
        {'username': 'GoodStuffBuds', 'message': 'I am starving myself'},
        {'username': 'elimist_', 'role': 'assistant', 'message': 'eat something goodstuffbuds'},
        {'username': 'cassova_', 'message': turn},
    ]}
    speaker = 'cassova_ (the streamer, your co-host partner)'
    messages = engine._build_messages(turn, context, speaker, 'Be conversational.')

    assert [m['role'] for m in messages] == ['system', 'user', 'user']
    background = json.loads(messages[1]['content'].split('\n', 1)[1])
    assert [m['content'] for m in background] == [
        'GoodStuffBuds: I am starving myself', 'eat something goodstuffbuds']
    assert f'Your current conversation partner is {speaker}' in messages[0]['content']
    assert messages[-1]['content'] == f'{speaker}: {turn}'
    assert context['recent_messages'][-1]['message'] == turn

    # Chat still uses chronological conversation turns, including bot replies.
    context.pop('input_source')
    chat = engine._build_messages(turn, context, 'cassova_', 'Be conversational.')
    assert [m['role'] for m in chat] == ['system', 'user', 'assistant', 'user']
