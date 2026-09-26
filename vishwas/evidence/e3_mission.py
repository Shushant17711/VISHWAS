"""Evidence channel E3 - mission-logic consistency.

Does the drone's behaviour match the role it was assigned?  Formation slot
occupancy, assigned search-sector coverage, waypoint adherence, and whether
its reported detections are corroborated by anyone else.

E3 is deliberately weak evidence.  A drone can drift out of its slot for
entirely honest reasons - wind, an avoidance manoeuvre, a lost link - and any
system that expels a drone for being out of position is a system that expels
honest drones.  Its value is in *combination*: E3 alone accuses nobody, but
E3 agreeing with E2 raises confidence substantially, and E3 is the only
channel that notices a drone which is exactly where it says it is while
quietly not doing its job.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..config import EvidenceConfig


@dataclass
class E3Report:
    z: float = 0.0
    worst: str = "none"
    terms: dict[str, float] = field(default_factory=dict)


class MissionConsistencyChecker:
    """Compares a peer's claimed behaviour with its assigned mission role."""

    def __init__(self, cfg: EvidenceConfig) -> None:
        self.cfg = cfg
        self._phantom_strikes: dict[int, float] = {}

    # ------------------------------------------------------------------
    def score(
        self,
        peer: int,
        claimed_pos: np.ndarray,
        expected_pos: np.ndarray | None,
        tolerance: float,
        *,
        phase: str = "search",
        uncorroborated_detections: int = 0,
        corroborated_detections: int = 0,
    ) -> E3Report:
        terms: dict[str, float] = {}
        claimed_pos = np.asarray(claimed_pos, dtype=float)

        if expected_pos is not None:
            error = float(np.linalg.norm(claimed_pos[:2] - np.asarray(expected_pos, float)[:2]))
            if phase == "formation":
                sigma = self.cfg.e3_slot_sigma
                key = "formation_slot"
            else:
                sigma = self.cfg.e3_sector_sigma
                key = "sector_adherence"
            # Tolerance is the honest operating envelope; only excursions
            # beyond it accumulate, and they accumulate slowly.
            terms[key] = max(0.0, (error - tolerance) / max(sigma, 1e-6))

        # Reported detections nobody else can see.  One is noise; a steady
        # stream of them is a drone injecting false targets into the shared
        # picture, which is attack scenario 3.
        if uncorroborated_detections or corroborated_detections:
            strike = self._phantom_strikes.get(peer, 0.0)
            strike = 0.85 * strike + uncorroborated_detections - 0.5 * corroborated_detections
            strike = float(np.clip(strike, 0.0, 12.0))
            self._phantom_strikes[peer] = strike
            terms["phantom_reports"] = max(0.0, (strike - 1.0) / 1.5)

        if not terms:
            return E3Report()
        worst = max(terms, key=lambda key: terms[key])
        return E3Report(float(max(0.0, terms[worst])), worst, terms)
