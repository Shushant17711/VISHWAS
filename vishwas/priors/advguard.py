"""Layer 2 - AdvGuard-Lite: a runtime perception-integrity trust signal.

Purpose: detect that a drone's perception stack is being manipulated.  That
matters twice over - it is a threat in itself, and it is a strong signal that
*this drone's reports about the world are unreliable*, which is precisely
what the consensus layer needs to know before weighting its vote.

Background, and it is published research rather than speculation: onboard
object detectors are provably vulnerable to adversarial patches.  An
optimised printed pattern can cause a detector to miss a vehicle entirely.

Two parts:

* **Offline robustness scoring** - generate adversarial patches against the
  swarm's detection model with an Expectation-over-Transformation attack
  (optimising a patch to survive varied angle, scale and lighting), measure
  detection-accuracy degradation, and turn that into a per-model robustness
  score.  See ``scripts/advguard_eot.py``; results land in a JSON file that
  this module consumes.
* **Runtime integrity flag** - monitor the statistical distribution of
  detection confidences during flight.  Adversarial manipulation produces
  characteristic signatures: anomalous confidence entropy, unstable class
  assignment across consecutive frames on a static object, and detection
  dropout inconsistent with scene continuity.  Deviation from the drone's own
  established baseline emits ``tau_runtime[j]`` in [0, 1].

**Honest scoping note.**  The full version of this idea includes adversarial
*hardening* - retraining the model on generated patches.  That is listed as
future scope and is not claimed as built.  What is built is the detection and
the scoring.
"""

from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..config import PriorConfig


@dataclass
class PatchRobustness:
    """Offline EoT result for one detection model."""

    model: str
    clean_map: float                        # mAP with no patch present
    patched_map: float                      # mAP under the optimised patch
    patch_area_frac: float = 0.03
    transformations: int = 0

    @property
    def degradation(self) -> float:
        if self.clean_map <= 1e-6:
            return 1.0
        return float(np.clip(1.0 - self.patched_map / self.clean_map, 0.0, 1.0))

    @property
    def robustness(self) -> float:
        """Per-model robustness score in [0, 1]; 1.0 = patch had no effect."""
        return float(1.0 - self.degradation)

    @classmethod
    def load(cls, path: str | Path) -> dict[str, "PatchRobustness"]:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        out: dict[str, PatchRobustness] = {}
        for entry in data.get("models", []):
            r = cls(
                model=str(entry["model"]),
                clean_map=float(entry["clean_map"]),
                patched_map=float(entry["patched_map"]),
                patch_area_frac=float(entry.get("patch_area_frac", 0.03)),
                transformations=int(entry.get("transformations", 0)),
            )
            out[r.model] = r
        return out


@dataclass
class PerceptionFrame:
    """One perception tick as reported by a drone's detector."""

    tick: int
    confidences: list[float] = field(default_factory=list)
    class_ids: list[int] = field(default_factory=list)
    expected_detections: int = 0

    @property
    def dropped(self) -> bool:
        return len(self.confidences) < self.expected_detections


@dataclass
class RuntimeReport:
    tau_runtime: float = 1.0
    entropy: float = 0.0
    instability: float = 0.0
    dropout: float = 0.0
    flagged: bool = False
    reason: str = "nominal"

    def as_row(self) -> dict:
        return {
            "tau_runtime": round(self.tau_runtime, 4),
            "entropy": round(self.entropy, 4),
            "instability": round(self.instability, 4),
            "dropout": round(self.dropout, 4),
            "flagged": self.flagged,
            "reason": self.reason,
        }


class PerceptionMonitor:
    """Sliding-window integrity monitor for one drone's detector."""

    def __init__(self, cfg: PriorConfig, drone_id: int, robustness: float = 1.0) -> None:
        self.cfg = cfg
        self.drone_id = drone_id
        self.robustness = float(np.clip(robustness, 0.0, 1.0))
        self.window: deque[PerceptionFrame] = deque(maxlen=cfg.adv_entropy_window)
        self.report = RuntimeReport()

    # ------------------------------------------------------------------
    def update(self, frame: PerceptionFrame) -> RuntimeReport:
        self.window.append(frame)
        if len(self.window) < 4:
            return self.report

        entropy = float(np.mean([_confidence_entropy(f.confidences) for f in self.window]))
        instability = _class_flip_rate(self.window)
        dropout = float(np.mean([1.0 if f.dropped else 0.0 for f in self.window]))

        c = self.cfg
        # Each signal contributes only the part of itself that exceeds its
        # alarm level, so a merely noisy sensor does not cost a drone trust.
        pen = (
            c.adv_w_entropy * _excess(entropy, c.adv_entropy_alarm)
            + c.adv_w_instability * _excess(instability, c.adv_instability_alarm)
            + c.adv_w_dropout * _excess(dropout, c.adv_dropout_alarm)
        )
        # A model already known to be fragile offline gets less benefit of the
        # doubt online: the two halves of AdvGuard compose.
        pen *= 2.0 - self.robustness
        tau = float(np.clip(1.0 - pen, 0.0, 1.0))

        reasons = {
            "confidence_entropy": _excess(entropy, c.adv_entropy_alarm),
            "class_instability": _excess(instability, c.adv_instability_alarm),
            "detection_dropout": _excess(dropout, c.adv_dropout_alarm),
        }
        worst = max(reasons, key=lambda key: reasons[key])
        self.report = RuntimeReport(
            tau_runtime=tau,
            entropy=entropy,
            instability=instability,
            dropout=dropout,
            flagged=tau < 0.65,
            reason=worst if reasons[worst] > 0 else "nominal",
        )
        return self.report


# ----------------------------------------------------------------------
def _confidence_entropy(confidences: list[float]) -> float:
    """Normalised entropy of a detection-confidence vector.

    A healthy detector is decisive: a few high confidences and a sharp
    distribution.  Patch attacks flatten it.
    """
    if not confidences:
        return 0.0
    p = np.asarray(confidences, dtype=float)
    p = np.clip(p, 1e-9, None)
    p = p / p.sum()
    h = float(-np.sum(p * np.log(p)))
    return float(h / math.log(len(p))) if len(p) > 1 else 0.0


def _class_flip_rate(window: deque[PerceptionFrame]) -> float:
    """How often the top class assignment changes between consecutive frames."""
    tops = [max(zip(f.confidences, f.class_ids))[1] if f.confidences else None
            for f in window]
    pairs = [(a, b) for a, b in zip(tops, list(tops)[1:]) if a is not None and b is not None]
    if not pairs:
        return 0.0
    return float(sum(1 for a, b in pairs if a != b) / len(pairs))


def _excess(value: float, alarm: float) -> float:
    if alarm >= 1.0:
        return 0.0
    return float(np.clip((value - alarm) / max(1.0 - alarm, 1e-6), 0.0, 1.0))
