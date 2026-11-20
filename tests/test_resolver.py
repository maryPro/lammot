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
from pathlib import Path
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
_FAKE_URL_18 = (
    "https://rr5---sn-muxed.googlevideo.com/videoplayback"
    "?expire=9999999999&itag=18&mime=video/mp4"
)

# A minimal format list that yt-dlp would return
_FORMATS_BOTH = [
    {"format_id": "251", "url": _FAKE_URL_251, "acodec": "opus"},
    {"format_id": "140", "url": _FAKE_URL_140, "acodec": "mp4a.40.2"},
]
_FORMATS_140_ONLY = [
    {"format_id": "140", "url": _FAKE_URL_140, "acodec": "mp4a.40.2"},
]
_FORMATS_18_ONLY = [
    {"format_id": "18", "url": _FAKE_URL_18, "acodec": "mp4a.40.2", "vcodec": "avc1.42001E"},
]
_FORMATS_NONE = [
    {"format_id": "137", "url": "https://rr1---sn-abc.googlevideo.com/vp?itag=137", "acodec": "none"},
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


@pytest.fixture(autouse=True)
def serial_resolver(monkeypatch):
    """Existing tests exercise the deterministic serial path."""
    monkeypatch.setattr(resolver, "_PARALLEL_RESOLVE", False)


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

    def test_muxed_itag_18_when_only_muxed_is_available(self):
        """The web client exposes only the gate-free muxed MP4."""
        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=_make_ydl_mock(_FORMATS_18_ONLY)):
            url = get_stream_url("dQw4w9WgXcQ", prefer="aac")

        assert url == _FAKE_URL_18

    def test_muxed_itag_18_beats_a_large_audio_only_gate(self):
        """Trade a little bandwidth for an instant start when AAC is gated."""
        now = 1_800_000_000
        formats = [
            {"format_id": "140", "url": _FAKE_URL_140, "available_at": now + 5},
            {"format_id": "18", "url": _FAKE_URL_18},
        ]

        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=_make_ydl_mock(formats)), \
             patch("time.time", return_value=now), \
             patch("time.sleep") as sleep:
            url = get_stream_url("dQw4w9WgXcQ", prefer="aac")

        assert url == _FAKE_URL_18
        sleep.assert_not_called()

    def test_muxed_itag_18_beats_a_large_opus_gate(self):
        now = 1_800_000_000
        formats = [
            {"format_id": "251", "url": _FAKE_URL_251_LATE, "available_at": now + 5},
            {"format_id": "18", "url": _FAKE_URL_18},
        ]

        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=_make_ydl_mock(formats)), \
             patch("time.time", return_value=now), \
             patch("time.sleep") as sleep:
            url = get_stream_url("dQw4w9WgXcQ")

        assert url == _FAKE_URL_18
        sleep.assert_not_called()

    def test_audio_only_wins_over_muxed_when_both_are_ready(self):
        formats = [
            {"format_id": "140", "url": _FAKE_URL_140},
            {"format_id": "18", "url": _FAKE_URL_18},
        ]

        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=_make_ydl_mock(formats)):
            url = get_stream_url("dQw4w9WgXcQ", prefer="aac")

        assert url == _FAKE_URL_140

    def test_raises_value_error_when_no_supported_format(self):
        """If none of itag 251/140/18 is present, ValueError is raised."""
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
            with pytest.raises(resolver.TransientResolveError, match="not available yet"):
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
            with pytest.raises(resolver.TransientResolveError, match="not available yet"):
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

    def test_falls_back_to_default_when_web_is_gated(self):
        """The web client is tried first; default is added only when it fails."""
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
        assert calls == [["web"], ["web_embedded", "default"]]
        sleep.assert_not_called()

    def test_no_fallback_pass_when_web_is_ready(self):
        """A usable web result must not be re-resolved."""
        calls = []

        def fake_ydl(opts):
            calls.append(opts["extractor_args"]["youtube"]["player_client"])
            return _make_ydl_mock(_FORMATS_BOTH)

        with patch("app.resolver.yt_dlp.YoutubeDL", side_effect=fake_ydl):
            url = get_stream_url("dQw4w9WgXcQ")

        assert url == _FAKE_URL_251
        assert calls == [["web"]]

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


