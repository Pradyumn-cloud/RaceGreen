"""Bounded retry policy (no backoff for local tests)."""

from __future__ import annotations


class RetryPolicy:
    def __init__(self, max_retries: int = 2) -> None:
        self.max_retries = max_retries

    def should_retry(self, failure_class: str, attempt: int) -> bool:
        if attempt >= self.max_retries:
            return False
        if failure_class == "deterministic":
            return False
        if failure_class == "timeout":
            return attempt < 1  # retry once on timeout
        if failure_class == "flaky":
            return True
        if failure_class in ("infra",):
            return attempt < 1
        return False
