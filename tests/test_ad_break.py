"""Ad breaks: usable durations, tracked timers, no stale scheduler.

Three defects sat here. The ad length was taken straight from the event and
fed to arithmetic, string formatting and asyncio.sleep -- a null or the
string form of duration_seconds would raise mid-announcement. The end
scheduler was a raw asyncio.create_task, untracked by the TaskRegistry the
project mandates, sleeping up to three minutes and therefore outliving
shutdown. And nothing cancelled a previous break's scheduler, so it could
fire during the next break and end it early.
"""
import asyncio

import pytest

from features.ad_announcer import AdAnnouncer


def _announcer():
    ad = AdAnnouncer.__new__(AdAnnouncer)
    ad.enabled = True
    ad.ad_active = False
    ad.ad_start_time = None
    ad.ad_duration = 0
    ad.last_ad_time = None
    ad.min_time_between_ads = 0
    ad.announce_at_end = False
    ad.announce_in_chat = False
    ad.announce_with_voice = False
    ad.twitch_client = None
    ad.audio_queue = None
    ad.response_coordinator = None
    ad.chat_buffer = None
    ad.channel_name = 'cassova_'
    ad._end_task = None
    ad.announced = []
    # fallback pools the no-LLM path formats
    ad.ad_start_messages = ["ads for {duration}s ({minutes} minute{s})"]
    ad.standard_ad_messages = ["standard break"]
    ad.long_ad_messages = ["long break: {duration}s / {minutes}m"]
    ad.ad_end_messages = ["we're back"]

    async def fake_announce(message, is_start=True):
        ad.announced.append((is_start, message))
    ad._announce_ad = fake_announce

    async def no_llm(*a, **k):
        return None
    ad._generate_hook_message = no_llm
    ad._generate_return_message = no_llm
    return ad


# ------------------------------------------------------------- durations

@pytest.mark.parametrize('raw, expected', [
    (90, 90),
    ('90', 90),          # Twitch has been seen sending this as a string
    (90.0, 90),
    (None, 42),          # null -> default
    ('', 42),
    ('junk', 42),
    (0, 42),             # !ad 0 must not mean a zero-second break
    (-30, 42),
    (600, 180),          # clamped to Twitch's maximum
])
def test_duration_coercion(raw, expected):
    assert AdAnnouncer._coerce_duration(raw, default=42) == expected


async def test_null_duration_does_not_break_the_announcement():
    """The old code put None into `>=` comparisons and asyncio.sleep."""
    ad = _announcer()
    await ad._handle_ad_start({'type': 'commercial_start', 'length': None})
    assert ad.ad_duration == 90
    assert ad.announced and ad.announced[0][0] is True
    ad._end_task.cancel()


async def test_string_duration_from_eventsub_is_handled():
    ad = _announcer()
    captured = {}

    async def spy(event):
        captured.update(event)
        ad.ad_duration = AdAnnouncer._coerce_duration(event.get('length'), 90)
    ad._handle_ad_start = spy
    await ad.handle_ad_break_begin({'duration_seconds': '180', 'is_automatic': True})
    assert captured['length'] == 180 and captured['is_automatic'] is True
    assert isinstance(captured['length'], int)


async def test_missing_duration_uses_the_eventsub_default():
    ad = _announcer()
    captured = {}

    async def spy(event):
        captured.update(event)
    ad._handle_ad_start = spy
    await ad.handle_ad_break_begin({})
    assert captured['length'] == 30


# ------------------------------------------------------- scheduler safety

async def test_end_scheduler_is_registry_tracked_not_a_raw_task():
    ad = _announcer()
    await ad._handle_ad_start({'length': 1})
    assert ad._end_task is not None, "the scheduler must be retained so it can be cancelled"
    from utils.task_registry import get_global_registry
    registry = get_global_registry()
    names = [t for t in getattr(registry, 'active_tasks', {})]
    assert any('ad_break_end' in str(n) for n in names), names
    ad._end_task.cancel()


async def test_a_new_break_cancels_the_previous_scheduler():
    """Otherwise the old timer fires during the new break and ends it early."""
    ad = _announcer()
    await ad._handle_ad_start({'length': 180})
    first = ad._end_task
    await ad._handle_ad_start({'length': 30})
    second = ad._end_task
    second.cancel()                      # clean up before any assertion can raise
    await asyncio.sleep(0)               # let the cancellations actually land
    assert first.cancelled() or first.done(), "stale scheduler must be cancelled"
    assert second is not first


async def test_scheduler_ends_the_break_and_clears_itself():
    ad = _announcer()
    ad.announce_at_end = True
    await ad._handle_ad_start({'length': 0.05})
    ad.ad_duration = 0.05
    await asyncio.wait_for(ad._end_task, timeout=2)
    assert ad.ad_active is False
    assert ad._end_task is None
    assert any(is_start is False for is_start, _ in ad.announced)


async def test_ending_an_inactive_break_is_a_noop():
    ad = _announcer()
    ad.ad_active = False
    await ad._handle_ad_end({})
    assert ad.announced == []


async def test_cooldown_blocks_a_second_break():
    ad = _announcer()
    ad.min_time_between_ads = 480
    await ad._handle_ad_start({'length': 60})
    ad._end_task.cancel()
    count = len(ad.announced)
    ad.ad_active = False
    await ad._handle_ad_start({'length': 60})
    assert len(ad.announced) == count, "too-soon break must be skipped"
