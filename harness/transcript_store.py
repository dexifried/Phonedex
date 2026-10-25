"""Opt-in, self-hosted text transcript storage for PhoneDex.

No call recording, network requests, phone numbers or external dependencies.
Transcript storage is OFF by default; use PHONEDEX_TRANSCRIPT_MODE=local to enable.
Data is plain SQLite and relies on local OS filesystem access controls.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

ID = re.compile(r"^call_[a-f0-9]{32}$")
MODES = ("off", "local", "worker", "both")
MAX_FRAGMENTS = 2000
DEFAULT_RETENTION_DAYS = 7
SPEAKERS = frozenset(("caller", "assistant"))


def transcript_mode() -> str:
    mode = os.environ.get("PHONEDEX_TRANSCRIPT_MODE", "off").strip().lower()
    if mode not in MODES:
        raise ValueError("PHONEDEX_TRANSCRIPT_MODE must be off, local, worker, or both")
    return mode


def local_enabled() -> bool:
    return transcript_mode() in ("local", "both")


def worker_enabled() -> bool:
    return transcript_mode() in ("worker", "both")


def retention_days() -> int:
    raw = os.environ.get("PHONEDEX_TRANSCRIPT_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS))
    try:
        n = int(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("Transcript retention must be an integer number of days") from exc
    if not (1 <= n <= 90):
        raise ValueError("Transcript retention must be between 1 and 90 days")
    return n


def storage_path() -> Path:
    base = os.environ.get("PHONEDEX_DATA_DIR", "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".phonedex"
    return root / "transcripts.sqlite3"


class LocalTranscriptStore:
    def __init__(self, path: Path | None = None, *, days: int | None = None):
        self.path = Path(path) if path is not None else storage_path()
        self.days = days if days is not None else retention_days()
        if not (1 <= self.days <= 90):
            raise ValueError("Invalid transcript retention")
        # No filesystem effects in the constructor.
        if self.path.is_symlink():
            raise OSError("Refusing a symlinked transcript database")

    def _open(self) -> sqlite3.Connection:
        folder = self.path.parent
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        if folder.is_symlink() or self.path.is_symlink():
            raise OSError("Refusing symlinked transcript storage")
        # Create mode 0600 on POSIX. Windows security follows NTFS ACLs.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("""CREATE TABLE IF NOT EXISTS calls (
            id TEXT PRIMARY KEY, direction TEXT NOT NULL,
            started_at TEXT NOT NULL, updated_epoch INTEGER NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS fragments (
            call_id TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL, speaker TEXT NOT NULL,
            ms INTEGER NOT NULL, text TEXT NOT NULL,
            PRIMARY KEY(call_id, seq)
        )""")
        db.commit()
        return db

    def prune(self) -> int:
        with closing(self._open()) as db, db:
            before = db.execute(
                "SELECT COUNT(*) FROM calls WHERE updated_epoch < ?",
                (int(time.time())-self.days*86400,),
            ).fetchone()[0]
            db.execute("DELETE FROM calls WHERE updated_epoch < ?",
                       (int(time.time())-self.days*86400,))
            return before

    def append(self, call_id: str, direction: str, started_at: str,
               start_index: int, items: list[dict]) -> int:
        if not ID.fullmatch(call_id) or direction not in ("inbound", "outbound"):
            raise ValueError("Invalid local call identifier or direction")
        if not isinstance(started_at, str) or len(started_at) > 64:
            raise ValueError("Invalid call timestamp")
        if (not isinstance(start_index, int) or start_index < 0 or
                start_index + len(items) > MAX_FRAGMENTS):
            raise ValueError("Local transcript size limit exceeded")
        if not items:
            return 0
        valid = []
        for index, item in enumerate(items, start_index):
            if not isinstance(item, dict):
                raise ValueError("Invalid transcript item")
            speaker = item.get("speaker")
            text = item.get("text")
            ms = item.get("ms", 0)
            if (speaker not in SPEAKERS or not isinstance(text, str) or
                    not 1 <= len(text) <= 1200 or not isinstance(ms, (int,float)) or
                    not 0 <= ms <= 3600000):
                raise ValueError("Invalid transcript fragment")
            valid.append((call_id, index, speaker, int(ms), text))
        now = int(time.time())
        with closing(self._open()) as db, db:
            db.execute(
                "INSERT INTO calls (id, direction, started_at, updated_epoch) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET updated_epoch=excluded.updated_epoch",
                (call_id, direction, started_at, now),
            )
            db.executemany(
                "INSERT OR IGNORE INTO fragments (call_id, seq, speaker, ms, text) "
                "VALUES (?, ?, ?, ?, ?)", valid,
            )
            db.execute("DELETE FROM calls WHERE updated_epoch < ?", (now-self.days*86400,))
        return len(valid)

    def list_calls(self, limit: int = 30) -> list[dict]:
        with closing(self._open()) as db, db:
            rows = db.execute("""SELECT c.id, c.direction, c.started_at, COUNT(f.seq)
                FROM calls c LEFT JOIN fragments f ON f.call_id=c.id
                GROUP BY c.id ORDER BY c.updated_epoch DESC LIMIT ?""",
                (max(1,min(int(limit),200)),)).fetchall()
        return [{"id":r[0], "direction":r[1], "started_at":r[2], "fragments":r[3]}
                for r in rows]

    def get_call(self, call_id: str) -> list[dict]:
        if not ID.fullmatch(call_id):
            raise ValueError("Invalid local call identifier")
        with closing(self._open()) as db, db:
            rows = db.execute(
                "SELECT speaker, ms, text FROM fragments WHERE call_id=? ORDER BY seq",
                (call_id,),
            ).fetchall()
        return [{"speaker":r[0],"ms":r[1],"text":r[2]} for r in rows]

    def delete(self, call_id: str) -> bool:
        if not ID.fullmatch(call_id):
            raise ValueError("Invalid local call identifier")
        with closing(self._open()) as db, db:
            result = db.execute("DELETE FROM calls WHERE id=?", (call_id,))
            return result.rowcount > 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage local PhoneDex text transcripts. No cloud access.")
    action = parser.add_subparsers(dest="action", required=True)
    action.add_parser("list", help="List local transcript IDs and timestamps")
    get = action.add_parser("show", help="Print transcript text locally")
    get.add_argument("call_id")
    delete = action.add_parser("delete", help="Delete a call's local text")
    delete.add_argument("call_id")
    action.add_parser("prune", help="Delete records older than the configured retention")
    args = parser.parse_args()
    store = LocalTranscriptStore()
    if args.action == "list":
        print(json.dumps(store.list_calls(), ensure_ascii=False, indent=2))
    elif args.action == "show":
        print(json.dumps(store.get_call(args.call_id), ensure_ascii=False, indent=2))
    elif args.action == "delete":
        print("DELETED" if store.delete(args.call_id) else "NOT_FOUND")
    elif args.action == "prune":
        print("DELETED_EXPIRED_CALLS="+str(store.prune()))


if __name__ == "__main__":
    main()
