from pathlib import Path

import pytest

from racegreen.verification.sandbox import DockerSandbox


def test_docker_sandbox_wraps_command_and_mounts_worktree(tmp_path: Path) -> None:
    sandbox = DockerSandbox(image="python:3.12-slim", memory="512m", cpus="1.0")
    wrapped = sandbox.wrap(["python", "-m", "pytest"], worktree=tmp_path)

    assert wrapped[:3] == ["docker", "run", "--rm"]
    assert "--network" in wrapped
    assert wrapped[wrapped.index("--network") + 1] == "none"
    assert f"{tmp_path.resolve()}:/work" in wrapped
    assert wrapped[-3:] == ["python", "-m", "pytest"]
    assert "--read-only" in wrapped
    assert "--memory" in wrapped
    assert "--cpus" in wrapped
    assert wrapped[-4] == "python:3.12-slim"


def test_docker_sandbox_rejects_unsafe_network() -> None:
    with pytest.raises(ValueError, match="sandbox network"):
        DockerSandbox(image="python:3.12-slim", network="container:host")


def test_docker_sandbox_requires_docker(monkeypatch) -> None:
    sandbox = DockerSandbox(image="python:3.12-slim")
    monkeypatch.setattr("racegreen.verification.sandbox.shutil.which", lambda _: None)
    with pytest.raises(RuntimeError, match="docker executable"):
        sandbox.validate_available()
