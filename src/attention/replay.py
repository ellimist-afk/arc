"""Replay harness: timestamped JSONL stimuli in → JSONL decisions out.

Input record shape (one per line; unknown keys ignored):
  {"t": 12.5, "type": "speech_ended", "id": "s1", "source": "streamer_voice",
   "actor": {"username": "streamer", "roles": ["streamer"]},
   "trust": "trusted", "channel": "", "payload": {"text": "hey"}}

Special record: {"t": 30.0, "type": "poll"} forces a timer poll at t (the
harness also polls automatically at every event's timestamp, so explicit
polls are only needed to observe expiries between events).

Usage:
  python -m attention.replay fixture.jsonl [--config config.json] > out.jsonl
  (run from src/, or with src/ on PYTHONPATH — same convention as tests/)
"""
import argparse
import json
import sys
from typing import Any, Dict, Iterator, List, TextIO

from attention.config import AttentionConfig
from attention.router import AttentionRouter
from attention.stimulus import (Actor, Source, Stimulus, StimulusType, Trust)


def stimulus_from_record(rec: Dict[str, Any], seq: int) -> Stimulus:
    actor_d = rec.get("actor", {}) or {}
    return Stimulus(
        id=str(rec.get("id", f"replay_{seq}")),
        type=StimulusType(rec["type"]),
        source=Source(rec.get("source", "internal")),
        actor=Actor(user_id=str(actor_d.get("user_id", "")),
                    username=str(actor_d.get("username", "")),
                    roles=tuple(actor_d.get("roles", ()))),
        ts=float(rec.get("ts", rec["t"])),
        channel=str(rec.get("channel", "")),
        trust=Trust(rec.get("trust", "untrusted")),
        payload=rec.get("payload", {}) or {},
    )


def replay(records: Iterator[Dict[str, Any]],
           config: AttentionConfig) -> Iterator[Dict[str, Any]]:
    """Deterministic: same records + config → same decision stream."""
    router = AttentionRouter(config)
    seq = 0

    def flatten(decision_dict):
        """Emit the decision, then any boundary-released decisions it carries
        as their own records (annotated with the releasing stimulus)."""
        children = decision_dict.pop("released", [])
        decision_dict["released_count"] = len(children)
        yield decision_dict
        for c in children:
            c.pop("released", None)
            c["released_by"] = decision_dict["stimulus_id"]
            yield c

    for rec in records:
        now = float(rec["t"])
        if rec.get("type") == "poll":
            for d in router.poll(now):
                yield from flatten(d.to_dict())
            continue
        seq += 1
        stim = stimulus_from_record(rec, seq)
        yield from flatten(router.handle(stim, now).to_dict())
        for d in router.poll(now):
            yield from flatten(d.to_dict())


def read_jsonl(fh: TextIO) -> List[Dict[str, Any]]:
    out = []
    for line in fh:
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(json.loads(line))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("fixture", help="JSONL stimulus file ('-' for stdin)")
    ap.add_argument("--config", help="JSON file of AttentionConfig overrides")
    args = ap.parse_args()

    config = AttentionConfig()
    if args.config:
        with open(args.config, encoding="utf-8") as fh:
            config = AttentionConfig.from_dict(json.load(fh))

    fh = sys.stdin if args.fixture == "-" else open(args.fixture, encoding="utf-8")
    try:
        records = read_jsonl(fh)
    finally:
        if fh is not sys.stdin:
            fh.close()
    records.sort(key=lambda r: float(r["t"]))
    try:
        for decision in replay(iter(records), config):
            print(json.dumps(decision, ensure_ascii=False))
    except BrokenPipeError:      # e.g. piped into head
        sys.stderr.close()


if __name__ == "__main__":
    main()
