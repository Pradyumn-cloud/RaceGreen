from pathlib import Path

from racegreen.verification.prefilter import PreFilter
from racegreen.agent.candidate import compute_patch_hash


def test_oracle_patches_accepted(tmp_path):
    pf = PreFilter()
    oracle_dir = Path("benchmarks/tier1/oracle")
    if not oracle_dir.exists():
        return
    count = 0
    for p in sorted(oracle_dir.glob("*.patch")):
        data = p.read_bytes()
        res = pf.filter(data, compute_patch_hash(data), p.stem)
        # Oracle patches should be accepted by prefilters (may have soft limits but not rejected)
        # allow soft limits
        bad = [r for r in res.rejections if r.rule in ("empty_diff", "malformed_diff", "apply_check_failed", "hard_size_cap", "symlink_detected", "submodule_detected", "binary_artifact", "test_config_tamper", "vcs_metadata_change")]
        assert len(bad) == 0, f"{p} rejected: {bad}"
        count += 1
    assert count >= 1
