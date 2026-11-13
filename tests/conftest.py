"""
Shared pytest setup.

app.main refuses to start without BOT_TOKEN, so a throwaway test token is set
here before any test module imports the app.  Never put a real token here.
PUBLIC_URL is cleared so tests never try to talk to Telegram.
"""

import os

import pytest

os.environ.setdefault("BOT_TOKEN", "test-only-bot-token")
os.environ["PUBLIC_URL"] = ""


@pytest.fixture
async def temp_db(tmp_path, monkeypatch):
    """A fresh SQLite file per test; the real lammot.db is never touched."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    from app import database

    await database.init_db()
    return database
