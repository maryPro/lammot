"""
app/resolver.py — Sprint 2: Stream URL Resolver
LammotBot Telegram Mini App Music Player

Converts a YouTube video_id into a direct HTTPS audio stream URL hosted on
Google's CDN (*.googlevideo.com).

Important: a googlevideo URL is tied to the IP address that resolved it.
The browser therefore cannot play it directly when the server runs elsewhere
(Railway); app/main.py relays the audio through /audio/{video_id}. The server
still never stores audio — it only passes bytes through.

Format priority:
    prefer="opus" (default): itag 251 WebM/Opus ~160 kbps, then itag 140
    prefer="aac":            itag 140 M4A/AAC  ~128 kbps, then itag 251
    (Telegram on older iOS cannot play WebM/Opus, so the client asks for aac.)

Caching (app/cache.py, 2 hours):
    "<video_id>"            → URL for prefer="opus"
    "<video_id>:aac"        → URL for prefer="aac"
    "<key>:headers"         → HTTP headers yt-dlp says to send with that URL

yt-dlp needs a JavaScript runtime (Deno) and the yt-dlp-ejs package for
YouTube since 2025.11 — install "yt-dlp[default]" and Deno (see README).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Dict, Optional, Tuple

import yt_dlp  # type: ignore

from app.cache import cache_delete, cache_get, cache_set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Format constants
# ---------------------------------------------------------------------------
_PREFERRED_ITAG: int = 251   # WebM/Opus ~160 kbps
_FALLBACK_ITAG: int  = 140   # M4A/AAC  ~128 kbps
_STREAM_TTL: float   = 7200  # 2 hours in seconds

# YouTube gates a freshly resolved URL until its ``available_at`` timestamp
# (the detected pre-roll ad length): requesting it earlier yields HTTP 403.
# Normally this is a few seconds; anything longer means a premiere or an
# unusually long ad, so fail fast instead of holding a thread pool worker.
_MAX_AVAILABILITY_WAIT: float = 30.0

# Resolver client strategy.  The embedded client is asked first in the normal
# extraction; if the selected format is still gated for more than
# _GATE_RETRY_THRESHOLD seconds, one extra embedded-only extraction is tried
# because pre-roll ads are assigned per response and often disappear on retry.
_EMBEDDED_CLIENTS: Tuple[str, ...] = ("web_embedded",)
_DEFAULT_CLIENTS: Tuple[str, ...] = ("web_embedded", "default")
_GATE_RETRY_THRESHOLD: float = 3.0

# When the preferred Opus stream is gated but AAC is not, prefer the immediate
# AAC stream if it saves at least this many seconds.  AAC is playable
# everywhere, so this trades a little bitrate for instant playback.
_GATE_SWITCH_MIN_SAVING: float = 1.5

# One lock per cache key: concurrent /api/stream and /audio requests for the
# same track share a single yt-dlp extraction instead of racing.
_RESOLVE_LOCKS: Dict[str, threading.Lock] = {}
_RESOLVE_LOCKS_GUARD = threading.Lock()

FORMATS = ("opus", "aac")
_ITAG_ORDER = {
    "opus": (_PREFERRED_ITAG, _FALLBACK_ITAG),
    "aac": (_FALLBACK_ITAG, _PREFERRED_ITAG),
}

# YouTube video_id: exactly 11 chars of [A-Za-z0-9_-].  Validating this before
# building the URL guarantees yt-dlp only ever receives a YouTube watch URL.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Only these headers from yt-dlp are forwarded to the CDN by the audio relay
_FORWARDED_HEADERS = ("User-Agent", "Accept", "Accept-Language", "Origin", "Referer")
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


def _ydl_opts(player_clients: Optional[list[str]] = None) -> dict:
    """yt-dlp options — extract info only, no download, no console noise."""
    clients = list(player_clients) if player_clients else list(_DEFAULT_CLIENTS)
    opts: dict = {
        "quiet": True,            # suppress progress output
        "no_warnings": True,      # suppress warning lines
        "skip_download": True,    # never write any file to disk
        # No format hint: we pick itag 251/140 ourselves in _pick_format().
        # Asking yt-dlp for "bestaudio" makes it fail on clients whose formats
        # are SABR-only or missing a URL, even though other clients have them.
        "extractor_args": {
            "youtube": {"player_client": clients},
        },
    }
    # Optional, for hosts where YouTube asks to "confirm you're not a bot".
    # The file holds account cookies: treat it as a secret (see README).
    cookies = os.getenv("YTDLP_COOKIES_FILE")
    if cookies:
        opts["cookiefile"] = cookies
    return opts


def _cache_key(video_id: str, prefer: str) -> str:
    return video_id if prefer == "opus" else f"{video_id}:{prefer}"


def _resolve_lock(key: str) -> threading.Lock:
    """One lock per cache key so concurrent callers share a resolution."""
    with _RESOLVE_LOCKS_GUARD:
        lock = _RESOLVE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _RESOLVE_LOCKS[key] = lock
        return lock


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _pick_format(formats: list[dict], order: Tuple[int, ...] = _ITAG_ORDER["opus"]) -> Optional[dict]:
    """
    Choose the audio format dict to use from a list of yt-dlp format dicts.

    yt-dlp merges formats from several player clients, so the same itag may
    appear more than once with different ``available_at`` gates.  The CDN
    rejects a fresh URL with HTTP 403 until that moment, so the last duplicate
    is not always usable first.  Prefer the earliest-available duplicate within
    the requested itag order.

    Returns the first format from *order* (itags) that has a ``url``, or
    None if none of them is present.
    """
    by_itag: dict[int, list[dict]] = {}
    for fmt in formats:
        format_id = str(fmt.get("format_id") or "")
        if format_id.isdigit() and fmt.get("url"):
            by_itag.setdefault(int(format_id), []).append(fmt)

    for itag in order:
        candidates = by_itag.get(itag)
        if not candidates:
            continue

        fmt = min(candidates, key=lambda f: float(f.get("available_at") or 0))
        logger.debug(
            "Resolver: selected itag %d (available_at=%s)",
            itag,
            fmt.get("available_at"),
        )
        return fmt

    return None


def _gate_wait(fmt: Optional[dict]) -> float:
    """Seconds until *fmt* becomes available (0 when it is ready now)."""
    if not fmt:
        return float("inf")
    return max(0.0, float(fmt.get("available_at") or 0) - time.time())


def _select_format(formats: list[dict], prefer: str) -> Optional[dict]:
    """
    Pick a format, trading Opus for AAC only when it avoids a real wait.

    AAC is universally playable, so a short gate on Opus is worth hopping over
    a multi-second pre-roll gate on Opus.  The reverse switch is never done:
    clients that ask for AAC may not decode Opus.
    """
    fmt = _pick_format(formats, _ITAG_ORDER[prefer])
    if fmt is None or prefer != "opus":
        return fmt

    preferred_wait = _gate_wait(fmt)
    if preferred_wait < _GATE_SWITCH_MIN_SAVING:
        return fmt

    alt = _pick_format(formats, (_FALLBACK_ITAG,))
    if alt is None:
        return fmt

    if preferred_wait - _gate_wait(alt) >= _GATE_SWITCH_MIN_SAVING:
        logger.info(
            "Resolver: using itag %s instead of Opus to avoid a %.1fs gate",
            alt.get("format_id"),
            preferred_wait,
        )
        return alt
    return fmt


def _extract_formats(video_id: str, player_clients: Tuple[str, ...]) -> list[dict]:
    """Run yt-dlp for one client set and return its format list."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        with yt_dlp.YoutubeDL(_ydl_opts(list(player_clients))) as ydl:
            info: dict = ydl.extract_info(url, download=False)
    except yt_dlp.utils.YoutubeDLError as exc:
        raise RuntimeError(
            f"yt-dlp failed to extract info for video_id={video_id!r}: {exc}"
        ) from exc
    return (info or {}).get("formats") or []


