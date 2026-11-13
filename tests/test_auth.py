"""tests/test_auth.py — initData verification and signed audio links (pure functions)."""

import hashlib
import hmac
import json
import urllib.parse

import pytest

from app import auth

TOKEN = "123:test-token"
NOW = 1_800_000_000


def _init_data(auth_date=NOW, user=None, tamper=False, hash_value=None):
    data = {"auth_date": str(auth_date), "user": json.dumps(user or {"id": 7, "first_name": "Ann"})}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hash_value or hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    if tamper:
        data["auth_date"] = str(auth_date + 1)
    return urllib.parse.urlencode(data)


def test_valid_init_data_and_user():
    parsed = auth.verify_init_data(_init_data(), TOKEN, 3600, now=NOW + 10)
    user = auth.parse_user(parsed)
    assert (user.id, user.first_name, user.username) == (7, "Ann", None)


@pytest.mark.parametrize(
    "init_data, message",
    [
        (_init_data(tamper=True), "Invalid Telegram signature"),
        (_init_data(hash_value="é" * 64), "Invalid Telegram signature"),  # non-ASCII: no crash
        ("not a query string", "Invalid initData format"),
        ("auth_date=1", "Missing hash in initData"),
    ],
)
def test_rejected_init_data(init_data, message):
    with pytest.raises(auth.AuthError, match=message):
        auth.verify_init_data(init_data, TOKEN, 3600, now=NOW)


def test_expired_init_data():
    with pytest.raises(auth.AuthError, match="initData expired"):
        auth.verify_init_data(_init_data(), TOKEN, 3600, now=NOW + 3601)


def test_init_data_without_user_is_rejected():
    parsed = auth.verify_init_data(_init_data(user={"first_name": "x"}), TOKEN, 3600, now=NOW)
    with pytest.raises(auth.AuthError):
        auth.parse_user(parsed)


KEY = auth.derive_key(TOKEN, b"lammot-audio")


def test_audio_token_roundtrip():
    token = auth.sign_audio_token(KEY, "dQw4w9WgXcQ", "aac", 7, ttl=60, now=NOW)
    assert auth.verify_audio_token(KEY, "dQw4w9WgXcQ", token, now=NOW + 59) == ("aac", 7)


def test_audio_token_is_bound_to_track_format_and_time():
    token = auth.sign_audio_token(KEY, "dQw4w9WgXcQ", "opus", 7, ttl=60, now=NOW)
    with pytest.raises(auth.AuthError):
        auth.verify_audio_token(KEY, "9bZkp7q19f0", token, now=NOW)       # other track
    with pytest.raises(auth.AuthError):
        auth.verify_audio_token(KEY, "dQw4w9WgXcQ", "aac" + token[4:], now=NOW)  # other format
    with pytest.raises(auth.AuthError, match="expired"):
        auth.verify_audio_token(KEY, "dQw4w9WgXcQ", token, now=NOW + 61)
    with pytest.raises(auth.AuthError):
        auth.verify_audio_token(auth.derive_key("other", b"lammot-audio"), "dQw4w9WgXcQ", token, now=NOW)
