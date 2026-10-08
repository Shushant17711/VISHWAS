"""Accuser credibility - where the three layers actually compose.

Not all opinions count equally.  A vote to expel a drone is weighted by how
much the swarm trusts the *accuser*, and that trust is derived from the two
outer layers plus the accuser's own track record:

    w_i = g( tau_static[i], tau_runtime[i], accusation_history[i] )

* ``tau_static`` comes from TrustChain-SBOM: a drone whose firmware carries
  unpatched critical CVEs, unverified-origin components or a thin provenance
  record is *a priori* more likely to be the compromised one, so its
  accusations carry less weight.
* ``tau_runtime`` comes from AdvGuard-Lite: a drone whose perception stack
  shows signs of adversarial manipulation is reporting from a corrupted
  sensor picture, so its accusations are less reliable.
* history is earned in flight: accusations the swarm later upheld raise
  credibility, accusations it rejected lower it.

This is the mechanism that turns three separate ideas into one system.  The
outer modules are not decoration - they determine whose vote counts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..config import ConsensusConfig


@dataclass
class CredibilityRecord:
    drone_id: int
    tau_static: float = 1.0
    tau_runtime: float = 1.0
    upheld: int = 0
    rejected: int = 0
    history: float = 0.5                    # running [0, 1] track record
    weight: float = 1.0

    def as_row(self) -> dict:
        return {
            "drone": self.drone_id,
            "tau_static": round(self.tau_static, 3),
            "tau_runtime": round(self.tau_runtime, 3),
            "history": round(self.history, 3),
            "weight": round(self.weight, 3),
            "upheld": self.upheld,
            "rejected": self.rejected,
        }


class CredibilityBook:
    """Maintains ``w_i`` for every drone in the swarm."""

    def __init__(
        self,
        cfg: ConsensusConfig,
        drones: list[int],
        enabled: bool = True,
        anchors: frozenset[int] = frozenset(),
    ) -> None:
        self.cfg = cfg
        self.enabled = enabled
        # Certified anchors (see MeshConfig.certified_anchors): their vote
        # weight is pinned at 1.0 permanently, immune to both the normal
        # tau_static/tau_runtime/history computation below and to reject()
        # - a hardware root of trust does not get "outvoted" by history any
        # more than it gets outvoted by a quorum.
        self.anchors = anchors
        self.records: dict[int, CredibilityRecord] = {
            d: CredibilityRecord(d) for d in drones
        }
        self.recompute()

    # ------------------------------------------------------------------
    def set_priors(self, static: dict[int, float], runtime: dict[int, float]) -> None:
        for d, rec in self.records.items():
            rec.tau_static = float(np.clip(static.get(d, 1.0), 0.0, 1.0))
            rec.tau_runtime = float(np.clip(runtime.get(d, 1.0), 0.0, 1.0))
        self.recompute()

    def set_runtime(self, drone: int, value: float) -> None:
        rec = self.records.get(drone)
        if rec is not None:
            rec.tau_runtime = float(np.clip(value, 0.0, 1.0))
            self._recompute_one(rec)

    # ------------------------------------------------------------------
    def _recompute_one(self, rec: CredibilityRecord) -> None:
        if rec.drone_id in self.anchors:
            rec.weight = 1.0
            return
        if not self.enabled:
            # Ablation configuration B: uniform trust weights.  This is the
            # comparison that shows the outer layers earn their place.
            rec.weight = 1.0
            return
        c = self.cfg
        raw = (
            c.cred_w_static * rec.tau_static
            + c.cred_w_runtime * rec.tau_runtime
            + c.cred_w_history * rec.history
        )
        total = c.cred_w_static + c.cred_w_runtime + c.cred_w_history
        rec.weight = float(np.clip(raw / total, c.cred_floor, 1.0))

    def recompute(self) -> None:
        for rec in self.records.values():
            self._recompute_one(rec)

    # ------------------------------------------------------------------
    def weight(self, drone: int) -> float:
        rec = self.records.get(drone)
        return rec.weight if rec else self.cfg.cred_floor

    def weights(self) -> dict[int, float]:
        return {d: rec.weight for d, rec in self.records.items()}

    # ------------------------------------------------------------------
    def uphold(self, accusers: list[int]) -> None:
        """The swarm agreed with these accusers - reward them."""
        for d in accusers:
            rec = self.records.get(d)
            if rec is None:
                continue
            rec.upheld += 1
            rec.history = float(np.clip(rec.history + self.cfg.history_reward, 0.0, 1.0))
            self._recompute_one(rec)

    def reject(self, accusers: list[int]) -> None:
        """These accusers were shown to be wrong - their voice is discounted."""
        for d in accusers:
            if d in self.anchors:
                continue
            rec = self.records.get(d)
            if rec is None:
                continue
            rec.rejected += 1
            rec.history = float(np.clip(rec.history - self.cfg.history_penalty, 0.0, 1.0))
            self._recompute_one(rec)

    # ------------------------------------------------------------------
    def effective(self, self_suspicion: dict[int, float], discount: float) -> dict[int, float]:
        """Weights collapsed for nodes that are themselves under suspicion.

        A node the swarm is already suspicious of should not be able to
        marshal a majority against an honest peer.  This is the direct
        countermeasure to the Byzantine-accuser scenario.
        """
        out: dict[int, float] = {}
        exponent = max(0.0, float(discount))
        for d, rec in self.records.items():
            s = float(np.clip(self_suspicion.get(d, 0.0), 0.0, 1.0))
            scale = (1.0 - s) ** exponent if exponent > 0.0 else 1.0
            out[d] = float(max(self.cfg.cred_floor, rec.weight * scale))
        return out

    def remove(self, drone: int) -> None:
        self.records.pop(drone, None)

    def table(self) -> list[dict]:
        return [rec.as_row() for rec in self.records.values()]
