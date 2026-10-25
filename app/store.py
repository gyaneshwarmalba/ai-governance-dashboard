"""SQLite storage for call telemetry, guardrail violations, and the audit chain."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    ts REAL NOT NULL,
    team TEXT NOT NULL,
    project TEXT,
    user_id TEXT,
    provider TEXT,
    model TEXT NOT NULL,
    template TEXT,
    template_version TEXT,
    latency_ms REAL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    status TEXT NOT NULL,
    error TEXT,
    quality REAL
);
CREATE INDEX IF NOT EXISTS calls_ts ON calls(ts);
CREATE INDEX IF NOT EXISTS calls_team_ts ON calls(team, ts);
CREATE TABLE IF NOT EXISTS violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    span_id TEXT NOT NULL REFERENCES calls(span_id),
    ts REAL NOT NULL,
    rule TEXT NOT NULL,
    stage TEXT NOT NULL,
    action TEXT NOT NULL,
    severity TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    span_id TEXT,
    payload TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self.lock = threading.RLock()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def insert_call(self, row: dict[str, Any], violations: list[dict[str, Any]]) -> None:
        cols = ", ".join(row)
        marks = ", ".join(f":{k}" for k in row)
        with self.lock:
            self._conn.execute(f"INSERT OR REPLACE INTO calls ({cols}) VALUES ({marks})", row)
            self._conn.executemany(
                "INSERT INTO violations (span_id, ts, rule, stage, action, severity, detail) "
                "VALUES (:span_id, :ts, :rule, :stage, :action, :severity, :detail)",
                violations,
            )
            self._conn.commit()

    def raw_connection(self) -> sqlite3.Connection:
        return self._conn
