"""StreamSessionSummarizer: trigger logic, watermarks, folding, persistence.

The LLM is a fake that records what it was asked and returns canned text.
Time is an injected counter. No real I/O except tmp_path for persistence.
"""
import asyncio
import json

from bot.channel_chat_buffer import ChannelChatBuffer
from bot.session_summarizer import StreamSessionSummarizer

CH = "cassova_"


class FakeLLM:
    def __init__(self, reply="summary v1"):
        self.reply = reply
        self.calls = []
        self.block = None  # asyncio.Event to hold a call open

    async def __call__(self, messages):
        self.calls.append(messages)
        if self.block is not None:
            await self.block.wait()
        return self.reply() if callable(self.reply) else self.reply


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(buffer=None, llm=None, clock=None, **kw):
    buffer = buffer or ChannelChatBuffer(max_turns_per_channel=200)
    llm = llm or FakeLLM()
    clock = clock or Clock()
    defaults = dict(turns_per_update=10, min_turns=3, max_interval_s=600,
                    max_words=50, max_chars=200)
    defaults.update(kw)
    s = StreamSessionSummarizer(buffer, llm, bot_name="elimist_", clock=clock, **defaults)
    s.get_summary(CH)  # create channel state now, so "session start" is t0
    return s, buffer, llm, clock


def fill(buffer, n, start=0):
    for i in range(start, start + n):
        buffer.append_viewer(CH, f"viewer{i % 3}", f"message number {i}")


def spawn_inline(created):
    """A `spawn` that runs the coroutine to completion as a task, collecting it."""
    def _spawn(coro, name):
        t = asyncio.get_event_loop().create_task(coro, name=name)
        created.append(t)
        return t
    return _spawn


# ------------------------------------------------------------ chat buffer seq

def test_chat_buffer_seq_is_monotonic_and_survives_eviction():
    b = ChannelChatBuffer(max_turns_per_channel=3)
    fill(b, 5)
    assert b.last_seq(CH) == 5
    assert [t["seq"] for t in b.get_since(CH, 0)] == [3, 4, 5]  # 1,2 evicted
    assert b.get_since(CH, 4)[0]["message"] == "message number 4"
    assert b.get_since(CH, 5) == []
    assert b.last_seq("#Cassova_") == 5  # channel normalization
    assert b.last_seq("nobody") == 0


def test_chat_buffer_assistant_turns_share_the_counter():
    b = ChannelChatBuffer()
    b.append_viewer(CH, "v", "hi")
    b.append_assistant(CH, "bot", "hello")
    assert [t["role"] for t in b.get_since(CH, 0)] == ["viewer", "assistant"]
    assert b.last_seq(CH) == 2


# ---------------------------------------------------------------- triggers

def test_no_update_below_batch_size():
    s, b, llm, clock = make()
    fill(b, 9)
    assert not s.should_update(CH)


def test_update_at_batch_size():
    s, b, llm, clock = make()
    fill(b, 10)
    assert s.should_update(CH)


def test_slow_chat_folds_after_interval_with_min_turns():
    s, b, llm, clock = make()
    fill(b, 3)
    assert not s.should_update(CH)
    clock.t += 601
    assert s.should_update(CH)


def test_slow_chat_does_not_fold_below_min_turns():
    s, b, llm, clock = make()
    fill(b, 2)
    clock.t += 601
    assert not s.should_update(CH)


def test_pending_event_alone_folds_after_interval():
    s, b, llm, clock = make()
    s.note_event(CH, "someone raided with 40 viewers")
    assert not s.should_update(CH)
    clock.t += 601
    assert s.should_update(CH)


def test_nothing_to_fold_never_triggers():
    s, b, llm, clock = make()
    clock.t += 10_000
    assert not s.should_update(CH)


# ------------------------------------------------------------------ folding

