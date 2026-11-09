from __future__ import annotations

import json
import socket
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from racegreen.dashboard import create_server
from racegreen.storage.db import RaceDB


def _start(db: RaceDB, tmp_path: Path):
    server = create_server(db, port=0, artifacts_root=tmp_path / "runs")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(server, thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def _get(server, path: str, headers: dict[str, str] | None = None):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    connection.request("GET", path, headers=headers or {})
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response, body


def _seed_run(
    db: RaceDB, patch_path: Path | None = None, *, terminal: bool = True
) -> None:
    events = [
        {
            "event": "run_configured",
            "run_id": "run-dashboard",
            "agent_id": "scheduler",
            "timestamp": "2026-01-01T00:00:00Z",
            "base_commit": "abc123",
            "config_fingerprint": "cfg-1",
        },
        {
            "event": "run_started",
            "run_id": "run-dashboard",
            "agent_id": "scheduler",
            "timestamp": "2026-01-01T00:00:01Z",
            "base_commit": "abc123",
        },
        {
            "event": "agent_started",
            "run_id": "run-dashboard",
            "agent_id": "agent-1",
            "timestamp": "2026-01-01T00:00:02Z",
        },
        {
            "event": "candidate_frozen",
            "run_id": "run-dashboard",
            "agent_id": "agent-1",
            "candidate_id": "candidate-1",
            "timestamp": "2026-01-01T00:00:03Z",
        },
        {
            "event": "prefilter_finished",
            "run_id": "run-dashboard",
            "agent_id": "agent-1",
            "candidate_id": "candidate-1",
            "timestamp": "2026-01-01T00:00:04Z",
            "patch_hash": "hash-1",
            "accepted": True,
            **({"patch_path": str(patch_path)} if patch_path else {}),
        },
        {
            "event": "agent_finished",
            "run_id": "run-dashboard",
            "agent_id": "agent-1",
            "timestamp": "2026-01-01T00:00:05Z",
            "status": "submitted",
        },
    ]
    if terminal:
        events.append(
            {
                "event": "run_finished",
                "run_id": "run-dashboard",
                "agent_id": "scheduler",
                "timestamp": "2026-01-01T00:00:06Z",
                "state": "completed",
            }
        )
    for event in events:
        db.append_event(event)


def test_dashboard_is_loopback_only_and_exposes_run_snapshot(tmp_path) -> None:
    db = RaceDB(tmp_path / "racegreen.db")
    _seed_run(db)
    server, thread = _start(db, tmp_path)
    try:
        response, body = _get(server, "/api/runs/run-dashboard")
        payload = json.loads(body)
        assert response.status == 200
        assert payload["run"]["run_id"] == "run-dashboard"
        assert payload["run"]["state"] == "completed"
        assert payload["run"]["base_commit"] == "abc123"
        assert payload["agents"][0]["status"] == "submitted"
        assert payload["candidates"][0]["state"] == "prefilter_passed"
        assert payload["counts"] == {"agents": 1, "candidates": 1, "events": 7}
        assert payload["last_sequence"] == 7

        with pytest.raises(ValueError):
            create_server(db, host="0.0.0.0", port=0)
    finally:
        _stop(server, thread)


def test_dashboard_exposes_events_filters_and_winner_state(tmp_path) -> None:
    db = RaceDB(tmp_path / "racegreen.db")
    _seed_run(db, terminal=False)
    assert db.try_set_winner(
        "run-dashboard",
        "candidate-1",
        "hash-1",
        "abc123",
        {"complete_full_suite": True, "argv": ["pytest"]},
    )
    db.append_event(
        {
            "event": "run_finished",
            "run_id": "run-dashboard",
            "agent_id": "scheduler",
            "timestamp": "2026-01-01T00:00:07Z",
            "state": "completed",
        }
    )
    server, thread = _start(db, tmp_path)
    try:
        response, body = _get(server, "/api/runs/run-dashboard/events")
        events = json.loads(body)
        assert response.status == 200
        assert [event["sequence"] for event in events] == list(range(1, 9))

        response, body = _get(
            server, "/api/runs/run-dashboard/events?type=agent_started,agent_finished"
        )
        filtered = json.loads(body)
        assert response.status == 200
        assert [event["event"] for event in filtered] == [
            "agent_started",
            "agent_finished",
        ]

        response, body = _get(
            server, "/api/runs/run-dashboard/events?limit=2&latest=1"
        )
        latest = json.loads(body)
        assert response.status == 200
        assert [event["sequence"] for event in latest] == [7, 8]

        response, body = _get(server, "/api/runs/run-dashboard/events?before=3&limit=2")
        before = json.loads(body)
        assert response.status == 200
        assert [event["sequence"] for event in before] == [1, 2]

        response, body = _get(server, "/api/runs/run-dashboard")
        snapshot = json.loads(body)
        assert response.status == 200
        assert snapshot["winner"]["candidate_id"] == "candidate-1"
        assert snapshot["winner"]["evidence_complete"] is True
        assert snapshot["candidates"][0]["state"] == "won"

        response, body = _get(server, "/api/overview")
        overview = json.loads(body)
        assert response.status == 200
        assert overview["runs"]["completed"] == 1
        assert overview["candidates"]["by_state"]["won"] == 1
        assert overview["winners"] == {"total": 1, "full_suite_confirmed": 1}
    finally:
        _stop(server, thread)


def test_dashboard_help_page_and_assets_are_served(tmp_path) -> None:
    db = RaceDB(tmp_path / "racegreen.db")
    server, thread = _start(db, tmp_path)
    try:
        response, body = _get(server, "/")
        page = body.decode("utf-8")
        assert response.status == 200
        assert "Help &amp; commands" in page
        assert "racegreen provider-test" in page
        assert "racegreen db verify" in page
        assert 'data-tab="compare"' in page
        assert 'data-view="provider"' not in page
        assert "<script>" not in page
        assert 'style="' not in page
        assert response.getheader("Content-Security-Policy")
        assert response.getheader("X-Frame-Options") == "DENY"

        response, body = _get(server, "/assets/app.js")
        assert response.status == 200
        assert b"EventSource" in body
        assert b"renderCompare" in body
        assert response.getheader("X-Content-Type-Options") == "nosniff"

        response, body = _get(server, "/assets/app.css")
        assert response.status == 200
        assert b"--bg" in body
    finally:
        _stop(server, thread)


def test_dashboard_rejects_foreign_hosts_origins_and_post(tmp_path) -> None:
    db = RaceDB(tmp_path / "racegreen.db")
    server, thread = _start(db, tmp_path)
    try:
        response, body = _get(server, "/api/overview", {"Host": "evil.example"})
        assert response.status == 403
        assert b"loopback host" in body

        response, body = _get(server, "/api/overview", {"Origin": "https://evil.example"})
        assert response.status == 403
        assert b"cross-origin" in body

        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("POST", "/api/overview")
        response = connection.getresponse()
        assert response.status == 405
        assert b"read-only" in response.read()
        connection.close()
    finally:
        _stop(server, thread)


def test_dashboard_serves_only_authorized_candidate_patches(tmp_path) -> None:
    artifacts_root = tmp_path / "runs"
    candidate_dir = artifacts_root / "run-dashboard" / "artifacts" / "candidates"
    candidate_dir.mkdir(parents=True)
    patch = candidate_dir / "candidate-1.patch"
    patch.write_text("--- a/example\n+++ b/example\n", encoding="utf-8")
    db = RaceDB(tmp_path / "racegreen.db")
    _seed_run(db, patch)
    server, thread = _start(db, tmp_path)
    try:
        response, body = _get(
            server, "/api/runs/run-dashboard/candidates/candidate-1/patch"
        )
        assert response.status == 200
        assert body == patch.read_bytes()
        assert response.getheader("Content-Type") == "text/plain; charset=utf-8"

        response, body = _get(
            server, "/api/runs/run-dashboard/candidates/missing/patch"
        )
        assert response.status == 404
        assert b"unknown run" in body or b"unknown candidate" in body
    finally:
        _stop(server, thread)

    outside = tmp_path / "outside.patch"
    outside.write_text("secret", encoding="utf-8")
    db2 = RaceDB(tmp_path / "racegreen2.db")
    _seed_run(db2, outside)
    server, thread = _start(db2, tmp_path)
    try:
        response, body = _get(
            server, "/api/runs/run-dashboard/candidates/candidate-1/patch"
        )
        assert response.status == 404
        assert b"unavailable" in body
        assert b"secret" not in body
    finally:
        _stop(server, thread)

    db3 = RaceDB(tmp_path / "racegreen3.db")
    db3.append_event(
        {
            "event": "run_started",
            "run_id": "../escape",
            "agent_id": "scheduler",
            "timestamp": "2026-01-01T00:00:00Z",
        }
    )
    db3.append_event(
        {
            "event": "candidate_submitted",
            "run_id": "../escape",
            "agent_id": "agent-1",
            "candidate_id": "candidate-1",
            "timestamp": "2026-01-01T00:00:01Z",
            "patch_hash": "hash-1",
            "patch_path": str(outside),
        }
    )
    server, thread = _start(db3, tmp_path)
    try:
        response, body = _get(
            server, "/api/runs/..%2Fescape/candidates/candidate-1/patch"
        )
        assert response.status == 404
        assert b"unavailable" in body
        assert b"secret" not in body
    finally:
        _stop(server, thread)


def test_dashboard_streams_persisted_events(tmp_path) -> None:
    db = RaceDB(tmp_path / "racegreen.db")
    _seed_run(db)
    server, thread = _start(db, tmp_path)
    sock = socket.create_connection(("127.0.0.1", server.server_port), timeout=5)
    try:
        request = (
            f"GET /api/runs/run-dashboard/stream?after=0 HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{server.server_port}\r\n"
            "Accept: text/event-stream\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
        headers, _, body = raw.partition(b"\r\n\r\n")
        assert b"HTTP/1.1 200 OK" in headers
        assert b"text/event-stream" in headers
        while b"data:" not in body:
            chunk = sock.recv(4096)
            if not chunk:
                break
            body += chunk
        assert b'"event":"run_configured"' in body

        db.append_event(
            {
                "event": "agent_status",
                "run_id": "run-dashboard",
                "agent_id": "agent-1",
                "timestamp": "2026-01-01T00:00:07Z",
                "status": "working",
            }
        )
        received = b""
        while b"agent_status" not in received:
            chunk = sock.recv(4096)
            if not chunk:
                break
            received += chunk
        assert b'"event":"agent_status"' in received
    finally:
        sock.close()
        _stop(server, thread)
