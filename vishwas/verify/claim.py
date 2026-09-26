"""What an accusation *is*, once it stops being a ballot.

The consensus layer above this package used to receive a number: accuser ``i``
says peer ``j`` is suspicious to degree ``s``.  A number cannot be checked, and
a mechanism that cannot check its inputs can only count them - which is why a
compromised majority could out-number the honest minority and expel it.

Here an accusation is a *claim* instead: an assertion plus the raw
measurements that assertion is supposed to follow from.  The two halves are
kept deliberately separate, because the whole verification argument rests on
the difference between them:

* :class:`ClaimEvidence` is what the accuser *asserts* it measured - the
  gossiped z-vector.  A compromised drone can put anything in here.
* :class:`ClaimBundle` carries what every drone *published to the whole mesh*
  this round - telemetry claims and ``RangeReportMsg`` ranges.  Those are
  broadcast commitments: an attacker chooses them freely, but it chooses them
  once, in public, before it knows which accusations will be audited, and it
  cannot retract or vary them per-audit.

Verification is therefore arithmetic on the second against the first, and
never a poll of anybody's opinion.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class ClaimEvidence:
    """The per-channel figures an accuser asserts as grounds for its claim.

    This mirrors ``EvidenceEngine.evidence_payload`` exactly - it is that
    payload, gossiped.  ``present`` distinguishes "the accuser cited nothing"
    (a bare assertion, which verifies as UNSUPPORTED) from "the accuser cited
    all-zero measurements" (a self-contradictory claim, which is worse).
    """

    z1: float = 0.0
    z2: float = 0.0
    z3: float = 0.0
    cusum: float = 0.0
    present: bool = False

    @property
    def cited_magnitude(self) -> float:
        """How strong an anomaly the accuser is asserting it saw."""
        return max(abs(self.z1), abs(self.z2), abs(self.z3))

    def as_vector(self) -> np.ndarray:
        return np.array([self.z1, self.z2, self.z3, self.cusum], dtype=float)

    def as_row(self) -> dict:
        return {
            "z1": round(self.z1, 3),
            "z2": round(self.z2, 3),
            "z3": round(self.z3, 3),
            "cusum": round(self.cusum, 3),
            "present": self.present,
        }


@dataclass
class Accusation:
    """One accuser's claim about one target, with the evidence it cited."""

    accuser: int
    target: int
    score: float
    evidence: ClaimEvidence = field(default_factory=ClaimEvidence)

    def as_row(self) -> dict:
        return {
            "accuser": self.accuser,
            "target": self.target,
            "score": round(self.score, 4),
            "evidence": self.evidence.as_row(),
        }


@dataclass
class ClaimBundle:
    """Every public commitment the swarm made this round.

    Assembled by :class:`~vishwas.sim.swarm.Swarm` from the mesh, never from
    ground truth.  The verifier is given exactly what an honest drone
    listening to the same broadcasts would have.

    Attributes
    ----------
    claims
        ``drone -> claimed position``.  Forgeable, and that is the point: the
        verifier tests claims *against* measurements, it never assumes them.
    ranges
        ``observer -> {peer -> (range_m, sigma_m)}`` as broadcast in
        ``RangeReportMsg``.  Measured by the observer's own radio against
        ground truth for an honest drone; freely chosen, but publicly
        committed, for a compromised one.
    evidence
        ``accuser -> {target -> ClaimEvidence}`` from the gossip payload.
    anchors
        Certified-anchor ids (``MeshConfig.certified_anchors``).  A hardware
        root of trust is the one reference that sits outside the vote.
    """

    tick: int = 0
    claims: dict[int, np.ndarray] = field(default_factory=dict)
    ranges: dict[int, dict[int, tuple[float, float]]] = field(default_factory=dict)
    evidence: dict[int, dict[int, ClaimEvidence]] = field(default_factory=dict)
    anchors: frozenset[int] = frozenset()

    def evidence_for(self, accuser: int, target: int) -> ClaimEvidence:
        return self.evidence.get(accuser, {}).get(target, ClaimEvidence())

    def observers_of(self, target: int, exclude: set[int] | None = None) -> list[int]:
        """Drones that published a range to ``target`` this round.

        ``exclude`` always contains the target itself and, at the call site,
        the accuser whose claim is being audited - an accuser does not get to
        supply the evidence that vindicates its own accusation.
        """
        skip = set(exclude or ())
        skip.add(target)
        return sorted(
            o
            for o, table in self.ranges.items()
            if o not in skip and target in table and o in self.claims
        )
