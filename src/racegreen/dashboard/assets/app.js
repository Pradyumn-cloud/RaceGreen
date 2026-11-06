"use strict";

const CANONICAL_RUN_STATES = [
  "preparing",
  "running",
  "cancelling",
  "cancelled",
  "completed",
  "exhausted",
  "failed",
  "unverifiable",
];

const COMMON_EVENT_TYPES = [
  "run_configured",
  "run_started",
  "run_finished",
  "run_failed",
  "run.unverifiable",
  "agent_started",
  "agent_status",
  "agent_finished",
  "agent_failed",
  "agent_cancelled",
  "candidate_frozen",
  "prefilter_started",
  "prefilter_finished",
  "candidate_submitted",
  "candidate_rejected",
  "candidate.won",
  "candidate.rejected",
  "candidate.confirmed_but_not_selected",
  "arbiter.winner_declared",
  "verification_started",
  "verification_finished",
];

const state = {
  view: "overview",
  runs: [],
  overview: null,
  selected: null,
  snapshot: null,
  tab: "candidates",
  auto: true,
  stream: null,
  pollTimer: null,
  lastSeq: 0,
  events: [],
  eventTypes: new Set(),
  eventsFirst: null,
  eventsLast: null,
  eventFilters: { type: "", agent: "", candidate: "", q: "" },
  follow: true,
  pending: 0,
  eventsToken: 0,
  snapshotTimer: null,
  loadToken: 0,
  drawerCandidate: null,
  toastTimer: null,
  lastError: "",
  lastErrorAt: 0,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const ESCAPE = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ESCAPE[char]);
}

function label(value) {
  return String(value || "unknown").replace(/_/g, " ").toUpperCase();
}

function short(value, length = 12) {
  const text = String(value ?? "");
  return text ? text.slice(0, length) : "—";
}

function badgeClass(value) {
  const text = String(value || "").toLowerCase();
  if (["completed", "won", "submitted", "prefilter_passed"].includes(text)) return "good";
  if (
    [
      "failed",
      "rejected",
      "cancelled",
      "exhausted",
      "unverifiable",
      "provider_failed",
      "timed_out",
      "infra_failed",
    ].includes(text)
  ) {
    return "bad";
  }
  if (
    [
      "running",
      "preparing",
      "cancelling",
      "working",
      "started",
      "received",
      "prefiltering",
      "selected_tests",
      "confirming",
      "confirmed_but_not_selected",
    ].includes(text)
  ) {
    return "warn";
  }
  return "info";
}

function badge(value) {
  return `<span class="badge ${badgeClass(value)}">${esc(label(value))}</span>`;
}

function parseDate(value) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "number") {
    const millis = value < 1e12 ? value * 1000 : value;
    return new Date(millis);
  }
  const text = String(value).trim();
  if (/^\d+(\.\d+)?$/.test(text)) {
    const number = Number(text);
    return new Date(number < 1e12 ? number * 1000 : number);
  }
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatWhen(value) {
  const date = parseDate(value);
  if (!date) return "—";
  return `${date.toISOString().slice(0, 19).replace("T", " ")}Z`;
}

function relative(value) {
  const date = parseDate(value);
  if (!date) return "unknown time";
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

async function api(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body && body.error) message = String(body.error);
    } catch (_) {
      // Keep the status-based message.
    }
    throw new Error(message);
  }
  return response.json();
}

function showError(message) {
  const text = String(message || "Unexpected error");
  if (text === state.lastError && Date.now() - state.lastErrorAt < 5000) return;
  state.lastError = text;
  state.lastErrorAt = Date.now();
  toast(text, true);
}

function toast(message, bad = false) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.toggle("bad", Boolean(bad));
  element.classList.remove("hidden");
  if (state.toastTimer) window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => element.classList.add("hidden"), 3800);
}

async function copyText(text) {
  const value = String(text ?? "");
  try {
    await navigator.clipboard.writeText(value);
    toast("Copied to clipboard.");
  } catch (_) {
    const area = document.createElement("textarea");
    area.value = value;
    area.setAttribute("readonly", "");
    area.className = "clipboard-helper";
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand("copy");
      toast("Copied to clipboard.");
    } catch (_) {
      toast("Could not copy. Select the text manually.", true);
    } finally {
      area.remove();
    }
  }
}

