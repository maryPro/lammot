"""
app/auth.py — identity and signed links
LammotBot Telegram Mini App Music Player

Two jobs, both pure functions (no FastAPI here, easy to unit-test):

1. verify_init_data(): checks Telegram Mini App initData.
   https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
   - HMAC-SHA256 signature, compared in constant time
   - auth_date older than max_age is rejected (replay protection)

2. sign_audio_token() / verify_audio_token(): short-lived signed links for
   /audio/{video_id}. An <audio> tag cannot send an Authorization header, so
   the link itself carries a signature bound to video_id, format, user and
   expiry. A token for one track cannot be reused for another.

All keys are derived from BOT_TOKEN with distinct labels, so there is only one
secret to manage. Rotating the bot token in BotFather invalidates everything.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qsl


class AuthError(Exception):
    """Authentication failed. The message is safe to show to the client."""


@dataclass(frozen=True)
class TelegramUser:
    id: int
    first_name: Optional[str]
    username: Optional[str]


def derive_key(bot_token: str, label: bytes) -> bytes:
    """Independent key per purpose, derived from the bot token."""
    return hmac.new(label, bot_token.encode(), hashlib.sha256).digest()


# ---------------------------------------------------------------------------
# Telegram initData
# ---------------------------------------------------------------------------
def verify_init_data(
    init_data: str,
    bot_token: str,
    max_age: int,
    now: Optional[float] = None,
) -> Dict[str, str]:
    """Return the parsed initData fields (without hash) or raise AuthError."""
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        raise AuthError("Invalid initData format") from None

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise AuthError("Missing hash in initData")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = derive_key(bot_token, b"WebAppData")
    calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    # Constant-time comparison — protects against timing attacks.
    # Compare bytes: compare_digest raises TypeError on non-ASCII str input.
    if not hmac.compare_digest(calculated.encode(), received_hash.encode()):
        raise AuthError("Invalid Telegram signature")

    try:
        auth_date = int(parsed.get("auth_date", ""))
    except ValueError:
        raise AuthError("Missing or invalid auth_date") from None
    current = time.time() if now is None else now
    if current - auth_date > max_age:
        raise AuthError("initData expired")

    return parsed


def parse_user(parsed: Dict[str, str]) -> TelegramUser:
    """Extract the Telegram user from verified initData."""
    try:
        raw = json.loads(parsed["user"])
        user_id = int(raw["id"])
    except (KeyError, ValueError, TypeError):
        raise AuthError("initData has no valid user") from None
    if user_id <= 0:
        raise AuthError("initData has no valid user")

    def _short(value, limit: int) -> Optional[str]:
        return str(value)[:limit] if value else None

    return TelegramUser(
        id=user_id,
        first_name=_short(raw.get("first_name"), 128),
        username=_short(raw.get("username"), 64),
    )


# ---------------------------------------------------------------------------
# Signed audio links
# Format: "<fmt>.<user_id>.<expires_at>.<signature>"
# ---------------------------------------------------------------------------
AUDIO_FORMATS = ("opus", "aac")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _audio_signature(key: bytes, video_id: str, fmt: str, user_id: int, exp: int) -> str:
    msg = f"{video_id}|{fmt}|{user_id}|{exp}".encode()
    return _b64(hmac.new(key, msg, hashlib.sha256).digest())


def sign_audio_token(
    key: bytes,
    video_id: str,
    fmt: str,
    user_id: int,
    ttl: int,
    now: Optional[float] = None,
) -> str:
    if fmt not in AUDIO_FORMATS:
        raise ValueError(f"fmt must be one of {AUDIO_FORMATS}")
    exp = int((time.time() if now is None else now) + ttl)
    sig = _audio_signature(key, video_id, fmt, user_id, exp)
    return f"{fmt}.{user_id}.{exp}.{sig}"


def verify_audio_token(
    key: bytes,
    video_id: str,
    token: str,
    now: Optional[float] = None,
) -> Tuple[str, int]:
    """Return (fmt, user_id) or raise AuthError."""
    parts = token.split(".")
    if len(parts) != 4:
        raise AuthError("Invalid audio link")
    fmt, user_part, exp_part, sig = parts
    if fmt not in AUDIO_FORMATS:
        raise AuthError("Invalid audio link")
    try:
        user_id = int(user_part)
        exp = int(exp_part)
    except ValueError:
        raise AuthError("Invalid audio link") from None

    expected = _audio_signature(key, video_id, fmt, user_id, exp)
    if not hmac.compare_digest(expected.encode(), sig.encode()):
        raise AuthError("Invalid audio link")
    if (time.time() if now is None else now) > exp:
        raise AuthError("Audio link expired")
    return fmt, user_id
