"""ChatVelocity: rate math, adaptive thresholds, pacing multiplier shape,
burst detection, regime transitions. Injected clock, no timers."""
import pytest

from features.chat_velocity import ChatVelocity


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(**kw):
    c = Clock()
    v = ChatVelocity(clock=c, **kw)
    return v, c


def feed(v, c, count, spacing_s):
    for _ in range(count):
        c.t += spacing_s
        v.note_message()


# ------------------------------------------------------------------- rate

def test_rate_counts_only_the_window():
    v, c = make(window_s=60)
    feed(v, c, 10, 1.0)                 # 10 msgs in the last ~10s
    assert v.per_minute() == 10.0
    c.t += 120                          # window empties
    assert v.per_minute() == 0.0
    assert v.messages_seen == 10        # lifetime counter unaffected


def test_peak_tracks_maximum():
    v, c = make(window_s=60)
    feed(v, c, 30, 0.5)
    peak = v.peak_per_minute
    c.t += 300
    feed(v, c, 1, 1.0)
    assert v.peak_per_minute == peak >= 30


# -------------------------------------------------------------- multiplier

def test_quiet_chat_boosts():
    v, c = make()
    feed(v, c, 2, 30.0)                 # 2 msgs/min
    assert v.multiplier() == v.quiet_boost
    assert v.regime() == "quiet"


def test_dead_chat_boosts_too():
    v, c = make()
    assert v.multiplier() == v.quiet_boost   # no messages at all


def test_busy_chat_damps():
    v, c = make()
    feed(v, c, 30, 1.0)                 # ~30 msgs/min
    assert v.multiplier() == v.busy_damp
    assert v.regime() == "busy"


def test_multiplier_is_monotonic_in_rate():
    mults = []
    for spacing in (30.0, 12.0, 8.0, 5.0, 3.0, 2.0, 1.0):
        v, c = make()
        feed(v, c, int(120 / spacing) or 1, spacing)
        mults.append(v.multiplier())
    assert mults == sorted(mults, reverse=True), mults
    assert mults[0] == 2.5 and mults[-1] == 0.35


def test_middle_of_the_band_is_near_one():
    v, c = make(quiet_per_min=2.0, busy_per_min=12.0, baseline_alpha=0.0)
    # geometric middle of 2..12 is ~4.9 msg/min
    feed(v, c, 10, 60 / 4.9)
    assert v.multiplier() == pytest.approx(1.0, rel=0.35)


# ----------------------------------------------------- adaptive thresholds

def test_big_channel_baseline_raises_the_quiet_bar():
    v, c = make(baseline_alpha=0.5)
    # sustain ~20 msgs/min for a while: baseline climbs
    feed(v, c, 200, 3.0)
    assert v.baseline() > 10
    # now 6 msgs/min — "quiet" for THIS channel even though it beats the
    # absolute quiet floor of 2/min
    feed(v, c, 12, 10.0)
    assert v.regime() == "quiet"
    assert v.multiplier() == v.quiet_boost


def test_burst_does_not_drag_baseline_up_instantly():
    v, c = make()
    feed(v, c, 5, 30.0)                 # calm channel, baseline ~2
    calm = v.baseline()
    feed(v, c, 40, 0.25)                # 10-second explosion
    assert v.baseline() < calm + 5, "per-minute EMA must smooth a burst"


# ------------------------------------------------------------------ burst

def test_burst_detection_needs_volume_and_ratio():
    v, c = make(burst_min_messages=8, burst_ratio=3.0)
    feed(v, c, 4, 0.5)                  # fast but tiny: not a burst
    assert not v.is_burst()
    feed(v, c, 10, 0.5)                 # sustained spike
    assert v.is_burst()


def test_no_burst_when_fast_is_the_channels_normal():
    v, c = make(baseline_alpha=0.5)
    feed(v, c, 300, 2.0)                # 30/min forever: that IS the baseline
    assert v.baseline() > 20
    assert not v.is_burst()


# ------------------------------------------------------------- transitions

def test_regime_transitions_are_logged_once(caplog):
    import logging
    v, c = make()
    with caplog.at_level(logging.INFO, logger="features.chat_velocity"):
        feed(v, c, 3, 30.0)             # quiet (from initial 'normal')
        feed(v, c, 40, 0.5)             # busy
        feed(v, c, 40, 0.5)             # still busy: no new line
    lines = [r.message for r in caplog.records if "Chat pace" in r.message]
    assert len(lines) == 2
    assert "-> quiet" in lines[0] and "-> busy" in lines[1]


def test_stats_shape():
    v, c = make()
    feed(v, c, 5, 2.0)
    s = v.stats()
    assert set(s) == {"per_minute", "baseline", "peak_per_minute", "messages_seen", "regime"}
    assert s["messages_seen"] == 5
