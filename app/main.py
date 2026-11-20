"""
app/main.py — FastAPI app: API routes, Telegram auth, audio relay, bot webhook
LammotBot Telegram Mini App Music Player

Rules:
  - The user id ALWAYS comes from verified Telegram initData, never from the
    URL. Paths do not contain telegram_user_id.
  - Blocking calls (ytmusicapi, yt-dlp) run in the thread pool so one slow
    resolve does not stop the event loop for everyone else.
  - Upstream error details are logged, never returned to the client.
  - Audio is relayed through /audio/{video_id}: googlevideo URLs only work
    from the IP that resolved them (the server), not from the user's phone.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import sys
import tempfile
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path as FsPath
from typing import Any, AsyncIterator, Deque, Dict, List, Literal, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import auth, bot, database, radio, resolver, search
from app.prefetch import SearchPrefetcher

logger = logging.getLogger("lammot.api")
# Railway labels stderr as [err]; expected INFO lines must go to stdout.
logging.basicConfig(stream=sys.stdout, level=os.getenv("LOG_LEVEL", "INFO"))

# httpx logs every request URL at INFO; Bot API URLs contain the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Configuration (environment only; no defaults for secrets)
# ---------------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    # Fail fast: without the token anyone could forge initData signatures.
    raise RuntimeError(
        "BOT_TOKEN environment variable is not set. "
        "Set it in Railway variables (or .env locally) before starting the app."
    )

# How long initData stays valid, seconds (replay protection)
INIT_DATA_MAX_AGE = int(os.getenv("INIT_DATA_MAX_AGE", "86400"))  # 24 h

# Public HTTPS address of this app, e.g. https://lammot.up.railway.app
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
if PUBLIC_URL and not PUBLIC_URL.startswith("https://"):
    raise RuntimeError("PUBLIC_URL must start with https:// (Telegram requires HTTPS)")

# Optional: normally read from getMe on startup
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@") or None
# Optional: short name of a Mini App created with /newapp in BotFather.
# Empty → share links open the bot's Main Mini App (t.me/<bot>?startapp=...).
MINI_APP_SHORT_NAME = os.getenv("MINI_APP_SHORT_NAME", "").strip() or None

# The frontend is served from the same origin, so CORS only matters for
# tools/tests; an empty value falls back to PUBLIC_URL.
ALLOWED_ORIGINS = [
    o.strip()
    for o in (os.getenv("ALLOWED_ORIGINS") or PUBLIC_URL or "https://lammot.up.railway.app").split(",")
    if o.strip()
]

ENABLE_DOCS = os.getenv("ENABLE_DOCS", "") == "1"

VIDEO_ID_PATTERN = r"^[A-Za-z0-9_-]{11}$"
_VIDEO_ID_RE = re.compile(VIDEO_ID_PATTERN)
SHARE_TOKEN_PATTERN = r"^[A-Za-z0-9_-]{16,64}$"
_RANGE_RE = re.compile(r"^bytes=\d*-\d*$")

MAX_EXCLUDE = 50          # ids the client may pass to /api/radio
RADIO_HISTORY = 50        # radio skips the user's last 50 played tracks
MAX_PLAYLISTS = 100       # per user
AUDIO_LINK_TTL = 6 * 3600  # signed /audio links live 6 hours

# Resolver canary / cookie replacement
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


CANARY_INTERVAL_SECONDS = max(60, _env_int("CANARY_INTERVAL_SECONDS", 900))
CANARY_START_DELAY_SECONDS = max(1, _env_int("CANARY_START_DELAY_SECONDS", 15))
COOKIE_TEST_TIMEOUT_SECONDS = max(5.0, _env_float("COOKIE_TEST_TIMEOUT_SECONDS", 45.0))
COOKIES_MAX_BYTES = 100 * 1024

# Server-side prefetch of the top search results.  A single background worker
# keeps bot-check risk and Deno/CPU load bounded; both limits are clamped.
SEARCH_PREFETCH_ENABLED = os.getenv("SEARCH_PREFETCH_ENABLED", "1") == "1"
SEARCH_PREFETCH_TOP_N = min(5, max(1, _env_int("SEARCH_PREFETCH_TOP_N", 3)))
SEARCH_PREFETCH_QUEUE_SIZE = min(50, max(1, _env_int("SEARCH_PREFETCH_QUEUE_SIZE", 12)))
SEARCH_PREFETCH_SETTLE_SECONDS = min(2.0, max(0.0, _env_float(
    "SEARCH_PREFETCH_SETTLE_SECONDS", 0.5
)))
SEARCH_PREFETCH_DEDUPE_SECONDS = min(5.0, max(0.0, _env_float(
    "SEARCH_PREFETCH_DEDUPE_SECONDS", 1.0
)))


# Alert only on meaningful transitions, never once per failed track.
_alert_lock = asyncio.Lock()
_last_alert_seq = 0
_last_alert_status = "unknown"
_last_alert_account = "primary"

AUDIO_KEY = auth.derive_key(BOT_TOKEN, b"lammot-audio")
WEBHOOK_SECRET = bot.webhook_secret(BOT_TOKEN)

FRONTEND_DIR = FsPath(__file__).resolve().parent.parent / "frontend"

# Filled from getMe on startup (or BOT_USERNAME); used for share links
_bot_username: Optional[str] = BOT_USERNAME


# ---------------------------------------------------------------------------
# Shared HTTP client (CDN relay + Bot API). Created lazily so tests can
# replace it and so importing the module does not open sockets.
# ---------------------------------------------------------------------------
_http_client: Optional[httpx.AsyncClient] = None
# Strong references to fire-and-forget CDN warm-up tasks, so the event loop
# does not garbage-collect them before they finish.
_warm_tasks: set[asyncio.Task] = set()
# Background prefetcher for the top search results; None when disabled.
_search_prefetcher: Optional[SearchPrefetcher] = None


def _get_http() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, read=60.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
    return _http_client


async def _warm_cdn(url: str, headers: Dict[str, str]) -> None:
    """
    Send a 1-byte Range request to the CDN URL returned by the resolver.

    This happens after /api/stream has already answered, while the frontend
    is still setting ``audio.src``.  It opens the DNS/TLS/HTTP connection and
    fills the shared keep-alive pool, so the first /audio request does not pay
    that cost.  Never logs the URL or headers.
    """
    if not url:
        return
    warm_headers = dict(headers)
    warm_headers["Range"] = "bytes=0-1"
    client = _get_http()
    started = time.perf_counter()
    try:
        resp = await client.send(
            client.build_request("GET", url, headers=warm_headers), stream=True
        )
        try:
            logger.info(
                "cdn_warm status=%s ttfb_ms=%.0f",
                resp.status_code,
                (time.perf_counter() - started) * 1000,
            )
        finally:
            await resp.aclose()
    except httpx.HTTPError as exc:
        logger.debug("cdn_warm failed: %s", type(exc).__name__)
    except Exception as exc:  # a warm-up must never break a request path
        logger.debug("cdn_warm failed: %s", type(exc).__name__)


def _schedule_cdn_warm(url: str, headers: Dict[str, str]) -> None:
    task = asyncio.create_task(_warm_cdn(url, headers))
    _warm_tasks.add(task)
    task.add_done_callback(_warm_tasks.discard)


async def _prefetch_resolve(
    video_id: str, fmt: str, should_stop=None
) -> tuple[str, Dict[str, str]]:
    """Resolve through the resolver's background path, then fetch its headers."""
    url = await run_in_threadpool(
        resolver.get_stream_url,
        video_id,
        fmt,
        prefetch=True,
        should_stop=should_stop,
    )
    return url, resolver.get_stream_headers(video_id, fmt)


