"""ResponseCoordinator.coordinate_streamed_response: audio queued immediately,
chat posted with the finalized text, drains when there's no audio queue,
times out without hanging."""
import asyncio

from bot.response_coordinator import ResponseCoordinator
from personality.streamed_reply import StreamedReply


class FakeTwitch:
    def __init__(self):
        self.sent = []

    def is_connected(self):
        return True

    async def send_message(self, text):
        self.sent.append(text)


class FakeAudioQueue:
    """Consumes the sentence stream in the background, like the real queue's player would."""
    def __init__(self):
        self.consumed = []
        self.calls = []

    async def queue_utterance(self, sentences, **kw):
        self.calls.append(kw)

        async def consume():
            async for s in sentences:
                self.consumed.append(s)
        asyncio.create_task(consume())
        return object()


def make_reply(items, finalize_text=True):
    reply = StreamedReply(personality="test")

    async def gen():
        try:
            for s in items:
                await asyncio.sleep(0.001)
                yield s
        finally:
            if finalize_text:
                reply.speech_text = " ".join(items)
                reply.text = reply.speech_text.lower()
            reply.done.set()
    reply.sentences = gen()
    return reply


async def test_queues_audio_then_posts_finalized_text():
    tw, aq = FakeTwitch(), FakeAudioQueue()
    rc = ResponseCoordinator(twitch_client=tw, audio_queue=aq, settings_path="nope.json")
    reply = make_reply(["Hello there.", "Second one."])
    text = await rc.coordinate_streamed_response(reply, priority="high", is_mention=True, user="v", prefetch_depth=3)
    assert aq.calls[0]["priority"] == "high" and aq.calls[0]["is_mention"] is True
    assert aq.calls[0]["prefetch_depth"] == 3
    assert aq.consumed == ["Hello there.", "Second one."]
    assert tw.sent == ["hello there. second one."]
    assert text == "hello there. second one."
    assert rc.response_count == 1


async def test_no_audio_queue_drains_and_still_posts_chat():
    tw = FakeTwitch()
    rc = ResponseCoordinator(twitch_client=tw, audio_queue=None, settings_path="nope.json")
    reply = make_reply(["Only chat."])
    text = await rc.coordinate_streamed_response(reply)
    assert tw.sent == ["only chat."] and text == "only chat."


async def test_nothing_produced_posts_nothing():
    tw, aq = FakeTwitch(), FakeAudioQueue()
    rc = ResponseCoordinator(twitch_client=tw, audio_queue=aq, settings_path="nope.json")
    reply = make_reply([], finalize_text=False)
    text = await rc.coordinate_streamed_response(reply)
    assert tw.sent == [] and text is None


async def test_timeout_does_not_hang():
    tw = FakeTwitch()

    class NeverConsumes:
        async def queue_utterance(self, sentences, **kw):
            return object()  # nobody pulls; reply never finalizes

    rc = ResponseCoordinator(twitch_client=tw, audio_queue=NeverConsumes(), settings_path="nope.json")
    reply = make_reply(["stuck"])
    text = await rc.coordinate_streamed_response(reply, timeout=0.05)
    assert text is None and tw.sent == []
