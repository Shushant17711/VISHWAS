"""Evidence channel E1 - self-consistency.

The cheapest of the three channels, and the one that catches unsophisticated
spoofing instantly.  It asks a single question of a peer's telemetry stream:

    *Does this trajectory obey the flight envelope of the airframe that
    claims to be flying it?*

A liar who teleports, out-accelerates its own class, turns faster than its
yaw authority allows, or reports a manoeuvre profile that does not match its
reported battery drain, fails here without any peer ever looking at it.

E1 is local, needs no gossip and no ranging.  A competent attacker defeats it
easily - which is the point of having E2 and E3.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from ..config import AirframeConfig, EvidenceConfig
from ..sim.kinematics import YAW_HOLD_SPEED, expected_power


@dataclass
class E1Report:
    z: float                                # standardised violation, >= 0
    worst: str                              # which sub-check dominated
    terms: dict[str, float]

    @property
    def clean(self) -> bool:
        return self.z <= 0.0


class SelfConsistencyChecker:
    """Per-peer envelope checker over a short sliding window of telemetry."""

    def __init__(self, airframe: AirframeConfig, cfg: EvidenceConfig, dt: float) -> None:
        self.air = airframe
        self.cfg = cfg
        self.dt = dt
        self._hist: deque[dict] = deque(maxlen=4)

    # ------------------------------------------------------------------
    def update(self, msg) -> E1Report:
        """Score one :class:`TelemetryMsg` against the previous one."""
        cur = {
            "tick": msg.tick,
            "pos": np.asarray(msg.position, dtype=float),
            "vel": np.asarray(msg.velocity, dtype=float),
            "yaw": float(msg.yaw),
            "batt": float(msg.battery_frac),
        }
        terms: dict[str, float] = {}
        if not self._hist:
            self._hist.append(cur)
            return E1Report(0.0, "bootstrap", terms)

        prev = self._hist[-1]
        dt = max(1e-6, (cur["tick"] - prev["tick"]) * self.dt)
        tol = self.cfg.e1_envelope_tolerance          # slack over the envelope

        # -- speed ------------------------------------------------------
        speed = float(np.linalg.norm(cur["vel"]))
        terms["speed"] = _excess(speed, self.air.max_speed * tol, self.air.max_speed * 0.1)

        # -- acceleration ----------------------------------------------
        accel = float(np.linalg.norm(cur["vel"] - prev["vel"])) / dt
        terms["accel"] = _excess(accel, self.air.max_accel * tol, self.air.max_accel * 0.15)

        # -- vertical rate ---------------------------------------------
        climb = (cur["pos"][2] - prev["pos"][2]) / dt
        if climb >= 0:
            terms["climb"] = _excess(climb, self.air.max_climb_rate * tol,
                                     self.air.max_climb_rate * 0.2)
        else:
            terms["descent"] = _excess(-climb, self.air.max_descent_rate * tol,
                                       self.air.max_descent_rate * 0.2)

        # -- yaw rate ---------------------------------------------------
        # Heading only carries information while the airframe is translating.
        # A near-hovering quad has a heading defined by noise, so a yaw jump
        # between two slow ticks is not evidence of anything and must not be
        # charged against the drone - that is how honest swarms get expelled
        # during formation join, when everyone is briefly close to stationary.
        moving = (float(np.linalg.norm(cur["vel"][:2])) > YAW_HOLD_SPEED
                  and float(np.linalg.norm(prev["vel"][:2])) > YAW_HOLD_SPEED)
        if moving:
            dyaw = abs(_wrap(cur["yaw"] - prev["yaw"])) / dt
            terms["turn_rate"] = _excess(dyaw, self.air.max_turn_rate * tol,
                                         self.air.max_turn_rate * 0.25)
        else:
            terms["turn_rate"] = 0.0

        # -- position/velocity integration ------------------------------
        # The single most useful check: a teleport, or any position stream
        # that is not the integral of its own velocity stream, dies here.
        predicted = prev["pos"] + 0.5 * (prev["vel"] + cur["vel"]) * dt
        mismatch = float(np.linalg.norm(cur["pos"] - predicted))
        # Tolerance grows with the distance actually travelled.
        travel = float(np.linalg.norm(cur["vel"])) * dt
        sigma = self.cfg.e1_integration_sigma + 0.10 * travel
        terms["integration"] = mismatch / sigma

        # -- energy plausibility ----------------------------------------
        drawn = (prev["batt"] - cur["batt"]) * self.air.battery_capacity
        expect = expected_power(self.air, cur["vel"]) * dt
        if expect > 1e-6:
            rel = abs(drawn - expect) / expect
            terms["energy"] = max(0.0, rel - self.cfg.e1_energy_sigma) / self.cfg.e1_energy_sigma
        else:
            terms["energy"] = 0.0

        self._hist.append(cur)
        worst = max(terms, key=lambda key: terms[key])
        return E1Report(float(max(0.0, terms[worst])), worst, terms)


# ----------------------------------------------------------------------
def _excess(value: float, limit: float, sigma: float) -> float:
    """How many sigma past ``limit`` is ``value``?  Zero if inside."""
    if sigma <= 0:
        return 0.0
    return max(0.0, (value - limit) / sigma)


def _wrap(angle: float) -> float:
    return float((angle + np.pi) % (2 * np.pi) - np.pi)