def _prefetch_warm(url: str, headers: Dict[str, str]) -> None:
    _schedule_cdn_warm(url, headers)


def _owner_tg_id() -> Optional[int]:
    raw = os.getenv("OWNER_TG_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("OWNER_TG_ID is not an integer; owner commands and alerts are disabled")
        return None


def _canary_video_ids() -> List[str]:
    raw = os.getenv("CANARY_VIDEO_IDS", "")
    ids = [v.strip() for v in raw.split(",") if _VIDEO_ID_RE.match(v.strip())]
    return ids[:5]


def _cookie_path(target: str) -> str:
    env = "YTDLP_COOKIES_FILE" if target == "primary" else "YTDLP_RESERVE_COOKIES_FILE"
    return os.getenv(env, "").strip()


async def _send_owner_message(text: str) -> None:
    owner = _owner_tg_id()
    if owner is None:
        return
    try:
        await bot.call(_get_http(), BOT_TOKEN, "sendMessage", {
            "chat_id": owner,
            "text": text,
            "disable_web_page_preview": True,
        })
    except bot.TelegramError as exc:
        logger.warning("Owner alert failed: %s", exc)


async def _alert_on_transition() -> None:
    """Send at most one Telegram alert per meaningful state transition."""
    global _last_alert_seq, _last_alert_status, _last_alert_account
    async with _alert_lock:
        state = resolver.get_state()
        seq = int(state.get("transition_seq") or 0)
        status = str(state.get("status") or "unknown")
        account = str(state.get("active_account") or "primary")
        if seq == _last_alert_seq:
            return
        _last_alert_seq = seq

        text = ""
        if account != _last_alert_account:
            text = f"Resolver switched to the {account} cookies account."
        elif status == "bot_check" and _last_alert_status != "bot_check":
            text = (
                "YouTube bot-check: cookies are not working. "
                "Upload a fresh cookies.txt with /cookies."
            )
        elif status == "ok" and _last_alert_status == "bot_check":
            text = "Resolver is working again."
        elif status == "degraded" and _last_alert_status == "ok":
            text = "Resolver is degraded: some tracks cannot be resolved right now."

        _last_alert_status = status
        _last_alert_account = account
        if text:
            await _send_owner_message(text)


def _resolver_status_from(results: List[str]) -> str:
    if any(r == "ok" for r in results):
        return "ok"
    if results and all(r == "bot_check" for r in results):
        return "bot_check"
    return "degraded"


async def _run_canary_once() -> None:
    video_ids = _canary_video_ids()
    if not video_ids:
        return
    results: List[str] = []
    for video_id in video_ids:
        try:
            await run_in_threadpool(
                resolver.get_stream_url, video_id, "opus", True, dedicated=True
            )
            results.append("ok")
        except resolver.BotCheckError:
            results.append("bot_check")
        except resolver.TransientResolveError:
            results.append("transient")
        except Exception:
            results.append("other")
    status = _resolver_status_from(results)
    if status == "ok":
        resolver.set_status("ok", reason="canary_ok")
    elif status == "bot_check":
        resolver.set_status("bot_check", reason="canary_bot_check")
    else:
        resolver.set_status("degraded", reason="canary_degraded")
    await _alert_on_transition()


async def _canary_loop() -> None:
    await asyncio.sleep(CANARY_START_DELAY_SECONDS)
    while True:
        try:
            await _run_canary_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Canary check failed")
        await asyncio.sleep(CANARY_INTERVAL_SECONDS)


async def _stats_loop() -> None:
    while True:
        try:
            await asyncio.sleep(3600)
            logger.info("Resolver stats: %s", resolver.stats_snapshot(reset=True))
        except asyncio.CancelledError:
            raise


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global _bot_username, _http_client, _search_prefetcher
    await database.init_db()

    if PUBLIC_URL:
        try:
            username = await bot.setup(_get_http(), BOT_TOKEN, PUBLIC_URL)
            _bot_username = _bot_username or username
            logger.info("Telegram webhook and menu button set for %s", PUBLIC_URL)
        except bot.TelegramError as exc:
            logger.warning("Telegram setup failed: %s", exc)
    else:
        logger.warning("PUBLIC_URL is not set: webhook not configured, /start will not answer")

    cache_dir = os.getenv("YTDLP_CACHE_DIR", "").strip()
    if cache_dir:
        try:
            FsPath(cache_dir).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("Could not create YTDLP_CACHE_DIR: %s", type(exc).__name__)

    tasks: List[asyncio.Task] = []
    if _canary_video_ids():
        tasks.append(asyncio.create_task(_canary_loop()))
    tasks.append(asyncio.create_task(_stats_loop()))

    if SEARCH_PREFETCH_ENABLED:
        _search_prefetcher = SearchPrefetcher(
            _prefetch_resolve,
            _prefetch_warm,
            top_n=SEARCH_PREFETCH_TOP_N,
            queue_size=SEARCH_PREFETCH_QUEUE_SIZE,
            settle_seconds=SEARCH_PREFETCH_SETTLE_SECONDS,
            dedupe_seconds=SEARCH_PREFETCH_DEDUPE_SECONDS,
        )
        await _search_prefetcher.start()

    try:
        yield
    finally:
        if _search_prefetcher is not None:
            await _search_prefetcher.stop()
            _search_prefetcher = None
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        if _http_client is not None:
            await _http_client.aclose()
            _http_client = None


app = FastAPI(
    title="LammotBot API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url=None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
)


# ---------------------------------------------------------------------------
# Security headers (pure ASGI, so streaming responses pass through untouched)
# ---------------------------------------------------------------------------
CSP = (
    "default-src 'self'; "
    "script-src 'self' https://telegram.org; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' https: data: blob:; "
    "media-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'none'"
)


class SecurityHeadersMiddleware:
    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                extra = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ]
                if path.startswith("/api/"):
                    extra.append((b"cache-control", b"no-store"))
                elif not path.startswith(("/audio/", "/telegram/")):
                    # Static frontend: always revalidate so updates reach
                    # Telegram's aggressively caching webviews
                    extra.append((b"content-security-policy", CSP.encode()))
                    extra.append((b"cache-control", b"no-cache"))
                present = {k.lower() for k, _ in headers}
                headers.extend((k, v) for k, v in extra if k not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,          # auth is a header, not a cookie
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


# ---------------------------------------------------------------------------
# Rate limiting (in-memory, per Telegram user; one process on Railway)
# ---------------------------------------------------------------------------
class RateLimiter:
    def __init__(self, limit: int, window: float = 60.0):
        self.limit = limit
        self.window = window
        self._hits: Dict[int, Deque[float]] = defaultdict(deque)

    def check(self, key: int) -> None:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            raise HTTPException(status_code=429, detail="Too many requests, try again in a minute")
        hits.append(now)

    def reset(self) -> None:
        self._hits.clear()


search_limiter = RateLimiter(limit=60)
stream_limiter = RateLimiter(limit=60)


# ---------------------------------------------------------------------------
# Authentication via Telegram initData
# ---------------------------------------------------------------------------
def verify_telegram_init_data(
    authorization: Optional[str] = Header(None),
) -> Dict[str, str]:
    if not authorization or not authorization.startswith("tma "):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header. Expected 'tma <initData>'",
        )
    try:
        return auth.verify_init_data(authorization[4:], BOT_TOKEN, INIT_DATA_MAX_AGE)
    except auth.AuthError as exc:
        logger.info("Auth rejected: %s", exc)
        raise HTTPException(status_code=401, detail=str(exc)) from None