def _resolve_via_ytdlp(video_id: str, prefer: str = "opus") -> Tuple[str, Dict[str, str]]:
    """
    Call yt-dlp to extract audio format information for *video_id*.

    Returns (CDN URL, HTTP headers to send with it).

    Raises
    ------
    ValueError
        If neither itag 251 nor itag 140 is available for this video.
    RuntimeError
        If yt-dlp itself raises an exception during extraction.
    """
    first_error: Optional[RuntimeError] = None
    try:
        formats = _extract_formats(video_id, _DEFAULT_CLIENTS)
    except RuntimeError as exc:
        first_error = exc
        formats = []

    fmt = _select_format(formats, prefer)

    # A gated stream usually means YouTube attached a pre-roll ad to this
    # response.  Ads are per-response, so one embedded-only retry often yields
    # the same itag without a gate and removes the wait entirely.
    if fmt is None or _gate_wait(fmt) > _GATE_RETRY_THRESHOLD:
        logger.info(
            "Resolver: first pass unusable for %s (gate=%.1fs), retrying embedded",
            video_id,
            _gate_wait(fmt) if fmt else -1.0,
        )
        try:
            extra = _extract_formats(video_id, _EMBEDDED_CLIENTS)
        except Exception as exc:  # retry is best-effort; keep the first result
            logger.info("Resolver: embedded retry failed for %s: %s", video_id, exc)
        else:
            fmt = _select_format(formats + extra, prefer)

    if fmt is None:
        if first_error is not None:
            raise first_error
        raise ValueError(
            f"No supported audio format (itag 251 or 140) found "
            f"for video_id={video_id!r}"
        )

    _wait_until_available(fmt)

    raw_headers = fmt.get("http_headers") or {}
    headers = {k: str(v) for k, v in raw_headers.items() if k in _FORWARDED_HEADERS}
    return fmt["url"], headers or dict(_DEFAULT_HEADERS)


