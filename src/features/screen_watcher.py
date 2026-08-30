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
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

LOOK_PROMPT = (
    "You are looking at a live stream's screen. In ONE short factual "
    "sentence: what game or application is this, and what is happening right "
    "now? Name what you can actually see -- a menu, a death screen, a score, "
    "a boss, a code editor. If something is clearly going wrong or notable, "
    "say so plainly. Never speculate and never invent numbers."
)


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

        self._description: Optional[str] = None
        self._seen_at: float = 0.0
        self._signature: Optional[bytes] = None
        self._scene_since: float = 0.0
        self._task = None
        self.looks = 0
        self.failures = 0
        self.unchanged_skips = 0

    # ------------------------------------------------------------- config

    @classmethod
    def from_settings(cls, settings: Dict[str, Any], openai_client: Any,
                      model: str) -> "ScreenWatcher":
        cfg = (settings or {}).get('screen_awareness') or {}
        raw = cfg.get('bbox')
        bbox = tuple(raw) if isinstance(raw, (list, tuple)) and len(raw) == 4 else None
        return cls(
            openai_client=openai_client,
            model=cfg.get('model') or model,
            interval_s=cfg.get('interval_seconds', 60),
            enabled=bool(cfg.get('enabled', False)),
            bbox=bbox,
            max_edge=int(cfg.get('max_edge', 768)),
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
        if time.monotonic() - self._seen_at > self.interval_s * 3:
            return None
        return self._description

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

        params: Dict[str, Any] = {"max_completion_tokens": 80}
        model = self.model or ''
        if model.startswith('gpt-5'):
            params["reasoning_effort"] = (
                'none' if model.startswith('gpt-5.5') else 'minimal')
        try:
            response = await asyncio.wait_for(
                self.openai_client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": [
                        {"type": "text", "text": LOOK_PROMPT},
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
        now = time.monotonic()
        # A materially different screen starts a new scene; that clock is how
        # the co-host can notice the streamer has been stuck on one thing.
        if self._difference(signature, self._signature) >= self.scene_threshold:
            self._scene_since = now
        self._description = text
        self._signature = signature
        self._seen_at = now
        self.looks += 1
        logger.info(f"Screen: {text[:110]}")
        return text

    # --------------------------------------------------------------- loop

    async def _loop(self) -> None:
        while True:
            try:
                await self.look()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.debug(f"Screen watcher iteration failed: {e}")
            await asyncio.sleep(self.interval_s)

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