def current_user(data: Dict[str, str] = Depends(verify_telegram_init_data)) -> auth.TelegramUser:
    try:
        return auth.parse_user(data)
    except auth.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None


async def db_user(user: auth.TelegramUser = Depends(current_user)) -> auth.TelegramUser:
    """current_user + make sure the users row exists (FKs need it)."""
    await database.get_or_create_user(user.id, user.first_name, user.username)
    return user


# ---------------------------------------------------------------------------
# Models and helpers
# ---------------------------------------------------------------------------
class TrackIn(BaseModel):
    video_id: str = Field(..., pattern=VIDEO_ID_PATTERN)
    title: str = Field("", max_length=300)
    artist: str = Field("", max_length=200)
    duration_seconds: int = Field(0, ge=0, le=24 * 3600)
    # https only: the value ends up in an <img src> on other users' screens
    thumbnail_url: str = Field("", max_length=1000, pattern=r"^(https://\S+)?$")


class HistoryIn(BaseModel):
    kind: Literal["search", "play"]
    query: Optional[str] = Field(None, max_length=200)
    track: Optional[TrackIn] = None


class PlaylistIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


def _ok(data: Any) -> Dict[str, Any]:
    return {"ok": True, "data": data}


def _query_hash(query: str) -> str:
    """Short, non-reversible key to correlate one search without logging it."""
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:10]


