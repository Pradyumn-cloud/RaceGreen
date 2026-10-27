"""Cleanup and survivor scanning."""

from __future__ import annotations

import os


def scan_for_markers(marker: str = "RACEGREEN_RUN_ID") -> list[int]:
    survivors = []
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/environ", "rb") as f:
                    data = f.read()
                if marker.encode() in data:
                    survivors.append(int(pid))
            except Exception:
                continue
    except Exception:
        pass
    return survivors
