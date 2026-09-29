"""Blind bake-off: the same real stream moments, several chat models, pick the best line.

Pulls recent moments out of talkbot.log (viewer mentions and vision-flagged
screen moments), asks each model for the co-host's line using the exact
system prompt and screen scenario the bot sends, and writes a local HTML page
where the lines are shown shuffled as A/B/C/D. Pick one per moment, then press
"reveal" to see which model won how often and its median reply time.

The endpoint is LLM_BASE_URL (or --base-url) with the key from LLM_API_KEY.
The key is only ever read from the environment (or .env), never from the
command line, and is never written to the output files.

Usage:
    python tools/model_bakeoff.py --log talkbot.log --models x-ai/grok-4.7,openai/gpt-5.5 --out ../bakeoff
    python tools/model_bakeoff.py --log talkbot.log --models a,b --out out --dry-run   # no network

Limits: mentions get the system prompt and the message only; the live bot
also adds viewer history and recent chat from its context builder, which a
log does not hold.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import random
import re
import statistics
import string
import sys
import tempfile
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

MENTION_RE = re.compile(r"Processing mention from (?P<user>[^:]+): '(?P<text>.*)'\s*$")
# The kind is optional: older logs wrote a bare "Screen [NOTABLE]:".
SCREEN_RE = re.compile(r"Screen \[NOTABLE(?: (?P<kind>[^\]]+))?\]: (?P<text>.*?)\s*$")

LLM_PARAMS = ('temperature', 'max_tokens', 'presence_penalty', 'frequency_penalty')


@dataclass
class Moment:
    source: str            # 'mention' or 'screen'
    text: str
    user: str = ''         # mentions: the viewer
    kind: str = ''         # screen: death, win, ...


# ------------------------------------------------------------------ moments

def extract_moments(log_path: Path, n: int = 20) -> List[Moment]:
    """Newest first, mentions and screen moments interleaved as they happened.
    A moment repeated word for word is kept once."""
    lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
    out: List[Moment] = []
    seen = set()
    for line in reversed(lines):
        m = MENTION_RE.search(line)
        if m:
            moment = Moment('mention', m.group('text').strip(), user=m.group('user').strip())
        else:
            m = SCREEN_RE.search(line)
            if not m:
                continue
            moment = Moment('screen', m.group('text').strip(),
                            kind=(m.group('kind') or 'other').strip())
        key = (moment.source, moment.text)
        if not moment.text or key in seen:
            continue
        seen.add(key)
        out.append(moment)
        if len(out) >= n:
            break
    return out


# ------------------------------------------------------------------- prompts

def screen_scenario(what_happened: str) -> str:
    """The first-of-a-kind scenario TalkBot._react_to_screen sends.
    tests/test_model_bakeoff.py holds this equal to the bot's own string."""
    return (
        f"Something just happened on stream: {what_happened}. React to it "
        f"in one line, as if you were watching. Do not narrate the screen "
        f"back; say the thing a co-host would say about it. If there is "
        f"nothing new worth saying, reply with exactly PASS."
    )


def screen_context(what_happened: str) -> Dict[str, Any]:
    """The context keys _react_to_screen sets that shape the prompt."""
    return {'on_screen': what_happened, 'screen_is_the_topic': True,
            'always_respond': True, 'may_pass': True}


@contextlib.contextmanager
def _in_repo():
    """The engine reads bot_settings.json and all_personalities.json from
    the working directory."""
    before = os.getcwd()
    os.chdir(ROOT)
    try:
        yield
    finally:
        os.chdir(before)


def build_engine(preset: str):
    """A PersonalityEngine set up the way the bot sets it up, with no client
    and a throwaway config dir so nothing in personality_settings is written."""
    from personality.personality_engine import PersonalityEngine
    with _in_repo():
        engine = PersonalityEngine(memory_system=None, openai_api_key=None,
                                   config_path=tempfile.mkdtemp(prefix="bakeoff-"))
        ok = asyncio.run(engine.switch_personality_by_name(preset))
    if not ok:
        raise SystemExit(f"unknown preset {preset!r}; see all_personalities.json")
    return engine


def build_request(engine, moment: Moment, model: str) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """Messages and params exactly as the engine would send them for this model."""
    from personality.model_params import adapt_params
    if moment.source == 'screen':
        message = screen_scenario(moment.text)
        context = screen_context(moment.text)
        user = os.getenv('TWITCH_CHANNEL') or engine.streamer_name or 'streamer'
    else:
        message, context, user = moment.text, {}, moment.user
    with _in_repo():
        engine._update_response_modifiers()
        prompt = engine._build_personality_prompt()
        messages = engine._build_messages(message, context, user, prompt)
    base = {p: engine.response_modifiers[p] for p in LLM_PARAMS if p in engine.response_modifiers}
    return messages, adapt_params(model, base)


