"""Three orthogonal evidence channels plus sequential change detection.

An attacker who defeats one channel is still caught by the others - that
orthogonality is the design argument, and it is why the engine fuses rather
than picks.
"""

from __future__ import annotations

from .cusum import CusumDetector, CusumState
from .e1_self import E1Report, SelfConsistencyChecker
from .e2_cross import CrossObservationChecker, E2Report
from .e3_mission import E3Report, MissionConsistencyChecker
from .engine import EvidenceEngine, ObservationBundle, PeerAssessment

__all__ = [
    "CusumDetector",
    "CusumState",
    "SelfConsistencyChecker",
    "E1Report",
    "CrossObservationChecker",
    "E2Report",
    "MissionConsistencyChecker",
    "E3Report",
    "EvidenceEngine",
    "ObservationBundle",
    "PeerAssessment",
]
