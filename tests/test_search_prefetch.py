"""
tests/test_search_prefetch.py — server-side prefetch of top search results

No network and no real resolver calls: search is patched where app.main looks
it up, the prefetcher is either a fake object or the real class with fake
callbacks, and the CDN warm-up is a plain recording function.
"""

import asyncio
import hashlib
import hmac
import json
import time
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app import main, resolver
from app.main import BOT_TOKEN, app

VID = "dQw4w9WgXcQ"
VID2 = "9bZkp7q19f0"
VID3 = "kJQP7kiw5Fk"
VID4 = "abcdefghijk"
VID5 = "lmnopqrstuv"
VID6 = "wxyzABCDEFG"


def make_tma_header(user_id: int = 123456) -> str:
    data = {
        "query_id": "AAHdF6IQAAAAAN0XohD-test",
        "user": json.dumps({"id": user_id, "first_name": "Test", "username": f"u{user_id}"}),
        "auth_date": str(int(time.time())),
    }
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return f"tma {urllib.parse.urlencode(data)}"


@pytest.fixture
def headers():
    return {"Authorization": make_tma_header()}


@pytest.fixture
async def client(temp_db):
    main.search_limiter.reset()
    main.stream_limiter.reset()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def _prefetcher(resolve, warm=None, **kwargs):
    from app.prefetch import SearchPrefetcher

    # Existing behavior tests must not pay the production settle window.
    kwargs.setdefault("settle_seconds", 0)
    return SearchPrefetcher(resolve, warm or (lambda url, headers: None), **kwargs)


def _results(*video_ids):
    return [{"video_id": video_id, "title": f"Track {video_id}"} for video_id in video_ids]


def _drain(prefetcher):
    return [
        prefetcher._queue.get_nowait()
        for _ in range(prefetcher._queue.qsize())
    ]


# ------------------------------------------------------------ API behavior
async def test_search_enqueues_results_with_requested_format(client, headers):
    fake_results = _results(VID, VID2, VID3)
    fake = MagicMock()
    with patch("app.main.search.search_songs", return_value=fake_results), \
         patch.object(main, "_search_prefetcher", fake):
        r = await client.get("/api/search?q=x&limit=20&format=aac", headers=headers)

    assert r.status_code == 200
    assert r.json() == {"ok": True, "data": fake_results}
    q_hash = hashlib.sha256(b"x").hexdigest()[:10]
    fake.enqueue_results.assert_called_once_with(
        fake_results, "aac", owner=123456, request_key=q_hash
    )


async def test_search_does_not_fail_when_prefetch_enqueue_raises(client, headers):
    fake_results = _results(VID, VID2)
    fake = MagicMock()
    fake.enqueue_results.side_effect = RuntimeError("prefetch bug")
    with patch("app.main.search.search_songs", return_value=fake_results), \
         patch.object(main, "_search_prefetcher", fake):
        r = await client.get("/api/search?q=x&format=opus", headers=headers)

    assert r.status_code == 200
    assert r.json() == {"ok": True, "data": fake_results}


async def test_search_prefetch_is_noop_when_disabled(client, headers):
    fake_results = _results(VID)
    with patch("app.main.search.search_songs", return_value=fake_results), \
         patch.object(main, "_search_prefetcher", None), \
         patch("app.main.resolver.get_stream_url") as resolve:
        r = await client.get("/api/search?q=x", headers=headers)

    assert r.status_code == 200
    resolve.assert_not_called()


async def test_search_rejects_unknown_format(client, headers):
    with patch("app.main.search.search_songs") as m:
        r = await client.get("/api/search?q=x&format=mp3", headers=headers)

    assert r.status_code == 422
    m.assert_not_called()


def test_frontend_search_sends_preferred_format():
    source = (Path(__file__).resolve().parents[1] / "frontend" / "app.js").read_text(
        encoding="utf-8"
    )
    assert "format=${preferredFormat}" in source


# ------------------------------------------------ SearchPrefetcher behavior
async def test_enqueues_only_top_three_unique():
    async def resolve(video_id, fmt):
        return "https://cdn.example/audio", {}

    prefetcher = _prefetcher(resolve)
    count = prefetcher.enqueue_results(_results(VID, VID, VID2, VID3, VID4), "opus")

    assert count == 3
    jobs = _drain(prefetcher)
    assert [(job.video_id, job.format) for job in jobs] == [
        (VID, "opus"),
        (VID2, "opus"),
        (VID3, "opus"),
    ]


