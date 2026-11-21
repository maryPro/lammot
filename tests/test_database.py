"""tests/test_database.py — Sprint 5: SQLite layer (temporary DB per test)."""

import re

import pytest

A, B = 1001, 2002


async def _user(db, uid):
    await db.get_or_create_user(uid, f"User{uid}", f"user{uid}")


async def _fav(db, uid, vid, title="T"):
    return await db.add_favorite(uid, vid, title, "Artist", 200, "https://i.ytimg.com/x.jpg")


# ------------------------------------------------------------------- setup
async def test_init_db_is_idempotent(temp_db):
    await temp_db.init_db()  # second call must not fail or drop data


async def test_get_or_create_user_creates_and_updates(temp_db):
    user = await temp_db.get_or_create_user(A, "Maria", "maria")
    assert user["telegram_user_id"] == A
    user = await temp_db.get_or_create_user(A, "Mariia", None)
    assert (user["first_name"], user["username"]) == ("Mariia", None)


# --------------------------------------------------------------- favorites
async def test_favorites_add_list_remove(temp_db):
    await _user(temp_db, A)
    assert await _fav(temp_db, A, "aaaaaaaaaaa") is True
    assert await _fav(temp_db, A, "bbbbbbbbbbb") is True
    assert await _fav(temp_db, A, "aaaaaaaaaaa") is False  # duplicate

    favs = await temp_db.get_favorites(A)
    assert [f["track_id"] for f in favs] == ["bbbbbbbbbbb", "aaaaaaaaaaa"]  # newest first

    assert await temp_db.remove_favorite(A, "aaaaaaaaaaa") is True
    assert await temp_db.remove_favorite(A, "aaaaaaaaaaa") is False
    assert [f["track_id"] for f in await temp_db.get_favorites(A)] == ["bbbbbbbbbbb"]


async def test_users_are_isolated(temp_db):
    await _user(temp_db, A)
    await _user(temp_db, B)
    await _fav(temp_db, A, "aaaaaaaaaaa")
    assert await temp_db.get_favorites(B) == []
    assert await temp_db.remove_favorite(B, "aaaaaaaaaaa") is False
    assert len(await temp_db.get_favorites(A)) == 1


# ----------------------------------------------------------------- history
async def test_search_history_is_capped(temp_db, monkeypatch):
    monkeypatch.setattr(temp_db, "SEARCH_HISTORY_LIMIT", 3)
    await _user(temp_db, A)
    for q in ["one", "two", "three", "four", "five"]:
        await temp_db.add_search_query(A, q)
    rows = await temp_db.get_search_history(A, limit=10)
    assert [r["query"] for r in rows] == ["five", "four", "three"]


async def test_play_history_is_capped_newest_first(temp_db, monkeypatch):
    monkeypatch.setattr(temp_db, "PLAY_HISTORY_LIMIT", 2)
    await _user(temp_db, A)
    for vid in ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"]:
        await temp_db.add_played_track(A, vid, "T", "Artist", None)
    rows = await temp_db.get_play_history(A, limit=50)
    assert [r["track_id"] for r in rows] == ["ccccccccccc", "bbbbbbbbbbb"]


async def test_clear_history_kinds(temp_db):
    await _user(temp_db, A)
    await temp_db.add_search_query(A, "query")
    await temp_db.add_played_track(A, "aaaaaaaaaaa", "T", "Artist", None)
    await _fav(temp_db, A, "aaaaaaaaaaa")

    await temp_db.clear_history(A, "search")
    assert await temp_db.get_search_history(A) == []
    assert len(await temp_db.get_play_history(A)) == 1

    await temp_db.clear_history(A, "all")
    assert await temp_db.get_play_history(A) == []
    assert len(await temp_db.get_favorites(A)) == 1  # favorites untouched

    with pytest.raises(ValueError):
        await temp_db.clear_history(A, "everything")


