"""Attack-injection framework.

Each behaviour describes a *compromised* drone: one holding valid keys, whose
messages authenticate correctly, and which is free to lie about anything it
reports.  Behaviours may modify four things:

``corrupt_telemetry``   what the drone claims about itself
``corrupt_ranges``      what the drone claims to have measured about peers
``corrupt_suspicion``   what the drone gossips about its peers' honesty
``control_override``    what the airframe physically does

The six behaviours below are exactly the six evaluation scenarios in the
project plan, ordered from "any sane system catches this" to
"this is the one that separates a real system from a toy".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class AttackBehaviour:
    """Base class - a no-op attacker (used as a control)."""

    drone_id: int
    start_tick: int = 0
    rng: np.random.Generator = field(default_factory=np.random.default_rng)
    accomplices: tuple[int, ...] = ()

    name: str = "none"
    #: Short human-readable line used by the dashboard event log.
    description: str = "No malicious behaviour."

    def active(self, tick: int) -> bool:
        return tick >= self.start_tick

    # -- what the drone says about itself ---------------------------------
    def corrupt_telemetry(
        self, tick: int, true_pos: np.ndarray, true_vel: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        return true_pos.copy(), true_vel.copy()

    # -- what the drone says it measured ----------------------------------
    def corrupt_ranges(
        self,
        tick: int,
        ranges: dict[int, tuple[float, float]],
        my_claimed_pos: np.ndarray,
        world_claimed: dict[int, np.ndarray],
    ) -> dict[int, tuple[float, float]]:
        """Default optimal-liar strategy: report ranges that are perfectly
        consistent with the *fabricated* world the attacker is projecting.

        This is deliberately the strongest thing an attacker can do with its
        own radio, and it is why E2 must aggregate across *many* observers.

        The ``malicious`` guard is load-bearing, for exactly the reason
        ``corrupt_suspicion`` documents below.  An honest drone reports what
        its radio measured against *ground truth*; that measurement
        disagreeing with a liar's claim is the entire E2 signal, and it is the
        one thing the attacker does not own.  Without the guard the control
        behaviour re-derives every honest drone's ranges from the *claimed*
        world instead - so an honest peer unwittingly corroborates whatever
        position a liar asserts, the multilateration fix reproduces the
        fabricated world by construction, and the only surviving E2 evidence
        is each drone's own first-hand ``direct_z`` (which ``swarm._score``
        takes straight from ``Mesh.measure_ranges`` and never routes through
        here).  That is measurably what was happening: pairwise range
        reciprocity ``|r_ij - r_ji|`` sat at the pure-noise level ~0.8 sigma
        for *every* pair including compromised ones, i.e. no drone's published
        ranges contradicted any other's, which is impossible when somebody is
        lying about where they are.
        """
        if not self.malicious or not self.active(tick):
            return ranges
        forged: dict[int, tuple[float, float]] = {}
        for peer, (measured, sigma) in ranges.items():
            peer_pos = world_claimed.get(peer)
            if peer_pos is None:
                forged[peer] = (measured, sigma)
                continue
            fake = float(np.linalg.norm(my_claimed_pos - peer_pos))
            forged[peer] = (fake + float(self.rng.normal(0.0, sigma)), sigma)
        return forged

    # -- what the drone says about its peers' honesty ----------------------
    @property
    def malicious(self) -> bool:
        """The control behaviour is a genuine no-op; every subclass is not."""
        return self.name != "none"

    def corrupt_suspicion(self, tick: int, scores: dict[int, float]) -> dict[int, float]:
        # An honest drone gossips exactly what its own evidence engine believes.
        # Covering for an accomplice is an *attacker* capability, so the control
        # behaviour must never touch the vector - otherwise the swarm silences
        # its own accusations and no attacker is ever detectable.
        if not self.malicious or not self.active(tick):
            return scores
        # Never accuse an accomplice, and never accuse yourself out loud.
        return {j: (0.0 if j in self.accomplices else s) for j, s in scores.items()}

    # -- the evidence the drone cites for its accusations ------------------
    def corrupt_claim_evidence(
        self,
        tick: int,
        scores: dict[int, float],
        payload: dict[int, dict[str, float]],
    ) -> dict[int, dict[str, float]]:
        """Forge the E1/E2/E3 figures cited as grounds for a fabricated claim.

        Without this, ClaimCheck would be fighting a strawman.  An attacker
        that gossips "peer 4 is 0.95 suspicious" while its attached evidence
        payload honestly reports ``z2 = 0.3`` has refuted itself in one line,
        and the claim-to-evidence binding test would catch every fabrication
        for free.  A competent attacker forges both halves so they agree, and
        that is what this models: the cited z-scores are back-filled from the
        suspicion the attacker decided to broadcast, so the claim is
        internally consistent and the binding test passes.

        What it still cannot forge is the part that is not its to write.  The
        cited evidence is a *statement*; the ``RangeReportMsg`` this drone
        already broadcast is a *commitment*, and every other drone's ranges
        are measurements taken by radios it does not own.  ClaimCheck audits
        the statement against those, so making the lie self-consistent does
        not make it survive - it just moves where it breaks.
        """
        if not self.malicious or not self.active(tick):
            return payload
        forged = {p: dict(v) for p, v in payload.items()}
        for peer, score in scores.items():
            if peer in self.accomplices:
                # Cover for accomplices: cite clean evidence to match the
                # zeroed suspicion score.
                forged[peer] = {"z1": 0.0, "z2": 0.0, "z3": 0.0, "cusum": 0.0}
                continue
            cited = forged.get(peer)
            if cited is None:
                continue
            # Back-fill a z-vector consistent with the asserted suspicion.  E2
            # carries it: that is the channel an accusation of position
            # falsification would actually rest on.
            implied = 6.0 * float(score)
            if implied > max(cited.get("z2", 0.0), 0.0):
                cited["z2"] = implied
                cited["cusum"] = max(float(cited.get("cusum", 0.0)), float(score))
        return forged

    # -- what the airframe physically does ---------------------------------
    def control_override(
        self, tick: int, accel: np.ndarray, true_pos: np.ndarray, true_vel: np.ndarray
    ) -> np.ndarray:
        return accel

    # -- fabricated sensor detections --------------------------------------
    def inject_detections(
        self, tick: int, detections: list[dict], true_pos: np.ndarray
    ) -> list[dict]:
        return detections


# --------------------------------------------------------------------------
# Scenario 1 - position teleport (baseline sanity: E1 must catch this instantly)
# --------------------------------------------------------------------------
@dataclass
class PositionTeleport(AttackBehaviour):
    jump: np.ndarray = field(default_factory=lambda: np.array([260.0, -180.0, 0.0]))
    name: str = "position_teleport"
    description: str = "Claims an instantaneous position jump outside the flight envelope."

    def corrupt_telemetry(self, tick, true_pos, true_vel):
        if not self.active(tick):
            return true_pos.copy(), true_vel.copy()
        return true_pos + self.jump, true_vel.copy()


# --------------------------------------------------------------------------
# Scenario 2 - slow trajectory drift (the hard case: only CUSUM catches it)
# --------------------------------------------------------------------------
@dataclass
class SlowDrift(AttackBehaviour):
    drift_rate: float = 0.9              # m/s of lie accumulated per second
    direction: np.ndarray = field(default_factory=lambda: np.array([0.8, 0.6, 0.0]))
    dt: float = 0.2
    name: str = "slow_drift"
    description: str = (
        "Walks its reported position away from truth slowly enough to stay "
        "under any instantaneous threshold."
    )

    def __post_init__(self) -> None:
        norm = float(np.linalg.norm(self.direction))
        if norm > 0:
            self.direction = self.direction / norm

    def corrupt_telemetry(self, tick, true_pos, true_vel):
        if not self.active(tick):
            return true_pos.copy(), true_vel.copy()
        elapsed = (tick - self.start_tick) * self.dt
        offset = self.direction * self.drift_rate * elapsed
        # Velocity is falsified consistently, so E1 integration stays clean.
        fake_vel = true_vel + self.direction * self.drift_rate
        return true_pos + offset, fake_vel


# --------------------------------------------------------------------------
# Scenario 3 - false target injection (corrupts multi-drone triangulation)
# --------------------------------------------------------------------------
@dataclass
class FalseTargetInjection(AttackBehaviour):
    n_phantoms: int = 2
    phantom_range: float = 300.0
    name: str = "false_target"
    description: str = "Reports high-confidence detections of targets that do not exist."

    _phantoms: list[np.ndarray] = field(default_factory=list, init=False)

    def inject_detections(self, tick, detections, true_pos):
        if not self.active(tick):
            return detections
        if not self._phantoms:
            for _ in range(self.n_phantoms):
                bearing = self.rng.uniform(0, 2 * np.pi)
                offset = np.array(
                    [
                        self.phantom_range * np.cos(bearing),
                        self.phantom_range * np.sin(bearing),
                        -true_pos[2],
                    ]
                )
                self._phantoms.append(true_pos + offset)
        out = list(detections)
        for k, pos in enumerate(self._phantoms):
            out.append(
                {
                    "target_id": f"phantom-{self.drone_id}-{k}",
                    "position": pos.tolist(),
                    "confidence": float(self.rng.uniform(0.88, 0.97)),
                    "phantom": True,
                }
            )
        return out


# --------------------------------------------------------------------------
# Scenario 4 - formation sabotage (induced collision risk)
# --------------------------------------------------------------------------
@dataclass
class FormationSabotage(AttackBehaviour):
    target_peer: int | None = None
    aggression: float = 3.2
    name: str = "formation_sabotage"
    description: str = (
        "Physically leaves its formation slot on a closing course with a peer "
        "while continuing to report nominal slot-keeping."
    )

    _slot_pos: np.ndarray | None = field(default=None, init=False)
    _peer_pos: np.ndarray | None = field(default=None, init=False)

    def set_context(self, slot_pos: np.ndarray, peer_pos: np.ndarray | None) -> None:
        self._slot_pos = slot_pos
        self._peer_pos = peer_pos

    def control_override(self, tick, accel, true_pos, true_vel):
        if not self.active(tick) or self._peer_pos is None:
            return accel
        to_peer = self._peer_pos - true_pos
        dist = float(np.linalg.norm(to_peer))
        if dist < 1e-6:
            return accel
        return accel * 0.2 + (to_peer / dist) * self.aggression

    def corrupt_telemetry(self, tick, true_pos, true_vel):
        if not self.active(tick) or self._slot_pos is None:
            return true_pos.copy(), true_vel.copy()
        # Claim to be exactly where the mission plan says it should be.
        jitter = self.rng.normal(0.0, 1.2, size=3)
        return self._slot_pos + jitter, true_vel * 0.4


# --------------------------------------------------------------------------
# Scenario 5 - Byzantine accuser (robustness of the mechanism itself)
# --------------------------------------------------------------------------
@dataclass
class ByzantineAccuser(AttackBehaviour):
    victims: tuple[int, ...] = ()
    accusation_level: float = 0.95
    name: str = "byzantine_accuser"
    description: str = (
        "Flies and reports honestly, but gossips fabricated high suspicion "
        "against honest peers to get them expelled."
    )

    def corrupt_ranges(self, tick, ranges, my_claimed_pos, world_claimed):
        # It is not lying about itself, so it reports true ranges - this is
        # what makes it hard: nothing about its own state looks wrong.
        return ranges

    def corrupt_suspicion(self, tick, scores):
        if not self.active(tick):
            return scores
        out = dict(scores)
        targets = self.victims or tuple(j for j in scores if j != self.drone_id)
        for v in targets:
            out[v] = self.accusation_level
        for a in self.accomplices:
            out[a] = 0.0
        return out


# --------------------------------------------------------------------------
# Scenario 6 - colluding pair (hardest case)
# --------------------------------------------------------------------------
@dataclass
class CollusiveDrift(SlowDrift):
    name: str = "collusion"
    description: str = (
        "Two compromised drones drift their reported positions together and "
        "mutually corroborate each other's falsified measurements."
    )

    def corrupt_ranges(self, tick, ranges, my_claimed_pos, world_claimed):
        """Report ranges consistent with the *shared* fabricated world.

        Accomplices are vouched for; honest peers are ranged as if the lie
        were the truth.  The one thing this cannot do is stop honest drones
        from measuring the real range to *this* airframe.
        """
        return super().corrupt_ranges(tick, ranges, my_claimed_pos, world_claimed)

    def corrupt_suspicion(self, tick, scores):
        if not self.active(tick):
            return scores
        out = {j: (0.0 if j in self.accomplices else s) for j, s in scores.items()}
        return out


# --------------------------------------------------------------------------
# Scenario 7 - spoofed certified anchor (attestation is not truth)
# --------------------------------------------------------------------------
@dataclass
class SpoofedAnchor(AttackBehaviour):
    """A hardware-attested drone whose *sensors* are lying to it.

    This scenario exists to answer the sharpest question the certified-anchor
    design invites: *if a hardware root of trust fixes this, why not attest
    every drone?*  Because attestation proves the airframe is the one you
    shipped, running the firmware you signed - it proves nothing about whether
    the numbers its sensors produce are true.

    GNSS spoofing is the canonical case and it walks straight past a secure
    element.  The receiver is fed a counterfeit constellation, the flight
    computer believes the position it derives, and it broadcasts that position
    in good faith over an authenticated link, from unmodified firmware, with a
    valid attestation chain.  Every hardware check passes.  Nothing about the
    drone is "compromised" in the sense a TPM is designed to detect.

    The modelling detail that makes this scenario worth flying: **the ranges
    stay honest**.  Peer ranging is time-of-flight against other radios and
    does not consult GNSS at all, so a spoofed drone still measures its
    neighbours correctly even while it is wrong about itself.  That asymmetry
    is exactly what E2 and ClaimCheck are built to exploit - and it is why the
    swarm can catch a spoofed anchor that its own attestation chain vouches
    for.

    Simplification, stated rather than hidden: the airframe here keeps flying
    its true trajectory and only *reports* the spoofed position.  A real
    spoofed drone would also fly wrong, because it steers on the position it
    believes.  Modelling that too would add a mission-degradation effect on
    top of the integrity question this scenario is about.
    """

    spoof_rate: float = 1.1              # m/s of GNSS error injected
    direction: np.ndarray = field(default_factory=lambda: np.array([-0.6, 0.8, 0.0]))
    dt: float = 0.2
    name: str = "spoofed_anchor"
    description: str = (
        "A hardware-attested drone whose GNSS is spoofed: it reports a false "
        "position in good faith, with valid attestation and honest ranging."
    )

    def __post_init__(self) -> None:
        norm = float(np.linalg.norm(self.direction))
        if norm > 0:
            self.direction = self.direction / norm

    def corrupt_telemetry(self, tick, true_pos, true_vel):
        if not self.active(tick):
            return true_pos.copy(), true_vel.copy()
        elapsed = (tick - self.start_tick) * self.dt
        # A spoofer walks its victim off gradually; a step change would be
        # caught by E1's flight envelope before the interesting part happens.
        return true_pos + self.direction * self.spoof_rate * elapsed, true_vel.copy()

    def corrupt_ranges(self, tick, ranges, my_claimed_pos, world_claimed):
        # Time-of-flight ranging never consults GNSS.  The spoofed drone
        # measures its neighbours perfectly correctly - which is precisely
        # what lets the swarm catch it.
        return ranges

    def corrupt_suspicion(self, tick, scores):
        # It is not malicious.  It gossips exactly what it believes, which is
        # itself distorted by the spoof - honestly reported, honestly wrong.
        return scores


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------
BEHAVIOURS: dict[str, type[AttackBehaviour]] = {
    "none": AttackBehaviour,
    "position_teleport": PositionTeleport,
    "slow_drift": SlowDrift,
    "false_target": FalseTargetInjection,
    "formation_sabotage": FormationSabotage,
    "byzantine_accuser": ByzantineAccuser,
    "collusion": CollusiveDrift,
    "spoofed_anchor": SpoofedAnchor,
}

SCENARIOS: list[dict[str, object]] = [
    {
        "id": 1,
        "key": "position_teleport",
        "title": "Position teleport",
        "tests": "Baseline sanity - E1 should catch this instantly.",
        "n_compromised": 1,
    },
    {
        "id": 2,
        "key": "slow_drift",
        "title": "Slow trajectory drift",
        "tests": "The hard case - only sequential change detection catches it.",
        "n_compromised": 1,
    },
    {
        "id": 3,
        "key": "false_target",
        "title": "False target injection",
        "tests": "Corrupts multi-drone triangulation.",
        "n_compromised": 1,
    },
    {
        "id": 4,
        "key": "formation_sabotage",
        "title": "Formation sabotage",
        "tests": "Induced collision risk.",
        "n_compromised": 1,
    },
    {
        "id": 5,
        "key": "byzantine_accuser",
        "title": "Byzantine accuser",
        "tests": "Robustness of the consensus mechanism itself.",
        "n_compromised": 1,
    },
    {
        "id": 7,
        "key": "spoofed_anchor",
        "title": "Spoofed certified anchor",
        "tests": (
            "Attestation proves identity, not truth - a hardware-attested drone "
            "whose GNSS is spoofed reports a false position with a valid signature."
        ),
        "n_compromised": 1,
    },
    {
        "id": 6,
        "key": "collusion",
        "title": "Colluding pair",
        "tests": "Hardest case - two drones corroborating.",
        "n_compromised": 2,
    },
]


def build_behaviour(
    key: str,
    drone_id: int,
    start_tick: int,
    rng: np.random.Generator,
    accomplices: tuple[int, ...] = (),
    **kwargs,
) -> AttackBehaviour:
    cls = BEHAVIOURS.get(key, AttackBehaviour)
    return cls(
        drone_id=drone_id,
        start_tick=start_tick,
        rng=rng,
        accomplices=accomplices,
        **kwargs,
    )