async def test_new_search_drops_stale_jobs_for_the_same_owner():
    async def resolve(video_id, fmt):
        return "https://cdn.example/audio", {}

    prefetcher = _prefetcher(resolve)
    prefetcher.enqueue_results(_results(VID, VID2, VID3), "opus", owner=1)
    count = prefetcher.enqueue_results(_results(VID4, VID5, VID6), "opus", owner=1)

    assert count == 3
    jobs = _drain(prefetcher)
    assert [(job.video_id, job.format) for job in jobs] == [
        (VID4, "opus"),
        (VID5, "opus"),
        (VID6, "opus"),
    ]


async def test_new_search_keeps_other_owners_jobs():
    async def resolve(video_id, fmt):
        return "https://cdn.example/audio", {}

    prefetcher = _prefetcher(resolve)
    prefetcher.enqueue_results(_results(VID, VID2, VID3), "opus", owner=1)
    prefetcher.enqueue_results(_results(VID4, VID5, VID6), "opus", owner=2)

    jobs = _drain(prefetcher)
    assert [job.video_id for job in jobs] == [VID, VID2, VID3, VID4, VID5, VID6]


async def test_worker_resolves_only_the_newest_search():
    resolved = []

    async def resolve(video_id, fmt):
        resolved.append(video_id)
        return "https://cdn.example/audio", {}

    prefetcher = _prefetcher(resolve)
    prefetcher.enqueue_results(_results(VID, VID2, VID3), "opus", owner=1)
    prefetcher.enqueue_results(_results(VID4, VID5, VID6), "opus", owner=1)

    await prefetcher.start()
    try:
        await asyncio.wait_for(prefetcher._queue.join(), 1)
    finally:
        await prefetcher.stop()

    assert resolved == [VID4, VID5, VID6]


async def test_superseding_one_owner_keeps_join_working_for_others():
    resolved = []

    async def resolve(video_id, fmt):
        resolved.append(video_id)
        return "https://cdn.example/audio", {}

    prefetcher = _prefetcher(resolve)
    prefetcher.enqueue_results(_results(VID, VID2, VID3), "opus", owner=1)
    prefetcher.enqueue_results(_results(VID4, VID5, VID6), "opus", owner=2)
    prefetcher.enqueue_results(_results(VID,), "opus", owner=1)

    await prefetcher.start()
    try:
        await asyncio.wait_for(prefetcher._queue.join(), 1)
    finally:
        await prefetcher.stop()

    assert resolved == [VID4, VID5, VID6, VID]


async def test_worker_resolves_and_warms_in_order():
    resolved, warmed = [], []

    async def resolve(video_id, fmt):
        resolved.append((video_id, fmt))
        return f"https://cdn.example/{video_id}", {"X-Test": video_id}

    def warm(url, headers):
        warmed.append((url, headers))

    prefetcher = _prefetcher(resolve, warm)
    await prefetcher.start()
    try:
        prefetcher.enqueue_results(_results(VID, VID2), "aac")
        await asyncio.wait_for(prefetcher._queue.join(), 1)
    finally:
        await prefetcher.stop()

    assert resolved == [(VID, "aac"), (VID2, "aac")]
    assert warmed == [
        (f"https://cdn.example/{VID}", {"X-Test": VID}),
        (f"https://cdn.example/{VID2}", {"X-Test": VID2}),
    ]


async def test_worker_drains_queue_on_bot_check():
    attempted, warmed = [], []

    async def resolve(video_id, fmt):
        attempted.append(video_id)
        raise resolver.BotCheckError("bot")

    prefetcher = _prefetcher(resolve, lambda url, headers: warmed.append(url))
    await prefetcher.start()
    try:
        prefetcher.enqueue_results(_results(VID, VID2, VID3), "opus")
        await asyncio.wait_for(prefetcher._queue.join(), 1)
    finally:
        await prefetcher.stop()

    assert attempted == [VID]
    assert prefetcher._queue.qsize() == 0
    assert warmed == []


async def test_worker_continues_after_transient_error():
    attempted, warmed = [], []

    async def resolve(video_id, fmt):
        attempted.append(video_id)
        if video_id == VID:
            raise resolver.TransientResolveError("network")
        return f"https://cdn.example/{video_id}", {}

    prefetcher = _prefetcher(resolve, lambda url, headers: warmed.append(url))
    await prefetcher.start()
    try:
        prefetcher.enqueue_results(_results(VID, VID2), "opus")
        await asyncio.wait_for(prefetcher._queue.join(), 1)
    finally:
        await prefetcher.stop()

    assert attempted == [VID, VID2]
    assert warmed == [f"https://cdn.example/{VID2}"]


