# Upcoming Work

This file tracks known hardening, correctness, and usability work that is not
yet complete. It is a backlog, not a release promise. Priorities reflect risk to
verification trust and operator experience.

## Priority 0: Agent protocol reliability

### 1. Handle malformed tool-call responses explicitly

**Problem:** A provider response that looks like a tool call but contains invalid
JSON is currently treated as a normal final answer. The runner returns
`COMPLETED` when `response.tool_calls` is empty, so the agent can stop without
submitting a candidate.

**Impact:** A local model can edit a worktree, produce a truncated tool call, and
end the run with no candidate. The run correctly ends `EXHAUSTED`, but the cause
is not visible in the summary and the work is discarded.

**Proposed change:** Detect content that begins with `{` or `[` but does not
parse as a tool call. Send a bounded corrective message asking for valid JSON or
`submit_candidate`. After the retry budget is exhausted, finish with a distinct
`PROTOCOL_ERROR` status and record the parse failure.

**Acceptance criteria:**

- A malformed tool-call response produces a bounded retry, not an immediate
  `COMPLETED`.
- Exhausted retries produce a clear protocol failure with the parse error
  recorded.
- Valid JSON-in-content tool calls continue to work.
- The behavior is covered by unit tests using scripted providers.

**Affected areas:** `src/racegreen/agent/runner.py`,
`src/racegreen/agent/provider.py`, `src/racegreen/agent/models.py`,
`tests/unit/test_agent.py`.

### 2. Add a bounded submission nudge

**Problem:** The agent can change files and finish without calling
`submit_candidate`. The runner does not ask again, even when the worktree has
changed.

**Impact:** Useful work can be discarded because of one missing tool call. This
is especially common with smaller local models.

**Proposed change:** When a response has no tool calls and the worktree differs
from the base commit, send one bounded reminder that the task is not complete
until `submit_candidate` is called. Do not auto-submit by default; keep explicit
submission as the protocol.

**Acceptance criteria:**

- A changed worktree plus a no-tool-call response triggers at most one nudge.
- A candidate submitted after the nudge follows the normal intake and
  verification path.
- An unchanged worktree or an explicit final answer is not nudged.
- The nudge and its outcome are recorded in the event trace.

**Affected areas:** `src/racegreen/agent/runner.py`,
`src/racegreen/agent/candidate.py`, `tests/unit/test_agent.py`.

### 3. Record a clear cause for `EXHAUSTED`

**Problem:** The final run summary reports `state: exhausted` with an empty
verification list but does not say why no winner was declared.

**Impact:** Operators must inspect raw events to distinguish "no candidate was
submitted" from "all candidates were rejected" or "the full-suite budget was
exhausted."

**Proposed change:** Add a structured `cause` field to the run summary and the
`run_finished` event, for example `no_candidate_submitted`,
`no_candidate_confirmed`, `full_suite_budget_exhausted`, or `user_cancelled`.
Include per-agent submission status.

**Acceptance criteria:**

- Every terminal run has a machine-readable cause.
- The dashboard and `run-show` display the cause.
- Existing persisted runs remain readable.

**Affected areas:** `src/racegreen/scheduler/runner.py`,
`src/racegreen/storage/db.py`, `src/racegreen/dashboard/`.

## Priority 1: Verification reporting correctness

### 4. Reconcile per-agent verification status

**Problem:** `run_finished.results` can retain `"verification": "queued"` after
the verification pipeline has already recorded a winner. The top-level
`verification` array is correct, but the per-agent summary is stale.

**Impact:** The run summary and dashboard can present contradictory states for
the same candidate.

**Proposed change:** Reconcile `results` with the final verification results
before writing `run_finished`, or replace the submission-time status with a
terminal status such as `won`, `rejected`, or `cancelled`.

**Acceptance criteria:**

- A completed race never reports `queued` for a candidate with a terminal
  verification result.
- The summary and the `verification` array agree.
- Regression coverage asserts the final per-agent status.

**Affected areas:** `src/racegreen/scheduler/runner.py`,
`src/racegreen/verification/pipeline.py`,
`tests/unit/scheduler/test_race_pipeline.py`.

### 5. Correct `target_tests_passed` semantics

**Problem:** When `test_command` and `full_suite_command` are the same, the
target-test stage is skipped and `target_tests_passed` remains `false`, even
when the full suite passes and the candidate wins.

**Impact:** A winning verification record appears to contain a failed target-test
signal. This is misleading during review and auditing.

**Proposed change:** Represent the field as `true` when the full suite is the
target and it passed, or replace the boolean with a tri-state value such as
`passed`, `failed`, and `not_run`. Prefer `not_run` over a false failure signal.

**Acceptance criteria:**

- A clean full-suite win does not report `target_tests_passed: false`.
- A genuinely failed target stage still reports a failure.
- The report and dashboard use the same semantics.

**Affected areas:** `src/racegreen/verification/runner.py`,
`src/racegreen/report.py`, `src/racegreen/dashboard/`.

## Priority 1: Provider compatibility

### 6. Send valid OpenAI-compatible tool conversation messages

**Problem:** After a tool call, the runner appends only a `tool` message with
content. It does not append the assistant message containing `tool_calls` and it
does not include a `tool_call_id`. Ollama tolerates this, but a strict
OpenAI-compatible endpoint may reject the conversation or ignore the tool
result.

**Impact:** Hosted-provider support may fail even though local Ollama works.

**Proposed change:** Maintain a standards-compliant message history:

- append the assistant message with its tool calls;
- append one tool result per call with the matching `tool_call_id`;
- keep provider-specific fallbacks behind the adapter.

**Acceptance criteria:**

- The provider adapter sends a valid tool-call conversation to a strict
  OpenAI-compatible endpoint.
