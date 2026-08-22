# Phase 1 Realtime Spike

Isolated probes for the Realtime redesign (`docs/ARC_REALTIME_REDESIGN.md` §21).
**Nothing here touches the bot** — no imports from `src/`, no schema changes,
separate requirements file. Delete the `spike/` folder and Arc is untouched.

## Setup (on the streaming PC, Windows)

```powershell
cd <repo root>
python -m pip install -r spike/requirements.txt
# OPENAI_API_KEY must be in the environment or in the repo-root .env
```

API facts verified 2026-08-18 against developers.openai.com: endpoint
`wss://api.openai.com/v1/realtime?model=...`, no beta header, models
`gpt-realtime-2.1` / `gpt-realtime-2.1-mini`, GA event names
(`response.output_audio.delta`, `conversation.item.truncate{item_id,
content_index, audio_end_ms}`). `gpt-realtime-2.1` limits: 60-minute
sessions, 128k-token context window, 32k maximum output tokens.

## Step 1 — audio_probe.py (no OpenAI traffic, run this first)

```powershell
python spike/audio_probe.py --list
```

Pick the **mic-carrying Voicemeeter output** as `--input` and the **dedicated
Arc strip** as `--output` (unique name substring, or `idx:N`). Prefer WASAPI
entries over MME duplicates. There are deliberately no defaults — ambiguous or
missing devices abort with the candidate list.

```powershell
python spike/audio_probe.py --input "<mic device>" --output "<arc strip>" --all
```

Runs, in order: **capture** (5 s with live meter — open `spike/runs/capture_check.wav`
and confirm it's your voice, not desktop audio), **playback** (played-ms
accounting vs wall clock), **hardstop** (stop-request → silence latency),
**echotest** (chirp on output while metering input — **must PASS before
Step 2**; a FAIL means Arc would hear itself: fix the Voicemeeter bus so the
Arc strip doesn't feed the capture path). Then optionally:

```powershell
python spike/audio_probe.py --output "<arc strip>" --test failure
```

and disable the device mid-tone to see how failure surfaces.

## Step 2 — realtime_probe.py

Guided 10-turn scenario (recommended):

```powershell
python spike/realtime_probe.py --input "<mic device>" --output "<arc strip>" --scripted
```

Free-form with manual authorization (closest to the AttentionRouter design):

```powershell
python spike/realtime_probe.py --input "<mic device>" --output "<arc strip>"
```

Keys: `a` arm (flushes the pre-roll, starts streaming — do this first),
`p` back to passive, `SPACE` authorize a response (manual mode), `n` next
scripted step, `q` quit + summary.

What's deliberately configured: server VAD **on** but `create_response` and
`interrupt_response` **off** — every response is authorized locally
(keypress, or on-turn-end in `--scripted`). Mic **pre-roll** default 2000 ms
(`--preroll-ms`) is flushed on arming; while passive nothing is sent anywhere.
**Barge-in grace** default 250 ms (`--grace-ms`): speech that ends inside the
grace window is a cough — playback continues; sustained speech stops playback
(hard stop), sends `response.cancel`, and truncates the item at measured
played-ms. Note: the reported onset→silence latency *includes* the grace
window by design; run once with `--grace-ms 0` to measure the raw stop path.
If coughs still cancel playback, check the `grace_window_speech_gap_ms`
entries in the log and raise `--grace-ms` — server VAD adds trailing silence
before `speech_stopped`, so the sweet spot may be 400–800 ms (that tuning is
finding U5).

Useful flags: `--model gpt-realtime-2.1` (A/B against mini, finding U2),
`--vad server_vad` (A/B against semantic, U6), `--voice`, `--max-minutes`,
`--instructions <file>`.

## Step 3 — summarize

```powershell
python spike/summarize.py spike/runs/realtime_*.jsonl
```

Prints p50/p95 first-audio, interrupt→silence, cough handling, estimated cost
(from real `usage` blocks — finding U4), disconnects, device errors, any
unexpected API event names, and the conversation transcript. Send the
`spike/runs/*.jsonl` files back for analysis.

## Wiring smoke test (no hardware, no API key — works anywhere)

```powershell
python spike/fake_realtime_server.py            # terminal 1
python spike/realtime_probe.py --no-audio --auto-authorize --url ws://localhost:8787 --max-minutes 0.5   # terminal 2
```

Exercises **the complete local barge-in path** (hard stop → `response.cancel`
→ `conversation.item.truncate` at measured played-ms) plus one injected cough.
Genuine end-to-end barge-in remains unverified until this runs with physical
audio hardware against the real OpenAI API.

## What each turn of the scripted scenario is probing

1–2 normal turns (baseline latency) · 3–4 fast follow-ups (turn-detection
lag) · 5–6 real interruptions (cancel + truncate) · 7 cough (grace window) ·
8 truncation truth ("what number did you reach?" — Arc must not claim numbers
you never heard) · 9 output-device failure (error surfacing + reopen) ·
10 clean close.
