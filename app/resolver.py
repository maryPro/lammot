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


def _ydl_opts() -> dict:
    """yt-dlp options — extract info only, no download, no console noise."""
    opts: dict = {
        "quiet": True,            # suppress progress output
        "no_warnings": True,      # suppress warning lines
        "skip_download": True,    # never write any file to disk
        "format": "bestaudio",    # hint: we pick the itag ourselves below
    }
    # Optional, for hosts where YouTube asks to "confirm you're not a bot".
    # The file holds account cookies: treat it as a secret (see README).
    cookies = os.getenv("YTDLP_COOKIES_FILE")
    if cookies:
        opts["cookiefile"] = cookies
    return opts


def _cache_key(video_id: str, prefer: str) -> str:
    return video_id if prefer == "opus" else f"{video_id}:{prefer}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _pick_format(formats: list[dict], order: Tuple[int, int] = _ITAG_ORDER["opus"]) -> Optional[dict]:
    """
    Choose the audio format dict to use from a list of yt-dlp format dicts.

    Returns the first format from *order* (itags) that has a ``url``, or
    None if none of them is present.
    """
    by_itag: dict[int, dict] = {}
    for fmt in formats:
        format_id = str(fmt.get("format_id") or "")
        if format_id.isdigit():
            by_itag[int(format_id)] = fmt

    for itag in order:
        fmt = by_itag.get(itag)
        if fmt and fmt.get("url"):
            logger.debug("Resolver: selected itag %d", itag)
            return fmt

    return None


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
    url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
            info: dict = ydl.extract_info(url, download=False)
    except yt_dlp.utils.YoutubeDLError as exc:
        # YoutubeDLError is the base class of DownloadError, ExtractorError, etc.
        raise RuntimeError(
            f"yt-dlp failed to extract info for video_id={video_id!r}: {exc}"
        ) from exc

    formats: list[dict] = (info or {}).get("formats") or []
    fmt = _pick_format(formats, _ITAG_ORDER[prefer])

    if fmt is None:
        raise ValueError(
            f"No supported audio format (itag 251 or 140) found "
            f"for video_id={video_id!r}"
        )

    raw_headers = fmt.get("http_headers") or {}
    headers = {k: str(v) for k, v in raw_headers.items() if k in _FORWARDED_HEADERS}
    return fmt["url"], headers or dict(_DEFAULT_HEADERS)


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

    # --- Cache miss: resolve via yt-dlp ---
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
