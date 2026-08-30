"""Three features that connect machinery already in the codebase.

1. Vision-flagged moments clip themselves. Chat bursts clip what the ROOM
   noticed; a death or a win nobody typed about was going unclipped.
2. Channel point redemptions get an in-character reaction. The scope was
   already granted and the event was never subscribed to.
3. A regular returning after days away is flagged in context, so the
   co-host can notice rather than greeting them like a stranger.
"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from bot.optimized_context_builder import OptimizedContextBuilder

BOT = Path("src/bot/bot.py").read_text(encoding="utf-8")
CLIPPER = Path("src/features/auto_clipper.py").read_text(encoding="utf-8")
EVENTSUB = Path("src/twitch/eventsub_websocket.py").read_text(encoding="utf-8")
ANNOUNCER = Path("src/features/event_announcer.py").read_text(encoding="utf-8")
ENGINE = Path("src/personality/personality_engine.py").read_text(encoding="utf-8")


# ------------------------------------------- 1. clip what the screen did

def test_a_notable_moment_clips_itself():
    assert "async def _clip_notable_moment" in BOT
    fn = BOT.split("async def _clip_notable_moment")[1].split("\n    async def ")[0]
    assert "self.auto_clipper.should_clip_moment()" in fn, "must respect the cooldown"
    assert 'mark_triggered(source="moment")' in fn


def test_the_two_clip_sources_share_one_cooldown():
    """Otherwise a death during a hype burst clips twice. Exercised for real
    in test_auto_clipper.py; this pins the wiring that reaches it."""
    clipper = Path("src/features/auto_clipper.py").read_text(encoding="utf-8")
    assert "def _cooling_down(self)" in clipper, "one cooldown, two callers"
    for method in ("def should_clip(", "def should_clip_moment("):
        body = clipper.split(method)[1].split("\n    def ")[0]
        assert "self._cooling_down()" in body, f"{method} must consult it"


def test_the_clip_does_not_delay_the_reaction():
    react = BOT.split("async def _react_to_screen")[1].split("\n    async def ")[0]
    assert "task_registry.create_task(" in react, "a Helix round trip must not block the line"
    assert 'name="clip_notable_moment"' in react


def test_the_reason_names_the_moment():
    fn = BOT.split("async def _clip_notable_moment")[1].split("\n    async def ")[0]
    assert 'reason=f"on screen:' in fn


def test_auto_clip_still_takes_a_burst_reason():
    assert 'async def _auto_clip(self, reason: str = "chat hype")' in BOT


def test_the_behaviour_can_be_turned_off():
    assert "clip_notable_moments: bool = True" in CLIPPER
    assert 'cfg.get("clip_notable_moments", True)' in CLIPPER
    assert "clip_notable_moments" in BOT


def test_a_missing_clipper_is_not_a_crash():
    fn = BOT.split("async def _clip_notable_moment")[1].split("\n    async def ")[0]
    assert "if not self.auto_clipper:" in fn


# ------------------------------------------------ 2. channel point redemptions

def test_the_redemption_event_is_subscribed():
    assert "'channel.channel_points_custom_reward_redemption.add'" in EVENTSUB


def test_the_scope_it_needs_is_requested():
    """channel:read:redemptions -- already in the auth script, which is why
    this could be wired at all."""
    script = Path("get_twitch_tokens.py").read_text(encoding="utf-8")
    assert '"channel:read:redemptions"' in script


def test_the_bot_routes_redemptions():
    assert "'channel.channel_points_custom_reward_redemption.add'," in BOT
    assert "self._on_redemption)" in BOT
    assert "async def _on_redemption" in BOT


def test_the_reaction_uses_the_reward_title_not_the_points():
    fn = ANNOUNCER.split("async def handle_redemption")[1].split("\n    async def ")[0]
    assert "reward.get('title')" in fn
    assert "not to the" in fn and "points" in fn, "the title is what varies"


def test_a_redemption_with_no_input_still_works():
    fn = ANNOUNCER.split("async def handle_redemption")[1].split("\n    async def ")[0]
    assert "if user_input:" in fn, "user_input is only present on some rewards"


def test_redemption_fields_are_null_safe():
    """EventSub sends nulls, not missing keys -- .get(k, default) would keep
    the None. This codebase has been bitten by that repeatedly."""
    fn = ANNOUNCER.split("async def handle_redemption")[1].split("\n    async def ")[0]
    assert "or {}" in fn and "or 'Someone'" in fn
    assert "or 0" in fn and "or ''" in fn


def test_a_junk_cost_does_not_raise():
    fn = ANNOUNCER.split("async def handle_redemption")[1].split("\n    async def ")[0]
    assert "except (TypeError, ValueError)" in fn


def test_a_long_user_input_is_clipped():
    fn = ANNOUNCER.split("async def handle_redemption")[1].split("\n    async def ")[0]
    assert "[:160]" in fn


# ------------------------------------------------- 3. returning regulars

NOW = datetime(2026, 8, 30, 12, 0, 0)


def _viewer(**kw):
    base = {"message_count": 40, "last_seen": NOW - timedelta(days=3),
            "from_memory": False}
    base.update(kw)
    return {"viewer_data": base}


@pytest.mark.parametrize("days, expected", [
    (3, "3 days"),
    (6, "6 days"),
    (8, "about a week"),
    (16, "a couple of weeks"),
    (45, "over a month"),
])
def test_absences_are_described_in_human_terms(days, expected):
    data = _viewer(last_seen=NOW - timedelta(days=days))
    assert OptimizedContextBuilder._returning_after(data, NOW) == expected


def test_someone_here_today_is_not_returning():
    data = _viewer(last_seen=NOW - timedelta(hours=5))
    assert OptimizedContextBuilder._returning_after(data, NOW) == ""


def test_a_stranger_is_not_a_regular():
    """Nothing to remember them by; the first-timer path handles them."""
    assert OptimizedContextBuilder._returning_after(_viewer(message_count=2), NOW) == ""


def test_the_in_memory_fallback_is_never_trusted():
    """It resets on restart, so it would call everyone a returning regular."""
    assert OptimizedContextBuilder._returning_after(_viewer(from_memory=True), NOW) == ""


@pytest.mark.parametrize("data", [
    {}, {"viewer_data": None}, {"viewer_data": "nope"},
    {"viewer_data": {"message_count": 40, "last_seen": "yesterday"}},
    {"viewer_data": {"message_count": "many", "last_seen": NOW}},
])
def test_anything_unclear_stays_silent(data):
    assert OptimizedContextBuilder._returning_after(data, NOW) == ""


def test_a_naive_aware_mismatch_does_not_raise():
    from datetime import timezone
    data = _viewer(last_seen=datetime(2026, 8, 1, tzinfo=timezone.utc))
    assert OptimizedContextBuilder._returning_after(data, NOW) == ""


def test_the_context_carries_it_on_both_paths():
    builder = Path("src/bot/optimized_context_builder.py").read_text(encoding="utf-8")
    assert '"returning_after": self._returning_after(data)' in builder
    assert "context['returning_after'] = \"\"" in builder


def test_the_prompt_asks_for_a_passing_mention_not_a_ceremony():
    assert "has not been around for" in ENGINE
    assert "never as a formal welcome" in ENGINE
