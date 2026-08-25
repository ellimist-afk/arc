"""The warm context cache must expire, and invalidation must reach it.

`l2_ttl = 300` was declared and never applied: LRUCache had no age limit, so
entries left only by LRU eviction at maxsize=100. On a channel with fewer
than 100 distinct chatters -- every channel this runs on -- nothing was ever
evicted, and a viewer's context stayed frozen at whatever it was on their
first message for the whole stream.

invalidate_cache() then only cleared L1, so the stale L2 copy was promoted
straight back into L1 on the next read, making invalidation a no-op that
looked like it worked.
"""
import pytest

from bot.optimized_context_builder import LRUCache, OptimizedContextBuilder


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# --------------------------------------------------------------- LRUCache

def test_entries_expire_once_past_the_ttl():
    c = Clock()
    cache = LRUCache(maxsize=10, ttl=300, clock=c)
    cache.put('k', {'v': 1})
    c.t += 299
    assert cache.get('k') == {'v': 1}
    c.t += 2
    assert cache.get('k') is None, "past the TTL the entry must be gone"
    assert cache.expirations == 1


def test_expired_entry_is_dropped_not_just_hidden():
    c = Clock()
    cache = LRUCache(maxsize=10, ttl=10, clock=c)
    cache.put('k', 1)
    c.t += 20
    cache.get('k')
    assert 'k' not in cache.cache


def test_no_ttl_means_the_old_unbounded_behaviour():
    c = Clock()
    cache = LRUCache(maxsize=10, clock=c)
    cache.put('k', 1)
    c.t += 10_000
    assert cache.get('k') == 1


def test_refreshing_a_key_restarts_its_clock():
    c = Clock()
    cache = LRUCache(maxsize=10, ttl=100, clock=c)
    cache.put('k', 1)
    c.t += 80
    cache.put('k', 2)          # rewritten: age resets
    c.t += 80
    assert cache.get('k') == 2


def test_lru_eviction_still_works():
    cache = LRUCache(maxsize=2, ttl=300, clock=Clock())
    cache.put('a', 1)
    cache.put('b', 2)
    cache.put('c', 3)
    assert cache.get('a') is None and cache.get('c') == 3


def test_hit_and_miss_accounting():
    c = Clock()
    cache = LRUCache(maxsize=5, ttl=10, clock=c)
    cache.put('k', 1)
    cache.get('k')             # hit
    cache.get('absent')        # miss
    c.t += 20
    cache.get('k')             # expired -> miss
    assert cache.hits == 1 and cache.misses == 2


def test_delete_prefix_removes_only_matching_keys():
    cache = LRUCache(maxsize=10, ttl=300, clock=Clock())
    for key in ('ch:alice:general', 'ch:alice:mention', 'ch:bob:general', 'other:alice:x'):
        cache.put(key, key)
    assert cache.delete_prefix('ch:alice:') == 2
    assert cache.get('ch:bob:general') == 'ch:bob:general'
    assert cache.get('other:alice:x') == 'other:alice:x'
    assert cache.get('ch:alice:general') is None


def test_clear_empties_everything():
    cache = LRUCache(maxsize=10, ttl=300, clock=Clock())
    cache.put('a', 1)
    cache.clear()
    assert cache.get('a') is None and len(cache.cache) == 0


# ------------------------------------------------------------ invalidation

@pytest.fixture
def builder():
    b = OptimizedContextBuilder(memory_system=None)
    return b


def _seed(b, key, value):
    import time
    b.l1_cache[key] = (value, time.time())
    b.l2_cache.put(key, value)


def test_l2_is_constructed_with_the_declared_ttl(builder):
    assert builder.l2_cache.ttl == builder.l2_ttl == 300


def test_invalidating_a_viewer_clears_both_tiers(builder):
    _seed(builder, 'ch:alice:general', {'stale': True})
    _seed(builder, 'ch:bob:general', {'keep': True})

    builder.invalidate_cache(viewer='alice', channel='ch')

    assert 'ch:alice:general' not in builder.l1_cache
    assert builder.l2_cache.get('ch:alice:general') is None, \
        "a stale L2 entry is promoted back into L1 on the next read"
    assert builder.l2_cache.get('ch:bob:general') == {'keep': True}


def test_invalidating_a_channel_clears_both_tiers(builder):
    _seed(builder, 'ch:alice:general', {'a': 1})
    _seed(builder, 'ch:bob:general', {'b': 2})
    _seed(builder, 'other:carol:general', {'c': 3})

    builder.invalidate_cache(channel='ch')

    assert not [k for k in builder.l1_cache if k.startswith('ch:')]
    assert 'other:carol:general' in builder.l1_cache, "another channel must be untouched"
    assert builder.l2_cache.get('ch:alice:general') is None
    assert builder.l2_cache.get('ch:bob:general') is None
    assert builder.l2_cache.get('other:carol:general') == {'c': 3}


def test_invalidating_everything_clears_both_tiers(builder):
    _seed(builder, 'ch:alice:general', {'a': 1})
    _seed(builder, 'other:bob:general', {'b': 2})

    builder.invalidate_cache()

    assert builder.l1_cache == {}
    assert builder.l2_cache.get('ch:alice:general') is None
    assert builder.l2_cache.get('other:bob:general') is None


def test_invalidation_survives_an_empty_cache(builder):
    builder.invalidate_cache(viewer='nobody', channel='ch')
    builder.invalidate_cache(channel='ch')
    builder.invalidate_cache()


def test_l2_hit_still_promotes_into_l1(builder):
    """The promotion path is what made a stale L2 entry so damaging; it must
    still work for entries that are legitimately warm."""
    builder.l2_cache.put('ch:alice:general', {'v': 1})
    assert builder._check_l2_cache('ch:alice:general') == {'v': 1}
    assert 'ch:alice:general' in builder.l1_cache
