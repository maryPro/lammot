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


# ---------------------------------------------------------------- deletion
async def test_delete_user_data_only_affects_that_user(temp_db):
    await _user(temp_db, A)
    await _user(temp_db, B)
    for uid in (A, B):
        await _fav(temp_db, uid, "aaaaaaaaaaa")
        await temp_db.add_search_query(uid, "q")
        await temp_db.add_played_track(uid, "aaaaaaaaaaa", "T", "Ar", None)
    pl_a = await temp_db.create_playlist(A, "A list")
    await temp_db.add_track_to_playlist(A, pl_a["id"], "aaaaaaaaaaa", "T", "Ar", 1, None)
    pl_b = await temp_db.create_playlist(B, "B list")

    assert await temp_db.delete_user_data(A) is True

    assert await temp_db.get_favorites(A) == []
    assert await temp_db.get_search_history(A) == []
    assert await temp_db.get_play_history(A) == []
    assert await temp_db.get_playlists(A) == []
    assert await temp_db.get_shared_playlist(pl_a["share_token"]) is None

    assert len(await temp_db.get_favorites(B)) == 1
    assert len(await temp_db.get_search_history(B)) == 1
    assert await temp_db.get_shared_playlist(pl_b["share_token"]) is not None

    assert await temp_db.delete_user_data(A) is False  # already gone
