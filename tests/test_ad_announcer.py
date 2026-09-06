from unittest.mock import AsyncMock

import pytest

from features.ad_announcer import AdAnnouncer


@pytest.mark.asyncio
async def test_ad_end_announcement_can_be_disabled():
    announcer = AdAnnouncer(twitch_client=None)
    announcer.ad_active = True
    announcer.update_settings({"announce_at_end": False})
    announcer._generate_return_message = AsyncMock()
    announcer._announce_ad = AsyncMock()

    await announcer._handle_ad_end({})

    assert announcer.ad_active is False
    announcer._generate_return_message.assert_not_awaited()
    announcer._announce_ad.assert_not_awaited()


@pytest.mark.asyncio
async def test_ad_end_announcement_remains_available():
    announcer = AdAnnouncer(twitch_client=None)
    announcer.ad_active = True
    announcer.update_settings({"announce_at_end": True})
    announcer._generate_return_message = AsyncMock(return_value="Welcome back!")
    announcer._announce_ad = AsyncMock()

    await announcer._handle_ad_end({})

    announcer._announce_ad.assert_awaited_once_with("Welcome back!", is_start=False)


# --------------------------------------------------------- one chat send

class _Twitch:
    """Counts what actually reaches the channel."""

    def __init__(self):
        self.sent = []

    def is_connected(self):
        return True

    async def send_message(self, msg):
        self.sent.append(msg)


class _Coordinator:
    """Stands in for ResponseCoordinator: it posts the chat line itself."""

    def __init__(self, twitch):
        self.twitch = twitch
        self.calls = []

    async def coordinate_response(self, chat_msg, audio_task=None, priority="normal",
                                  is_mention=False, is_voice=False):
        self.calls.append(chat_msg)
        if chat_msg:
            await self.twitch.send_message(chat_msg)
        if audio_task:
            await audio_task()


class _AudioQueue:
    def __init__(self):
        self.queued = []

    async def queue_audio(self, text, priority="normal"):
        self.queued.append(text)


def _announcer(chat, voice, coordinator=True):
    twitch = _Twitch()
    a = AdAnnouncer(twitch_client=twitch)
    a.announce_in_chat = chat
    a.announce_with_voice = voice
    a.audio_queue = _AudioQueue()
    a.response_coordinator = _Coordinator(twitch) if coordinator else None
    return a, twitch


@pytest.mark.asyncio
async def test_chat_and_voice_together_send_the_ad_line_once():
    """Live 2026-09-06 13:59:45 the ad line posted twice: the direct send
    ran AND the coordinator sent the same text again. TTS was off in
    settings, but this announcer's voice flag is independent of that."""
    a, twitch = _announcer(chat=True, voice=True)

    await a._announce_ad("ads for two minutes, stay put", is_start=True)

    assert twitch.sent == ["ads for two minutes, stay put"]
    assert a.audio_queue.queued == ["ads for two minutes, stay put"]


@pytest.mark.asyncio
async def test_chat_only_still_posts_the_line():
    a, twitch = _announcer(chat=True, voice=False)

    await a._announce_ad("ads incoming", is_start=True)

    assert twitch.sent == ["ads incoming"]
    assert a.audio_queue.queued == []


@pytest.mark.asyncio
async def test_voice_only_says_it_without_posting_it():
    a, twitch = _announcer(chat=False, voice=True)

    await a._announce_ad("ads incoming", is_start=True)

    assert twitch.sent == []
    assert a.audio_queue.queued == ["ads incoming"]


@pytest.mark.asyncio
async def test_voice_without_a_coordinator_falls_back_to_the_queue():
    a, twitch = _announcer(chat=True, voice=True, coordinator=False)

    await a._announce_ad("ads incoming", is_start=True)

    assert twitch.sent == ["ads incoming"], "no coordinator: the direct send is the only one"
    assert a.audio_queue.queued == ["ads incoming"]


@pytest.mark.asyncio
async def test_the_coordinator_does_not_post_a_blank_line():
    """Audio-only is expressed as an empty chat_msg, so the coordinator has
    to treat that as 'nothing to say in chat'."""
    from bot.response_coordinator import ResponseCoordinator

    twitch = _Twitch()
    c = ResponseCoordinator(twitch_client=twitch, audio_queue=None)
    await c.coordinate_response(chat_msg="", priority="high")

    assert twitch.sent == []
