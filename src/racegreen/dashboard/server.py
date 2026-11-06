"""Loopback-only HTTP and SSE server for the RaceGreen dashboard."""

from __future__ import annotations

import json
import socket
import sqlite3
import threading
import time
import webbrowser
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ..storage.db import RaceDB
from ..storage.security import authorize_artifact

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_MAX_SSE_CLIENTS = 8
_MAX_PATCH_BYTES = 2 * 1024 * 1024
_CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "connect-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)


@lru_cache(maxsize=8)
def _asset(name: str) -> bytes:
    return files("racegreen.dashboard").joinpath("assets", name).read_bytes()


def _hostname(value: str) -> str | None:
    try:
        parsed = urlparse(f"//{value}")
    except ValueError:
        return None
    return parsed.hostname


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        db: RaceDB,
        artifacts_root: Path,
    ) -> None:
        self.db = db
        self.artifacts_root = artifacts_root
        self.stop_event = threading.Event()
        self._sse_lock = threading.Lock()
        self._sse_clients = 0
        super().__init__(server_address, handler)

    def try_acquire_sse(self) -> bool:
        with self._sse_lock:
            if self._sse_clients >= _MAX_SSE_CLIENTS:
                return False
            self._sse_clients += 1
            return True

    def release_sse(self) -> None:
        with self._sse_lock:
            self._sse_clients = max(0, self._sse_clients - 1)

    def shutdown(self) -> None:
        self.stop_event.set()
        super().shutdown()

    def server_close(self) -> None:
        self.stop_event.set()
        super().server_close()


