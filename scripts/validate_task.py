"""Validate a benchmark task and emit one machine-readable JSONL result."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import yaml

PROJECT_ROOT = Path(__file__).parents[1].resolve()


def _argv(value: object) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ValueError("commands must be non-empty argument arrays")
    return value


def _run(command: Sequence[str], cwd: Path) -> tuple[bool, float, str, bool]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(command), cwd=cwd, check=False, capture_output=True, text=True, timeout=300
        )
    except subprocess.TimeoutExpired as error:
        output = str(error.stdout or "") + str(error.stderr or "")
        return False, time.monotonic() - started, output[-4000:], True
    return (
        completed.returncode == 0,
        time.monotonic() - started,
        (completed.stdout + completed.stderr)[-4000:],
        False,
    )


def _failure_signature(output: str) -> tuple[str, ...]:
    """Stable pytest failure identities for F2P/P2P comparisons."""
    return tuple(sorted(set(re.findall(r"FAILED ([^\s]+)", output))))


def _pristine(repo: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and not result.stdout.strip()


def environment_fingerprint() -> str:
    payload = {
        "python": sys.version,
        "platform": platform.platform(),
        "executable": os.path.abspath(sys.executable),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _oracle_path(relative: object) -> Path:
    if not isinstance(relative, str):
        raise ValueError("oracle patch must be a relative path")
    patch = (PROJECT_ROOT / relative).resolve()
    if PROJECT_ROOT not in patch.parents:
        raise ValueError("oracle patch escapes the repository")
    return patch


def _owned_remove(path: Path, root: Path) -> None:
    """Remove only a validator-owned child below the exact benchmark root."""
    path = path.resolve()
    root = root.resolve()
    if path.parent != root or not (path / ".racegreen-owned").is_file():
        raise RuntimeError(f"refusing to remove unowned validation path: {path}")
    for child in path.rglob("*"):
        if child.is_symlink() or child.is_file():
            child.unlink()
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_dir():
            child.rmdir()
    path.rmdir()


def _assert_no_leakage(repo: Path) -> None:
    if (repo / "tests" / "test_held_out.py").exists():
        raise RuntimeError("agent fixture leaks held-out tests")
    if (repo / "oracle").exists() or (repo / "decoys").exists():
        raise RuntimeError("agent fixture leaks oracle or decoy patches")
    history = subprocess.run(
        ["git", "-C", str(repo), "rev-list", "--count", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    if history.stdout.strip() != "1":
        raise RuntimeError("agent fixture contains fix history")


def _validate_decoys(task: dict[str, Any], repo: Path, visible: list[str],
                     held_out: list[str], root: Path) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for index, relative in enumerate(task.get("decoy_patches", [])):
        patch = (PROJECT_ROOT / "benchmarks" / "tier1" / relative).resolve()
        target = root / f"decoy-{task['task_id']}-{index}"
        subprocess.run(["git", "clone", "--no-hardlinks", str(repo), str(target)],
                       check=True, capture_output=True, text=True)
        try:
            applied = subprocess.run(
                ["git", "-C", str(target), "apply", str(patch)],
                check=False, capture_output=True, text=True,
            ).returncode == 0
            visible_passed = applied and _run(visible, target)[0]
            held_out_passed = applied and _run(held_out, target)[0]
            results.append({
                "patch": relative, "applied": applied,
                "visible_passed": visible_passed, "held_out_passed": held_out_passed,
                "valid": applied and visible_passed and not held_out_passed,
            })
        finally:
            if target.exists():
                _owned_remove(target, root)
    return {
        "decoy_count": len(results),
        "decoy_valid": all(item["valid"] for item in results),
        "decoys": results,
    }


def validate(task: dict[str, Any], repo: Path) -> dict[str, Any]:
    for relative in task.get("decoy_patches", []):
        decoy = (PROJECT_ROOT / "benchmarks" / "tier1" / relative).resolve()
        if PROJECT_ROOT not in decoy.parents or not decoy.is_file():
            raise RuntimeError(f"missing declared decoy patch: {relative}")
    commands = task["commands"]
    full = _argv(commands["full"])
    visible = _argv(commands["visible"])
    held_out = _argv(commands["held_out"])
    if not _pristine(repo):
        raise RuntimeError("repository must be pristine before validation")
    baseline: list[bool] = []
    baseline_signatures: list[tuple[str, ...]] = []
    baseline_timeouts: list[bool] = []
    duration = 0.0
    for _ in range(3):
        if not _pristine(repo):
            raise RuntimeError("repository became dirty during baseline")
        passed, elapsed, output, timed_out = _run(full, repo)
        baseline.append(passed)
        baseline_signatures.append(_failure_signature(output))
        baseline_timeouts.append(timed_out)
        duration += elapsed
    flaky = len(set(baseline)) > 1
    visible_passed, _, _, visible_timeout = _run(visible, repo)
    held_out_baseline, _, _, held_out_timeout = _run(held_out, repo)
    oracle_passed = False
    oracle_signature: tuple[str, ...] = ()
    oracle_full_timeout = False
    oracle_held_out_timeout = False
    mutation_killed = False
    mutation_status = "not-run"
    decoy_data = {"decoy_count": 0, "decoy_valid": True, "decoys": []}
    patch = _oracle_path(task["oracle"]["patch"])
    validation_root = (PROJECT_ROOT / ".bench" / "validation").resolve()
    validation_root.mkdir(parents=True, exist_ok=True)
    decoy_data = _validate_decoys(task, repo, visible, held_out, validation_root)
    isolated = (validation_root / task["task_id"]).resolve()
    if isolated.exists():
        _owned_remove(isolated, validation_root)
    subprocess.run(
        ["git", "clone", "--no-hardlinks", str(repo), str(isolated)],
        check=True,
        capture_output=True,
        text=True,
    )
    if patch.is_file() and _pristine(isolated):
        if patch.stat().st_size == 0:
            oracle_full, _, oracle_output, oracle_full_timeout = _run(full, isolated)
            oracle_held_out, _, _, oracle_held_out_timeout = _run(held_out, isolated)
            oracle_signature = _failure_signature(oracle_output)
            same_base_failure = (
                not all(baseline)
                and bool(oracle_signature)
                and all(signature == oracle_signature for signature in baseline_signatures)
            )
            oracle_passed = oracle_held_out and (oracle_full or same_base_failure)
            bug = isolated / "bug.py"
            if bug.is_file():
                original = bug.read_text(encoding="utf-8")
                if "return 7" in original:
                    bug.write_text(original.replace("return 7", "return 8", 1), encoding="utf-8")
                    mutation_killed = not _run(visible, isolated)[0]
                    mutation_status = "killed" if mutation_killed else "survived"
                    bug.write_text(original, encoding="utf-8")
        else:
            check = subprocess.run(
                ["git", "-C", str(isolated), "apply", "--check", str(patch)], check=False
            )
            if check.returncode == 0:
                applied = subprocess.run(["git", "-C", str(isolated), "apply", str(patch)], check=False)
                if applied.returncode == 0:
                    oracle_full, _, oracle_output, oracle_full_timeout = _run(full, isolated)
                    oracle_held_out, _, _, oracle_held_out_timeout = _run(held_out, isolated)
                    oracle_signature = _failure_signature(oracle_output)
                    same_base_failure = (
                        not all(baseline)
                        and bool(oracle_signature)
                        and all(signature == oracle_signature for signature in baseline_signatures)
                    )
                    oracle_passed = oracle_held_out and (oracle_full or same_base_failure)
    _owned_remove(isolated, validation_root)
    return {
        "event": "task_result",
        "task_id": task["task_id"],
        "environment_fingerprint": environment_fingerprint(),
        "pristine": True,
        "baseline_runs": baseline,
        "flaky": flaky,
        "known_flaky": bool(task.get("known_flaky", False)),
        "flaky_classification": "known" if task.get("known_flaky", False) else (
            "observed" if flaky else "none"
        ),
        "failure_semantics": "F2P" if not all(baseline) and oracle_passed else (
            "P2P" if all(baseline) and oracle_passed else "rejected"
        ),
        "timeout": any(baseline_timeouts)
        or visible_timeout
        or held_out_timeout
        or oracle_full_timeout
        or oracle_held_out_timeout,
        "visible_baseline_passed": visible_passed,
        "held_out_baseline_passed": held_out_baseline,
        "oracle_passed": oracle_passed,
        "held_out_discriminated": (
            not bool(task.get("control", False))
            and not held_out_baseline
            and oracle_passed
        ),
        "duration_seconds": duration,
        "control": bool(task.get("control", False)),
        "mutation_status": mutation_status,
        "mutation_killed": mutation_killed,
        **decoy_data,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="YAML manifest file or directory")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(".bench/results.jsonl"))
    args = parser.parse_args()
    manifest_paths = (
        sorted(args.manifest.glob("*.yaml"))
        if args.manifest.is_dir()
        else [args.manifest]
    )
    tasks: list[dict[str, Any]] = []
    for manifest_path in manifest_paths:
        with manifest_path.open(encoding="utf-8-sig") as stream:
            task = yaml.safe_load(stream)
        if not isinstance(task, dict):
            raise ValueError(f"Manifest must contain a mapping: {manifest_path}")
        tasks.append(task)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8") as output:
        for task in tasks:
            generated_paths: list[Path] = []
            try:
                if task.get("repository", {}).get("kind") == "synthetic":
                    sys.path.insert(0, str(PROJECT_ROOT))
                    from benchmarks.tier1.generate import materialize_task

                    generated = (PROJECT_ROOT / ".bench" / "fixtures" / str(task["task_id"])).resolve()
                    generated.parent.mkdir(parents=True, exist_ok=True)
                    if generated.exists():
                        _owned_remove(generated, generated.parent)
                    task["_include_held_out"] = False
                    materialize_task(task, generated)
                    generated_paths.append(generated)
                    _assert_no_leakage(generated)
                    evaluation = generated.parent / (str(task["task_id"]) + "-evaluation")
                    if evaluation.exists():
                        _owned_remove(evaluation, generated.parent)
                    task["_include_held_out"] = True
                    materialize_task(task, evaluation)
                    generated_paths.append(evaluation)
                    task_repo = evaluation
                else:
                    task_repo = args.repo.resolve()
                result = validate(task, task_repo)
            except (KeyError, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                result = {"event": "task_result", "task_id": task.get("task_id"), "error": str(error)}
            finally:
                fixture_root = PROJECT_ROOT / ".bench" / "fixtures"
                for path in reversed(generated_paths):
                    if path.exists():
                        _owned_remove(path, fixture_root)
            output.write(json.dumps(result, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
