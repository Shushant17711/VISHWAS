"""The Evidence Engine - one instance per drone.

Every drone `i` independently scores every peer `j` across three orthogonal
evidence channels, fuses them into a single residual stream, and runs
sequential change detection on that stream to produce a suspicion score
``s_i[j]`` that is updated continuously.

Orthogonality is the whole design argument: an attacker who defeats one
channel is still caught by the others.  Faking self-consistency (E1) is easy;
faking it *while* satisfying the range measurements of every honest peer (E2)
requires controlling radios you do not own; doing both *while* still flying
the mission you were assigned (E3) leaves nothing to attack with.

The engine deliberately outputs a *score*, never a decision.  Expulsion is a
collective judgement and belongs to the consensus layer - a single drone's
opinion is not trustworthy, because that drone may itself be the compromised
one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..config import SimConfig
from .cusum import CusumDetector
from .e1_self import E1Report, SelfConsistencyChecker
from .e2_cross import CrossObservationChecker, E2Report
from .e3_mission import E3Report, MissionConsistencyChecker


@dataclass
class PeerAssessment:
    """Everything drone ``i`` currently believes about peer ``j``."""

    peer: int
    suspicion: float = 0.0
    fused: float = 0.0
    cusum: float = 0.0
    alarmed: bool = False
    alarm_tick: int | None = None
    z1: float = 0.0
    z2: float = 0.0
    z3: float = 0.0
    dominant: str = "none"
    abstained: bool = True
    detail: dict = field(default_factory=dict)

    def as_row(self) -> dict:
        return {
            "peer": self.peer,
            "suspicion": round(self.suspicion, 4),
            "cusum": round(self.cusum, 3),
            "z1": round(self.z1, 3),
            "z2": round(self.z2, 3),
            "z3": round(self.z3, 3),
            "dominant": self.dominant,
            "alarmed": self.alarmed,
        }


@dataclass
class ObservationBundle:
    """Inputs for one tick, assembled by the swarm from the mesh."""

    tick: int
    claims: dict[int, np.ndarray] = field(default_factory=dict)
    telemetry: dict[int, object] = field(default_factory=dict)
    my_position: np.ndarray | None = None
    my_ranges: dict[int, tuple[float, float]] = field(default_factory=dict)
    peer_ranges: dict[int, dict[int, tuple[float, float]]] = field(default_factory=dict)
    expectations: dict[int, tuple[np.ndarray, float]] = field(default_factory=dict)
    phase: str = "search"
    detection_support: dict[int, tuple[int, int]] = field(default_factory=dict)
    credibility: dict[int, float] = field(default_factory=dict)


class EvidenceEngine:
    def __init__(self, drone_id: int, cfg: SimConfig, rng: np.random.Generator) -> None:
        self.me = drone_id
        self.cfg = cfg
        self.ev = cfg.evidence
        self.rng = rng
        self._e1: dict[int, SelfConsistencyChecker] = {}
        self._cusum: dict[int, CusumDetector] = {}
        self._e2 = CrossObservationChecker(self.ev, cfg.mesh, rng, drone_id)
        self._e3 = MissionConsistencyChecker(self.ev)
        self.assessments: dict[int, PeerAssessment] = {}

    # ------------------------------------------------------------------
    def _checker(self, peer: int) -> SelfConsistencyChecker:
        if peer not in self._e1:
            self._e1[peer] = SelfConsistencyChecker(self.cfg.airframe, self.ev, self.cfg.dt)
        return self._e1[peer]

    def _detector(self, peer: int) -> CusumDetector:
        if peer not in self._cusum:
            self._cusum[peer] = CusumDetector(
                k=self.ev.cusum_slack_k,
                h=self.ev.cusum_threshold_h,
                decay=self.ev.cusum_decay,
                reset_on_alarm=self.ev.cusum_reset_on_alarm,
            )
        return self._cusum[peer]

    # ------------------------------------------------------------------
    def update(self, bundle: ObservationBundle) -> dict[int, PeerAssessment]:
        """Score every peer we heard from this tick."""
        peers = [p for p in bundle.claims if p != self.me]
        for peer in peers:
            self.assessments[peer] = self._score_peer(peer, bundle)
        # Peers we heard nothing from: decay, never accumulate.  Silence is
        # not evidence of guilt - a jammed honest drone must not be expelled
        # for being jammed.
        for peer, assessment in self.assessments.items():
            if peer in bundle.claims or peer == self.me:
                continue
            state = self._detector(peer).abstain()
            assessment.cusum = state.normalised(self.ev.cusum_threshold_h)
            assessment.suspicion = self._suspicion(state.statistic)
            assessment.abstained = True
        return self.assessments

    # ------------------------------------------------------------------
    def _score_peer(self, peer: int, bundle: ObservationBundle) -> PeerAssessment:
        out = PeerAssessment(peer=peer)

        # -- E1 ---------------------------------------------------------
        msg = bundle.telemetry.get(peer)
        r1 = self._checker(peer).update(msg) if msg is not None else E1Report(0.0, "silent", {})
        out.z1 = r1.z

        # -- E2 ---------------------------------------------------------
        r2 = E2Report()
        if bundle.my_position is not None:
            r2 = self._e2.score(
                peer,
                bundle.claims,
                bundle.my_position,
                bundle.my_ranges,
                bundle.peer_ranges,
                credibility=bundle.credibility or None,
            )
        out.z2 = r2.z

        # -- E3 ---------------------------------------------------------
        expected = bundle.expectations.get(peer)
        corrob, uncorrob = bundle.detection_support.get(peer, (0, 0))
        claim = bundle.claims.get(peer)
        r3 = E3Report()
        if claim is not None and (expected is not None or corrob or uncorrob):
            exp_pos, tol = expected if expected is not None else (None, 0.0)
            r3 = self._e3.score(
                peer,
                claim,
                exp_pos,
                tol,
                phase=bundle.phase,
                uncorroborated_detections=uncorrob,
                corroborated_detections=corrob,
            )
        out.z3 = r3.z

        # -- fusion -----------------------------------------------------
        # E2 dominates by design: it is the only channel the attacker cannot
        # forge.  E1 and E3 are corroborating weight, not primary evidence.
        fused = (
            self.ev.w_e1 * out.z1
            + self.ev.w_e2 * out.z2
            + self.ev.w_e3 * out.z3
        )
        out.fused = float(fused)

        detector = self._detector(peer)
        if r2.abstained and msg is None:
            state = detector.abstain()
        else:
            state = detector.update(fused, bundle.tick)

        out.cusum = state.normalised(self.ev.cusum_threshold_h)
        out.alarmed = state.alarmed
        out.alarm_tick = state.first_alarm_tick
        out.suspicion = self._suspicion(state.statistic)
        out.abstained = r2.abstained
        contributions = {
            "E1": self.ev.w_e1 * out.z1,
            "E2": self.ev.w_e2 * out.z2,
            "E3": self.ev.w_e3 * out.z3,
        }
        out.dominant = max(contributions, key=lambda key: contributions[key])
        out.detail = {
            "e1": r1.worst,
            "e2_offset_m": round(r2.offset, 2),
            "e2_observers": r2.n_observers,
            "e2_inliers": r2.n_inliers,
            "e3": r3.worst,
            "statistic": round(state.statistic, 3),
        }
        return out

    # ------------------------------------------------------------------
    def _suspicion(self, statistic: float) -> float:
        """Map the CUSUM statistic to [0, 1], crossing 0.5 at the alarm level."""
        x = self.ev.suspicion_gain * (statistic - self.ev.cusum_threshold_h)
        return float(1.0 / (1.0 + np.exp(-np.clip(x, -60.0, 60.0))))

    # ------------------------------------------------------------------
    def suspicion_vector(self, peers: list[int]) -> dict[int, float]:
        """The vector this drone gossips to the swarm each accusation round."""
        return {
            p: float(self.assessments[p].suspicion)
            for p in peers
            if p in self.assessments and p != self.me
        }

    def evidence_payload(self, peers: list[int]) -> dict[int, dict[str, float]]:
        """Compact per-peer evidence attached to a gossip message."""
        out: dict[int, dict[str, float]] = {}
        for p in peers:
            a = self.assessments.get(p)
            if a is None or p == self.me:
                continue
            out[p] = {"z1": a.z1, "z2": a.z2, "z3": a.z3, "cusum": a.cusum}
        return out

    def forget(self, peer: int) -> None:
        """Drop state for an expelled peer."""
        self._e1.pop(peer, None)
        self._cusum.pop(peer, None)
        self.assessments.pop(peer, None)
        self._e2.last.pop(peer, None)
