"""An optional line should not pay for a second LLM call.

Measured 2026-08-29: one generation is ~1.9s, and a guard rejection made it
two. Last stream 6 of 11 retries were rejected again and skipped, so that
second call produced nothing at all -- ~11s of generation for zero lines.
Mentions (someone is waiting) and lull fillers (silence otherwise) still
retry; an unsolicited interjection is dropped on the first rejection.
"""
from pathlib import Path


from personality.personality_engine import PersonalityEngine

SRC = Path("src/personality/personality_engine.py").read_text(encoding="utf-8")


def _engine(monkeypatch, always_repetitive=True):
    e = PersonalityEngine.__new__(PersonalityEngine)
    e.repetition_guard_enabled = True
    e.repetition_rejections = 0
    e.repetition_forced = 0
    e.calls = []

    class Guard:
        def check(self, text, fresh_topic=False, topic_exempt=None):
            class V:
                ok = not always_repetitive
                reason = "similarity 0.90"
                score = 0.9
                nearest = "a previous line"
                reused_opening = None
                hot_phrases = []
                retold_from = None
                retold_words = []
            return V()

        def avoid_hint(self, verdict):
            return "vary it"

        @staticmethod
        def topic_words(text):
            return set()
    e.repetition_guard = Guard()

    async def fake_generate(message, context, user, prompt):
        e.calls.append(prompt)
        return "a regenerated line"
    e._generate_text = fake_generate
    return e


async def test_an_unsolicited_line_is_dropped_without_a_second_call(monkeypatch):
    e = _engine(monkeypatch)
    out = await e._enforce_variety(
        text="a draft", message="just chatting", context={}, user="v",
        prompt="p", is_mention=False)
    assert out is None
    assert e.calls == [], "no retry should have been generated"


async def test_a_mention_still_retries():
    e = _engine(None)
    out = await e._enforce_variety(
        text="a draft", message="hey bot", context={}, user="v",
        prompt="p", is_mention=True)
    assert len(e.calls) == 1, "someone is waiting; it must try again"
    assert out is not None, "a mention is always answered"


async def test_a_lull_filler_still_retries():
    e = _engine(None)
    await e._enforce_variety(
        text="a draft", message="[DEAD_AIR_FILLER]", context={}, user="system",
        prompt="p", is_mention=False)
    assert len(e.calls) == 1, "silence otherwise; the retry is worth it"


async def test_a_clean_draft_never_costs_a_second_call():
    e = _engine(None, always_repetitive=False)
    out = await e._enforce_variety(
        text="a fine draft", message="just chatting", context={}, user="v",
        prompt="p", is_mention=False)
    assert out == "a fine draft"
    assert e.calls == []


def test_the_drop_is_logged_distinctly_for_the_auditor():
    assert "dropping instead of regenerating" in SRC


def test_the_rejection_is_still_counted():
    """The drop must not hide from the metrics."""
    idx = SRC.index("dropping instead of regenerating")
    before = SRC[:idx]
    assert "self.repetition_rejections += 1" in before
