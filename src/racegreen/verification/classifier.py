"""Failure classification."""

from __future__ import annotations

from enum import StrEnum


class FailureClass(StrEnum):
    DETERMINISTIC = "deterministic"
    FLAKY = "flaky"
    MISSING_DEPS = "missing_deps"
    SETUP_ERROR = "setup_error"
    TIMEOUT = "timeout"
    INFRA = "infra"


def classify(exit_code: int, stdout: str = "", stderr: str = "") -> FailureClass:
    s = (stdout + "\n" + stderr).lower()
    if "timeout" in s or "timed out" in s:
        return FailureClass.TIMEOUT
    if "modulenotfounderror" in s or "no module named" in s or "importerror" in s:
        return FailureClass.MISSING_DEPS
    if "fixture" in s and "error" in s:
        return FailureClass.SETUP_ERROR
    if "connection" in s or "port" in s and "bind" in s:
        return FailureClass.INFRA
    if exit_code != 0:
        return FailureClass.DETERMINISTIC
    return FailureClass.DETERMINISTIC
