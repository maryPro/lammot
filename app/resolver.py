"""
app/resolver.py — Sprint 2: Stream URL Resolver
LammotBot Telegram Mini App Music Player

Converts a YouTube video_id into a direct, playable HTTPS audio stream URL
hosted on Google's CDN (*.googlevideo.com).

The server never stores audio — it only resolves the URL and hands it to the
browser, which plays the stream natively via the HTML5 <audio> tag.

Format priority:
    1. itag 251 — WebM/Opus  ~160 kbps  (best quality, smallest size)
    2. itag 140 — M4A/AAC   ~128 kbps  (fallback for older clients)

Caching:
    CDN URLs are cached in-memory for 2 hours (see app/cache.py).
    YouTube CDN URLs expire in ~6 hours, so 2 h gives a 4-hour safety margin.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import yt_dlp  # type: ignore

from app.cache import cache_get, cache_set

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Format constants
# ---------------------------------------------------------------------------
_PREFERRED_ITAG: int = 251   # WebM/Opus ~160 kbps
_FALLBACK_ITAG: int  = 140   # M4A/AAC  ~128 kbps
_STREAM_TTL: float   = 7200  # 2 hours in seconds

# YouTube video_id: exactly 11 chars of [A-Za-z0-9_-].  Validating this before
# building the URL guarantees yt-dlp only ever receives a YouTube watch URL.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# yt-dlp options — extract info only, no download, no console noise
_YDL_OPTS: dict = {
    "quiet": True,            # suppress progress output
    "no_warnings": True,      # suppress warning lines
    "skip_download": True,    # never write any file to disk
    "format": "bestaudio",    # hint: we override via format selection below
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _pick_format(formats: list[dict]) -> Optional[str]:
    """
    Choose the best audio stream URL from a list of yt-dlp format dicts.

    Selection order:
        1. itag 251 (WebM/Opus ~160 kbps)
        2. itag 140 (M4A/AAC  ~128 kbps)

    Returns the ``url`` string of the chosen format, or None if neither
    itag is present in the format list.
    """
    # Build a quick lookup: itag (int) → format dict
    by_itag: dict[int, dict] = {}
    for fmt in formats:
        format_id = str(fmt.get("format_id") or "")
        if format_id.isdigit():
            by_itag[int(format_id)] = fmt

    for preferred in (_PREFERRED_ITAG, _FALLBACK_ITAG):
        fmt = by_itag.get(preferred)
        if fmt and fmt.get("url"):
            logger.debug("Resolver: selected itag %d", preferred)
            return fmt["url"]

    return None


def _resolve_via_ytdlp(video_id: str) -> str:
    """
    Call yt-dlp to extract audio format information for *video_id*.

    Returns a direct CDN URL (guaranteed to start with https:// and contain
    'googlevideo').

    Raises
    ------
    ValueError
        If neither itag 251 nor itag 140 is available for this video.
    RuntimeError
        If yt-dlp itself raises an exception during extraction.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        with yt_dlp.YoutubeDL(_YDL_OPTS) as ydl:
            info: dict = ydl.extract_info(url, download=False)
    except yt_dlp.utils.YoutubeDLError as exc:
        # YoutubeDLError is the base class of DownloadError, ExtractorError, etc.
        raise RuntimeError(
            f"yt-dlp failed to extract info for video_id={video_id!r}: {exc}"
        ) from exc

    formats: list[dict] = (info or {}).get("formats") or []
    stream_url = _pick_format(formats)

    if stream_url is None:
        raise ValueError(
            f"No supported audio format (itag 251 or 140) found "
            f"for video_id={video_id!r}"
        )

    return stream_url


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_stream_url(video_id: str) -> str:
    """
    Return a direct, playable HTTPS audio CDN URL for a YouTube video.

    The URL is served by Google's CDN (``*.googlevideo.com``) and can be fed
    directly into an HTML5 ``<audio>`` ``src`` attribute.

    Results are cached in-memory for 2 hours so that repeated calls for the
    same track within a session are instant (~0 ms) without hitting YouTube.

    Parameters
    ----------
    video_id : str
        11-character YouTube video identifier (e.g. ``"dQw4w9WgXcQ"``).

    Returns
    -------
    str
        Direct HTTPS stream URL starting with ``https://`` and containing
        ``googlevideo``.

    Raises
    ------
    ValueError
        If *video_id* is empty / not a valid YouTube id, or neither of the required audio formats is
        available on YouTube for this video.
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
    if not video_id or not video_id.strip():
        raise ValueError("video_id must be a non-empty string")

    video_id = video_id.strip()
    if not _VIDEO_ID_RE.match(video_id):
        raise ValueError(f"Invalid YouTube video_id: {video_id!r}")

    # --- Cache hit ---
    cached = cache_get(video_id)
    if cached is not None:
        logger.debug("Resolver: cache hit for video_id=%s", video_id)
        return cached

    # --- Cache miss: resolve via yt-dlp ---
    logger.info("Resolver: cache miss — resolving via yt-dlp for video_id=%s", video_id)
    stream_url = _resolve_via_ytdlp(video_id)

    cache_set(video_id, stream_url, ttl=_STREAM_TTL)
    logger.info("Resolver: cached stream URL for video_id=%s (TTL=%ds)", video_id, int(_STREAM_TTL))

    return stream_url
