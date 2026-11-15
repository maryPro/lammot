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
    Both fall back to itag 18 (muxed 360p MP4/AAC) when the audio-only
    streams are behind a pre-roll gate.
    (Telegram on older iOS cannot play WebM/Opus, so the client asks for aac.)

Caching (app/cache.py):
    "<video_id>"            → URL for prefer="opus"
    "<video_id>:aac"        → URL for prefer="aac"
    "<key>:headers"         → HTTP headers yt-dlp says to send with that URL
    "<key>:unavailable"     → negative marker for a dead/unplayable video

The URL cache TTL is derived from the googlevideo ``expire`` query parameter
instead of a fixed value, so live URLs are kept as long as possible while
expired ones are never handed out.

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
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import yt_dlp  # type: ignore

from app.cache import cache_delete, cache_get, cache_set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ResolverError(Exception):
    """Base class for resolver failures that are safe to log/classify."""


class BotCheckError(ResolverError):
    """YouTube asks the server to prove it is not a bot."""


class TransientResolveError(ResolverError):
    """Network, timeout, or another error that may succeed on a retry."""


class UnavailableError(ValueError):
    """The video or its audio formats are not available at all."""


class VideoUnavailableError(UnavailableError):
    """Private, removed, age-restricted, or otherwise unavailable video."""


class FormatUnavailableError(UnavailableError):
    """The video has no playable itag 251/140/18 right now."""


# ---------------------------------------------------------------------------
# Format constants
# ---------------------------------------------------------------------------
_PREFERRED_ITAG: int = 251   # WebM/Opus ~160 kbps
_FALLBACK_ITAG: int = 140    # M4A/AAC  ~128 kbps
_STREAM_TTL: float = 7200    # fallback when the URL has no usable expire
_STREAM_TTL_MAX: float = 6 * 3600
_STREAM_TTL_SAFETY: float = 30 * 60
_MIN_CACHE_TTL: float = 60
_NEGATIVE_TTL: float = 3600
_BOT_CHECK_COOLDOWN: float = 60

# YouTube gates a freshly resolved URL until its ``available_at`` timestamp
# (the detected pre-roll ad length): requesting it earlier yields HTTP 403.
# Normally this is a few seconds; anything longer means a premiere or an
# unusually long ad, so fail fast instead of holding a thread pool worker.
# Keeping the cap short also stops a single gated track from adding 10-20 s to
# tap-to-play; _select_format tries a shorter-gated itag before we wait.
_MAX_AVAILABILITY_WAIT: float = 2.0

# Prefetch/canary fail fast at _MAX_AVAILABILITY_WAIT so they do not hold a
# resolve slot or the per-key lock through a long pre-roll.  A user tap, on the
# other hand, is better served by waiting a few seconds than by skipping the
# track, so the parallel tap path allows this longer gate.
_MAX_TAP_GATE_WAIT: float = 10.0

# Resolver client strategy.  The plain web client is tried first: its muxed
# 360p MP4 (itag 18) is served without a pre-roll gate, so playback starts
# immediately.  If that pass yields no usable format, we pay for one pass with
# the embedded/default clients, which expose the audio-only itags 140/251.
_FAST_CLIENTS: Tuple[str, ...] = ("web",)
_DEFAULT_CLIENTS: Tuple[str, ...] = ("web_embedded", "default")

# Interactive taps race these client sets: the pre-roll gate is assigned per
# player response, so a differently resolved client often returns an ungated
# format while the first one is still gated.  Each set is cheap and the race
# stops as soon as one set yields a usable format.
_FAST_CLIENT_SETS: Tuple[Tuple[str, ...], ...] = (
    ("web",),
    ("web_embedded",),
    ("web_embedded", "default"),
)
# Prefetch and taps both race; canary/cookie validation stay serial.
_PARALLEL_RESOLVE = True

