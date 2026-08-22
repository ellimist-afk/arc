"""VOICE_BACKEND must gate the voice path, in both directions.

The risk this guards: with the realtime backend active, a legacy transcript
that ALSO ran the staged pipeline would make Arc answer twice per utterance
(the same class of bug as cdcf3f3's doubled voice input, one layer up).

_handle_voice_input is exercised directly on a bare TalkBot instance; no
network, no audio device, no database, no bot setup.
"""
import asyncio

import pytest

from bot.bot import TalkBot


def _bot(**config):
    """A TalkBot with only the attributes _handle_voice_input touches."""
    bot = TalkBot.__new__(TalkBot)
    bot.config = {'BOT_NAME': 'talkbot', 'TWITCH_CHANNEL': 'cassova_', **config}
    bot.realtime_backend = None
    bot.voice_commands = None
    bot.personality_engine = None
    bot.last_voice_response = None
    bot.recent_voice_texts = []
    bot.response_times = []
    return bot


class SpyRealtime:
    def __init__(self):
        self.transcripts = []

    async def on_legacy_transcript(self, text):
        self.transcripts.append(text)
        return None


async def test_realtime_routes_transcripts_to_wake_detection_only():
    bot = _bot(VOICE_BACKEND='realtime')
    spy = SpyRealtime()
    bot.realtime_backend = spy
    # would raise AttributeError if the staged pipeline ran (no engine wired)
    await bot._handle_voice_input('hey bud can you hear me')
    assert spy.transcripts == ['hey bud can you hear me']
    assert bot.recent_voice_texts == [], "staged pipeline must not also run"


async def test_realtime_forwards_even_short_and_duplicate_text():
    """The <4 char filter and the duplicate filter belong to the staged
    pipeline; the wake detector must see everything the recognizer heard."""
    bot = _bot(VOICE_BACKEND='realtime')
    spy = SpyRealtime()
    bot.realtime_backend = spy
    await bot._handle_voice_input('hi')
    await bot._handle_voice_input('hi')
    assert spy.transcripts == ['hi', 'hi']


async def test_legacy_path_untouched_when_no_realtime_backend():
    """Default config: the staged pipeline still applies its own filters."""
    bot = _bot()
    assert bot.realtime_backend is None
    await bot._handle_voice_input('hi')          # under the 4-char floor
    assert bot.recent_voice_texts == []
    # a longer non-trigger phrase reaches the trigger check and is ignored
    await bot._handle_voice_input('just talking to my chat about the game')
    assert bot.realtime_backend is None, "legacy must never construct a backend"


async def test_setup_failure_falls_back_to_legacy_loudly(caplog):
    """A misconfigured realtime backend must not silently disable voice."""
    import logging
    bot = _bot(VOICE_BACKEND='realtime')
    bot.audio_queue = None
    bot.task_registry = None
    bot.service_registry = None
    # no REALTIME_INPUT_DEVICE/OUTPUT_DEVICE -> setup must raise internally
    with caplog.at_level(logging.ERROR):
        await bot._setup_realtime_backend()
    assert bot.realtime_backend is None
    assert any('REALTIME BACKEND FAILED TO START' in r.message
               for r in caplog.records if r.levelno == logging.ERROR)
    assert any('staying on the legacy' in r.message for r in caplog.records)
