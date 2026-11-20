"""
app/prefetch.py — background prefetch of the top search results.

A search response never waits for a resolve.  This module keeps one asyncio
worker that resolves the first few results through the resolver's sequential
prefetch path (one extraction slot at a time) and warms the CDN connection, so
a later tap hits the resolver cache or waits only for the remaining part of an
in-flight resolve.

A newer search from the same user supersedes that user's queued prefetches, so
fast typing cannot leave the final query's top results behind stale work.  A
short settle window gives the next keystroke a chance to supersede a job before
it spends a yt-dlp extraction slot, and identical result batches are
deduplicated for a brief window.

The resolver and CDN warm-up callbacks are injected by app.main; this module
must never import app.main (that would be a circular import).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from app import resolver

logger = logging.getLogger(__name__)

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

ResolveCallback = Callable[..., Awaitable[Tuple[str, Dict[str, str]]]]
WarmCallback = Callable[[str, Dict[str, str]], None]


def _accepts_should_stop(callback: Callable[..., Any]) -> bool:
    """True when the injected resolver callback can receive ``should_stop``."""
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return False
    if "should_stop" in signature.parameters:
        return True
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


@dataclass(frozen=True)
class PrefetchJob:
    video_id: str
    format: str
    owner: Any = None
    generation: int = 0
    enqueued_at: float = 0.0
    request_key: Optional[str] = None


class SearchPrefetcher:
    """One worker, a bounded queue and a deduplicated pending set."""

    def __init__(
        self,
        resolve: ResolveCallback,
        warm: WarmCallback,
        *,
        top_n: int = 3,
        queue_size: int = 12,
        settle_seconds: float = 1.0,
        dedupe_seconds: float = 1.0,
        workers: int = 1,
    ) -> None:
        self._resolve = resolve
        self._warm = warm
        self._resolve_accepts_should_stop = _accepts_should_stop(resolve)
        self._top_n = max(1, int(top_n))
        self._settle_seconds = max(0.0, float(settle_seconds))
        self._dedupe_seconds = max(0.0, float(dedupe_seconds))
        self._workers = max(1, int(workers))
        self._queue: asyncio.Queue[PrefetchJob] = asyncio.Queue(
            maxsize=max(1, int(queue_size))
        )
        # How many queued or in-flight jobs currently reference a cache key.
        self._pending: Dict[Tuple[str, str], int] = {}
        # Latest search generation per user, used to drop stale queued work.
        self._generations: Dict[Any, int] = {}
        # Owner-scoped signature of the last enqueued batch and its timestamp.
        self._last_signature: Dict[Any, Tuple[Tuple[str, ...], float]] = {}
        # Wakes the worker out of its settle window as soon as a new search
        # arrives, so a stale job does not hold the only worker for its full
        # settle time.
        self._wake = asyncio.Event()
        self._tasks: List[asyncio.Task] = []

    @property
    def _task(self) -> Optional[asyncio.Task]:
        """First worker task, kept for callers that inspect the single worker."""
        return self._tasks[0] if self._tasks else None

    def enqueue_results(
        self,
        results: Iterable[dict],
        format: str,
        *,
        owner: Any = None,
        request_key: Optional[str] = None,
    ) -> int:
        """
        Queue up to ``top_n`` unique valid results.  Never blocks, never
        raises: a full queue or a malformed result is simply skipped.

        A newer call for the same ``owner`` drops that owner's older queued
        jobs, so the latest search is not stuck behind previous keystrokes.  An
        identical batch from the same owner within ``dedupe_seconds`` is
        ignored, so a duplicated client request cannot churn generations or
        start extra extractions.
        """
        ids: List[str] = []
        for item in results or ():
            if len(ids) >= self._top_n:
                break
            if not isinstance(item, dict):
                continue
            video_id = item.get("video_id")
            if not isinstance(video_id, str) or not _VIDEO_ID_RE.match(video_id):
                logger.debug("search_prefetch skipped invalid video_id format=%s", format)
                continue
            if video_id in ids:
                continue
            ids.append(video_id)

        now = time.monotonic()
        signature = (format, tuple(sorted(ids)))
        previous = self._last_signature.get(owner)
        if (
            previous is not None
            and previous[0] == signature
            and now - previous[1] < self._dedupe_seconds
        ):
            logger.info(
                "search_prefetch duplicate format=%s count=%d q_hash=%s",
                format,
                len(ids),
                request_key or "-",
            )
            return 0
        self._last_signature[owner] = (signature, now)

        generation = self._generations.get(owner, 0) + 1
        self._generations[owner] = generation
        self._drop_stale(owner, generation)
        # Even when the queue is already empty, a stale job may be sleeping in
        # its settle window; wake it so it can be skipped immediately.
        self._wake.set()

        enqueued = 0
        for video_id in ids:
            key = (video_id, format)
            if self._pending.get(key, 0) > 0:
                continue
            try:
                self._queue.put_nowait(
                    PrefetchJob(
                        video_id, format, owner, generation, now, request_key
                    )
                )
            except asyncio.QueueFull:
                logger.info(
                    "search_prefetch dropped video_id=%s format=%s reason=queue_full",
                    video_id,
                    format,
                )
                break
            self._pending[key] = self._pending.get(key, 0) + 1
            enqueued += 1
            logger.info(
                "search_prefetch enqueued video_id=%s format=%s generation=%d "
                "q_hash=%s",
                video_id,
                format,
                generation,
                request_key or "-",
            )
        if enqueued:
            logger.info(
                "search_prefetch enqueue count=%d format=%s generation=%d "
                "q_hash=%s",
                enqueued,
                format,
                generation,
                request_key or "-",
            )
        return enqueued

    async def start(self) -> None:
        if any(not task.done() for task in self._tasks):
            return
        self._tasks = [
            asyncio.create_task(self._run(), name=f"search-prefetch-{index}")
            for index in range(self._workers)
        ]

    async def stop(self) -> None:
        tasks = self._tasks
        self._tasks = []
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("search_prefetch worker failed")

    def _release(self, job: PrefetchJob) -> None:
        key = (job.video_id, job.format)
        count = self._pending.get(key, 0) - 1
        if count > 0:
            self._pending[key] = count
        else:
            self._pending.pop(key, None)

    def _is_current(self, job: PrefetchJob) -> bool:
        """True when no newer search from the same owner superseded *job*."""
        return job.generation >= self._generations.get(job.owner, job.generation)

    async def _settle(self, job: PrefetchJob) -> None:
        """
        Wait the settle window, but return early when a new search arrives.

        The worker then sees that the job is stale and discards it before it
        spends an extraction slot.  A new search from another owner wakes the
        loop too; the job stays current and simply continues waiting for the
        remaining time.
        """
        if self._settle_seconds <= 0:
            return
        deadline = time.monotonic() + self._settle_seconds
        while self._is_current(job):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return

    def _drop_stale(self, owner: Any, generation: int) -> None:
        kept: List[PrefetchJob] = []
        dropped = 0
        while True:
            try:
                job = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if job.owner == owner and job.generation < generation:
                self._release(job)
                dropped += 1
            else:
                kept.append(job)
        # Re-putting a job adds a new unfinished count, so close the old one
        # for every drained item (kept and dropped) to keep queue.join() sane.
        for job in kept:
            self._queue.put_nowait(job)
        for _ in range(len(kept) + dropped):
            self._queue.task_done()
        if dropped:
            logger.info("search_prefetch superseded count=%d", dropped)

    def _drain(self) -> None:
        drained = 0
        while True:
            try:
                job = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._release(job)
            self._queue.task_done()
            drained += 1
        if drained:
            logger.info("search_prefetch drained count=%d", drained)

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            request_key = job.request_key or "-"
            queue_wait_ms = (
                max(0.0, (time.monotonic() - job.enqueued_at) * 1000)
                if job.enqueued_at
                else 0.0
            )
            settle_ms = 0.0
            try:
                if self._settle_seconds > 0:
                    started = time.monotonic()
                    await self._settle(job)
                    settle_ms = (time.monotonic() - started) * 1000

                if not self._is_current(job):
                    logger.info(
                        "search_prefetch skipped video_id=%s format=%s "
                        "generation=%d reason=superseded-before-start "
                        "queue_wait_ms=%.0f settle_ms=%.0f q_hash=%s",
                        job.video_id,
                        job.format,
                        job.generation,
                        queue_wait_ms,
                        settle_ms,
                        request_key,
                    )
                    continue

                should_stop = lambda: not self._is_current(job)
                if self._resolve_accepts_should_stop:
                    url, headers = await self._resolve(
                        job.video_id, job.format, should_stop=should_stop
                    )
                else:
                    url, headers = await self._resolve(job.video_id, job.format)
                self._warm(url, headers)
                logger.info(
                    "search_prefetch resolved video_id=%s format=%s "
                    "generation=%d queue_wait_ms=%.0f settle_ms=%.0f q_hash=%s",
                    job.video_id,
                    job.format,
                    job.generation,
                    queue_wait_ms,
                    settle_ms,
                    request_key,
                )
            except resolver.PrefetchCancelled:
                logger.info(
                    "search_prefetch skipped video_id=%s format=%s "
                    "generation=%d reason=superseded queue_wait_ms=%.0f "
                    "settle_ms=%.0f q_hash=%s",
                    job.video_id,
                    job.format,
                    job.generation,
                    queue_wait_ms,
                    settle_ms,
                    request_key,
                )
            except resolver.BotCheckError:
                logger.warning(
                    "search_prefetch bot_check video_id=%s format=%s q_hash=%s",
                    job.video_id,
                    job.format,
                    request_key,
                )
                self._drain()
            except resolver.UnavailableError:
                logger.info(
                    "search_prefetch skipped video_id=%s format=%s "
                    "reason=unavailable q_hash=%s",
                    job.video_id,
                    job.format,
                    request_key,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "search_prefetch skipped video_id=%s format=%s error=%s "
                    "q_hash=%s",
                    job.video_id,
                    job.format,
                    type(exc).__name__,
                    request_key,
                )
            finally:
                self._release(job)
                self._queue.task_done()
