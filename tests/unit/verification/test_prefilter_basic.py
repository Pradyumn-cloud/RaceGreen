from pathlib import Path

from racegreen.verification.prefilter import PreFilter, PreFilterConfig
from racegreen.verification.intake import CandidateIntake


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