# Muxed 360p MP4 with AAC audio (itag 18).  Used as a gate-free fallback when
# the audio-only stream is behind a pre-roll.
_MUXED_ITAG = 18

# When the preferred Opus stream is gated but AAC is not, prefer the immediate
# AAC stream if it saves at least this many seconds.  AAC is playable
# everywhere, so this trades a little bitrate for instant playback.
_GATE_SWITCH_MIN_SAVING: float = 1.5

# One lock per cache key: concurrent /api/stream and /audio requests for the
# same track share a single yt-dlp extraction instead of racing.
_RESOLVE_LOCKS: Dict[str, threading.Lock] = {}
_RESOLVE_LOCKS_GUARD = threading.Lock()

# Limit concurrent yt-dlp extractions.  Resolving is expensive and each extra
# request is another chance for YouTube to answer with a bot check.
def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


_RESOLVE_CONCURRENCY = _env_int("YTDLP_RESOLVE_CONCURRENCY", 2)
_RESOLVE_SEMAPHORE = threading.BoundedSemaphore(_RESOLVE_CONCURRENCY)
# Background extraction threads are bounded too: an early-race win leaves the
# other clients running for a few seconds, so a fixed pool prevents thread
# accumulation under rapid tapping.
# Keep this small: each yt-dlp/Deno extraction uses a few hundred MB, and the
# Railway container has a 1 GB memory limit.  Two client sets plus this pool
# cap keeps a normal tap below the limit while still racing the gate.
_EXTRACT_POOL = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="resolve",
)
# The canary gets its own single slot: a scheduled health check must never
# occupy one of the user resolve slots (a tap and the radio prefetch already
# use both of them).
_CANARY_SEMAPHORE = threading.BoundedSemaphore(1)
# At most one background prefetch may occupy a resolve slot, so a user tap
# always finds a free slot even while search results are being warmed.
_PREFETCH_GUARD = threading.BoundedSemaphore(1)
_MIN_RESOLVE_INTERVAL = _env_float("YTDLP_MIN_RESOLVE_INTERVAL", 0.0)
_RESOLVE_PACING_LOCK = threading.Lock()
_last_resolve_at = 0.0


def _pace_resolve() -> None:
    """Optional minimum spacing between yt-dlp extractions (0 = disabled)."""
    global _last_resolve_at
    if _MIN_RESOLVE_INTERVAL <= 0:
        return
    with _RESOLVE_PACING_LOCK:
        wait = _MIN_RESOLVE_INTERVAL - (time.monotonic() - _last_resolve_at)
        if wait > 0:
            time.sleep(wait)
        _last_resolve_at = time.monotonic()


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

