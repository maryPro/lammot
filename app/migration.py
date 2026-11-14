"""
app/migration.py — safe SQLite backup/verify for the EU volume move.

The database runs in WAL mode, so copying only ``lammot.db`` can lose rows
that still live in the ``-wal`` file.  SQLite's backup API takes a consistent
snapshot while the service keeps running.

The module ships inside the Docker image, so it can be executed on Railway:

    python -m app.migration --source /data/lammot.db --dest /data/lammot-backup.db
    python -m app.migration --verify ./lammot-backup.db

Both commands print JSON; the exit code is 0 only when integrity_check is ok.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

_COUNT_TABLES = (
    "users",
    "favorites",
    "playlists",
    "playlist_tracks",
    "search_history",
    "play_history",
)


def _readonly(path: str) -> sqlite3.Connection:
    if not Path(path).exists():
        raise FileNotFoundError(f"SQLite database not found: {path}")
    uri = f"file:{quote(str(Path(path).resolve()))}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=10)


def verify_database(path: str) -> Dict[str, object]:
    """Return integrity_check and row counts for *path* without modifying it."""
    try:
        conn = _readonly(path)
    except FileNotFoundError:
        raise
    try:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        counts: Dict[str, int] = {}
        if integrity == "ok":
            for table in _COUNT_TABLES:
                if table in tables:
                    counts[table] = int(
                        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    )
    except sqlite3.DatabaseError as exc:
        return {
            "path": str(path),
            "integrity": f"error: {type(exc).__name__}",
            "counts": {},
        }
    finally:
        conn.close()
    return {"path": str(path), "integrity": integrity, "counts": counts}


def backup_database(source: str, dest: str) -> Dict[str, object]:
    """Copy *source* to *dest* with the SQLite backup API, then verify it."""
    src = _readonly(source)
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    # `backup()` replaces the destination content, but stale WAL/SHM files
    # from an earlier copy could confuse verification.
    for suffix in ("", "-wal", "-shm"):
        stale = Path(f"{dest_path}{suffix}")
        if stale.exists():
            stale.unlink()
    dst = sqlite3.connect(str(dest_path))
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    return verify_database(str(dest_path))


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Consistent SQLite backup/verify for Lammot volume moves."
    )
    parser.add_argument(
        "--source",
        default=os.getenv("DATABASE_PATH", "/data/lammot.db"),
        help="Database to copy (default: $DATABASE_PATH or /data/lammot.db)",
    )
    parser.add_argument(
        "--dest",
        default="/data/lammot-backup.db",
        help="Backup file to create (default: /data/lammot-backup.db)",
    )
    parser.add_argument(
        "--verify",
        metavar="PATH",
        help="Only verify an existing backup instead of making a new one",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        result = (
            verify_database(args.verify)
            if args.verify
            else backup_database(args.source, args.dest)
        )
    except (OSError, sqlite3.Error) as exc:
        print(json.dumps(
            {"path": str(args.verify or args.source), "integrity": "error", "counts": {},
             "error": type(exc).__name__},
            ensure_ascii=False,
            indent=2,
        ))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["integrity"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
