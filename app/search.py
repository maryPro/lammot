"""
app/search.py — Sprint 1: Search Module
LammotBot Telegram Mini App Music Player

Wraps ytmusicapi to search YouTube Music for official tracks and, opt-in,
regular YouTube videos (the Extended tab).
Supports any language query: English, Russian, Ukrainian, etc.

The search mirrors YouTube Music's own engine:
  - Query by artist name  → returns that artist's popular songs
  - Query by track title  → returns songs with that title
  - Query by both         → returns best-matching tracks
"""

from __future__ import annotations

from typing import Optional

from ytmusicapi import YTMusic

# ---------------------------------------------------------------------------
# Module-level singleton — YTMusic() without credentials is enough for search.
# It uses the public, unauthenticated YouTube Music API.
# ---------------------------------------------------------------------------
_ytmusic: Optional[YTMusic] = None


# ---------------------------------------------------------------------------
# Extended video search limits
# ---------------------------------------------------------------------------
VIDEO_MIN_DURATION_SECONDS = 45
VIDEO_MAX_DURATION_SECONDS = 900
VIDEO_RAW_LIMIT = 25


def _get_client() -> YTMusic:
    """Return a shared (lazy-initialised) YTMusic client instance."""
    global _ytmusic
    if _ytmusic is None:
        _ytmusic = YTMusic()
    return _ytmusic


def get_client() -> YTMusic:
    """Public wrapper around the shared client, used by app.artist."""
    return _get_client()


# ---------------------------------------------------------------------------
# Duration helpers
# ---------------------------------------------------------------------------

def _parse_duration(raw: str | int | None) -> int:
    """
    Convert a 'MM:SS' or 'H:MM:SS' duration string to total seconds.

    Returns 0 if the value is missing or unparseable (defensive, never raises).
    """
    if not raw:
        return 0
    if isinstance(raw, (int, float)):
        return max(0, int(raw))
    try:
        parts = raw.strip().split(":")
        parts = [int(p) for p in parts]
        if len(parts) == 2:          # MM:SS
            return parts[0] * 60 + parts[1]
        if len(parts) == 3:          # H:MM:SS
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
    except (ValueError, AttributeError):
        pass
    return 0


# ---------------------------------------------------------------------------
# Thumbnail helpers
# ---------------------------------------------------------------------------

def _best_thumbnail(thumbnails: list[dict] | None) -> str:
    """
    Pick the highest-resolution thumbnail URL from a ytmusicapi thumbnail list.

    ytmusicapi returns thumbnails sorted from smallest to largest, so we take
    the last element.  Falls back to an empty string if none are available.
    """
    if not thumbnails:
        return ""
    # Last item is always the largest available resolution
    return thumbnails[-1].get("url", "")


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def search_songs(query: str, limit: int = 10) -> list[dict]:
    """
    Search YouTube Music for official tracks matching *query*.

    Parameters
    ----------
    query : str
        Free-text search string in any language.  Can be:
          - an artist name   ("Земфира")       → popular songs by that artist
          - a track title    ("Воскресенье")   → songs with that title
          - artist + title   ("Земфира Хочешь") → best-matching track
    limit : int
        Maximum number of results to return (default 10).

    Returns
    -------
    list[dict]
        Each dict contains:
          - video_id        (str)  — YouTube video ID, always 11 characters
          - title           (str)  — track title
          - artist          (str)  — primary artist name
          - artist_id       (str|None) — artist channel browse id
          - duration_seconds(int)  — track length in seconds (0 if unknown)
          - thumbnail_url   (str)  — HTTPS URL of the best-resolution thumbnail

    Notes
    -----
    - filter="songs" ensures only official label-uploaded tracks are returned.
      Covers, remixes, and fan uploads that are not classified as songs by
      YouTube Music are excluded automatically.
    - Results are ranked by YouTube Music's own relevance engine, so a bare
      artist query ("Земфира") will surface that artist's most popular songs.
    - Individual parse errors are caught per-result so one malformed item
      does not discard the entire batch.  Network / API errors from
      ytmusicapi are NOT swallowed: they propagate so the API layer can
      return 502 instead of pretending there are no results.
    """
    if not query or not query.strip():
        return []

    client = _get_client()

    raw_results: list[dict] = client.search(
        query=query.strip(),
        filter="songs",   # official tracks only — no covers, no remixes
        limit=limit,
    )

    songs: list[dict] = []

    for item in raw_results:
        if len(songs) >= limit:
            break

        try:
            video_id: str = item.get("videoId") or ""
            # Skip items without a valid video ID (e.g. shelf headers)
            if not video_id or len(video_id) != 11:
                continue

            title: str = item.get("title") or ""

            # artists is a list of dicts: [{"id": ..., "name": "Земфира"}, ...]
            artists_raw: list[dict] = item.get("artists") or []
            artist: str = artists_raw[0].get("name", "") if artists_raw else ""
            artist_id: Optional[str] = artists_raw[0].get("id") if artists_raw else None

            # duration is a formatted string like "3:45"
            duration_seconds: int = _parse_duration(item.get("duration"))

            thumbnail_url: str = _best_thumbnail(item.get("thumbnails"))

            songs.append(
                {
                    "video_id": video_id,
                    "title": title,
                    "artist": artist,
                    "artist_id": artist_id or None,
                    "duration_seconds": duration_seconds,
                    "thumbnail_url": thumbnail_url,
                }
            )

        except Exception:
            # Defensive: a single broken result must never kill the whole search
            continue

    return songs


# ---------------------------------------------------------------------------
# Extended search — regular YouTube videos
# ---------------------------------------------------------------------------

def _video_duration(item: dict) -> int:
    return _parse_duration(item.get("length") or item.get("duration"))


def _is_live(item: dict) -> bool:
    return bool(item.get("isLive") or item.get("live") or item.get("isUpcoming"))


def _video_artist(item: dict) -> str:
    artists_raw = item.get("artists") or []
    artist = artists_raw[0].get("name") if artists_raw else None
    return artist or item.get("channel") or "Unknown Artist"


def search_videos(query: str, limit: int = 10) -> list[dict]:
    """
    Search YouTube for regular videos (remixes, slowed versions, fan uploads).

    Extended search is opt-in: the default Songs tab keeps using
    ``filter="songs"``.  Results are limited to a 45–900 second window with a
    known duration; live, upcoming and malformed items are skipped.

    Upstream ytmusicapi errors are not swallowed: they propagate so the API
    layer can return 502.
    """
    if not query or not query.strip():
        return []

    client = _get_client()
    raw_results: list[dict] = client.search(
        query=query.strip(),
        filter="videos",
        limit=VIDEO_RAW_LIMIT,
    )

    videos: list[dict] = []

    for item in raw_results:
        if len(videos) >= limit:
            break

        try:
            video_id: str = item.get("videoId") or ""
            if not video_id or len(video_id) != 11:
                continue

            duration_seconds = _video_duration(item)
            if duration_seconds <= 0:
                continue
            if duration_seconds < VIDEO_MIN_DURATION_SECONDS:
                continue
            if duration_seconds > VIDEO_MAX_DURATION_SECONDS:
                continue
            if _is_live(item):
                continue

            videos.append(
                {
                    "video_id": video_id,
                    "title": item.get("title") or "",
                    "artist": _video_artist(item),
                    "artist_id": None,
                    "duration_seconds": duration_seconds,
                    "thumbnail_url": _best_thumbnail(
                        item.get("thumbnails") or item.get("thumbnail")
                    ),
                    "kind": "video",
                }
            )

        except Exception:
            # Defensive: one malformed item must not fail the whole batch
            continue

    return videos
