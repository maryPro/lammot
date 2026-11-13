"""
app/database.py — Sprint 5: SQLite storage via aiosqlite
LammotBot Telegram Mini App Music Player

Stores users, favorites, playlists, search/play history and two caches
(stream URLs, track_key -> video_id).

Rules this module follows:
  - DB path comes from DATABASE_PATH (default ./lammot.db), read on every
    connect so tests can point it at a temporary file.
  - Only parameterised SQL (``?`` placeholders). No string formatting in
    queries, including table names.
  - PRAGMA foreign_keys is per-connection in SQLite, so it is switched on in
    _connect() for every connection, not only in init_db().
  - History is capped per user: SEARCH_HISTORY_LIMIT searches and
    PLAY_HISTORY_LIMIT plays. Older rows are deleted in the same transaction
    as the insert.
  - Every function takes telegram_user_id from the caller. The API layer must
    pass the id from verified initData only, never from the URL.

Ordering uses the AUTOINCREMENT ``id`` rather than timestamps:
CURRENT_TIMESTAMP has 1-second resolution, ``id`` is strictly increasing.

Column names follow ARCHITECTURE.md (track_id, duration). The API layer maps
them to the public track shape (video_id, duration_seconds).
"""

from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import aiosqlite

DEFAULT_DATABASE_PATH = "./lammot.db"

# Retention: how many rows of history are kept per user
SEARCH_HISTORY_LIMIT = 50
PLAY_HISTORY_LIMIT = 200

# Stream URLs from the CDN expire in ~6 h; keep the same 2 h TTL as app/cache.py
STREAM_CACHE_TTL_SECONDS = 7200

HISTORY_KINDS = ("search", "play", "all")


class PlaylistNotFoundError(LookupError):
    """Playlist does not exist or belongs to another user (API maps this to 404)."""


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL
        REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    track_id TEXT NOT NULL,
    title TEXT,
    artist TEXT,
    duration INTEGER,
    thumbnail_url TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(telegram_user_id, track_id)
);
CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(telegram_user_id, id);

CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL
        REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    share_token TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_playlists_user ON playlists(telegram_user_id, id);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER NOT NULL
        REFERENCES playlists(id) ON DELETE CASCADE,
    track_id TEXT NOT NULL,
    title TEXT,
    artist TEXT,
    duration INTEGER,
    thumbnail_url TEXT,
    position INTEGER NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(playlist_id, track_id)
);

CREATE TABLE IF NOT EXISTS search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL
        REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    query TEXT NOT NULL,
    searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_search_history_user ON search_history(telegram_user_id, id);

CREATE TABLE IF NOT EXISTS play_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL
        REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    track_id TEXT NOT NULL,
    title TEXT,
    artist TEXT,
    thumbnail_url TEXT,
    played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_play_history_user ON play_history(telegram_user_id, id);

