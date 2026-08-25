"""Sub/cheer/follow events react in character, with templates as the safety net.

Before: every money moment used a random canned line, so a roast co-host went
warm and generic exactly when a viewer paid. Now the personality engine writes
the reaction; the templates still cover a disabled, failed, slow or empty LLM
path so an event is never silently dropped. Events are also remembered, so
later banter can call back to them.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from features.event_announcer import EventAnnouncer


class FakeEngine:
    def __init__(self, text="deadpan reaction", delay=0.0, raises=False, empty=False):
        self.text, self.delay, self.raises, self.empty = text, delay, raises, empty
        self.calls = []

    async def generate_response(self, message, context, user, is_mention=False):
        self.calls.append({"message": message, "context": context,
                           "user": user, "is_mention": is_mention})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise RuntimeError("llm down")
        if self.empty:
            return None
        return {"text": self.text, "speech_text": self.text}


def make(engine=None, **cfg):
    bot = SimpleNamespace()
    bot.config = {"TWITCH_CHANNEL": "cassova_"}
    bot.personality_engine = engine
    bot.sent = []
    bot.audio = []
    bot.twitch_client = SimpleNamespace(
        send_message=lambda m: _append(bot.sent, m))
    bot.audio_queue = SimpleNamespace(
        queue_audio=lambda m, priority="normal": _append(bot.audio, (m, priority)))
    bot.chat_buffer = SimpleNamespace(get_recent=lambda channel, limit=10: [
        {"username": "v", "message": "chat said this", "role": "viewer"}])
    bot.stream_info = SimpleNamespace(describe=lambda: "playing Overwatch")
    bot.noted = []
    bot.session_summarizer = SimpleNamespace(
        note_event=lambda ch, text: bot.noted.append(text),
        get_summary=lambda ch: "earlier: chat argued about pineapple")
    bot.recapped = []
    bot.stream_recap = SimpleNamespace(record_event=bot.recapped.append)

    ea = EventAnnouncer(bot)
    for k, v in cfg.items():
        setattr(ea, k, v)
    return ea, bot


async def _append(store, *args):
    store.append(args[0] if len(args) == 1 else args)


# --------------------------------------------------------------- in character

async def test_sub_reaction_comes_from_the_personality_engine():
    engine = FakeEngine("congratulations on funding my electricity bill")
    ea, bot = make(engine)
    await ea.handle_subscribe({"user_name": "alice", "tier": "1000"})

    assert bot.sent == ["congratulations on funding my electricity bill"]
    assert bot.audio == [("congratulations on funding my electricity bill", "high")]
    call = engine.calls[0]
    assert "alice just subscribed" in call["message"]
    assert call["user"] == "alice"
    assert call["is_mention"] is True, "an event must never be a coin flip"
    assert ea.reactions_generated == 1 and ea.reactions_fell_back == 0


async def test_reaction_gets_game_summary_and_recent_chat():
    engine = FakeEngine()
    ea, bot = make(engine)
    await ea.handle_cheer({"user_name": "bob", "bits": 100})
    ctx = engine.calls[0]["context"]
    assert ctx["stream_now"] == "playing Overwatch"
    assert ctx["session_summary"].startswith("earlier:")
    assert ctx["recent_messages"][0]["message"] == "chat said this"


@pytest.mark.parametrize("handler, event, expected", [
    ("handle_subscribe", {"user_name": "alice", "tier": "3000"}, "at Tier 3"),
    ("handle_subscribe", {"user_name": "alice", "tier": "1000", "is_gift": True,
                          "gifter_name": "bob"}, "gifted subscription from bob"),
    ("handle_resub", {"user_name": "carol", "cumulative_months": 14}, "14 months"),
    # gift scenarios now carry the tier as well as the count
    ("handle_gift_sub", {"user_name": "dave", "total": 5}, "gifted 5 Tier 1 subscriptions"),
    ("handle_cheer", {"user_name": "erin", "bits": 5000}, "massive cheer of 5000 bits"),
    ("handle_follow", {"user_name": "frank"}, "just followed"),
])
async def test_every_event_type_describes_itself_to_the_model(handler, event, expected):
    engine = FakeEngine()
    ea, bot = make(engine)
    await getattr(ea, handler)(event)
    assert expected in engine.calls[0]["message"], engine.calls[0]["message"]


async def test_cheer_message_reaches_the_model():
    engine = FakeEngine()
    ea, bot = make(engine)
    await ea.handle_cheer({"user_name": "bob", "bits": 200, "message": "you owe me a clip"})
    assert "you owe me a clip" in engine.calls[0]["message"]


# ------------------------------------------------------------------ fallbacks

async def test_llm_failure_falls_back_to_a_template():
    ea, bot = make(FakeEngine(raises=True))
    await ea.handle_subscribe({"user_name": "alice", "tier": "1000"})
    assert len(bot.sent) == 1 and "alice" in bot.sent[0]
    assert ea.reactions_fell_back == 1 and ea.reactions_generated == 0


async def test_empty_response_falls_back():
    ea, bot = make(FakeEngine(empty=True))
    await ea.handle_cheer({"user_name": "bob", "bits": 50})
    assert len(bot.sent) == 1 and "bob" in bot.sent[0]
    assert ea.reactions_fell_back == 1


async def test_slow_llm_times_out_into_a_template():
    ea, bot = make(FakeEngine(delay=0.5), reaction_timeout=0.05)
    await ea.handle_subscribe({"user_name": "alice", "tier": "1000"})
    assert len(bot.sent) == 1 and "alice" in bot.sent[0]
    assert ea.reactions_fell_back == 1


async def test_disabled_llm_reactions_use_templates_without_calling_the_engine():
    engine = FakeEngine()
    ea, bot = make(engine, llm_reactions=False)
    await ea.handle_subscribe({"user_name": "alice", "tier": "1000"})
    assert engine.calls == [] and len(bot.sent) == 1
    assert ea.reactions_fell_back == 1


async def test_missing_engine_still_announces():
    ea, bot = make(None)
    await ea.handle_gift_sub({"user_name": "dave", "total": 2})
    assert len(bot.sent) == 1 and "dave" in bot.sent[0]


async def test_disabled_announcer_does_nothing():
    engine = FakeEngine()
    ea, bot = make(engine, enabled=False)
    await ea.handle_subscribe({"user_name": "alice", "tier": "1000"})
    assert bot.sent == [] and engine.calls == []


# -------------------------------------------------------------------- memory

async def test_events_are_remembered_for_later_banter():
    ea, bot = make(FakeEngine())
    await ea.handle_subscribe({"user_name": "alice", "tier": "2000"})
    await ea.handle_cheer({"user_name": "bob", "bits": 300})
    await ea.handle_follow({"user_name": "carol"})
    assert bot.noted == ["alice subscribed at Tier 2", "bob cheered 300 bits", "carol followed"]
    assert bot.recapped == bot.noted


async def test_batched_follows_announce_once_and_are_remembered():
    ea, bot = make(FakeEngine())
    await ea.handle_follow({"user_name": "one"})          # starts the cooldown
    await ea.handle_follow({"user_name": "two"})          # queued
    await ea.handle_follow({"user_name": "three"})        # queued
    ea.last_follow_time = None                            # cooldown expires
    await ea.handle_follow({"user_name": "four"})
    assert len(bot.sent) == 2, bot.sent
    assert "two" in bot.sent[1] and "four" in bot.sent[1]
    assert any("new followers" in n for n in bot.noted)


async def test_broken_memory_hooks_do_not_lose_the_announcement():
    ea, bot = make(FakeEngine())

    def boom(*a, **k):
        raise RuntimeError("summarizer gone")
    bot.session_summarizer = SimpleNamespace(note_event=boom, get_summary=lambda ch: "")
    bot.stream_recap = SimpleNamespace(record_event=boom)
    await ea.handle_subscribe({"user_name": "alice", "tier": "1000"})
    assert len(bot.sent) == 1


def test_settings_control_the_reaction_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bot_settings.json").write_text(
        json.dumps({"event_reactions": {"enabled": False, "timeout_s": 3}}))
    ea = EventAnnouncer(SimpleNamespace(config={}))
    assert ea.llm_reactions is False and ea.reaction_timeout == 3.0


def test_missing_settings_default_to_enabled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ea = EventAnnouncer(SimpleNamespace(config={}))
    assert ea.llm_reactions is True and ea.reaction_timeout == 8.0