def _error_response(status: int, code: str, detail: str) -> JSONResponse:
    """Error body with a stable machine-readable code next to the human text."""
    return JSONResponse(
        status_code=status,
        content={"ok": False, "detail": detail, "code": code},
    )


def _track_out(row: Dict[str, Any]) -> Dict[str, Any]:
    """DB row (track_id, duration) → the one public track shape used everywhere."""
    return {
        "video_id": row["track_id"],
        "title": row.get("title") or "",
        "artist": row.get("artist") or "",
        "duration_seconds": int(row.get("duration") or 0),
        "thumbnail_url": row.get("thumbnail_url") or "",
    }


def _playlist_out(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "share_token": row["share_token"],
        "track_count": int(row.get("track_count") or 0),
        "created_at": row.get("created_at"),
    }


def _share_base() -> Optional[str]:
    if not _bot_username:
        return None
    if MINI_APP_SHORT_NAME:
        return f"https://t.me/{_bot_username}/{MINI_APP_SHORT_NAME}?startapp="
    return f"https://t.me/{_bot_username}?startapp="


def _validate_video_ids(ids: List[str]) -> List[str]:
    if len(ids) > MAX_EXCLUDE:
        raise HTTPException(status_code=422, detail=f"exclude accepts at most {MAX_EXCLUDE} ids")
    if any(not _VIDEO_ID_RE.match(v) for v in ids):
        raise HTTPException(status_code=422, detail="Invalid video_id in exclude list")
    return ids


def _playlist_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Playlist not found")


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"ok": True}


@app.get("/health", include_in_schema=False)
async def health():
    """Last known resolver state; deliberately never triggers a resolve."""
    return {"ok": True, "resolver": resolver.get_state()}


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------
@app.get("/api/me")
async def api_me(user: auth.TelegramUser = Depends(db_user)):
    return _ok({
        "user": {"id": user.id, "first_name": user.first_name, "username": user.username},
        "share_base": _share_base(),
    })


