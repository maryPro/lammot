"""
tests/test_api.py — API, Telegram initData auth, audio relay, bot webhook

External services are never called: search / resolver / radio functions are
patched where app.main looks them up, the CDN is an httpx.MockTransport and
the Bot API call is an AsyncMock. Each test gets its own temporary database.
"""

import hashlib
import hmac
import json
import time
import urllib.parse
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app import main
from app.main import BOT_TOKEN, app

VID = "dQw4w9WgXcQ"
VID2 = "9bZkp7q19f0"
CDN_URL = "https://rr1---sn-abc.googlevideo.com/videoplayback?itag=251"
TRACK = {
    "video_id": VID,
    "title": "Test Track",
    "artist": "Test Artist",
    "duration_seconds": 213,
    "thumbnail_url": "https://i.ytimg.com/vi/x/hq.jpg",
}


def make_tma_header(user_id: int = 123456, auth_date: int | None = None) -> str:
    data = {
        "query_id": "AAHdF6IQAAAAAN0XohD-test",
        "user": json.dumps({"id": user_id, "first_name": "Test", "username": f"u{user_id}"}),
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
    }
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return f"tma {urllib.parse.urlencode(data)}"


@pytest.fixture
def headers():
    return {"Authorization": make_tma_header()}


@pytest.fixture
def other_headers():
    return {"Authorization": make_tma_header(user_id=654321)}


@pytest.fixture
async def client(temp_db):
    main.search_limiter.reset()
    main.stream_limiter.reset()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# --------------------------------------------------------------------- auth
async def test_missing_auth_header(client):
    r = await client.get("/api/search?q=test")
    assert r.status_code == 401


async def test_invalid_signature(client):
    r = await client.get(
        "/api/search?q=test",
        headers={"Authorization": "tma query_id=test&auth_date=1&hash=invalid"},
    )
    assert r.status_code == 401