class TestMemoryBudget:
    """Three yt-dlp/Deno extractions must remain under the 1 GB container."""

    def test_deno_solver_heap_is_capped_in_the_image(self):
        """Without a V8 cap one Deno process peaks around 340 MB."""
        dockerfile = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text(
            encoding="utf-8"
        )
        assert "DENO_V8_FLAGS=--max-old-space-size=160,--max-semi-space-size=1" in dockerfile

    def test_extraction_cap_is_three(self):
        assert resolver._EXTRACT_CONCURRENCY == 3
        assert resolver._EXTRACT_POOL._max_workers == resolver._EXTRACT_CONCURRENCY

    def test_extract_formats_never_runs_more_than_three_ytdlp_jobs(self):
        """The cap also covers canary/cookie validation, not only the race pool."""
        active = 0
        peak = 0
        state_lock = threading.Lock()
        start = threading.Barrier(6)

        def fake_extract_info(_url, download=False):
            nonlocal active, peak
            assert download is False
            with state_lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.1)
            with state_lock:
                active -= 1
            return {"formats": _FORMATS_BOTH}

        mock_ydl = _make_ydl_mock(_FORMATS_BOTH)
        mock_ydl.extract_info.side_effect = fake_extract_info

        def worker():
            start.wait()
            return resolver._extract_formats("dQw4w9WgXcQ", ("web",))

        with patch("app.resolver.yt_dlp.YoutubeDL", return_value=mock_ydl):
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(lambda _: worker(), range(6)))

        assert peak <= resolver._EXTRACT_CONCURRENCY


class TestClientOptions:
    def test_default_pass_uses_the_web_client(self):
        """The web client's muxed MP4 is usually free of a pre-roll gate."""
        clients = _ydl_opts()["extractor_args"]["youtube"]["player_client"]
        assert clients == ["web"]

    def test_fallback_pass_adds_the_default_client(self):
        """Videos where the web client is gated get the embedded/default clients."""
        assert resolver._fallback_clients() == ("web_embedded", "default")

    def test_fast_stage_uses_only_the_web_client(self):
        clients = _ydl_opts(["web"])["extractor_args"]["youtube"]["player_client"]
        assert clients == ["web"]

    def test_no_format_hint_is_passed_to_ytdlp(self):
        """We select itag 251/140/18 ourselves; a format hint breaks SABR clients."""
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


class TestPrefetchPriority:
    """A tap must never wait behind background prefetch work."""

    @staticmethod
    def _slot():
        slot = MagicMock()
        slot.__enter__ = MagicMock(return_value=None)
        slot.__exit__ = MagicMock(return_value=False)
        return slot

    def test_prefetch_acquires_the_guard_before_a_resolve_slot(self):
        guard = self._slot()
        user_slot = self._slot()

        with patch("app.resolver._extract_formats", return_value=_FORMATS_BOTH), \
             patch("app.resolver._PREFETCH_GUARD", guard), \
             patch("app.resolver._RESOLVE_SEMAPHORE", user_slot):
            url = get_stream_url("dQw4w9WgXcQ", prefetch=True)

        assert url == _FAKE_URL_251
        guard.__enter__.assert_called_once()
        user_slot.__enter__.assert_called_once()

    def test_interactive_resolve_skips_the_prefetch_guard(self):
        guard = self._slot()
        user_slot = self._slot()

        with patch("app.resolver._extract_formats", return_value=_FORMATS_BOTH), \
             patch("app.resolver._PREFETCH_GUARD", guard), \
             patch("app.resolver._RESOLVE_SEMAPHORE", user_slot):
            url = get_stream_url("dQw4w9WgXcQ")

        assert url == _FAKE_URL_251
        guard.__enter__.assert_not_called()
        user_slot.__enter__.assert_called_once()


