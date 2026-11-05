"""
app/radio.py — Sprint 3: Infinite radio queue
LammotBot Telegram Mini App Music Player

Source: ytmusicapi.get_watch_playlist(videoId=...)

Post-processing:
  - the seed track itself is never returned (it is already playing)
  - deduplication against the user's recent history and within the queue
  - diversity filter: at most 1 track per artist in any 4 consecutive tracks
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from ytmusicapi import YTMusic

logger = logging.getLogger(__name__)

# Окно разнообразия: артист не может повториться среди 4 подряд идущих треков,
# значит при добавлении нового трека проверяем 3 предыдущих.
_DIVERSITY_WINDOW = 3

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


def get_radio_queue(
    video_id: str,
    history: Optional[Iterable[str]] = None,
    limit: int = 5,
) -> list[dict]:
    """
    Вернуть до *limit* похожих треков для радио на основе *video_id*.

    *history* — недавно сыгранные video_id, которые нужно пропустить.
    Ошибки ytmusicapi пробрасываются наверх, чтобы API мог вернуть 502,
    а не молча пустую очередь.
    """
    raw_playlist = _get_client().get_watch_playlist(videoId=video_id)
    tracks = raw_playlist.get("tracks", []) or []

    # Исходный трек уже играет — его тоже исключаем
    skip_ids = set(history or ())
    skip_ids.add(video_id)

    queue: list[dict] = []
    seen_ids: set[str] = set()
    recent_artists: list[str] = []

    for track in tracks:
        if len(queue) >= limit:
            break

        t_vid = track.get("videoId")
        if not t_vid or t_vid in skip_ids or t_vid in seen_ids:
            continue

        artists = track.get("artists") or []
        artist_name = artists[0].get("name") if artists else None
        artist_name = artist_name or "Unknown Artist"

        if artist_name in recent_artists:
            continue

        thumbnails = track.get("thumbnail") or []
        thumbnail_url = thumbnails[-1].get("url", "") if thumbnails else ""

        queue.append(
            {
                "video_id": t_vid,
                "title": track.get("title") or "Unknown Title",
                "artist": artist_name,
                "duration_seconds": _parse_duration(track.get("length")),
                "thumbnail_url": thumbnail_url,
            }
        )
        seen_ids.add(t_vid)

        recent_artists.append(artist_name)
        if len(recent_artists) > _DIVERSITY_WINDOW:
            recent_artists.pop(0)

    logger.debug("Radio: seed=%s -> %d tracks", video_id, len(queue))
    return queue
