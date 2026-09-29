"""Periodic look at the screen, so the co-host knows what is being played.

Without this the bot can only ever riff on chat: it has no idea the streamer
just died to the same boss for the ninth time. A background loop captures the
screen every `interval_s`, asks a vision model for one factual sentence, and
caches it. `describe()` returns that cached line and NEVER blocks -- a reply
must not wait on a screenshot.

PRIVACY: this sends periodic screenshots of the configured display to the
OpenAI API. It stays off unless `screen_awareness.enabled` is true, and it
should point at the game display, not one holding DMs or credentials.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

LOOK_PROMPT = (
    "You are looking at a live stream's screen. In ONE short factual "
    "sentence: what game or application is this, and what is happening right "
    "now? Name what you can actually see -- a menu, a death screen, a score, "
    "a boss, a code editor. Never speculate and never invent numbers."
)

# Pixel differences cannot tell a camera pan from a death screen: during
# gameplay every frame differs. So the model, which can already see the
# screen, is asked to judge whether anything actually HAPPENED since the
# last look -- it costs nothing extra on a call being made anyway.
COMPARE_SUFFIX = (
    "\n\nLast time you looked, you saw: \"{previous}\"\n"
    "If something genuinely happened since then -- a death, a win, a boss "
    "appearing, a run ending, a crash, a dramatic swing -- begin your reply "
    "with NOTABLE[kind]: where kind is ONE word from death, win, loss, "
    "highlight, boss, crash, other -- followed by the one sentence. "
    "Routine movement, camera "
    "pans, menus, ordinary play and scrolling are NOT notable; in that case "
    "reply with the sentence alone and no prefix. "
    "Menus, lobbies, hero select, queues and loading screens are never notable."
)
NOTABLE_PREFIX = "NOTABLE:"
# The kind is what makes a rerun visible: five deaths in a row are five
# moments to a pixel differ and one story to a co-host. A bare NOTABLE:
# still parses (kind "other") -- the model drops the bracket often enough
# that rejecting it would cost real moments.
NOTABLE_RE = re.compile(r"^NOTABLE(?:\[([^\]]{0,24})\])?:\s*", re.I)
# However slowly we poll, a description older than this is history.
MAX_DESCRIPTION_AGE_S = 300.0
# Kinds that are never a moment however confidently the model flags them.
# Live 2026-09-06 two hero-select screens got co-host lines, because naming
# "lobby" in the vocabulary invited the model to flag a menu as NOTABLE
# against the same prompt's "menus are NOT notable". A menu is the absence
# of something happening; it can never be the thing worth interrupting for.
NEVER_MOMENTS = frozenset({'lobby', 'menu', 'loading', 'queue', 'hero select'})


@dataclass
class _Run:
    """One run of same-kind moments. Runs are PER KIND: Overwatch
    interleaves death, lobby, death, highlight, so a single shared run was
    reset by every interruption and every death spoke as if it were the
    first."""
    streak: int = 0
    said_at_streak: int = 0
    last_at: float = 0.0        # monotonic
    started_at: float = 0.0     # monotonic


def _number(value: Any, default: float) -> float:
    """A hand-edited JSON setting is a string or a typo as often as a number.
    A bad one must fall back, not raise -- an exception here disables vision
    entirely and the only trace is one warning at startup."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        if value is not None:
            logger.warning(f"Screen awareness: {value!r} is not a number; using {default}")
        return default
    return out


def _bbox(raw: Any) -> Optional[Tuple[int, int, int, int]]:
    """Validate a capture region. A malformed one is dropped, not passed on:
    ImageGrab raises on it, and that failure would be a silent debug line
    every interval forever."""
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        if raw is not None:
            logger.warning(f"Screen awareness: ignoring malformed bbox {raw!r}")
        return None
    try:
        left, top, right, bottom = (int(v) for v in raw)
    except (TypeError, ValueError):
        logger.warning(f"Screen awareness: bbox {raw!r} is not four numbers; ignoring")
        return None
    if right <= left or bottom <= top:
        logger.warning(f"Screen awareness: bbox {raw!r} has no area; ignoring")
        return None
    return (left, top, right, bottom)