# --------------------------------------------------------------- playlists
async def test_playlist_positions_and_duplicates(temp_db):
    await _user(temp_db, A)
    pl = await temp_db.create_playlist(A, "Road trip")
    assert pl["track_count"] == 0
    for vid in ["aaaaaaaaaaa", "bbbbbbbbbbb"]:
        assert await temp_db.add_track_to_playlist(A, pl["id"], vid, "T", "Ar", 100, None) is True
    assert await temp_db.add_track_to_playlist(A, pl["id"], "aaaaaaaaaaa", "T", "Ar", 100, None) is False

    tracks = await temp_db.get_playlist_tracks(A, pl["id"])
    assert [(t["track_id"], t["position"]) for t in tracks] == [("aaaaaaaaaaa", 0), ("bbbbbbbbbbb", 1)]
    assert (await temp_db.get_playlist(A, pl["id"]))["track_count"] == 2
    assert (await temp_db.get_playlists(A))[0]["track_count"] == 2


async def test_playlist_of_another_user_looks_missing(temp_db):
    await _user(temp_db, A)
    await _user(temp_db, B)
    pl = await temp_db.create_playlist(A, "Mine")
    nf = temp_db.PlaylistNotFoundError

    with pytest.raises(nf):
        await temp_db.get_playlist(B, pl["id"])
    with pytest.raises(nf):
        await temp_db.get_playlist_tracks(B, pl["id"])
    with pytest.raises(nf):
        await temp_db.add_track_to_playlist(B, pl["id"], "aaaaaaaaaaa", "T", "Ar", 1, None)
    with pytest.raises(nf):
        await temp_db.remove_track_from_playlist(B, pl["id"], "aaaaaaaaaaa")
    with pytest.raises(nf):
        await temp_db.delete_playlist(B, pl["id"])
    assert await temp_db.get_playlists(B) == []
    assert len(await temp_db.get_playlists(A)) == 1


async def test_delete_playlist_removes_tracks(temp_db):
    await _user(temp_db, A)
    pl = await temp_db.create_playlist(A, "Temp")
    await temp_db.add_track_to_playlist(A, pl["id"], "aaaaaaaaaaa", "T", "Ar", 1, None)
    await temp_db.delete_playlist(A, pl["id"])
    assert await temp_db.get_playlists(A) == []
    assert await temp_db.get_shared_playlist(pl["share_token"]) is None


async def test_shared_playlist_is_read_only_view(temp_db):
    await _user(temp_db, A)
    pl = await temp_db.create_playlist(A, "Shared")
    assert re.fullmatch(r"[A-Za-z0-9_-]{16,64}", pl["share_token"])
    await temp_db.add_track_to_playlist(A, pl["id"], "bbbbbbbbbbb", "Two", "Ar", 1, None)
    await temp_db.add_track_to_playlist(A, pl["id"], "aaaaaaaaaaa", "One", "Ar", 1, None)

    shared = await temp_db.get_shared_playlist(pl["share_token"])
    assert shared["name"] == "Shared"
    assert [t["track_id"] for t in shared["tracks"]] == ["bbbbbbbbbbb", "aaaaaaaaaaa"]
    assert "telegram_user_id" not in shared and "id" not in shared  # no owner leak
    assert await temp_db.get_shared_playlist("unknown-token-1234567") is None


# ---------------------------------------------------------------- artist ids
async def test_artist_id_round_trips_in_favorites_and_playlists(temp_db):
    await _user(temp_db, A)
    artist_id = "UCaaaaaaaa1"
    await temp_db.add_favorite(
        A, "aaaaaaaaaaa", "T", "Ar", 200, None, artist_id=artist_id
    )
    favs = await temp_db.get_favorites(A)
    assert favs[0]["artist_id"] == artist_id

    pl = await temp_db.create_playlist(A, "With artist")
    await temp_db.add_track_to_playlist(
        A, pl["id"], "aaaaaaaaaaa", "T", "Ar", 200, None, artist_id=artist_id
    )
    tracks = await temp_db.get_playlist_tracks(A, pl["id"])
    assert tracks[0]["artist_id"] == artist_id

    shared = await temp_db.get_shared_playlist(pl["share_token"])
    assert shared["tracks"][0]["artist_id"] == artist_id


