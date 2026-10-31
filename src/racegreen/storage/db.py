"""Durable SQLite trace storage and winner arbitration."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class RaceDB:
    """Append-only event store with projections rebuilt in the same transaction."""

    EVENT_LIMIT = 64 * 1024

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    cause TEXT,
                    base_commit TEXT,
                    config_fingerprint TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS run_events (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    agent_id TEXT,
                    candidate_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS agents (
                    run_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_event_sequence INTEGER NOT NULL,
                    PRIMARY KEY (run_id, agent_id)
                );
                CREATE TABLE IF NOT EXISTS candidates (
                    run_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    agent_id TEXT,
                    state TEXT NOT NULL,
                    patch_hash TEXT,
                    last_event_sequence INTEGER NOT NULL,
                    PRIMARY KEY (run_id, candidate_id)
                );
                CREATE TABLE IF NOT EXISTS winners (
                    run_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    patch_hash TEXT NOT NULL,
                    base_commit TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    seq INTEGER,
                    created_at REAL
                );
                INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', '2');
                """
            )
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()
            }
            for name, definition in (
                ("base_commit", "TEXT"),
                ("config_fingerprint", "TEXT"),
                ("created_at", "TEXT"),
                ("updated_at", "TEXT"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {definition}")

    @classmethod
    def _bounded_json(cls, payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) <= cls.EVENT_LIMIT:
            return encoded
        marker = {"truncated": True, "original_bytes": len(encoded.encode("utf-8"))}
        while len(json.dumps({**payload, **marker}, sort_keys=True).encode("utf-8")) > cls.EVENT_LIMIT:
            longest = max((key for key, value in payload.items() if isinstance(value, str)), key=len, default="")
            if not longest:
                break
            payload = {key: value for key, value in payload.items() if key != longest}
        return json.dumps({**payload, **marker}, sort_keys=True, separators=(",", ":"))

    def append_event(self, record: dict[str, Any]) -> dict[str, Any]:
        run_id = str(record["run_id"])
        event_type = str(record.get("event", ""))
        if not event_type:
            raise ValueError("event record must contain event")
        event_id = str(record.get("event_id") or f"{run_id}:{time.time_ns()}")
        payload = dict(record)
        payload["event_id"] = event_id
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT payload_json FROM run_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if existing is not None:
                return dict(json.loads(existing["payload_json"]))
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
                "FROM run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            sequence = int(row["next_sequence"])
            payload["sequence"] = sequence
            encoded = self._bounded_json(payload)
            conn.execute(
                "INSERT INTO run_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    sequence,
                    event_id,
                    event_type,
                    record.get("agent_id"),
                    record.get("candidate_id"),
                    encoded,
                    str(record.get("timestamp", "")),
                ),
            )
            self._project_event(conn, payload, sequence)
            conn.commit()
            return dict(json.loads(encoded))

    def ingest_events(self, records: list[dict[str, Any]]) -> int:
        count = 0
        for record in records:
            before = self.event_count(str(record["run_id"]))
            self.append_event(record)
            count += int(self.event_count(str(record["run_id"])) > before)
        return count

    def event_count(self, run_id: str) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM run_events WHERE run_id = ?", (run_id,)).fetchone()
            return int(row["count"])

    def events(self, run_id: str, after: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        if limit < 1 or limit > 10000:
            raise ValueError("limit must be between 1 and 10000")
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM run_events WHERE run_id = ? AND sequence > ? "
                "ORDER BY sequence LIMIT ?",
                (run_id, after, limit),
            ).fetchall()
            return [dict(json.loads(row["payload_json"])) for row in rows]

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT run_id, state, cause, base_commit, config_fingerprint, created_at, updated_at "
                "FROM runs ORDER BY COALESCE(updated_at, created_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def run_snapshot(self, run_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            run = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(f"unknown run: {run_id}")
            agents = conn.execute("SELECT * FROM agents WHERE run_id = ?", (run_id,)).fetchall()
            candidates = conn.execute("SELECT * FROM candidates WHERE run_id = ?", (run_id,)).fetchall()
            return {
                "run": dict(run),
                "agents": [dict(row) for row in agents],
                "candidates": [dict(row) for row in candidates],
                "event_count": self.event_count(run_id),
            }

    def verify(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"SQLite integrity check failed: {integrity}")
            violations: list[str] = []
            for row in conn.execute(
                "SELECT run_id, sequence, LAG(sequence) OVER (PARTITION BY run_id ORDER BY sequence) AS previous "
                "FROM run_events"
            ):
                if row["previous"] is not None and row["sequence"] != row["previous"] + 1:
                    violations.append(f"{row['run_id']}: non-contiguous event sequence")
            for row in conn.execute(
                "SELECT run_id, COUNT(*) AS count FROM winners GROUP BY run_id HAVING count > 1"
            ):
                violations.append(f"{row['run_id']}: multiple winners")
            return {"integrity": integrity, "violations": violations}

    def try_set_winner(
        self, run_id: str, candidate_id: str, patch_hash: str, base_commit: str, evidence: dict[str, Any]
    ) -> bool:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM winners WHERE run_id = ?", (run_id,)).fetchone():
                conn.rollback()
                return False
            state = conn.execute("SELECT state FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if state and state["state"] in ("CANCELLED", "FAILED", "UNVERIFIABLE", "COMPLETED"):
                conn.rollback()
                return False
            now = str(time.time())
            conn.execute(
                "INSERT OR REPLACE INTO runs(run_id,state,cause,updated_at) VALUES(?,?,?,?)",
                (run_id, "CANCELLING", "winner_declared", now),
            )
            conn.execute(
                "INSERT INTO winners VALUES(?,?,?,?,?,?,?)",
                (run_id, candidate_id, patch_hash, base_commit, json.dumps(evidence), None, time.time()),
            )
            event: dict[str, Any] = {
                "event_id": f"{run_id}:winner:{candidate_id}",
                "event": "arbiter.winner_declared",
                "run_id": run_id,
                "agent_id": "arbiter",
                "candidate_id": candidate_id,
                "patch_hash": patch_hash,
                "base_commit": base_commit,
                "timestamp": now,
            }
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            sequence = int(row["next_sequence"])
            event["sequence"] = sequence
            encoded = self._bounded_json(event)
            conn.execute(
                "INSERT INTO run_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, sequence, event["event_id"], event["event"], "arbiter", candidate_id, encoded, now),
            )
            conn.commit()
            return True

    def get_winner(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT candidate_id,patch_hash,base_commit,evidence_json FROM winners WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "candidate_id": row["candidate_id"],
                "patch_hash": row["patch_hash"],
                "base_commit": row["base_commit"],
                "evidence": json.loads(row["evidence_json"]),
            }

    @staticmethod
    def _project_event(conn: sqlite3.Connection, payload: dict[str, Any], sequence: int) -> None:
        run_id = str(payload["run_id"])
        event = str(payload["event"])
        now = str(payload.get("timestamp", ""))
        conn.execute(
            "INSERT INTO runs(run_id,state,created_at,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(run_id) DO UPDATE SET updated_at=excluded.updated_at",
            (run_id, "UNKNOWN", now, now),
        )
        agent_id = payload.get("agent_id")
        if agent_id:
            status = {
                "agent_started": "working",
                "agent_status": str(payload.get("status", "working")),
                "agent_completed": "completed",
                "agent_failed": "failed",
                "agent_cancelled": "cancelled",
            }.get(event)
            if status:
                conn.execute(
                    "INSERT INTO agents VALUES(?,?,?,?) ON CONFLICT(run_id,agent_id) "
                    "DO UPDATE SET status=excluded.status,last_event_sequence=excluded.last_event_sequence",
                    (run_id, str(agent_id), status, sequence),
                )
        candidate_id = payload.get("candidate_id")
        if candidate_id:
            state = str(payload.get("state") or payload.get("status") or event)
            conn.execute(
                "INSERT INTO candidates VALUES(?,?,?,?,?,?) ON CONFLICT(run_id,candidate_id) "
                "DO UPDATE SET state=excluded.state,patch_hash=COALESCE(excluded.patch_hash,candidates.patch_hash),"
                "last_event_sequence=excluded.last_event_sequence",
                (run_id, str(candidate_id), agent_id, state, payload.get("patch_hash"), sequence),
            )
