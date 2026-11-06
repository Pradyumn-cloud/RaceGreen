from __future__ import annotations

import json
import threading
from http.client import HTTPConnection

from racegreen.dashboard import create_server
from racegreen.storage.db import RaceDB


def test_dashboard_is_loopback_only_and_exposes_run_snapshot(tmp_path) -> None:
    db = RaceDB(tmp_path / "racegreen.db")
    db.append_event(
        {
            "event": "run_started",
            "run_id": "run-dashboard",
            "timestamp": "2026-01-01T00:00:00Z",
        }
    )
    server = create_server(db, port=0)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/api/runs/run-dashboard")
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["run"]["run_id"] == "run-dashboard"
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_dashboard_exposes_events_and_winner_state(tmp_path) -> None:
    db = RaceDB(tmp_path / "racegreen.db")
    db.append_event(
        {
            "event": "run_started",
            "run_id": "run-events",
            "timestamp": "2026-01-01T00:00:00Z",
        }
    )
    db.append_event(
        {
            "event": "agent_started",
            "run_id": "run-events",
            "agent_id": "agent-1",
            "timestamp": "2026-01-01T00:00:01Z",
        }
    )
    server = create_server(db, port=0)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/api/runs/run-events/events")
        response = connection.getresponse()
        events = json.loads(response.read())
        assert response.status == 200
        assert len(events) == 2
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