@app.delete("/api/me", status_code=204)
async def api_delete_me(
    confirm: str = Query("", max_length=10),
    user: auth.TelegramUser = Depends(current_user),
):
    """Delete everything stored about the caller (GDPR right to erasure)."""
    if confirm != "true":
        raise HTTPException(status_code=400, detail="Pass confirm=true to delete all your data")
    await database.delete_user_data(user.id)
    # Audit trail: the fact and the id only, no name or username
    logger.info("User data deleted: telegram_user_id=%s", user.id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Search, stream, radio
# ---------------------------------------------------------------------------
@app.get("/api/search")
async def api_search(
    q: str = Query(..., min_length=1, max_length=200, description="Search query"),
    limit: int = Query(10, ge=1, le=25),
    format: Literal["opus", "aac"] = Query("opus", alias="format"),
    user: auth.TelegramUser = Depends(current_user),
):
    search_limiter.check(user.id)
    q_hash = _query_hash(q)
    try:
        results = await run_in_threadpool(search.search_songs, q, limit)
    except Exception:
        logger.exception("Search failed for q_hash=%s", q_hash)
        raise HTTPException(status_code=502, detail="Search service unavailable")
    enqueued = 0
    if _search_prefetcher is not None:
        try:
            count = _search_prefetcher.enqueue_results(
                results, format, owner=user.id, request_key=q_hash
            )
            enqueued = count if isinstance(count, int) else 0
        except Exception:
            # A prefetch bug must never turn a successful search into a 500.
            logger.exception("search prefetch enqueue failed")
    logger.info(
        "api_search q_hash=%s format=%s results=%d prefetch_enqueued=%d",
        q_hash,
        format,
        len(results),
        enqueued,
    )
    return _ok(results)


@app.get("/api/stream/{video_id}")
async def api_stream(
    response: Response,
    video_id: str = Path(..., pattern=VIDEO_ID_PATTERN),
    fmt: str = Query("opus", alias="format", pattern="^(opus|aac)$"),
    prefetch: bool = Query(False, description="Background warm-up request"),
    user: auth.TelegramUser = Depends(current_user),
):
    """
    Resolve the track now (so errors show up before playback) and return a
    signed same-origin link the <audio> tag can play.
    """
    stream_limiter.check(user.id)
    started = time.perf_counter()
    try:
        if prefetch:
            cdn_url = await run_in_threadpool(
                resolver.get_stream_url, video_id, fmt, prefetch=True
            )
        else:
            cdn_url = await run_in_threadpool(resolver.get_stream_url, video_id, fmt)
    except resolver.BotCheckError:
        logger.warning(
            "Stream: bot check video_id=%s format=%s", video_id, fmt
        )
        await _alert_on_transition()
        return _error_response(
            503, "bot_check", "Music service is temporarily unavailable"
        )
    except resolver.UnavailableError:
        logger.info("Stream: unavailable video_id=%s format=%s", video_id, fmt)
        return _error_response(404, "unavailable", "This track is not available")
    except resolver.TransientResolveError:
        logger.warning(
            "Stream: transient resolve error video_id=%s format=%s", video_id, fmt
        )
        await _alert_on_transition()
        return _error_response(502, "upstream_error", "Stream service unavailable")
    except Exception:
        logger.exception(
            "Stream resolve failed for video_id=%s format=%s", video_id, fmt
        )
        return _error_response(502, "upstream_error", "Stream service unavailable")
    await _alert_on_transition()

    resolve_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "api_stream video_id=%s format=%s prefetch=%s resolve_ms=%.0f",
        video_id,
        fmt,
        prefetch,
        resolve_ms,
    )
    response.headers["Server-Timing"] = f"resolve;dur={resolve_ms:.1f}"

    # The browser will ask /audio in a moment; warm DNS/TLS/keep-alive now
    # so the relay's first CDN request is faster.  Never blocks this response.
    _schedule_cdn_warm(cdn_url, resolver.get_stream_headers(video_id, fmt))

    token = auth.sign_audio_token(AUDIO_KEY, video_id, fmt, user.id, AUDIO_LINK_TTL)
    return _ok({
        "video_id": video_id,
        "format": fmt,
        "stream_url": f"/audio/{video_id}?t={token}",
    })


async def _relay(
    resp: httpx.Response,
    video_id: str = "",
    fmt: str = "",
    resolve_ms: float = 0.0,
    cdn_ms: float = 0.0,
) -> AsyncIterator[bytes]:
    started = time.perf_counter()
    first_chunk_ms: Optional[float] = None
    total_bytes = 0
    try:
        async for chunk in resp.aiter_raw():
            if first_chunk_ms is None:
                first_chunk_ms = (time.perf_counter() - started) * 1000
            total_bytes += len(chunk)
            yield chunk
    finally:
        await resp.aclose()
        logger.info(
            "audio_relay video_id=%s format=%s resolve_ms=%.0f cdn_ms=%.0f "
            "first_chunk_ms=%.0f total_ms=%.0f bytes=%d",
            video_id,
            fmt,
            resolve_ms,
            cdn_ms,
            first_chunk_ms if first_chunk_ms is not None else -1.0,
            (time.perf_counter() - started) * 1000,
            total_bytes,
        )


