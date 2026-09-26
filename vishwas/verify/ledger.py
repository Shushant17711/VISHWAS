"""Append-only record of every verdict, for the "why" the dashboard shows.

An automated system that expels an airframe from a live mission owes an
account of the decision that a person can audit afterwards.  The vote math was
already recorded (``ExclusionEvent.detail``); this records the half that
matters more, which is what the swarm *refused* to act on and on what grounds.
Nothing here is read back by the verifier - it is evidence for humans.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .verdict import TargetAudit, Verdict


@dataclass
class ClaimLedger:
    """Bounded, append-only audit trail of ClaimCheck decisions."""

    max_entries: int = 4096
    entries: list[dict] = field(default_factory=list)
    #: accuser -> how many of its claims have been refuted outright
    refuted_by_accuser: dict[int, int] = field(
        default_factory=lambda: defaultdict(int)
    )
    #: target -> how many expulsions ClaimCheck has blocked for it
    blocked_by_target: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def record(self, tick: int, audit: TargetAudit) -> None:
        for finding in audit.findings:
            self.counts[finding.verdict.value] += 1
            if finding.verdict is Verdict.REFUTED:
                self.refuted_by_accuser[finding.accuser] += 1
        if len(self.entries) < self.max_entries:
            self.entries.append({"tick": tick, **audit.as_row()})

    def record_block(self, tick: int, target: int, reason: str, detail: dict) -> None:
        """Note an expulsion the verifier prevented - the headline event."""
        self.blocked_by_target[target] += 1
        self.counts["blocked"] += 1
        if len(self.entries) < self.max_entries:
            self.entries.append(
                {"tick": tick, "target": target, "blocked": reason, **detail}
            )

    def summary(self) -> dict:
        return {
            "verdicts": dict(self.counts),
            "refuted_by_accuser": dict(self.refuted_by_accuser),
            "blocked_by_target": dict(self.blocked_by_target),
            "entries": len(self.entries),
        }
