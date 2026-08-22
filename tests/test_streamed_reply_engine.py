"""PersonalityEngine.generate_response_streamed: laziness, sentence yield,
guard gating (first-sentence fallback, later-sentence drop), failure modes,
and finalization of chat text from what was actually spoken.

The OpenAI client is faked: `chat.completions.create(stream=True)` returns
an async iterator of chunk-shaped objects. No network.
"""
import asyncio
from types import SimpleNamespace

import pytest

from personality.personality_engine import PersonalityEngine


def chunk(text):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


class FakeStream:
    """Async iterator of deltas; optionally raises after N deltas."""
    def __init__(self, deltas, raise_after=None, delay=0.0):
        self.deltas, self.raise_after, self.delay = list(deltas), raise_after, delay
        self.i = 0
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.raise_after is not None and self.i >= self.raise_after:
            raise RuntimeError("stream died")
        if self.i >= len(self.deltas):
            raise StopAsyncIteration
        if self.delay:
            await asyncio.sleep(self.delay)
        d = self.deltas[self.i]
        self.i += 1
        return chunk(d)


class FakeClient:
    def __init__(self, *streams):
        self.streams = list(streams)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kw):
        self.calls.append(kw)
        assert kw.get("stream") is True
        if not self.streams:
            raise RuntimeError("no stream scripted")
        s = self.streams.pop(0)
        if isinstance(s, Exception):
            raise s
        return s


def make_engine(*streams):
    e = PersonalityEngine(memory_system=None, openai_api_key="test-key")
    e.openai_client = FakeClient(*streams)
    e.should_respond_override = True
    return e


async def collect(reply):
    out = []
    async for s in reply.sentences:
        out.append(s)
    return out


def words(text, n=3):
    """Split text into deltas of n characters to simulate token streaming."""
    return [text[i:i + n] for i in range(0, len(text), n)]


CTX = {"recent_messages": [], "engagement_level": "new"}


async def test_is_lazy_until_iterated():
    e = make_engine(FakeStream(words("Hello there chat. Second sentence here.")))
    reply = await e.generate_response_streamed("hi", CTX, "viewer", is_mention=True)
    assert reply is not None
    assert e.openai_client.calls == [], "model must not be called before something pulls sentences"
    got = await collect(reply)
    assert len(e.openai_client.calls) == 1
    assert got == ["Hello there chat.", "Second sentence here."]
    assert await reply.wait() is not None


async def test_text_is_assembled_from_spoken_sentences_and_recorded():
    e = make_engine(FakeStream(words("First sentence is here. Second sentence is here.")))
    reply = await e.generate_response_streamed("hi", CTX, "viewer", is_mention=True)
    await collect(reply)
    assert reply.done.is_set()
    assert reply.speech_text == "First sentence is here. Second sentence is here."
    assert reply.text  # personality modifications applied (may lowercase/strip)
    assert reply.fell_back is False and reply.aborted is False
    # the guard saw the whole reply
    assert e.repetition_guard.check("First sentence is here. Second sentence is here.").ok is False


async def test_speech_filter_applies_to_yield_but_not_chat_text():
    e = make_engine(FakeStream(words("Hey @viewer welcome in. Good to see you here.")))
    reply = await e.generate_response_streamed(
        "hi", CTX, "viewer", is_mention=True, speech_filter=lambda s: s.replace("@", "")
    )
    got = await collect(reply)
    assert got[0] == "Hey viewer welcome in."
    assert "@viewer" in reply.speech_text


async def test_first_sentence_rejected_falls_back_to_blocking_path():
    dup = "Honestly that build is a war crime."
    e = make_engine(FakeStream(words(dup + " And more after it.")))
    e.repetition_guard.record(dup)  # make the opener a near-duplicate

    async def fake_blocking(message, context, user, is_mention=False):
        return {"text": "fresh take here", "speech_text": "Fresh take on that build. Still bad though."}
    e.generate_response = fake_blocking

    reply = await e.generate_response_streamed("hi", CTX, "viewer", is_mention=True)
    got = await collect(reply)
    assert got == ["Fresh take on that build.", "Still bad though."]
    assert reply.fell_back is True
    assert reply.speech_text == "Fresh take on that build. Still bad though."
    assert e.repetition_rejections >= 1


async def test_later_duplicate_sentence_is_dropped_not_regenerated():
    dup = "Chat is absolutely unhinged tonight."
    e = make_engine(FakeStream(words("New opener for this reply. " + dup + " Closing thought goes here.")))
    e.repetition_guard.record(dup)
    reply = await e.generate_response_streamed("hi", CTX, "viewer", is_mention=True)
    got = await collect(reply)
    assert got == ["New opener for this reply.", "Closing thought goes here."]
    assert reply.fell_back is False
    assert dup not in reply.speech_text


async def test_stream_failure_after_first_token_ends_with_what_was_said():
    deltas = words("Said this much already. Then the connec")
    e = make_engine(FakeStream(deltas, raise_after=len(deltas) - 1))
    reply = await e.generate_response_streamed("hi", CTX, "viewer", is_mention=True)
    got = await collect(reply)
    assert got[0] == "Said this much already."
    assert reply.done.is_set()
    assert len(e.openai_client.calls) == 1, "no retry after first token (would double-speak)"


async def test_stream_failure_before_first_token_retries_once():
    e = make_engine(RuntimeError("429"), FakeStream(words("Recovered on retry fine.")))
    reply = await e.generate_response_streamed("hi", CTX, "viewer", is_mention=True)
    got = await collect(reply)
    assert got == ["Recovered on retry fine."]
    assert len(e.openai_client.calls) == 2


async def test_total_failure_finalizes_with_no_text():
    e = make_engine(RuntimeError("down"), RuntimeError("still down"))
    reply = await e.generate_response_streamed("hi", CTX, "viewer", is_mention=True)
    got = await collect(reply)
    assert got == []
    assert reply.done.is_set() and reply.text is None


async def test_consumer_abandoning_midway_finalizes_spoken_part():
    e = make_engine(FakeStream(words("One full sentence here. Two full sentence here. Three."), delay=0.001))
    reply = await e.generate_response_streamed("hi", CTX, "viewer", is_mention=True)
    first = await reply.sentences.__anext__()
    await reply.sentences.aclose()
    assert first == "One full sentence here."
    assert reply.done.is_set()
    assert reply.aborted is True
    assert reply.speech_text == "One full sentence here."


async def test_declines_when_personality_would_not_respond():
    e = make_engine(FakeStream(words("never sent")))
    e.should_respond_override = None
    e._should_respond = lambda message, is_mention: False
    assert await e.generate_response_streamed("hi", CTX, "viewer", is_mention=False) is None
    assert e.openai_client.calls == []


async def test_no_client_returns_none():
    e = PersonalityEngine(memory_system=None, openai_api_key=None)
    assert await e.generate_response_streamed("hi", CTX, "viewer", is_mention=True) is None


def test_build_messages_matches_blocking_path_shape():
    e = make_engine()
    ctx = {"recent_messages": [
        {"username": "a", "message": "older", "role": "viewer"},
        {"username": "bot", "message": "reply", "role": "assistant"},
    ]}
    msgs = e._build_messages("now", ctx, "viewer", "SYSTEM")
    assert msgs[0]["role"] == "system" and msgs[0]["content"].startswith("SYSTEM")
    assert msgs[-1] == {"role": "user", "content": "viewer: now"}
    assert [m["role"] for m in msgs[1:-1]] == ["user", "assistant"]
    assert msgs[1]["content"] == "a: older"