function setConnection(text, tone) {
  const connection = $("#connection");
  $("#connectionText").textContent = text;
  const dot = connection.querySelector(".dot");
  dot.className = `dot ${tone || ""}`.trim();
  $("#sideDot").className = `dot ${tone || ""}`.trim();
  $("#sideLabel").textContent =
    tone === "live" ? "LIVE TRACE" : tone === "warn" ? "RECONNECTING" : tone === "bad" ? "TRACE OFFLINE" : "LOCAL TRACE";
  $("#sideNote").textContent = tone === "live" ? "streaming persisted events" : "SQLite trace store";
}

async function loadOverview() {
  try {
    state.overview = await api("/api/overview");
    renderOverview();
    if (!state.selected) setConnection("LOCAL DB", "");
  } catch (error) {
    setConnection("DB OFFLINE", "bad");
    showError(error.message);
  }
}

async function loadRuns() {
  try {
    state.runs = await api("/api/runs");
    if (state.selected && !state.runs.some((run) => run.run_id === state.selected)) {
      state.selected = null;
      state.snapshot = null;
      state.lastSeq = 0;
      disconnectStream();
      renderDetail();
    }
    renderRuns();
  } catch (error) {
    showError(error.message);
  }
}

async function loadAll() {
  await Promise.allSettled([loadOverview(), loadRuns()]);
  if (!state.selected && state.runs.length) {
    const preferred =
      state.runs.find((run) => ["running", "preparing", "cancelling"].includes(run.state)) ||
      state.runs[0];
    await selectRun(preferred.run_id);
  }
}

function renderOverview() {
  const overview = state.overview;
  if (!overview) return;
  $("#mTotal").textContent = String(overview.runs.total);
  $("#mActive").textContent = String(overview.runs.active);
  $("#mCandidates").textContent = String(overview.candidates.total);
  $("#mEvents").textContent = String(overview.events);
  $("#mWinners").textContent = String(overview.winners.full_suite_confirmed);

  const select = $("#stateFilter");
  const current = select.value;
  const observed = Object.keys(overview.runs.by_state || {});
  const states = Array.from(new Set([...CANONICAL_RUN_STATES, ...observed]));
  select.replaceChildren(new Option("ALL STATES", ""));
  for (const value of states) {
    select.appendChild(new Option(label(value), value));
  }
  select.value = states.includes(current) ? current : "";
}

function renderRuns() {
  const search = $("#runSearch").value.trim().toLowerCase();
  const stateFilter = $("#stateFilter").value;
  const runs = state.runs.filter((run) => {
    if (stateFilter && run.state !== stateFilter) return false;
    if (!search) return true;
    return [run.run_id, run.state, run.base_commit, run.winner_candidate_id]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(search));
  });

  $("#runCount").textContent = `${runs.length} of ${state.runs.length} run${state.runs.length === 1 ? "" : "s"}`;
  const container = $("#runs");
  if (!runs.length) {
    container.innerHTML = `<div class="empty">${state.runs.length ? "No runs match this filter." : "No persisted runs yet."}</div>`;
    return;
  }
  container.innerHTML = runs
    .map((run) => {
      const winner = run.winner_candidate_id
        ? ` · winner ${esc(short(run.winner_candidate_id, 10))}`
        : "";
      const base = run.base_commit ? `base ${esc(short(run.base_commit, 10))}` : "no base recorded";
      const when = relative(run.updated_at || run.created_at);
      return `<button class="run ${state.selected === run.run_id ? "selected" : ""}" type="button" data-run="${esc(run.run_id)}">
        <div class="run-line">${badge(run.state)}<span class="run-id">${esc(run.run_id)}</span></div>
        <div class="run-meta"><span>${base} · ${esc(when)}</span><span>${esc(String(run.candidate_count || 0))} cand${winner}</span></div>
      </button>`;
    })
    .join("");
}