# ------------------------------------------------------------------- calling

class FakeClient:
    """--dry-run: a client shaped like AsyncOpenAI that never touches the network."""

    class chat:
        class completions:
            @staticmethod
            async def create(model, messages, **params):
                await asyncio.sleep(0)
                return _Reply(f"[{model}] a line about: {messages[-1]['content'][:50]}")


class _Reply:
    def __init__(self, text):
        msg = type("M", (), {"content": text})()
        self.choices = [type("C", (), {"message": msg})()]


async def call_model(client, model: str, messages, params, timeout: float = 60.0
                     ) -> Tuple[str, Optional[float], Optional[str]]:
    """(line, seconds to the full reply, error)."""
    t0 = time.perf_counter()
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(model=model, messages=messages, **params),
            timeout=timeout)
        text = (resp.choices[0].message.content or '').strip()
        return text, time.perf_counter() - t0, None
    except Exception as e:  # noqa: BLE001 - one bad model must not end the run
        return '', None, f"{type(e).__name__}: {str(e)[:200]}"


# ------------------------------------------------------------------ shuffling

def shuffle_labels(models: List[str], rng: random.Random) -> Dict[str, str]:
    """letter -> model, in a fresh random order per moment."""
    order = list(models)
    rng.shuffle(order)
    return {string.ascii_uppercase[i]: m for i, m in enumerate(order)}


def tally(picks: Dict[int, str], mappings: List[Dict[str, str]], models: List[str]) -> Dict[str, int]:
    """Map picked letters back to models. picks: moment index -> letter."""
    counts = {m: 0 for m in models}
    for i, letter in picks.items():
        counts[mappings[i][letter]] += 1
    return counts


