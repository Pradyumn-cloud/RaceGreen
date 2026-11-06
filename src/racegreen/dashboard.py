"""Loopback-only dashboard for reviewing persisted RaceGreen runs."""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .storage.db import RaceDB

_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RaceGreen // Control Plane</title>
<style>
:root{color-scheme:dark;--bg:#090d14;--panel:#111722;--panel2:#151c29;--line:#253044;
--text:#e7edf7;--muted:#8290a5;--cyan:#28d7ff;--green:#32e7a5;--amber:#f5c46b;
--red:#ff6e86;--violet:#a88bff;--shadow:0 12px 35px #0005;font:13px/1.45
ui-monospace,SFMono-Regular,Consolas,monospace}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 75% -20%,#19304b55,transparent 36%),var(--bg);color:var(--text)}
button,input{font:inherit}button{cursor:pointer}button:focus-visible,input:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}
.shell{display:grid;grid-template-columns:224px 1fr;min-height:100vh}.sidebar{background:#0b1019;border-right:1px solid var(--line);padding:18px 12px;display:flex;flex-direction:column}
.brand{display:flex;align-items:center;gap:10px;font-weight:800;letter-spacing:.08em;margin:4px 8px 28px}.brand-mark{color:var(--cyan);font-size:21px}
.eyebrow{font-size:10px;color:var(--muted);letter-spacing:.14em;text-transform:uppercase}.nav{display:grid;gap:5px;margin-top:12px}
.nav button{background:transparent;border:0;color:var(--muted);padding:11px 12px;text-align:left;border-radius:6px;display:flex;gap:10px;align-items:center}
.nav button:hover,.nav button.active{background:#1b2636;color:var(--text)}.nav button.active{box-shadow:inset 2px 0 var(--cyan)}
.side-status{margin-top:auto;border:1px solid var(--line);padding:12px;border-radius:7px;background:#0e151f}.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 10px var(--green);margin-right:7px}
.content{min-width:0}.topbar{height:64px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:14px;padding:0 28px;background:#0d131eaa;backdrop-filter:blur(8px)}
.topbar h1{font-size:15px;margin:0;letter-spacing:.06em}.topbar-spacer{flex:1}.connection{color:var(--green);font-size:11px}.top-action,.primary,.ghost{border:1px solid var(--line);border-radius:5px;padding:8px 12px;background:#121b29;color:var(--text)}
.primary{background:#0d708b;border-color:#1bbbd7;color:#eaffff}.top-action:hover,.ghost:hover{border-color:var(--cyan)}.primary:hover{background:#1297b1}
.page{max-width:1500px;margin:0 auto;padding:24px 28px 42px}.page-head{display:flex;align-items:end;gap:20px;margin-bottom:22px}.page-head h2{font-size:22px;margin:3px 0}.page-head p{color:var(--muted);margin:0}.head-actions{margin-left:auto;display:flex;gap:8px}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:14px}.metric,.panel{background:linear-gradient(145deg,#141c29,#101621);border:1px solid var(--line);box-shadow:var(--shadow);border-radius:7px}
.metric{padding:15px 16px;position:relative;overflow:hidden}.metric:after{content:"";position:absolute;left:0;bottom:0;height:2px;width:62%;background:var(--cyan)}.metric.green:after{background:var(--green)}.metric.violet:after{background:var(--violet)}.metric.amber:after{background:var(--amber)}
.metric-label{color:var(--muted);font-size:10px;letter-spacing:.13em;text-transform:uppercase}.metric-value{font-size:25px;margin:7px 0 3px}.metric-note{font-size:11px;color:var(--muted)}
.workspace{display:grid;grid-template-columns:minmax(330px,.86fr) minmax(500px,1.5fr);gap:14px}.panel{min-width:0}.panel-head{padding:14px 16px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px}.panel-head h3{margin:0;font-size:12px;letter-spacing:.1em;text-transform:uppercase}.panel-head .right{margin-left:auto;color:var(--muted);font-size:11px}
.run-tools{padding:11px 12px;border-bottom:1px solid var(--line);display:flex;gap:8px}.search{width:100%;background:#0b111b;border:1px solid var(--line);border-radius:5px;color:var(--text);padding:8px 10px}.select{background:#0b111b;color:var(--muted);border:1px solid var(--line);border-radius:5px;padding:7px}
.run-list{max-height:590px;overflow:auto}.run{display:block;width:100%;border:0;border-bottom:1px solid #202a3a;background:transparent;color:var(--text);padding:13px 15px;text-align:left}.run:hover,.run.selected{background:#182334}.run-line{display:flex;gap:8px;align-items:center}.run-id{font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.run-meta{display:flex;justify-content:space-between;color:var(--muted);font-size:11px;margin-top:5px}.badge{display:inline-flex;align-items:center;border:1px solid currentColor;border-radius:3px;padding:2px 5px;font-size:9px;letter-spacing:.08em;text-transform:uppercase}.badge.good{color:var(--green)}.badge.warn{color:var(--amber)}.badge.bad{color:var(--red)}.badge.info{color:var(--cyan)}
.detail{display:grid;grid-template-rows:auto auto 1fr}.run-title{font-size:16px;font-weight:700}.run-title small{font-size:11px;color:var(--muted);font-weight:400}.detail-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;padding:14px 16px;border-bottom:1px solid var(--line)}.stat{background:#0c121c;border:1px solid #202a3a;border-radius:5px;padding:10px}.stat b{display:block;font-size:17px}.stat span{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.08em}
.tabs{display:flex;gap:2px;border-bottom:1px solid var(--line);padding:0 10px}.tab{border:0;border-bottom:2px solid transparent;background:transparent;color:var(--muted);padding:11px 10px;font-size:11px}.tab.active{color:var(--cyan);border-color:var(--cyan)}.tab-body{padding:14px 16px;min-height:300px;max-height:540px;overflow:auto}
.candidate{border:1px solid var(--line);border-radius:5px;padding:12px;margin-bottom:9px;background:#0d141f}.candidate-head{display:flex;justify-content:space-between;align-items:center}.candidate-id{font-weight:700}.candidate-meta{color:var(--muted);font-size:11px;margin-top:7px}.progress{height:4px;background:#263145;margin-top:10px;border-radius:4px;overflow:hidden}.progress i{display:block;height:100%;background:var(--green);width:100%}
.event{display:grid;grid-template-columns:86px 120px 1fr;gap:10px;border-bottom:1px solid #202a3a;padding:9px 0;font-size:11px}.event-time{color:var(--muted)}.event-type{color:var(--cyan)}.event-data{color:#c6d0df;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.empty{color:var(--muted);padding:30px;text-align:center}.code{background:#090e16;border:1px solid var(--line);border-radius:5px;padding:12px;color:#c5d1e2;white-space:pre-wrap;overflow:auto}
.modal-backdrop{position:fixed;inset:0;background:#0009;display:grid;place-items:center;z-index:5}.modal{width:min(620px,calc(100% - 32px));background:var(--panel2);border:1px solid var(--line);box-shadow:var(--shadow);border-radius:8px;padding:20px}.modal h3{margin-top:0}.modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}.hidden{display:none!important}
.help-page{display:none}.help-page.visible{display:block}.help-intro{max-width:760px;color:var(--muted);margin-bottom:18px}.help-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.help-card{background:linear-gradient(145deg,#141c29,#101621);border:1px solid var(--line);border-radius:7px;padding:16px}.help-card h3{margin:0 0 7px;font-size:13px}.help-card p{color:var(--muted);font-size:12px;margin:0 0 12px}.command{background:#090e16;border:1px solid #29364c;border-radius:5px;padding:11px;color:#b9eefa;white-space:pre-wrap;word-break:break-word;font-size:11px}.command-row{display:flex;gap:8px;align-items:start}.command-row .command{flex:1}.copy-small{border:1px solid var(--line);background:#172337;color:var(--text);border-radius:4px;padding:7px 9px;font-size:10px}.help-search{max-width:420px;margin-bottom:14px}
@media(max-width:1000px){.shell{grid-template-columns:70px 1fr}.sidebar{padding:18px 8px}.brand span,.nav button span,.side-status{display:none}.brand{justify-content:center}.nav button{justify-content:center}.metrics{grid-template-columns:repeat(2,1fr)}.workspace{grid-template-columns:1fr}.run-list{max-height:300px}}
@media(max-width:600px){.topbar{padding:0 15px}.page{padding:18px 12px}.metrics{grid-template-columns:1fr 1fr}.detail-grid{grid-template-columns:1fr 1fr}.head-actions{display:none}.event{grid-template-columns:70px 1fr}.event-data{grid-column:2}.event-type{grid-column:2}}
</style></head>
<body><div class="shell">
<aside class="sidebar"><div class="brand"><span class="brand-mark">◆</span><span>RACEGREEN</span></div>
<div class="eyebrow">Workspaces &amp; workflows</div><nav class="nav">
<button class="active" data-view="overview"><span>▦</span><span>Overview / Runs</span></button>
<button data-view="help"><span>?</span><span>Help &amp; Commands</span></button></nav>
<div class="side-status"><div><i class="dot"></i><b>LOCAL DAEMON</b></div><div class="metric-note" style="margin-top:8px">SQLite trace online</div></div></aside>
<section class="content"><header class="topbar"><h1 id="viewTitle">OVERVIEW / RUNS</h1><div class="topbar-spacer"></div><span class="connection"><i class="dot"></i>LIVE</span><button class="top-action" id="refresh">↻ REFRESH</button><button class="primary" id="newRun">＋ NEW RUN</button></header>
<main class="page"><div id="overviewPage"><div class="page-head"><div><div class="eyebrow">RaceGreen control plane</div><h2>Agent operations</h2><p>Inspect candidates, verification evidence, and run activity.</p></div><div class="head-actions"><button class="ghost" id="copyCommand">▣ COPY CLI</button><button class="ghost" id="toggleAuto">AUTO-REFRESH: ON</button></div></div>
<div class="metrics"><div class="metric"><div class="metric-label">Total runs</div><div class="metric-value" id="mTotal">—</div><div class="metric-note">persisted in SQLite</div></div><div class="metric green"><div class="metric-label">Completed</div><div class="metric-value" id="mCompleted">—</div><div class="metric-note" id="mCompletionNote">full evidence required</div></div><div class="metric violet"><div class="metric-label">Candidates</div><div class="metric-value" id="mCandidates">—</div><div class="metric-note">across visible runs</div></div><div class="metric amber"><div class="metric-label">Event records</div><div class="metric-value" id="mEvents">—</div><div class="metric-note">trace source of truth</div></div></div>
<div class="workspace"><section class="panel"><div class="panel-head"><h3>Run queue</h3><span class="right" id="runCount">0 runs</span></div><div class="run-tools"><input id="runSearch" class="search" placeholder="Search run ID or state..." aria-label="Search runs"><select id="stateFilter" class="select"><option value="">ALL</option><option>COMPLETED</option><option>UNKNOWN</option><option>FAILED</option><option>CANCELLED</option></select></div><div class="run-list" id="runs"><div class="empty">Loading run queue...</div></div></section>
<section class="panel detail"><div class="panel-head"><div><div class="run-title" id="detailTitle">Select a run <small>to inspect its evidence</small></div><div class="metric-note" id="detailSub"></div></div><span class="right" id="detailStatus"></span></div><div class="detail-grid"><div class="stat"><span>Agents</span><b id="sAgents">—</b></div><div class="stat"><span>Candidates</span><b id="sCandidates">—</b></div><div class="stat"><span>Events</span><b id="sEvents">—</b></div><div class="stat"><span>Winner</span><b id="sWinner">—</b></div></div><div class="tabs"><button class="tab active" data-tab="candidates">CANDIDATES</button><button class="tab" data-tab="events">LIVE EVENT STREAM</button><button class="tab" data-tab="evidence">EVIDENCE</button></div><div class="tab-body" id="tabBody"><div class="empty">Choose a run from the queue.</div></div></section></div></main></section></div>
</div><div class="help-page" id="helpPage"><div class="page-head"><div><div class="eyebrow">Operator handbook</div><h2>Help &amp; commands</h2><p class="help-intro">Everything needed to configure a provider, run agents, inspect evidence, open this dashboard, and safely clean up. Copy a command, replace the values in angle brackets, and run it from the project directory.</p></div></div><input class="search help-search" id="helpSearch" placeholder="Search commands and workflows..." aria-label="Search help"><div class="help-grid" id="helpCards"></div></div></main></section></div>
<div class="modal-backdrop hidden" id="modal"><div class="modal"><h3>Start a new RaceGreen run</h3><p class="metric-note">The dashboard currently prepares a safe CLI command; execution remains explicit in your terminal.</p><div class="code" id="commandBox">uv run racegreen race . "Describe the task" --agents 2</div><div class="modal-actions"><button class="ghost" id="closeModal">CLOSE</button><button class="primary" id="modalCopy">COPY COMMAND</button></div></div></div>
<script>
const state={runs:[],selected:null,snapshot:null,tab:"candidates",auto:true};
const commands=[
["First setup","Create a provider profile without storing credentials.","uv run racegreen setup","Choose Ollama for a local model or a hosted provider. Existing RACEGREEN_* environment variables override the file."],
["Check readiness","Check Python, Git, provider configuration, and home directory.","uv run racegreen doctor","A local provider may work without an API key. Use provider-test for a real endpoint check."],
["Test provider","Check that the configured endpoint responds.","uv run racegreen provider-test","This does not send a model completion."],
["Run one agent","Run one bounded coding agent in an isolated worktree.","uv run racegreen agent-run . \"<task>\"","The source repository is not modified automatically."],
["Run a race","Run multiple agents against the same task.","uv run racegreen race . \"<task>\" --agents 2 --max-concurrency 2","A winner requires configured full-suite confirmation."],
["Inspect runs","List recorded runs and inspect one run.","uv run racegreen runs\nuv run racegreen run-show <RUN_ID>","Copy the run ID from the list, then inspect its candidates and events."],
["Open dashboard","Open this local operator console.","uv run racegreen dashboard --open","The server is loopback-only and the dashboard is read-only."],
["HTML report","Create a standalone report for one run.","uv run racegreen report <RUN_ID> --output racegreen-report.html","Open the generated file in a browser."],
["Events","List, export, or replay the event timeline.","uv run racegreen events list <RUN_ID>\nuv run racegreen events export <RUN_ID> --output events.jsonl\nuv run racegreen-replay show events.jsonl","Events are the persisted trace source of truth."],
["Verify database","Check SQLite integrity and event invariants.","uv run racegreen db verify","Run this after unusual interruptions or manual event ingestion."],
["Offline demo","Try the persistence and report flow without an API.","uv run racegreen demo","The demo does not measure agent quality."],
["Clean artifacts","List or remove only RaceGreen-owned run folders.","uv run racegreen clean\nuv run racegreen clean --run-id <RUN_ID> --yes","Review the list before removing a run."],
["Review and apply","Review a candidate patch before changing your repository.","git diff\n git apply --check <PATCH_PATH>\n git apply <PATCH_PATH>","The dashboard does not apply changes. Create a review branch first and verify the target is clean."]
];
const $=id=>document.getElementById(id), text=value=>value==null?"":String(value);
function badge(value){const s=text(value), cls=/pass|complete|won|success/i.test(s)?"good":/fail|reject|error/i.test(s)?"bad":/unknown|work|pending|cancel/i.test(s)?"warn":"info";return `<span class="badge ${cls}">${escapeHtml(s||"UNKNOWN")}</span>`}
function escapeHtml(value){return text(value).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function formatTime(value){if(!value)return "—";const d=new Date(value);return Number.isNaN(d.getTime())?text(value).slice(0,16):d.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"})}
async function get(path){const r=await fetch(path,{cache:"no-store"});if(!r.ok)throw new Error("Request failed: "+r.status);return r.json()}
async function loadRuns(){state.runs=await get("/api/runs");renderRuns();updateMetrics()}
function renderRuns(){const q=$("runSearch").value.toLowerCase(), f=$("stateFilter").value.toLowerCase();const visible=state.runs.filter(r=>(!q||JSON.stringify(r).toLowerCase().includes(q))&&(!f||text(r.state).toLowerCase()===f));$("runCount").textContent=visible.length+" runs";const root=$("runs");root.replaceChildren();if(!visible.length){root.innerHTML='<div class="empty">No matching runs.</div>';return}visible.forEach(run=>{const b=document.createElement("button");b.className="run"+(state.selected===run.run_id?" selected":"");b.innerHTML=`<div class="run-line"><span class="run-id">${escapeHtml(run.run_id)}</span>${badge(run.state)}</div><div class="run-meta"><span>${escapeHtml(run.base_commit||"base unavailable")}</span><span>${formatTime(run.updated_at||run.created_at)}</span></div>`;b.onclick=()=>loadRun(run.run_id);root.appendChild(b)})}
function updateMetrics(){let completed=0;state.runs.forEach(r=>{if(/complete|won/i.test(text(r.state)))completed++});$("mTotal").textContent=state.runs.length;$("mCompleted").textContent=completed;$("mCandidates").textContent="—";$("mEvents").textContent="—"}
async function loadRun(id){state.selected=id;state.snapshot=await get("/api/runs/"+encodeURIComponent(id));renderRuns();const d=state.snapshot,r=d.run||{};$("detailTitle").innerHTML=`Run ${escapeHtml(id)} <small>base ${escapeHtml(r.base_commit||"unavailable")}</small>`;$("detailSub").textContent="updated "+formatTime(r.updated_at||r.created_at);$("detailStatus").innerHTML=badge(r.state);$("sAgents").textContent=(d.agents||[]).length;$("sCandidates").textContent=(d.candidates||[]).length;$("sEvents").textContent=text(d.event_count);$("sWinner").textContent=d.winner?"CONFIRMED":"—";$("mCandidates").textContent=state.runs.reduce((n,x)=>n+(x.run_id===id?(d.candidates||[]).length:0),0);$("mEvents").textContent=d.event_count;renderTab()}
function renderTab(){const d=state.snapshot;if(!d){$("tabBody").innerHTML='<div class="empty">Choose a run from the queue.</div>';return}if(state.tab==="candidates"){const items=d.candidates||[];$("tabBody").innerHTML=items.length?items.map(c=>`<div class="candidate"><div class="candidate-head"><span class="candidate-id">${escapeHtml(c.candidate_id)}</span>${badge(c.state)}</div><div class="candidate-meta">agent ${escapeHtml(c.agent_id||"unknown")} · patch ${escapeHtml(c.patch_hash||"not recorded")}</div><div class="progress"><i></i></div></div>`).join(""):'<div class="empty">No candidate snapshots recorded.</div>'}else if(state.tab==="events"){loadEvents()}else{$("tabBody").innerHTML=`<div class="code">${escapeHtml(JSON.stringify({run:d.run,agents:d.agents,candidates:d.candidates,winner:d.winner||null},null,2))}</div>`}}
async function loadEvents(){if(!state.selected)return;try{const events=await get("/api/runs/"+encodeURIComponent(state.selected)+"/events");$("tabBody").innerHTML=events.length?events.map(e=>`<div class="event"><span class="event-time">${escapeHtml(formatTime(e.timestamp))}</span><span class="event-type">${escapeHtml(e.event||"event")}</span><span class="event-data">${escapeHtml(JSON.stringify(e))}</span></div>`).join(""):'<div class="empty">No events recorded.</div>'}catch(error){$("tabBody").innerHTML=`<div class="empty">${escapeHtml(error.message)}</div>`}}
function copyCommand(){navigator.clipboard?.writeText('uv run racegreen race . "Describe the task" --agents 2');$("copyCommand").textContent="✓ COPIED";setTimeout(()=>$("copyCommand").textContent="▣ COPY CLI",1200)}
function renderHelp(){const q=$("helpSearch").value.toLowerCase();$("helpCards").innerHTML=commands.filter(c=>c.join(" ").toLowerCase().includes(q)).map((c,i)=>`<article class="help-card"><h3>${escapeHtml(c[0])}</h3><p>${escapeHtml(c[3])}</p><div class="command-row"><pre class="command">${escapeHtml(c[2])}</pre><button class="copy-small" data-command="${escapeHtml(c[2])}">COPY</button></div></article>`).join("")||'<div class="empty">No matching commands.</div>';document.querySelectorAll(".copy-small").forEach(b=>b.onclick=()=>{navigator.clipboard?.writeText(b.dataset.command||"");b.textContent="COPIED";setTimeout(()=>b.textContent="COPY",1000)})}
document.querySelectorAll(".nav button").forEach(b=>b.onclick=()=>{document.querySelectorAll(".nav button").forEach(x=>x.classList.remove("active"));b.classList.add("active");const help=b.dataset.view==="help";$("viewTitle").textContent=help?"HELP / COMMANDS":"OVERVIEW / RUNS";$("helpPage").classList.toggle("visible",help);$("overviewPage")?.classList.toggle("hidden",help);if(help)renderHelp()});
document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>{document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));b.classList.add("active");state.tab=b.dataset.tab;renderTab()});
$("runSearch").oninput=renderRuns;$("stateFilter").onchange=renderRuns;$("refresh").onclick=()=>loadRuns().then(()=>state.selected&&loadRun(state.selected));
$("copyCommand").onclick=copyCommand;$("newRun").onclick=()=>$("modal").classList.remove("hidden");$("closeModal").onclick=()=>$("modal").classList.add("hidden");$("modalCopy").onclick=()=>{navigator.clipboard?.writeText($("commandBox").textContent);$("modalCopy").textContent="COPIED";setTimeout(()=>$("modalCopy").textContent="COPY COMMAND",1200)};$("toggleAuto").onclick=()=>{state.auto=!state.auto;$("toggleAuto").textContent="AUTO-REFRESH: "+(state.auto?"ON":"OFF")};
$("helpSearch").oninput=renderHelp;loadRuns().catch(error=>$("runs").innerHTML=`<div class="empty">${escapeHtml(error.message)}</div>`);setInterval(()=>{if(state.auto)loadRuns().catch(()=>{})},5000);
</script></body></html>"""


def create_server(db: RaceDB, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("dashboard must bind to loopback")

    class Handler(BaseHTTPRequestHandler):
        def _send(
            self, status: int, payload: object, content_type: str = "application/json"
        ) -> None:
            body = (
                payload.encode("utf-8")
                if isinstance(payload, str)
                else json.dumps(payload).encode("utf-8")
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/":
                self._send(200, _HTML, "text/html")
            elif path == "/api/runs":
                self._send(200, db.list_runs())
            elif path == "/api/overview":
                runs = db.list_runs()
                self._send(
                    200,
                    {
                        "runs": len(runs),
                        "completed": sum(
                            text.get("state") in {"COMPLETED", "WON"} for text in runs
                        ),
                        "states": {
                            state: sum(item.get("state") == state for item in runs)
                            for state in {str(item.get("state")) for item in runs}
                        },
                    },
                )
            elif path.startswith("/api/runs/") and path.endswith("/events"):
                run_id = unquote(path[len("/api/runs/") : -len("/events")])
                query = parse_qs(parsed.query)
                after = int(query.get("after", ["0"])[0])
                self._send(200, db.events(run_id, after=after, limit=10000))
            elif path.startswith("/api/runs/"):
                run_id = unquote(path.removeprefix("/api/runs/"))
                try:
                    snapshot = db.run_snapshot(run_id)
                    snapshot["winner"] = db.get_winner(run_id)
                    self._send(200, snapshot)
                except KeyError:
                    self._send(404, {"error": "unknown run"})
            else:
                self._send(404, {"error": "not found"})

        def log_message(self, format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def text(value: object) -> str:
    return "" if value is None else str(value)


def serve(
    db: RaceDB,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
) -> None:
    server = create_server(db, host, port)
    url = f"http://{host}:{server.server_port}/"
    if open_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    print(f"RaceGreen dashboard: {url}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
