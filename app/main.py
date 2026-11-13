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

import hmac
import logging
import os
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path as FsPath
from typing import Any, AsyncIterator, Deque, Dict, List, Literal, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import auth, bot, database, radio, resolver, search

logger = logging.getLogger("lammot.api")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
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


def _get_http() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, read=60.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
    return _http_client


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global _bot_username, _http_client
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

    try:
        yield
    finally:
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
    user: auth.TelegramUser = Depends(current_user),
):
    search_limiter.check(user.id)
    try:
        results = await run_in_threadpool(search.search_songs, q, limit)
    except Exception:
        logger.exception("Search failed for query=%r", q)
        raise HTTPException(status_code=502, detail="Search service unavailable")
    return _ok(results)


@app.get("/api/stream/{video_id}")
async def api_stream(
    video_id: str = Path(..., pattern=VIDEO_ID_PATTERN),
    fmt: str = Query("opus", alias="format", pattern="^(opus|aac)$"),
    user: auth.TelegramUser = Depends(current_user),
):
    """
    Resolve the track now (so errors show up before playback) and return a
    signed same-origin link the <audio> tag can play.
    """
    stream_limiter.check(user.id)
    try:
        await run_in_threadpool(resolver.get_stream_url, video_id, fmt)
    except ValueError:
        logger.info("Stream: no supported audio format for video_id=%s", video_id)
        raise HTTPException(status_code=404, detail="No playable audio stream for this track")
    except Exception:
        logger.exception("Stream resolve failed for video_id=%s", video_id)
        raise HTTPException(status_code=502, detail="Stream service unavailable")

    token = auth.sign_audio_token(AUDIO_KEY, video_id, fmt, user.id, AUDIO_LINK_TTL)
    return _ok({
        "video_id": video_id,
        "format": fmt,
        "stream_url": f"/audio/{video_id}?t={token}",
    })


async def _relay(resp: httpx.Response) -> AsyncIterator[bytes]:
    try:
        async for chunk in resp.aiter_raw():
            yield chunk
    finally:
        await resp.aclose()


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

    range_header = request.headers.get("range")
    if range_header and not _RANGE_RE.match(range_header):
        raise HTTPException(status_code=416, detail="Unsupported Range header")

    client = _get_http()
    resp: Optional[httpx.Response] = None
    for attempt in (1, 2):
        try:
            cdn_url = await run_in_threadpool(resolver.get_stream_url, video_id, fmt)
        except ValueError:
            raise HTTPException(status_code=404, detail="No playable audio stream for this track")
        except Exception:
            logger.exception("Audio: resolve failed for video_id=%s", video_id)
            raise HTTPException(status_code=502, detail="Audio source unavailable")

        headers = resolver.get_stream_headers(video_id, fmt)
        if range_header:
            headers["Range"] = range_header
        try:
            resp = await client.send(client.build_request("GET", cdn_url, headers=headers), stream=True)
        except httpx.HTTPError as exc:
            logger.warning("Audio: CDN request failed for video_id=%s: %s", video_id, type(exc).__name__)
            raise HTTPException(status_code=502, detail="Audio source unavailable") from None

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
    return StreamingResponse(_relay(resp), status_code=resp.status_code, headers=out_headers)


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
# Telegram bot webhook (/start → "Open Lammot" button)
# ---------------------------------------------------------------------------
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
    # Always 200: otherwise Telegram keeps retrying the same update
    return {"ok": True}


# ---------------------------------------------------------------------------
# Frontend static files — mounted last so it never shadows the routes above
# ---------------------------------------------------------------------------
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
