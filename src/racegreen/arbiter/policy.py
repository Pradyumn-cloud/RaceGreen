"""Deterministic winner-selection policies.

The verifier and arbiter establish that a candidate is *confirmed*. A policy
then decides which confirmed candidate, if any, should win. Policies never
override confirmation; they only choose among candidates that already carry
complete full-suite evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

FIRST_CONFIRMED = "first_confirmed"
BEST_OF_CONFIRMED = "best_of_confirmed"
CONSENSUS = "consensus"
POLICIES = (FIRST_CONFIRMED, BEST_OF_CONFIRMED, CONSENSUS)


@dataclass(frozen=True)
class ConfirmedCandidate:
    """A candidate that has already passed full-suite confirmation."""

    candidate_id: str
    agent_id: str
    patch_hash: str
    base_commit: str
    evidence: dict[str, Any]
    confirmed_at: float
    changed_files: int = 0
    changed_lines: int = 0


@dataclass(frozen=True)
class PolicyDecision:
    policy: str
    selected: ConfirmedCandidate | None
    reason: str
    considered: tuple[str, ...]
    quorum: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "selected": self.selected.candidate_id if self.selected else None,
            "reason": self.reason,
            "considered": list(self.considered),
            "quorum": self.quorum,
        }


def validate_policy(policy: str) -> str:
    if policy not in POLICIES:
        raise ValueError(f"unknown winner policy: {policy}")
    return policy


def consensus_quorum(total_agents: int, configured: int | None = None) -> int:
    """Return a conservative quorum for identical confirmed patches."""
    if configured is not None:
        if configured < 1:
            raise ValueError("consensus quorum must be at least 1")
        return min(configured, max(1, total_agents))
    # Two agents must both agree; three agents need two; larger races need a
    # strict majority. This keeps consensus meaningful without making a
    # single-agent run impossible.
    if total_agents <= 1:
        return 1
    return max(2, total_agents // 2 + 1)


def select_winner(
    policy: str,
    candidates: list[ConfirmedCandidate],
    *,
    total_agents: int,
    configured_quorum: int | None = None,
) -> PolicyDecision:
    """Choose a winner or return a no-winner decision with a reason."""
    validate_policy(policy)
    ordered = sorted(candidates, key=lambda item: (item.confirmed_at, item.candidate_id))
    considered = tuple(item.candidate_id for item in ordered)

    if not ordered:
        return PolicyDecision(policy, None, "no_confirmed_candidates", considered)

    if policy == FIRST_CONFIRMED:
        return PolicyDecision(policy, ordered[0], "earliest_confirmation", considered)

    if policy == BEST_OF_CONFIRMED:
        # Prefer the smallest confirmed patch, then the earliest confirmation.
        # Patch size is a disclosed tie-break, never correctness evidence.
        best = min(
            ordered,
            key=lambda item: (
                item.changed_lines,
                item.changed_files,
                item.confirmed_at,
                item.candidate_id,
            ),
        )
        return PolicyDecision(policy, best, "smallest_confirmed_patch", considered)

    quorum = consensus_quorum(total_agents, configured_quorum)
    # Count distinct agents, not candidate IDs. A single agent that submits
    # the same patch twice must not satisfy a multi-agent quorum.
    groups: dict[str, dict[str, ConfirmedCandidate]] = {}
    for item in ordered:
        groups.setdefault(item.patch_hash, {}).setdefault(item.agent_id, item)
    for patch_hash, group in groups.items():
        if len(group) >= quorum:
            # The first confirmation in the agreeing group is the winner.
            selected = next(iter(group.values()))
            return PolicyDecision(
                policy,
                selected,
                f"consensus_patch_hash:{patch_hash[:12]}",
                considered,
                quorum=quorum,
            )
    return PolicyDecision(
        policy,
        None,
        "no_consensus",
        considered,
        quorum=quorum,
    )