async function selectRun(runId) {
  if (!runId) return;
  if (state.selected !== runId) {
    state.selected = runId;
    state.tab = "candidates";
    state.snapshot = null;
    state.eventsToken += 1;
    state.events = [];
    state.eventTypes = new Set();
    state.eventsFirst = null;
    state.eventsLast = null;
    state.pending = 0;
    state.follow = true;
    state.eventFilters = { type: "", agent: "", candidate: "", q: "" };
    closeDrawer();
    renderRuns();
    setActiveTab("candidates");
    renderDetail();
  }
  await loadSnapshot(runId, { initial: true });
}

async function loadSnapshot(runId, { initial = false } = {}) {
  const token = ++state.loadToken;
  try {
    const snapshot = await api(`/api/runs/${encodeURIComponent(runId)}`);
    if (token !== state.loadToken || state.selected !== runId) return;
    state.snapshot = snapshot;
    if (initial) {
      state.lastSeq = Number(snapshot.last_sequence || 0);
    }
    renderDetail({ preserveTab: !initial });
    if (initial) connectStream();
  } catch (error) {
    showError(error.message);
  }
}

function renderDetail({ preserveTab = false } = {}) {
  const snapshot = state.snapshot;
  if (!snapshot) {
    $("#detailTitle").textContent = "Select a run";
    $("#detailSub").textContent = "Choose a run from the queue to inspect its evidence.";
    $("#detailStatus").innerHTML = "";
    $("#sAgents").textContent = "—";
    $("#sCandidates").textContent = "—";
    $("#sEvents").textContent = "—";
    $("#sWinner").textContent = "—";
    $("#sSequence").textContent = "—";
    $("#tabBody").innerHTML = `<div class="empty">Choose a run from the queue.</div>`;
    return;
  }
  const run = snapshot.run;
  $("#detailTitle").innerHTML = `${esc(run.run_id)} <small>${esc(run.last_event_type || "no events")}</small>`;
  $("#detailSub").textContent = `${run.base_commit ? `base ${short(run.base_commit, 14)}` : "no base commit"} · updated ${formatWhen(run.updated_at || run.created_at)}`;
  $("#detailStatus").innerHTML = badge(run.state) + (snapshot.winner ? ` ${badge("won")}` : "");
  $("#sAgents").textContent = String(snapshot.counts.agents);
  $("#sCandidates").textContent = String(snapshot.counts.candidates);
  $("#sEvents").textContent = String(snapshot.counts.events);
  $("#sWinner").textContent = snapshot.winner ? short(snapshot.winner.candidate_id, 16) : "—";
  $("#sSequence").textContent = String(snapshot.last_sequence || 0);
  if (!preserveTab || state.tab !== "timeline") renderTab();
  else updateTimelineStatus();
}

function setActiveTab(tab) {
  state.tab = tab;
  $$(".tab").forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
}

function renderTab() {
  if (state.tab === "timeline") {
    renderTimeline();
    return;
  }
  if (state.tab === "evidence") {
    renderEvidence();
    return;
  }
  if (state.tab === "agents") {
    renderAgents();
    return;
  }
  renderCandidates();
}

function renderCandidates() {
  const body = $("#tabBody");
  const candidates = state.snapshot?.candidates || [];
  if (!candidates.length) {
    body.innerHTML = `<div class="empty">No candidate snapshots recorded for this run.</div>`;
    return;
  }
  body.innerHTML = candidates
    .map((candidate) => {
      const meta = [
        candidate.agent_id || "unknown agent",
        candidate.stage || "no stage",
        candidate.patch_hash ? `hash ${short(candidate.patch_hash, 14)}` : "hash not recorded",
      ].join(" · ");
      const note = [candidate.classification, candidate.reason].filter(Boolean).join(" · ");
      return `<article class="candidate">
        <div class="candidate-head"><span class="candidate-id">${esc(candidate.candidate_id)}</span>${badge(candidate.state)}</div>
        <div class="candidate-meta">${esc(meta)}</div>
        ${note ? `<div class="candidate-meta warn-text">${esc(note)}</div>` : ""}
        ${pipeline(candidate)}
        <div class="candidate-actions">
          <button class="ghost" type="button" data-patch="${esc(candidate.candidate_id)}" ${candidate.has_patch ? "" : "disabled"}>VIEW PATCH</button>
          <button class="ghost" type="button" data-focus-events="${esc(candidate.candidate_id)}">FOCUS EVENTS</button>
        </div>
      </article>`;
    })
    .join("");
}

