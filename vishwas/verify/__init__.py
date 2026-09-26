"""ClaimCheck - independent, physics-backed verification of accusations.

The consensus layer one directory over decides *collectively*; this package
decides *independently*.  That distinction is the whole point.  Every
mechanism in ``vishwas/consensus`` - the trimmed weighted mean, the ``2f+1``
quorum, credibility weighting - aggregates opinions, and an aggregate of
opinions follows whichever bloc is larger.  Nothing here aggregates anything.
Each accusation is audited on its own against measurements the swarm already
broadcast, and a claim that the physics refutes stays refuted no matter how
many drones signed it.

Entry point: :class:`~vishwas.verify.verifier.ClaimVerifier`, wired into
``ConsensusEngine.round`` and fed by ``Swarm._accusation_round``.
"""

from .claim import Accusation, ClaimBundle, ClaimEvidence
from .ledger import ClaimLedger
from .safety import SafetyDecision, SafetyEngine
from .verdict import Finding, TargetAudit, Verdict
from .verifier import ClaimVerifier

__all__ = [
    "Accusation",
    "ClaimBundle",
    "ClaimEvidence",
    "ClaimLedger",
    "ClaimVerifier",
    "Finding",
    "SafetyDecision",
    "SafetyEngine",
    "TargetAudit",
    "Verdict",
]
