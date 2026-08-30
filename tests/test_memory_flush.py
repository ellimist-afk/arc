"""The outage buffer must not discard messages it never wrote.

_store_message_to_db and _store_memory_to_db return False on failure -- they
do not raise. The flush loop counted every item as flushed regardless, then
popped that many off the buffer, so a database that came back but rejected
writes silently destroyed exactly the data the buffer exists to protect.
"""
from collections import deque

from memory.resilient_memory_system import ResilientMemorySystem


def _memory(items=(), maxlen=1000):
    m = ResilientMemorySystem.__new__(ResilientMemorySystem)
    m.db = object()                      # truthy: a connection exists
    m.memory_buffer = deque(items, maxlen=maxlen)
    m.write_count = 0
    m.db_failures = 0
    m.user_memory = {}
    m.stored = []
    return m


def msg(n):
    return {'type': 'message', 'data': {'username': f'v{n}', 'message': f'm{n}'}}


def _accept_all(m):
    async def store(data):
        m.stored.append(data)
        return True
    m._store_message_to_db = store
    m._store_memory_to_db = store


def _reject_after(m, n):
    async def store(data):
        if len(m.stored) >= n:
            return False                 # the DB says no, without raising
        m.stored.append(data)
        return True
    m._store_message_to_db = store
    m._store_memory_to_db = store


# ------------------------------------------------------------ the defect

async def test_rejected_writes_stay_buffered():
    m = _memory([msg(i) for i in range(5)])
    _reject_after(m, 2)
    await m._flush_buffer_to_database()
    assert len(m.stored) == 2
    assert len(m.memory_buffer) == 3, "unwritten items must survive the flush"
    assert m.memory_buffer[0]['data']['message'] == 'm2', "and keep their order"


async def test_a_total_rejection_keeps_everything():
    m = _memory([msg(i) for i in range(4)])
    _reject_after(m, 0)
    await m._flush_buffer_to_database()
    assert m.stored == []
    assert len(m.memory_buffer) == 4


async def test_a_retry_after_recovery_finishes_the_job():
    m = _memory([msg(i) for i in range(5)])
    _reject_after(m, 2)
    await m._flush_buffer_to_database()
    _accept_all(m)                        # database recovers
    await m._flush_buffer_to_database()
    assert len(m.memory_buffer) == 0
    assert [d['message'] for d in m.stored] == ['m0', 'm1', 'm2', 'm3', 'm4']


async def test_no_duplicates_across_a_partial_then_full_flush():
    m = _memory([msg(i) for i in range(3)])
    _reject_after(m, 1)
    await m._flush_buffer_to_database()
    _accept_all(m)
    await m._flush_buffer_to_database()
    assert [d['message'] for d in m.stored] == ['m0', 'm1', 'm2']


# ------------------------------------------------------- ordinary flushing

async def test_a_clean_flush_empties_the_buffer():
    m = _memory([msg(i) for i in range(3)])
    _accept_all(m)
    await m._flush_buffer_to_database()
    assert len(m.memory_buffer) == 0 and len(m.stored) == 3


async def test_an_exception_stops_the_flush_without_losing_items():
    m = _memory([msg(i) for i in range(3)])

    async def boom(data):
        raise RuntimeError("connection reset")
    m._store_message_to_db = boom
    await m._flush_buffer_to_database()
    assert len(m.memory_buffer) == 3


async def test_an_unflushable_item_does_not_wedge_the_queue():
    m = _memory([{'type': 'mystery', 'data': {}}, msg(1)])
    _accept_all(m)
    await m._flush_buffer_to_database()
    assert len(m.memory_buffer) == 0, "the unknown item must not block the rest"
    assert len(m.stored) == 1


async def test_flush_without_a_connection_is_a_noop():
    m = _memory([msg(0)])
    m.db = None
    await m._flush_buffer_to_database()
    assert len(m.memory_buffer) == 1


async def test_empty_buffer_is_fine():
    m = _memory()
    _accept_all(m)
    await m._flush_buffer_to_database()
    assert len(m.memory_buffer) == 0


# -------------------------------------------------------- buffer pressure

def test_a_full_buffer_warns_before_overwriting(caplog):
    import logging
    m = _memory([msg(i) for i in range(3)], maxlen=3)
    with caplog.at_level(logging.WARNING, logger="memory.resilient_memory_system"):
        m._note_buffer_pressure()
    assert any('buffer full' in r.message.lower() for r in caplog.records)
    assert m.buffer_overflows == 1


def test_a_buffer_with_room_stays_quiet(caplog):
    import logging
    m = _memory([msg(0)], maxlen=10)
    with caplog.at_level(logging.WARNING, logger="memory.resilient_memory_system"):
        m._note_buffer_pressure()
    assert caplog.records == []


def test_overflow_warnings_are_rate_limited(caplog):
    import logging
    m = _memory([msg(i) for i in range(3)], maxlen=3)
    with caplog.at_level(logging.WARNING, logger="memory.resilient_memory_system"):
        for _ in range(150):
            m._note_buffer_pressure()
    warnings = [r for r in caplog.records if 'buffer full' in r.message.lower()]
    assert len(warnings) == 2, "first, then every hundredth"
    assert m.buffer_overflows == 150


# --------------------------- the buffer holds only what the DB did not take

async def test_a_successful_write_is_not_buffered():
    """The buffer received every message unconditionally and only drained on
    reconnect, so a long healthy stream filled it with already-saved data,
    warned about "dropping unsaved items" that were saved all along, and a
    recovery flush would have re-written up to a thousand duplicates."""
    m = _memory()
    m.db_available = True
    m.context_memory = {}
    _accept_all(m)
    await m.store_message({'username': 'v', 'message': 'saved fine', 'user_id': 'u'})
    assert len(m.stored) == 1
    assert len(m.memory_buffer) == 0, "saved items must not sit in the outage buffer"


async def test_a_failed_write_is_buffered():
    m = _memory()
    m.db_available = True
    m.context_memory = {}
    _reject_after(m, 0)
    await m.store_message({'username': 'v', 'message': 'db said no', 'user_id': 'u'})
    assert len(m.memory_buffer) == 1
    assert m.db_failures == 1


async def test_an_unavailable_db_buffers():
    m = _memory()
    m.db_available = False
    m.context_memory = {}
    _accept_all(m)
    await m.store_message({'username': 'v', 'message': 'offline', 'user_id': 'u'})
    assert m.stored == [], "no DB call while unavailable"
    assert len(m.memory_buffer) == 1


async def test_recovery_flush_writes_only_the_failures():
    """The duplication scenario end to end: healthy writes, an outage, then
    recovery. Only the outage items reach the flush."""
    m = _memory()
    m.context_memory = {}
    m.db_available = True
    _accept_all(m)
    await m.store_message({'username': 'v', 'message': 'before outage', 'user_id': 'u'})
    m.db_available = False
    await m.store_message({'username': 'v', 'message': 'during outage', 'user_id': 'u'})
    m.db_available = True
    await m._flush_buffer_to_database()
    assert [d['message'] for d in m.stored] == ['before outage', 'during outage']
    assert len(m.memory_buffer) == 0


async def test_the_memory_path_follows_the_same_rule():
    m = _memory()
    m.db_available = True
    m.context_memory = {}
    _accept_all(m)
    await m.store_memory({'key': 'k', 'value': 'saved'})
    assert len(m.memory_buffer) == 0
    m.db_available = False
    await m.store_memory({'key': 'k2', 'value': 'unsaved'})
    assert len(m.memory_buffer) == 1
