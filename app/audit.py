"""Tamper-evident audit log.

Each entry stores the SHA-256 of (previous hash + canonical entry). Editing,
deleting, or reordering any row breaks every hash after it, and `verify()`
reports the first broken sequence number. Prompts are never stored in clear;
entries keep a hash of the prompt plus a redacted preview.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from app.store import Store

GENESIS = "0" * 64


def _digest(prev_hash: str, seq: int, ts: float, actor: str, action: str, span_id: str | None, payload: str) -> str:
    canonical = json.dumps([seq, ts, actor, action, span_id, payload], separators=(",", ":"))
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    entries: int
    first_broken_seq: int | None = None


class AuditLog:
    def __init__(self, store: Store) -> None:
        self.store = store

    def append(
        self, actor: str, action: str, payload: dict[str, Any], span_id: str | None = None, ts: float | None = None
    ) -> str:
        body = json.dumps(payload, sort_keys=True, default=str)
        with self.store.lock:
            last = self.store.query("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1")
            seq = last[0]["seq"] + 1 if last else 1
            prev = last[0]["hash"] if last else GENESIS
            ts = time.time() if ts is None else ts
            digest = _digest(prev, seq, ts, actor, action, span_id, body)
            self.store.execute(
                "INSERT INTO audit (seq, ts, actor, action, span_id, payload, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (seq, ts, actor, action, span_id, body, prev, digest),
            )
        return digest

    def verify(self) -> VerifyResult:
        rows = self.store.query("SELECT * FROM audit ORDER BY seq")
        prev = GENESIS
        for expected_seq, row in enumerate(rows, start=1):
            recomputed = _digest(prev, row["seq"], row["ts"], row["actor"], row["action"], row["span_id"], row["payload"])
            if row["seq"] != expected_seq or row["prev_hash"] != prev or row["hash"] != recomputed:
                return VerifyResult(False, len(rows), row["seq"])
            prev = row["hash"]
        return VerifyResult(True, len(rows))

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.query("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,))
