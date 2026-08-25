"""Every queued audio clip honors the injected TTS gate.

Chat replies checked TTS_ENABLED, but the dead-air filler, ad announcer and
event announcer queued audio unconditionally — with TTS "off", the bot still
spoke on follows and lulls (heard live 2026-08-24). The gate lives at the
queue chokepoint and reads config live, so the settings hot-reload applies.
"""
import asyncio

from audio.optimized_queue import OptimizedAudioQueue


def make_queue():
    q = OptimizedAudioQueue(openai_api_key="test", enable_pre_buffering=False)
    return q


async def test_gate_off_drops_every_clip():
    q = make_queue()
    q.tts_gate = lambda: False
    await q.queue_audio("dead air filler line", priority="low")
    await q.queue_audio("thanks for the follow", priority="high")
    assert q.queue == []


async def test_gate_on_enqueues():
    q = make_queue()
    q.tts_gate = lambda: True
    await q.queue_audio("hello there", priority="normal")
    assert len(q.queue) == 1


async def test_gate_flips_live_like_a_settings_reload():
    q = make_queue()
    config = {"TTS_ENABLED": False}
    q.tts_gate = lambda: bool(config.get("TTS_ENABLED", True))
    await q.queue_audio("muted era", priority="normal")
    config["TTS_ENABLED"] = True                    # hot-reload flips the flag
    await q.queue_audio("audible era", priority="normal")
    assert [i.text for i in q.queue] == ["audible era"]


async def test_no_gate_behaves_as_before():
    q = make_queue()
    await q.queue_audio("legacy behavior", priority="normal")
    assert len(q.queue) == 1


async def test_broken_gate_fails_open():
    q = make_queue()

    def boom():
        raise RuntimeError("config gone")
    q.tts_gate = boom
    await q.queue_audio("still audible", priority="normal")
    assert len(q.queue) == 1, "a broken gate must not silently mute the bot"


async def test_streamed_utterances_are_not_gated_here():
    # Streamed replies are gated by the bot before creation; dropping one
    # unconsumed at the queue would strand its reply waiter.
    q = make_queue()
    q.tts_gate = lambda: False

    async def sentences():
        yield "one"
    player = await q.queue_utterance(sentences(), priority="normal")
    assert player is not None and len(q.queue) == 1
