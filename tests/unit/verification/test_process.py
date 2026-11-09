import os
import sys
import threading
import time
from pathlib import Path

from racegreen.verification.env import (
    cleanup_verification_env,
    sandbox_env,
    verification_env,
)
from racegreen.verification.process import run_bounded_command


def test_bounded_command_limits_output(tmp_path: Path) -> None:
    result = run_bounded_command(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('x' * 1_000_000); "
            "sys.stderr.write('y' * 1_000_000)",
        ],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout_seconds=10,
        max_output_bytes=1000,
    )

    assert result.returncode == 0
    assert len(result.stdout) <= 1000
    assert len(result.stderr) <= 1000


def test_bounded_command_times_out_and_terminates(tmp_path: Path) -> None:
    started = time.monotonic()
    result = run_bounded_command(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout_seconds=0.1,
        max_output_bytes=1000,
    )

    assert result.timed_out is True
    assert result.cancelled is False
    assert time.monotonic() - started < 5


def test_verification_environment_cleanup_removes_private_tempdir() -> None:
    env = verification_env()
    private = Path(env["TMPDIR"])
    assert private.is_dir()
    cleanup_verification_env(env)
    assert not private.exists()


def test_sandbox_environment_uses_container_tmp() -> None:
    env = verification_env()
    try:
        adapted = sandbox_env(env)
        assert adapted["TMPDIR"] == "/tmp"
        assert adapted["TEMP"] == "/tmp"
        assert adapted["TMP"] == "/tmp"
        assert env["TMPDIR"] != "/tmp"
    finally:
        cleanup_verification_env(env)


def test_bounded_command_cancellation_terminates(tmp_path: Path) -> None:
    cancellation = threading.Event()
    timer = threading.Timer(0.1, cancellation.set)
    started = time.monotonic()
    timer.start()
    try:
        result = run_bounded_command(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=tmp_path,
            env=os.environ.copy(),
            timeout_seconds=30,
            max_output_bytes=1000,
            cancel_event=cancellation,
        )
    finally:
        timer.cancel()

    assert result.cancelled is True
    assert result.timed_out is False
    assert time.monotonic() - started < 5
