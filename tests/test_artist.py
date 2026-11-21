"""tests/test_artist.py — artist search, artist page and album page.

No network: the ytmusicapi client is mocked where ``app.artist`` looks it up.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.artist import (
    AlbumNotFoundError,
    ArtistNotFoundError,
    clear_cache,
    get_album_page,
    get_artist_page,
    search_artists,
)

ARTIST_ID = "UCmMUZbaYdNH0bEd1PAlAqsA"
ALBUM_ID = "MPREb_AYetWMZunqA"


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_cache()
    yield
    clear_cache()


def _client(**methods):
    client = MagicMock()
    for name, value in methods.items():
        getattr(client, name).return_value = value
    return client


def _track(video_id="ZrOKjDZOtkA", **overrides):
    item = {
        "videoId": video_id,
        "title": "Wonderwall",
        "artists": [{"name": "Oasis", "id": ARTIST_ID}],
        "album": "Morning Glory",
        "duration": "4:18",
        "duration_seconds": 258,
        "thumbnails": [{"url": "https://i.ytimg.com/track.jpg"}],
    }
    item.update(overrides)
    return item


# ------------------------------------------------------------ artist search
def test_search_artists_maps_cards():
    client = _client(
        search=[
            {
                "browseId": ARTIST_ID,
                "artist": "Oasis",
                "thumbnails": [
                    {"url": "https://i.ytimg.com/small.jpg"},
                    {"url": "https://i.ytimg.com/large.jpg"},
                ],
            }
        ]
    )
    with patch("app.artist.get_client", return_value=client):
        results = search_artists("Oasis", limit=3)

    assert results == [
        {
            "browse_id": ARTIST_ID,
            "name": "Oasis",
            "thumbnail_url": "https://i.ytimg.com/large.jpg",
        }
    ]
    client.search.assert_called_once_with(query="Oasis", filter="artists", limit=3)


def test_search_artists_caps_and_skips_invalid_items():
    items = [
        {"browseId": "UCaaaaaaaa1", "artist": "One", "thumbnails": []},
        {"browseId": "", "artist": "No id"},
        {"browseId": "UCaaaaaaaa3", "title": "Title fallback", "thumbnails": []},
        {"browseId": "UCaaaaaaaa4", "artist": "", "title": ""},
        {"browseId": "UCaaaaaaaa5", "artist": "Five"},
    ]
    client = _client(search=items)
    with patch("app.artist.get_client", return_value=client):
        results = search_artists("q", limit=3)

    assert [r["name"] for r in results] == ["One", "Title fallback", "Five"]
    assert len(results) == 3


def test_search_artists_empty_query_never_calls_upstream():
    client = _client(search=[])
    with patch("app.artist.get_client", return_value=client):
        assert search_artists("   ") == []
    client.search.assert_not_called()


# -------------------------------------------------------------- artist page
def test_artist_page_sections_and_thumbnail_fallback():
    raw = {
        "name": "Oasis",
        "thumbnails": [{"url": "https://i.ytimg.com/artist.jpg"}],
        "songs": {
            "results": [
                _track(),
                _track(video_id="bbbbbbbbbbb", thumbnails=[], duration="3:00", duration_seconds=None),
            ]
        },
        "albums": {
            "results": [
                {
                    "browseId": ALBUM_ID,
                    "title": "Morning Glory",
                    "year": "1995",
                    "type": "Album",
                    "thumbnails": [{"url": "https://i.ytimg.com/album.jpg"}],
                }
            ]
        },
        "singles": {
            "results": [
                {
                    "browseId": "MPREb_single1",
                    "title": "Stand By Me",
                    "year": "2016",
                    "thumbnails": [],
                }
            ]
        },
    }
    client = _client(get_artist=raw)
    with patch("app.artist.get_client", return_value=client):
        page = get_artist_page(ARTIST_ID)

    assert page["artist"] == {
        "browse_id": ARTIST_ID,
        "name": "Oasis",
        "thumbnail_url": "https://i.ytimg.com/artist.jpg",
    }

    first, second = page["popular"]
    assert first == {
        "video_id": "ZrOKjDZOtkA",
        "title": "Wonderwall",
        "artist": "Oasis",
        "artist_id": ARTIST_ID,
        "duration_seconds": 258,
        "thumbnail_url": "https://i.ytimg.com/track.jpg",
    }
    assert second["duration_seconds"] == 180
    assert second["thumbnail_url"] == "https://i.ytimg.com/artist.jpg"

    assert page["albums"] == [
        {
            "browse_id": ALBUM_ID,
            "title": "Morning Glory",
            "year": "1995",
            "type": "Album",
            "thumbnail_url": "https://i.ytimg.com/album.jpg",
        }
    ]
    assert page["singles"] == [
        {
            "browse_id": "MPREb_single1",
            "title": "Stand By Me",
            "year": "2016",
            "type": "Single",
            "thumbnail_url": "",
        }
    ]
    client.get_artist.assert_called_once_with(ARTIST_ID)


def test_artist_page_missing_sections_become_empty_lists():
    raw = {"name": "Oasis", "thumbnails": [{"url": "https://i.ytimg.com/a.jpg"}]}
    client = _client(get_artist=raw)
    with patch("app.artist.get_client", return_value=client):
        page = get_artist_page(ARTIST_ID)

    assert page["popular"] == [] and page["albums"] == [] and page["singles"] == []


def test_artist_page_without_a_name_is_not_found():
    client = _client(get_artist={"thumbnails": []})
    with patch("app.artist.get_client", return_value=client):
        with pytest.raises(ArtistNotFoundError):
            get_artist_page(ARTIST_ID)


def test_artist_page_cache_hits_upstream_once():
    raw = {"name": "Oasis", "thumbnails": [], "songs": {"results": [_track()]}}
    client = _client(get_artist=raw)
    stats = {}
    with patch("app.artist.get_client", return_value=client):
        get_artist_page(ARTIST_ID, stats=stats)
        assert stats["cache"] == "miss"
        again = get_artist_page(ARTIST_ID, stats=stats)

    assert stats["cache"] == "hit"
    assert again["popular"][0]["video_id"] == "ZrOKjDZOtkA"
    client.get_artist.assert_called_once_with(ARTIST_ID)


def test_artist_page_upstream_errors_propagate():
    client = MagicMock()
    client.get_artist.side_effect = RuntimeError("network down")
    with patch("app.artist.get_client", return_value=client):
        with pytest.raises(RuntimeError, match="network down"):
            get_artist_page(ARTIST_ID)


# --------------------------------------------------------------- album page
def test_album_page_normalizes_tracks_and_drops_invalid():
    raw = {
        "title": "Morning Glory",
        "type": "Album",
        "year": "1995",
        "artists": [{"name": "Oasis", "id": ARTIST_ID}],
        "thumbnails": [{"url": "https://i.ytimg.com/cover.jpg"}],
        "tracks": [
            _track().copy(),
            {"videoId": "", "title": "No id", "thumbnails": [{"url": "https://x"}]},
            {"title": "Also no id"},
        ],
    }
    raw["tracks"][0]["thumbnails"] = []
    client = _client(get_album=raw)
    with patch("app.artist.get_client", return_value=client):
        page = get_album_page(ALBUM_ID)

    assert page["album"] == {
        "browse_id": ALBUM_ID,
        "title": "Morning Glory",
        "artist": "Oasis",
        "artist_id": ARTIST_ID,
        "year": "1995",
        "type": "Album",
        "thumbnail_url": "https://i.ytimg.com/cover.jpg",
    }
    assert page["tracks"] == [
        {
            "video_id": "ZrOKjDZOtkA",
            "title": "Wonderwall",
            "artist": "Oasis",
            "artist_id": ARTIST_ID,
            "duration_seconds": 258,
            "thumbnail_url": "https://i.ytimg.com/cover.jpg",
        }
    ]
    client.get_album.assert_called_once_with(ALBUM_ID)


def test_album_page_parses_duration_string_when_seconds_are_missing():
    raw = {
        "title": "Album",
        "artists": [{"name": "A", "id": "UCaaaaaaaa1"}],
        "thumbnails": [],
        "tracks": [_track(duration="5:03", duration_seconds=None)],
    }
    client = _client(get_album=raw)
    with patch("app.artist.get_client", return_value=client):
        page = get_album_page(ALBUM_ID)

    assert page["tracks"][0]["duration_seconds"] == 303


def test_album_page_without_a_title_is_not_found():
    client = _client(get_album={"tracks": []})
    with patch("app.artist.get_client", return_value=client):
        with pytest.raises(AlbumNotFoundError):
            get_album_page(ALBUM_ID)


def test_album_page_cache_hits_upstream_once():
    raw = {
        "title": "Album",
        "artists": [{"name": "A", "id": "UCaaaaaaaa1"}],
        "thumbnails": [],
        "tracks": [],
    }
    client = _client(get_album=raw)
    stats = {}
    with patch("app.artist.get_client", return_value=client):
        get_album_page(ALBUM_ID, stats=stats)
        get_album_page(ALBUM_ID, stats=stats)

    assert stats["cache"] == "hit"
    client.get_album.assert_called_once_with(ALBUM_ID)


def test_album_page_upstream_errors_propagate():
    client = MagicMock()
    client.get_album.side_effect = RuntimeError("network down")
    with patch("app.artist.get_client", return_value=client):
        with pytest.raises(RuntimeError, match="network down"):
            get_album_page(ALBUM_ID)
