"""SQLite storage for durable winner CAS."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path


class RaceDB:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _init(self) -> None:
        with self._lock, sqlite3.connect(self.path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS winners (
                    run_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    patch_hash TEXT NOT NULL,
                    base_commit TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    seq INTEGER,
                    created_at REAL
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    cause TEXT
                )"""
            )
            conn.commit()

    def try_set_winner(self, run_id: str, candidate_id: str, patch_hash: str, base_commit: str, evidence: dict) -> bool:
        import time

        with self._lock, sqlite3.connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute("SELECT run_id FROM winners WHERE run_id = ?", (run_id,))
                if cur.fetchone() is not None:
                    conn.execute("ROLLBACK")
                    return False
                cur = conn.execute("SELECT state FROM runs WHERE run_id = ?", (run_id,))
                row = cur.fetchone()
                if row and row[0] in ("CANCELLED", "FAILED", "UNVERIFIABLE", "COMPLETED"):
                    conn.execute("ROLLBACK")
                    return False
                conn.execute(
                    "INSERT OR REPLACE INTO runs(run_id,state) VALUES(?,?)",
                    (run_id, "CANCELLING"),
                )
                conn.execute(
                    "INSERT INTO winners(run_id,candidate_id,patch_hash,base_commit,evidence_json,seq,created_at) VALUES(?,?,?,?,?,?,?)",
                    (run_id, candidate_id, patch_hash, base_commit, json.dumps(evidence), None, time.time()),
                )
                conn.execute("COMMIT")
                return True
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_winner(self, run_id: str) -> dict | None:
        with self._lock, sqlite3.connect(self.path) as conn:
            cur = conn.execute("SELECT candidate_id,patch_hash,base_commit,evidence_json FROM winners WHERE run_id = ?", (run_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {"candidate_id": row[0], "patch_hash": row[1], "base_commit": row[2], "evidence": json.loads(row[3])}