function pipeline(candidate) {
  const steps = ["RECEIVED", "PREFILTER", "SELECTED", "CONFIRM", "WON"];
  const ranks = {
    received: 0,
    prefiltering: 1,
    prefilter_passed: 2,
    selected_tests: 3,
    confirming: 4,
    won: 5,
    confirmed_but_not_selected: 5,
    rejected: 2,
    cancelled: 2,
    infra_failed: 2,
  };
  let index = ranks[candidate.state] ?? 0;
  const terminalFailure = ["rejected", "cancelled", "infra_failed"].includes(candidate.state);
  if (candidate.state === "rejected") {
    const stage = String(candidate.stage || "").toLowerCase();
    if (stage.includes("full")) index = 5;
    else if (stage.includes("selected") || stage.includes("confirm")) index = 4;
    else index = 2;
  }
  return `<div class="pipeline">${steps
    .map((step, stepIndex) => {
      let className = "";
      if (terminalFailure && stepIndex === Math.min(index, 4)) className = "failed";
      else if (stepIndex < index) className = "done";
      else if (stepIndex === index && candidate.state === "won") className = "done";
      else if (
        stepIndex === index &&
        ["prefiltering", "selected_tests", "confirming", "confirmed_but_not_selected"].includes(candidate.state)
      ) {
        className = "current";
      }
      return `<span class="${className}">${step}</span>`;
    })
    .join("")}</div>`;
}

function renderEvidence() {
  const body = $("#tabBody");
  const holder = state.snapshot?.winner;
  if (!holder) {
    body.innerHTML = `<div class="empty">No winner is recorded for this run. Verification and rejection evidence remains available in the event timeline.</div>`;
    return;
  }
  const complete = holder.evidence_complete === true;
  const tone = complete ? "confirmed" : "declared";
  const note = complete
    ? "Full-suite confirmation evidence is recorded. The dashboard displays the decision; it does not re-verify it."
    : "A winner was declared, but full-suite confirmation evidence is not recorded. Treat this result as unverified until the configured full suite has been checked.";
  body.innerHTML = `<div class="winner-banner ${tone}">
    <div class="eyebrow">Winner declaration</div>
    <h3>${esc(holder.candidate_id)}</h3>
    <p>${esc(note)}</p>
    <div class="candidate-meta">${complete ? badge("won") : badge("unverifiable")} patch ${esc(short(holder.patch_hash, 20))} · base ${esc(short(holder.base_commit, 14))}</div>
    <div class="candidate-actions"><button class="ghost" type="button" data-patch="${esc(holder.candidate_id)}">VIEW PATCH</button></div>
    <div class="eyebrow evidence-label">Recorded evidence</div>
    <pre class="code">${esc(JSON.stringify(holder.evidence || {}, null, 2))}</pre>
  </div>`;
}

function renderAgents() {
  const body = $("#tabBody");
  const agents = state.snapshot?.agents || [];
  if (!agents.length) {
    body.innerHTML = `<div class="empty">No agents recorded for this run.</div>`;
    return;
  }
  body.innerHTML = agents
    .map(
      (agent) => `<article class="candidate">
        <div class="candidate-head"><span class="candidate-id">${esc(agent.agent_id)}</span>${badge(agent.status)}</div>
        <div class="candidate-meta">last event ${esc(agent.last_event_type || "—")} · seq ${esc(String(agent.last_event_sequence ?? "—"))} · ${esc(formatWhen(agent.last_event_at))}</div>
      </article>`,
    )
    .join("");
}