async def test_drops_jobs_when_queue_is_full():
    async def resolve(video_id, fmt):
        return "https://cdn.example/audio", {}

    prefetcher = _prefetcher(resolve, queue_size=1)
    count = prefetcher.enqueue_results(_results(VID, VID2, VID3), "opus")

    assert count == 1
    assert prefetcher._queue.qsize() == 1


async def test_skips_invalid_video_ids():
    async def resolve(video_id, fmt):
        return "https://cdn.example/audio", {}

    prefetcher = _prefetcher(resolve)
    count = prefetcher.enqueue_results(
        [{"video_id": "short"}, {"video_id": VID}], "opus"
    )

    assert count == 1
    job = prefetcher._queue.get_nowait()
    assert job.video_id == VID


async def test_stop_cancels_worker():
    started = asyncio.Event()
    release = asyncio.Event()

    async def resolve(video_id, fmt):
        started.set()
        await release.wait()
        return "https://cdn.example/audio", {}

    prefetcher = _prefetcher(resolve)
    await prefetcher.start()
    prefetcher.enqueue_results(_results(VID), "opus")
    await asyncio.wait_for(started.wait(), 1)

    await prefetcher.stop()

    assert prefetcher._task is None


# ------------------------------------------- settle window and deduplication
async def test_settle_drops_superseded_job_before_extraction():
    """A stale queued job must be discarded before it spends a yt-dlp slot."""
    resolved = []

    async def resolve(video_id, fmt, should_stop=None):
        resolved.append(video_id)
        return "https://cdn.example/audio", {}

    prefetcher = _prefetcher(resolve, settle_seconds=0.2)
    await prefetcher.start()
    try:
        prefetcher.enqueue_results(_results(VID), "opus", owner=1)
        await asyncio.sleep(0.05)
        prefetcher.enqueue_results(_results(VID2), "opus", owner=1)
        await asyncio.wait_for(prefetcher._queue.join(), 2)
    finally:
        await prefetcher.stop()

    assert resolved == [VID2]


async def test_latest_generation_is_processed_after_stale_settle():
    """Several fast keystrokes leave only the final query's top result."""
    resolved = []

    async def resolve(video_id, fmt, should_stop=None):
        resolved.append(video_id)
        return "https://cdn.example/audio", {}

    prefetcher = _prefetcher(resolve, settle_seconds=0.15)
    await prefetcher.start()
    try:
        prefetcher.enqueue_results(_results(VID), "opus", owner=1)
        await asyncio.sleep(0.03)
        prefetcher.enqueue_results(_results(VID2), "opus", owner=1)
        await asyncio.sleep(0.03)
        prefetcher.enqueue_results(_results(VID3), "opus", owner=1)
        await asyncio.wait_for(prefetcher._queue.join(), 2)
    finally:
        await prefetcher.stop()

    assert resolved == [VID3]


async def test_settle_zero_keeps_existing_behavior():
    resolved = []

    async def resolve(video_id, fmt, should_stop=None):
        resolved.append(video_id)
        return "https://cdn.example/audio", {}

    prefetcher = _prefetcher(resolve, settle_seconds=0)
    await prefetcher.start()
    try:
        prefetcher.enqueue_results(_results(VID), "opus", owner=1)
        await asyncio.wait_for(prefetcher._queue.join(), 1)
    finally:
        await prefetcher.stop()

    assert resolved == [VID]


async def test_duplicate_result_signature_is_skipped_within_window():
    async def resolve(video_id, fmt, should_stop=None):
        return "https://cdn.example/audio", {}

    prefetcher = _prefetcher(resolve, settle_seconds=0, dedupe_seconds=60)
    first = prefetcher.enqueue_results(_results(VID2, VID), "opus", owner=1)
    second = prefetcher.enqueue_results(_results(VID, VID2), "opus", owner=1)

    assert (first, second) == (2, 0)
    assert prefetcher._queue.qsize() == 2


async def test_same_signature_is_allowed_after_dedupe_window():
    async def resolve(video_id, fmt, should_stop=None):
        return "https://cdn.example/audio", {}

    prefetcher = _prefetcher(resolve, settle_seconds=0, dedupe_seconds=0.03)
    await prefetcher.start()
    try:
        assert prefetcher.enqueue_results(_results(VID), "opus", owner=1) == 1
        await asyncio.wait_for(prefetcher._queue.join(), 1)
        await asyncio.sleep(0.05)
        assert prefetcher.enqueue_results(_results(VID), "opus", owner=1) == 1
        await asyncio.wait_for(prefetcher._queue.join(), 1)
    finally:
        await prefetcher.stop()