- Multiple tool calls in one response are matched to the correct results.
- Local Ollama behavior remains supported.
- Provider tests assert the outgoing message shape.

**Affected areas:** `src/racegreen/agent/runner.py`,
`src/racegreen/agent/provider.py`, `tests/unit/test_provider.py`.

## Priority 2: Agent tool safety

### 7. Add read-before-write or patch-based editing

**Problem:** The `edit` tool replaces an entire file. A model that does not read
the original file can overwrite unrelated content with placeholders.

**Impact:** Weak models produce destructive patches. Verification may reject
them, but the race wastes time and the behavior is easy to avoid.

**Proposed change:** Add a `str_replace` or patch-based edit tool, or require a
successful read of the target path before the first write. Keep whole-file
replacement available only as an explicit operation.

**Acceptance criteria:**

- The default edit path cannot silently replace an unread file.
- A read-before-write violation returns an actionable tool error.
- Existing targeted edits remain simple.
- Tool tests cover create, replace, and read-before-write cases.

**Affected areas:** `src/racegreen/agent/tools.py`,
`src/racegreen/agent/provider.py`, `tests/unit/test_tools.py`.

## Priority 2: CLI and documentation consistency

### 8. Use positional run IDs everywhere

**Problem:** Some operator-facing text and examples still use `--run-id`, while
the winner, report, event, and run-inspection commands take a positional run ID.

**Impact:** Operators copy a command that fails or is inconsistent with the CLI
help.

**Proposed change:** Standardize examples and generated next-step messages on
the positional form:

```bash
racegreen winner show <run-id>
racegreen winner export <run-id> --output winner.patch
racegreen run-show <run-id>
racegreen report <run-id> --output report.html
```

**Acceptance criteria:**

- Generated next-step text matches the actual CLI.
- README and operator docs use positional run IDs.
- A CLI test asserts the generated command text.

**Affected areas:** `src/racegreen/cli.py`, `README.md`, `docs/agent.md`.

### 9. Document winner branch naming

**Problem:** The branch command does not accept a `--branch` option. The branch
name is derived from the run ID as `racegreen/<run-id>`.

**Impact:** Operators may pass an unsupported option or look for the wrong branch
name.

**Proposed change:** Document the derived branch name and the optional
`--repository` argument. Add a `--branch` option only if custom naming is
required.

**Acceptance criteria:**

- Documentation shows the supported command and resulting branch name.
- CLI help and README agree.

**Affected areas:** `README.md`, `docs/agent.md`,
`src/racegreen/arbiter/integration.py`.

## Priority 2: Verification depth

### 10. Add a first-class held-out test workflow

**Problem:** The benchmark protocol requires held-out tests that agents do not
see, but the normal race workflow does not expose a first-class held-out
confirmation step.

**Impact:** A candidate can pass the visible suite and still fail on unseen
behavior. The benchmark documentation warns about this, but operators need a
repeatable workflow.

**Proposed change:** Add an optional held-out command or test path that is
applied only after the visible full suite passes, without exposing it to the
agent worktree. Record held-out evidence separately from visible-suite evidence.

**Acceptance criteria:**

- A winner can be required to pass both the visible full suite and a held-out
  suite.
- Held-out tests are never copied into agent worktrees or prompts.
- False-winner reporting distinguishes visible-only passes from held-out
  failures.

**Affected areas:** `src/racegreen/verification/`,
`src/racegreen/arbiter/`, `scripts/benchmark.py`, `docs/benchmark.md`.

### 11. Validate the Docker sandbox against a live daemon

**Problem:** The Docker verification sandbox is unit-tested but has not been
exercised against a live Docker daemon in the recorded benchmark.

**Impact:** The documented sandbox behavior is not yet backed by an end-to-end
run on a real Docker host.

**Proposed change:** Add an integration test or documented manual validation
that runs the sandbox with a prepared image, verifies the mount policy, confirms
`--network none`, and checks process cancellation.

**Acceptance criteria:**

- A live-daemon validation is recorded with the image, command, and result.
- The validation does not require Docker for normal RaceGreen operation.
- Documentation states exactly what was and was not validated.

**Affected areas:** `src/racegreen/verification/sandbox.py`,
`tests/unit/test_sandbox.py`, `docs/benchmark.md`.

## Priority 3: Local-model usability

### 12. Add model capability guidance and diagnostics

**Problem:** Small local models can fail at tool-call formatting, file editing,
and submission. The current diagnostics do not distinguish model capability
problems from repository or verification problems.

**Impact:** Operators may blame RaceGreen or the test suite when the model is the
limiting factor.

**Proposed change:** Document recommended model classes for tool use, add a
provider smoke test that exercises a real tool call, and surface protocol
failures separately from verification failures.

**Acceptance criteria:**

- A failed tool protocol is reported as a protocol failure, not a generic agent
  completion.
- Documentation explains the tradeoffs of small local models and recommends
  conservative concurrency.
- The provider smoke test can be run without starting a full race.

**Affected areas:** `src/racegreen/cli.py`,
`src/racegreen/agent/provider.py`, `README.md`, `docs/agent.md`.

## Known behavior that is not a defect

- `baseline_unavailable` is expected when the base commit has no runnable tests
  or no JUnit output. Commit a usable test suite before racing.
- A destructive candidate can win when the visible tests do not check the
  behavior that was deleted. RaceGreen verifies the tests it is given; test
  quality remains the operator's responsibility.
- Agent edits to test files and test configuration are rejected by the candidate
  pre-filter. Agents cannot delete or weaken tests through the normal path.
- A run that submits no confirmed candidate ends `EXHAUSTED`. This is the
  correct state; it is not a successful race.
- Worktrees provide Git isolation, not an operating-system security sandbox.
