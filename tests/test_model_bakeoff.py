"""tools/model_bakeoff.py: a blind pick between chat models on real moments.

It is only worth anything if every model gets exactly what the live bot would
send -- same system prompt, same screen scenario -- and if the page cannot
leak which model wrote which line before the reveal. The key must never land
in the files it writes.
"""
import asyncio
import importlib.util
import json
import random
import sys
import tempfile
from pathlib import Path

from personality.personality_engine import PersonalityEngine
from test_screen_variety import _bot as _screen_bot

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("model_bakeoff", ROOT / "tools" / "model_bakeoff.py")
bakeoff = importlib.util.module_from_spec(_spec)
sys.modules["model_bakeoff"] = bakeoff      # dataclasses look the module up
_spec.loader.exec_module(bakeoff)

LOG = """\
2026-09-20 20:00:00,001 - src.bot.bot - INFO - Processing mention from alice: '@elimist_ first one'
2026-09-20 20:01:00,001 - features.screen_watcher - INFO - Screen [NOTABLE]: Overwatch 2, a kill cam.
2026-09-20 20:02:00,001 - src.bot.bot - INFO - Something unrelated happened
2026-09-20 20:03:00,001 - features.screen_watcher - INFO - Screen [NOTABLE death]: Cassidy died to Widowmaker.
2026-09-20 20:04:00,001 - src.bot.bot - INFO - Processing mention from bob_2: '@elimist_ it's "quoted": yes'
2026-09-20 20:05:00,001 - features.screen_watcher - INFO - Screen [NOTABLE death]: Cassidy died to Widowmaker.
"""


def _log(tmp_path):
    p = tmp_path / "talkbot.log"
    p.write_text(LOG, encoding="utf-8")
    return p


# ------------------------------------------------------------------ extraction

def test_extracts_mentions_and_screen_moments_newest_first(tmp_path):
    ms = bakeoff.extract_moments(_log(tmp_path), 20)
    assert [(m.source, m.text) for m in ms] == [
        ('screen', 'Cassidy died to Widowmaker.'),
        ('mention', '@elimist_ it\'s "quoted": yes'),
        ('screen', 'Overwatch 2, a kill cam.'),
        ('mention', '@elimist_ first one'),
    ], "newest first, a word-for-word repeat kept once"
    assert ms[0].kind == 'death' and ms[2].kind == 'other'
    assert ms[1].user == 'bob_2'


def test_extraction_stops_at_n(tmp_path):
    assert len(bakeoff.extract_moments(_log(tmp_path), 2)) == 2


# ------------------------------------------------------------------ parity

async def test_screen_scenario_is_the_bots_own_string():
    bot = _screen_bot()
    await bot._react_to_screen("cassidy hit the floor", "death", 1)
    assert bot.seen['message'] == bakeoff.screen_scenario("cassidy hit the floor")
    for key, value in bakeoff.screen_context("cassidy hit the floor").items():
        assert bot.seen['context'][key] == value


def _live_engine_request(message, context, user, is_mention):
    """What the engine really sends to its client, captured."""
    captured = {}

    class Client:
        class chat:
            class completions:
                @staticmethod
                async def create(**kw):
                    captured.update(kw)
                    return bakeoff._Reply("hey there")

    e = PersonalityEngine(memory_system=None, openai_api_key=None,
                          config_path=tempfile.mkdtemp())
    asyncio.run(e.switch_personality_by_name('uwu'))
    e.openai_client = Client()
    asyncio.run(e.generate_response(message=message, context=context, user=user,
                                    is_mention=is_mention))
    return captured


def test_mention_prompt_matches_the_engine():
    moment = bakeoff.Moment('mention', '@elimist_ how is the run going', user='alice')
    live = _live_engine_request(moment.text, {}, 'alice', True)
    engine = bakeoff.build_engine('uwu')
    messages, params = bakeoff.build_request(engine, moment, live['model'])
    assert messages == live['messages']
    assert messages[0]['content'] == live['messages'][0]['content']
    assert params == {k: v for k, v in live.items() if k not in ('model', 'messages')}


def test_screen_prompt_matches_the_engine(monkeypatch):
    monkeypatch.delenv('TWITCH_CHANNEL', raising=False)
    engine = bakeoff.build_engine('uwu')
    what = "Cassidy died to Widowmaker."
    live = _live_engine_request(bakeoff.screen_scenario(what), bakeoff.screen_context(what),
                                engine.streamer_name, False)
    messages, _ = bakeoff.build_request(engine, bakeoff.Moment('screen', what, kind='death'),
                                        live['model'])
    assert messages == live['messages']


