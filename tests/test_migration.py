"""
tests/test_migration.py — safe SQLite backup/verify for the EU volume move.

The production database runs in WAL mode, so copying only ``lammot.db`` can
lose recently committed rows.  ``app/migration.py`` uses SQLite's backup API
and must be testable without network or Railway.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from app import migration


def _make_db(path, rows: int = 3, *, journal: str = "WAL") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA journal_mode={journal}")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE favorites (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE playlists (id INTEGER PRIMARY KEY)")
    for _ in range(rows):
        conn.execute("INSERT INTO users DEFAULT VALUES")
        conn.execute("INSERT INTO favorites DEFAULT VALUES")
        conn.execute("INSERT INTO playlists DEFAULT VALUES")
    conn.commit()
    return conn


def test_backup_includes_committed_wal_rows(tmp_path):
    source = tmp_path / "lammot.db"
    dest = tmp_path / "lammot-backup.db"
    # Keep the source connection open: WAL rows live outside the main file
    # until a checkpoint, exactly like the running production service.
    conn = _make_db(source)
    try:
        result = migration.backup_database(str(source), str(dest))
    finally:
        conn.close()

    assert result["integrity"] == "ok"
    assert result["counts"] == {"users": 3, "favorites": 3, "playlists": 3}

    check = sqlite3.connect(dest)
    try:
        assert check.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3
    finally:
        check.close()


def test_verify_reports_only_tables_that_exist(tmp_path):
    path = tmp_path / "partial.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO users DEFAULT VALUES")
    conn.commit()
    conn.close()

    result = migration.verify_database(str(path))

    assert result["integrity"] == "ok"
    assert result["counts"] == {"users": 1}


def test_backup_overwrites_an_existing_destination(tmp_path):
    source = tmp_path / "lammot.db"
    dest = tmp_path / "lammot-backup.db"
    src_conn = _make_db(source, rows=5)
    try:
        old = _make_db(dest, rows=1, journal="DELETE")
        old.close()

        result = migration.backup_database(str(source), str(dest))
    finally:
        src_conn.close()

    assert result["counts"]["users"] == 5


def test_backup_rejects_a_missing_source(tmp_path):
    with pytest.raises(FileNotFoundError):
        migration.backup_database(str(tmp_path / "nope.db"), str(tmp_path / "out.db"))


def test_cli_backs_up_and_verifies(tmp_path, capsys):
    source = tmp_path / "lammot.db"
    dest = tmp_path / "backup.db"
    conn = _make_db(source, rows=2)
    try:
        assert migration.main(["--source", str(source), "--dest", str(dest)]) == 0
    finally:
        conn.close()

    payload = json.loads(capsys.readouterr().out)
    assert payload["integrity"] == "ok"
    assert payload["counts"]["users"] == 2

    assert migration.main(["--verify", str(dest)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["integrity"] == "ok"


def test_cli_fails_on_broken_verify(tmp_path, capsys):
    broken = tmp_path / "not-a-database.db"
    broken.write_text("this is not sqlite")

    assert migration.main(["--verify", str(broken)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["integrity"] != "ok"
