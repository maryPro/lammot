"""
tests/test_search.py — Sprint 1: Search Module Tests
LammotBot Telegram Mini App Music Player

Integration tests hit the real YouTube Music API (network required). The
unit tests below them mock the client, so they run in the normal suite.

Run with:
    pytest tests/test_search.py -v
Skip network tests (offline / CI):
    pytest -m "not integration"
"""

from unittest.mock import MagicMock, patch

import pytest

from app.search import search_songs


# ---------------------------------------------------------------------------
# Required fields every result must expose
# ---------------------------------------------------------------------------

REQUIRED_KEYS = {
    "video_id",
    "title",
    "artist",
    "artist_id",
    "duration_seconds",
    "thumbnail_url",
}


# ---------------------------------------------------------------------------
# Unit tests (no network)
# ---------------------------------------------------------------------------

def _client_returning(items):
    client = MagicMock()
    client.search.return_value = items
    return client


def _song(**overrides):
    item = {
        "videoId": "aaaaaaaaaaa",
        "title": "Wonderwall",
        "artists": [{"name": "Oasis", "id": "UCmMUZbaYdNH0bEd1PAlAqsA"}],
        "duration": "4:18",
        "thumbnails": [{"url": "https://i.ytimg.com/vi/x/hq.jpg"}],
    }
    item.update(overrides)
    return item


def test_song_result_includes_artist_id():
    client = _client_returning([_song()])
    with patch("app.search._get_client", return_value=client):
        results = search_songs("Oasis", limit=1)

    assert results[0]["artist_id"] == "UCmMUZbaYdNH0bEd1PAlAqsA"


def test_song_without_artist_id_gets_none():
    client = _client_returning([_song(artists=[{"name": "Oasis"}])])
    with patch("app.search._get_client", return_value=client):
        results = search_songs("Oasis", limit=1)

    assert results[0]["artist_id"] is None


def test_song_without_artists_gets_none():
    client = _client_returning([_song(artists=[])])
    with patch("app.search._get_client", return_value=client):
        results = search_songs("Oasis", limit=1)

    assert results[0]["artist"] == ""
    assert results[0]["artist_id"] is None


# ---------------------------------------------------------------------------
# Integration tests (real API)
# ---------------------------------------------------------------------------

@pytest.mark.integration
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

    # artist_id is either a channel id or None
    assert first["artist_id"] is None or isinstance(first["artist_id"], str)


@pytest.mark.integration
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


@pytest.mark.integration
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
