"""
app/artist.py — Artist search, artist pages and album pages.

Metadata comes only from YouTube Music (ytmusicapi). Three public functions
return the JSON shapes the Mini App consumes:

  - search_artists(query, limit)  → artist cards for the Songs shelf
  - get_artist_page(browse_id)    → artist header + popular/albums/singles
  - get_album_page(browse_id)     → album header + ordered tracks

Upstream ytmusicapi errors are not swallowed: they propagate so the API layer
can return 502 instead of pretending the page is empty. Malformed or empty
pages raise the two *NotFoundError classes, which the API maps to 404.

A small process-local TTL cache keeps repeated page opens cheap:
artist search 5 min, artist page 15 min, album page 1 hour.
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.search import _best_thumbnail, _parse_duration, get_client

# ---------------------------------------------------------------------------
# Limits and cache policy
# ---------------------------------------------------------------------------
ARTIST_SEARCH_TTL_SECONDS = 5 * 60
ARTIST_PAGE_TTL_SECONDS = 15 * 60
ALBUM_PAGE_TTL_SECONDS = 60 * 60

ARTIST_SEARCH_MIN_LIMIT = 1
ARTIST_SEARCH_MAX_LIMIT = 5
ARTIST_SEARCH_DEFAULT_LIMIT = 3

POPULAR_LIMIT = 5
ALBUM_SHELF_LIMIT = 10
SINGLE_SHELF_LIMIT = 10

CACHE_MAX_ENTRIES = 512

# Monotonic time → payload. Insertion order is the eviction order.
_cache: Dict[str, Tuple[float, Any]] = {}
_cache_lock = threading.Lock()


class ArtistNotFoundError(LookupError):
    """The upstream response has no usable artist page (API → 404)."""


class AlbumNotFoundError(LookupError):
    """The upstream response has no usable album page (API → 404)."""


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------
def clear_cache() -> None:
    """Drop every cached page (tests and manual cache resets)."""
    with _cache_lock:
        _cache.clear()


def _store(key: str, value: Any, ttl: float, now: float) -> None:
    if len(_cache) >= CACHE_MAX_ENTRIES:
        expired = [k for k, (expires_at, _) in _cache.items() if expires_at <= now]
        for old in expired:
            _cache.pop(old, None)
        while len(_cache) >= CACHE_MAX_ENTRIES:
            _cache.pop(next(iter(_cache)), None)
    _cache[key] = (now + ttl, value)


def _cached(
    key: str,
    ttl: float,
    loader: Callable[[], Any],
    stats: Optional[Dict[str, str]],
) -> Any:
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None and entry[0] > now:
            if stats is not None:
                stats["cache"] = "hit"
            return entry[1]
        if entry is not None:
            _cache.pop(key, None)
    if stats is not None:
        stats["cache"] = "miss"
    value = loader()
    with _cache_lock:
        _store(key, value, ttl, now)
    return value


def _search_cache_key(query: str, limit: int) -> str:
    digest = hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()[:16]
    return f"artist_search:{digest}:{limit}"


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------
def _first_artist(item: Dict[str, Any]) -> Dict[str, Any]:
    artists = item.get("artists") or []
    return artists[0] if artists and isinstance(artists[0], dict) else {}


def _normalize_track(
    item: Dict[str, Any],
    fallback_thumbnail: str = "",
    fallback_artist: str = "",
    fallback_artist_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """One public track shape, or None when the item cannot be played."""
    if not isinstance(item, dict):
        return None
    video_id = item.get("videoId") or ""
    if not video_id:
        return None

    artist = _first_artist(item)
    duration = item.get("duration_seconds")
    if not duration:
        duration = _parse_duration(item.get("duration"))

    return {
        "video_id": video_id,
        "title": item.get("title") or "",
        "artist": artist.get("name") or item.get("artist") or fallback_artist,
        "artist_id": artist.get("id") or fallback_artist_id or None,
        "duration_seconds": int(duration or 0),
        "thumbnail_url": _best_thumbnail(item.get("thumbnails")) or fallback_thumbnail,
    }


def _normalize_tracks(
    items: Any,
    limit: int,
    fallback_thumbnail: str = "",
    fallback_artist: str = "",
    fallback_artist_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    tracks: List[Dict[str, Any]] = []
    for item in items or []:
        if len(tracks) >= limit:
            break
        track = _normalize_track(item, fallback_thumbnail, fallback_artist, fallback_artist_id)
        if track is not None:
            tracks.append(track)
    return tracks


def _normalize_artist_card(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    browse_id = item.get("browseId") or ""
    name = item.get("artist") or item.get("title") or ""
    if not browse_id or not name:
        return None
    return {
        "browse_id": browse_id,
        "name": name,
        "thumbnail_url": _best_thumbnail(item.get("thumbnails")),
    }


def _normalize_album_card(
    item: Dict[str, Any], default_type: str = ""
) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    browse_id = item.get("browseId") or ""
    if not browse_id:
        return None
    return {
        "browse_id": browse_id,
        "title": item.get("title") or "",
        "year": item.get("year") or "",
        "type": item.get("type") or default_type,
        "thumbnail_url": _best_thumbnail(item.get("thumbnails")),
    }


def _normalize_album_cards(
    items: Any, limit: int, default_type: str = ""
) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    for item in items or []:
        if len(cards) >= limit:
            break
        card = _normalize_album_card(item, default_type)
        if card is not None:
            cards.append(card)
    return cards


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def search_artists(
    query: str, limit: int = ARTIST_SEARCH_DEFAULT_LIMIT, stats: Optional[Dict[str, str]] = None
) -> List[Dict[str, Any]]:
    """
    Search YouTube Music for artists matching *query*.

    Returns at most *limit* cards (1–5, default 3). Empty results are [].
    Upstream failures propagate to the API layer (502).
    """
    if not query or not query.strip():
        return []

    limit = max(ARTIST_SEARCH_MIN_LIMIT, min(int(limit), ARTIST_SEARCH_MAX_LIMIT))
    key = _search_cache_key(query, limit)

    def load() -> List[Dict[str, Any]]:
        raw = get_client().search(query=query.strip(), filter="artists", limit=limit)
        cards: List[Dict[str, Any]] = []
        for item in raw or []:
            if len(cards) >= limit:
                break
            card = _normalize_artist_card(item)
            if card is not None:
                cards.append(card)
        return cards

    return _cached(key, ARTIST_SEARCH_TTL_SECONDS, load, stats)


def get_artist_page(
    browse_id: str, stats: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Artist header plus Popular (5), Albums (10) and Singles and EPs (10).

    Raises ArtistNotFoundError when the upstream page has no artist name.
    """
    key = f"artist:{browse_id}"

    def load() -> Dict[str, Any]:
        raw = get_client().get_artist(browse_id)
        if not isinstance(raw, dict):
            raise ArtistNotFoundError(browse_id)
        name = raw.get("name") or ""
        if not name:
            raise ArtistNotFoundError(browse_id)

        artist_thumbnail = _best_thumbnail(raw.get("thumbnails"))
        artist = {
            "browse_id": browse_id,
            "name": name,
            "thumbnail_url": artist_thumbnail,
        }

        songs = raw.get("songs") or {}
        albums = raw.get("albums") or {}
        singles = raw.get("singles") or {}

        return {
            "artist": artist,
            "popular": _normalize_tracks(
                songs.get("results"), POPULAR_LIMIT, artist_thumbnail, name, browse_id
            ),
            "albums": _normalize_album_cards(
                albums.get("results"), ALBUM_SHELF_LIMIT, "Album"
            ),
            "singles": _normalize_album_cards(
                singles.get("results"), SINGLE_SHELF_LIMIT, "Single"
            ),
        }

    return _cached(key, ARTIST_PAGE_TTL_SECONDS, load, stats)


def get_album_page(
    browse_id: str, stats: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Album header plus its tracks in release order.

    Raises AlbumNotFoundError when the upstream page has no title.
    """
    key = f"album:{browse_id}"

    def load() -> Dict[str, Any]:
        raw = get_client().get_album(browse_id)
        if not isinstance(raw, dict):
            raise AlbumNotFoundError(browse_id)
        title = raw.get("title") or ""
        if not title:
            raise AlbumNotFoundError(browse_id)

        artist = _first_artist(raw)
        artist_name = artist.get("name") or raw.get("artist") or ""
        artist_id = artist.get("id") or None
        cover = _best_thumbnail(raw.get("thumbnails"))

        return {
            "album": {
                "browse_id": browse_id,
                "title": title,
                "artist": artist_name,
                "artist_id": artist_id,
                "year": raw.get("year") or "",
                "type": raw.get("type") or "",
                "thumbnail_url": cover,
            },
            "tracks": _normalize_tracks(
                raw.get("tracks"), 10_000, cover, artist_name, artist_id
            ),
        }

    return _cached(key, ALBUM_PAGE_TTL_SECONDS, load, stats)
