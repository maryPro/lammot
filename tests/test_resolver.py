"""
tests/test_resolver.py — Sprint 2: Stream Resolver tests
LammotBot Telegram Mini App Music Player

Tests for app/resolver.py and app/cache.py.

All tests use unittest.mock to patch yt_dlp.YoutubeDL so that:
  - No real network requests are made
  - Tests are deterministic and fast (< 1 ms per test)

Covered scenarios:
  1. Preferred format itag 251 is returned when available
  2. Fallback format itag 140 is returned when 251 is absent
  3. ValueError is raised when neither itag is available
  4. ValueError is raised for an empty video_id
  5. Cache hit: second call returns cached URL without calling yt-dlp again
  6. URL shape: starts with https:// and contains 'googlevideo'
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

from app.cache import cache_clear
from app import resolver
from app.resolver import get_stream_url, _PREFERRED_ITAG, _FALLBACK_ITAG, _ydl_opts

# ---------------------------------------------------------------------------
# Shared fake CDN URLs
# ---------------------------------------------------------------------------
_FAKE_URL_251 = (
    "https://rr1---sn-abc123.googlevideo.com/videoplayback"
    "?expire=9999999999&itag=251&mime=audio/webm"
)
_FAKE_URL_140 = (
    "https://rr2---sn-xyz456.googlevideo.com/videoplayback"
    "?expire=9999999999&itag=140&mime=audio/mp4"
)
_FAKE_URL_251_EARLY = (
    "https://rr3---sn-early.googlevideo.com/videoplayback"
    "?expire=9999999999&itag=251&mime=audio/webm"
)
_FAKE_URL_251_LATE = (
    "https://rr4---sn-late.googlevideo.com/videoplayback"
    "?expire=9999999999&itag=251&mime=audio/webm"
)

# A minimal format list that yt-dlp would return
_FORMATS_BOTH = [
    {"format_id": "251", "url": _FAKE_URL_251, "acodec": "opus"},
    {"format_id": "140", "url": _FAKE_URL_140, "acodec": "mp4a.40.2"},
]
_FORMATS_140_ONLY = [
    {"format_id": "140", "url": _FAKE_URL_140, "acodec": "mp4a.40.2"},
]
_FORMATS_NONE = [
    {"format_id": "18", "url": "https://rr1---sn-abc.googlevideo.com/vp?itag=18", "acodec": "mp4a.40.2"},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ydl_mock(formats: list[dict]) -> MagicMock:
    """
    Build a MagicMock that quacks like yt_dlp.YoutubeDL used as a context manager.

    ydl.extract_info(url, download=False) → {"formats": formats}
    """
    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl.extract_info.return_value = {"formats": formats}
    return mock_ydl


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_cache():
    """Wipe the in-memory cache before every test for full isolation."""
    cache_clear()
    yield
    cache_clear()


# ---------------------------------------------------------------------------
# Tests: format selection
# ---------------------------------------------------------------------------

class TestFormatSelection:
    """get_stream_url picks the right itag from the yt-dlp format list."""

    def test_preferred_itag_251_returned_when_available(self):
        """When both formats are present, itag 251 (Opus) wins."""
        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=_make_ydl_mock(_FORMATS_BOTH)):
            url = get_stream_url("dQw4w9WgXcQ")

        assert url == _FAKE_URL_251

    def test_fallback_itag_140_when_251_absent(self):
        """When only itag 140 is available, we fall back to M4A/AAC."""
        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=_make_ydl_mock(_FORMATS_140_ONLY)):
            url = get_stream_url("dQw4w9WgXcQ")

        assert url == _FAKE_URL_140

    def test_raises_value_error_when_no_supported_format(self):
        """If neither itag 251 nor 140 is present, ValueError is raised."""
        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=_make_ydl_mock(_FORMATS_NONE)):
            with pytest.raises(ValueError, match="No supported audio format"):
                get_stream_url("dQw4w9WgXcQ")


# ---------------------------------------------------------------------------
# Tests: URL shape
# ---------------------------------------------------------------------------

class TestUrlShape:
    """The resolved URL must meet the CDN shape contract."""

    def test_url_starts_with_https(self):
        """CDN stream URL must use HTTPS."""
        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=_make_ydl_mock(_FORMATS_BOTH)):
            url = get_stream_url("dQw4w9WgXcQ")

        assert url.startswith("https://"), f"URL should start with https://, got: {url!r}"

    def test_url_contains_googlevideo(self):
        """CDN stream URL must be served from googlevideo.com."""
        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=_make_ydl_mock(_FORMATS_BOTH)):
            url = get_stream_url("dQw4w9WgXcQ")

        assert "googlevideo" in url, f"URL should contain 'googlevideo', got: {url!r}"

    def test_url_starts_with_https_fallback(self):
        """Fallback URL (itag 140) also uses HTTPS."""
        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=_make_ydl_mock(_FORMATS_140_ONLY)):
            url = get_stream_url("dQw4w9WgXcQ")

        assert url.startswith("https://")
        assert "googlevideo" in url


# ---------------------------------------------------------------------------
# Tests: input validation
# ---------------------------------------------------------------------------

class TestInputValidation:
    """get_stream_url validates its input before calling yt-dlp."""

    def test_raises_for_empty_string(self):
        """Empty video_id must raise ValueError immediately."""
        with pytest.raises(ValueError):
            get_stream_url("")

    def test_raises_for_whitespace_only(self):
        """Whitespace-only video_id must raise ValueError immediately."""
        with pytest.raises(ValueError):
            get_stream_url("   ")

    @pytest.mark.parametrize(
        "bad_id",
        ["short", "dQw4w9WgXcQx", "https://evil.example/x", "dQw4w9WgXc!"],
    )
    def test_rejects_malformed_video_id_without_calling_ytdlp(self, bad_id):
        """Anything that is not an 11-char YouTube id never reaches yt-dlp."""
        mock_ydl = _make_ydl_mock(_FORMATS_BOTH)
        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=mock_ydl):
            with pytest.raises(ValueError, match="Invalid YouTube video_id"):
                get_stream_url(bad_id)
        mock_ydl.extract_info.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: caching behaviour
# ---------------------------------------------------------------------------

class TestCaching:
    """The in-memory cache must be used correctly."""

    def test_cache_miss_calls_ytdlp(self):
        """On first call (cold cache), yt-dlp is invoked exactly once."""
        mock_ydl = _make_ydl_mock(_FORMATS_BOTH)

        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=mock_ydl):
            get_stream_url("dQw4w9WgXcQ")

        mock_ydl.extract_info.assert_called_once()

    def test_cache_hit_skips_ytdlp(self):
        """On second call with the same video_id, yt-dlp is NOT called again."""
        mock_ydl = _make_ydl_mock(_FORMATS_BOTH)

        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=mock_ydl):
            url_first  = get_stream_url("dQw4w9WgXcQ")
            url_second = get_stream_url("dQw4w9WgXcQ")

        # yt-dlp was only called once despite two get_stream_url calls
        mock_ydl.extract_info.assert_called_once()
        assert url_first == url_second

    def test_different_video_ids_resolved_independently(self):
        """Two different video IDs each trigger their own yt-dlp call."""
        mock_ydl = _make_ydl_mock(_FORMATS_BOTH)

        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=mock_ydl):
            get_stream_url("dQw4w9WgXcQ")
            get_stream_url("9bZkp7q19f0")

        assert mock_ydl.extract_info.call_count == 2

    def test_cache_clear_forces_re_resolve(self):
        """After cache_clear(), the next call must hit yt-dlp again."""
        mock_ydl = _make_ydl_mock(_FORMATS_BOTH)

        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=mock_ydl):
            get_stream_url("dQw4w9WgXcQ")  # populates cache
            cache_clear()
            get_stream_url("dQw4w9WgXcQ")  # should re-resolve

        assert mock_ydl.extract_info.call_count == 2


# ---------------------------------------------------------------------------
# Tests: YouTube availability gate
# ---------------------------------------------------------------------------

class TestAvailabilityGate:
    """
    YouTube CDN answers 403 for a freshly resolved URL until ``available_at``
    (the pre-roll ad length). The resolver must not hand out the URL earlier,
    otherwise /audio gets 403 and the browser cannot play the track.
    """

    def test_waits_until_format_available_at(self):
        """A short pre-roll gate is still waited out before returning the URL."""
        now = 1_800_000_000
        formats = [{"format_id": "251", "url": _FAKE_URL_251, "available_at": now + 1.5}]

        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=_make_ydl_mock(formats)), \
             patch("time.time", return_value=now), \
             patch("time.sleep") as sleep:
            url = get_stream_url("dQw4w9WgXcQ")

        sleep.assert_called_once()
        assert sleep.call_args.args[0] == pytest.approx(1.5)
        assert url == _FAKE_URL_251

    def test_gate_longer_than_the_cap_fails_fast(self):
        """A 5 s pre-roll must not hold a thread pool worker: fail fast instead."""
        now = 1_800_000_000
        formats = [{"format_id": "251", "url": _FAKE_URL_251, "available_at": now + 5}]

        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=_make_ydl_mock(formats)), \
             patch("time.time", return_value=now), \
             patch("time.sleep") as sleep:
            with pytest.raises(resolver.FormatUnavailableError, match="not available yet"):
                get_stream_url("dQw4w9WgXcQ")

        sleep.assert_not_called()

    def test_no_wait_when_available_at_is_absent_or_past(self):
        """No gate (or an already passed one) must not add any delay."""
        now = 1_800_000_000
        formats = [
            {"format_id": "251", "url": _FAKE_URL_251},
            {"format_id": "140", "url": _FAKE_URL_140, "available_at": now - 30},
        ]

        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=_make_ydl_mock(formats)), \
             patch("time.time", return_value=now), \
             patch("time.sleep") as sleep:
            url = get_stream_url("dQw4w9WgXcQ")

        sleep.assert_not_called()
        assert url == _FAKE_URL_251

    def test_far_future_available_at_is_rejected(self):
        """A premiere hours away must fail fast instead of holding a thread."""
        now = 1_800_000_000
        formats = [{"format_id": "251", "url": _FAKE_URL_251, "available_at": now + 3600}]

        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=_make_ydl_mock(formats)), \
             patch("time.time", return_value=now), \
             patch("time.sleep") as sleep:
            with pytest.raises(ValueError, match="not available yet"):
                get_stream_url("dQw4w9WgXcQ")

        sleep.assert_not_called()

    def test_earliest_duplicate_of_same_itag_is_selected(self):
        """yt-dlp merges clients: prefer the 251 duplicate with the earliest gate."""
        now = 1_800_000_000
        formats = [
            {"format_id": "251", "url": _FAKE_URL_251_EARLY, "available_at": now + 2},
            {"format_id": "251", "url": _FAKE_URL_251_LATE, "available_at": now + 20},
        ]

        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=_make_ydl_mock(formats)), \
             patch("time.time", return_value=now), \
             patch("time.sleep") as sleep:
            url = get_stream_url("dQw4w9WgXcQ")

        assert url == _FAKE_URL_251_EARLY
        sleep.assert_called_once()
        assert sleep.call_args.args[0] == pytest.approx(2)

    def test_duplicate_without_gate_beats_a_later_duplicate(self):
        """A duplicate without available_at must win and cause no waiting."""
        now = 1_800_000_000
        formats = [
            {"format_id": "251", "url": _FAKE_URL_251_EARLY},
            {"format_id": "251", "url": _FAKE_URL_251_LATE, "available_at": now + 20},
        ]

        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=_make_ydl_mock(formats)), \
             patch("time.time", return_value=now), \
             patch("time.sleep") as sleep:
            url = get_stream_url("dQw4w9WgXcQ")

        assert url == _FAKE_URL_251_EARLY
        sleep.assert_not_called()

    def test_itag_preference_beats_an_earlier_fallback_itag(self):
        """251 stays preferred when its gate is too small to be worth avoiding."""
        now = 1_800_000_000
        formats = [
            {"format_id": "251", "url": _FAKE_URL_251_LATE},
            {"format_id": "140", "url": _FAKE_URL_140},
        ]

        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=_make_ydl_mock(formats)), \
             patch("time.time", return_value=now), \
             patch("time.sleep"):
            url = get_stream_url("dQw4w9WgXcQ")

        assert url == _FAKE_URL_251_LATE

    def test_switches_to_aac_when_opus_gate_is_large(self):
        """A long Opus gate is skipped in favour of an immediately playable AAC."""
        now = 1_800_000_000
        formats = [
            {"format_id": "251", "url": _FAKE_URL_251_LATE, "available_at": now + 5},
            {"format_id": "140", "url": _FAKE_URL_140},
        ]

        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=_make_ydl_mock(formats)), \
             patch("time.time", return_value=now), \
             patch("time.sleep") as sleep:
            url = get_stream_url("dQw4w9WgXcQ")

        assert url == _FAKE_URL_140
        sleep.assert_not_called()

    def test_falls_back_to_default_when_embedded_is_gated(self):
        """Embedded is tried alone first; default is added only when it fails."""
        now = 1_800_000_000
        gated = [{"format_id": "251", "url": _FAKE_URL_251_LATE, "available_at": now + 6}]
        ungated = [{"format_id": "251", "url": _FAKE_URL_251_EARLY}]
        calls = []

        def fake_ydl(opts):
            calls.append(opts["extractor_args"]["youtube"]["player_client"])
            return _make_ydl_mock(gated if len(calls) == 1 else ungated)

        with patch("app.resolver.yt_dlp.YoutubeDL", side_effect=fake_ydl), \
             patch("time.time", return_value=now), \
             patch("time.sleep") as sleep:
            url = get_stream_url("dQw4w9WgXcQ")

        assert url == _FAKE_URL_251_EARLY
        assert calls == [["web_embedded"], ["web_embedded", "default"]]
        sleep.assert_not_called()

    def test_no_fallback_pass_when_embedded_is_ready(self):
        """The production regression: a usable embedded result must not be re-resolved."""
        calls = []

        def fake_ydl(opts):
            calls.append(opts["extractor_args"]["youtube"]["player_client"])
            return _make_ydl_mock(_FORMATS_BOTH)

        with patch("app.resolver.yt_dlp.YoutubeDL", side_effect=fake_ydl):
            url = get_stream_url("dQw4w9WgXcQ")

        assert url == _FAKE_URL_251
        assert calls == [["web_embedded"]]

    def test_concurrent_calls_resolve_once(self):
        """Single-flight: concurrent prefetch + click share one extraction."""
        calls = 0
        calls_lock = threading.Lock()

        def fake_extract(url, download=False):
            nonlocal calls
            with calls_lock:
                calls += 1
            time.sleep(0.1)
            return {"formats": _FORMATS_BOTH}

        mock_ydl = _make_ydl_mock(_FORMATS_BOTH)
        mock_ydl.extract_info.side_effect = fake_extract

        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=mock_ydl):
            with ThreadPoolExecutor(max_workers=8) as pool:
                urls = list(pool.map(lambda _: get_stream_url("dQw4w9WgXcQ"), range(8)))

        assert calls == 1
        assert len(set(urls)) == 1
        assert urls[0] == _FAKE_URL_251


class TestClientOptions:
    def test_default_pass_uses_only_the_embedded_client(self):
        """Embedded has no pre-roll gate, so it is the cheap first attempt."""
        clients = _ydl_opts()["extractor_args"]["youtube"]["player_client"]
        assert clients == ["web_embedded"]

    def test_fallback_pass_adds_the_default_client(self):
        """Videos where embedding is disabled still get the default clients."""
        assert resolver._fallback_clients() == ("web_embedded", "default")

    def test_embedded_stage_uses_only_the_embedded_client(self):
        clients = _ydl_opts(["web_embedded"])["extractor_args"]["youtube"]["player_client"]
        assert clients == ["web_embedded"]

    def test_no_format_hint_is_passed_to_ytdlp(self):
        """We select itag 251/140 ourselves; a format hint breaks SABR clients."""
        assert "format" not in _ydl_opts()


# ---------------------------------------------------------------------------
# Error classification, cache policy, cookie accounts, bot-check gate
# ---------------------------------------------------------------------------

class TestErrorClassification:
    def test_bot_check_is_classified_and_not_retried(self):
        def fake_ydl(_opts):
            raise __import__("yt_dlp").utils.DownloadError(
                "Sign in to confirm you're not a bot"
            )

        with patch("app.resolver.yt_dlp.YoutubeDL", side_effect=fake_ydl) as mock:
            with pytest.raises(resolver.BotCheckError):
                get_stream_url("dQw4w9WgXcQ")

        assert mock.call_count == 1

    def test_private_video_is_unavailable_and_not_retried(self):
        def fake_ydl(_opts):
            raise __import__("yt_dlp").utils.DownloadError("Private video")

        with patch("app.resolver.yt_dlp.YoutubeDL", side_effect=fake_ydl) as mock:
            with pytest.raises(resolver.VideoUnavailableError):
                get_stream_url("dQw4w9WgXcQ")

        assert mock.call_count == 1

    def test_unknown_ytdlp_error_is_transient_and_retried(self):
        def fake_ydl(_opts):
            raise __import__("yt_dlp").utils.DownloadError("connection reset")

        with patch("app.resolver.yt_dlp.YoutubeDL", side_effect=fake_ydl) as mock:
            with pytest.raises(resolver.TransientResolveError):
                get_stream_url("dQw4w9WgXcQ")

        assert mock.call_count == 2


class TestCachePolicy:
    def test_ttl_is_derived_from_expire_with_safety_margin(self):
        now = 1_800_000_000
        url = "https://rr1---sn.googlevideo.com/videoplayback?expire=1800003600"
        with patch("app.resolver.time.time", return_value=now):
            assert resolver._ttl_for_url(url) == pytest.approx(1800)

    def test_short_lived_url_is_not_cached(self):
        now = 1_800_000_000
        url = "https://rr1---sn.googlevideo.com/videoplayback?expire=1800000060"
        with patch("app.resolver.time.time", return_value=now):
            assert resolver._ttl_for_url(url) == 0

    def test_ttl_is_capped_and_falls_back_for_missing_expire(self):
        now = 1_800_000_000
        long_url = "https://rr1---sn.googlevideo.com/videoplayback?expire=1800100000"
        with patch("app.resolver.time.time", return_value=now):
            assert resolver._ttl_for_url(long_url) == resolver._STREAM_TTL_MAX
            assert resolver._ttl_for_url("https://rr1---sn.googlevideo.com/videoplayback") == resolver._STREAM_TTL

    def test_negative_cache_avoids_a_second_extraction(self):
        with patch(
            "app.resolver._extract_formats",
            side_effect=resolver.VideoUnavailableError("gone"),
        ) as extract:
            with pytest.raises(resolver.VideoUnavailableError):
                get_stream_url("dQw4w9WgXcQ")
            with pytest.raises(resolver.VideoUnavailableError):
                get_stream_url("dQw4w9WgXcQ")

        assert extract.call_count == 1


class TestCookieAccounts:
    def test_bot_check_switches_to_the_reserve_file(self, tmp_path, monkeypatch):
        primary = tmp_path / "cookies.txt"
        reserve = tmp_path / "cookies-reserve.txt"
        primary.write_text("# primary")
        reserve.write_text("# reserve")
        monkeypatch.setenv("YTDLP_COOKIES_FILE", str(primary))
        monkeypatch.setenv("YTDLP_RESERVE_COOKIES_FILE", str(reserve))

        calls = []

        def fake_extract(video_id, clients, cookiefile=None):
            calls.append(cookiefile)
            if cookiefile == str(primary):
                raise resolver.BotCheckError("bot")
            return _FORMATS_BOTH

        with patch("app.resolver._extract_formats", side_effect=fake_extract):
            url = get_stream_url("dQw4w9WgXcQ")

        assert url == _FAKE_URL_251
        assert calls == [str(primary), str(reserve)]
        assert resolver.get_state()["active_account"] == "reserve"

    def test_verify_cookiefile_uses_an_explicit_path(self, tmp_path):
        path = str(tmp_path / "cookies.txt")
        with patch("app.resolver._extract_formats", return_value=_FORMATS_BOTH) as extract:
            assert resolver.verify_cookiefile(path, "dQw4w9WgXcQ") is True

        assert extract.call_args.kwargs["cookiefile"] == path


class TestBotCheckGate:
    def test_active_bot_check_short_circuits_without_ytdlp(self):
        resolver.set_status("bot_check")
        with patch("app.resolver._extract_formats") as extract:
            with pytest.raises(resolver.BotCheckError):
                get_stream_url("dQw4w9WgXcQ")
        extract.assert_not_called()

    def test_force_bypasses_the_gate(self):
        resolver.set_status("bot_check")
        with patch("app.resolver._extract_formats", return_value=_FORMATS_BOTH):
            assert get_stream_url("dQw4w9WgXcQ", force=True) == _FAKE_URL_251

    def test_reset_state_clears_the_gate(self):
        resolver.set_status("bot_check")
        resolver.reset_state()
        with patch("app.resolver._extract_formats", return_value=_FORMATS_BOTH):
            assert get_stream_url("dQw4w9WgXcQ") == _FAKE_URL_251


class TestOptionalOptions:
    def test_cache_dir_and_pot_url_are_passed_only_when_configured(self, monkeypatch):
        monkeypatch.setenv("YTDLP_CACHE_DIR", "/data/cache")
        monkeypatch.setenv("BGUTIL_BASE_URL", "http://bgutil.railway.internal:4416")
        opts = _ydl_opts()
        assert opts["cachedir"] == "/data/cache"
        assert opts["extractor_args"]["youtubepot-bgutilhttp"]["base_url"] == [
            "http://bgutil.railway.internal:4416"
        ]


def test_cached_url_is_served_even_during_bot_check():
    from app.cache import cache_set

    resolver.set_status("bot_check")
    cache_set("dQw4w9WgXcQ", _FAKE_URL_251, ttl=600)
    assert get_stream_url("dQw4w9WgXcQ") == _FAKE_URL_251


class TestCanaryIsolation:
    """The canary must not occupy one of the two user resolve slots."""

    def test_canary_uses_a_dedicated_semaphore(self):
        canary_slot = MagicMock()
        canary_slot.__enter__ = MagicMock(return_value=None)
        canary_slot.__exit__ = MagicMock(return_value=False)
        user_slot = MagicMock()
        user_slot.__enter__ = MagicMock(return_value=None)
        user_slot.__exit__ = MagicMock(return_value=False)

        with patch("app.resolver._extract_formats", return_value=_FORMATS_BOTH), \
             patch("app.resolver._CANARY_SEMAPHORE", canary_slot), \
             patch("app.resolver._RESOLVE_SEMAPHORE", user_slot):
            url = get_stream_url("dQw4w9WgXcQ", force=True, dedicated=True)

        assert url == _FAKE_URL_251
        canary_slot.__enter__.assert_called_once()
        user_slot.__enter__.assert_not_called()

    def test_user_resolve_keeps_the_shared_semaphore(self):
        canary_slot = MagicMock()
        user_slot = MagicMock()
        user_slot.__enter__ = MagicMock(return_value=None)
        user_slot.__exit__ = MagicMock(return_value=False)

        with patch("app.resolver._extract_formats", return_value=_FORMATS_BOTH), \
             patch("app.resolver._CANARY_SEMAPHORE", canary_slot), \
             patch("app.resolver._RESOLVE_SEMAPHORE", user_slot):
            get_stream_url("dQw4w9WgXcQ")

        user_slot.__enter__.assert_called_once()
        canary_slot.__enter__.assert_not_called()
