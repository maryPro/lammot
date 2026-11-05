"""
app/cache.py — In-memory TTL cache for stream URLs
LammotBot Telegram Mini App Music Player

Simple dict-based cache with per-entry expiry timestamps.
No external dependencies (no Redis, no memcached).

Usage:
    from app.cache import cache_get, cache_set

    url = cache_get("dQw4w9WgXcQ")
    if url is None:
        url = resolve_expensive(...)
        cache_set("dQw4w9WgXcQ", url, ttl=7200)

Why TTL = 2 hours:
    YouTube CDN URLs expire in ~6 hours.  Using 2 h gives a 4-hour safety
    margin so clients never receive a stale URL that stops mid-playback.
"""

from __future__ import annotations

import time
from typing import Optional

# ---------------------------------------------------------------------------
# Internal storage: { key: (value, expires_at_unix_timestamp) }
# ---------------------------------------------------------------------------
_cache: dict[str, tuple[str, float]] = {}

# Upper bound on entries so memory cannot grow without limit when many
# different tracks are played and never requested again (lazy eviction alone
# would keep them forever).
MAX_ENTRIES: int = 5000


def _evict_if_full() -> None:
    """Drop expired entries; if still full, drop the oldest-inserted ones."""
    if len(_cache) < MAX_ENTRIES:
        return
    now = time.time()
    for k in [k for k, (_, exp) in _cache.items() if exp <= now]:
        del _cache[k]
    while len(_cache) >= MAX_ENTRIES:
        # dicts keep insertion order -> first key is the oldest
        del _cache[next(iter(_cache))]


def cache_get(key: str) -> Optional[str]:
    """
    Return the cached value for *key*, or None if missing / expired.

    Expired entries are evicted lazily on access (no background sweeper needed
    for the small number of concurrent streams expected at this scale).
    """
    entry = _cache.get(key)
    if entry is None:
        return None

    value, expires_at = entry
    if time.time() > expires_at:
        # Lazy eviction — remove the stale entry immediately
        del _cache[key]
        return None

    return value


def cache_set(key: str, value: str, ttl: float = 7200.0) -> None:
    """
    Store *value* under *key* with an expiry of *ttl* seconds from now.

    Parameters
    ----------
    key : str
        Cache key (typically a YouTube video_id).
    value : str
        Value to store (typically a CDN stream URL).
    ttl : float
        Time-to-live in seconds.  Default is 7 200 s (2 hours).
    """
    _cache.pop(key, None)  # re-insert so the key becomes the newest
    _evict_if_full()
    _cache[key] = (value, time.time() + ttl)


def cache_clear() -> None:
    """Remove all entries from the cache.  Useful in tests."""
    _cache.clear()
