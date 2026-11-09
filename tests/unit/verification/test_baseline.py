from racegreen.verification.baseline import BaseBaseline, baseline_is_usable
from racegreen.verification.junit import JUnitSummary


def _baseline(
    *,
    returncode: int = 0,
    junit_present: bool = True,
    tests: int = 1,
    failures: int = 0,
    errors: int = 0,
    timed_out: bool = False,
) -> BaseBaseline:
    return BaseBaseline(
        base_commit="a" * 40,
        failing_tests=set(),
        summary=JUnitSummary(tests=tests, failures=failures, errors=errors),
        cached_at=0.0,
        returncode=returncode,
        junit_present=junit_present,
        timed_out=timed_out,
    )


def test_baseline_is_usable_requires_junit_and_tests() -> None:
    assert baseline_is_usable(_baseline())
    assert not baseline_is_usable(_baseline(junit_present=False))
    assert not baseline_is_usable(_baseline(tests=0))
    assert not baseline_is_usable(_baseline(timed_out=True))


def test_baseline_is_usable_rejects_unexplained_command_failure() -> None:
    assert not baseline_is_usable(_baseline(returncode=1))
    assert baseline_is_usable(_baseline(returncode=1, failures=1))