@app.get("/audio/{video_id}", include_in_schema=False)
async def audio_relay(
    request: Request,
    video_id: str = Path(..., pattern=VIDEO_ID_PATTERN),
    t: str = Query(..., max_length=200),
):
    """
    Relay audio bytes from the CDN (Range requests supported, so seeking works).
    Nothing is written to disk.
    """
    try:
        fmt, _user_id = auth.verify_audio_token(AUDIO_KEY, video_id, t)
    except auth.AuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None

    logger.info("audio_start video_id=%s format=%s", video_id, fmt)
    resolve_ms = 0.0
    cdn_ms = 0.0

    range_header = request.headers.get("range")
    if range_header and not _RANGE_RE.match(range_header):
        raise HTTPException(status_code=416, detail="Unsupported Range header")
    forced_range = not range_header

    client = _get_http()
    resp: Optional[httpx.Response] = None
    for attempt in (1, 2):
        resolve_started = time.perf_counter()
        try:
            cdn_url = await run_in_threadpool(resolver.get_stream_url, video_id, fmt)
        except resolver.BotCheckError:
            logger.warning("Audio: bot check video_id=%s format=%s", video_id, fmt)
            await _alert_on_transition()
            raise HTTPException(status_code=503, detail="Audio source temporarily unavailable")
        except resolver.UnavailableError:
            logger.info("Audio: unavailable video_id=%s format=%s", video_id, fmt)
            raise HTTPException(status_code=404, detail="No playable audio stream for this track")
        except resolver.TransientResolveError:
            logger.warning("Audio: transient resolve error video_id=%s format=%s", video_id, fmt)
            await _alert_on_transition()
            raise HTTPException(status_code=502, detail="Audio source unavailable")
        except Exception:
            logger.exception("Audio: resolve failed for video_id=%s", video_id)
            raise HTTPException(status_code=502, detail="Audio source unavailable")

        resolve_ms = (time.perf_counter() - resolve_started) * 1000
        headers = resolver.get_stream_headers(video_id, fmt)
        if range_header:
            headers["Range"] = range_header
        else:
            # Some webviews fetch media without a Range header.  A plain 200
            # response gets buffered by the Cloudflare tunnel for many seconds,
            # while a 206 streams immediately.  Ask the CDN for the first range
            # so the relay always returns a streaming 206 response.
            headers["Range"] = "bytes=0-"
        cdn_started = time.perf_counter()
        try:
            resp = await client.send(client.build_request("GET", cdn_url, headers=headers), stream=True)
        except httpx.HTTPError as exc:
            logger.warning("Audio: CDN request failed for video_id=%s: %s", video_id, type(exc).__name__)
            raise HTTPException(status_code=502, detail="Audio source unavailable") from None
        cdn_ms = (time.perf_counter() - cdn_started) * 1000

        if resp.status_code in (403, 410) and attempt == 1:
            # URL expired or was revoked: forget it and resolve again once
            await resp.aclose()
            resolver.invalidate(video_id, fmt)
            continue
        break

    assert resp is not None
    if resp.status_code == 416:
        await resp.aclose()
        return Response(status_code=416)
    if resp.status_code not in (200, 206):
        logger.warning("Audio: CDN answered %s for video_id=%s", resp.status_code, video_id)
        await resp.aclose()
        raise HTTPException(status_code=502, detail="Audio source unavailable")

    out_headers = {
        k: resp.headers[k]
        for k in ("content-type", "content-length", "content-range", "accept-ranges")
        if k in resp.headers
    }
    out_headers.setdefault("accept-ranges", "bytes")
    out_headers.setdefault("content-type", "audio/webm" if fmt == "opus" else "audio/mp4")
    out_headers["Server-Timing"] = (
        f"resolve;dur={resolve_ms:.1f}, cdn;dur={cdn_ms:.1f}"
    )
    status_code = resp.status_code
    if forced_range and status_code == 206:
        # The client asked for the whole file: answer 200 and drop the
        # Content-Range it never requested.
        status_code = 200
        out_headers.pop("content-range", None)
    return StreamingResponse(
        _relay(resp, video_id, fmt, resolve_ms, cdn_ms),
        status_code=status_code,
        headers=out_headers,
    )


@app.get("/api/radio/{video_id}")
async def api_radio(
    video_id: str = Path(..., pattern=VIDEO_ID_PATTERN),
    exclude: List[str] = Query(
        default=[],
        description="video_ids already in the client's queue (up to 50)",
    ),
    limit: int = Query(5, ge=1, le=20),
    user: auth.TelegramUser = Depends(db_user),
):
    queued = _validate_video_ids(exclude)
    played = await database.get_play_history(user.id, limit=RADIO_HISTORY)
    # Order-preserving de-duplication: client queue first, then play history
    history = list(dict.fromkeys([*queued, *(row["track_id"] for row in played)]))
    try:
        queue = await run_in_threadpool(radio.get_radio_queue, video_id, history, limit)
    except Exception:
        logger.exception("Radio failed for video_id=%s", video_id)
        raise HTTPException(status_code=502, detail="Radio service unavailable")
    return _ok(queue)


# ---------------------------------------------------------------------------
# Favorites
# ---------------------------------------------------------------------------
@app.get("/api/favorites")
async def api_favorites(user: auth.TelegramUser = Depends(db_user)):
    rows = await database.get_favorites(user.id)
    return _ok([_track_out(r) for r in rows])


@app.post("/api/favorites")
async def api_favorites_add(track: TrackIn, user: auth.TelegramUser = Depends(db_user)):
    added = await database.add_favorite(
        user.id, track.video_id, track.title, track.artist,
        track.duration_seconds, track.thumbnail_url or None,
    )
    return _ok({"added": added})


@app.delete("/api/favorites/{video_id}")
async def api_favorites_remove(
    video_id: str = Path(..., pattern=VIDEO_ID_PATTERN),
    user: auth.TelegramUser = Depends(db_user),
):
    removed = await database.remove_favorite(user.id, video_id)
    return _ok({"removed": removed})


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
@app.get("/api/history")
async def api_history(user: auth.TelegramUser = Depends(db_user)):
    """Last 10 distinct search queries + last 20 played tracks."""
    searches = await database.get_search_history(user.id, limit=database.SEARCH_HISTORY_LIMIT)
    seen: set = set()
    recent: List[str] = []
    for row in searches:
        key = row["query"].strip().lower()
        if key and key not in seen:
            seen.add(key)
            recent.append(row["query"])
        if len(recent) == 10:
            break
    played = await database.get_play_history(user.id, limit=20)
    return _ok({"searches": recent, "played": [_track_out(r) for r in played]})


