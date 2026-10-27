"""JUnit XML parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass
class JUnitSummary:
    tests: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    passed: int = 0
    cases: list[dict] = None  # type: ignore

    def __post_init__(self):
        if self.cases is None:
            self.cases = []


def parse_junit(path: Path) -> JUnitSummary:
    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except Exception:
        return JUnitSummary()
    summary = JUnitSummary()
    # handle junit xml variants
    for testsuite in root.iter("testsuite"):
        summary.tests += int(testsuite.attrib.get("tests", 0) or 0)
        summary.failures += int(testsuite.attrib.get("failures", 0) or 0)
        summary.errors += int(testsuite.attrib.get("errors", 0) or 0)
        summary.skipped += int(testsuite.attrib.get("skipped", 0) or 0)
    for testcase in root.iter("testcase"):
        name = testcase.attrib.get("name", "")
        classname = testcase.attrib.get("classname", "")
        status = "passed"
        if testcase.find("failure") is not None:
            status = "failed"
        elif testcase.find("error") is not None:
            status = "error"
        elif testcase.find("skipped") is not None:
            status = "skipped"
        summary.cases.append({"name": name, "classname": classname, "status": status})
    summary.passed = max(0, summary.tests - summary.failures - summary.errors - summary.skipped)
    return summary
