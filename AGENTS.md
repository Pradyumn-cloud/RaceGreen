# RaceGreen Engineering Instructions

These instructions apply to the entire RaceGreen repository. Follow them for every change. If a task conflicts with a requirement here, preserve security, data integrity, and user control, then explain the conflict in the completion summary.

## 1. Product mission and design principles

RaceGreen runs a bounded race of coding agents in isolated Git worktrees, verifies submitted patches incrementally, confirms a potential winner with the configured full test suite, and stops unnecessary work after confirmation.

Prioritize, in order:

1. Correctness and user data integrity.
2. Isolation between agents, runs, and the host.
3. Reproducible verification and explainable decisions.
4. Bounded resource and provider cost usage.
5. Measured performance improvements.

Do not claim a patch is correct because an agent says so, because selected tests pass, or because a verifier model approves it. The configured full-suite confirmation is required before the arbiter can declare a winner. Preserve human review in the integration path by default.

## 2. Required development workflow

Before editing:

- Read this file and the relevant project documentation, configuration, and adjacent code.
- Identify the user-visible behavior, failure modes, affected components, and acceptance criteria.
- Inspect repository state before cleanup, branch operations, or patch application. Never overwrite unrelated user changes.
- Prefer a small, coherent change over broad refactoring.

While editing:

- Keep orchestration decisions deterministic where possible and record the inputs to each decision.
- Use typed interfaces and explicit state transitions. Avoid hidden global state and implicit cross-agent communication.
- Keep external-provider, filesystem, process, scheduler, verifier, persistence, and UI concerns behind narrow interfaces.
- Add or update documentation when behavior, configuration, security assumptions, or operator actions change.
- Do not add dependencies without documenting the reason, maintenance status, license, and security impact.

Before completion:

- Run only the checks appropriate to the task and report exactly what was run and its result. Do not claim checks passed if they were not run.
- Review the final diff for accidental files, secrets, generated artifacts, weakened safety controls, and unrelated edits.
- Summarize behavior changes, verification, known limitations, and required operator action.

## 3. Code quality and formatting

- Follow the repository's existing formatter, linter, type checker, and naming conventions. Do not introduce a competing style system.
- Prefer clear names, small functions, explicit errors, and narrow types over clever abstractions.
- Keep public APIs and persisted event schemas versioned and documented. Make schema evolution backward compatible where practical.
- Use structured logging with stable event names and IDs. Do not log credentials, access tokens, private keys, or unredacted environment dumps.
- Validate all external inputs at boundaries: CLI arguments, config files, agent events, model responses, filesystem paths, and UI requests.
- Use UTC timestamps internally; render local time only at presentation boundaries.
- Ensure asynchronous tasks have explicit ownership, cancellation, timeouts, and cleanup behavior.
- Do not use broad exception swallowing. Preserve the cause and provide actionable context without leaking secrets.

## 4. Security and isolation requirements

Treat agent-generated code, model output, repository contents, test output, and task descriptions as untrusted input.

### Filesystem and Git

- Resolve and validate paths before reading, writing, deleting, or extracting files. Reject paths that escape the run's allowed root, including traversal and symlink escapes.
- Each agent may write only inside its own assigned worktree and run-scoped artifact directory.
- Never delete or reset the user's source repository, current branch, or unrelated worktree. Cleanup may remove only resources RaceGreen created and can positively identify.
- Capture the starting commit and working-tree status. Refuse unsafe integration if the target has changed unexpectedly; provide a patch or new branch for review.
- Never run destructive Git operations such as `reset --hard`, forced checkout, or forced worktree removal on user-owned state.
- Do not trust repository scripts, hooks, submodules, or setup commands. Make execution policy explicit and require opt-in for risky setup actions.
- Do not place secrets in worktrees, prompts, logs, SQLite, crash reports, or artifacts. Redact known secret patterns before persistence.

### Processes and commands

- Launch commands with argument arrays, never shell-concatenated strings. If a shell is necessary, use an explicit, reviewed command policy.
- Use a minimal environment allowlist; do not inherit all host environment variables by default.
- Set working directory, timeout, resource limits where supported, and process-group ownership explicitly.
- Cancellation must terminate the full process tree, wait for exit, escalate after a bounded grace period, and record the outcome.
- Enforce global and per-agent limits for wall time, tokens/cost, output bytes, open files, and concurrent processes where feasible.
- Make network access an explicit policy. Do not silently allow agents to upload repository content or access cloud metadata endpoints.
- Clearly document that worktrees are version-control isolation, not a security sandbox. For untrusted code, use an appropriate OS-level sandbox/container/VM and validate its threat model.

### Providers, credentials, and UI

- Keep provider credentials in an OS credential store or protected environment injection; never write them to config files or logs.
- Use TLS for remote providers. Apply bounded retries with backoff and avoid retrying non-idempotent operations blindly.
- Protect local web endpoints against cross-site requests, unauthorized origins, unsafe filesystem paths, and unintended remote binding. Bind to loopback by default.
- Escape untrusted text in rendered HTML. Do not execute HTML, Markdown links, or scripts from agent output.
- The UI must display agent, test, and arbiter state as untrusted claims backed by recorded events.

## 5. Race, candidate, and verification invariants

These are product correctness requirements, not optional implementation details:

1. Every run records an immutable base commit and configuration fingerprint.
2. Every candidate is an immutable patch snapshot with a content hash; verification never runs against a moving worktree.
3. A candidate cannot enter `WON` based only on pre-filters, selected tests, agent claims, or model review.
4. Only a successful configured full-suite confirmation may transition a candidate to `WON`.
5. A test-selection miss or unsupported mapping must broaden verification or fall back to the full suite; it must not silently imply confidence.
6. Flaky or infrastructure failures are distinguishable from deterministic test failures. Retries are bounded and recorded.
7. Candidate verification is isolated from concurrent edits. Each test process is tied to a candidate ID and patch hash.
8. Cancellation is idempotent. A canceled agent cannot submit a late candidate that changes the winner.
9. Tie-breaking is documented, deterministic, and never presented as correctness evidence. Prefer the earlier fully confirmed candidate; use diff size only as a disclosed tie-break when confirmation ordering is genuinely simultaneous.
10. If full-suite confirmation fails, reject that candidate and allow the race to continue unless the run budget is exhausted.
11. Winner application is a separate operation with a fresh target-state check. Default to a reviewable patch or new branch, not an automatic commit to the user's current branch.
12. Event persistence failure must not silently produce an untraceable winner. Stop safely or mark the run as unverifiable.

Use explicit transitions, for example:

```text
PREPARING -> RUNNING -> CANDIDATE -> PREFILTERING
          -> SELECTED_TESTS -> CONFIRMING -> WON
                                         -> REJECTED -> RUNNING
          -> EXHAUSTED | CANCELLED | FAILED
```

Reject invalid transitions and persist transition causes.

## 6. Resource profile: Windows, WSL2, RTX 4050, 16 GB RAM

- Develop and run Linux tooling in WSL2. Keep repository clones, worktrees, environments, and caches on the WSL Linux filesystem rather than `/mnt/c` for performance.
- Treat local inference as optional. Start with one API-backed model and a small concurrency limit; measure system RAM, GPU memory, CPU, provider rate limits, and spend before increasing agent count.
- Do not assume every RTX 4050 has the same VRAM capacity. Detect and report available GPU memory; do not make GPU availability a requirement for core operation.
- Never start multiple large local models automatically. Apply explicit memory and concurrency safeguards.
- Share only immutable or read-only dependency caches. Do not share a writable virtual environment between agents.
- Make Docker, container, and VM support optional. Do not assume KVM or microVM acceleration is available under Windows/WSL2.
- The CLI must remain usable without a GPU, dashboard, or Docker.

## 7. Development phases and exit gates

Work in this order unless a documented dependency requires otherwise. Each phase ends with a reviewable state and a short record of evidence.

### Phase 0 — Product and benchmark definition

Define supported repository types, agent protocol, verifier policy, threat model, baseline tasks, cost limits, and success metrics. Record exact sources for any public performance or novelty claim.

**Exit gate:** benchmark fixtures, measurable baseline, and written threat model exist; unsupported claims are excluded.

### Phase 1 — Safe workspace lifecycle

Implement repo inspection, base-state capture, worktree creation, unique run/agent IDs, scoped artifact paths, cleanup, and interruption recovery.

**Exit gate:** repeated lifecycle operations preserve user state and recover cleanly from partial setup.

### Phase 2 — Single-agent vertical slice

Implement one provider adapter, bounded process lifecycle, candidate snapshots, pre-filter, test execution, event persistence, and patch export.

**Exit gate:** start, observe, cancel, inspect, and replay a run; timeout and provider errors produce explicit states.

### Phase 3 — Multi-agent scheduler

Add bounded concurrency, per-agent/global budgets, candidate backpressure, process-tree cancellation, and late-event rejection.

**Exit gate:** cancellation leaves no owned processes running; every cost and outcome maps to a run and agent.

### Phase 4 — Incremental verification

Add conservative language-specific pre-filters and selected-test integration with a full-suite fallback. Measure selection coverage and time saved against full runs.

**Exit gate:** selector limitations are explicit; no selected-test-only path can declare a winner.

### Phase 5 — Arbiter and safe integration

Add immutable verification snapshots, retries for classified flaky failures, full-suite confirmation, deterministic arbitration, loser cancellation, and reviewable patch/new-branch output.

**Exit gate:** failed confirmation returns the race to a valid state; successful confirmation is required for `WON`; integration preserves changed target state.

### Phase 6 — Live UI and replay

Add loopback-only SSE progress, candidate diffs, test status, event timeline, reconnection, and log/artifact navigation.

**Exit gate:** UI reconnects from persisted state, escapes untrusted output, and does not become the only source of truth.

### Phase 7 — Hardening and release

Add language/provider adapters, packaging, migration policy, security review, performance profiles, docs, and release automation.

**Exit gate:** supported configurations are documented and reproducible; dependency and security review is complete; benchmark results include failures and costs.

## 8. Validation expectations

For behavior changes, add or update checks at the narrowest useful layer and include failure paths. Important areas include:

- Worktree creation, collisions, submodules, dirty repositories, interruption, cleanup, and symlink traversal.
- Invalid and concurrent arbiter transitions; candidate hash consistency; stale and late events.
- Process-group termination, timeouts, output limits, and cancellation races.
- Test-selection misses, flaky failures, full-suite rejection, and infrastructure failure classification.
- SQLite migration, disk-full behavior, redaction, and trace replay.
- Provider throttling, malformed responses, credential absence, and retry limits.
- Loopback binding, origin validation, HTML escaping, and path authorization.

Do not weaken or skip a required verification layer to make a benchmark look faster. Report selected-test and full-suite results separately.

## 9. Documentation and completion format

Keep setup instructions aligned with supported environments. Document commands, configuration fields, defaults, permissions, limitations, and cleanup behavior.

For each completed engineering task, report:

- What changed and why.
- Files or components affected.
- Checks run and their results, or why they were not run.
- Security, reliability, or compatibility implications.
- Remaining limitations and next step, if any.

Never report an unverified assumption as a measured result.