class TestParallelResolution:
    """Interactive taps race several clients and stop at the first usable one."""

    @staticmethod
    def _by_clients(web, embedded, default):
        return {
            ("web",): web,
            ("web_embedded",): embedded,
            ("web_embedded", "default"): default,
        }

    def test_returns_an_ungated_client_when_web_is_gated(self):
        now = 1_800_000_000
        gated = [{"format_id": "18", "url": _FAKE_URL_18, "available_at": now + 6}]
        ungated = [{"format_id": "140", "url": _FAKE_URL_140}]
        by_clients = self._by_clients(gated, ungated, ungated)

        def fake_extract(_video_id, clients, _cookiefile=None):
            return by_clients[clients]

        with patch("app.resolver._extract_formats", side_effect=fake_extract), \
             patch("time.time", return_value=now):
            url, _headers = resolver._resolve_parallel("dQw4w9WgXcQ", "aac")

        assert url == _FAKE_URL_140

    def test_returns_the_fast_muxed_stream_when_it_is_ready(self):
        def fake_extract(_video_id, _clients, _cookiefile=None):
            return _FORMATS_18_ONLY

        with patch("app.resolver._extract_formats", side_effect=fake_extract):
            url, _headers = resolver._resolve_parallel("dQw4w9WgXcQ", "aac")

        assert url == _FAKE_URL_18

    def test_all_gated_merges_and_waits_for_the_earliest_gate(self):
        now = 1_800_000_000
        web = [{"format_id": "18", "url": _FAKE_URL_18, "available_at": now + 8}]
        embedded = [{"format_id": "140", "url": _FAKE_URL_140, "available_at": now + 2}]
        default = [{"format_id": "140", "url": _FAKE_URL_140, "available_at": now + 2.5}]
        by_clients = self._by_clients(web, embedded, default)

        def fake_extract(_video_id, clients, _cookiefile=None):
            return by_clients[clients]

        with patch("app.resolver._extract_formats", side_effect=fake_extract), \
             patch("time.time", return_value=now), \
             patch("time.sleep") as sleep:
            url, _headers = resolver._resolve_parallel("dQw4w9WgXcQ", "aac")

        assert url == _FAKE_URL_140
        sleep.assert_called_once()
        assert sleep.call_args.args[0] == pytest.approx(2.0)

    def test_race_starts_all_three_client_sets_together(self):
        """All three sets must be in flight before the first one can finish."""
        barrier = threading.Barrier(len(resolver._FAST_CLIENT_SETS), timeout=2)
        started = []
        started_lock = threading.Lock()

        def fake_extract(_video_id, clients, _cookiefile=None):
            with started_lock:
                started.append(clients)
            barrier.wait()
            return _FORMATS_BOTH

        with patch("app.resolver._extract_formats", side_effect=fake_extract):
            url, _headers = resolver._resolve_parallel("dQw4w9WgXcQ", "aac")

        assert url == _FAKE_URL_140
        assert set(started) == set(resolver._FAST_CLIENT_SETS)

    def test_all_clients_gated_beyond_the_tap_cap_fail(self):
        now = 1_800_000_000
        gated = [{"format_id": "18", "url": _FAKE_URL_18, "available_at": now + 60}]
        by_clients = self._by_clients(gated, gated, gated)

        def fake_extract(_video_id, clients, _cookiefile=None):
            return by_clients[clients]

        with patch("app.resolver._extract_formats", side_effect=fake_extract), \
             patch("time.time", return_value=now), \
             patch("time.sleep") as sleep:
            with pytest.raises(resolver.TransientResolveError, match="not available yet"):
                resolver._resolve_parallel("dQw4w9WgXcQ", "aac")

        sleep.assert_not_called()

    def test_race_raises_the_most_actionable_error(self):
        def fake_extract(_video_id, clients, _cookiefile=None):
            if clients == ("web",):
                raise resolver.FormatUnavailableError("no format")
            if clients == ("web_embedded",):
                raise resolver.TransientResolveError("network")
            raise resolver.BotCheckError("bot check")

        with patch("app.resolver._extract_formats", side_effect=fake_extract):
            with pytest.raises(resolver.BotCheckError):
                resolver._resolve_parallel("dQw4w9WgXcQ", "aac")

    def test_dispatcher_uses_the_race_for_normal_playback(self, monkeypatch):
        monkeypatch.setattr(resolver, "_PARALLEL_RESOLVE", True)
        with patch("app.resolver._resolve_parallel", return_value=("race", {})) as race, \
             patch("app.resolver._resolve_serial", return_value=("serial", {})) as serial:
            assert resolver._resolve_via_ytdlp("x", "aac", None, parallel=True)[0] == "race"
            assert resolver._resolve_via_ytdlp("x", "aac", None, parallel=False)[0] == "serial"

        race.assert_called_once()
        serial.assert_called_once()

    def test_prefetch_uses_the_sequential_prefetch_path(self, monkeypatch):
        monkeypatch.setattr(resolver, "_PARALLEL_RESOLVE", True)
        with patch("app.resolver._resolve_prefetch", return_value=(_FAKE_URL_18, {})) as prefetch, \
             patch("app.resolver._resolve_parallel", return_value=(_FAKE_URL_18, {})) as race, \
             patch("app.resolver._resolve_serial", return_value=(_FAKE_URL_18, {})) as serial:
            url = get_stream_url("dQw4w9WgXcQ", prefetch=True)

        assert url == _FAKE_URL_18
        prefetch.assert_called_once()
        race.assert_not_called()
        serial.assert_not_called()

    def test_prefetch_race_survives_a_gate_that_serial_rejects(self, monkeypatch):
        """A gated web pass must not make prefetch give up while a tap would win."""
        monkeypatch.setattr(resolver, "_PARALLEL_RESOLVE", True)
        now = 1_800_000_000
        gated = [{"format_id": "18", "url": _FAKE_URL_18, "available_at": now + 4}]
        ungated = [{"format_id": "140", "url": _FAKE_URL_140}]
        by_clients = self._by_clients(gated, ungated, gated)

        def fake_extract(_video_id, clients, _cookiefile=None, **_kwargs):
            return by_clients[clients]

        with patch("app.resolver._extract_formats", side_effect=fake_extract), \
             patch("time.time", return_value=now):
            url = get_stream_url("dQw4w9WgXcQ", "aac", prefetch=True)

        assert url == _FAKE_URL_140

    def test_tap_gate_wait_is_three_seconds(self, monkeypatch):
        monkeypatch.setattr(resolver, "_PARALLEL_RESOLVE", True)
        with patch("app.resolver._resolve_parallel", return_value=(_FAKE_URL_18, {})) as race:
            get_stream_url("dQw4w9WgXcQ")

        assert resolver._MAX_TAP_GATE_WAIT == 3.0
        assert race.call_args.args[3] == resolver._MAX_TAP_GATE_WAIT

    def test_extraction_timeout_is_separate_from_the_gate_wait(self):
        """A 3 s total deadline made every cold resolve return 502 at 3.0 s."""
        assert resolver._MAX_TAP_EXTRACT_WAIT >= 10.0
        assert resolver._MAX_TAP_EXTRACT_WAIT > resolver._MAX_TAP_GATE_WAIT


