"""JUnit XML parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

_MAX_JUNIT_BYTES = 10 * 1024 * 1024


def with_junit_output(command: list[str], junit_path: Path) -> list[str]:
    """Append one JUnit output argument, replacing caller-supplied ones."""
    cleaned: list[str] = []
    skip = False
    for part in command:
        if skip:
            skip = False
            continue
        if part == "--junitxml":
            skip = True
            continue
        if part.startswith("--junitxml="):
            continue
        cleaned.append(part)
    return cleaned + [f"--junitxml={junit_path}"]


def _int_attr(element: ET.Element, name: str) -> int:
    try:
        return max(0, int(element.attrib.get(name, 0) or 0))
    except (TypeError, ValueError):
        return 0


@dataclass
class JUnitSummary:
    tests: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    passed: int = 0
    cases: list[dict] = None  # type: ignore

    def __post_init__(self) -> None:
        if self.cases is None:
            self.cases = []


def parse_junit(path: Path) -> JUnitSummary:
    try:
        if path.stat().st_size > _MAX_JUNIT_BYTES:
            return JUnitSummary()
        raw = path.read_bytes()
        # JUnit reports are untrusted test output. Reject DTD/entity payloads
        # instead of allowing parser-side entity expansion.
        upper = raw.upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            return JUnitSummary()
        root = ET.fromstring(raw)
    except (OSError, ET.ParseError, ValueError):
        return JUnitSummary()
    summary = JUnitSummary()
    # handle junit xml variants
    for testsuite in root.iter("testsuite"):
        summary.tests += _int_attr(testsuite, "tests")
        summary.failures += _int_attr(testsuite, "failures")
        summary.errors += _int_attr(testsuite, "errors")
        summary.skipped += _int_attr(testsuite, "skipped")
    for testcase in root.iter("testcase"):
        name = testcase.attrib.get("name", "")
        classname = testcase.attrib.get("classname", "")
        status = "passed"
        message = ""
        failure = testcase.find("failure")
        error = testcase.find("error")
        if failure is not None:
            status = "failed"
            message = (failure.attrib.get("message") or failure.text or "").strip()
        elif error is not None:
            status = "error"
            message = (error.attrib.get("message") or error.text or "").strip()
        elif testcase.find("skipped") is not None:
            status = "skipped"
        summary.cases.append(
            {
                "name": name,
                "classname": classname,
                "status": status,
                "message": message[:500],
            }
        )
    summary.passed = max(0, summary.tests - summary.failures - summary.errors - summary.skipped)
    return summary
