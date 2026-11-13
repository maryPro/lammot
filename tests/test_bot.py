"""tests/test_bot.py — /start reply building and webhook secret (no network)."""

import re

from app import bot

PUBLIC = "https://lammot.example"


def _update(text, chat_id=42):
    return {"message": {"chat": {"id": chat_id}, "text": text}}


def test_start_reply_has_web_app_button():
    reply = bot.build_start_reply(_update("/start"), PUBLIC)
    button = reply["reply_markup"]["inline_keyboard"][0][0]
    assert reply["chat_id"] == 42
    assert button == {"text": bot.OPEN_BUTTON_TEXT, "web_app": {"url": PUBLIC}}


def test_start_with_playlist_payload_opens_that_playlist():
    reply = bot.build_start_reply(_update("/start playlist_AbCdEfGhIjKlMnOpQr12"), PUBLIC)
    url = reply["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"]
    assert url == f"{PUBLIC}/?playlist=AbCdEfGhIjKlMnOpQr12"


def test_other_messages_are_ignored():
    assert bot.build_start_reply(_update("hello"), PUBLIC) is None
    assert bot.build_start_reply({"edited_message": {}}, PUBLIC) is None


def test_webhook_secret_fits_telegram_rules():
    secret = bot.webhook_secret("123:abc")
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret)
    assert secret != bot.webhook_secret("123:other")
