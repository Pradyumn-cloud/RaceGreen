"""Loopback-only dashboard for reviewing persisted RaceGreen runs."""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from .storage.db import RaceDB

_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RaceGreen Dashboard</title>
<style>
:root{color-scheme:dark;font-family:Inter,system-ui,sans-serif}
body{background:#10141c;color:#e8edf5;margin:0}
header{background:#171e2b;border-bottom:1px solid #2b3547;padding:20px 28px}
main{display:grid;grid-template-columns:280px 1fr;gap:20px;max-width:1400px;margin:20px auto;padding:0 20px}
section,aside{background:#171e2b;border:1px solid #2b3547;border-radius:12px;padding:18px}
h1,h2{margin:0 0 8px}h1{font-size:22px}h2{font-size:18px}
p{color:#aab6c8}.run{display:block;width:100%;text-align:left;background:transparent;
border:0;border-bottom:1px solid #2b3547;color:#e8edf5;padding:12px 4px;cursor:pointer}
.run:hover,.run.selected{background:#202b3d}.state{color:#7ee2a8;font-size:12px}
pre{white-space:pre-wrap;overflow:auto;background:#0c1017;border-radius:8px;padding:14px;
max-height:520px;color:#cbd5e1}button{background:#4f8cff;color:white;border:0;border-radius:7px;
padding:9px 13px;cursor:pointer}button:disabled{opacity:.5;cursor:default}
.muted{color:#8997aa;font-size:13px}
</style></head>
<body><header><h1>RaceGreen Dashboard</h1>
<p>Review evidence from persisted runs. Agent output is displayed as untrusted text.</p></header>
<main><aside><h2>Runs</h2><div id="runs">Loading...</div></aside>
<section><div id="detail"><h2>Select a run</h2><p>Choose a run to inspect its state, candidates, and event timeline.</p></div></section>
</main>
<script>
const runs = document.getElementById("runs"), detail = document.getElementById("detail");
function text(value){return value == null ? "" : String(value)}
async function loadRuns(){
  const response=await fetch("/api/runs"); const data=await response.json(); runs.replaceChildren();
  if(!data.length){runs.textContent="No runs recorded yet.";return}
  data.forEach(run=>{const b=document.createElement("button");b.className="run";
    const id=document.createElement("strong");id.textContent=text(run.run_id);b.appendChild(id);
    b.appendChild(document.createElement("br"));const state=document.createElement("span");
    state.className="state";state.textContent=text(run.state);b.appendChild(state);
    b.onclick=()=>loadRun(run.run_id,b);runs.appendChild(b)})
}
async function loadRun(id,button){
  document.querySelectorAll(".run").forEach(item=>item.classList.remove("selected"));button.classList.add("selected");
  const response=await fetch("/api/runs/"+encodeURIComponent(id));const data=await response.json();
  detail.replaceChildren();const title=document.createElement("h2");title.textContent="Run "+id;detail.appendChild(title);
  const summary=document.createElement("p");summary.textContent="State: "+text(data.run.state)+" | Base: "+text(data.run.base_commit);detail.appendChild(summary);
  const heading=document.createElement("h2");heading.textContent="Evidence";detail.appendChild(heading);
  const pre=document.createElement("pre");pre.textContent=JSON.stringify(data,null,2);detail.appendChild(pre);
}
loadRuns().catch(error=>{runs.textContent="Unable to load runs: "+error});
</script></body></html>"""


def create_server(db: RaceDB, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("dashboard must bind to loopback")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: object, content_type: str = "application/json") -> None:
            body = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self._send(200, _HTML, "text/html")
            elif path == "/api/runs":
                self._send(200, db.list_runs())
            elif path.startswith("/api/runs/"):
                run_id = unquote(path.removeprefix("/api/runs/"))
                try:
                    self._send(200, db.run_snapshot(run_id))
                except KeyError:
                    self._send(404, {"error": "unknown run"})
            else:
                self._send(404, {"error": "not found"})

        def log_message(self, format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def serve(db: RaceDB, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    server = create_server(db, host, port)
    url = f"http://{host}:{server.server_port}/"
    if open_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    print(f"RaceGreen dashboard: {url}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
