"""Context builder: first-message detection evidence rules, live-field refresh,
and the engine's knowledge block for stream/first-timer lines. No DB."""
from datetime import datetime, timedelta, timezone

from bot.channel_chat_buffer import ChannelChatBuffer
from bot.optimized_context_builder import OptimizedContextBuilder
from personality.personality_engine import PersonalityEngine

NOW = datetime(2026, 8, 22, 20, 0, 0)
first = OptimizedContextBuilder._is_first_message


def data(history, viewer=None):
    # viewer=None models a timed-out fetch (the builder's gather returns None)
    return {"interaction_history": history, "viewer_data": viewer}


# --------------------------------------------------------- first message

def test_exactly_one_db_row_with_fresh_user_is_first():
    d = data([{"message": "hi"}], {"username": "v", "first_seen": NOW - timedelta(seconds=5)})
    assert first(d, now=NOW)


def test_zero_rows_is_not_evidence():
    # channel mismatch / failed insert look like this; never greet on it
    assert not first(data([], {"username": "v"}), now=NOW)


def test_timed_out_fetch_is_not_evidence():
    assert not first(data(None, {"username": "v"}), now=NOW)
    assert not first(data([{"message": "hi"}], None), now=NOW)


def test_multiple_rows_is_returning():
    assert not first(data([{"message": "a"}, {"message": "b"}], {"username": "v"}), now=NOW)


def test_in_memory_fallback_is_not_evidence():
    assert not first(data([{"message": "hi"}], {"username": "v", "from_memory": True}), now=NOW)


def test_old_first_seen_means_history_was_pruned():
    d = data([{"message": "hi"}], {"username": "v", "first_seen": NOW - timedelta(days=30)})
    assert not first(d, now=NOW)


def test_aware_first_seen_handled():
    aware_now = NOW.replace(tzinfo=timezone.utc)
    d = data([{"message": "hi"}], {"username": "v", "first_seen": aware_now - timedelta(seconds=3)})
    assert first(d, now=aware_now)


def test_naive_aware_mismatch_stays_quiet():
    d = data([{"message": "hi"}], {"username": "v", "first_seen": NOW.replace(tzinfo=timezone.utc)})
    assert not first(d, now=NOW)  # naive now vs aware first_seen -> TypeError -> False


def test_missing_first_seen_is_ok_when_history_says_one():
    assert first(data([{"message": "hi"}], {"username": "v"}), now=NOW)


# --------------------------------------------------------- live fields

class StubStreamInfo:
    def describe(self):
        return "playing Elden Ring"


def test_refresh_live_fields_clears_first_flag_and_adds_stream():
    buf = ChannelChatBuffer()
    buf.append_viewer("ch", "v", "hello")
    b = OptimizedContextBuilder(memory_system=None, chat_buffer=buf)
    b.stream_info = StubStreamInfo()
    ctx = {"is_first_message": True, "recent_messages": []}
    b._refresh_live_fields(ctx, "ch")
    assert ctx["is_first_message"] is False
    assert ctx["stream_now"] == "playing Elden Ring"
    assert ctx["recent_messages"][0]["message"] == "hello"
    assert ctx["session_summary"] == ""


def test_stream_now_tolerates_missing_or_broken_provider():
    b = OptimizedContextBuilder(memory_system=None)
    assert b._stream_now() == ""

    class Broken:
        def describe(self):
            raise RuntimeError("x")
    b.stream_info = Broken()
    assert b._stream_now() == ""


# --------------------------------------------------------- engine block

def test_knowledge_block_orders_stream_then_summary_and_ends_with_greeting():
    e = PersonalityEngine(memory_system=None, openai_api_key=None)
    ctx = {
        "stream_now": 'playing Elden Ring, stream title "no hit run"',
        "session_summary": "chat is betting on a death count.",
        "engagement_level": "new",
        "greet_first_timer": True,
    }
    lines = e._format_context_knowledge(ctx, "newbie").splitlines()
    assert lines[1] == '- Stream right now: playing Elden Ring, stream title "no hit run"'
    assert lines[2] == "- Earlier this stream: chat is betting on a death count."
    assert lines[-1].startswith("- newbie is chatting here for the FIRST time ever.")


def test_no_greeting_line_without_flag():
    e = PersonalityEngine(memory_system=None, openai_api_key=None)
    block = e._format_context_knowledge({"is_first_message": True, "engagement_level": "new"}, "v")
    assert "FIRST time" not in block, "detection alone must not change the prompt; policy must opt in"


def test_base_url_env_is_picked_up(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    e = PersonalityEngine(memory_system=None, openai_api_key="ollama")
    assert e.openai_base_url == "http://localhost:11434/v1"
    assert str(e.openai_client.base_url).startswith("http://localhost:11434/v1")
    e2 = PersonalityEngine(memory_system=None, openai_api_key="x", openai_base_url="http://h:1/v1")
    assert e2.openai_base_url == "http://h:1/v1"


# ------------------------------- first-timer flag must not leak (Bugbot F7)

class _FakeL2:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def put(self, key, value):
        self.store[key] = value


class _StubMemory:
    """Positive first-message evidence: exactly one stored turn, fresh user."""
    def __init__(self):
        self.history = [{"message": "hi", "seq": 1}]

    async def get_viewer_context(self, viewer, channel):
        return {"username": viewer, "first_seen": datetime.now()}

    async def get_recent_messages(self, channel=None, limit=50, username=None):
        return list(self.history)

    async def get_interaction_history(self, viewer, channel, limit=5):
        return list(self.history)

    async def get_channel_context(self, channel):
        return {"channel": channel}


async def test_greeting_flag_never_leaks_into_the_cached_context():
    """bot.py copies before setting greet_first_timer, because build_context
    hands back the very dict it keeps in L1/L2. A leaked flag would greet the
    same viewer as a first-timer for the rest of the stream."""
    b = OptimizedContextBuilder(memory_system=_StubMemory())
    b.l2_cache = _FakeL2()

    ctx1 = await b.build_context(viewer="newbie", channel="ch", message="hi")
    assert ctx1["is_first_message"] is True

    # What the bot does: copy, then flag.
    per_request = dict(ctx1)
    per_request["greet_first_timer"] = True

    second = await b.build_context(viewer="newbie", channel="ch", message="hello again")
    assert "greet_first_timer" not in second, "the greeting instruction leaked via cache"
    assert second["is_first_message"] is False, "a cache hit is never a first message"


async def test_refresh_clears_a_greeting_flag_that_somehow_got_cached():
    """Defence in depth: even if some caller mutates a cached context, the
    live-field refresh strips the transient flag on the next hit."""
    b = OptimizedContextBuilder(memory_system=_StubMemory())
    b.l2_cache = _FakeL2()

    ctx = await b.build_context(viewer="newbie", channel="ch", message="hi")
    ctx["greet_first_timer"] = True          # simulate the old leaking write

    again = await b.build_context(viewer="newbie", channel="ch", message="hi again")
    assert "greet_first_timer" not in again
