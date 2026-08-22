"""PersonalityEngine + RepetitionGuard: the regenerate-once contract.

`_generate_text` is stubbed with a scripted sequence so each test controls
exactly what the "model" drafts. No OpenAI client is constructed.
"""
import pytest

from personality.personality_engine import PersonalityEngine


class ScriptedLLM:
    def __init__(self, drafts):
        self.drafts = list(drafts)
        self.prompts = []

    async def __call__(self, message, context, user, prompt):
        self.prompts.append(prompt)
        return self.drafts.pop(0) if self.drafts else None


@pytest.fixture
def engine(monkeypatch):
    e = PersonalityEngine(memory_system=None, openai_api_key=None)
    e.repetition_guard_enabled = True
    e.repetition_guard.similarity_threshold = 0.45
    monkeypatch.setattr(e, "_should_respond", lambda message, is_mention: True)
    monkeypatch.setattr(e, "_should_speak", lambda message, is_mention: True)
    return e


async def generate(engine, llm, text="hey bot what do you think", mention=True):
    engine._generate_text = llm
    return await engine.generate_response(text, context={}, user="viewer", is_mention=mention)


FRESH = "the build is fine, the pilot is the problem"
SAME = "The build is fine; the pilot is the problem."
DIFFERENT = "your loadout should be tried at the hague"


async def test_fresh_draft_delivered_and_recorded(engine):
    llm = ScriptedLLM([FRESH])
    r = await generate(engine, llm)
    assert r["speech_text"] == FRESH
    assert engine.repetition_guard.history == [FRESH]
    assert len(llm.prompts) == 1
    assert engine.repetition_rejections == 0


async def test_repeat_triggers_one_regeneration_with_hint(engine):
    engine.repetition_guard.record(FRESH)
    llm = ScriptedLLM([SAME, DIFFERENT])
    r = await generate(engine, llm)
    assert r["speech_text"] == DIFFERENT
    assert len(llm.prompts) == 2
    assert "repeated yourself" not in llm.prompts[0]
    assert "repeated yourself" in llm.prompts[1]
    assert FRESH in llm.prompts[1], "hint quotes what it collided with"
    assert engine.repetition_rejections == 1
    assert engine.repetition_guard.history[-1] == DIFFERENT


async def test_unsolicited_reply_dropped_when_retry_also_repeats(engine):
    engine.repetition_guard.record(FRESH)
    llm = ScriptedLLM([SAME, SAME])
    r = await generate(engine, llm, text="lol", mention=False)
    assert r is None
    assert engine.repetition_forced == 0
    assert engine.repetition_guard.history == [FRESH], "nothing recorded"


async def test_mention_always_answers_with_least_bad_draft(engine):
    engine.repetition_guard.record(FRESH)
    less_bad = "the build is fine honestly, but chat, the pilot is doing crimes"
    llm = ScriptedLLM([SAME, less_bad])
    r = await generate(engine, llm, mention=True)
    assert r is not None
    assert r["speech_text"] == less_bad
    assert engine.repetition_forced == 1


async def test_mention_with_failed_retry_generation_falls_back_to_first_draft(engine):
    engine.repetition_guard.record(FRESH)
    llm = ScriptedLLM([SAME])  # retry returns None
    r = await generate(engine, llm, mention=True)
    assert r["speech_text"] == SAME
    assert engine.repetition_forced == 1


async def test_guard_disabled_never_retries(engine):
    engine.repetition_guard_enabled = False
    engine.repetition_guard.record(FRESH)
    llm = ScriptedLLM([SAME, DIFFERENT])
    r = await generate(engine, llm)
    assert r["speech_text"] == SAME
    assert len(llm.prompts) == 1


def test_session_summary_lands_first_in_knowledge_block(engine):
    ctx = {
        "session_summary": "cassova_ is on the final boss; viewer0 keeps calling it a skill issue.",
        "history_summary": "Seen once before",
        "engagement_level": "casual",
    }
    block = engine._format_context_knowledge(ctx, "viewer0")
    lines = block.splitlines()
    assert lines[0] == "What you know right now:"
    assert lines[1].startswith("- Earlier this stream: cassova_ is on the final boss")
    assert any(l.startswith("- History with viewer0") for l in lines)


def test_stats_expose_guard_counters(engine):
    st = engine.get_stats()
    assert st["repetition_rejections"] == 0
    assert st["repetition_forced"] == 0
