"""Summarize one or more spike JSONL runs into a markdown report.

Usage:
  python spike/summarize.py spike/runs/realtime_20260818_*.jsonl
"""
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import percentile


def load(paths):
    recs = []
    for pattern in paths:
        for p in sorted(glob.glob(pattern)):
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            r = json.loads(line)
                            r["_file"] = p
                            recs.append(r)
                        except json.JSONDecodeError:
                            pass
    return recs


def fmt(v):
    return f"{v:0.0f} ms" if isinstance(v, (int, float)) else "n/a"


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    recs = load(sys.argv[1:])
    if not recs:
        sys.exit("no records found")

    first_audio = [r["first_audio_ms"] for r in recs
                   if r.get("event") == "response_done" and r.get("first_audio_ms")]
    model_lat = [r["model_latency_ms"] for r in recs
                 if r.get("event") == "response_done" and r.get("model_latency_ms")]
    interrupts = [r for r in recs if r.get("event") == "interrupted"]
    silences = [r["onset_to_silence_ms"] for r in interrupts]
    coughs = [r for r in recs if r.get("event") == "false_start_ignored"]
    gaps = [r["gap_ms"] for r in recs if r.get("event") == "grace_window_speech_gap_ms"]
    costs = [r for r in recs if r.get("event") == "response_done" and
             (r.get("cost") or {}).get("estimated")]
    total_usd = sum(r["cost"]["usd"] for r in costs)
    audio_out_tok = sum(r["cost"].get("audio_out", 0) for r in costs)
    disconnects = [r for r in recs if r.get("event") == "disconnect"]
    dev_errors = [r for r in recs if "device_error" in r.get("event", "")
                  or r.get("event") in ("playback_reopen_failed",
                                        "playback_device_reopened", "mic_error")]
    unhandled = {}
    for r in recs:
        if r.get("event") == "session_summary":
            unhandled.update(r.get("unhandled_event_types", {}))
    api_errors = [r for r in recs if r.get("event") == "api_error"]
    transcripts = [(r.get("event"), (r.get("transcript") or "").strip())
                   for r in recs if r.get("event") in ("you_said", "arc_said")]

    print("# Spike run summary\n")
    print(f"- files: {sorted(set(r['_file'] for r in recs))}")
    print(f"- responses measured: {len(first_audio)}")
    print(f"- **first-audio latency** (turn end → audible): "
          f"p50 {fmt(percentile(first_audio, 50))}, "
          f"p95 {fmt(percentile(first_audio, 95))} "
          f"(targets: 800 / 1800)")
    print(f"- model-only latency (authorize → first delta): "
          f"p50 {fmt(percentile(model_lat, 50))}")
    print(f"- **interruptions**: {len(interrupts)}, onset→silence "
          f"p50 {fmt(percentile(silences, 50))} (incl. grace window)")
    print(f"- cough/false-starts correctly ignored: {len(coughs)} "
          f"(speech gaps seen in grace: {[round(g) for g in gaps]})")
    print(f"- **estimated cost**: ${total_usd:0.4f} across {len(costs)} responses "
          f"(audio-out tokens: {audio_out_tok})")
    print(f"- disconnects: {len(disconnects)}; device errors/reopens: {len(dev_errors)}")
    if api_errors:
        print(f"- API errors: {[ (r.get('error') or {}).get('code') or r.get('error') for r in api_errors ]}")
    if unhandled:
        print(f"- unhandled/renamed event types seen: {unhandled}")
    if transcripts:
        print("\n## Conversation\n")
        for who, text in transcripts:
            tag = "you" if who == "you_said" else "arc"
            if text:
                print(f"- **{tag}**: {text}")
    print("\n## Raw notable events\n")
    for r in recs:
        if r.get("event") in ("interrupted", "false_start_ignored",
                              "playback_device_error", "disconnect", "api_error"):
            print(f"- t+{r.get('t_mono_ms', 0) / 1000:0.1f}s  {r['event']}  "
                  + json.dumps({k: v for k, v in r.items()
                                if k not in ('t_wall', 't_mono_ms', 'event', '_file')},
                               default=str)[:200])


if __name__ == "__main__":
    main()
