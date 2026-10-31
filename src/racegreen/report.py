"""Generate a self-contained, inert HTML report from SQLite trace data."""

from __future__ import annotations

import html
import json
from pathlib import Path

from .storage.db import RaceDB
from .storage.security import safe_terminal_text


def write_report(db: RaceDB, run_id: str, output: Path) -> Path:
    snapshot = db.run_snapshot(run_id)
    events = db.events(run_id, limit=10000)
    payload = html.escape(json.dumps(snapshot, sort_keys=True, indent=2))
    event_text = html.escape(
        "\n".join(safe_terminal_text(json.dumps(event, sort_keys=True)) for event in events)
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<meta name="referrer" content="no-referrer"><title>RaceGreen {html.escape(run_id)}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem}}
pre{{white-space:pre-wrap;overflow:auto;background:#f4f4f4;padding:1rem}}</style></head>
<body><h1>RaceGreen run</h1><p>{html.escape(run_id)}</p>
<h2>Snapshot</h2><pre>{payload}</pre><h2>Events</h2><pre>{event_text}</pre></body></html>
"""
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return output
