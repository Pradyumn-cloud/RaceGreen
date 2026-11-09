
from pathlib import Path

from racegreen.verification.prefilter import PreFilter, PreFilterConfig


def test_empty_rejected():
    pf = PreFilter()
    res = pf.filter(b"", "", "c1")
    assert res.accepted is False
    assert any(r.rule == "empty_diff" for r in res.rejections)


def test_hard_cap():
    pf = PreFilter(PreFilterConfig(max_patch_bytes_hard=10))
    res = pf.filter(b"diff --git a/x b/x\n" + b"x" * 100, "h", "c1")
    assert res.accepted is False
    assert any(r.rule == "hard_size_cap" for r in res.rejections)


def test_soft_limit_does_not_reject_by_default():
    pf = PreFilter(PreFilterConfig(max_patch_bytes_soft=5, max_patch_bytes_hard=1000000))
    res = pf.filter(b"diff --git a/x b/x\n" + b"a" * 100, "h", "c1")
    assert res.soft_limit_triggered is True
    # may still be rejected by other rules but not solely by soft limit


def test_test_file_edits_are_rejected():
    patch = Path("benchmarks/tier1/decoys/tier1-control-04-test-edit.patch").read_bytes()
    pf = PreFilter()

    result = pf.filter(patch, "hash", "candidate")

    assert result.accepted is False
    assert any(rejection.rule == "test_file_edit" for rejection in result.rejections)


def test_pyproject_test_configuration_edits_are_rejected():
    patch = (
        b"diff --git a/pyproject.toml b/pyproject.toml\n"
        b"index 1111111..2222222 100644\n"
        b"--- a/pyproject.toml\n"
        b"+++ b/pyproject.toml\n"
        b"@@ -1 +1,2 @@\n"
        b" [tool.pytest.ini_options]\n"
        b"+addopts = \"-k not integration\"\n"
    )
    pf = PreFilter()

    result = pf.filter(patch, "hash", "candidate")

    assert result.accepted is False
    assert any(rejection.rule == "test_config_tamper" for rejection in result.rejections)
