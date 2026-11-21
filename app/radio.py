"""
app/radio.py — Sprint 3: Infinite radio queue
LammotBot Telegram Mini App Music Player

Source: ytmusicapi.get_watch_playlist(videoId=...)

Post-processing:
  - the seed track itself is never returned (it is already playing)
  - deduplication against the user's recent history and within the queue
  - diversity filter: at most 1 track per artist in any 4 consecutive tracks
  - video radio (kind="video"): the queue is limited to 45–900 s videos, and a
    second pass without the diversity filter fills the queue if the first pass
    is starved
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from ytmusicapi import YTMusic

logger = logging.getLogger(__name__)

# Окно разнообразия: артист не может повториться среди 4 подряд идущих треков,
# значит при добавлении нового трека проверяем 3 предыдущих.
_DIVERSITY_WINDOW = 3

# Video radio keeps the same duration window as Extended search.
VIDEO_MIN_DURATION_SECONDS = 45
VIDEO_MAX_DURATION_SECONDS = 900

_ytmusic: Optional[YTMusic] = None


def _get_client() -> YTMusic:
    """Ленивая инициализация клиента (не трогаем сеть при импорте модуля)."""
    global _ytmusic
    if _ytmusic is None:
        _ytmusic = YTMusic()
    return _ytmusic


def _parse_duration(length_str: Optional[str]) -> int:
    """Конвертирует строку формата 'M:SS' или 'H:MM:SS' в секунды."""
    if not length_str:
        return 0
    try:
        parts = [int(p) for p in length_str.strip().split(":")]
    except ValueError:
        return 0
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


def _artist_name(track: dict) -> str:
    artists = track.get("artists") or []
    name = artists[0].get("name") if artists else None
    return name or track.get("channel") or "Unknown Artist"


def _artist_id(track: dict) -> Optional[str]:
    artists = track.get("artists") or []
    return artists[0].get("id") if artists else None


def _video_allowed(track: dict, duration_seconds: int) -> bool:
    if duration_seconds <= 0:
        return False
    if duration_seconds < VIDEO_MIN_DURATION_SECONDS:
        return False
    if duration_seconds > VIDEO_MAX_DURATION_SECONDS:
        return False
    if track.get("isLive") or track.get("live") or track.get("isUpcoming"):
        return False
    return True


def get_radio_queue(
    video_id: str,
    history: Optional[Iterable[str]] = None,
    limit: int = 5,
    kind: str = "song",
) -> list[dict]:
    """
    Вернуть до *limit* похожих треков для радио на основе *video_id*.

    *history* — недавно сыгранные video_id, которые нужно пропустить.
    *kind* — "song" (по умолчанию, текущее поведение) или "video": для видео
    очередь ограничена 45–900 секундами, а второй проход без фильтра
    разнообразия добирает треки, если первый проход получился коротким.
    Ошибки ytmusicapi пробрасываются наверх, чтобы API мог вернуть 502,
    а не молча пустую очередь.
    """
    is_video = kind == "video"
    raw_playlist = _get_client().get_watch_playlist(videoId=video_id)
    tracks = raw_playlist.get("tracks", []) or []

    # Исходный трек уже играет — его тоже исключаем
    skip_ids = set(history or ())
    skip_ids.add(video_id)

    queue: list[dict] = []
    seen_ids: set[str] = set()
    recent_artists: list[str] = []

    def add(
        track: dict,
        t_vid: str,
        artist_name: str,
        artist_id: Optional[str],
        duration_seconds: int,
    ) -> None:
        thumbnails = track.get("thumbnail") or []
        thumbnail_url = thumbnails[-1].get("url", "") if thumbnails else ""
        queue.append(
            {
                "video_id": t_vid,
                "title": track.get("title") or "Unknown Title",
                "artist": artist_name,
                "artist_id": None if is_video else artist_id,
                "duration_seconds": duration_seconds,
                "thumbnail_url": thumbnail_url,
            }
        )
        seen_ids.add(t_vid)

    def candidate(track: dict) -> tuple[str, str, Optional[str], int] | None:
        t_vid = track.get("videoId")
        if not t_vid or t_vid in skip_ids or t_vid in seen_ids:
            return None
        duration_seconds = _parse_duration(track.get("length"))
        if is_video and not _video_allowed(track, duration_seconds):
            return None
        return t_vid, _artist_name(track), _artist_id(track), duration_seconds

    # Первый проход: фильтр разнообразия
    for track in tracks:
        if len(queue) >= limit:
            break

        found = candidate(track)
        if found is None:
            continue
        t_vid, artist_name, artist_id, duration_seconds = found

        if artist_name in recent_artists:
            continue

        add(track, t_vid, artist_name, artist_id, duration_seconds)

        recent_artists.append(artist_name)
        if len(recent_artists) > _DIVERSITY_WINDOW:
            recent_artists.pop(0)

    # Второй проход без фильтра разнообразия: только для видео-радио.
    if is_video and len(queue) < limit:
        for track in tracks:
            if len(queue) >= limit:
                break
            found = candidate(track)
            if found is None:
                continue
            add(track, *found)

    logger.debug(
        "Radio: seed=%s kind=%s -> %d tracks", video_id, kind, len(queue)
    )
    return queue