@app.post("/api/history")
async def api_history_add(item: HistoryIn, user: auth.TelegramUser = Depends(db_user)):
    if item.kind == "search":
        query = (item.query or "").strip()
        if not query:
            raise HTTPException(status_code=422, detail="query is required for kind=search")
        await database.add_search_query(user.id, query)
    else:
        if item.track is None:
            raise HTTPException(status_code=422, detail="track is required for kind=play")
        t = item.track
        await database.add_played_track(user.id, t.video_id, t.title, t.artist, t.thumbnail_url or None)
    return _ok(None)


@app.delete("/api/history", status_code=204)
async def api_history_clear(
    kind: str = Query("all", alias="type", pattern="^(search|play|all)$"),
    user: auth.TelegramUser = Depends(current_user),
):
    await database.clear_history(user.id, kind)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Playlists. Every call checks that the playlist belongs to the caller;
# someone else's playlist looks exactly like a missing one (404).
# ---------------------------------------------------------------------------
@app.get("/api/playlists")
async def api_playlists(user: auth.TelegramUser = Depends(db_user)):
    rows = await database.get_playlists(user.id)
    return _ok([_playlist_out(r) for r in rows])


@app.post("/api/playlists")
async def api_playlists_create(body: PlaylistIn, user: auth.TelegramUser = Depends(db_user)):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Playlist name cannot be empty")
    if len(await database.get_playlists(user.id)) >= MAX_PLAYLISTS:
        raise HTTPException(status_code=409, detail=f"You can have up to {MAX_PLAYLISTS} playlists")
    row = await database.create_playlist(user.id, name)
    return _ok(_playlist_out(row))


# Public on purpose: anyone with the link can view (read-only).
# Declared before /api/playlists/{playlist_id}.
@app.get("/api/playlists/shared/{share_token}")
async def api_playlist_shared(share_token: str = Path(..., pattern=SHARE_TOKEN_PATTERN)):
    playlist = await database.get_shared_playlist(share_token)
    if playlist is None:
        raise _playlist_not_found()
    return _ok({
        "name": playlist["name"],
        "tracks": [_track_out(t) for t in playlist["tracks"]],
    })


@app.get("/api/playlists/{playlist_id}")
async def api_playlist(
    playlist_id: int = Path(..., ge=1),
    user: auth.TelegramUser = Depends(db_user),
):
    try:
        meta = await database.get_playlist(user.id, playlist_id)
        tracks = await database.get_playlist_tracks(user.id, playlist_id)
    except database.PlaylistNotFoundError:
        raise _playlist_not_found() from None
    return _ok({"playlist": _playlist_out(meta), "tracks": [_track_out(t) for t in tracks]})


@app.delete("/api/playlists/{playlist_id}", status_code=204)
async def api_playlist_delete(
    playlist_id: int = Path(..., ge=1),
    user: auth.TelegramUser = Depends(db_user),
):
    try:
        await database.delete_playlist(user.id, playlist_id)
    except database.PlaylistNotFoundError:
        raise _playlist_not_found() from None
    return Response(status_code=204)


@app.post("/api/playlists/{playlist_id}/tracks")
async def api_playlist_add_track(
    track: TrackIn,
    playlist_id: int = Path(..., ge=1),
    user: auth.TelegramUser = Depends(db_user),
):
    try:
        added = await database.add_track_to_playlist(
            user.id, playlist_id, track.video_id, track.title, track.artist,
            track.duration_seconds, track.thumbnail_url or None,
        )
    except database.PlaylistNotFoundError:
        raise _playlist_not_found() from None
    return _ok({"added": added})


@app.delete("/api/playlists/{playlist_id}/tracks/{video_id}")
async def api_playlist_remove_track(
    playlist_id: int = Path(..., ge=1),
    video_id: str = Path(..., pattern=VIDEO_ID_PATTERN),
    user: auth.TelegramUser = Depends(db_user),
):
    try:
        removed = await database.remove_track_from_playlist(user.id, playlist_id, video_id)
    except database.PlaylistNotFoundError:
        raise _playlist_not_found() from None
    return _ok({"removed": removed})


# ---------------------------------------------------------------------------
# Telegram bot webhook (/start → "Open Lammot" button, /cookies → replace)
# ---------------------------------------------------------------------------
def _validate_netscape_cookies(data: bytes) -> Optional[str]:
    """Return an error message, or None when the file looks like YouTube cookies."""
    if not data:
        return "Файл пустой."
    if len(data) > COOKIES_MAX_BYTES:
        return "Файл больше 100 KB."
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "Файл не похож на текстовый Netscape cookies."

    found_youtube = False
    for raw_line in text.splitlines():
        line = raw_line.strip("\r")
        if not line:
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            return "Некорректный формат Netscape cookies."
        domain = fields[0].strip().lower().lstrip(".")
        if domain == "youtube.com" or domain.endswith(".youtube.com"):
            found_youtube = True
    if not found_youtube:
        return "В файле нет cookies для youtube.com."
    return None


