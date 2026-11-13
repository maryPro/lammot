"""
app/bot.py — Telegram bot side of LammotBot
LammotBot Telegram Mini App Music Player

The bot does one thing: answer /start with a button that opens the Mini App.

How updates arrive: a webhook. On startup (when PUBLIC_URL is set) the app
calls setWebhook with a secret_token; Telegram then sends that token in the
X-Telegram-Bot-Api-Secret-Token header of every update, and app/main.py
rejects requests without it.

The bot token is part of every Bot API URL, so this module never logs URLs
and never lets an httpx exception (whose text can contain the URL) escape.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("lammot.bot")

TELEGRAM_API = "https://api.telegram.org"
WEBHOOK_PATH = "/telegram/webhook"
OPEN_BUTTON_TEXT = "Open Lammot 🎵"

# Payload of t.me/<bot>?start=playlist_<token> (fallback share link)
_START_PLAYLIST_RE = re.compile(r"^/start(?:@\w+)?\s+playlist_([A-Za-z0-9_-]{16,64})$")


class TelegramError(Exception):
    """A Bot API call failed. The message never contains the bot token."""


def webhook_secret(bot_token: str) -> str:
    """
    Secret for X-Telegram-Bot-Api-Secret-Token, derived from the bot token.

    64 hex chars — within Telegram's allowed alphabet [A-Za-z0-9_-] and
    1..256 length. Nothing extra to configure or store.
    """
    return hmac.new(b"lammot-webhook", bot_token.encode(), hashlib.sha256).hexdigest()


async def call(client: httpx.AsyncClient, bot_token: str, method: str, payload: Dict[str, Any]) -> Any:
    """POST a Bot API method and return its "result"."""
    try:
        resp = await client.post(f"{TELEGRAM_API}/bot{bot_token}/{method}", json=payload, timeout=15)
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Do not chain the exception: its text may include the URL with the token
        raise TelegramError(f"{method}: {type(exc).__name__}") from None
    if not data.get("ok"):
        raise TelegramError(f"{method}: {data.get('description', 'unknown error')}")
    return data.get("result")


async def setup(client: httpx.AsyncClient, bot_token: str, public_url: str) -> Optional[str]:
    """
    Point Telegram at our webhook and set the chat menu button.

    Returns the bot username (from getMe), used to build share links.
    """
    await call(client, bot_token, "setWebhook", {
        "url": public_url + WEBHOOK_PATH,
        "secret_token": webhook_secret(bot_token),
        "allowed_updates": ["message"],
        "drop_pending_updates": True,
    })
    await call(client, bot_token, "setChatMenuButton", {
        "menu_button": {"type": "web_app", "text": "Open", "web_app": {"url": public_url}},
    })
    me = await call(client, bot_token, "getMe", {})
    return (me or {}).get("username")


def build_start_reply(update: Dict[str, Any], public_url: str) -> Optional[Dict[str, Any]]:
    """
    sendMessage payload for a /start message, or None to ignore the update.

    "/start playlist_<token>" opens the shared playlist directly.
    """
    message = update.get("message") or {}
    text = message.get("text") or ""
    chat_id = (message.get("chat") or {}).get("id")
    if not chat_id or not text.startswith("/start"):
        return None

    url = public_url
    button_text = OPEN_BUTTON_TEXT
    match = _START_PLAYLIST_RE.match(text.strip())
    if match:
        url = f"{public_url}/?playlist={match.group(1)}"
        button_text = "Open shared playlist 🎵"

    return {
        "chat_id": chat_id,
        "text": "Lammot is a music player inside Telegram.\nTap the button to start listening.",
        "reply_markup": {
            "inline_keyboard": [[{"text": button_text, "web_app": {"url": url}}]],
        },
    }
