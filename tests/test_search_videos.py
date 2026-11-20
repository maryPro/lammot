"""
tests/test_search_videos.py — Extended search: regular YouTube videos.

No network: ytmusicapi is replaced by a fake client patched where
app.search looks it up.
"""

from unittest.mock import MagicMock, patch

from app.search import VIDEO_RAW_LIMIT, search_videos


def _client_returning(items):
    client = MagicMock()
    client.search.return_value = items
    return client


def _video(**overrides):
    item = {
        "videoId": "XEinHUvu1Pc",
        "title": "Roger Sanchez - Lost (Remix) slowed",
        "channel": "KIRMAN",
        "length": "9:29",
        "thumbnails": [{"url": "https://i.ytimg.com/vi/x/hq.jpg"}],
    }
    item.update(overrides)
    return item


def test_search_videos_requests_the_videos_filter():
    client = _client_returning([])
    with patch("app.search._get_client", return_value=client):
        search_videos("kirman lost")

    client.search.assert_called_once_with(
        query="kirman lost", filter="videos", limit=VIDEO_RAW_LIMIT
    )


def test_video_result_shape_has_a_kind():
    client = _client_returning([_video()])
    with patch("app.search._get_client", return_value=client):
        results = search_videos("kirman lost")

    assert results == [
        {
            "video_id": "XEinHUvu1Pc",
            "title": "Roger Sanchez - Lost (Remix) slowed",
            "artist": "KIRMAN",
            "duration_seconds": 569,
            "thumbnail_url": "https://i.ytimg.com/vi/x/hq.jpg",
            "kind": "video",
        }
    ]


def test_short_video_is_excluded():
    client = _client_returning([_video(length="0:20")])
    with patch("app.search._get_client", return_value=client):
        assert search_videos("kirman lost") == []


def test_video_longer_than_fifteen_minutes_is_excluded():
    client = _client_returning([_video(length="15:01")])
    with patch("app.search._get_client", return_value=client):
        assert search_videos("kirman lost") == []


def test_exactly_fifteen_minutes_is_included():
    client = _client_returning([_video(length="15:00")])
    with patch("app.search._get_client", return_value=client):
        results = search_videos("kirman lost")

    assert [r["duration_seconds"] for r in results] == [900]


def test_unknown_duration_is_excluded():
    client = _client_returning([_video(length=None), _video(length=None, duration=0)])
    with patch("app.search._get_client", return_value=client):
        assert search_videos("kirman lost") == []


def test_live_and_upcoming_videos_are_excluded():
    client = _client_returning(
        [
            _video(isLive=True),
            _video(live=True),
            _video(isUpcoming=True),
            _video(videoId="bbbbbbbbbbb"),
        ]
    )
    with patch("app.search._get_client", return_value=client):
        results = search_videos("kirman lost")

    assert [r["video_id"] for r in results] == ["bbbbbbbbbbb"]


def test_channel_is_used_when_artists_are_missing():
    client = _client_returning([_video(channel="KIRMAN")])
    with patch("app.search._get_client", return_value=client):
        assert search_videos("kirman lost")[0]["artist"] == "KIRMAN"


def test_artists_take_priority_over_channel():
    client = _client_returning([_video(artists=[{"name": "Roger Sanchez"}])])
    with patch("app.search._get_client", return_value=client):
        assert search_videos("kirman lost")[0]["artist"] == "Roger Sanchez"


def test_missing_artist_and_channel_fall_back_to_unknown():
    item = _video()
    item.pop("channel")
    client = _client_returning([item])
    with patch("app.search._get_client", return_value=client):
        assert search_videos("kirman lost")[0]["artist"] == "Unknown Artist"


def test_items_without_a_valid_video_id_are_skipped():
    client = _client_returning([_video(videoId="short"), _video(videoId=None), _video()])
    with patch("app.search._get_client", return_value=client):
        results = search_videos("kirman lost")

    assert [r["video_id"] for r in results] == ["XEinHUvu1Pc"]


def test_limit_is_applied_after_filtering():
    items = [_video(videoId=f"vid{i:08d}") for i in range(6)]
    client = _client_returning(items)
    with patch("app.search._get_client", return_value=client):
        results = search_videos("kirman lost", limit=3)

    assert len(results) == 3


def test_empty_query_returns_nothing_without_a_client_call():
    client = _client_returning([_video()])
    with patch("app.search._get_client", return_value=client) as get_client:
        assert search_videos("   ") == []

    get_client.assert_not_called()
    client.search.assert_not_called()


def test_upstream_errors_propagate():
    client = MagicMock()
    client.search.side_effect = RuntimeError("network down")
    with patch("app.search._get_client", return_value=client):
        try:
            search_videos("kirman lost")
        except RuntimeError as exc:
            assert "network down" in str(exc)
        else:  # pragma: no cover - the assertion below is the real check
            assert False, "expected the upstream error to propagate"
