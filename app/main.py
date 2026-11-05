"""
app/main.py — Sprint 4: FastAPI app, API routes, Telegram initData auth
LammotBot Telegram Mini App Music Player
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import radio, resolver, search

logger = logging.getLogger("lammot.api")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

# ---------------------------------------------------------------------------
# Конфигурация (только из переменных окружения, без значений по умолчанию
# для секретов)
# ---------------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    # Fail fast: без токена любой мог бы подделать подпись initData.
    raise RuntimeError(
        "BOT_TOKEN environment variable is not set. "
        "Set it in Railway variables (or .env locally) before starting the app."
    )

# Сколько секунд initData считается действительным (защита от replay).
INIT_DATA_MAX_AGE = int(os.getenv("INIT_DATA_MAX_AGE", "86400"))  # 24 ч

# Разрешённые источники для CORS, через запятую.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "https://lammot.up.railway.app").split(",")
    if o.strip()
]

# YouTube video_id: ровно 11 символов из [A-Za-z0-9_-]
VIDEO_ID_PATTERN = r"^[A-Za-z0-9_-]{11}$"
_VIDEO_ID_RE = re.compile(VIDEO_ID_PATTERN)
MAX_EXCLUDE = 50  # радио учитывает последние 50 сыгранных треков

app = FastAPI(title="LammotBot API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,          # авторизация идёт заголовком, не cookie
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


# ---------------------------------------------------------------------------
# Авторизация через Telegram initData
# https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
# ---------------------------------------------------------------------------
def verify_telegram_init_data(
    authorization: Optional[str] = Header(None),
) -> Dict[str, str]:
    if not authorization or not authorization.startswith("tma "):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header. Expected 'tma <initData>'",
        )

    init_data = authorization[4:]
    try:
        parsed_data = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid initData format")

    received_hash = parsed_data.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Missing hash in initData")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    # Сравнение за постоянное время — защита от timing-атак
    if not hmac.compare_digest(calculated_hash, received_hash):
        logger.warning("Auth: invalid initData signature")
        raise HTTPException(status_code=401, detail="Invalid Telegram signature")

    # Проверка свежести: перехваченный initData не должен работать вечно
    try:
        auth_date = int(parsed_data.get("auth_date", ""))
    except ValueError:
        raise HTTPException(status_code=401, detail="Missing or invalid auth_date")
    if time.time() - auth_date > INIT_DATA_MAX_AGE:
        raise HTTPException(status_code=401, detail="initData expired")

    return parsed_data


def _validate_video_ids(ids: List[str]) -> List[str]:
    if len(ids) > MAX_EXCLUDE:
        raise HTTPException(status_code=422, detail=f"exclude accepts at most {MAX_EXCLUDE} ids")
    bad = [v for v in ids if not _VIDEO_ID_RE.match(v)]
    if bad:
        raise HTTPException(status_code=422, detail="Invalid video_id in exclude list")
    return ids


# ---------------------------------------------------------------------------
# API эндпоинты
# Блокирующие вызовы (ytmusicapi, yt-dlp) выполняются в пуле потоков,
# чтобы не останавливать event loop для остальных пользователей.
# ---------------------------------------------------------------------------
@app.get("/api/search")
async def api_search(
    q: str = Query(..., min_length=1, max_length=200, description="Search query"),
    limit: int = Query(10, ge=1, le=25),
    user_data: Dict[str, Any] = Depends(verify_telegram_init_data),
):
    try:
        results = await run_in_threadpool(search.search_songs, q, limit)
    except Exception:
        logger.exception("Search failed for query=%r", q)
        raise HTTPException(status_code=502, detail="Search service unavailable")
    return {"ok": True, "data": results}


@app.get("/api/stream/{video_id}")
async def api_stream(
    video_id: str = Path(..., pattern=VIDEO_ID_PATTERN),
    user_data: Dict[str, Any] = Depends(verify_telegram_init_data),
):
    try:
        stream_url = await run_in_threadpool(resolver.get_stream_url, video_id)
    except ValueError:
        # Нет подходящего аудиоформата для этого видео
        logger.info("Stream: no supported audio format for video_id=%s", video_id)
        raise HTTPException(status_code=404, detail="No playable audio stream for this track")
    except Exception:
        logger.exception("Stream resolve failed for video_id=%s", video_id)
        raise HTTPException(status_code=502, detail="Stream service unavailable")
    return {"ok": True, "data": {"video_id": video_id, "stream_url": stream_url}}


@app.get("/api/radio/{video_id}")
async def api_radio(
    video_id: str = Path(..., pattern=VIDEO_ID_PATTERN),
    exclude: List[str] = Query(
        default=[],
        description="Recently played video_ids to skip (up to 50)",
    ),
    limit: int = Query(5, ge=1, le=20),
    user_data: Dict[str, Any] = Depends(verify_telegram_init_data),
):
    # TODO (Sprint 5): брать историю из play_history в SQLite вместо параметра
    history = _validate_video_ids(exclude)
    try:
        queue = await run_in_threadpool(radio.get_radio_queue, video_id, history, limit)
    except Exception:
        logger.exception("Radio failed for video_id=%s", video_id)
        raise HTTPException(status_code=502, detail="Radio service unavailable")
    return {"ok": True, "data": queue}


# ---------------------------------------------------------------------------
# Раздача статики (Frontend) — монтируется последней, чтобы не перекрывать /api
# ---------------------------------------------------------------------------
if os.path.exists("frontend"):
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
