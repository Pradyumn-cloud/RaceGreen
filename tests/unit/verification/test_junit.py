from pathlib import Path

from racegreen.verification.artifacts import write_bounded
from racegreen.verification.junit import parse_junit, with_junit_output


def test_parse_junit_counts_cases_and_failures(tmp_path: Path) -> None:
    path = tmp_path / "junit.xml"
    path.write_text(
        """<?xml version="1.0"?>
<testsuite tests="2" failures="1" errors="0" skipped="0">
  <testcase classname="tests.test_app" name="test_ok" />
  <testcase classname="tests.test_app" name="test_bad">
    <failure message="assertion failed">trace</failure>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )

    summary = parse_junit(path)

    assert summary.tests == 2
    assert summary.failures == 1
    assert summary.errors == 0
    assert summary.passed == 1
    assert [case["status"] for case in summary.cases] == ["passed", "failed"]


def test_parse_junit_ignores_malformed_and_oversized_files(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.xml"
    malformed.write_text("<testsuite", encoding="utf-8")
    assert parse_junit(malformed).tests == 0

    oversized = tmp_path / "oversized.xml"
    oversized.write_bytes(b"<testsuite tests='1'/>" + b" " * (10 * 1024 * 1024 + 1))
    assert parse_junit(oversized).tests == 0


def test_parse_junit_rejects_dtd_entities(tmp_path: Path) -> None:
    path = tmp_path / "junit.xml"
    path.write_text(
        '<!DOCTYPE testsuite [<!ENTITY x "boom">]>'
        '<testsuite tests="1"><testcase name="&x;" /></testsuite>',
        encoding="utf-8",
    )

    assert parse_junit(path).tests == 0


def test_parse_junit_tolerates_invalid_numeric_attributes(tmp_path: Path) -> None:
    path = tmp_path / "junit.xml"
    path.write_text(
        '<testsuite tests="many" failures="-2" errors="x"><testcase name="x" /></testsuite>',
        encoding="utf-8",
    )

    summary = parse_junit(path)

    assert summary.tests == 0
    assert summary.failures == 0
    assert summary.errors == 0


def test_with_junit_output_replaces_existing_output_argument() -> None:
    command = ["pytest", "-q", "--junitxml=old.xml", "--junitxml", "other.xml"]
    result = with_junit_output(command, Path("new.xml"))

    assert result == ["pytest", "-q", "--junitxml=new.xml"]


def test_write_bounded_never_exceeds_limit(tmp_path: Path) -> None:
    path = tmp_path / "artifact.bin"
    for limit in (1, 8, 32):
        write_bounded(path, b"x" * 10_000, limit)
        assert path.stat().st_size <= limit