def test_each_model_gets_params_its_endpoint_accepts():
    engine = bakeoff.build_engine('uwu')
    m = bakeoff.Moment('mention', 'hi', user='a')
    _, grok = bakeoff.build_request(engine, m, 'x-ai/grok-4.7')
    _, gpt = bakeoff.build_request(engine, m, 'openai/gpt-5.5')
    assert 'max_tokens' in grok and 'reasoning_effort' not in grok
    assert 'presence_penalty' not in grok
    assert 'max_completion_tokens' in gpt and gpt['reasoning_effort'] == 'none'


# ------------------------------------------------------------------ shuffling

def test_shuffle_mapping_round_trips():
    models = ['x-ai/grok-4.7', 'openai/gpt-5.5', 'google/gemini-3-flash', 'meta/llama']
    rng = random.Random(7)
    mappings = [bakeoff.shuffle_labels(models, rng) for _ in range(30)]
    for mp in mappings:
        assert sorted(mp) == ['A', 'B', 'C', 'D'] and sorted(mp.values()) == sorted(models)
    assert len({tuple(mp.values()) for mp in mappings}) > 1, "order must actually vary"
    inverse = [{v: k for k, v in mp.items()} for mp in mappings]
    picks = {i: inverse[i]['openai/gpt-5.5'] for i in range(30)}
    assert bakeoff.tally(picks, mappings, models) == {
        'x-ai/grok-4.7': 0, 'openai/gpt-5.5': 30, 'google/gemini-3-flash': 0, 'meta/llama': 0}


# ------------------------------------------------------------------ dry run

def test_dry_run_writes_both_files_and_never_the_key(tmp_path, monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', 'sk-or-v1-SECRET-bakeoff-key')
    out = tmp_path / "out"
    rc = bakeoff.main(['--log', str(_log(tmp_path)), '--models', 'x-ai/grok-4.7,openai/gpt-5.5',
                       '--out', str(out), '--dry-run', '--seed', '1'])
    assert rc == 0
    htmls, jsons = list(out.glob("bakeoff-*.html")), list(out.glob("bakeoff-*.json"))
    assert len(htmls) == 1 and len(jsons) == 1
    page, raw = htmls[0].read_text(encoding="utf-8"), jsons[0].read_text(encoding="utf-8")
    for text in (page, raw):
        assert 'SECRET-bakeoff-key' not in text
    data = json.loads(raw)
    assert len(data['moments']) == 4
    for m in data['moments']:
        assert set(m['labels'].values()) == {'x-ai/grok-4.7', 'openai/gpt-5.5'}
        for letter, model in m['labels'].items():
            assert m['replies'][letter]['line'].startswith(f"[{model}]"), "mapping must match the line"
    assert set(data['median_seconds']) == {'x-ai/grok-4.7', 'openai/gpt-5.5'}
    assert 'Reveal' in page and 'type="radio"' not in page.split('<script')[0], \
        "lines are rendered client-side from the data block"
    assert 'http' not in page.split('<script id="data"')[0].replace('http-equiv', ''), \
        "the page loads nothing from the network"


def test_exits_nonzero_without_a_base_url(tmp_path, monkeypatch):
    """No --base-url and no LLM_BASE_URL must fail before any client is built,
    never falling through to the OpenAI SDK's default endpoint."""
    monkeypatch.setenv('LLM_API_KEY', 'sk-or-v1-SECRET-bakeoff-key')
    monkeypatch.delenv('LLM_BASE_URL', raising=False)
    # The real .env sets LLM_BASE_URL since chat moved to OpenRouter; keep it out.
    monkeypatch.setattr('dotenv.load_dotenv', lambda *a, **k: False)
    out = tmp_path / "out"
    rc = bakeoff.main(['--log', str(_log(tmp_path)), '--models', 'x-ai/grok-4.7',
                       '--out', str(out)])
    assert rc != 0
    assert not list(out.glob("bakeoff-*"))


def test_whitespace_only_llm_base_url_env_counts_as_empty(tmp_path, monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', 'sk-or-v1-SECRET-bakeoff-key')
    monkeypatch.setenv('LLM_BASE_URL', '   ')
    out = tmp_path / "out"
    rc = bakeoff.main(['--log', str(_log(tmp_path)), '--models', 'x-ai/grok-4.7',
                       '--out', str(out)])
    assert rc != 0


def test_the_key_never_comes_from_argv():
    import argparse
    src = (ROOT / "tools" / "model_bakeoff.py").read_text(encoding="utf-8")
    assert '--api-key' not in src and '--key' not in src