function renderTimeline() {
  const body = $("#tabBody");
  const agentOptions = (state.snapshot?.agents || [])
    .map((agent) => `<option value="${esc(agent.agent_id)}">${esc(agent.agent_id)}</option>`)
    .join("");
  const candidateOptions = (state.snapshot?.candidates || [])
    .map((candidate) => `<option value="${esc(candidate.candidate_id)}">${esc(candidate.candidate_id)}</option>`)
    .join("");
  const typeOptions = Array.from(new Set([...COMMON_EVENT_TYPES, ...state.eventTypes]))
    .sort()
    .map((value) => `<option value="${esc(value)}">${esc(value)}</option>`)
    .join("");
  body.innerHTML = `<div class="timeline-tools">
      <input id="eventSearch" class="search" placeholder="Filter loaded events (JSON included)..." value="${esc(state.eventFilters.q)}" aria-label="Filter loaded events">
      <select id="eventTypeFilter" class="select" aria-label="Filter by event type"><option value="">ALL EVENTS</option>${typeOptions}</select>
      <select id="eventAgentFilter" class="select" aria-label="Filter by agent"><option value="">ALL AGENTS</option>${agentOptions}</select>
      <select id="eventCandidateFilter" class="select" aria-label="Filter by candidate"><option value="">ALL CANDIDATES</option>${candidateOptions}</select>
      <button class="ghost" id="followToggle" type="button">FOLLOW: ON</button>
      <button class="ghost" id="loadOlder" type="button">LOAD OLDER</button>
    </div>
    <div class="timeline-status"><span id="timelineCount">No events loaded.</span><button class="ghost hidden" id="newEvents" type="button"></button></div>
    <div id="eventList"></div>`;

  $("#eventTypeFilter").value = state.eventFilters.type;
  $("#eventAgentFilter").value = state.eventFilters.agent;
  $("#eventCandidateFilter").value = state.eventFilters.candidate;
  $("#eventSearch").addEventListener("input", (event) => {
    state.eventFilters.q = event.target.value;
    renderEventList();
    updateTimelineStatus();
  });
  $("#eventTypeFilter").addEventListener("change", (event) => {
    state.eventFilters.type = event.target.value;
    loadEvents({ reset: true });
  });
  $("#eventAgentFilter").addEventListener("change", (event) => {
    state.eventFilters.agent = event.target.value;
    loadEvents({ reset: true });
  });
  $("#eventCandidateFilter").addEventListener("change", (event) => {
    state.eventFilters.candidate = event.target.value;
    loadEvents({ reset: true });
  });
  $("#followToggle").addEventListener("click", () => {
    state.follow = !state.follow;
    if (state.follow) state.pending = 0;
    updateTimelineStatus();
    if (state.follow) scrollEventListToEnd();
  });
  $("#loadOlder").addEventListener("click", () => loadEvents({ older: true }));
  $("#newEvents").addEventListener("click", () => {
    state.follow = true;
    state.pending = 0;
    renderEventList();
    updateTimelineStatus();
    scrollEventListToEnd();
  });

  loadEvents({ reset: true });
}

function eventMatches(event) {
  const filters = state.eventFilters;
  if (filters.type && event.event !== filters.type) return false;
  if (filters.agent && event.agent_id !== filters.agent) return false;
  if (filters.candidate && event.candidate_id !== filters.candidate) return false;
  const query = filters.q.trim().toLowerCase();
  if (query && !JSON.stringify(event).toLowerCase().includes(query)) return false;
  return true;
}

function dedupeEvents(events) {
  const bySequence = new Map();
  for (const event of events) {
    if (event && Number.isFinite(Number(event.sequence))) {
      bySequence.set(Number(event.sequence), event);
    }
  }
  return Array.from(bySequence.values()).sort((left, right) => Number(left.sequence) - Number(right.sequence));
}

function updateEventBounds() {
  if (!state.events.length) {
    state.eventsFirst = null;
    state.eventsLast = null;
    return;
  }
  state.eventsFirst = Number(state.events[0].sequence);
  state.eventsLast = Number(state.events[state.events.length - 1].sequence);
}

