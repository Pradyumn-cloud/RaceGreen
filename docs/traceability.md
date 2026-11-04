# Phase 8 traceability matrix

This matrix is the release audit index. A requirement is not considered
covered by a model claim; it needs an implementation and an executable check.

| Requirement | Implementation | Check |
|---|---|---|
| Worktree ownership and dirty-tree refusal | `workspace/manager.py` | `tests/unit/test_workspace.py` |
| Candidate patch hashing | `agent/candidate.py` | `tests/unit/test_candidate.py` |
| Full-suite winner confirmation | `verification/` and `arbiter/` | `tests/unit/verification/` |
| Atomic winner persistence | `storage/db.py` | `tests/unit/arbiter/test_arbiter_cas.py` |
| SQLite integrity and trace projections | `storage/db.py` | `tests/unit/storage/test_db.py` |
| Artifact path authority | `storage/security.py` | `tests/unit/storage/test_db.py` |
| Terminal output sanitization | `storage/security.py` | `tests/unit/storage/test_db.py` |
| Bounded cancellation | `scheduler/cancellation.py` | `tests/unit/scheduler/test_cancellation.py` |
| Provider error handling | `agent/provider.py` | `tests/unit/test_provider.py` |
| No-API demonstration | `cli.py` and demo fixtures | `tests/unit/test_demo.py` |
| Clean installation | `.github/workflows/ci.yml` | CI workflow |
| Path authority property checks | `storage/security.py` | `tests/unit/test_release_hardening.py` |
| SQLite sequence and winner recovery checks | `storage/db.py` | `tests/unit/test_release_hardening.py` |
| Benchmark arm interleaving and replay metadata | `scripts/benchmark.py` | `tests/unit/test_benchmark_runner.py` |
| Secret and dependency audit gates | `.github/workflows/ci.yml` | CI workflow |

Known gaps are tracked as issues rather than hidden in this document. Agent
command network isolation is intentionally not claimed: worktrees do not
provide an OS sandbox.