async def _process_cookies_update(command: Dict[str, Any]) -> None:
    """
    Owner-only /cookies flow: validate a document, test it, then atomically
    replace the active (or reserve) cookies file.  Never replies to strangers.
    """
    owner = _owner_tg_id()
    user_id = command.get("user_id")
    if owner is None or user_id != owner:
        return

    client = _get_http()
    chat_id = command.get("chat_id")
    message_id = command.get("message_id")
    target = command.get("target") or "primary"
    document = command.get("document") or {}
    target_path = _cookie_path(target)

    try:
        if not document:
            await bot.send_message(
                client, BOT_TOKEN, chat_id,
                "Отправь файл cookies.txt документом с подписью /cookies "
                "(для резервного — /cookies reserve).",
            )
            return
        if not target_path:
            await bot.send_message(
                client, BOT_TOKEN, chat_id,
                f"Путь cookies для {target} не настроен на сервере.",
            )
            return
        canaries = _canary_video_ids()
        if not canaries:
            await bot.send_message(
                client, BOT_TOKEN, chat_id,
                "CANARY_VIDEO_IDS не настроен: проверка cookies невозможна.",
            )
            return
        file_size = int(document.get("file_size") or 0)
        if file_size > COOKIES_MAX_BYTES:
            await bot.send_message(
                client, BOT_TOKEN, chat_id, "Файл больше 100 KB."
            )
            return

        data = await bot.download_document(
            client, BOT_TOKEN, str(document.get("file_id") or "")
        )
        format_error = _validate_netscape_cookies(data)
        if format_error:
            logger.info(
                "cookies: user=%s target=%s result=invalid_format", user_id, target
            )
            await bot.send_message(client, BOT_TOKEN, chat_id, format_error)
            return

        parent = FsPath(target_path).parent
        parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix=".cookies-", suffix=".tmp", dir=str(parent)
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
            try:
                await asyncio.wait_for(
                    run_in_threadpool(
                        resolver.verify_cookiefile, temp_path, canaries[0]
                    ),
                    timeout=COOKIE_TEST_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                logger.info(
                    "cookies: user=%s target=%s result=test_failed error=%s",
                    user_id, target, type(exc).__name__,
                )
                await bot.send_message(
                    client, BOT_TOKEN, chat_id,
                    "Проверка не прошла: cookies не дают воспроизвести тестовый трек. "
                    "Старый файл не изменён.",
                )
                return

            os.replace(temp_path, target_path)
            if target == "primary":
                resolver.set_active_account("primary")
                resolver.set_status("ok", reason="cookies_replaced")
            else:
                resolver.reset_state()
            logger.info(
                "cookies: user=%s target=%s result=replaced", user_id, target
            )
            await bot.send_message(
                client, BOT_TOKEN, chat_id,
                "Cookies заменены, тестовый резолв прошёл.",
            )
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
    except bot.CookiesTooLargeError:
        logger.info("cookies: user=%s target=%s result=too_large", user_id, target)
        await bot.send_message(client, BOT_TOKEN, chat_id, "Файл больше 100 KB.")
    except bot.TelegramError as exc:
        logger.warning("cookies: user=%s target=%s result=telegram_error: %s", user_id, target, exc)
        if chat_id is not None:
            try:
                await bot.send_message(
                    client, BOT_TOKEN, chat_id,
                    "Не удалось обработать файл. Попробуй ещё раз.",
                )
            except bot.TelegramError:
                pass
    except Exception as exc:
        logger.warning(
            "cookies: user=%s target=%s result=error error=%s",
            user_id, target, type(exc).__name__,
        )
        if chat_id is not None:
            try:
                await bot.send_message(
                    client, BOT_TOKEN, chat_id,
                    "Не удалось заменить cookies. Старый файл не изменён.",
                )
            except bot.TelegramError:
                pass
    finally:
        if chat_id is not None and message_id is not None:
            await bot.delete_message(client, BOT_TOKEN, chat_id, message_id)


@app.post(bot.WEBHOOK_PATH, include_in_schema=False)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(None),
):
    secret = x_telegram_bot_api_secret_token or ""
    if not hmac.compare_digest(secret.encode(), WEBHOOK_SECRET.encode()):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        update = await request.json()
    except ValueError:
        return {"ok": True}
    if not isinstance(update, dict) or not PUBLIC_URL:
        return {"ok": True}

    reply = bot.build_start_reply(update, PUBLIC_URL)
    if reply:
        try:
            await bot.call(_get_http(), BOT_TOKEN, "sendMessage", reply)
        except bot.TelegramError as exc:
            logger.warning("sendMessage failed: %s", exc)

    cookies = bot.parse_cookies_command(update)
    if cookies is not None:
        await _process_cookies_update(cookies)
    # Always 200: otherwise Telegram keeps retrying the same update
    return {"ok": True}


# ---------------------------------------------------------------------------
# Frontend static files — mounted last so it never shadows the routes above
# ---------------------------------------------------------------------------
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