async def test_update_folds_turns_and_advances_watermark():
    s, b, llm, clock = make()
    fill(b, 10)
    b.append_assistant(CH, "elimist_", "i have opinions about this")
    s.note_event(CH, "cassova_ switched to Elden Ring")

    assert await s.update(CH)
    assert s.get_summary(CH) == "summary v1"
    assert s.stats(CH)["watermark"] == 11
    assert s.stats(CH)["unsummarized_turns"] == 0
    assert s.stats(CH)["pending_events"] == 0

    msgs = llm.calls[0]
    assert msgs[0]["role"] == "system"
    assert "Maximum 50 words" in msgs[0]["content"]
    user = msgs[1]["content"]
    assert "(none yet" in user
    assert "[event] cassova_ switched to Elden Ring" in user
    assert "elimist_ (the co-host): i have opinions about this" in user
    assert "viewer0: message number 0" in user


async def test_second_fold_sees_previous_summary_and_only_new_turns():
    s, b, llm, clock = make()
    fill(b, 10)
    await s.update(CH)
    llm.reply = "summary v2"
    fill(b, 4, start=10)
    await s.update(CH)
    user = llm.calls[1]["content"] if isinstance(llm.calls[1], dict) else llm.calls[1][1]["content"]
    assert "PREVIOUS SUMMARY:\nsummary v1" in user
    assert "message number 10" in user
    assert "message number 9" not in user
    assert s.get_summary(CH) == "summary v2"


async def test_failed_fold_keeps_watermark_for_retry():
    s, b, llm, clock = make()
    fill(b, 10)
    llm.reply = ""  # empty -> failure
    assert not await s.update(CH)
    assert s.get_summary(CH) == ""
    assert s.stats(CH)["watermark"] == 0
    assert s.stats(CH)["failures"] == 1
    assert s.should_update(CH), "still due; nothing was consumed"


async def test_llm_exception_is_swallowed_and_counted():
    async def boom(_):
        raise RuntimeError("api down")
    s, b, llm, clock = make(llm=boom)
    fill(b, 10)
    assert not await s.update(CH)
    assert s.stats(CH)["failures"] == 1
    assert not s.stats(CH)["in_flight"]


async def test_summary_truncated_to_max_chars():
    s, b, llm, clock = make(max_chars=40)
    llm.reply = "word " * 50
    fill(b, 10)
    await s.update(CH)
    out = s.get_summary(CH)
    assert len(out) <= 44 and out.endswith("...")


async def test_turns_arriving_during_fold_are_not_lost():
    s, b, llm, clock = make()
    llm.block = asyncio.Event()
    fill(b, 10)
    task = asyncio.create_task(s.update(CH))
    await asyncio.sleep(0)            # let it snapshot + start the LLM call
    fill(b, 3, start=10)              # chat keeps moving mid-call
    llm.block.set()
    await task
    assert s.stats(CH)["watermark"] == 10
    assert s.stats(CH)["unsummarized_turns"] == 3


# ------------------------------------------------------------- scheduling

async def test_maybe_schedule_spawns_once_and_blocks_reentry():
    s, b, llm, clock = make()
    llm.block = asyncio.Event()
    fill(b, 10)
    created = []
    assert s.maybe_schedule(CH, spawn_inline(created))
    assert not s.maybe_schedule(CH, spawn_inline(created)), "in-flight guard"
    assert len(created) == 1
    assert created[0].get_name() == "session_summary_cassova_"
    llm.block.set()
    await created[0]
    assert s.get_summary(CH) == "summary v1"
    assert not s.maybe_schedule(CH, spawn_inline(created)), "nothing new"


async def test_failed_attempt_does_not_retrigger_until_interval():
    s, b, llm, clock = make()
    llm.reply = ""
    fill(b, 3)
    clock.t += 601
    created = []
    assert s.maybe_schedule(CH, spawn_inline(created))
    await created[0]
    assert not s.should_update(CH), "last_attempt resets the interval"
    clock.t += 601
    assert s.should_update(CH)


# ------------------------------------------------------------ persistence