# --------------------------------------------------------------------- output

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Model bake-off</title>
<style>
:root { color-scheme: dark; --bg:#121316; --card:#1c1e23; --line:#2c2f36;
        --text:#e9e9ec; --dim:#a3a6ae; --accent:#8ab4ff; --win:#7ddc9a; }
body { background:var(--bg); color:var(--text); margin:0;
       font:20px/1.5 system-ui, "Segoe UI", sans-serif; }
main { max-width:900px; margin:0 auto; padding:24px 16px 80px; }
h1 { font-size:28px; margin:0 0 4px; }
.meta { color:var(--dim); font-size:16px; margin-bottom:24px; }
.moment { background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:16px 18px; margin:0 0 18px; }
.moment h2 { font-size:17px; color:var(--dim); font-weight:600; margin:0 0 6px; }
.what { margin:0 0 12px; }
label { display:flex; gap:12px; align-items:flex-start; padding:10px 8px;
        border-radius:8px; cursor:pointer; }
label:hover { background:#24272e; }
input[type=radio] { width:22px; height:22px; margin-top:4px; flex:none; }
.letter { font-weight:700; color:var(--accent); flex:none; width:1.4em; }
.err { color:#ff9b8a; font-style:italic; }
.who { display:none; color:var(--win); font-size:15px; margin-left:auto; flex:none; }
.revealed .who { display:inline; }
button { font:inherit; background:var(--accent); color:#0d1117; border:0;
         border-radius:8px; padding:10px 22px; cursor:pointer; }
table { border-collapse:collapse; margin-top:16px; width:100%; }
th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); }
#result { margin-top:12px; }
</style></head>
<body><main>
<h1>Model bake-off</h1>
<div class="meta" id="meta"></div>
<div id="moments"></div>
<button id="reveal" type="button">Reveal</button>
<div id="result"></div>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
const data = JSON.parse(document.getElementById('data').textContent);
document.getElementById('meta').textContent =
  data.moments.length + ' moments, ' + data.models.length + ' models, preset ' + data.preset +
  '. Pick the best line for each, then reveal.';
const root = document.getElementById('moments');
data.moments.forEach((m, i) => {
  const box = document.createElement('section'); box.className = 'moment';
  const h = document.createElement('h2');
  h.textContent = (i + 1) + '. ' + (m.source === 'screen' ? 'Screen (' + m.kind + ')' : 'Viewer ' + m.user);
  const w = document.createElement('p'); w.className = 'what'; w.textContent = m.text;
  box.append(h, w);
  Object.keys(m.labels).sort().forEach(letter => {
    const lab = document.createElement('label');
    const r = document.createElement('input'); r.type = 'radio'; r.name = 'm' + i; r.value = letter;
    const l = document.createElement('span'); l.className = 'letter'; l.textContent = letter;
    const t = document.createElement('span');
    const reply = m.replies[letter];
    if (reply.error) { t.className = 'err'; t.textContent = 'error: ' + reply.error; }
    else { t.textContent = reply.line || '(empty)'; }
    const who = document.createElement('span'); who.className = 'who';
    who.textContent = m.labels[letter] + (reply.seconds != null ? ' ' + reply.seconds.toFixed(1) + 's' : '');
    lab.append(r, l, t, who); box.append(lab);
  });
  root.append(box);
});
document.getElementById('reveal').addEventListener('click', () => {
  const counts = {}; data.models.forEach(x => counts[x] = 0);
  let picked = 0;
  data.moments.forEach((m, i) => {
    const r = document.querySelector('input[name="m' + i + '"]:checked');
    if (r) { counts[m.labels[r.value]] += 1; picked += 1; }
  });
  document.body.classList.add('revealed');
  const res = document.getElementById('result');
  res.innerHTML = '<p>' + picked + ' of ' + data.moments.length + ' moments picked.</p>' +
    '<table><tr><th>Model</th><th>Picks</th><th>Median reply time</th></tr></table>';
  const table = res.querySelector('table');
  data.models.slice().sort((a, b) => counts[b] - counts[a]).forEach(x => {
    const med = data.median_seconds[x];
    const tr = table.insertRow();
    tr.insertCell().textContent = x;
    tr.insertCell().textContent = counts[x];
    tr.insertCell().textContent = med == null ? '-' : med.toFixed(2) + ' s';
  });
});
</script>
</body></html>
"""


def _median_seconds(results: Dict[str, List[Optional[float]]]) -> Dict[str, Optional[float]]:
    return {m: (round(statistics.median([s for s in xs if s is not None]), 3)
                if any(s is not None for s in xs) else None)
            for m, xs in results.items()}


def write_outputs(out_dir: Path, stamp: str, payload: Dict[str, Any]) -> Tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"bakeoff-{stamp}.html"
    json_path = out_dir / f"bakeoff-{stamp}.json"
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html_path.write_text(PAGE.replace("__DATA__", blob), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return html_path, json_path


# ------------------------------------------------------------------------ run

async def run(moments: List[Moment], models: List[str], engine, client,
              seed: Optional[int] = None) -> Dict[str, Any]:
    rng = random.Random(seed)
    latencies: Dict[str, List[Optional[float]]] = {m: [] for m in models}
    rows = []
    for i, moment in enumerate(moments):
        labels = shuffle_labels(models, rng)
        replies = {}
        for letter, model in labels.items():
            messages, params = build_request(engine, moment, model)
            line, seconds, error = await call_model(client, model, messages, params)
            latencies[model].append(seconds)
            replies[letter] = {'line': line, 'seconds': seconds, 'error': error}
            print(f"  {i + 1}/{len(moments)} {model}: "
                  f"{'%.2fs' % seconds if seconds is not None else error}", file=sys.stderr)
        rows.append({**asdict(moment), 'labels': labels, 'replies': replies})
    return {'models': models, 'moments': rows, 'median_seconds': _median_seconds(latencies),
            'latencies': latencies}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--log", required=True, help="path to talkbot.log")
    ap.add_argument("--models", required=True, help="comma-separated model ids")
    ap.add_argument("--out", required=True, help="directory for the html and json")
    ap.add_argument("-n", "--moments", type=int, default=20)
    ap.add_argument("--preset", default="uwu")
    ap.add_argument("--base-url", default=None, help="defaults to LLM_BASE_URL")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true", help="fake client, no network")
    args = ap.parse_args(argv)

    log_path = Path(args.log).resolve()
    out_dir = Path(args.out).resolve()
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models or len(models) > 26:
        ap.error("give between 1 and 26 models")

    moments = extract_moments(log_path, args.moments)
    if not moments:
        print(f"no mentions or screen moments found in {log_path}", file=sys.stderr)
        return 1

    base_url = args.base_url
    if args.dry_run:
        client = FakeClient()
    else:
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
        except ImportError:
            pass
        api_key = os.getenv("LLM_API_KEY")
        base_url = (base_url or os.getenv("LLM_BASE_URL") or "").strip() or None
        if not api_key:
            print("LLM_API_KEY is not set (env or .env)", file=sys.stderr)
            return 2
        if not base_url:
            print("no base URL: pass --base-url or set LLM_BASE_URL", file=sys.stderr)
            return 2
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    engine = build_engine(args.preset)
    payload = asyncio.run(run(moments, models, engine, client, seed=args.seed))
    payload.update({'preset': args.preset, 'log': str(log_path), 'dry_run': args.dry_run,
                    'base_url': base_url or '(default)',
                    'created': datetime.now().isoformat(timespec='seconds')})
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    html_path, json_path = write_outputs(out_dir, stamp, payload)
    print(f"wrote {html_path}\nwrote {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
