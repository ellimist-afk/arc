"""Doubled voice input fix: each utterance must reach the handler exactly once.

The bug: recognition._audio_callback delivered every recognized utterance
through TWO paths -- the registered on_text_recognized callback AND the
internal queue that bot._process_voice_commands polled -- so
bot._handle_voice_input ran twice per utterance, ~1ms apart.

No audio device is touched: _audio_callback is invoked directly with a stub
recognizer, exactly as speech_recognition's listener thread would.
"""
import asyncio
from pathlib import Path

from components.voice.recognition import VoiceRecognition

BOT_PY = Path(__file__).resolve().parents[1] / 'src' / 'bot' / 'bot.py'


class StubRecognizer:
    """Stands in for sr.Recognizer inside the listener-thread callback."""

    def __init__(self, text: str):
        self.text = text

    def recognize_google(self, audio):
        return self.text


async def test_utterance_delivered_once_via_callback():
    vr = VoiceRecognition()
    received = []

    async def on_text(text):
        received.append(text)

    vr.on_text_recognized = on_text
    vr.main_loop = asyncio.get_running_loop()

    # speech_recognition invokes the callback from its own listener thread
    await asyncio.to_thread(vr._audio_callback, StubRecognizer('hey bot test'), None)
    await asyncio.sleep(0.2)  # let the scheduled coroutine run

    assert received == ['hey bot test']
    assert vr.audio_queue.empty(), (
        "text must not ALSO land on the queue -- that second path is what "
        "doubled every utterance"
    )


async def test_queue_still_serves_pull_consumers_without_callback():
    vr = VoiceRecognition()
    await asyncio.to_thread(vr._audio_callback, StubRecognizer('hello there'), None)
    assert vr.audio_queue.get_nowait() == 'hello there'


def test_bot_no_longer_polls_the_recognition_queue():
    bot_src = BOT_PY.read_text(encoding='utf-8')
    assert '_process_voice_commands' not in bot_src
    assert 'voice_processor' not in bot_src