async def test_expired_init_data(client):
    old = int(time.time()) - 2 * 86400
    r = await client.get(
        "/api/search?q=test", headers={"Authorization": make_tma_header(auth_date=old)}
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "initData expired"


async def test_no_user_id_in_paths(client, headers):
    """Old-style /api/favorites/{telegram_user_id} must not exist any more."""
    r = await client.get("/api/favorites/123456", headers=headers)
    assert r.status_code in (404, 405)


async def test_security_headers(client, headers):
    with patch("app.main.search.search_songs", return_value=[]):
        r = await client.get("/api/search?q=x", headers=headers)
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"


async def test_me(client, headers):
    r = await client.get("/api/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["data"]["user"]["id"] == 123456


# ------------------------------------------------------------------- search
async def test_search_endpoint(client, headers):
    fake = [{"video_id": VID, "title": "Test Track"}]
    with patch("app.main.search.search_songs", return_value=fake) as m:
        r = await client.get("/api/search?q=test&limit=3", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "data": fake}
    m.assert_called_once_with("test", 3)


async def test_search_upstream_error_hides_details(client, headers):
    with patch("app.main.search.search_songs", side_effect=Exception("secret internals")):
        r = await client.get("/api/search?q=test", headers=headers)
    assert r.status_code == 502
    assert "secret internals" not in r.text


async def test_search_rate_limited(client, headers, monkeypatch):
    monkeypatch.setattr(main.search_limiter, "limit", 2)
    with patch("app.main.search.search_songs", return_value=[]):
        codes = [(await client.get("/api/search?q=a", headers=headers)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# ------------------------------------------------------------------- stream
async def test_stream_returns_signed_same_origin_link(client, headers):
    with patch("app.main.resolver.get_stream_url", return_value=CDN_URL) as m:
        r = await client.get(f"/api/stream/{VID}", headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["video_id"] == VID and data["format"] == "opus"
    assert data["stream_url"].startswith(f"/audio/{VID}?t=opus.123456.")
    assert "googlevideo" not in data["stream_url"]  # CDN URL never reaches the client
    m.assert_called_once_with(VID, "opus")


async def test_stream_aac_format(client, headers):
    with patch("app.main.resolver.get_stream_url", return_value=CDN_URL) as m:
        r = await client.get(f"/api/stream/{VID}?format=aac", headers=headers)
    assert r.json()["data"]["format"] == "aac"
    m.assert_called_once_with(VID, "aac")


async def test_stream_rejects_invalid_video_id(client, headers):
    with patch("app.main.resolver.get_stream_url") as m:
        r = await client.get("/api/stream/not-a-valid-id!!", headers=headers)
    assert r.status_code == 422
    m.assert_not_called()


async def test_stream_no_format_returns_404(client, headers):
    with patch("app.main.resolver.get_stream_url", side_effect=ValueError("no format")):
        r = await client.get(f"/api/stream/{VID}", headers=headers)
    assert r.status_code == 404


# -------------------------------------------------------------- audio relay
@pytest.fixture
async def cdn(monkeypatch):
    """Fake googlevideo: records requests, answers from a list of responses."""
    seen, answers = [], []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, hdrs, body = answers.pop(0) if answers else (200, {}, b"")
        return httpx.Response(status, headers=hdrs, stream=httpx.ByteStream(body))

    fake = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(main, "_http_client", fake)
    yield seen, answers
    await fake.aclose()


async def _audio_link(client, headers, vid=VID):
    with patch("app.main.resolver.get_stream_url", return_value=CDN_URL):
        r = await client.get(f"/api/stream/{vid}", headers=headers)
    return r.json()["data"]["stream_url"]


async def test_audio_relay_passes_range(client, headers, cdn):
    seen, answers = cdn
    answers.append((206, {"content-type": "audio/webm", "content-range": "bytes 0-3/10"}, b"OPUS"))
    link = await _audio_link(client, headers)

    with patch("app.main.resolver.get_stream_url", return_value=CDN_URL):
        r = await client.get(link, headers={"Range": "bytes=0-3"})

    assert r.status_code == 206
    assert r.content == b"OPUS"
    assert r.headers["content-range"] == "bytes 0-3/10"
    assert r.headers["accept-ranges"] == "bytes"
    assert str(seen[0].url) == CDN_URL
    assert seen[0].headers["range"] == "bytes=0-3"


async def test_audio_relay_reresolves_once_on_403(client, headers, cdn):
    seen, answers = cdn
    answers.extend([(403, {}, b""), (200, {"content-type": "audio/webm"}, b"OK")])
    link = await _audio_link(client, headers)

    with patch("app.main.resolver.get_stream_url", return_value=CDN_URL) as resolve, \
         patch("app.main.resolver.invalidate") as invalidate:
        r = await client.get(link)

    assert r.status_code == 200 and r.content == b"OK"
    invalidate.assert_called_once_with(VID, "opus")
    assert resolve.call_count == 2
    assert len(seen) == 2


async def test_audio_relay_rejects_bad_or_foreign_token(client, headers, cdn):
    link = await _audio_link(client, headers, vid=VID)
    token = link.split("t=", 1)[1]

    r = await client.get(f"/audio/{VID}?t=opus.123456.9999999999.forged")
    assert r.status_code == 403
    # A valid token for one track cannot be used for another
    r = await client.get(f"/audio/{VID2}?t={token}")
    assert r.status_code == 403
    assert cdn[0] == []  # the CDN was never contacted


# -------------------------------------------------------------------- radio
async def test_radio_uses_queue_and_play_history(client, headers):
    played = "kJQP7kiw5Fk"
    r = await client.post(
        "/api/history", headers=headers,
        json={"kind": "play", "track": {**TRACK, "video_id": VID2}},
    )
    assert r.status_code == 200

    fake = [{"video_id": "abcdefghijk", "title": "Recommended Track"}]
    with patch("app.main.radio.get_radio_queue", return_value=fake) as m:
        r = await client.get(f"/api/radio/{VID}?exclude={played}", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "data": fake}
    m.assert_called_once_with(VID, [played, VID2], 5)


async def test_radio_rejects_invalid_exclude(client, headers):
    with patch("app.main.radio.get_radio_queue") as m:
        r = await client.get(f"/api/radio/{VID}?exclude=bad", headers=headers)
    assert r.status_code == 422
    m.assert_not_called()


async def test_radio_rejects_too_many_excludes(client, headers):
    qs = "&".join(f"exclude={VID}" for _ in range(51))
    with patch("app.main.radio.get_radio_queue") as m:
        r = await client.get(f"/api/radio/{VID}?{qs}", headers=headers)
    assert r.status_code == 422
    m.assert_not_called()


# ---------------------------------------------------------------- favorites
async def test_favorites_flow_and_isolation(client, headers, other_headers):
    r = await client.post("/api/favorites", headers=headers, json=TRACK)
    assert r.json()["data"] == {"added": True}
    r = await client.post("/api/favorites", headers=headers, json=TRACK)
    assert r.json()["data"] == {"added": False}

    r = await client.get("/api/favorites", headers=headers)
    assert r.json()["data"] == [TRACK]  # same track shape as search results
    r = await client.get("/api/favorites", headers=other_headers)
    assert r.json()["data"] == []

    r = await client.delete(f"/api/favorites/{VID}", headers=other_headers)
    assert r.json()["data"] == {"removed": False}
    r = await client.delete(f"/api/favorites/{VID}", headers=headers)
    assert r.json()["data"] == {"removed": True}


async def test_favorite_rejects_non_https_thumbnail(client, headers):
    bad = {**TRACK, "thumbnail_url": "javascript:alert(1)"}
    r = await client.post("/api/favorites", headers=headers, json=bad)
    assert r.status_code == 422


# ------------------------------------------------------------------ history
async def test_history_dedupes_recent_searches(client, headers):
    for q in ["Zemfira", "nirvana", "zemfira"]:
        await client.post("/api/history", headers=headers, json={"kind": "search", "query": q})
    r = await client.get("/api/history", headers=headers)
    assert r.json()["data"]["searches"] == ["zemfira", "nirvana"]


async def test_clear_history_keeps_favorites(client, headers):
    await client.post("/api/favorites", headers=headers, json=TRACK)
    await client.post("/api/history", headers=headers, json={"kind": "search", "query": "x"})
    r = await client.delete("/api/history?type=search", headers=headers)
    assert r.status_code == 204
    assert (await client.get("/api/history", headers=headers)).json()["data"]["searches"] == []
    assert len((await client.get("/api/favorites", headers=headers)).json()["data"]) == 1


# ---------------------------------------------------------------- playlists
async def test_playlist_flow_share_and_isolation(client, headers, other_headers):
    r = await client.post("/api/playlists", headers=headers, json={"name": "  Road trip "})
    pl = r.json()["data"]
    assert pl["name"] == "Road trip" and pl["track_count"] == 0

    r = await client.post(f"/api/playlists/{pl['id']}/tracks", headers=headers, json=TRACK)
    assert r.json()["data"] == {"added": True}

    r = await client.get(f"/api/playlists/{pl['id']}", headers=headers)
    assert r.json()["data"]["tracks"] == [TRACK]

    # Another user: the playlist looks missing everywhere
    assert (await client.get(f"/api/playlists/{pl['id']}", headers=other_headers)).status_code == 404
    r = await client.post(f"/api/playlists/{pl['id']}/tracks", headers=other_headers, json=TRACK)
    assert r.status_code == 404
    assert (await client.delete(f"/api/playlists/{pl['id']}", headers=other_headers)).status_code == 404

    # Shared link works without auth and does not expose the owner
    r = await client.get(f"/api/playlists/shared/{pl['share_token']}")
    assert r.status_code == 200
    assert r.json()["data"] == {"name": "Road trip", "tracks": [TRACK]}

    assert (await client.delete(f"/api/playlists/{pl['id']}", headers=headers)).status_code == 204
    assert (await client.get(f"/api/playlists/shared/{pl['share_token']}")).status_code == 404


async def test_playlist_name_cannot_be_blank(client, headers):
    r = await client.post("/api/playlists", headers=headers, json={"name": "   "})
    assert r.status_code == 422


# --------------------------------------------------------------- delete me
async def test_delete_me_requires_confirm(client, headers):
    r = await client.delete("/api/me", headers=headers)
    assert r.status_code == 400


async def test_delete_me_removes_only_my_data(client, headers, other_headers):
    for h in (headers, other_headers):
        await client.post("/api/favorites", headers=h, json=TRACK)
    r = await client.delete("/api/me?confirm=true", headers=headers)
    assert r.status_code == 204
    assert (await client.get("/api/favorites", headers=headers)).json()["data"] == []
    assert len((await client.get("/api/favorites", headers=other_headers)).json()["data"]) == 1


# ------------------------------------------------------------------ webhook
def _start_update(text="/start"):
    return {"update_id": 1, "message": {"message_id": 1, "chat": {"id": 42}, "text": text}}


async def test_webhook_rejects_missing_secret(client):
    r = await client.post("/telegram/webhook", json=_start_update())
    assert r.status_code == 401


async def test_webhook_start_sends_open_button(client, monkeypatch):
    monkeypatch.setattr(main, "PUBLIC_URL", "https://lammot.example")
    with patch("app.main.bot.call", new=AsyncMock()) as call:
        r = await client.post(
            "/telegram/webhook",
            json=_start_update(),
            headers={"X-Telegram-Bot-Api-Secret-Token": main.WEBHOOK_SECRET},
        )
    assert r.status_code == 200
    _, _, method, payload = call.call_args.args
    assert method == "sendMessage"
    button = payload["reply_markup"]["inline_keyboard"][0][0]
    assert button["web_app"]["url"] == "https://lammot.example"
