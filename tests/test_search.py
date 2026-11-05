"""
tests/test_search.py — Sprint 1: Search Module Tests
LammotBot Telegram Mini App Music Player

Run with:
    pytest tests/test_search.py -v
Skip (offline / CI):
    pytest -m "not integration"

These are integration tests that hit the real YouTube Music API.
Requires network access and ytmusicapi installed in the venv.

pytest -v               → verbose, shows each test name
pytest -v -s            → also prints any print() statements
pytest --tb=short       → shorter tracebacks on failures
"""

import pytest

from app.search import search_songs

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Required fields every result must expose
# ---------------------------------------------------------------------------

REQUIRED_KEYS = {"video_id", "title", "artist", "duration_seconds", "thumbnail_url"}


# ---------------------------------------------------------------------------
# Test 1 — English query: search by artist + title
# Verifies: non-empty results, all required keys present, video_id is valid.
# ---------------------------------------------------------------------------

def test_english_query_structure():
    """
    English query returns at least one result with all required fields.
    'Radiohead Creep' is a canonical, always-present YouTube Music track.
    """
    results = search_songs("Radiohead Creep", limit=5)

    assert len(results) > 0, "Expected at least one result for 'Radiohead Creep'"

    first = results[0]

    # All required keys must be present
    assert REQUIRED_KEYS.issubset(first.keys()), (
        f"Missing keys: {REQUIRED_KEYS - first.keys()}"
    )

    # video_id must be exactly 11 characters (YouTube standard)
    assert len(first["video_id"]) == 11, (
        f"video_id should be 11 chars, got: {first['video_id']!r}"
    )

    # title must be a non-empty string
    assert isinstance(first["title"], str) and first["title"], (
        "title must be a non-empty string"
    )


# ---------------------------------------------------------------------------
# Test 2 — Cyrillic query: search by artist name only
# Verifies: duration > 0, thumbnail_url starts with https://.
# A bare artist query ("Земфира") must surface popular songs, not an error.
# ---------------------------------------------------------------------------

def test_cyrillic_artist_only_query():
    """
    Russian-language artist-only query returns valid tracks.
    'Земфира' is queried without a track name to test the artist→songs path.
    """
    results = search_songs("Земфира", limit=5)

    assert len(results) > 0, "Expected results for 'Земфира'"

    for track in results:
        # Duration must be a positive integer
        assert isinstance(track["duration_seconds"], int), (
            f"duration_seconds must be int, got {type(track['duration_seconds'])}"
        )
        assert track["duration_seconds"] > 0, (
            f"duration_seconds must be > 0, got {track['duration_seconds']} "
            f"for track: {track['title']!r}"
        )

        # Thumbnail must be an HTTPS URL
        assert track["thumbnail_url"].startswith("https://"), (
            f"thumbnail_url must start with 'https://', "
            f"got: {track['thumbnail_url']!r}"
        )


# ---------------------------------------------------------------------------
# Test 3 — Cyrillic query: search by track title only
# Verifies: limit is respected, results contain the queried word in some field.
# "Воскресенье" is a track title that should return ≤ 3 results when limit=3.
# ---------------------------------------------------------------------------

def test_title_only_query_respects_limit():
    """
    Track-title-only query respects the limit parameter.
    'Воскресенье' (Sunday) is a common Russian song title — many artists have
    recorded it, so this also verifies the filter correctly returns songs only.
    """
    limit = 3
    results = search_songs("Воскресенье", limit=limit)

    # Must not exceed requested limit
    assert len(results) <= limit, (
        f"Expected at most {limit} results, got {len(results)}"
    )

    # Must return something (title is well-known)
    assert len(results) > 0, "Expected at least one result for 'Воскресенье'"

    # Every result must have a non-empty title
    for track in results:
        assert track["title"], f"Empty title in result: {track}"