class DashboardHTTPServerV6(DashboardHTTPServer):
    address_family = socket.AF_INET6


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def dashboard(self) -> DashboardHTTPServer:
        return self.server  # type: ignore[return-value]

    def _allowed(self) -> bool:
        host = self.headers.get("Host", "")
        if not host or _hostname(host) not in _LOOPBACK_HOSTS:
            self._send_json(403, {"error": "loopback host required"})
            return False
        origin = self.headers.get("Origin")
        if origin:
            try:
                parsed = urlparse(origin)
            except ValueError:
                self._send_json(403, {"error": "invalid origin"})
                return False
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.hostname not in _LOOPBACK_HOSTS
                or (
                    parsed.port is not None
                    and parsed.port != self.dashboard.server_port
                )
            ):
                self._send_json(403, {"error": "cross-origin request rejected"})
                return False
        return True

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Content-Security-Policy", _CSP)

    def _send(
        self,
        status: int,
        payload: object,
        content_type: str = "application/json",
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        if isinstance(payload, bytes):
            body = payload
        elif isinstance(payload, str):
            body = payload.encode("utf-8")
        else:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
        if "charset=" not in content_type:
            content_type = f"{content_type}; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: object) -> None:
        self._send(status, payload, "application/json")

    def _send_asset(self, name: str, content_type: str) -> None:
        try:
            payload = _asset(name)
        except (FileNotFoundError, OSError):
            self._send_json(404, {"error": "asset not found"})
            return
        self._send(200, payload, content_type)

    def do_GET(self) -> None:
        if not self._allowed():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path in {"/", "/index.html"}:
                self._send_asset("index.html", "text/html")
            elif path == "/assets/app.css":
                self._send_asset("app.css", "text/css")
            elif path == "/assets/app.js":
                self._send_asset("app.js", "text/javascript")
            elif path == "/api/health":
                self._send_json(
                    200,
                    {
                        "status": "ok",
                        "database": str(self.dashboard.db.path),
                        "artifacts_root": str(self.dashboard.artifacts_root),
                    },
                )
            elif path == "/api/overview":
                self._send_json(200, self.dashboard.db.overview())
            elif path == "/api/runs":
                limit = _int_query(query, "limit", default=100, minimum=1, maximum=1000)
                self._send_json(200, self.dashboard.db.list_runs(limit=limit))
            elif path.startswith("/api/runs/"):
                self._handle_run(path, query)
            else:
                self._send_json(404, {"error": "not found"})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except KeyError:
            self._send_json(404, {"error": "unknown run"})
        except sqlite3.Error:
            self._send_json(500, {"error": "database unavailable"})

    def _handle_run(self, path: str, query: dict[str, list[str]]) -> None:
        rest = path[len("/api/runs/") :].strip("/")
        if not rest:
            self._send_json(404, {"error": "unknown run"})
            return

        if rest.endswith("/stream"):
            run_id = unquote(rest[: -len("/stream")])
            self._stream(run_id, _int_query(query, "after", default=0, minimum=0))
            return
        if rest.endswith("/events"):
            run_id = unquote(rest[: -len("/events")])
            event_types = _csv_query(query, "type")
            self._send_json(
                200,
                self.dashboard.db.events(
                    run_id,
                    after=_int_query(query, "after", default=0, minimum=0),
                    before=_optional_int_query(query, "before", minimum=1),
                    limit=_int_query(query, "limit", default=300, minimum=1, maximum=1000),
                    latest=_bool_query(query, "latest"),
                    event_types=event_types or None,
                    agent_id=_first_query(query, "agent") or None,
                    candidate_id=_first_query(query, "candidate") or None,
                ),
            )
            return
        if "/candidates/" in rest and rest.endswith("/patch"):
            run_part, candidate_part = rest.rsplit("/candidates/", 1)
            run_id = unquote(run_part)
            candidate_id = unquote(candidate_part[: -len("/patch")])
            self._send_patch(run_id, candidate_id)
            return

        run_id = unquote(rest)
        snapshot = self.dashboard.db.run_snapshot(run_id)
        self._send_json(200, snapshot)

    def _run_root(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
            raise ValueError("invalid run id")
        root = self.dashboard.artifacts_root.resolve()
        candidate = (root / run_id).resolve()
        if candidate == root or root not in candidate.parents:
            raise ValueError("run root escapes the artifacts root")
        return candidate

    def _send_patch(self, run_id: str, candidate_id: str) -> None:
        try:
            patch_path = self.dashboard.db.candidate_patch_path(run_id, candidate_id)
        except sqlite3.Error:
            self._send_json(500, {"error": "database unavailable"})
            return
        if not patch_path:
            self._send_json(404, {"error": "patch path not recorded"})
            return
        try:
            run_root = self._run_root(run_id)
            resolved = authorize_artifact(run_root, Path(patch_path))
        except (OSError, ValueError):
            self._send_json(404, {"error": "patch artifact unavailable"})
            return
        try:
            size = resolved.stat().st_size
        except OSError:
            self._send_json(404, {"error": "patch artifact unavailable"})
            return
        if size > _MAX_PATCH_BYTES:
            self._send_json(413, {"error": "patch artifact too large"})
            return
        try:
            payload = resolved.read_bytes()
        except OSError:
            self._send_json(404, {"error": "patch artifact unavailable"})
            return
        self._send(
            200,
            payload,
            "text/plain; charset=utf-8",
            headers={"Content-Disposition": 'inline; filename="candidate.patch"'},
        )

    def _stream(self, run_id: str, after: int) -> None:
        try:
            self.dashboard.db.run_snapshot(run_id)
        except KeyError:
            self._send_json(404, {"error": "unknown run"})
            return
        except sqlite3.Error:
            self._send_json(500, {"error": "database unavailable"})
            return
        if not self.dashboard.try_acquire_sse():
            self._send_json(429, {"error": "too many live dashboard connections"})
            return

        last = after
        last_event_id = self.headers.get("Last-Event-ID")
        if last_event_id:
            try:
                last = max(last, int(last_event_id))
            except ValueError:
                pass
        last_heartbeat = time.monotonic()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Transfer-Encoding", "chunked")
            self._security_headers()
            self.end_headers()
            while not self.dashboard.stop_event.is_set():
                events = self.dashboard.db.events(run_id, after=last, limit=500)
                for event in events:
                    sequence = int(event.get("sequence") or 0)
                    if sequence <= last:
                        continue
                    data = json.dumps(event, sort_keys=True, separators=(",", ":"))
                    self._write_chunk(f"id: {sequence}\ndata: {data}\n\n")
                    last = sequence
                if time.monotonic() - last_heartbeat >= 15:
                    self._write_chunk(": keepalive\n\n")
                    last_heartbeat = time.monotonic()
                if not events:
                    self.dashboard.stop_event.wait(0.75)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.dashboard.release_sse()
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            self.close_connection = True

    def _write_chunk(self, value: str) -> None:
        payload = value.encode("utf-8")
        self.wfile.write(f"{len(payload):X}\r\n".encode("ascii") + payload + b"\r\n")
        self.wfile.flush()

    def do_POST(self) -> None:
        if not self._allowed():
            return
        self._send_json(405, {"error": "dashboard is read-only"})

    def log_message(self, format: str, *args: object) -> None:
        return


def create_server(
    db: RaceDB,
    host: str = "127.0.0.1",
    port: int = 8765,
    artifacts_root: Path | None = None,
) -> DashboardHTTPServer:
    if host not in _LOOPBACK_HOSTS:
        raise ValueError("dashboard must bind to loopback")
    root = (artifacts_root or Path.home() / ".racegreen" / "runs").expanduser().resolve()
    server_class = DashboardHTTPServerV6 if ":" in host else DashboardHTTPServer
    return server_class(
        (host, port),
        Handler,
        db=db,
        artifacts_root=root,
    )


def serve(
    db: RaceDB,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
    artifacts_root: Path | None = None,
) -> None:
    server = create_server(db, host, port, artifacts_root)
    display_host = f"[{host}]" if ":" in host else host
    url = f"http://{display_host}:{server.server_port}/"
    if open_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    print(f"RaceGreen dashboard: {url}")
    try:
        server.serve_forever()
    finally:
        server.stop_event.set()
        server.server_close()


def _first_query(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    return values[0] if values else ""


def _csv_query(query: dict[str, list[str]], key: str) -> list[str]:
    values: list[str] = []
    for item in query.get(key, []):
        values.extend(part for part in item.split(",") if part)
    return values


def _int_query(
    query: dict[str, list[str]],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    value = _first_query(query, key)
    if not value:
        return default
    parsed = int(value)
    if parsed < minimum or (maximum is not None and parsed > maximum):
        raise ValueError(f"{key} is out of range")
    return parsed


def _optional_int_query(
    query: dict[str, list[str]], key: str, *, minimum: int
) -> int | None:
    value = _first_query(query, key)
    if not value:
        return None
    parsed = int(value)
    if parsed < minimum:
        raise ValueError(f"{key} is out of range")
    return parsed


def _bool_query(query: dict[str, list[str]], key: str) -> bool:
    return _first_query(query, key).strip().lower() in {"1", "true", "yes", "on"}