async function loadEvents({ reset = false, older = false } = {}) {
  if (!state.selected || !state.snapshot) return;
  const token = ++state.eventsToken;
  if (reset) {
    state.events = [];
    state.eventsFirst = null;
    state.eventsLast = null;
    state.pending = 0;
    state.follow = true;
  }
  if (older && !state.eventsFirst) return;

  const params = new URLSearchParams();
  params.set("limit", "300");
  if (reset) {
    params.set("latest", "1");
  } else if (older) {
    params.set("before", String(state.eventsFirst));
    params.set("latest", "1");
  } else {
    params.set("after", String(state.eventsLast || 0));
  }
  if (state.eventFilters.type) params.set("type", state.eventFilters.type);
  if (state.eventFilters.agent) params.set("agent", state.eventFilters.agent);
  if (state.eventFilters.candidate) params.set("candidate", state.eventFilters.candidate);

  try {
    const events = await api(`/api/runs/${encodeURIComponent(state.selected)}/events?${params.toString()}`);
    if (token !== state.eventsToken) return;
    for (const event of events) {
      if (event.event) state.eventTypes.add(String(event.event));
    }
    if (reset) state.events = dedupeEvents([...events, ...state.events]);
    else if (older) state.events = dedupeEvents([...events, ...state.events]);
    else state.events = dedupeEvents([...state.events, ...events]);
    updateEventBounds();
    renderEventList();
    updateTimelineStatus();
    if (reset && state.follow) scrollEventListToEnd();
  } catch (error) {
    if (token !== state.eventsToken) return;
    const list = $("#eventList");
    if (list) list.innerHTML = `<div class="error">${esc(error.message)}</div>`;
  }
}

function renderEventList() {
  const list = $("#eventList");
  if (!list) return;
  const events = state.events.filter(eventMatches);
  list.replaceChildren();
  if (!events.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = state.events.length
      ? "No loaded events match the current filters."
      : "No events recorded yet.";
    list.appendChild(empty);
    return;
  }
  for (const event of events) list.appendChild(eventNode(event));
}

function eventNode(event) {
  const details = document.createElement("details");
  details.className = "event";
  const summary = document.createElement("summary");
  const time = document.createElement("span");
  time.className = "event-time";
  time.textContent = formatWhen(event.timestamp);
  time.title = formatWhen(event.timestamp);
  const type = document.createElement("span");
  type.className = "event-type";
  type.textContent = String(event.event || "event");
  const agent = document.createElement("span");
  agent.className = "event-agent";
  agent.textContent = String(event.agent_id || "—");
  const candidate = document.createElement("span");
  candidate.className = "event-candidate";
  candidate.textContent = String(event.candidate_id || `seq ${event.sequence ?? "—"}`);
  summary.append(time, type, agent, candidate);
  const code = document.createElement("pre");
  code.className = "code";
  code.textContent = JSON.stringify(event, null, 2);
  details.append(summary, code);
  return details;
}

function updateTimelineStatus() {
  const count = $("#timelineCount");
  if (count) {
    const visible = state.events.filter(eventMatches).length;
    count.textContent = `${visible} of ${state.events.length} loaded event${state.events.length === 1 ? "" : "s"}`;
  }
  const follow = $("#followToggle");
  if (follow) follow.textContent = `FOLLOW: ${state.follow ? "ON" : "OFF"}`;
  const older = $("#loadOlder");
  if (older) older.disabled = !state.eventsFirst || state.eventsFirst <= 1;
  const pending = $("#newEvents");
  if (pending) {
    pending.classList.toggle("hidden", state.pending <= 0);
    pending.textContent = `${state.pending} NEW EVENT${state.pending === 1 ? "" : "S"}`;
  }
}

function scrollEventListToEnd() {
  const list = $("#eventList");
  if (list) list.scrollTop = list.scrollHeight;
}

function handleStreamEvent(event) {
  if (event.run_id !== state.selected) return;
  const sequence = Number(event.sequence || 0);
  if (!sequence || sequence <= state.lastSeq) return;
  state.lastSeq = sequence;
  if (event.event) state.eventTypes.add(String(event.event));

  if (state.tab === "timeline" && eventMatches(event)) {
    state.events = dedupeEvents([...state.events, event]);
    updateEventBounds();
    if (state.follow) {
      const list = $("#eventList");
      if (list) {
        const empty = list.querySelector(".empty");
        if (empty) empty.remove();
        list.appendChild(eventNode(event));
        scrollEventListToEnd();
      }
    } else {
      state.pending += 1;
    }
    updateTimelineStatus();
  }

  if (
    [
      "run_finished",
      "run_failed",
      "run.unverifiable",
      "arbiter.winner_declared",
      "candidate.won",
      "candidate.rejected",
    ].includes(event.event)
  ) {
    loadOverview();
    loadRuns();
  }
  scheduleSnapshotRefresh();
}

