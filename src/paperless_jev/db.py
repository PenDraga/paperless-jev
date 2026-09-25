"""SQLite-Ablage für Konfiguration, Beschreibungen und Verarbeitungsjobs."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS instances (
    id         INTEGER PRIMARY KEY,
    name       TEXT UNIQUE NOT NULL,
    url        TEXT NOT NULL,
    public_url TEXT NOT NULL DEFAULT '',
    token      TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS descriptions (
    instance_id INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    object_id   INTEGER NOT NULL,
    text        TEXT NOT NULL DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (instance_id, kind, object_id)
);
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY,
    instance_id  INTEGER NOT NULL,
    doc_id       INTEGER NOT NULL,
    doc_title    TEXT,
    source       TEXT NOT NULL,
    status       TEXT NOT NULL,
    model        TEXT,
    input_tokens INTEGER,
    result       TEXT,
    applied      TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS jobs_doc ON jobs(instance_id, doc_id);
"""

JSON_COLUMNS = ("result", "applied")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    def query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_decode(dict(r)) for r in rows]

    def one(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple | list = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, params).lastrowid or 0

    # --- Jobs -------------------------------------------------------------

    def create_job(self, instance_id: int, doc_id: int, source: str) -> int:
        ts = now()
        return self.execute(
            "INSERT INTO jobs (instance_id, doc_id, source, status, created_at, updated_at)"
            " VALUES (?, ?, ?, 'queued', ?, ?)",
            (instance_id, doc_id, source, ts, ts),
        )

    def update_job(self, job_id: int, **fields: Any) -> None:
        fields["updated_at"] = now()
        for col in JSON_COLUMNS:
            if col in fields and fields[col] is not None:
                fields[col] = json.dumps(fields[col], ensure_ascii=False)
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.execute(f"UPDATE jobs SET {cols} WHERE id = ?", [*fields.values(), job_id])

    def job(self, job_id: int) -> dict[str, Any] | None:
        return self.one("SELECT * FROM jobs WHERE id = ?", (job_id,))


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    for col in JSON_COLUMNS:
        if row.get(col):
            row[col] = json.loads(row[col])
    return row