class ScreenWatcher:
    """Keeps a recent one-line description of what is on screen."""

    def __init__(
        self,
        openai_client: Any = None,
        model: str = "gpt-5.5",
        interval_s: float = 60.0,
        enabled: bool = False,
        bbox: Optional[Tuple[int, int, int, int]] = None,
        max_edge: int = 768,
        timeout_s: float = 12.0,
        change_threshold: float = 6.0,
        scene_threshold: float = 18.0,
        notable_cooldown_s: float = 240.0,
        same_kind_window_s: float = 600.0,
        streak_threshold: int = 3,
    ):
        self.openai_client = openai_client
        self.model = model
        self.interval_s = max(15.0, float(interval_s))   # a floor: this costs money
        self.enabled = enabled
        self.bbox = bbox
        self.max_edge = max_edge
        self.timeout_s = timeout_s
        # Below this mean pixel difference the screen counts as unchanged and
        # the look is free; above scene_threshold it is a different scene.
        self.change_threshold = change_threshold
        self.scene_threshold = scene_threshold
        # Least time between proactive reactions. Without it a chaotic game
        # would have the co-host narrating every fight.
        self.notable_cooldown_s = notable_cooldown_s
        # How long two moments of the same kind count as one run, and how
        # many unsaid repeats of that kind buy the next line. Without them
        # the co-host writes a fresh joke about every death forever.
        self.same_kind_window_s = same_kind_window_s
        self.streak_threshold = max(1, int(streak_threshold))
        # Async callable invoked as on_notable(what_happened, kind, streak).
        # Set by the bot.
        self.on_notable = None
        # Returns False when looking is pointless (stream offline). Set by the
        # bot; without it every look proceeds. An offline stream would
        # otherwise keep paying for screenshots and react into an empty room.
        self.should_look = None

        self._description: Optional[str] = None
        self._seen_at: float = 0.0
        self._signature: Optional[bytes] = None
        self._scene_since: float = 0.0
        self._task = None
        self._last_notable_at: float = 0.0
        # One run per kind. A death run keeps counting across an interleaved
        # highlight or menu; only its own window ends it.
        self._runs: Dict[str, _Run] = {}
        self.looks = 0
        self.failures = 0
        self.unchanged_skips = 0
        self.offline_skips = 0
        self.notables = 0
        self.notables_suppressed = 0
        self.notables_held_same_kind = 0
        self.notables_held_menu = 0

    # ------------------------------------------------------------- config

    @classmethod
    def from_settings(cls, settings: Dict[str, Any], openai_client: Any,
                      model: str) -> "ScreenWatcher":
        cfg = (settings or {}).get('screen_awareness') or {}
        return cls(
            openai_client=openai_client,
            model=cfg.get('model') or model,
            interval_s=_number(cfg.get('interval_seconds'), 60.0),
            enabled=bool(cfg.get('enabled', False)),
            bbox=_bbox(cfg.get('bbox')),
            max_edge=int(_number(cfg.get('max_edge'), 768.0)),
            notable_cooldown_s=_number(cfg.get('notable_cooldown_seconds'), 240.0),
            same_kind_window_s=_number(cfg.get('same_kind_window_seconds'), 600.0),
            streak_threshold=int(_number(cfg.get('streak_threshold'), 3.0)),
        )

    # -------------------------------------------------------------- reads

    def describe(self) -> Optional[str]:
        """The cached line, or None when it is missing or stale.

        Stale beats wrong: a description of a game that ended ten minutes ago
        would have the co-host confidently narrating something that is no
        longer on screen.
        """
        if not self._description:
            return None
        if time.monotonic() - self._seen_at > self._stale_after():
            return None
        return self._description

    def _stale_after(self) -> float:
        """How old a description may be before it is withheld.

        Capped independently of the interval: at a 10-minute poll,
        interval * 3 would have called a half-hour-old screenshot current.
        """
        return min(self.interval_s * 3, MAX_DESCRIPTION_AGE_S)

    def age_s(self) -> Optional[float]:
        return None if not self._seen_at else time.monotonic() - self._seen_at

    def scene_age_s(self) -> Optional[float]:
        """How long the screen has looked essentially like this."""
        if not self._scene_since or not self.describe():
            return None
        return time.monotonic() - self._scene_since

    def describe_with_duration(self) -> Optional[str]:
        """The description, noting how long it has been true.

        "still on this after 11 minutes" is the difference between the
        co-host reading a screenshot and noticing the streamer is stuck.
        """
        seen = self.describe()
        if not seen:
            return None
        held = self.scene_age_s() or 0
        if held >= 300:
            return f"{seen} (unchanged for about {round(held / 60)} minutes)"
        return seen

    def stats(self) -> Dict[str, Any]:
        return {'looks': self.looks, 'failures': self.failures,
                'unchanged_skips': self.unchanged_skips,
                'offline_skips': self.offline_skips,
                'notables': self.notables,
                'notables_suppressed': self.notables_suppressed,
                'notables_held_same_kind': self.notables_held_same_kind,
                'notables_held_menu': self.notables_held_menu,
                'has_description': bool(self.describe())}

    # ------------------------------------------------------------ capture

    def _grab(self) -> Optional[Tuple[str, bytes]]:
        """Screenshot -> (base64 JPEG, signature). Runs in a worker thread.

        The signature is a 32x32 greyscale thumbnail: comparing two of them
        is enough to tell "the screen is basically the same" from "something
        happened", which decides whether a look is worth an API call.
        """
        try:
            from PIL import ImageGrab
        except ImportError:
            logger.warning("Screen awareness needs Pillow; disabling")
            self.enabled = False
            return None
        try:
            img = ImageGrab.grab(bbox=self.bbox) if self.bbox else ImageGrab.grab()
            img = img.convert("RGB")
            signature = img.convert("L").resize((32, 32)).tobytes()
            img.thumbnail((self.max_edge, self.max_edge))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
            return base64.b64encode(buf.getvalue()).decode(), signature
        except Exception as e:  # noqa: BLE001 - a failed look must never matter
            logger.debug(f"Screen capture failed: {e}")
            return None

    @staticmethod
    def _difference(a: Optional[bytes], b: Optional[bytes]) -> float:
        """Mean per-pixel difference of two signatures, 0-255."""
        if not a or not b or len(a) != len(b):
            return 255.0
        return sum(abs(x - y) for x, y in zip(a, b)) / len(a)

    async def look(self) -> Optional[str]:
        """Take one look. Returns the new description, or None."""
        if not self.enabled or not self.openai_client:
            return None
        try:
            if self.should_look is not None and not self.should_look():
                self.offline_skips += 1
                logger.debug("Stream is offline; not looking at the screen")
                return None
        except Exception as e:  # noqa: BLE001 - a broken probe must not blind us
            logger.debug(f"Liveness probe failed, looking anyway: {e}")
        # Capture is blocking GDI work: keep it off the event loop.
        grabbed = await asyncio.to_thread(self._grab)
        if not grabbed:
            self.failures += 1
            return None
        b64, signature = grabbed

        # Nothing moved since the last look: the cached description is still
        # true, so keep it fresh instead of paying for an identical answer.
        if (self._description
                and self._difference(signature, self._signature) < self.change_threshold):
            self._seen_at = time.monotonic()
            self.unchanged_skips += 1
            logger.debug("Screen unchanged; keeping the current description")
            return self._description

        look_prompt = LOOK_PROMPT
        if self._description:
            look_prompt += COMPARE_SUFFIX.format(previous=self._description)

        from personality.model_params import adapt_params
        params: Dict[str, Any] = adapt_params(self.model, {"max_tokens": 80})
        try:
            response = await asyncio.wait_for(
                self.openai_client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": [
                        {"type": "text", "text": look_prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}",
                            "detail": "low"}}]}],
                    **params),
                timeout=self.timeout_s)
        except asyncio.TimeoutError:
            self.failures += 1
            logger.warning("Screen look timed out; keeping the previous description")
            return None
        except Exception as e:  # noqa: BLE001 - never take the bot down for this
            self.failures += 1
            logger.warning(f"Screen look failed: {e}")
            return None

        choices = getattr(response, 'choices', None) or []
        text = ((choices[0].message.content or '') if choices else '').strip()
        if not text:
            self.failures += 1
            return None
        match = NOTABLE_RE.match(text)
        notable = match is not None
        kind = 'other'
        if match:
            # An unknown word is kept, not rejected: "kill streak" from a
            # model that ignored the list is still a truthful grouping, and
            # dropping it would silently collapse the run into "other".
            kind = (match.group(1) or '').strip().lower() or 'other'
            text = text[match.end():].strip()
        if not text:
            self.failures += 1
            return None

        now = time.monotonic()
        # A materially different screen starts a new scene; that clock is how
        # the co-host can notice the streamer has been stuck on one thing.
        if self._difference(signature, self._signature) >= self.scene_threshold:
            self._scene_since = now
        self._description = text
        self._signature = signature
        self._seen_at = now
        self.looks += 1
        logger.info(f"Screen{f' [NOTABLE {kind}]' if notable else ''}: {text[:110]}")

        if notable:
            await self._announce(text, now, kind)
        return text

    def streak_started_s_ago(self, kind: str):
        """How long ago this kind's run began.

        The bot needs it to say "the fourth death in about six minutes".
        It is the watcher's clock, so the watcher keeps it. Per kind, so an
        interleaved highlight cannot reset the death run's clock.
        """
        run = self._runs.get(kind)
        if not run or not run.started_at:
            return None
        return time.monotonic() - run.started_at

    def _prune_runs(self, now: float) -> None:
        """Drop runs whose window has closed, so a long stream cannot grow a
        dict of every kind the model ever invented."""
        for kind, run in list(self._runs.items()):
            if now - run.last_at > self.same_kind_window_s:
                del self._runs[kind]

    def _note_kind(self, kind: str, now: float) -> "_Run":
        """Advance THIS KIND's run and return it. A gap longer than the
        window starts a new run; another kind arriving in between does not.

        Live 2026-09-06, Overwatch: death, lobby, death, highlight, death.
        With one shared run every interruption reset the count and every
        death spoke as if it were the first.

        "other" never joins a run: it is not a kind, it is the model
        declining to name one, so two of them are no more related than any
        two moments. Grouping them would hold real, unrelated moments -- so
        it gets a throwaway run of 1 that is never stored.
        """
        self._prune_runs(now)
        if kind == 'other':
            return _Run(streak=1, said_at_streak=0, last_at=now, started_at=now)
        run = self._runs.setdefault(kind, _Run())
        if run.streak > 0 and now - run.last_at <= self.same_kind_window_s:
            run.streak += 1
        else:
            run.streak = 1
            run.said_at_streak = 0
            run.started_at = now
        run.last_at = now
        return run

    async def _announce(self, what: str, now: float, kind: str = 'other') -> None:
        """Tell the bot something happened, at most once per cooldown and
        not once per repeat.

        Live 2026-09-05: three of four bot lines in ten minutes were deaths
        in Overwatch, each the same shape. The cooldown cannot see that --
        it only knows how long ago the last line was. The kind can: the
        first death of a run gets a line, the next ones are held and
        counted, and the threshold-th unsaid repeat gets a line about the
        pattern instead of about the moment.
        """
        if not self.on_notable:
            return
        # Before the cooldown and before the run: a menu must cost nothing.
        # Spending the cooldown on a hero-select screen would silence the
        # boss fight that follows it, and giving it a run would let two
        # lobbies hold each other as if they were a story.
        if kind in NEVER_MOMENTS:
            self.notables_held_menu += 1
            logger.info(f"Screen moment held, menus are not moments ({kind})")
            return
        run = self._note_kind(kind, now)
        if now - self._last_notable_at < self.notable_cooldown_s:
            self.notables_suppressed += 1
            logger.info(f"Screen moment held by cooldown ({what[:60]})")
            return
        # The run keeps counting through a cooldown hold, so a run whose
        # middle the cooldown swallowed still earns its pattern line.
        if (run.streak > 1
                and run.streak - run.said_at_streak < self.streak_threshold):
            self.notables_held_same_kind += 1
            logger.info(f"Screen moment held, same kind ({kind}, {run.streak} in a row)")
            return
        self._last_notable_at = now
        run.said_at_streak = run.streak
        self.notables += 1
        try:
            await self.on_notable(what, kind, run.streak)
        except Exception as e:  # noqa: BLE001 - a reaction must never break the loop
            logger.warning(f"Screen reaction failed: {e}")

    # --------------------------------------------------------------- loop

    async def _loop(self) -> None:
        while True:
            started = time.monotonic()
            try:
                await self.look()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.debug(f"Screen watcher iteration failed: {e}")
            # Sleep the REMAINDER of the interval. A full sleep after the work
            # made the real period interval + call time + reaction time, so a
            # 60s setting drifted toward 75s whenever the co-host reacted.
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(1.0, self.interval_s - elapsed))

    def start(self, create_task) -> None:
        """Begin watching. `create_task` is the bot's TaskRegistry factory."""
        if not self.enabled:
            logger.info("Screen awareness disabled")
            return
        if not self.openai_client:
            logger.warning("Screen awareness needs an OpenAI client; disabled")
            self.enabled = False
            return
        self._task = create_task(self._loop(), name="screen_watcher")
        where = f"region {self.bbox}" if self.bbox else "primary display"
        logger.info(f"Screen awareness active (every {self.interval_s:.0f}s, {where})")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            pass
        self._task = None