# yt-dlp error texts.  Keep these narrow: a false bot-check classification
# would stop the whole service, while a missed one only causes an extra retry.
_BOT_CHECK_RE = re.compile(
    r"sign in to confirm you(?:'|’)?re not a bot|"
    r"sign in to confirm you are not a bot|"
    r"confirm you(?:'|’)?re not a bot",
    re.IGNORECASE,
)
_VIDEO_UNAVAILABLE_RE = re.compile(
    r"video unavailable|private video|removed by the uploader|"
    r"this video is unavailable|this video is not available|"
    r"no longer available|age-restricted|confirm your age|"
    r"not made this video available|not available in your country",
    re.IGNORECASE,
)
_FORMAT_UNAVAILABLE_RE = re.compile(
    r"requested format is not available|no video formats found|"
    r"no supported audio format",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Resolver state (in-memory; a single Railway process)
# ---------------------------------------------------------------------------
_STATE_LOCK = threading.RLock()
_STATE: Dict[str, object] = {
    "status": "unknown",          # unknown | ok | bot_check | degraded
    "active_account": "primary",  # primary | reserve
    "last_check": None,
    "last_error_class": None,
    "transition_seq": 0,
    "reason": None,
}

_STATS_LOCK = threading.Lock()
_STATS: Counter = Counter()


def _inc(name: str, amount: int = 1) -> None:
    with _STATS_LOCK:
        _STATS[name] += amount


def stats_snapshot(reset: bool = False) -> Dict[str, int]:
    """Return resolver counters, optionally resetting them for the next hour."""
    with _STATS_LOCK:
        snapshot = dict(_STATS)
        if reset:
            _STATS.clear()
    return snapshot


def get_state() -> Dict[str, object]:
    """A copy of the current resolver state, safe to expose on /health."""
    with _STATE_LOCK:
        return dict(_STATE)


def set_status(status: str, reason: Optional[str] = None) -> None:
    with _STATE_LOCK:
        changed = status != _STATE["status"]
        _STATE["status"] = status
        _STATE["last_check"] = time.time()
        if reason is not None:
            _STATE["reason"] = reason
        if changed:
            _STATE["transition_seq"] = int(_STATE["transition_seq"]) + 1


def set_active_account(account: str) -> None:
    if account not in ("primary", "reserve"):
        raise ValueError(f"unknown cookie account: {account!r}")
    with _STATE_LOCK:
        changed = account != _STATE["active_account"]
        _STATE["active_account"] = account
        if changed:
            _STATE["transition_seq"] = int(_STATE["transition_seq"]) + 1
            _STATE["reason"] = "account_switch"


def reset_state() -> None:
    with _STATE_LOCK:
        _STATE.update({
            "status": "unknown",
            "last_check": None,
            "last_error_class": None,
            "reason": "reset",
        })
        _STATE["transition_seq"] = int(_STATE["transition_seq"]) + 1


def _active_account() -> str:
    with _STATE_LOCK:
        return str(_STATE["active_account"])


def _bot_check_active() -> bool:
    with _STATE_LOCK:
        if _STATE["status"] != "bot_check":
            return False
        last = _STATE.get("last_check")
        return bool(last) and time.time() - float(last) < _BOT_CHECK_COOLDOWN


def _record_success() -> None:
    _inc("resolve_ok")
    set_status("ok", reason="ok")
    with _STATE_LOCK:
        _STATE["last_error_class"] = None


def _record_failure(exc: Exception) -> None:
    if isinstance(exc, BotCheckError):
        _inc("bot_check")
        set_status("bot_check", reason="bot_check")
    elif isinstance(exc, UnavailableError):
        _inc("unavailable")
    elif isinstance(exc, TransientResolveError):
        _inc("transient")
        with _STATE_LOCK:
            # An unavailable video is not a service-wide degradation.
            if _STATE["status"] == "ok":
                set_status("degraded", reason="degraded")
    with _STATE_LOCK:
        _STATE["last_error_class"] = type(exc).__name__


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

def _configured_clients() -> Tuple[str, ...]:
    """Player clients for the first (cheap) pass, overridable in production."""
    raw = os.getenv("YTDLP_PLAYER_CLIENTS", "").strip()
    if raw:
        clients = tuple(c.strip() for c in raw.split(",") if c.strip())
        if clients:
            return clients
    return _FAST_CLIENTS


def _fallback_clients() -> Tuple[str, ...]:
    """Clients used only when the first pass yields no immediately usable format."""
    return _DEFAULT_CLIENTS


def _ydl_opts(
    player_clients: Optional[list[str]] = None,
    cookiefile: Optional[str] = None,
) -> dict:
    """yt-dlp options — extract info only, no download, no console noise."""
    clients = (
        list(player_clients)
        if player_clients is not None
        else list(_configured_clients())
    )
    opts: dict = {
        "quiet": True,            # suppress progress output
        "no_warnings": True,      # suppress warning lines
        "skip_download": True,    # never write any file to disk
        # No format hint: we pick itag 251/140/18 ourselves in _pick_format().
        # Asking yt-dlp for "bestaudio" makes it fail on clients whose formats
        # are SABR-only or missing a URL, even though other clients have them.
        "extractor_args": {
            "youtube": {"player_client": clients},
        },
    }

    # Optional persistent player/n-sig cache on the Railway volume.  This is
    # deliberately opt-in: local runs and tests must not write to /data.
    cache_dir = os.getenv("YTDLP_CACHE_DIR", "").strip()
    if cache_dir:
        opts["cachedir"] = cache_dir

    # Optional PO token provider (bgutil HTTP service).  Absent in normal
    # deployments; enabling it also requires installing the bgutil plugin
    # (a dependency change that is intentionally not part of this PR).
    bgutil = os.getenv("BGUTIL_BASE_URL", "").strip()
    if bgutil:
        opts["extractor_args"]["youtubepot-bgutilhttp"] = {
            "base_url": [bgutil],
        }

    # Optional, for hosts where YouTube asks to "confirm you're not a bot".
    # The file holds account cookies: treat it as a secret (see README).
    cookies = cookiefile if cookiefile is not None else os.getenv("YTDLP_COOKIES_FILE")
    if cookies:
        opts["cookiefile"] = cookies
    return opts


def _cache_key(video_id: str, prefer: str) -> str:
    return video_id if prefer == "opus" else f"{video_id}:{prefer}"


def _negative_key(key: str) -> str:
    return f"{key}:unavailable"


def _resolve_lock(key: str) -> threading.Lock:
    """One lock per cache key so concurrent callers share a resolution."""
    with _RESOLVE_LOCKS_GUARD:
        lock = _RESOLVE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _RESOLVE_LOCKS[key] = lock
        return lock


def _ttl_for_url(url: str) -> float:
    """
    Cache TTL derived from the googlevideo ``expire`` query parameter.

    Returns 0 when the URL is too close to expiry to be worth caching.
    """
    try:
        params = parse_qs(urlparse(url).query)
        expire = int(params.get("expire", [""])[0])
    except (TypeError, ValueError):
        return _STREAM_TTL
    if not expire:
        return _STREAM_TTL
    ttl = expire - time.time() - _STREAM_TTL_SAFETY
    if ttl < _MIN_CACHE_TTL:
        return 0.0
    return min(ttl, _STREAM_TTL_MAX)


def _raise_negative(kind: str, video_id: str) -> None:
    if kind == "video":
        raise VideoUnavailableError(f"Video unavailable: video_id={video_id!r}")
    raise FormatUnavailableError(
        f"No supported audio format (itag 251, 140 or 18) found "
        f"for video_id={video_id!r}"
    )


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
    Pick a format, preferring audio-only and falling back to gate-free muxed.

    The audio-only streams (251/140) are the best choice when they are
    playable.  When they are behind a pre-roll gate, itag 18 (muxed 360p MP4)
    is often served immediately; a small bandwidth trade buys an instant
    start.  Opus is traded for AAC only when that avoids a real wait.
    """
    fmt = _pick_format(formats, _ITAG_ORDER[prefer])

    if fmt is not None and prefer == "opus":
        preferred_wait = _gate_wait(fmt)
        if preferred_wait >= _GATE_SWITCH_MIN_SAVING:
            alt = _pick_format(formats, (_FALLBACK_ITAG,))
            if alt is not None and preferred_wait - _gate_wait(alt) >= _GATE_SWITCH_MIN_SAVING:
                logger.info(
                    "Resolver: using itag %s instead of Opus to avoid a %.1fs gate",
                    alt.get("format_id"),
                    preferred_wait,
                )
                fmt = alt

    muxed = _pick_format(formats, (_MUXED_ITAG,))
    if muxed is not None:
        fmt_wait = _gate_wait(fmt)
        muxed_wait = _gate_wait(muxed)
        if fmt_wait - muxed_wait >= _GATE_SWITCH_MIN_SAVING:
            logger.info(
                "Resolver: using muxed itag %s to avoid a %.1fs gate",
                muxed.get("format_id"),
                fmt_wait,
            )
            return muxed

    return fmt


def _classify_ytdlp_error(exc: Exception, video_id: str) -> ResolverError:
    """Map a yt-dlp error to a stable resolver exception without its URL."""
    text = str(exc)
    if _BOT_CHECK_RE.search(text):
        return BotCheckError(f"YouTube bot check for video_id={video_id!r}")
    if _VIDEO_UNAVAILABLE_RE.search(text):
        return VideoUnavailableError(f"Video unavailable: video_id={video_id!r}")
    if _FORMAT_UNAVAILABLE_RE.search(text):
        return FormatUnavailableError(
            f"No supported audio format for video_id={video_id!r}"
        )
    return TransientResolveError(
        f"yt-dlp failed for video_id={video_id!r} ({type(exc).__name__})"
    )


def _extract_formats(
    video_id: str,
    player_clients: Tuple[str, ...],
    cookiefile: Optional[str] = None,
) -> list[dict]:
    """Run yt-dlp for one client set and return its format list."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        with yt_dlp.YoutubeDL(_ydl_opts(list(player_clients), cookiefile)) as ydl:
            info: dict = ydl.extract_info(url, download=False)
    except yt_dlp.utils.YoutubeDLError as exc:
        raise _classify_ytdlp_error(exc, video_id) from exc
    except Exception as exc:  # network/socket errors and plugin failures
        raise TransientResolveError(
            f"yt-dlp failed for video_id={video_id!r} ({type(exc).__name__})"
        ) from exc
    return (info or {}).get("formats") or []


def _usable_now(fmt: Optional[dict]) -> bool:
    """True when *fmt* needs no significant gate wait."""
    return fmt is not None and _gate_wait(fmt) <= _MAX_AVAILABILITY_WAIT


def _resolve_serial(
    video_id: str,
    prefer: str = "opus",
    cookiefile: Optional[str] = None,
) -> Tuple[str, Dict[str, str]]:
    """
    Call yt-dlp to extract audio format information for *video_id*.

    The web client is tried alone first: it exposes a muxed 360p MP4
    (itag 18) that is usually free of pre-roll gates, so playback can start
    immediately.  Only when that pass has no format usable within
    _MAX_AVAILABILITY_WAIT do we run one fallback pass with _DEFAULT_CLIENTS
    to obtain the audio-only itags 140/251.

    Returns (CDN URL, HTTP headers to send with it).

    Raises
    ------
    BotCheckError, VideoUnavailableError, FormatUnavailableError,
    TransientResolveError
    """
    started = time.perf_counter()
    clients = _configured_clients()
    first_error: Optional[ResolverError] = None
    formats: list[dict] = []
    extract_calls = 0

    try:
        formats = _extract_formats(video_id, clients, cookiefile)
        extract_calls += 1
    except (BotCheckError, VideoUnavailableError):
        # Both are IP/account or video-wide: another extraction cannot fix
        # them and only adds load on YouTube.
        raise
    except (FormatUnavailableError, TransientResolveError) as exc:
        first_error = exc

    fmt = _select_format(formats, prefer)

    if not _usable_now(fmt):
        fallback = _fallback_clients()
        logger.info(
            "Resolver: %s: clients=%s unusable (gate=%.1fs), trying fallback",
            video_id,
            ",".join(clients),
            _gate_wait(fmt) if fmt else -1.0,
        )
        clients = fallback
        second_error: Optional[ResolverError] = None
        try:
            extra = _extract_formats(video_id, fallback, cookiefile)
            extract_calls += 1
        except (BotCheckError, VideoUnavailableError):
            raise
        except (FormatUnavailableError, TransientResolveError) as exc:
            second_error = exc
        else:
            # Merging keeps the earliest-gated duplicate of the same itag,
            # so an ungated duplicate from the fallback wins.
            fmt = _select_format(formats + extra, prefer)
        if fmt is None and second_error is not None:
            raise second_error

    if fmt is None:
        if first_error is not None:
            raise first_error
        raise FormatUnavailableError(
            f"No supported audio format (itag 251, 140 or 18) found "
            f"for video_id={video_id!r}"
        )

    logger.info(
        "Resolver: %s outcome=%s clients=%s passes=%d extract_ms=%.0f "
        "gate_ms=%.0f itag=%s",
        video_id,
        "ready" if _usable_now(fmt) else "gated",
        ",".join(clients),
        extract_calls,
        (time.perf_counter() - started) * 1000,
        _gate_wait(fmt) * 1000,
        fmt.get("format_id"),
    )

    _wait_until_available(fmt)

    raw_headers = fmt.get("http_headers") or {}
    headers = {k: str(v) for k, v in raw_headers.items() if k in _FORWARDED_HEADERS}
    return fmt["url"], headers or dict(_DEFAULT_HEADERS)


def _pick_resolver_error(errors: list[ResolverError]) -> ResolverError:
    """Return the most actionable error from a parallel race."""
    for cls in (
        BotCheckError,
        VideoUnavailableError,
        TransientResolveError,
        FormatUnavailableError,
    ):
        for exc in errors:
            if isinstance(exc, cls):
                return exc
    return errors[0]


def _resolve_parallel(
    video_id: str,
    prefer: str = "opus",
    cookiefile: Optional[str] = None,
    max_gate_wait: float = _MAX_TAP_GATE_WAIT,
) -> Tuple[str, Dict[str, str]]:
    """
    Race several client sets and return the first ungated format.

    YouTube assigns the pre-roll gate per player response, so a different
    client often produces an ungated duplicate while the first one is still
    gated.  We stop as soon as one set yields a usable format; if every set is
    gated, we merge their formats and wait for the earliest gate.
    """
    started = time.perf_counter()
    formats: list[dict] = []
    errors: list[ResolverError] = []
    best: Optional[tuple[dict, str]] = None
    extract_calls = 0

    pool = _EXTRACT_POOL
    futures = {
        pool.submit(_extract_formats, video_id, clients, cookiefile): clients
        for clients in _FAST_CLIENT_SETS
    }
    pending = set(futures)
    try:
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED, timeout=12)
            if not done:
                errors.append(
                    TransientResolveError(
                        f"parallel extraction for {video_id!r} timed out"
                    )
                )
                break
            for future in done:
                extract_calls += 1
                clients = futures[future]
                try:
                    extra = future.result()
                except ResolverError as exc:
                    errors.append(exc)
                    continue
                except Exception as exc:  # unexpected plugin/network failure
                    errors.append(
                        TransientResolveError(
                            f"parallel extraction for {video_id!r} failed "
                            f"({type(exc).__name__})"
                        )
                    )
                    continue
                formats.extend(extra)
                fmt = _select_format(extra, prefer)
                if _usable_now(fmt):
                    best = (fmt, ",".join(clients))
                    break
            if best is not None:
                break
    finally:
        for future in futures:
            future.cancel()

    if best is None:
        fmt = _select_format(formats, prefer)
        if fmt is None:
            if errors:
                raise _pick_resolver_error(errors)
            raise FormatUnavailableError(
                f"No supported audio format (itag 251, 140 or 18) found "
                f"for video_id={video_id!r}"
            )
        best = (fmt, "merged")

    fmt, winner = best
    logger.info(
        "Resolver: %s outcome=%s clients=parallel(%s) passes=%d "
        "extract_ms=%.0f gate_ms=%.0f itag=%s",
        video_id,
        "ready" if _usable_now(fmt) else "gated",
        winner,
        extract_calls,
        (time.perf_counter() - started) * 1000,
        _gate_wait(fmt) * 1000,
        fmt.get("format_id"),
    )

    _wait_until_available(fmt, max_gate_wait)

    raw_headers = fmt.get("http_headers") or {}
    headers = {k: str(v) for k, v in raw_headers.items() if k in _FORWARDED_HEADERS}
    return fmt["url"], headers or dict(_DEFAULT_HEADERS)


