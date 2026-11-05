"""tests/test_radio.py — Sprint 3: radio queue (ytmusicapi is mocked)."""

from unittest.mock import MagicMock, patch

import pytest

from app.radio import get_radio_queue


def _client_returning(tracks):
    client = MagicMock()
    client.get_watch_playlist.return_value = {"tracks": tracks}
    return client


def test_seed_track_is_not_returned():
    """get_watch_playlist puts the seed first — it is already playing, skip it."""
    tracks = [
        {"videoId": "seed", "title": "Seed", "artists": [{"name": "Artist S"}], "length": "3:00"},
        {"videoId": "next", "title": "Next", "artists": [{"name": "Artist N"}], "length": "2:30"},
    ]
    with patch("app.radio._get_client", return_value=_client_returning(tracks)):
        queue = get_radio_queue("seed", history=[], limit=5)

    assert [t["video_id"] for t in queue] == ["next"]
    assert queue[0]["duration_seconds"] == 150


def test_history_deduplication():
    tracks = [
        {"videoId": "track1", "title": "Title 1", "artists": [{"name": "Artist A"}], "length": "3:00"},
        {"videoId": "track2", "title": "Title 2", "artists": [{"name": "Artist B"}], "length": "4:00"},
        {"videoId": "track3", "title": "Title 3", "artists": [{"name": "Artist C"}], "length": "5:00"},
        {"videoId": "track3", "title": "Title 3", "artists": [{"name": "Artist D"}], "length": "5:00"},
    ]
    with patch("app.radio._get_client", return_value=_client_returning(tracks)):
        queue = get_radio_queue("track1", history=["track2"], limit=5)

    # track1 = seed, track2 = in history, second track3 = duplicate
    assert [t["video_id"] for t in queue] == ["track3"]
    assert queue[0]["duration_seconds"] == 300


def test_history_is_optional():
    tracks = [{"videoId": "a1", "title": "A", "artists": [{"name": "A"}]}]
    with patch("app.radio._get_client", return_value=_client_returning(tracks)):
        assert len(get_radio_queue("seed")) == 1


def test_diversity_filter():
    tracks = [
        {"videoId": "v1", "title": "T1", "artists": [{"name": "Artist A"}]},  # + window [A]
        {"videoId": "v2", "title": "T2", "artists": [{"name": "Artist A"}]},  # skip
        {"videoId": "v3", "title": "T3", "artists": [{"name": "Artist B"}]},  # + [A, B]
        {"videoId": "v4", "title": "T4", "artists": [{"name": "Artist C"}]},  # + [A, B, C]
        {"videoId": "v5", "title": "T5", "artists": [{"name": "Artist A"}]},  # skip
        {"videoId": "v6", "title": "T6", "artists": [{"name": "Artist D"}]},  # + [B, C, D]
        {"videoId": "v7", "title": "T7", "artists": [{"name": "Artist A"}]},  # + allowed again
    ]
    with patch("app.radio._get_client", return_value=_client_returning(tracks)):
        queue = get_radio_queue("seed", history=[], limit=5)

    assert [t["video_id"] for t in queue] == ["v1", "v3", "v4", "v6", "v7"]


def test_upstream_error_propagates():
    """Errors must reach the API (502), not silently become an empty queue."""
    client = MagicMock()
    client.get_watch_playlist.side_effect = Exception("network down")
    with patch("app.radio._get_client", return_value=client):
        with pytest.raises(Exception, match="network down"):
            get_radio_queue("seed")
