"""
Enhanced TTS Cache with SQLite persistence
PRD Migration Priority: TTS Cache Persistence
Target: 40%+ cache hit rate with voice variations
"""

import asyncio
import sqlite3
import hashlib
import json
import logging
import time
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from pathlib import Path
import aiosqlite

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Represents a cached TTS entry"""
    key: str
    text: str
    voice: str
    speed: float
    audio_data: bytes
    created_at: datetime
    last_accessed: datetime
    access_count: int
    size_bytes: int
    
    def to_dict(self) -> dict:
        """Convert to dictionary for storage"""
        return {
            'key': self.key,
            'text': self.text,
            'voice': self.voice,
            'speed': self.speed,
            'created_at': self.created_at.isoformat(),
            'last_accessed': self.last_accessed.isoformat(),
            'access_count': self.access_count,
            'size_bytes': self.size_bytes
        }


class TTSCacheSQLite:
    """
    SQLite-backed TTS cache with voice variations and pre-generation
    PRD Requirements:
    - Persistent cache across restarts
    - Voice variation storage
    - Pre-generate common responses
    - Target 40%+ cache hit rate
    """
    
    # Common responses to pre-generate on startup
    COMMON_RESPONSES = [
        # Greetings
        "Hello! Welcome to the stream!",
        "Hey there! How's it going?",
        "Welcome back everyone!",
        "Good to see you!",
        
        # Thanks
        "Thanks for watching!",
        "Thank you for the follow!",
        "Thanks for subscribing!",
        "I appreciate your support!",
        "Thanks for the bits!",
        "Thank you for the donation!",
        
        # Farewells
        "See you next time!",
        "Have a great day!",
        "Take care!",
        "Goodbye everyone!",
        
        # Engagement
        "That's a great question!",
        "Let me think about that...",
        "Interesting point!",
        "I see what you mean",
        "Good idea!",
        
        # Stream events
        "Welcome to the raid!",
        "Thanks for the raid!",
        "New follower! Welcome!",
        "Subscriber hype!",
        
        # Common reactions
        "That's awesome!",
        "No way!",
        "Absolutely!",
        "For sure!",
        "Let's go!"
    ]
    
    # Voice variations for pre-generation
    VOICE_VARIATIONS = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
    
    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        max_size_mb: int = 1000,
        ttl_hours: int = 168,  # 7 days
        target_hit_rate: float = 0.4
    ):
        """
        Initialize SQLite TTS cache
        
        Args:
            cache_dir: Directory for cache database
            max_size_mb: Maximum cache size in MB
            ttl_hours: Time-to-live for cache entries in hours
            target_hit_rate: Target cache hit rate (0.4 = 40%)
        """
        self.cache_dir = cache_dir or Path.home() / ".talkbot" / "tts_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.db_path = self.cache_dir / "tts_cache.db"
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.ttl_seconds = ttl_hours * 3600
        self.target_hit_rate = target_hit_rate
        
        # Statistics
        self.hits = 0
        self.misses = 0
        self.current_size = 0
        
        # Connection pool
        self.db: Optional[aiosqlite.Connection] = None
        
        logger.info(f"Initializing SQLite TTS cache at {self.db_path}")
        
    async def initialize(self):
        """Initialize database and tables"""
        self.db = await aiosqlite.connect(self.db_path)
        
        # Enable WAL mode for better concurrency
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA synchronous=NORMAL")
        
        # Create cache table
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS tts_cache (
                key TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                voice TEXT NOT NULL,
                speed REAL NOT NULL,
                audio_data BLOB NOT NULL,
                created_at TEXT NOT NULL,
                last_accessed TEXT NOT NULL,
                access_count INTEGER DEFAULT 1,
                size_bytes INTEGER NOT NULL
            )
        """)
        
        # Create indices for performance
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_last_accessed 
            ON tts_cache(last_accessed)
        """)
        
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_text_voice 
            ON tts_cache(text, voice)
        """)
        
        # Create statistics table
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS cache_stats (
                id INTEGER PRIMARY KEY,
                hits INTEGER DEFAULT 0,
                misses INTEGER DEFAULT 0,
                last_cleanup TEXT,
                last_pregeneration TEXT
            )
        """)
        
        await self.db.commit()
        
        # Load statistics
        await self._load_stats()
        
        # Calculate current cache size
        await self._calculate_cache_size()
        
        logger.info(f"TTS cache initialized with {self.current_size / 1024 / 1024:.1f}MB used")
        
    async def _load_stats(self):
        """Load statistics from database"""
        cursor = await self.db.execute("SELECT hits, misses FROM cache_stats WHERE id = 1")
        row = await cursor.fetchone()
        
        if row:
            self.hits, self.misses = row
        else:
            # Initialize stats
            await self.db.execute("""
                INSERT INTO cache_stats (id, hits, misses) 
                VALUES (1, 0, 0)
            """)
            await self.db.commit()
            
    async def _calculate_cache_size(self):
        """Calculate total cache size"""
        cursor = await self.db.execute("SELECT SUM(size_bytes) FROM tts_cache")
        row = await cursor.fetchone()
        self.current_size = row[0] if row[0] else 0
        
    def generate_key(self, text: str, voice: str = "alloy", speed: float = 1.0) -> str:
        """Generate unique cache key"""
        key_str = f"{text.lower().strip()}:{voice}:{speed}"
        return hashlib.sha256(key_str.encode()).hexdigest()
        
    async def get(
        self, 
        text: str, 
        voice: str = "alloy", 
        speed: float = 1.0,
        fuzzy_match: bool = True
    ) -> Optional[bytes]:
        """
        Retrieve cached audio
        
        Args:
            text: Text to look up
            voice: Voice model
            speed: Playback speed
            fuzzy_match: Enable fuzzy text matching
            
        Returns:
            Cached audio bytes or None
        """
        key = self.generate_key(text, voice, speed)
        
        # Try exact match first
        cursor = await self.db.execute("""
            SELECT audio_data, created_at FROM tts_cache 
            WHERE key = ?
        """, (key,))
        
        row = await cursor.fetchone()
        
        if row:
            audio_data, created_at = row
            created_dt = datetime.fromisoformat(created_at)
            
            # Check TTL
            if (datetime.now() - created_dt).total_seconds() < self.ttl_seconds:
                # Update access stats
                await self._update_access_stats(key)
                self.hits += 1
                await self._save_stats()

                logger.info(f"✓ CACHE HIT for: {text[:50]}... (hits={self.hits})")
                return audio_data
            else:
                # Expired - remove it
                await self.delete(key)
                
        # Try fuzzy matching if enabled
        if fuzzy_match:
            audio_data = await self._fuzzy_match(text, voice, speed)
            if audio_data:
                self.hits += 1
                await self._save_stats()
                return audio_data
                
        self.misses += 1
        await self._save_stats()
        logger.info(f"✗ CACHE MISS for: {text[:50]}... (misses={self.misses})")
        return None
        
    async def _fuzzy_match(self, text: str, voice: str, speed: float) -> Optional[bytes]:
        """
        Try fuzzy text matching for similar cached entries
        """
        # Search for similar text with same voice
        cursor = await self.db.execute("""
            SELECT key, text, audio_data, created_at 
            FROM tts_cache 
            WHERE voice = ? AND speed = ?
            ORDER BY last_accessed DESC
            LIMIT 100
        """, (voice, speed))
        
        rows = await cursor.fetchall()
        
        for key, cached_text, audio_data, created_at in rows:
            similarity = self._calculate_similarity(text, cached_text)
            if similarity > 0.85:  # 85% similarity threshold
                created_dt = datetime.fromisoformat(created_at)
                
                if (datetime.now() - created_dt).total_seconds() < self.ttl_seconds:
                    await self._update_access_stats(key)
                    logger.debug(f"Fuzzy cache hit ({similarity:.0%}) for: {text[:50]}...")
                    return audio_data
                    
        return None
        
    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """Calculate text similarity using Jaccard index"""
        text1 = text1.lower().strip()
        text2 = text2.lower().strip()
        
        if text1 == text2:
            return 1.0
            
        words1 = set(text1.split())
        words2 = set(text2.split())
        
        if not words1 or not words2:
            return 0.0
            
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        
        return len(intersection) / len(union) if union else 0.0
        
    async def put(
        self,
        text: str,
        audio_data: bytes,
        voice: str = "alloy",
        speed: float = 1.0
    ) -> bool:
        """
        Store audio in cache
        
        Args:
            text: Original text
            audio_data: Audio bytes
            voice: Voice model used
            speed: Playback speed
            
        Returns:
            True if stored successfully
        """
        key = self.generate_key(text, voice, speed)
        size_bytes = len(audio_data)
        
        # Check if we need to evict old entries
        if self.current_size + size_bytes > self.max_size_bytes:
            await self._evict_lru(size_bytes)
            
        try:
            now = datetime.now().isoformat()
            
            await self.db.execute("""
                INSERT OR REPLACE INTO tts_cache 
                (key, text, voice, speed, audio_data, created_at, 
                 last_accessed, access_count, size_bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """, (key, text, voice, speed, audio_data, now, now, size_bytes))
            
            await self.db.commit()
            
            self.current_size += size_bytes

            logger.info(f"✓ CACHED {size_bytes} bytes for: {text[:50]}... (voice={voice}, speed={speed})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to cache audio: {e}")
            return False
            
    async def _evict_lru(self, needed_bytes: int):
        """Evict least recently used entries to make space"""
        # Get LRU entries
        cursor = await self.db.execute("""
            SELECT key, size_bytes FROM tts_cache 
            ORDER BY last_accessed ASC
        """)
        
        rows = await cursor.fetchall()
        freed_bytes = 0
        keys_to_delete = []
        
        for key, size in rows:
            if freed_bytes >= needed_bytes:
                break
            keys_to_delete.append(key)
            freed_bytes += size
            
        # Delete entries
        if keys_to_delete:
            placeholders = ','.join('?' * len(keys_to_delete))
            await self.db.execute(
                f"DELETE FROM tts_cache WHERE key IN ({placeholders})",
                keys_to_delete
            )
            await self.db.commit()
            
            self.current_size -= freed_bytes
            logger.info(f"Evicted {len(keys_to_delete)} entries to free {freed_bytes / 1024:.1f}KB")
            
    async def _update_access_stats(self, key: str):
        """Update access statistics for a cache entry"""
        now = datetime.now().isoformat()
        
        await self.db.execute("""
            UPDATE tts_cache 
            SET last_accessed = ?, access_count = access_count + 1 
            WHERE key = ?
        """, (now, key))
        
        await self.db.commit()
        
    async def _save_stats(self):
        """Save hit/miss statistics"""
        await self.db.execute("""
            UPDATE cache_stats 
            SET hits = ?, misses = ? 
            WHERE id = 1
        """, (self.hits, self.misses))
        
        await self.db.commit()
        
    async def delete(self, key: str):
        """Delete a cache entry"""
        cursor = await self.db.execute(
            "SELECT size_bytes FROM tts_cache WHERE key = ?",
            (key,)
        )
        row = await cursor.fetchone()
        
        if row:
            size_bytes = row[0]
            await self.db.execute("DELETE FROM tts_cache WHERE key = ?", (key,))
            await self.db.commit()
            
            self.current_size -= size_bytes
            logger.debug(f"Deleted cache entry: {key}")
            
    async def pregenerate_common_responses(self, tts_callback):
        """
        Pre-generate common responses with voice variations
        
        Args:
            tts_callback: Async function to generate TTS (text, voice) -> bytes
        """
        logger.info("Pre-generating common TTS responses...")
        
        generated = 0
        skipped = 0
        
        for text in self.COMMON_RESPONSES:
            for voice in self.VOICE_VARIATIONS:
                # Check if already cached
                existing = await self.get(text, voice, fuzzy_match=False)
                
                if existing:
                    skipped += 1
                    continue
                    
                try:
                    # Generate TTS
                    audio_data = await tts_callback(text, voice)
                    
                    if audio_data:
                        await self.put(text, audio_data, voice)
                        generated += 1
                        
                        # Small delay to avoid rate limiting
                        await asyncio.sleep(0.5)
                        
                except Exception as e:
                    logger.warning(f"Failed to pre-generate '{text}' with {voice}: {e}")
                    
        # Update last pregeneration time
        now = datetime.now().isoformat()
        await self.db.execute("""
            UPDATE cache_stats 
            SET last_pregeneration = ? 
            WHERE id = 1
        """, (now,))
        await self.db.commit()
        
        logger.info(f"Pre-generation complete: {generated} generated, {skipped} already cached")
        
    async def cleanup_expired(self):
        """Remove expired cache entries"""
        cutoff = datetime.now() - timedelta(seconds=self.ttl_seconds)
        cutoff_str = cutoff.isoformat()
        
        # Get entries to delete
        cursor = await self.db.execute("""
            SELECT COUNT(*), SUM(size_bytes) 
            FROM tts_cache 
            WHERE created_at < ?
        """, (cutoff_str,))
        
        row = await cursor.fetchone()
        count, total_size = row[0] or 0, row[1] or 0
        
        if count > 0:
            # Delete expired entries
            await self.db.execute("""
                DELETE FROM tts_cache 
                WHERE created_at < ?
            """, (cutoff_str,))
            
            await self.db.commit()
            
            self.current_size -= total_size
            
            logger.info(f"Cleaned up {count} expired entries ({total_size / 1024:.1f}KB)")
            
        # Update last cleanup time
        now = datetime.now().isoformat()
        await self.db.execute("""
            UPDATE cache_stats 
            SET last_cleanup = ? 
            WHERE id = 1
        """, (now,))
        await self.db.commit()
        
    def get_hit_rate(self) -> float:
        """Calculate current cache hit rate"""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0
        
    async def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive cache statistics"""
        # Get entry count
        cursor = await self.db.execute("SELECT COUNT(*) FROM tts_cache")
        row = await cursor.fetchone()
        entry_count = row[0] if row else 0
        
        # Get top accessed entries
        cursor = await self.db.execute("""
            SELECT text, voice, access_count 
            FROM tts_cache 
            ORDER BY access_count DESC 
            LIMIT 10
        """)
        
        top_entries = []
        async for row in cursor:
            top_entries.append({
                'text': row[0][:50] + '...' if len(row[0]) > 50 else row[0],
                'voice': row[1],
                'access_count': row[2]
            })
            
        # Get voice distribution
        cursor = await self.db.execute("""
            SELECT voice, COUNT(*), SUM(size_bytes) 
            FROM tts_cache 
            GROUP BY voice
        """)
        
        voice_distribution = {}
        async for row in cursor:
            voice, count, size = row
            voice_distribution[voice] = {
                'count': count,
                'size_mb': size / 1024 / 1024 if size else 0
            }
            
        hit_rate = self.get_hit_rate()
        
        return {
            'hit_rate': f"{hit_rate:.1%}",
            'hits': self.hits,
            'misses': self.misses,
            'total_requests': self.hits + self.misses,
            'entry_count': entry_count,
            'cache_size_mb': self.current_size / 1024 / 1024,
            'max_size_mb': self.max_size_bytes / 1024 / 1024,
            'usage_percent': f"{(self.current_size / self.max_size_bytes * 100):.1f}%",
            'top_entries': top_entries,
            'voice_distribution': voice_distribution,
            'target_hit_rate': f"{self.target_hit_rate:.0%}",
            'target_met': hit_rate >= self.target_hit_rate
        }
        
    async def close(self):
        """Close database connection"""
        if self.db:
            await self.db.close()
            logger.info("TTS cache database closed")