function scheduleSnapshotRefresh() {
  if (state.snapshotTimer) window.clearTimeout(state.snapshotTimer);
  state.snapshotTimer = window.setTimeout(() => {
    if (state.selected) loadSnapshot(state.selected, { silent: true });
  }, 250);
}

function connectStream() {
  disconnectStream();
  if (!state.selected) return;
  if (!("EventSource" in window)) {
    setConnection("POLLING", "warn");
    state.pollTimer = window.setInterval(async () => {
      if (!state.selected) return;
      try {
        const events = await api(
          `/api/runs/${encodeURIComponent(state.selected)}/events?after=${state.lastSeq}&limit=500`,
        );
        for (const event of events) handleStreamEvent(event);
      } catch (_) {
        setConnection("POLLING ERROR", "bad");
      }
    }, 2000);
    return;
  }
  const url = `/api/runs/${encodeURIComponent(state.selected)}/stream?after=${state.lastSeq}`;
  const stream = new EventSource(url);
  state.stream = stream;
  setConnection("CONNECTING", "warn");
  stream.onopen = () => setConnection("LIVE", "live");
  stream.onerror = () => setConnection("RECONNECTING", "warn");
  stream.onmessage = (message) => {
    try {
      const event = JSON.parse(message.data);
      handleStreamEvent(event);
    } catch (_) {
      setConnection("STREAM ERROR", "bad");
    }
  };
}

function disconnectStream() {
  if (state.stream) {
    state.stream.close();
    state.stream = null;
    setConnection("LOCAL DB", "");
  }
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function openCandidate(candidateId) {
  if (!candidateId || !state.selected) return;
  const candidate = (state.snapshot?.candidates || []).find((item) => item.candidate_id === candidateId);
  if (!candidate) return;
  state.drawerCandidate = candidateId;
  $("#drawerTitle").textContent = candidateId;
  $("#drawerMeta").innerHTML = `${badge(candidate.state)} ${esc(candidate.agent_id || "unknown agent")} · ${esc(candidate.stage || "no stage")} · ${esc(short(candidate.patch_hash, 18))}`;
  $("#drawerBackdrop").classList.remove("hidden");
  $("#closeDrawer").focus();
  const patch = $("#drawerPatch");
  patch.textContent = "Loading patch...";
  const events = $("#drawerEvents");
  events.innerHTML = `<div class="empty">Loading events...</div>`;

  if (!candidate.has_patch) {
    patch.textContent = "No patch artifact path was recorded for this candidate.";
  } else {
    fetch(
      `/api/runs/${encodeURIComponent(state.selected)}/candidates/${encodeURIComponent(candidateId)}/patch`,
    )
      .then(async (response) => {
        if (!response.ok) {
          let message = `Patch unavailable (${response.status}).`;
          try {
            const body = await response.json();
            if (body && body.error) message = String(body.error);
          } catch (_) {
            // Keep the status-based message.
          }
          return message;
        }
        return response.text();
      })
      .then((text) => {
        if (state.drawerCandidate === candidateId) patch.textContent = text;
      })
      .catch(() => {
        if (state.drawerCandidate === candidateId) patch.textContent = "Patch unavailable.";
      });
  }

  api(
    `/api/runs/${encodeURIComponent(state.selected)}/events?candidate=${encodeURIComponent(candidateId)}&limit=200&latest=1`,
  )
    .then((items) => {
      if (state.drawerCandidate !== candidateId) return;
      events.replaceChildren();
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No events recorded for this candidate.";
        events.appendChild(empty);
        return;
      }
      for (const event of items) events.appendChild(eventNode(event));
    })
    .catch((error) => {
      if (state.drawerCandidate !== candidateId) return;
      events.innerHTML = `<div class="error">${esc(error.message)}</div>`;
    });
}

function closeDrawer() {
  state.drawerCandidate = null;
  $("#drawerBackdrop").classList.add("hidden");
}

