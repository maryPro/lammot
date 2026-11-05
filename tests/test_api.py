"""
tests/test_api.py — Sprint 4: API + Telegram initData auth

The real module functions (search_songs, get_stream_url, get_radio_queue) are
patched where app.main looks them up, so these tests break if main.py calls
a function that does not exist.
"""

import hashlib
import hmac
import time
import urllib.parse
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import BOT_TOKEN, app

VID = "dQw4w9WgXcQ"


def make_tma_header(user_id: int = 123456, auth_date: int | None = None) -> str:
    data = {
        "query_id": "AAHdF6IQAAAAAN0XohD-test",
        "user": f'{{"id":{user_id},"first_name":"Test","last_name":"User"}}',
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
async def client():
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


# ------------------------------------------------------------------- stream
async def test_stream_endpoint(client, headers):
    url = "https://rr1---sn-abc.googlevideo.com/videoplayback?itag=251"
    with patch("app.main.resolver.get_stream_url", return_value=url) as m:
        r = await client.get(f"/api/stream/{VID}", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "data": {"video_id": VID, "stream_url": url}}
    m.assert_called_once_with(VID)


async def test_stream_rejects_invalid_video_id(client, headers):
    with patch("app.main.resolver.get_stream_url") as m:
        r = await client.get("/api/stream/not-a-valid-id!!", headers=headers)
    assert r.status_code == 422
    m.assert_not_called()


async def test_stream_no_format_returns_404(client, headers):
    with patch("app.main.resolver.get_stream_url", side_effect=ValueError("no format")):
        r = await client.get(f"/api/stream/{VID}", headers=headers)
    assert r.status_code == 404


# -------------------------------------------------------------------- radio
async def test_radio_endpoint(client, headers):
    fake = [{"video_id": "9bZkp7q19f0", "title": "Recommended Track"}]
    played = "kJQP7kiw5Fk"
    with patch("app.main.radio.get_radio_queue", return_value=fake) as m:
        r = await client.get(f"/api/radio/{VID}?exclude={played}", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "data": fake}
    m.assert_called_once_with(VID, [played], 5)


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