def _resolve_via_ytdlp(
    video_id: str,
    prefer: str = "opus",
    cookiefile: Optional[str] = None,
    parallel: bool = True,
    max_gate_wait: float = _MAX_TAP_GATE_WAIT,
) -> Tuple[str, Dict[str, str]]:
    """Dispatch to the parallel race for normal playback, serial for canary."""
    if parallel and _PARALLEL_RESOLVE:
        return _resolve_parallel(video_id, prefer, cookiefile, max_gate_wait)
    return _resolve_serial(video_id, prefer, cookiefile)


def _wait_until_available(fmt: dict, max_wait: float = _MAX_AVAILABILITY_WAIT) -> None:
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
    if wait > max_wait:
        raise FormatUnavailableError(
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


def _resolve_with_accounts(
    video_id: str,
    prefer: str,
    cookiefile: Optional[str] = None,
    parallel: bool = True,
    max_gate_wait: float = _MAX_TAP_GATE_WAIT,
) -> Tuple[str, Dict[str, str], str]:
    """
    Try the active cookie file, then the reserve one on a bot check.

    Only bot checks trigger a switch: a transient network error is not a
    reason to burn requests on the second account.
    """
    if cookiefile is not None:
        url, headers = _resolve_via_ytdlp(video_id, prefer, cookiefile, parallel=parallel, max_gate_wait=max_gate_wait)
        return url, headers, _active_account()

    primary = os.getenv("YTDLP_COOKIES_FILE", "").strip()
    reserve = os.getenv("YTDLP_RESERVE_COOKIES_FILE", "").strip()
    active = _active_account()

    if active == "reserve":
        candidates = [("reserve", reserve), ("primary", primary)]
    else:
        candidates = [("primary", primary), ("reserve", reserve)]

    seen: set[str] = set()
    last_bot_check: Optional[BotCheckError] = None
    had_cookie_file = False

    for account, path in candidates:
        if not path or path in seen:
            continue
        seen.add(path)
        had_cookie_file = True
        if not os.path.exists(path):
            logger.warning("Resolver: cookies file for %s is missing", account)
            continue
        try:
            url, headers = _resolve_via_ytdlp(video_id, prefer, path, parallel=parallel, max_gate_wait=max_gate_wait)
        except BotCheckError as exc:
            last_bot_check = exc
            continue
        if account != active:
            set_active_account(account)
            _inc("account_switches")
            logger.warning("Resolver: switched to %s cookies", account)
        return url, headers, account

    if last_bot_check is not None:
        raise last_bot_check
    if had_cookie_file:
        # Every configured file was missing/unreadable; surface the real
        # yt-dlp result without cookies rather than inventing an error.
        logger.warning("Resolver: no usable cookies file, resolving anonymously")

    url, headers = _resolve_via_ytdlp(video_id, prefer, None, parallel=parallel, max_gate_wait=max_gate_wait)
    return url, headers, active


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_stream_url(
    video_id: str,
    prefer: str = "opus",
    force: bool = False,
    cookiefile: Optional[str] = None,
    dedicated: bool = False,
    prefetch: bool = False,
) -> str:
    """
    Return a direct HTTPS audio CDN URL for a YouTube video.

    The URL is served by Google's CDN (``*.googlevideo.com``) and only works
    from the IP address of the machine that resolved it.

    Results are cached until shortly before the URL's ``expire`` timestamp.

    Parameters
    ----------
    force:
        Bypass the cache, negative cache and the global bot-check gate.
        Used by the canary and cookie validation.
    cookiefile:
        Use this explicit cookies file instead of the configured accounts.
        Used by /cookies validation.
    dedicated:
        Use the canary's own semaphore instead of the shared user slots.
        Used by the canary so it never delays a tap.
    prefetch:
        Background warm-up.  At most one prefetch may occupy a resolve slot;
        other prefetches wait without taking a slot, so a tap is never queued
        behind background work.

    Raises
    ------
    ValueError
        If *video_id* is empty / not a valid YouTube id or *prefer* is unknown.
    VideoUnavailableError, FormatUnavailableError
        If the video or its audio is not playable.
    BotCheckError
        If YouTube demands a bot check (or it was recently detected).
    TransientResolveError
        If yt-dlp encounters a network or other temporary error.
    """
    video_id = _validate(video_id, prefer)
    key = _cache_key(video_id, prefer)

    if not force:
        negative = cache_get(_negative_key(key))
        if negative is not None:
            _inc("cache_hits")
            _raise_negative(negative, video_id)

        cached = cache_get(key)
        if cached is not None:
            _inc("cache_hits")
            logger.debug("Resolver: cache hit for %s", key)
            return cached

        # A cached URL is still playable after the cookies died, so only
        # block calls that would need a fresh yt-dlp extraction.
        if _bot_check_active():
            raise BotCheckError("YouTube bot check is active; resolver paused")

    # --- Cache miss: resolve once per key, even under concurrent requests ---
    with ExitStack() as stack:
        # A prefetch waits for the guard before it takes a resolve slot, so it
        # can never occupy the slot a tap would use.
        if prefetch and not dedicated:
            stack.enter_context(_PREFETCH_GUARD)
        stack.enter_context(_resolve_lock(key))

        if not force:
            negative = cache_get(_negative_key(key))
            if negative is not None:
                _inc("cache_hits")
                _raise_negative(negative, video_id)
            cached = cache_get(key)
            if cached is not None:
                _inc("cache_hits")
                logger.debug("Resolver: cache filled while waiting for %s", key)
                return cached
            if _bot_check_active():
                raise BotCheckError("YouTube bot check is active; resolver paused")

        logger.info("Resolver: cache miss — resolving via yt-dlp for %s", key)
        semaphore = _CANARY_SEMAPHORE if dedicated else _RESOLVE_SEMAPHORE
        stack.enter_context(semaphore)
        _pace_resolve()
        try:
            stream_url, headers, _account = _resolve_with_accounts(
                video_id,
                prefer,
                cookiefile,
                parallel=not prefetch and not dedicated,
                max_gate_wait=_MAX_TAP_GATE_WAIT,
            )
        except UnavailableError as exc:
            if not force:
                kind = "video" if isinstance(exc, VideoUnavailableError) else "format"
                cache_set(_negative_key(key), kind, ttl=_NEGATIVE_TTL)
            _record_failure(exc)
            raise
        except (BotCheckError, TransientResolveError) as exc:
            _record_failure(exc)
            raise

        ttl = _ttl_for_url(stream_url)
        if ttl > 0:
            cache_set(key, stream_url, ttl=ttl)
            cache_set(f"{key}:headers", json.dumps(headers), ttl=ttl)
            logger.info(
                "Resolver: cached stream URL for %s (TTL=%ds)", key, int(ttl)
            )
        else:
            logger.info("Resolver: not caching %s (URL expires too soon)", key)
        _record_success()

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
    cache_delete(_negative_key(key))


def verify_cookiefile(
    path: str,
    video_id: str,
    prefer: str = "opus",
) -> bool:
    """
    Prove that *path* is a working cookies file without touching global state.

    Only extraction and format selection are performed: waiting for a pre-roll
    gate is pointless here and would make /cookies slow.
    """
    video_id = _validate(video_id, prefer)
    formats = _extract_formats(video_id, _configured_clients(), cookiefile=path)
    if _select_format(formats, prefer) is None:
        raise FormatUnavailableError(
            f"No supported audio format for video_id={video_id!r}"
        )
    return True