function showView(view) {
  state.view = view;
  $$(".nav button").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $("#viewTitle").textContent = view === "help" ? "HELP / COMMANDS" : "OVERVIEW / RUNS";
  $("#overviewPage").classList.toggle("hidden", view !== "overview");
  $("#helpPage").classList.toggle("visible", view === "help");
}

function shellQuote(value) {
  const text = String(value || "");
  if (/^[A-Za-z0-9_./:@%+=,-]+$/.test(text)) return text;
  return `"${text.replace(/(["\\$`])/g, "\\$1")}"`;
}

function updateCommand() {
  const repo = $("#formRepo").value.trim() || ".";
  const task = $("#formTask").value.trim() || "Describe the task";
  const agents = Math.min(16, Math.max(1, Number.parseInt($("#formAgents").value, 10) || 1));
  const concurrency = Math.min(
    16,
    Math.max(1, Number.parseInt($("#formConcurrency").value, 10) || 1),
  );
  const command = `uv run racegreen race ${shellQuote(repo)} ${shellQuote(task)} --agents ${agents} --max-concurrency ${concurrency}`;
  $("#commandBox").textContent = command;
  return command;
}

function openModal() {
  $("#modal").classList.remove("hidden");
  updateCommand();
  $("#formRepo").focus();
}

function closeModal() {
  $("#modal").classList.add("hidden");
}

function bindEvents() {
  $$(".nav button").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });
  $("#refresh").addEventListener("click", async () => {
    await Promise.allSettled([loadOverview(), loadRuns()]);
    if (state.selected) await loadSnapshot(state.selected);
    if (state.tab === "timeline") await loadEvents({ reset: true });
    toast("Dashboard refreshed.");
  });
  $("#newRun").addEventListener("click", openModal);
  $("#toggleAuto").addEventListener("click", () => {
    state.auto = !state.auto;
    $("#toggleAuto").textContent = `AUTO-REFRESH: ${state.auto ? "ON" : "OFF"}`;
    if (state.auto) loadAll();
  });
  $("#copyCommand").addEventListener("click", () => copyText("uv run racegreen dashboard --open"));
  $("#runSearch").addEventListener("input", renderRuns);
  $("#stateFilter").addEventListener("change", renderRuns);
  $("#runs").addEventListener("click", (event) => {
    const target = event.target.closest("[data-run]");
    if (target) selectRun(target.dataset.run);
  });
  $$(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      setActiveTab(button.dataset.tab);
      renderTab();
    });
  });
  $("#tabBody").addEventListener("click", (event) => {
    const patch = event.target.closest("[data-patch]");
    if (patch) {
      openCandidate(patch.dataset.patch);
      return;
    }
    const focus = event.target.closest("[data-focus-events]");
    if (focus) {
      state.eventFilters.candidate = focus.dataset.focusEvents;
      setActiveTab("timeline");
      renderTab();
    }
  });
  $("#closeDrawer").addEventListener("click", closeDrawer);
  $("#drawerBackdrop").addEventListener("click", (event) => {
    if (event.target === $("#drawerBackdrop")) closeDrawer();
  });
  $("#closeModal").addEventListener("click", closeModal);
  $("#modal").addEventListener("click", (event) => {
    if (event.target === $("#modal")) closeModal();
  });
  ["#formRepo", "#formTask", "#formAgents", "#formConcurrency"].forEach((selector) => {
    $(selector).addEventListener("input", updateCommand);
  });
  $("#modalCopy").addEventListener("click", () => copyText(updateCommand()));
  $("#helpSearch").addEventListener("input", (event) => {
    const query = event.target.value.trim().toLowerCase();
    $$(".help-card").forEach((card) => {
      card.classList.toggle("hidden", Boolean(query) && !card.textContent.toLowerCase().includes(query));
    });
  });
  document.addEventListener("click", (event) => {
    const target = event.target.closest("[data-copy]");
    if (target) copyText(target.dataset.copy);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!$("#modal").classList.contains("hidden")) closeModal();
    else if (!$("#drawerBackdrop").classList.contains("hidden")) closeDrawer();
  });
}

async function init() {
  bindEvents();
  await loadAll();
  window.setInterval(() => {
    if (!state.auto) return;
    loadOverview();
    loadRuns();
  }, 5000);
}

document.addEventListener("DOMContentLoaded", init);