async def test_persist_and_restore_within_max_age(tmp_path):
    clock = Clock(5000.0)
    s, b, llm, _ = make(clock=clock, persist_dir=str(tmp_path))
    fill(b, 10)
    await s.update(CH)
    saved = json.loads((tmp_path / "session_summary_cassova_.json").read_text())
    assert saved["summary"] == "summary v1"

    # fresh process, fresh buffer, 10 min later
    clock2 = Clock(5600.0)
    s2, b2, _, _ = make(clock=clock2, persist_dir=str(tmp_path))
    assert s2.get_summary(CH) == "summary v1"
    assert s2.stats(CH)["watermark"] == 0, "new buffer restarts at seq 0"


async def test_stale_persisted_summary_ignored(tmp_path):
    clock = Clock(5000.0)
    s, b, llm, _ = make(clock=clock, persist_dir=str(tmp_path))
    fill(b, 10)
    await s.update(CH)

    clock2 = Clock(5000.0 + 7 * 3600)
    s2, *_ = make(clock=clock2, persist_dir=str(tmp_path))
    assert s2.get_summary(CH) == ""


def test_corrupt_persisted_file_ignored(tmp_path):
    (tmp_path / "session_summary_cassova_.json").write_text("{not json")
    s, *_ = make(persist_dir=str(tmp_path))
    assert s.get_summary(CH) == ""


# ------------------------------------------- turn preservation (Bugbot F9)

async def test_turns_are_not_lost_when_the_ring_buffer_evicts_during_a_fold():
    """The chat buffer is a small ring sized for prompting. Reading it only at
    fold time meant a busy channel could evict turns that had never been
    summarized -- they vanished from the co-host's memory of the stream."""
    small = ChannelChatBuffer(max_turns_per_channel=10)
    s, b, llm, clock = make(buffer=small, turns_per_update=5, min_turns=2)
    llm.block = asyncio.Event()

    fill(b, 5)
    assert s.should_update(CH) is True          # harvests 1..5
    task = asyncio.create_task(s.update(CH))
    await asyncio.sleep(0)

    # 12 more turns arrive mid-call: more than the ring can hold, so the
    # buffer evicts everything the summarizer had not already taken.
    for i in range(5, 17):
        b.append_viewer(CH, "viewer", f"message number {i}")
        s.should_update(CH)                     # the per-message hook harvests

    llm.block.set()
    await task

    assert s.stats(CH)["watermark"] == 5
    assert s.stats(CH)["unsummarized_turns"] == 12, \
        "every turn that arrived during the fold must still be owed"

    # The next fold sees all 12, including ones the ring has already dropped.
    llm.reply = "summary v2"
    await s.update(CH)
    folded = llm.calls[-1][1]["content"]
    for i in (5, 10, 16):
        assert f"message number {i}" in folded, f"turn {i} was lost"
    assert s.stats(CH)["unsummarized_turns"] == 0


async def test_backlog_survives_a_failed_fold_without_duplicating():
    s, b, llm, clock = make(turns_per_update=3, min_turns=1)
    fill(b, 3)

    llm.reply = ""                               # empty result == failure
    assert await s.update(CH) is False
    assert s.stats(CH)["unsummarized_turns"] == 3, "a failed fold must keep its turns"
    assert s.stats(CH)["watermark"] == 0, "watermark advances only on success"

    llm.reply = "summary v1"
    assert await s.update(CH) is True
    folded = llm.calls[-1][1]["content"]
    assert folded.count("message number 0") == 1, "retry must not duplicate turns"
    assert s.stats(CH)["unsummarized_turns"] == 0


async def test_backlog_is_capped():
    s, b, llm, clock = make(max_pending_turns=20, turns_per_update=1000)
    fill(b, 50)
    s.should_update(CH)
    assert s.stats(CH)["unsummarized_turns"] == 20


async def test_reset_drops_the_previous_streams_backlog():
    s, b, llm, clock = make()
    fill(b, 6)
    s.should_update(CH)
    assert s.stats(CH)["unsummarized_turns"] == 6

    s.reset(CH)
    assert s.stats(CH)["unsummarized_turns"] == 0, "a new stream starts clean"
    fill(b, 2, start=6)
    s.should_update(CH)
    assert s.stats(CH)["unsummarized_turns"] == 2, "new turns are still harvested"
