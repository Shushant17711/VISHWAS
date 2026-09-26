"""Four verdicts, because "yes" and "no" are not enough answers.

A binary verifier has to guess when the evidence is thin, and a guess under a
hostile majority is a coin flip weighted by the attacker.  Splitting "no" into
*refuted* and *unsupported*, and admitting *ambiguous* as a real outcome,
is what lets the swarm distinguish "the physics says this accusation is
false" from "nobody gave me anything to check" from "the measurements
themselves are contested".  Those three call for three different actions, and
only one of them is ever an expulsion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    """The outcome of auditing one accusation."""

    #: Independent re-derivation agrees the target is anomalous.  The only
    #: verdict that may ever contribute to an expulsion quorum.
    SUPPORTED = "supported"
    #: Independent physics contradicts the claim - the target is where it says
    #: it is, or the accuser's own published measurements confirm it.  The
    #: accuser pays for this.
    REFUTED = "refuted"
    #: The accuser cited nothing checkable.  Not punished (an honest drone can
    #: legitimately have no usable geometry), but never counted either.
    UNSUPPORTED = "unsupported"
    #: Not enough independent evidence either way, or the anchor set is split
    #: into contradictory worlds.  Safe default: do not expel.
    AMBIGUOUS = "ambiguous"

    @property
    def counts_for_quorum(self) -> bool:
        return self is Verdict.SUPPORTED

    @property
    def penalises_accuser(self) -> bool:
        return self is Verdict.REFUTED


@dataclass
class Finding:
    """One audited accusation: the verdict, and why."""

    accuser: int
    target: int
    verdict: Verdict
    reason: str = ""
    score: float = 0.0
    cited_z: float = 0.0             # strongest channel the accuser asserted
    commitment_z: float | None = None  # accuser's own published one-hop residual
    independent_z: float | None = None  # re-derived offset, in sigma
    independent_observers: int = 0
    anchor_backed: bool = False
    agreeing_fraction: float = 1.0
    duplicate_of: int | None = None    # collapsed into this accuser's claim

    @property
    def effective(self) -> bool:
        """Counts toward quorum: supported, and not a copy of another claim."""
        return self.verdict.counts_for_quorum and self.duplicate_of is None

    def as_row(self) -> dict:
        return {
            "accuser": self.accuser,
            "target": self.target,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "score": round(self.score, 4),
            "cited_z": round(self.cited_z, 3),
            "commitment_z": (
                None if self.commitment_z is None else round(self.commitment_z, 3)
            ),
            "independent_z": (
                None if self.independent_z is None else round(self.independent_z, 3)
            ),
            "observers": self.independent_observers,
            "anchor_backed": self.anchor_backed,
            "agreeing_fraction": round(self.agreeing_fraction, 3),
            "duplicate_of": self.duplicate_of,
        }


@dataclass
class TargetAudit:
    """Every finding about one target this round, plus the summary counts."""

    target: int
    findings: list[Finding] = field(default_factory=list)
    contested: bool = False           # anchor set split into rival worlds
    agreeing_fraction: float = 1.0
    anchor_backed: bool = False

    def by_verdict(self, verdict: Verdict) -> list[Finding]:
        return [f for f in self.findings if f.verdict is verdict]

    @property
    def supported_accusers(self) -> list[int]:
        return sorted(f.accuser for f in self.findings if f.effective)

    @property
    def refuted_accusers(self) -> list[int]:
        return sorted(f.accuser for f in self.findings if f.verdict is Verdict.REFUTED)

    @property
    def admissible(self) -> set[int]:
        """Accusers whose suspicion may still influence the weighted tally.

        Supported and ambiguous claims stay in the average - an ambiguous
        claim is not evidence of innocence, it is an absence of proof, and
        zeroing it would let an attacker launder a real anomaly by making the
        geometry hard to check.  Refuted and unsupported claims are dropped.
        """
        return {
            f.accuser
            for f in self.findings
            if f.verdict in (Verdict.SUPPORTED, Verdict.AMBIGUOUS)
            and f.duplicate_of is None
        }

    def as_row(self) -> dict:
        return {
            "target": self.target,
            "contested": self.contested,
            "agreeing_fraction": round(self.agreeing_fraction, 3),
            "anchor_backed": self.anchor_backed,
            "supported": self.supported_accusers,
            "refuted": self.refuted_accusers,
            "findings": [f.as_row() for f in self.findings],
        }