class TestGateCache:
    def test_gate_timeout_is_not_negative_cached(self):
        """A gated prefetch must not make the later tap return 404."""
        now = 1_800_000_000
        gated = [
            {"format_id": "140", "url": _FAKE_URL_140, "available_at": now + 5}
        ]
        ungated = [{"format_id": "140", "url": _FAKE_URL_140}]
        calls = []

        def fake_ydl(_opts):
            calls.append(1)
            return _make_ydl_mock(gated if len(calls) <= 2 else ungated)

        with patch("app.resolver.yt_dlp.YoutubeDL", side_effect=fake_ydl), \
             patch("time.time", return_value=now), \
             patch("time.sleep"):
            with pytest.raises(resolver.TransientResolveError, match="not available yet"):
                get_stream_url("dQw4w9WgXcQ", prefer="aac")

            url = get_stream_url("dQw4w9WgXcQ", prefer="aac")

        assert url == _FAKE_URL_140


class TestPrefetchExtractionPolicy:
    """Prefetch must not compete with taps for extraction capacity or cookies."""

    @staticmethod
    def _readonly_ydl(formats, extract_info=None):
        mock_ydl = _make_ydl_mock(formats)
        if extract_info is not None:
            mock_ydl.extract_info.side_effect = extract_info
        return MagicMock(return_value=mock_ydl)

    def test_prefetch_uses_at_most_one_extract_slot(self):
        active = 0
        peak = 0
        state_lock = threading.Lock()

        def fake_extract_info(_url, download=False):
            nonlocal active, peak
            assert download is False
            with state_lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with state_lock:
                active -= 1
            return {"formats": _FORMATS_BOTH}

        readonly = self._readonly_ydl(_FORMATS_BOTH, fake_extract_info)

        def worker():
            resolver._extract_formats("dQw4w9WgXcQ", ("web",), prefetch=True)

        with patch("app.resolver._ReadOnlyCookieYoutubeDL", readonly):
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _: worker(), range(4)))

        assert peak == 1

    def test_tap_can_extract_while_prefetch_holds_its_slot(self):
        prefetch_started = threading.Event()
        release_prefetch = threading.Event()

        def slow_extract(_url, download=False):
            assert download is False
            prefetch_started.set()
            assert release_prefetch.wait(2)
            return {"formats": _FORMATS_BOTH}

        prefetch_ydl = self._readonly_ydl(_FORMATS_BOTH, slow_extract)
        tap_ydl = _make_ydl_mock(_FORMATS_BOTH)
        tap_class = MagicMock(return_value=tap_ydl)

        with patch("app.resolver._ReadOnlyCookieYoutubeDL", prefetch_ydl), \
             patch("app.resolver.yt_dlp.YoutubeDL", tap_class):
            with ThreadPoolExecutor(max_workers=2) as pool:
                prefetch_future = pool.submit(
                    resolver._extract_formats, "dQw4w9WgXcQ", ("web",),
                    None, prefetch=True,
                )
                assert prefetch_started.wait(2)
                tap_future = pool.submit(
                    resolver._extract_formats, "dQw4w9WgXcQ", ("web",),
                )
                result = tap_future.result(timeout=2)
                release_prefetch.set()
                prefetch_future.result(timeout=2)

        assert result == _FORMATS_BOTH

    def test_prefetch_does_not_save_cookies(self, tmp_path):
        target = tmp_path / "cookies.txt"
        ydl = resolver._ReadOnlyCookieYoutubeDL({"quiet": True})

        ydl.save_cookies(str(target))

        assert not target.exists()

    def test_prefetch_extraction_uses_the_read_only_ydl(self):
        mock_ydl = _make_ydl_mock(_FORMATS_BOTH)

        with patch(
            "app.resolver._ReadOnlyCookieYoutubeDL", return_value=mock_ydl
        ) as readonly, \
             patch("app.resolver.yt_dlp.YoutubeDL") as tap_class:
            resolver._extract_formats("dQw4w9WgXcQ", ("web",), prefetch=True)

        readonly.assert_called_once()
        tap_class.assert_not_called()

    def test_prefetch_should_stop_between_client_attempts(self):
        now = 1_800_000_000
        gated = [{"format_id": "18", "url": _FAKE_URL_18, "available_at": now + 5}]
        calls = []
        state = {"stop": False}

        def should_stop():
            return state["stop"]

        def fake_extract(_video_id, clients, _cookiefile=None, **_kwargs):
            calls.append(clients)
            state["stop"] = True
            return gated

        with patch("app.resolver._extract_formats", side_effect=fake_extract), \
             patch("time.time", return_value=now):
            with pytest.raises(resolver.PrefetchCancelled):
                resolver._resolve_prefetch(
                    "dQw4w9WgXcQ", "aac", should_stop=should_stop
                )

        assert calls == [resolver._FAST_CLIENT_SETS[0]]

    def test_cancelled_prefetch_does_not_populate_cache(self):
        from app.cache import cache_get

        state = {"stop": False}

        def should_stop():
            return state["stop"]

        def fake_extract(_video_id, _clients, _cookiefile=None, **_kwargs):
            state["stop"] = True
            return _FORMATS_BOTH

        with patch("app.resolver._extract_formats", side_effect=fake_extract):
            with pytest.raises(resolver.PrefetchCancelled):
                get_stream_url(
                    "dQw4w9WgXcQ", prefetch=True, should_stop=should_stop
                )

        assert cache_get("dQw4w9WgXcQ") is None
        assert cache_get("dQw4w9WgXcQ:unavailable") is None

    def test_background_prefetch_skips_a_key_already_being_resolved(self):
        """A tap holding the key lock must not pin the single prefetch worker."""
        lock = resolver._resolve_lock("dQw4w9WgXcQ")
        lock.acquire()
        release = threading.Timer(0.4, lock.release)
        release.start()
        started = time.perf_counter()
        try:
            with patch("app.resolver._extract_formats", return_value=_FORMATS_BOTH):
                with pytest.raises(resolver.PrefetchCancelled):
                    get_stream_url(
                        "dQw4w9WgXcQ", prefetch=True, should_stop=lambda: False
                    )
            elapsed = time.perf_counter() - started
        finally:
            release.join()

        assert elapsed < 0.2

    def test_client_prefetch_still_waits_for_a_key_lock(self):
        """The browser's ?prefetch=1 request is not a background job."""
        lock = resolver._resolve_lock("dQw4w9WgXcQ")
        lock.acquire()
        release = threading.Timer(0.1, lock.release)
        release.start()
        try:
            with patch("app.resolver._extract_formats", return_value=_FORMATS_BOTH):
                url = get_stream_url("dQw4w9WgXcQ", prefetch=True)
        finally:
            release.join()

        assert url == _FAKE_URL_251
