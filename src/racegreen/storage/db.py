"""Durable SQLite trace storage and winner arbitration."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = 3

_RUN_STATES = (
    "preparing",
    "running",
    "cancelling",
    "cancelled",
    "completed",
    "exhausted",
    "failed",
    "unverifiable",
)
_TERMINAL_RUN_STATES = frozenset(
    {"cancelled", "completed", "exhausted", "failed", "unverifiable"}
)
_CANDIDATE_STATES = (
    "received",
    "prefiltering",
    "prefilter_passed",
    "selected_tests",
    "confirming",
    "won",
    "confirmed_but_not_selected",
    "rejected",
    "cancelled",
    "infra_failed",
)
_TERMINAL_CANDIDATE_STATES = frozenset(
    {"won", "confirmed_but_not_selected", "rejected", "cancelled", "infra_failed"}
)
_AGENT_STATUSES = frozenset(
    {
        "started",
        "working",
        "submitted",
        "completed",
        "timed_out",
        "cancelled",
        "provider_failed",
        "failed",
    }
)
_TERMINAL_AGENT_STATUSES = frozenset(
    {"submitted", "completed", "timed_out", "cancelled", "provider_failed", "failed"}
)

_RUN_TERMINAL_SQL = ",".join(f"'{state}'" for state in sorted(_TERMINAL_RUN_STATES))

_SCHEMA = """
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
    updated_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    last_event_sequence INTEGER NOT NULL DEFAULT 0,
    last_event_type TEXT
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
    last_event_at TEXT,
    last_event_type TEXT,
    PRIMARY KEY (run_id, agent_id)
);
CREATE TABLE IF NOT EXISTS candidates (
    run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    agent_id TEXT,
    state TEXT NOT NULL,
    patch_hash TEXT,
    last_event_sequence INTEGER NOT NULL,
    patch_path TEXT,
    stage TEXT,
    classification TEXT,
    reason TEXT,
    updated_at TEXT,
    last_event_type TEXT,
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
CREATE INDEX IF NOT EXISTS idx_run_events_run_sequence ON run_events(run_id, sequence);
CREATE INDEX IF NOT EXISTS idx_runs_updated ON runs(updated_at);
CREATE INDEX IF NOT EXISTS idx_candidates_run ON candidates(run_id);
CREATE INDEX IF NOT EXISTS idx_agents_run ON agents(run_id);
"""


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
            conn.executescript(_SCHEMA)
            self._migrate(conn)
            conn.commit()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        current = 0
        if row is not None:
            try:
                current = int(row["value"])
            except (TypeError, ValueError):
                current = 0

        additions: dict[str, tuple[tuple[str, str], ...]] = {
            "runs": (
                ("base_commit", "TEXT"),
                ("config_fingerprint", "TEXT"),
                ("created_at", "TEXT"),
                ("updated_at", "TEXT"),
                ("started_at", "TEXT"),
                ("finished_at", "TEXT"),
                ("last_event_sequence", "INTEGER NOT NULL DEFAULT 0"),
                ("last_event_type", "TEXT"),
            ),
            "agents": (
                ("last_event_at", "TEXT"),
                ("last_event_type", "TEXT"),
            ),
            "candidates": (
                ("patch_path", "TEXT"),
                ("stage", "TEXT"),
                ("classification", "TEXT"),
                ("reason", "TEXT"),
                ("updated_at", "TEXT"),
                ("last_event_type", "TEXT"),
            ),
        }
        for table, columns in additions.items():
            existing = {
                str(item["name"])
                for item in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, definition in columns:
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

        if current > _SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema {current} is newer than supported {_SCHEMA_VERSION}"
            )
        if current < _SCHEMA_VERSION:
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(_SCHEMA_VERSION),),
            )
            self._rebuild_projections(conn)

    @classmethod
    def _bounded_json(cls, payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) <= cls.EVENT_LIMIT:
            return encoded
        marker = {"truncated": True, "original_bytes": len(encoded.encode("utf-8"))}
        while (
            len(json.dumps({**payload, **marker}, sort_keys=True).encode("utf-8"))
            > cls.EVENT_LIMIT
        ):
            longest = max(
                (key for key, value in payload.items() if isinstance(value, str)),
                key=len,
                default="",
            )
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
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM run_events WHERE run_id = ?", (run_id,)
            ).fetchone()
            return int(row["count"])

    def events(
        self,
        run_id: str,
        after: int = 0,
        limit: int = 1000,
        *,
        before: int | None = None,
        latest: bool = False,
        event_types: list[str] | None = None,
        agent_id: str | None = None,
        candidate_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 10000:
            raise ValueError("limit must be between 1 and 10000")
        if before is not None and after > 0:
            raise ValueError("after and before are mutually exclusive")

        clauses = ["run_id = ?"]
        params: list[Any] = [run_id]
        if before is not None:
            clauses.append("sequence < ?")
            params.append(before)
        elif after > 0:
            clauses.append("sequence > ?")
            params.append(after)
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(event_types)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if candidate_id:
            clauses.append("candidate_id = ?")
            params.append(candidate_id)

        order = "DESC" if latest else "ASC"
        params.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM run_events "
                f"WHERE {' AND '.join(clauses)} "
                f"ORDER BY sequence {order} LIMIT ?",
                params,
            ).fetchall()
            events = [dict(json.loads(row["payload_json"])) for row in rows]
        if latest:
            events.reverse()
        return events

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT r.*, "
                "(SELECT COUNT(*) FROM agents a WHERE a.run_id = r.run_id) AS agent_count, "
                "(SELECT COUNT(*) FROM candidates c WHERE c.run_id = r.run_id) AS candidate_count, "
                "(SELECT COUNT(*) FROM run_events e WHERE e.run_id = r.run_id) AS event_count, "
                "(SELECT w.candidate_id FROM winners w WHERE w.run_id = r.run_id) AS winner_candidate_id "
                "FROM runs r "
                "ORDER BY COALESCE(NULLIF(r.updated_at, ''), NULLIF(r.created_at, ''), '') DESC "
                "LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def run_snapshot(self, run_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            run = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(f"unknown run: {run_id}")
            agents = conn.execute(
                "SELECT * FROM agents WHERE run_id = ? ORDER BY agent_id", (run_id,)
            ).fetchall()
            candidates = conn.execute(
                "SELECT * FROM candidates WHERE run_id = ? ORDER BY last_event_sequence",
                (run_id,),
            ).fetchall()
            event_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM run_events WHERE run_id = ?",
                    (run_id,),
                ).fetchone()["count"]
            )
            winner = self._get_winner(conn, run_id)
            candidate_rows: list[dict[str, Any]] = []
            for row in candidates:
                candidate = dict(row)
                candidate["has_patch"] = bool(candidate.pop("patch_path", None))
                candidate_rows.append(candidate)
            run_row = dict(run)
            last_sequence = int(
                run_row.get("last_event_sequence") or event_count or 0
            )
            return {
                "run": run_row,
                "agents": [dict(row) for row in agents],
                "candidates": candidate_rows,
                "event_count": event_count,
                "counts": {
                    "agents": len(agents),
                    "candidates": len(candidate_rows),
                    "events": event_count,
                },
                "last_sequence": last_sequence,
                "winner": winner,
            }

    def overview(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            run_by_state = {
                str(row["state"]): int(row["count"])
                for row in conn.execute(
                    "SELECT state, COUNT(*) AS count FROM runs GROUP BY state"
                ).fetchall()
            }
            candidate_by_state = {
                str(row["state"]): int(row["count"])
                for row in conn.execute(
                    "SELECT state, COUNT(*) AS count FROM candidates GROUP BY state"
                ).fetchall()
            }
            agent_by_status = {
                str(row["status"]): int(row["count"])
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS count FROM agents GROUP BY status"
                ).fetchall()
            }
            runs = {
                state: run_by_state.get(state, 0) for state in _RUN_STATES
            }
            candidates = {
                state: candidate_by_state.get(state, 0)
                for state in _CANDIDATE_STATES
            }
            agents = {
                status: agent_by_status.get(status, 0)
                for status in sorted(_AGENT_STATUSES)
            }
            event_total = int(
                conn.execute("SELECT COUNT(*) AS count FROM run_events").fetchone()["count"]
            )
            winner_rows = conn.execute("SELECT evidence_json FROM winners").fetchall()
            confirmed = 0
            for row in winner_rows:
                evidence = _decode_evidence(row["evidence_json"])
                if evidence.get("complete_full_suite") is True:
                    confirmed += 1
            schema_row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            try:
                schema_version = int(schema_row["value"]) if schema_row else _SCHEMA_VERSION
            except (TypeError, ValueError):
                schema_version = _SCHEMA_VERSION
            return {
                "schema_version": schema_version,
                "runs": {
                    "total": sum(runs.values()),
                    "by_state": runs,
                    "active": sum(
                        runs[state] for state in ("preparing", "running", "cancelling")
                    ),
                    "terminal": sum(runs[state] for state in _TERMINAL_RUN_STATES),
                    "completed": runs["completed"],
                },
                "candidates": {
                    "total": sum(candidates.values()),
                    "by_state": candidates,
                },
                "agents": {
                    "total": sum(agents.values()),
                    "by_status": agents,
                },
                "events": event_total,
                "winners": {
                    "total": len(winner_rows),
                    "full_suite_confirmed": confirmed,
                },
            }

    def rebuild_projections(self) -> dict[str, int]:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            counts = self._rebuild_projections(conn)
            conn.commit()
            return counts

    def _rebuild_projections(self, conn: sqlite3.Connection) -> dict[str, int]:
        conn.execute("DELETE FROM runs")
        conn.execute("DELETE FROM agents")
        conn.execute("DELETE FROM candidates")
        rows = conn.execute(
            "SELECT sequence, payload_json FROM run_events ORDER BY run_id, sequence"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"invalid persisted event payload at sequence {row['sequence']}"
                ) from exc
            self._project_event(conn, payload, int(row["sequence"]))
        run_count = int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
        agent_count = int(conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0])
        candidate_count = int(
            conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        )
        return {
            "events": len(rows),
            "runs": run_count,
            "agents": agent_count,
            "candidates": candidate_count,
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
        self,
        run_id: str,
        candidate_id: str,
        patch_hash: str,
        base_commit: str,
        evidence: dict[str, Any],
    ) -> bool:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM winners WHERE run_id = ?", (run_id,)
            ).fetchone():
                conn.rollback()
                return False
            state = conn.execute(
                "SELECT state FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if state and state["state"] in _TERMINAL_RUN_STATES:
                conn.rollback()
                return False
            now = str(time.time())
            conn.execute(
                "INSERT INTO runs(run_id, state, cause, updated_at) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET "
                "state=excluded.state, cause=excluded.cause, updated_at=excluded.updated_at",
                (run_id, "cancelling", "winner_declared", now),
            )
            conn.execute(
                "INSERT INTO winners VALUES(?,?,?,?,?,?,?)",
                (
                    run_id,
                    candidate_id,
                    patch_hash,
                    base_commit,
                    json.dumps(evidence, sort_keys=True),
                    None,
                    time.time(),
                ),
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
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
                "FROM run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            sequence = int(row["next_sequence"])
            conn.execute(
                "UPDATE winners SET seq = ? WHERE run_id = ? AND seq IS NULL",
                (sequence, run_id),
            )
            event["sequence"] = sequence
            encoded = self._bounded_json(event)
            conn.execute(
                "INSERT INTO run_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    sequence,
                    event["event_id"],
                    event["event"],
                    "arbiter",
                    candidate_id,
                    encoded,
                    now,
                ),
            )
            self._project_event(conn, event, sequence)
            conn.commit()
            return True

    def get_winner(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            return self._get_winner(conn, run_id)

    @staticmethod
    def _get_winner(
        conn: sqlite3.Connection, run_id: str
    ) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT candidate_id, patch_hash, base_commit, evidence_json, seq, created_at "
            "FROM winners WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if not row:
            return None
        evidence = _decode_evidence(row["evidence_json"])
        return {
            "candidate_id": row["candidate_id"],
            "patch_hash": row["patch_hash"],
            "base_commit": row["base_commit"],
            "evidence": evidence,
            "evidence_complete": evidence.get("complete_full_suite") is True,
            "sequence": row["seq"],
            "created_at": row["created_at"],
        }

    def candidate_patch_path(self, run_id: str, candidate_id: str) -> str | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT patch_path FROM candidates WHERE run_id = ? AND candidate_id = ?",
                (run_id, candidate_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown candidate: {candidate_id}")
            patch_path = row["patch_path"]
            return str(patch_path) if patch_path else None

    @classmethod
    def _project_event(
        cls, conn: sqlite3.Connection, payload: dict[str, Any], sequence: int
    ) -> None:
        run_id = str(payload["run_id"])
        event = str(payload.get("event", ""))
        now = str(payload.get("timestamp", ""))
        conn.execute(
            "INSERT INTO runs(run_id, state, created_at, updated_at, last_event_sequence, last_event_type) "
            "VALUES(?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET "
            "updated_at=CASE WHEN excluded.updated_at <> '' THEN excluded.updated_at ELSE runs.updated_at END, "
            "last_event_sequence=excluded.last_event_sequence, "
            "last_event_type=excluded.last_event_type",
            (run_id, "UNKNOWN", now, now, sequence, event),
        )

        base_commit = payload.get("base_commit")
        config_fingerprint = payload.get("config_fingerprint")
        if event == "run_configured":
            cls._set_run_fields(
                conn,
                run_id,
                state="preparing",
                base_commit=base_commit,
                config_fingerprint=config_fingerprint,
            )
        elif event == "run_started":
            cls._set_run_fields(
                conn,
                run_id,
                state="running",
                base_commit=base_commit,
                started_at=now,
            )
        elif event == "run_finished":
            state = str(payload.get("state") or "completed").lower()
            if state not in _RUN_STATES:
                state = "completed"
            cls._set_run_fields(conn, run_id, state=state, finished_at=now)
        elif event == "run_failed":
            cls._set_run_fields(
                conn,
                run_id,
                state="failed",
                cause=str(payload.get("error") or payload.get("cause") or "run_failed"),
                finished_at=now,
            )
        elif event in {"run.unverifiable", "run_unverifiable"}:
            cls._set_run_fields(
                conn,
                run_id,
                state="unverifiable",
                cause=str(
                    payload.get("reason") or payload.get("cause") or "unverifiable"
                ),
                finished_at=now,
            )
        elif event in {"arbiter.winner_declared", "candidate.won"}:
            cls._set_run_fields(conn, run_id, state="cancelling", cause="winner_declared")

        agent_id = payload.get("agent_id")
        if agent_id:
            cls._project_agent(
                conn, run_id, str(agent_id), event, payload, sequence, now
            )

        candidate_id = payload.get("candidate_id")
        if candidate_id:
            cls._project_candidate(
                conn,
                run_id,
                str(candidate_id),
                str(agent_id) if agent_id else None,
                event,
                payload,
                sequence,
                now,
            )
        if event in {"candidate.won", "arbiter.winner_declared"} and candidate_id:
            cls._project_winner(conn, run_id, str(candidate_id), payload, sequence)

    @classmethod
    def _set_run_fields(
        cls, conn: sqlite3.Connection, run_id: str, **fields: Any
    ) -> None:
        assignments: list[str] = []
        values: list[Any] = []
        state = fields.get("state")
        if state is not None:
            assignments.append(
                f"state=CASE WHEN state IN ({_RUN_TERMINAL_SQL}) THEN state ELSE ? END"
            )
            values.append(str(state))
        for key in ("cause", "base_commit", "config_fingerprint"):
            value = fields.get(key)
            if value is not None:
                assignments.append(f"{key}=COALESCE(NULLIF(?, ''), {key})")
                values.append(str(value))
        for key in ("started_at", "finished_at"):
            value = fields.get(key)
            if value is not None:
                assignments.append(f"{key}=COALESCE({key}, NULLIF(?, ''))")
                values.append(str(value))
        if not assignments:
            return
        values.append(run_id)
        conn.execute(
            f"UPDATE runs SET {', '.join(assignments)} WHERE run_id = ?", values
        )

    @classmethod
    def _project_agent(
        cls,
        conn: sqlite3.Connection,
        run_id: str,
        agent_id: str,
        event: str,
        payload: dict[str, Any],
        sequence: int,
        now: str,
    ) -> None:
        status: str | None = None
        if event in {"agent_started", "agent_status"}:
            status = str(payload.get("status") or "working").lower()
        elif event == "agent_finished":
            status = str(payload.get("status") or "completed").lower()
        elif event == "agent_failed":
            status = str(payload.get("status") or "failed").lower()
        elif event == "agent_cancelled":
            status = "cancelled"
        if status is None:
            return
        terminal = ",".join(
            f"'{value}'" for value in sorted(_TERMINAL_AGENT_STATUSES)
        )
        conn.execute(
            "INSERT INTO agents(run_id, agent_id, status, last_event_sequence, last_event_at, last_event_type) "
            "VALUES(?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id, agent_id) DO UPDATE SET "
            f"status=CASE WHEN agents.status IN ({terminal}) THEN agents.status "
            "ELSE excluded.status END, "
            "last_event_sequence=excluded.last_event_sequence, "
            "last_event_at=excluded.last_event_at, last_event_type=excluded.last_event_type",
            (run_id, agent_id, status, sequence, now, event),
        )

    @classmethod
    def _project_candidate(
        cls,
        conn: sqlite3.Connection,
        run_id: str,
        candidate_id: str,
        agent_id: str | None,
        event: str,
        payload: dict[str, Any],
        sequence: int,
        now: str,
    ) -> None:
        candidate_event = (
            event.startswith(
                ("candidate_", "candidate.", "prefilter_", "verification_")
            )
            or event == "arbiter.winner_declared"
        )
        if not candidate_event:
            return
        row = conn.execute(
            "SELECT state, patch_hash, patch_path, stage, classification, reason "
            "FROM candidates WHERE run_id = ? AND candidate_id = ?",
            (run_id, candidate_id),
        ).fetchone()
        current = dict(row) if row else {}
        new_state: str | None = None
        stage: str | None = None
        classification = payload.get("classification")
        reason = payload.get("reason") or payload.get("evidence") or payload.get("rule")
        patch_hash = payload.get("patch_hash")
        patch_path = payload.get("patch_path")

        if event == "candidate_frozen":
            new_state = "received"
            stage = "intake"
        elif event == "prefilter_started":
            new_state = "prefiltering"
            stage = "prefilter"
        elif event == "prefilter_finished":
            stage = "prefilter"
            accepted = payload.get("accepted")
            if accepted is True:
                new_state = "prefilter_passed"
            elif accepted is False:
                new_state = "rejected"
        elif event == "candidate_submitted":
            new_state = "prefilter_passed"
            stage = "snapshot"
        elif event == "candidate_rejected":
            new_state = "rejected"
            stage = str(payload.get("stage") or "prefilter")
        elif event == "candidate.rejected":
            new_state = "rejected"
            stage = str(payload.get("stage") or "full_suite")
        elif event in {"candidate.won", "arbiter.winner_declared"}:
            new_state = "won"
            stage = "full_suite"
        elif event == "candidate.confirmed_but_not_selected":
            new_state = "confirmed_but_not_selected"
            stage = str(payload.get("stage") or "full_suite")
        else:
            hint = str(payload.get("state") or "").lower()
            if hint in _CANDIDATE_STATES:
                new_state = hint
            stage = str(payload["stage"]) if payload.get("stage") else None

        if new_state is None and not row:
            new_state = "received"
        state = new_state or str(current.get("state") or "received")
        if (
            current.get("state") in _TERMINAL_CANDIDATE_STATES
            and new_state is not None
            and new_state != current.get("state")
        ):
            state = str(current["state"])
        conn.execute(
            "INSERT INTO candidates(run_id, candidate_id, agent_id, state, patch_hash, last_event_sequence, "
            "patch_path, stage, classification, reason, updated_at, last_event_type) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id, candidate_id) DO UPDATE SET "
            "agent_id=COALESCE(excluded.agent_id, candidates.agent_id), "
            "state=excluded.state, "
            "patch_hash=COALESCE(excluded.patch_hash, candidates.patch_hash), "
            "last_event_sequence=excluded.last_event_sequence, "
            "patch_path=COALESCE(excluded.patch_path, candidates.patch_path), "
            "stage=COALESCE(excluded.stage, candidates.stage), "
            "classification=COALESCE(excluded.classification, candidates.classification), "
            "reason=COALESCE(excluded.reason, candidates.reason), "
            "updated_at=CASE WHEN excluded.updated_at <> '' THEN excluded.updated_at ELSE candidates.updated_at END, "
            "last_event_type=excluded.last_event_type",
            (
                run_id,
                candidate_id,
                agent_id,
                state,
                str(patch_hash) if patch_hash else None,
                sequence,
                str(patch_path) if patch_path else None,
                stage,
                str(classification) if classification else None,
                str(reason) if reason else None,
                now,
                event,
            ),
        )

    @classmethod
    def _project_winner(
        cls,
        conn: sqlite3.Connection,
        run_id: str,
        candidate_id: str,
        payload: dict[str, Any],
        sequence: int,
    ) -> None:
        row = conn.execute(
            "SELECT patch_hash FROM candidates WHERE run_id = ? AND candidate_id = ?",
            (run_id, candidate_id),
        ).fetchone()
        patch_hash = payload.get("patch_hash") or (row["patch_hash"] if row else None)
        if not patch_hash:
            return
        base_commit = payload.get("base_commit")
        if not base_commit:
            run = conn.execute(
                "SELECT base_commit FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            base_commit = run["base_commit"] if run else None
        evidence = payload.get("evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        conn.execute(
            "INSERT OR IGNORE INTO winners(run_id, candidate_id, patch_hash, base_commit, evidence_json, seq, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                candidate_id,
                str(patch_hash),
                str(base_commit or ""),
                json.dumps(evidence, sort_keys=True),
                sequence,
                time.time(),
            ),
        )


def _decode_evidence(value: object) -> dict[str, Any]:
    try:
        evidence = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return evidence if isinstance(evidence, dict) else {}
