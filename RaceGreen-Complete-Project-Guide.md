# RaceGreen: Complete Project Build Guide

This guide takes RaceGreen from an empty repository to a usable, releasable local tool. It assumes Windows 11, WSL2, an RTX 4050, and 16 GB of system RAM. The first release is a Python-first CLI; a live UI, more model providers, and more languages come after the core race is dependable.

## 1. What you are building

RaceGreen starts several coding agents against the same task. Each agent receives an isolated Git worktree. As candidates arrive, RaceGreen snapshots their patches, applies cheap filters, runs a selected test set, and runs the full suite for promising candidates. A candidate becomes the winner only after full-suite confirmation. Once confirmed, RaceGreen cancels remaining workers and offers a reviewable patch or branch.

The intended command eventually looks like:

```bash
racegreen run --repo ./app --task "Fix the login redirect bug" --agents 2
```

The initial version should optimize for **verified time-to-patch, safe cleanup, clear evidence, and bounded spend**. Do not promise a fixed workspace startup time, a fixed speedup, universal language support, or that RaceGreen is the first tool of its kind until measured or researched.

## 2. Hardware-aware operating model

- Run Linux build tools inside WSL2 and keep source repositories, worktrees, virtual environments, and caches on the WSL Linux filesystem. Microsoft recommends that location for best performance when using Linux tools: [WSL filesystem guidance](https://learn.microsoft.com/en-us/windows/dev-environment/wsl/interop).
- The RTX 4050 is optional for RaceGreen. Device VRAM differs by machine, and local model inference competes with tests and the OS for memory. Begin with an API model and two agents; measure before increasing concurrency.
- Do not run several large local models on 16 GB system RAM by default. If evaluating local inference, test one quantized model at a time and log peak system and GPU memory.
- Keep Docker and VM support optional in the first release. Do not assume KVM or microVM acceleration is available under WSL2.
- Share only read-only package caches; do not point multiple agents at one writable virtual environment.

NVIDIA documents CUDA support for WSL2, with driver support provided from Windows: [CUDA on WSL guide](https://docs.nvidia.com/cuda/wsl-user-guide/index.html). Validate the device inside WSL before investing in GPU-dependent features.

## 3. Install the development environment

From Windows PowerShell, install or update WSL and Ubuntu if they are not already available:

```powershell
wsl --install -d Ubuntu
wsl --update
```

Open Ubuntu and install basic development tools:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip build-essential
```

Confirm GPU visibility if you want to evaluate local inference:

```bash
nvidia-smi
```

If that command is not available or does not show the GPU, continue with API-backed inference. Do not install a Linux display driver inside WSL as a workaround; follow NVIDIA's current WSL-specific instructions.

Create a Linux-side project directory and repository:

```bash
mkdir -p ~/src/racegreen
cd ~/src/racegreen
git init
```

If the repository already exists on Windows, clone it into `~/src` from inside WSL. Avoid developing in `/mnt/c/...` with Linux build tools due to filesystem overhead.

## 4. Define the initial release before coding

Write a short product spec with these decisions:

1. **First supported language:** Python 3.12.
2. **Toolchain:** `uv`, `ruff`, `mypy`, `pytest`, `asyncio`, SQLite, and Typer.
3. **First agent backend:** RaceGreen's own tool loop with one OpenAI-compatible provider. Do not build many provider adapters at once.
4. **First verifier:** configured commands plus conservative Python syntax checks; selected tests are advisory until measured.
5. **Winner condition:** configured full-suite command succeeds on the immutable candidate snapshot.
6. **Integration behavior:** produce a patch or new branch for review; never silently commit to the user's current branch.
7. **Initial concurrency:** two agents, adjustable down to one.
8. **Default security policy:** no implicit secret inheritance; no hidden network permission; explicit setup-command policy.
9. **Benchmark:** compare sequential, racing, and best-of-N wait-for-all using the same model, prompt budget, and test commands. Track full-suite runs avoided; selected tests do not shorten the full-suite critical path.
10. **False-winner measurement:** every task includes held-out tests that agents do not see. A false winner passes the visible full suite but fails held-out tests.

Record a threat model. Worktrees isolate Git changes, but they do not sandbox hostile code. If arbitrary repository code must be treated as hostile, add an OS-level execution boundary and evaluate its actual protections separately.

## 5. Create the project layout

Start with a modular Python package, command-line interface, and focused documentation. For example:

```text
racegreen/
  pyproject.toml
  README.md
  AGENTS.md
  src/racegreen/
    cli.py
    config.py
    coordinator.py
    domain/
      models.py
      states.py
      events.py
    workspace/
      git.py
      manager.py
    agents/
      protocol.py
      provider_one.py
      process.py
    scheduler/
      runner.py
      budgets.py
      cancellation.py
    verification/
      prefilter.py
      selection.py
      runner.py
    arbiter/
      state_machine.py
    storage/
      sqlite.py
      migrations/
    artifacts/
      patches.py
  docs/
    architecture.md
    security.md
    benchmark.md
  tests/
    unit/
    integration/
    fixtures/
```

Use the repository's chosen formatter, type checker, and test framework consistently. Pin runtime and development dependencies, document why each dependency is needed, and avoid adding a dashboard framework until the CLI lifecycle works.

Keep [AGENTS.md](./AGENTS.md) at the repository root and follow it for all code changes.

## 6. Define core data types and event contract

Before implementing concurrency, define stable IDs and explicit records:

- `Run`: run ID, repository, base commit, dirty-state summary, config fingerprint, start/end, terminal status.
- `Agent`: run ID, agent ID, strategy/provider, workspace path, budget, process ID, status, usage, exit reason.
- `Candidate`: candidate ID, agent ID, base commit, patch hash, immutable artifact location, changed paths, timestamps.
- `Verification`: candidate ID and patch hash, verification stage, command, selected tests, result classification, output reference, duration.
- `Event`: event ID, run ID, sequence number, UTC timestamp, schema version, event type, structured payload.

Persist append-only events such as `run.created`, `workspace.ready`, `agent.started`, `candidate.submitted`, `candidate.rejected`, `verification.started`, `verification.finished`, `candidate.confirmed`, `agent.cancelled`, and `run.finished`.

Requirements:

- Version persisted schemas and migrations.
- Store large test output as bounded artifact files, not huge SQLite rows.
- Redact secrets before writing any event or artifact.
- Record the patch hash in every verification event so evidence always points to the exact code tested.
- Treat persistence failure as a run-integrity failure; never silently claim a traceable winner without a durable record.

## 7. Phase 0 — Build a benchmark and baseline

Select a small, legally usable set of Python repository tasks with reliable setup and expected tests. Include both easy and difficult tasks, plus examples with flaky tests, unrelated test failures, and broad changes.

For each task, save:

- Repository URL and exact base commit.
- Task prompt and acceptance criteria.
- Setup and dependency lock information.
- Targeted test command and full-suite command.
- Expected behavior and known flaky tests.
- Baseline result, elapsed time, and model/provider cost.

Run sequential and best-of-N wait-for-all workflows before racing. Track success rate, verified time-to-patch, time to candidate, test duration, cost, full-suite runs avoided, false-winner rate from held-out tests, and common failure categories. These baselines are the comparison point for RaceGreen.

**Exit gate:** you can reproduce baseline runs from a clean environment and explain every metric. Avoid quoting research figures until you have verified the exact paper and metric.

## 8. Phase 1 — Implement safe repository and workspace handling

Use Git's worktree support to make an independent working directory per agent. Git documents worktrees as multiple working trees associated with one repository: [git-worktree reference](https://git-scm.com/docs/git-worktree).

Implement these operations first:

1. Inspect the repository root, current branch or detached state, current commit, dirty files, submodules, and Git version.
2. Capture the exact base commit and dirty-state summary in the run record.
3. Refuse ambiguous or unsafe setup. Do not overwrite user changes or assume a dirty source tree is disposable.
4. Create a unique run directory and one worktree/branch per agent from the recorded base.
5. Keep RaceGreen artifacts outside the source tree in a run-scoped directory.
6. Verify each workspace's Git root and expected base before starting an agent.
7. Track every resource RaceGreen creates so cleanup is scoped and safe.
8. Recover or report partial setup after cancellation, crash, or disk exhaustion.

Never use forced cleanup against user-owned worktrees. Do not use a shared writable venv. Use a separate environment per worktree with `uv` and a shared package download cache where supported; include environment creation in the workspace spin-up benchmark.

**Exit gate:** repeated creation, interruption, and cleanup preserve the source repo, current branch, untracked files, and unrelated worktrees.

## 9. Phase 2 — Build one complete agent path

Create a narrow provider/agent interface. Keep provider-specific request formats out of the scheduler and arbiter.

An agent run needs:

- Task and repository context.
- A private worktree path.
- A token/cost and wall-clock budget.
- A minimal, documented environment.
- Structured progress and candidate submission.
- Bounded output capture and secret redaction.
- Explicit exit, timeout, provider error, and cancellation handling.

If building your own tool loop, begin with a limited toolbox: file read, search, edit, bounded command execution, and candidate submission. Require all paths to resolve within the assigned workspace. Add repeated-action/loop detection and cap tool output to prevent runaway context and disk use.

Do not use shell string interpolation for model-generated commands. Spawn argument arrays with a controlled environment. Record process ownership and terminate the whole process tree on cancellation.

**Exit gate:** one agent can be started, observed, timed out, cancelled, and replayed from persisted events; provider failure has a clear terminal result.

## 10. Phase 3 — Add the bounded racing scheduler

Add concurrency only after the single-agent path is stable. The scheduler should own all workers and enforce:

- Maximum concurrent agents.
- Per-agent and per-run time limits.
- Per-agent and per-run token/cost limits where provider data permits.
- Maximum candidate count and candidate artifact size.
- Backpressure when verifiers are busy.
- Idempotent cancellation and rejection of late events after cancellation or terminal run state.
- Provider rate-limit handling with bounded exponential backoff.

Use different strategies only when the experiment is designed to measure them. For a first comparison, keep model and budget constant while changing prompts or agent procedure. Later compare models and report cost alongside success.

With your 16 GB system, start at two agents and measure peak RAM, GPU memory, CPU, test contention, and API cost. Scheduler concurrency should be configurable and conservative by default.

**Exit gate:** canceling a run stops every RaceGreen-owned child process and no late candidate can alter a final result.

## 11. Phase 4 — Candidate intake and cheap pre-filtering

Every candidate should be frozen immediately as a patch artifact, hashed, and associated with its base commit. Do not verify directly in the agent's live worktree because it may continue changing.

Initial pre-filters can reject:

- Empty or malformed diff.
- Invalid paths or changes outside allowed repository paths.
- Excessive file count or patch size, using documented configurable limits.
- Binary artifacts where not allowed.
- Python syntax errors in changed files.
- Agent writes that escaped the worktree.

Pre-filters are cost-saving checks, not correctness proof. Log rejection rule, evidence, and patch hash. Do not reject a valid large refactor merely because it exceeds a default threshold; allow the user to configure thresholds.

**Exit gate:** all candidates are immutable, attributable, reviewable, and safe to verify independently of agent edits.

## 12. Phase 5 — Incremental verification, then full confirmation

Implement verification as separate stages:

1. **Pre-filter:** diff integrity, path policy, syntax, and size checks.
2. **Selected tests:** tests selected for the candidate, or a broader fallback when mapping is unknown.
3. **Full suite:** configured repository-wide confirmation.

Treat test selection as an optimization, never as proof. Start with a conservative integration such as pytest-testmon or known project-specific test commands, then compare selection outcomes against full-suite results. A coverage map can come later after you can handle test contexts, subprocesses, generated code, dynamic imports, and stale coverage data.

Record for every stage:

- Candidate and patch hash.
- Exact command and working directory.
- Selection method and selected test list, when available.
- Exit status, duration, bounded output artifact, and classification.
- Whether a retry occurred and why.

Distinguish deterministic test failures from flaky tests, missing dependencies, setup errors, timeouts, and infrastructure failures. Retry only under a bounded, explicit policy. A selected-test pass should queue full confirmation; it cannot make the candidate a winner.

**Exit gate:** selector misses and unsupported mappings are visible, full-suite confirmation is mandatory, and a full-suite failure allows the race to continue.

## 13. Phase 6 — Arbiter, winner, and loser cancellation

Implement two explicit state machines and reject invalid transitions. Run state and candidate state are independent because multiple candidates may verify concurrently:

```text
Run:       PREPARING -> RUNNING -> EXHAUSTED | CANCELLED | FAILED
Candidate: RECEIVED -> PREFILTERING -> SELECTED_TESTS -> CONFIRMING -> WON
                                      \-> REJECTED
```

Arbitration behavior:

- Candidate verification uses the frozen patch artifact.
- A selected-test pass queues full-suite confirmation.
- A failed full suite rejects that candidate and lets other agents continue.
- A successful full suite atomically records the winner, closes candidate intake, and initiates idempotent cancellation of remaining agents.
- Verify child processes have exited before reporting cancellation complete.
- Do not let a late event from a canceled agent change the result.
- Ties use a deterministic, documented rule; smaller diff can be a tie-break but is not evidence of quality.
- If the event store cannot durably record the transition, stop safely or mark the run unverifiable.

Do not immediately cancel every other agent merely because one candidate passed selected tests. The winner condition is full-suite confirmation. If confirmation is slow, the product can later offer a user-selected latency policy, but it must not silently trade away the default correctness gate.

Integration should check that the destination branch still matches the expected base. Prefer exporting a patch or creating a new branch. If the target moved or has local changes, stop and present the conflict for human resolution.

**Exit gate:** only a full-suite pass can produce `WON`; cancellation and integration preserve user state.

## 14. Phase 7 — SQLite trace, benchmark harness, and safe reporting

SQLite is the authoritative trace store. Events are append-only; agent and
candidate tables are projections updated in the same transaction. The database
uses WAL mode, a busy timeout, per-run sequences allocated inside
`BEGIN IMMEDIATE`, bounded payloads, and atomic winner/event persistence.
JSONL is a derived export, not a second runtime source of truth.

First make the CLI useful without a UI. Provide commands to ingest and verify
traces, list runs, inspect snapshots, list events, export event JSONL, and
write a self-contained HTML report. Strip terminal control sequences before
printing untrusted agent or test output, and authorize artifacts against their
owned run directory.

Next add the benchmark harness. It must run the single-attempt,
sequential-retry, parallel-wait-all, RaceGreen, and ablation arms in
interleaved order across tasks and repeats. Record environment/configuration
fingerprints, verification outcomes, cost, cancellation, full-suite runs
avoided, resolved status, false winners, and failures in SQLite. Analysis must
read this store.

An optional live web interface may follow the static report. If added, it must
bind to loopback only, validate Host and Origin, require a random session
token, send restrictive security headers, serve artifacts as plain-text
downloads, and replay persisted events per-client without making the browser
authoritative.

**Exit gate:** SQLite verification passes, trace projections can be checked
against events, CLI/report output is safe, benchmark records are reproducible,
and no UI or report loses state or exposes artifacts outside its run directory.

## 15. Phase 8 — Hardening and release

Before packaging a first release:

- Review filesystem boundaries, symlink handling, process launch, network policy, secret redaction, and cancellation.
- Confirm no path can delete outside RaceGreen-owned run directories.
- Verify database migrations from the previous release and recovery after interrupted writes.
- Pin dependencies and review licenses, vulnerabilities, and update process.
- Test on a clean WSL2 environment with no GPU and with the optional GPU available.
- Test a dirty source repo, no-network configuration, rate-limit response, flaky test, timeout, disk-full condition, cancellation, and full-suite rejection.
- Benchmark the same tasks with sequential and racing modes at equivalent total budgets.
- Publish median and range, success rate, cost, false-winner rate, failed runs, test-selection fallback rate, and cancellation latency.
- Document supported OS, Python versions, provider setup, permissions, limitations, data retention, and troubleshooting.
- Keep release artifacts reproducible and create a versioned changelog.

Do not state that speed improved unless the benchmark shows it, and do not report only successful runs. A useful primary metric is **verified time-to-patch**, alongside success rate and cost per task.

## 16. Suggested first-run experience

The eventual first-use flow should be explicit:

1. `racegreen doctor` checks Git, Python, disk space, provider configuration, and optional GPU visibility.
2. `racegreen init` creates a local config with safe defaults and no embedded secrets.
3. User runs `racegreen run ... --agents 2` on a clean or explicitly acknowledged repository state.
4. CLI shows each agent's budget, workspace, and current state.
5. Candidate events show pre-filter and selected-test outcomes separately.
6. Full-suite confirmation determines whether a candidate wins.
7. CLI presents the patch, base commit, full-suite evidence, cost, and a command to inspect or apply it on a new branch.
8. `racegreen clean` lists owned resources before removing stale run artifacts; source repositories are never part of automatic cleanup.

## 17. Completion checklist

### Product behavior

- [ ] Run records base commit and configuration fingerprint.
- [ ] Each agent has an isolated worktree.
- [ ] Candidate patches are immutable and hashed.
- [ ] Cheap filters and selected tests produce traceable evidence.
- [ ] Full-suite confirmation is required for a winner.
- [ ] Failed confirmation resumes the race when budget remains.
- [ ] Winner cancellation stops child processes and blocks late candidates.
- [ ] Patch export or new-branch integration checks destination state.

### Security and reliability

- [ ] Path traversal and symlink escape are blocked.
- [ ] Commands use safe argument handling and a minimal environment.
- [ ] Credentials are never stored in logs, events, prompts, or artifacts.
- [ ] Network policy and local UI binding are explicit.
- [ ] Cleanup touches only positively identified RaceGreen-owned resources.
- [ ] Flaky, infrastructure, timeout, and deterministic failures are distinct.
- [ ] Storage failures cannot result in an untraceable winner.

### Operations and evidence

- [ ] WSL/Linux filesystem workflow is documented.
- [ ] CLI works without GPU or Docker.
- [ ] Peak memory, cost, and concurrency are bounded and visible.
- [ ] Benchmark compares equal-budget sequential and racing runs.
- [ ] Release claims match recorded measurements.
- [ ] User can inspect, replay, export, and clean a run safely.

## 18. Further reading

- [Git worktree reference](https://git-scm.com/docs/git-worktree)
- [Microsoft WSL filesystem guidance](https://learn.microsoft.com/en-us/windows/dev-environment/wsl/interop)
- [NVIDIA CUDA on WSL guide](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)
- [Superset repository](https://github.com/superset-sh/superset)
- [Superset orchestration documentation](https://docs.superset.sh/orchestration)
- [PerfAgent paper](https://arxiv.org/abs/2607.19653) — related verifier-in-the-loop software optimization work; verify exact claims before citing its numbers.
- [Kozuchi Agent paper](https://arxiv.org/abs/2608.15579) — related language-agnostic repair and test-time selection work; verify exact claims before citing its numbers.

Research novelty and specific benchmark numbers should be treated as open questions until each source and claim has been checked directly. A careful description of what RaceGreen does is more credible than an unsupported “first” claim.
