"""
app/prefetch.py — background prefetch of the top search results.

A search response never waits for a resolve.  This module keeps one asyncio
worker that resolves the first few results through the resolver's prefetch
path (serial extract, shared per-key lock) and warms the CDN connection, so a
later tap hits the resolver cache or waits only for the remaining part of an
in-flight resolve.

The resolver and CDN warm-up callbacks are injected by app.main; this module
must never import app.main (that would be a circular import).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Iterable, Optional, Set, Tuple

from app import resolver

logger = logging.getLogger(__name__)

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

ResolveCallback = Callable[[str, str], Awaitable[Tuple[str, Dict[str, str]]]]
WarmCallback = Callable[[str, Dict[str, str]], None]


@dataclass(frozen=True)
class PrefetchJob:
    video_id: str
    format: str


class SearchPrefetcher:
    """One worker, a bounded queue and a deduplicated pending set."""

    def __init__(
        self,
        resolve: ResolveCallback,
        warm: WarmCallback,
        *,
        top_n: int = 3,
        queue_size: int = 12,
    ) -> None:
        self._resolve = resolve
        self._warm = warm
        self._top_n = max(1, int(top_n))
        self._queue: asyncio.Queue[PrefetchJob] = asyncio.Queue(
            maxsize=max(1, int(queue_size))
        )
        self._pending: Set[Tuple[str, str]] = set()
        self._task: Optional[asyncio.Task] = None

    def enqueue_results(self, results: Iterable[dict], format: str) -> int:
        """
        Queue up to ``top_n`` unique valid results.  Never blocks, never
        raises: a full queue or a malformed result is simply skipped.
        """
        enqueued = 0
        for item in results or ():
            if enqueued >= self._top_n:
                break
            if not isinstance(item, dict):
                continue
            video_id = item.get("video_id")
            if not isinstance(video_id, str) or not _VIDEO_ID_RE.match(video_id):
                logger.debug("search_prefetch skipped invalid video_id format=%s", format)
                continue
            key = (video_id, format)
            if key in self._pending:
                continue
            try:
                self._queue.put_nowait(PrefetchJob(video_id, format))
            except asyncio.QueueFull:
                logger.info(
                    "search_prefetch dropped video_id=%s format=%s reason=queue_full",
                    video_id,
                    format,
                )
                break
            self._pending.add(key)
            enqueued += 1
            logger.info(
                "search_prefetch enqueued video_id=%s format=%s", video_id, format
            )
        return enqueued

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="search-prefetch")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("search_prefetch worker failed")

    def _drain(self) -> None:
        drained = 0
        while True:
            try:
                job = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._pending.discard((job.video_id, job.format))
            self._queue.task_done()
            drained += 1
        if drained:
            logger.info("search_prefetch drained count=%d", drained)

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                url, headers = await self._resolve(job.video_id, job.format)
                self._warm(url, headers)
                logger.info(
                    "search_prefetch resolved video_id=%s format=%s",
                    job.video_id,
                    job.format,
                )
            except resolver.BotCheckError:
                logger.warning(
                    "search_prefetch bot_check video_id=%s format=%s",
                    job.video_id,
                    job.format,
                )
                self._drain()
            except resolver.UnavailableError:
                logger.info(
                    "search_prefetch skipped video_id=%s format=%s reason=unavailable",
                    job.video_id,
                    job.format,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "search_prefetch skipped video_id=%s format=%s error=%s",
                    job.video_id,
                    job.format,
                    type(exc).__name__,
                )
            finally:
                self._pending.discard((job.video_id, job.format))
                self._queue.task_done()