async def test_init_db_migrates_old_tables_to_artist_id(tmp_path, monkeypatch):
    import aiosqlite

    path = tmp_path / "old.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    async with aiosqlite.connect(path) as db:
        await db.executescript(
            """
            CREATE TABLE favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                track_id TEXT NOT NULL,
                title TEXT,
                artist TEXT,
                duration INTEGER,
                thumbnail_url TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(telegram_user_id, track_id)
            );
            CREATE TABLE playlist_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id INTEGER NOT NULL,
                track_id TEXT NOT NULL,
                title TEXT,
                artist TEXT,
                duration INTEGER,
                thumbnail_url TEXT,
                position INTEGER NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(playlist_id, track_id)
            );
            """
        )
        await db.commit()

    from app import database

    await database.init_db()
    await database.init_db()  # a second startup must be a no-op

    async with database._connect() as db:
        favorites = await database._fetchall(db, "PRAGMA table_info(favorites)", ())
        playlist_tracks = await database._fetchall(db, "PRAGMA table_info(playlist_tracks)", ())

    assert "artist_id" in [column["name"] for column in favorites]
    assert "artist_id" in [column["name"] for column in playlist_tracks]


# ------------------------------------------------------------------- trims
async def test_trim_table_exists(temp_db):
    async with temp_db._connect() as db:
        row = await temp_db._fetchone(
            db,
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'track_trims'",
            (),
        )
    assert row is not None


async def test_trim_upsert_creates_and_replaces(temp_db):
    await _user(temp_db, A)
    row = await temp_db.upsert_trim(A, "aaaaaaaaaaa", 12.5, 42.0)
    assert (row["video_id"], row["start_seconds"], row["end_seconds"]) == ("aaaaaaaaaaa", 12.5, 42.0)

    row = await temp_db.upsert_trim(A, "aaaaaaaaaaa", 5.0, None)
    assert (row["start_seconds"], row["end_seconds"]) == (5.0, None)

    trims = await temp_db.get_trims(A)
    assert len(trims) == 1
    assert trims[0]["start_seconds"] == 5.0 and trims[0]["end_seconds"] is None


async def test_get_trims_isolated_per_user(temp_db):
    await _user(temp_db, A)
    await _user(temp_db, B)
    await temp_db.upsert_trim(A, "aaaaaaaaaaa", 1.0, 20.0)
    await temp_db.upsert_trim(B, "bbbbbbbbbbb", 2.0, 30.0)

    assert [t["video_id"] for t in await temp_db.get_trims(A)] == ["aaaaaaaaaaa"]
    assert [t["video_id"] for t in await temp_db.get_trims(B)] == ["bbbbbbbbbbb"]


async def test_delete_trim_removes_one_row(temp_db):
    await _user(temp_db, A)
    await temp_db.upsert_trim(A, "aaaaaaaaaaa", 1.0, 20.0)
    assert await temp_db.delete_trim(A, "aaaaaaaaaaa") is True
    assert await temp_db.delete_trim(A, "aaaaaaaaaaa") is False
    assert await temp_db.get_trims(A) == []


# ---------------------------------------------------------------- deletion
async def test_delete_user_data_only_affects_that_user(temp_db):
    await _user(temp_db, A)
    await _user(temp_db, B)
    for uid in (A, B):
        await _fav(temp_db, uid, "aaaaaaaaaaa")
        await temp_db.add_search_query(uid, "q")
        await temp_db.add_played_track(uid, "aaaaaaaaaaa", "T", "Ar", None)
        await temp_db.upsert_trim(uid, "aaaaaaaaaaa", 1.0, 20.0)
    pl_a = await temp_db.create_playlist(A, "A list")
    await temp_db.add_track_to_playlist(A, pl_a["id"], "aaaaaaaaaaa", "T", "Ar", 1, None)
    pl_b = await temp_db.create_playlist(B, "B list")

    assert await temp_db.delete_user_data(A) is True

    assert await temp_db.get_favorites(A) == []
    assert await temp_db.get_search_history(A) == []
    assert await temp_db.get_play_history(A) == []
    assert await temp_db.get_playlists(A) == []
    assert await temp_db.get_trims(A) == []
    assert await temp_db.get_shared_playlist(pl_a["share_token"]) is None

    assert len(await temp_db.get_favorites(B)) == 1
    assert len(await temp_db.get_search_history(B)) == 1
    assert len(await temp_db.get_trims(B)) == 1
    assert await temp_db.get_shared_playlist(pl_b["share_token"]) is not None

    assert await temp_db.delete_user_data(A) is False  # already gone
