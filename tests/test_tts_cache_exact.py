"""A TTS cache hit must be audio of the exact sentence asked for.

The cache used to fall back to any entry whose word set overlapped above
0.85. One changed word in a ten-word line ("not") still scores 0.91, so the
bot could say the opposite of what it meant. Plain lookups are exact now;
fuzzy matching is opt-in.
"""
from audio.tts_cache_sqlite import TTSCacheSQLite

SAID = "that was honestly a really good play from you there chat"
ASKED = "that was honestly not a really good play from you there chat"


async def _cache(tmp_path):
    cache = TTSCacheSQLite(cache_dir=tmp_path)
    await cache.initialize()
    return cache


async def test_one_changed_word_is_a_miss(tmp_path):
    cache = await _cache(tmp_path)
    try:
        assert await cache.put(SAID, b"audio-of-said", voice="nova")
        assert await cache.get(ASKED, voice="nova") is None
    finally:
        await cache.close()


async def test_the_same_sentence_still_hits(tmp_path):
    cache = await _cache(tmp_path)
    try:
        assert await cache.put(SAID, b"audio-of-said", voice="nova")
        assert await cache.get(SAID, voice="nova") == b"audio-of-said"
    finally:
        await cache.close()
