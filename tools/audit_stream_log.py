"""Post-stream audit of talkbot.log: which guards fired, and what they stopped.

The co-host's misbehavior guards each log a distinctive line when they act
(a dropped filler, a held reply, a rejected rerun). After a stream, this
reads the log and turns those into a report -- "the topic check saved you 3
reruns" -- instead of hunting through chat screenshots.

Usage:
    python tools/audit_stream_log.py                  # last session in talkbot.log
    python tools/audit_stream_log.py --all            # every session
    python tools/audit_stream_log.py path/to.log
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - "
    r"(?P<name>\S+) - (?P<level>[A-Z]+) - (?P<msg>.*)$"
)
SESSION_START = "Starting TalkBot setup..."

# Each guard: (key, substring that identifies its log line, human meaning)
GUARDS = [
    ("dead_air_abort", "Dead air ended while the line was being generated",
     "Fillers dropped because the room restarted mid-generation"),
    ("dead_air_quiet", "Dead air: no line worth sending; staying quiet",
     "Lulls where the co-host chose silence over a weak line"),
    ("breather_hold", "Holding follow-up reply",
     "Follow-up replies held so lines don't stack"),
    ("breather_hold_unsolicited", "Holding unsolicited reply",
     "Unprompted replies held so lines don't stack"),
    ("freshness_drop", "the room has moved on -- dropping",
     "Late replies dropped instead of answering a stale message"),
    ("rerun_reject", "re-told topic:",
     "Reruns of the co-host's own recent bit rejected"),
    ("tic_reject", "hot phrases:",
     "Catchphrase/tic drafts rejected"),
    ("repetition_skip", "retry also repetitive",
     "Lines skipped entirely after the retry repeated too"),
    ("unsolicited_dropped", "dropping instead of regenerating",
     "Optional lines dropped on first rejection (saved a second LLM call)"),
    ("repetition_forced", "both drafts repetitive, delivering least-bad",
     "Required replies delivered despite repetition (mentions)"),
    ("followup_upgrade", "treated as a mention",
     "Name-free follow-ups answered as direct addresses"),
    ("streak_spent", "Follow-up streak spent",
     "Ping-pong caps hit (viewer must use the name again)"),
    ("eventsub_blind", "EventSub blind for",
     "Windows where follow/sub/cheer events could not reach the bot"),
    ("reconnect_rejected", "reconnect handoff REJECTED",
     "Twitch refused a reconnect handoff (we were too slow -- check loop stalls)"),
    ("screen_look", "Screen: ",
     "Looks at the screen that produced a fresh description"),
    ("screen_offline", "Stream is offline; not looking",
     "Vision looks skipped because the stream was down"),
    ("muted", "Muted; not",
     "Lines withheld because the co-host was muted"),
    ("persona_switch", "Persona switched to",
     "Persona changes requested from chat"),
    ("voice_drop", "Dropped low-confidence transcript",
     "Garbled mic audio discarded instead of answered"),
    ("ad_fallback", "Ad line timed out",
     "Ad lines that fell back to the template pool (timeout)"),
    ("ad_failed", "Ad line failed",
     "Ad lines that fell back to the template pool (error)"),
]
# Not guards, but context that sizes the session.
ACTIVITY = [
    ("replies", "TTS Decision:", "Replies delivered"),
    ("fillers_sent", "Dead air detected (", "Dead-air fillers actually sent"),
    ("rejections_total", "Repetition guard rejected draft",
     "Drafts sent back for regeneration (all reasons)"),
]
MAX_EXAMPLES = 3


@dataclass
class Session:
    start: str
    end: str = ""
    counts: Counter = field(default_factory=Counter)
    examples: dict = field(default_factory=dict)
    warnings: Counter = field(default_factory=Counter)

    def note(self, key: str, ts: str, msg: str) -> None:
        self.counts[key] += 1
        self.examples.setdefault(key, [])
        if len(self.examples[key]) < MAX_EXAMPLES:
            self.examples[key].append(f"{ts}  {msg.strip()[:110]}")


def parse_sessions(text: str) -> List[Session]:
    """Split the log into bot runs and tally guard activity in each."""
    sessions: List[Session] = []
    current: Optional[Session] = None
    for raw in text.splitlines():
        m = LINE_RE.match(raw)
        if not m:                       # traceback / continuation line
            continue
        ts, level, msg = m["ts"], m["level"], m["msg"]
        if SESSION_START in msg:
            current = Session(start=ts)
            sessions.append(current)
            continue
        if current is None:             # log begins mid-session
            current = Session(start=f"(before log start, first line {ts})")
            sessions.append(current)
        current.end = ts
        for key, needle, _ in GUARDS + ACTIVITY:
            if needle in msg:
                current.note(key, ts, msg)
        if level in ("WARNING", "ERROR"):
            # Collapse per-incident details so identical problems group.
            current.warnings[re.sub(r"\d+", "N", msg)[:90]] += 1
    return sessions


def render(session: Session) -> str:
    out = [f"Session {session.start}  ->  {session.end or '(no further lines)'}", ""]
    out.append("Activity")
    for key, _, label in ACTIVITY:
        out.append(f"  {label}: {session.counts.get(key, 0)}")
    out.append("")
    fired = [(k, n, lbl) for k, n, lbl in
             ((k, session.counts.get(k, 0), lbl) for k, _, lbl in GUARDS) if n]
    quiet = [lbl for k, _, lbl in GUARDS if not session.counts.get(k)]
    out.append("Guards that fired")
    if not fired:
        out.append("  (none -- nothing to suppress, or this run predates the fixes)")
    for key, n, label in fired:
        out.append(f"  {label}: {n}")
        for ex in session.examples.get(key, []):
            out.append(f"      {ex}")
    if quiet:
        out.append("")
        out.append("Guards that never fired (nothing to suppress, or code not live)")
        for label in quiet:
            out.append(f"  - {label}")
    if session.warnings:
        out.append("")
        out.append("Top warnings/errors")
        for msg, n in session.warnings.most_common(5):
            out.append(f"  {n:>4}x  {msg}")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("logfile", nargs="?", default="talkbot.log")
    ap.add_argument("--all", action="store_true",
                    help="report every session, not just the last")
    args = ap.parse_args(argv)

    path = Path(args.logfile)
    if not path.exists():
        print(f"No log at {path}", file=sys.stderr)
        return 1
    sessions = parse_sessions(path.read_text(encoding="utf-8", errors="replace"))
    if not sessions:
        print("No sessions found in the log.", file=sys.stderr)
        return 1
    chosen = sessions if args.all else sessions[-1:]
    print(f"{len(sessions)} session(s) in {path}; showing {len(chosen)}.\n")
    print("\n\n".join(render(s) for s in chosen))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