-- No personal data below: shared caches, not tied to a user
CREATE TABLE IF NOT EXISTS stream_cache (
    video_id TEXT PRIMARY KEY,
    stream_url TEXT NOT NULL,
    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS track_resolution (
    track_key TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    resolved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


# ---------------------------------------------------------------------------
# Connections and transactions
# ---------------------------------------------------------------------------
def _db_path() -> str:
    return os.getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH)


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """
    Open a connection with the pragmas every connection needs.

    isolation_level=None turns off sqlite3's implicit transactions, so
    transactions are opened only by _transaction() and are explicit.
    """
    async with aiosqlite.connect(_db_path(), isolation_level=None) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("PRAGMA busy_timeout = 5000")
        yield db


@asynccontextmanager
async def _transaction() -> AsyncIterator[aiosqlite.Connection]:
    """
    One write transaction: COMMIT on success, ROLLBACK on any error.

    BEGIN IMMEDIATE takes the write lock up front, so read-then-write steps
    (ownership check, MAX(position), history trim) cannot interleave with
    another writer.
    """
    async with _connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            yield db
        except BaseException:
            await db.execute("ROLLBACK")
            raise
        await db.execute("COMMIT")


async def _fetchall(db: aiosqlite.Connection, sql: str, params: tuple) -> List[Dict[str, Any]]:
    async with db.execute(sql, params) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _fetchone(db: aiosqlite.Connection, sql: str, params: tuple) -> Optional[Dict[str, Any]]:
    async with db.execute(sql, params) as cur:
        row = await cur.fetchone()
    return dict(row) if row is not None else None


def _clamp_limit(limit: int, maximum: int) -> int:
    return max(1, min(int(limit), maximum))


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
async def init_db() -> None:
    """Create the DB file (and its folder), tables and indexes if missing."""
    parent = Path(_db_path()).parent
    parent.mkdir(parents=True, exist_ok=True)

    async with _connect() as db:
        # WAL is stored in the file itself, so setting it once is enough
        await db.execute("PRAGMA journal_mode = WAL")
        await db.executescript(SCHEMA)

        # Some SQLite builds ship without FK support; cascades would then
        # silently do nothing. Fail at startup instead.
        row = await _fetchone(db, "PRAGMA foreign_keys", ())
        if not row or list(row.values())[0] != 1:
            raise RuntimeError("SQLite foreign key support is not available")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
async def get_or_create_user(
    telegram_user_id: int,
    first_name: Optional[str],
    username: Optional[str],
) -> Dict[str, Any]:
    """
    Return the user row, creating it on first open.

    first_name / username are refreshed when they changed in Telegram.
    The common case (user exists, nothing changed) is a read only, so this is
    cheap to call on every request.
    """
    async with _connect() as db:
        user = await _fetchone(
            db,
            "SELECT telegram_user_id, username, first_name, created_at "
            "FROM users WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
    if user and user["first_name"] == first_name and user["username"] == username:
        return user

    async with _transaction() as db:
        await db.execute(
            "INSERT INTO users (telegram_user_id, first_name, username) VALUES (?, ?, ?) "
            "ON CONFLICT(telegram_user_id) DO UPDATE SET "
            "first_name = excluded.first_name, username = excluded.username",
            (telegram_user_id, first_name, username),
        )
        user = await _fetchone(
            db,
            "SELECT telegram_user_id, username, first_name, created_at "
            "FROM users WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
    assert user is not None
    return user


# ---------------------------------------------------------------------------
# Favorites
# ---------------------------------------------------------------------------
async def get_favorites(telegram_user_id: int) -> List[Dict[str, Any]]:
    """User's favorites, newest first."""
    async with _connect() as db:
        return await _fetchall(
            db,
            "SELECT track_id, title, artist, duration, thumbnail_url, added_at "
            "FROM favorites WHERE telegram_user_id = ? ORDER BY id DESC",
            (telegram_user_id,),
        )


async def add_favorite(
    telegram_user_id: int,
    track_id: str,
    title: Optional[str],
    artist: Optional[str],
    duration: Optional[int],
    thumbnail_url: Optional[str],
) -> bool:
    """Add a track. Returns False if it was already in favorites."""
    async with _transaction() as db:
        cur = await db.execute(
            "INSERT INTO favorites "
            "(telegram_user_id, track_id, title, artist, duration, thumbnail_url) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(telegram_user_id, track_id) DO NOTHING",
            (telegram_user_id, track_id, title, artist, duration, thumbnail_url),
        )
        added = cur.rowcount == 1
        await cur.close()
    return added


async def remove_favorite(telegram_user_id: int, track_id: str) -> bool:
    """Remove a track. Returns False if it was not in this user's favorites."""
    async with _transaction() as db:
        cur = await db.execute(
            "DELETE FROM favorites WHERE telegram_user_id = ? AND track_id = ?",
            (telegram_user_id, track_id),
        )
        removed = cur.rowcount > 0
        await cur.close()
    return removed


# ---------------------------------------------------------------------------
# Search history (capped at SEARCH_HISTORY_LIMIT per user)
# ---------------------------------------------------------------------------
async def get_search_history(telegram_user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """Latest search queries, newest first."""
    limit = _clamp_limit(limit, SEARCH_HISTORY_LIMIT)
    async with _connect() as db:
        return await _fetchall(
            db,
            "SELECT query, searched_at FROM search_history "
            "WHERE telegram_user_id = ? ORDER BY id DESC LIMIT ?",
            (telegram_user_id, limit),
        )


async def add_search_query(telegram_user_id: int, query: str) -> None:
    """Save a query and drop everything older than the newest SEARCH_HISTORY_LIMIT."""
    async with _transaction() as db:
        await db.execute(
            "INSERT INTO search_history (telegram_user_id, query) VALUES (?, ?)",
            (telegram_user_id, query),
        )
        await db.execute(
            "DELETE FROM search_history WHERE telegram_user_id = ? AND id NOT IN ("
            "  SELECT id FROM search_history WHERE telegram_user_id = ? "
            "  ORDER BY id DESC LIMIT ?"
            ")",
            (telegram_user_id, telegram_user_id, SEARCH_HISTORY_LIMIT),
        )


# ---------------------------------------------------------------------------
# Play history (capped at PLAY_HISTORY_LIMIT per user)
# ---------------------------------------------------------------------------
async def get_play_history(telegram_user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    """Latest played tracks, newest first. Radio uses the track_ids from here."""
    limit = _clamp_limit(limit, PLAY_HISTORY_LIMIT)
    async with _connect() as db:
        return await _fetchall(
            db,
            "SELECT track_id, title, artist, thumbnail_url, played_at FROM play_history "
            "WHERE telegram_user_id = ? ORDER BY id DESC LIMIT ?",
            (telegram_user_id, limit),
        )


async def add_played_track(
    telegram_user_id: int,
    track_id: str,
    title: Optional[str],
    artist: Optional[str],
    thumbnail_url: Optional[str],
) -> None:
    """Save a play and drop everything older than the newest PLAY_HISTORY_LIMIT."""
    async with _transaction() as db:
        await db.execute(
            "INSERT INTO play_history (telegram_user_id, track_id, title, artist, thumbnail_url) "
            "VALUES (?, ?, ?, ?, ?)",
            (telegram_user_id, track_id, title, artist, thumbnail_url),
        )
        await db.execute(
            "DELETE FROM play_history WHERE telegram_user_id = ? AND id NOT IN ("
            "  SELECT id FROM play_history WHERE telegram_user_id = ? "
            "  ORDER BY id DESC LIMIT ?"
            ")",
            (telegram_user_id, telegram_user_id, PLAY_HISTORY_LIMIT),
        )


# ---------------------------------------------------------------------------
# Playlists
# Every function that touches one playlist checks ownership with
# "WHERE id = ? AND telegram_user_id = ?" and raises PlaylistNotFoundError
# both for "does not exist" and "belongs to someone else", so the API cannot
# be used to find out which playlist ids exist.
# ---------------------------------------------------------------------------
async def _assert_owner(db: aiosqlite.Connection, telegram_user_id: int, playlist_id: int) -> None:
    row = await _fetchone(
        db,
        "SELECT 1 AS ok FROM playlists WHERE id = ? AND telegram_user_id = ?",
        (playlist_id, telegram_user_id),
    )
    if row is None:
        raise PlaylistNotFoundError(playlist_id)


async def create_playlist(telegram_user_id: int, name: str) -> Dict[str, Any]:
    """Create a playlist with a fresh share token (secrets.token_urlsafe(16))."""
    share_token = secrets.token_urlsafe(16)
    async with _transaction() as db:
        cur = await db.execute(
            "INSERT INTO playlists (telegram_user_id, name, share_token) VALUES (?, ?, ?)",
            (telegram_user_id, name, share_token),
        )
        playlist_id = cur.lastrowid
        await cur.close()
        playlist = await _fetchone(
            db,
            "SELECT id, name, share_token, created_at, 0 AS track_count "
            "FROM playlists WHERE id = ?",
            (playlist_id,),
        )
    assert playlist is not None
    return playlist


async def get_playlists(telegram_user_id: int) -> List[Dict[str, Any]]:
    """User's playlists with track counts, newest first."""
    async with _connect() as db:
        return await _fetchall(
            db,
            "SELECT p.id, p.name, p.share_token, p.created_at, "
            "       COUNT(t.id) AS track_count "
            "FROM playlists p LEFT JOIN playlist_tracks t ON t.playlist_id = p.id "
            "WHERE p.telegram_user_id = ? "
            "GROUP BY p.id ORDER BY p.id DESC",
            (telegram_user_id,),
        )


async def get_playlist(telegram_user_id: int, playlist_id: int) -> Dict[str, Any]:
    """One playlist of this user (name, share token, track count)."""
    async with _connect() as db:
        row = await _fetchone(
            db,
            "SELECT p.id, p.name, p.share_token, p.created_at, "
            "       COUNT(t.id) AS track_count "
            "FROM playlists p LEFT JOIN playlist_tracks t ON t.playlist_id = p.id "
            "WHERE p.id = ? AND p.telegram_user_id = ? "
            "GROUP BY p.id",
            (playlist_id, telegram_user_id),
        )
    if row is None:
        raise PlaylistNotFoundError(playlist_id)
    return row


async def delete_playlist(telegram_user_id: int, playlist_id: int) -> None:
    """Delete the user's playlist and its tracks. Its share link stops working."""
    async with _transaction() as db:
        await _assert_owner(db, telegram_user_id, playlist_id)
        # Explicit, so the result does not depend on PRAGMA foreign_keys
        await db.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
        await db.execute(
            "DELETE FROM playlists WHERE id = ? AND telegram_user_id = ?",
            (playlist_id, telegram_user_id),
        )


async def get_playlist_tracks(telegram_user_id: int, playlist_id: int) -> List[Dict[str, Any]]:
    """Tracks of the user's own playlist, in playlist order."""
    async with _connect() as db:
        await _assert_owner(db, telegram_user_id, playlist_id)
        return await _fetchall(
            db,
            "SELECT track_id, title, artist, duration, thumbnail_url, position, added_at "
            "FROM playlist_tracks WHERE playlist_id = ? ORDER BY position, id",
            (playlist_id,),
        )


async def add_track_to_playlist(
    telegram_user_id: int,
    playlist_id: int,
    track_id: str,
    title: Optional[str],
    artist: Optional[str],
    duration: Optional[int],
    thumbnail_url: Optional[str],
) -> bool:
    """Append a track to the end. Returns False if it is already in the playlist."""
    async with _transaction() as db:
        await _assert_owner(db, telegram_user_id, playlist_id)
        row = await _fetchone(
            db,
            "SELECT COALESCE(MAX(position), -1) + 1 AS next_pos "
            "FROM playlist_tracks WHERE playlist_id = ?",
            (playlist_id,),
        )
        next_pos = row["next_pos"] if row else 0
        cur = await db.execute(
            "INSERT INTO playlist_tracks "
            "(playlist_id, track_id, title, artist, duration, thumbnail_url, position) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(playlist_id, track_id) DO NOTHING",
            (playlist_id, track_id, title, artist, duration, thumbnail_url, next_pos),
        )
        added = cur.rowcount == 1
        await cur.close()
    return added


async def remove_track_from_playlist(telegram_user_id: int, playlist_id: int, track_id: str) -> bool:
    """Remove a track. Returns False if the track was not in the playlist."""
    async with _transaction() as db:
        await _assert_owner(db, telegram_user_id, playlist_id)
        cur = await db.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
            (playlist_id, track_id),
        )
        removed = cur.rowcount > 0
        await cur.close()
    return removed


async def get_shared_playlist(share_token: str) -> Optional[Dict[str, Any]]:
    """
    Public read-only view of a playlist by its share token.

    Returns only name and tracks: no owner id, no internal playlist id.
    None if the token is unknown (or the owner deleted their data).
    """
    async with _connect() as db:
        playlist = await _fetchone(
            db,
            "SELECT id, name, created_at FROM playlists WHERE share_token = ?",
            (share_token,),
        )
        if playlist is None:
            return None
        tracks = await _fetchall(
            db,
            "SELECT track_id, title, artist, duration, thumbnail_url, position "
            "FROM playlist_tracks WHERE playlist_id = ? ORDER BY position, id",
            (playlist["id"],),
        )
    return {"name": playlist["name"], "created_at": playlist["created_at"], "tracks": tracks}


# ---------------------------------------------------------------------------
# Shared caches (no personal data)
# Tables are ready for a future persistent cache; the running app uses the
# in-memory cache in app/cache.py.
# ---------------------------------------------------------------------------
async def get_cached_stream(video_id: str) -> Optional[str]:
    """Stream URL if it was cached less than STREAM_CACHE_TTL_SECONDS ago."""
    async with _connect() as db:
        row = await _fetchone(
            db,
            "SELECT stream_url FROM stream_cache "
            "WHERE video_id = ? AND cached_at > datetime('now', ?)",
            (video_id, f"-{STREAM_CACHE_TTL_SECONDS} seconds"),
        )
    return row["stream_url"] if row else None


async def set_cached_stream(video_id: str, stream_url: str) -> None:
    """Store a stream URL and drop expired ones."""
    async with _transaction() as db:
        await db.execute(
            "INSERT INTO stream_cache (video_id, stream_url) VALUES (?, ?) "
            "ON CONFLICT(video_id) DO UPDATE SET "
            "stream_url = excluded.stream_url, cached_at = CURRENT_TIMESTAMP",
            (video_id, stream_url),
        )
        await db.execute(
            "DELETE FROM stream_cache WHERE cached_at <= datetime('now', ?)",
            (f"-{STREAM_CACHE_TTL_SECONDS} seconds",),
        )


async def get_track_resolution(track_key: str) -> Optional[str]:
    """video_id previously resolved for a normalized "artist - title" key."""
    async with _connect() as db:
        row = await _fetchone(
            db,
            "SELECT video_id FROM track_resolution WHERE track_key = ?",
            (track_key,),
        )
    return row["video_id"] if row else None


async def set_track_resolution(track_key: str, video_id: str) -> None:
    async with _transaction() as db:
        await db.execute(
            "INSERT INTO track_resolution (track_key, video_id) VALUES (?, ?) "
            "ON CONFLICT(track_key) DO UPDATE SET "
            "video_id = excluded.video_id, resolved_at = CURRENT_TIMESTAMP",
            (track_key, video_id),
        )


# ---------------------------------------------------------------------------
# User-initiated deletion (GDPR: erasure)
# ---------------------------------------------------------------------------
async def clear_history(telegram_user_id: int, kind: str = "all") -> None:
    """
    Delete the user's search history, play history, or both.

    kind: "search" | "play" | "all". Favorites and playlists are not touched.
    """
    if kind not in HISTORY_KINDS:
        raise ValueError(f"kind must be one of {HISTORY_KINDS}")

    async with _transaction() as db:
        if kind in ("search", "all"):
            await db.execute(
                "DELETE FROM search_history WHERE telegram_user_id = ?",
                (telegram_user_id,),
            )
        if kind in ("play", "all"):
            await db.execute(
                "DELETE FROM play_history WHERE telegram_user_id = ?",
                (telegram_user_id,),
            )


async def delete_user_data(telegram_user_id: int) -> bool:
    """
    Delete everything stored about one user, in one transaction.

    Removes: users row, favorites, playlists with their tracks, search and
    play history. stream_cache and track_resolution are shared caches without
    personal data and are left alone. Shared links to this user's playlists
    stop working (404).

    Each table is deleted explicitly even though the FKs cascade, so the
    result does not depend on PRAGMA foreign_keys being on.

    The next time the user opens the Mini App, get_or_create_user() creates a
    new, empty user row. Deleted data is not restored.

    Returns True if the user existed.
    """
    async with _transaction() as db:
        await db.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id IN ("
            "  SELECT id FROM playlists WHERE telegram_user_id = ?"
            ")",
            (telegram_user_id,),
        )
        await db.execute("DELETE FROM playlists WHERE telegram_user_id = ?", (telegram_user_id,))
        await db.execute("DELETE FROM favorites WHERE telegram_user_id = ?", (telegram_user_id,))
        await db.execute("DELETE FROM search_history WHERE telegram_user_id = ?", (telegram_user_id,))
        await db.execute("DELETE FROM play_history WHERE telegram_user_id = ?", (telegram_user_id,))
        cur = await db.execute("DELETE FROM users WHERE telegram_user_id = ?", (telegram_user_id,))
        existed = cur.rowcount > 0
        await cur.close()
    return existed