def _wait_until_available(fmt: dict) -> None:
    """
    Wait until YouTube reports the format as available.

    yt-dlp exposes ``available_at`` (the pre-roll ad length): the CDN answers
    403 for the URL before that moment. Raising a new resolution does not help,
    because every fresh URL carries a new gate.
    """
    available_at = fmt.get("available_at")
    if not available_at:
        return

    wait = _gate_wait(fmt)
    if wait <= 0:
        return
    if wait > _MAX_AVAILABILITY_WAIT:
        raise ValueError(
            f"Format is not available yet (available_at in {int(wait)}s)"
        )

    logger.info("Resolver: waiting %.1fs until the stream becomes available", wait)
    time.sleep(wait)


def _validate(video_id: str, prefer: str) -> str:
    if not video_id or not video_id.strip():
        raise ValueError("video_id must be a non-empty string")
    video_id = video_id.strip()
    if not _VIDEO_ID_RE.match(video_id):
        raise ValueError(f"Invalid YouTube video_id: {video_id!r}")
    if prefer not in FORMATS:
        raise ValueError(f"prefer must be one of {FORMATS}")
    return video_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_stream_url(video_id: str, prefer: str = "opus") -> str:
    """
    Return a direct HTTPS audio CDN URL for a YouTube video.

    The URL is served by Google's CDN (``*.googlevideo.com``) and only works
    from the IP address of the machine that resolved it.

    Results are cached in-memory for 2 hours so that repeated calls for the
    same track are instant (~0 ms) without hitting YouTube.

    Raises
    ------
    ValueError
        If *video_id* is empty / not a valid YouTube id, *prefer* is unknown,
        or neither of the required audio formats is available.
    RuntimeError
        If yt-dlp encounters a network or extraction error.

    Examples
    --------
    >>> url = get_stream_url("dQw4w9WgXcQ")
    >>> url.startswith("https://")
    True
    >>> "googlevideo" in url
    True
    """
    video_id = _validate(video_id, prefer)
    key = _cache_key(video_id, prefer)

    # --- Cache hit ---
    cached = cache_get(key)
    if cached is not None:
        logger.debug("Resolver: cache hit for %s", key)
        return cached

    # --- Cache miss: resolve once per key, even under concurrent requests ---
    lock = _resolve_lock(key)
    with lock:
        cached = cache_get(key)
        if cached is not None:
            logger.debug("Resolver: cache filled while waiting for %s", key)
            return cached

        logger.info("Resolver: cache miss — resolving via yt-dlp for %s", key)
        stream_url, headers = _resolve_via_ytdlp(video_id, prefer)

        cache_set(key, stream_url, ttl=_STREAM_TTL)
        cache_set(f"{key}:headers", json.dumps(headers), ttl=_STREAM_TTL)
        logger.info("Resolver: cached stream URL for %s (TTL=%ds)", key, int(_STREAM_TTL))

    return stream_url


def get_stream_headers(video_id: str, prefer: str = "opus") -> Dict[str, str]:
    """Headers to send to the CDN with the URL from get_stream_url()."""
    raw = cache_get(f"{_cache_key(video_id, prefer)}:headers")
    if raw:
        try:
            return dict(json.loads(raw))
        except (ValueError, TypeError):
            pass
    return dict(_DEFAULT_HEADERS)


def invalidate(video_id: str, prefer: str = "opus") -> None:
    """Forget the cached URL (e.g. the CDN answered 403), so the next call re-resolves."""
    key = _cache_key(video_id, prefer)
    cache_delete(key)
    cache_delete(f"{key}:headers")
