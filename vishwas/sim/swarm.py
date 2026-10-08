"""The swarm loop - where every layer of VISHWAS actually meets.

One tick is, in order:

1. **Physics.**  Every airframe integrates its commanded acceleration under a
   real flight envelope.  This is ground truth and is never transmitted.
2. **Speech.**  Every drone says where it is (``TelemetryMsg``) and what it
   measured about its neighbours (``RangeReportMsg``).  A compromised drone
   lies here, and only here - it cannot alter what other radios observe.
3. **Evidence.**  Every drone independently scores every peer over E1/E2/E3
   and runs CUSUM on the fused residual.
4. **Consensus.**  At a fixed gossip interval, suspicion vectors are exchanged
   and the trust-weighted quorum decides whether to expel anyone.
5. **Mission.**  Sector coverage, target confirmation, and the reallocation
   that follows an exclusion.

The separation that matters: the evidence layer only ever produces scores, the
consensus layer makes the single collective decision, and the two outer trust
layers only ever *weight* that decision.  Nothing in this file consults ground
truth on behalf of a detector - the simulator knows the truth, the swarm does
not.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..config import SimConfig
from ..consensus import ConsensusEngine, CredibilityBook, ExclusionEvent
from ..evidence import EvidenceEngine, ObservationBundle
from ..priors.advguard import PerceptionFrame, PerceptionMonitor
from ..verify import ClaimBundle, ClaimEvidence
from .attacks import AttackBehaviour, build_behaviour
from .kinematics import Airframe, PhysicalState, seek_waypoint, separation_accel
from .mesh import Mesh, RangeReportMsg, TelemetryMsg
from .mission import Mission

HOME = np.array([0.0, 0.0, 0.0])


# ----------------------------------------------------------------------
# per-drone record
# ----------------------------------------------------------------------
@dataclass
class Drone:
    """One swarm member: an airframe, an opinion, and possibly a secret."""

    drone_id: int
    airframe: Airframe
    engine: EvidenceEngine
    monitor: PerceptionMonitor
    behaviour: AttackBehaviour
    compromised: bool = False
    excluded_at: int | None = None
    returning: bool = False
    plan: list[np.ndarray] = field(default_factory=list)
    plan_idx: int = 0
    claimed: np.ndarray = field(default_factory=lambda: np.zeros(3))
    claimed_vel: np.ndarray = field(default_factory=lambda: np.zeros(3))
    detections: list[dict] = field(default_factory=list)

    @property
    def state(self) -> PhysicalState:
        return self.airframe.state

    @property
    def alive(self) -> bool:
        return self.airframe.state.alive

    @property
    def contributing(self) -> bool:
        """Still flying the mission and still part of the consensus."""
        return self.alive and self.excluded_at is None


@dataclass
class TickRecord:
    """One row of the flight recorder - what the dashboard and metrics read."""

    tick: int
    phase: str
    positions: dict[int, list[float]]
    claimed: dict[int, list[float]]
    suspicion: dict[int, float]
    weights: dict[int, float]
    excluded: list[int]
    coverage: float
    targets_confirmed: int
    connectivity: float
    events: list[dict] = field(default_factory=list)


# ----------------------------------------------------------------------
class Swarm:
    """Runs one full scenario end to end."""

    def __init__(
        self,
        cfg: SimConfig,
        scenario: str = "none",
        n_compromised: int = 0,
        attack_start: int = 60,
        static_priors: dict[int, float] | None = None,
        rng: np.random.Generator | None = None,
    ) -> None:
        self.cfg = cfg
        self.scenario = scenario
        self.attack_start = attack_start
        self.rng = rng if rng is not None else np.random.default_rng(cfg.seed)
        self.tick = 0
        self.n = cfg.n_drones

        self.mesh = Mesh(cfg.mesh, self.n, self.rng)
        self.mission = Mission(cfg.mission, self.n, self.rng)
        self.mission.dt = cfg.dt

        # Who is compromised.  Chosen up front; the swarm is never told.
        # Certified anchors (cfg.mesh.certified_anchors) are never eligible -
        # a hardware root of trust is, by the threat model this simulator
        # scores, not the kind of thing the compromise draw represents.
        anchors = frozenset(cfg.mesh.certified_anchors)
        if scenario == "spoofed_anchor" and anchors and n_compromised:
            # The whole point of this scenario is that the *attested* drone is
            # the one lying, so the usual exclusion is inverted rather than
            # relaxed: the anchor is drawn first and deliberately.  The
            # exclusion elsewhere is not a bug being worked around - it is
            # correct for a key-extraction threat model, where a hardware root
            # of trust genuinely is out of reach.  Sensor spoofing is a
            # different threat that walks straight past attestation, and it
            # needs its own draw.
            forced = sorted(anchors)[:n_compromised]
            spare = [i for i in range(self.n) if i not in anchors]
            extra = (
                self.rng.choice(
                    spare, size=n_compromised - len(forced), replace=False
                ).tolist()
                if n_compromised > len(forced)
                else []
            )
            self.compromised: tuple[int, ...] = tuple(sorted(forced + list(extra)))
        else:
            candidates = [i for i in range(self.n) if i not in anchors]
            self.compromised = tuple(
                sorted(
                    self.rng.choice(
                        candidates, size=n_compromised, replace=False
                    ).tolist()
                )
            ) if n_compromised else ()

        self.static_priors = static_priors or {}
        self.drones: dict[int, Drone] = {}
        self._build_drones()

        self.credibility = CredibilityBook(
            cfg.consensus,
            list(range(self.n)),
            enabled=cfg.enable_trust_priors,
            anchors=anchors,
        )
        if self.static_priors:
            self.credibility.set_priors(self.static_priors, {})

        self.consensus = ConsensusEngine(cfg, list(range(self.n)), self.credibility)
        self.enable_consensus = cfg.enable_consensus

        self.history: list[TickRecord] = []
        self.events: list[ExclusionEvent] = []
        #: observer -> {peer: (range, sigma)} as broadcast this tick.
        self.published_ranges: dict[int, dict[int, tuple[float, float]]] = {}
        self._blocks_seen = 0
        self.reallocations: list[dict] = []
        self.event_log: list[dict] = []
        self.tick_seconds: list[float] = []

        # ---- the formation anchor -------------------------------------
        # Formation flight is flown against a *virtual* anchor that moves
        # along the mission plan, not against whichever drone happens to be
        # first in the live list.  Two reasons, and both matter:
        #   * a leader that chases its own slot never accelerates, so the
        #     swarm would hover at the launch point for the whole transit;
        #   * an expectation derived from a peer's *claim* is an expectation
        #     an attacker can move.  The anchor is public mission plan data
        #     that every honest drone can reproduce without trusting anyone.
        alt = float(cfg.mission.cruise_altitude)
        start = self._true_positions()[:, :2].mean(axis=0) if self.drones else np.zeros(2)
        self._anchor = np.array([float(start[0]), float(start[1]), alt])
        self._rally = np.array([HOME[0], HOME[1], alt])
        delta = self._rally[:2] - self._anchor[:2]
        self._heading = float(np.arctan2(delta[1], delta[0])) if np.linalg.norm(delta) > 1e-6 else 0.0

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _build_drones(self) -> None:
        cfg = self.cfg
        span = max(1.0, cfg.mission.formation_spacing)
        for i in range(self.n):
            start = np.array(
                [
                    -cfg.mission.area_size * 0.45 + (i % 4) * span,
                    -cfg.mission.area_size * 0.45 - (i // 4) * span,
                    cfg.mission.cruise_altitude,
                ]
            )
            state = PhysicalState(position=start, velocity=np.zeros(3))
            is_bad = i in self.compromised
            behaviour = build_behaviour(
                self.scenario if is_bad else "none",
                drone_id=i,
                start_tick=self.attack_start,
                rng=np.random.default_rng(int(self.rng.integers(0, 2**31 - 1))),
                # Only an attacker has accomplices to cover for.
                accomplices=tuple(j for j in self.compromised if j != i) if is_bad else (),
            )
            self.drones[i] = Drone(
                drone_id=i,
                airframe=Airframe(cfg.airframe, state),
                engine=EvidenceEngine(i, cfg, np.random.default_rng(cfg.seed + i)),
                monitor=PerceptionMonitor(
                    cfg.priors,
                    i,
                    robustness=0.55 if is_bad else 0.85,
                ),
                behaviour=behaviour,
                compromised=is_bad,
            )

    # ------------------------------------------------------------------
    # geometry helpers
    # ------------------------------------------------------------------
    @property
    def phase(self) -> str:
        return self.mission.phase

    def _live(self) -> list[int]:
        return [i for i, d in self.drones.items() if d.alive]

    def _contributing(self) -> list[int]:
        return [i for i, d in self.drones.items() if d.contributing]

    def _true_positions(self) -> np.ndarray:
        return np.array([self.drones[i].state.position for i in range(self.n)])

    def _alive_mask(self) -> np.ndarray:
        return np.array([self.drones[i].alive for i in range(self.n)], dtype=bool)

    def _leader(self) -> int:
        live = self._contributing()
        return live[0] if live else 0

    # ------------------------------------------------------------------
    # 1. control + physics
    # ------------------------------------------------------------------
    def _plan_for(self, drone: int) -> None:
        """(Re)build the search plan for a drone from its assigned sector."""
        d = self.drones[drone]
        idx = self.mission.sector_of.get(drone)
        if idx is None:
            d.plan, d.plan_idx = [], 0
            return
        sector = self.mission.sectors[idx % len(self.mission.sectors)]
        d.plan = sector.lawnmower(self.cfg.mission.cruise_altitude)
        d.plan_idx = 0

    def _waypoint(self, drone: int) -> np.ndarray:
        d = self.drones[drone]
        if d.returning:
            return np.array([HOME[0], HOME[1], self.cfg.mission.cruise_altitude])
        if self.mission.phase == "formation":
            slot = self.mission.slot_of.get(drone, 0)
            return self.mission.formation_slot(slot, self._anchor, self._heading)
        # Corroboration duty pre-empts the sweep: a report with one witness is
        # not actionable intelligence, and going to look is the only way to
        # turn it into either a confirmed target or a phantom-report strike.
        spot = self.mission.task_pos.get(drone)
        if spot is not None:
            return np.array(
                [float(spot[0]), float(spot[1]), self.cfg.mission.cruise_altitude]
            )
        if not d.plan:
            self._plan_for(drone)
        if not d.plan:
            return d.state.position
        target = d.plan[min(d.plan_idx, len(d.plan) - 1)]
        if float(np.linalg.norm(target[:2] - d.state.position[:2])) < 30.0:
            d.plan_idx = (d.plan_idx + 1) % len(d.plan)
            if d.plan_idx == 0 and self.mission.advance_sector(drone, self.tick):
                # A full pass is done and this drone owes a sweep elsewhere.
                self._plan_for(drone)
                if not d.plan:
                    return d.state.position
            target = d.plan[d.plan_idx]
        return target

    def _advance_anchor(self) -> None:
        """Move the virtual formation anchor one tick along the transit leg."""
        cfg = self.cfg
        delta = self._rally[:2] - self._anchor[:2]
        dist = float(np.linalg.norm(delta))
        if dist < 1e-6:
            return
        self._heading = float(np.arctan2(delta[1], delta[0]))
        step = min(dist, cfg.airframe.max_speed * 0.6 * cfg.dt)
        self._anchor[:2] += delta / dist * step

    def _step_physics(self) -> None:
        cfg = self.cfg
        if self.mission.phase == "formation":
            # Advance the virtual anchor along the transit leg at cruise
            # speed.  The vee is towed by the plan, not by a drone.
            self._advance_anchor()
        positions = {i: self.drones[i].state.position for i in self._live()}

        for i in self._live():
            d = self.drones[i]
            wp = self._waypoint(i)
            accel = seek_waypoint(d.state, wp, cfg.airframe)
            neighbours = [p for j, p in positions.items() if j != i]
            accel = accel + separation_accel(d.state, neighbours)
            if d.compromised and not d.returning:
                accel = d.behaviour.control_override(
                    self.tick, accel, d.state.position, d.state.velocity
                )
            d.airframe.step(accel, cfg.dt)

    # ------------------------------------------------------------------
    # 2. speech - what each drone claims and reports
    # ------------------------------------------------------------------
    def _speak(self) -> tuple[dict[int, np.ndarray], dict[int, TelemetryMsg]]:
        claims: dict[int, np.ndarray] = {}
        telemetry: dict[int, TelemetryMsg] = {}

        # Formation sabotage needs to know the slot it is abandoning.
        if self.scenario == "formation_sabotage":
            for i in self.compromised:
                d = self.drones[i]
                slot = self.mission.slot_of.get(i, 0)
                slot_pos = self.mission.formation_slot(slot, self._anchor, self._heading)
                peers = [j for j in self._contributing() if j != i]
                peer = getattr(d.behaviour, "target_peer", None)
                if peer is None or peer not in self.drones:
                    peer = peers[0] if peers else None
                peer_pos = self.drones[peer].state.position if peer is not None else None
                if hasattr(d.behaviour, "set_context"):
                    d.behaviour.set_context(slot_pos, peer_pos)

        for i in self._live():
            d = self.drones[i]
            pos, vel = d.behaviour.corrupt_telemetry(
                self.tick, d.state.position, d.state.velocity
            )
            d.claimed, d.claimed_vel = np.asarray(pos, float), np.asarray(vel, float)
            claims[i] = d.claimed

            # Genuine sensing happens at the true position; a compromised
            # drone may append targets that do not exist.
            dets = (
                self.mission.detect(i, d.state.position) if d.contributing else []
            )
            dets = d.behaviour.inject_detections(self.tick, dets, d.state.position)
            d.detections = dets

            msg = TelemetryMsg(
                sender=i,
                tick=self.tick,
                position=d.claimed,
                velocity=d.claimed_vel,
                yaw=float(d.state.yaw),
                battery_frac=float(
                    d.state.energy / max(1e-9, self.cfg.airframe.battery_capacity)
                ),
                detections=dets,
                assigned_slot=self.mission.slot_of.get(i),
                assigned_sector=self.mission.sector_of.get(i),
            )
            telemetry[i] = msg
            self.mesh.broadcast(msg, self.tick)
        return claims, telemetry

    def _report_ranges(self, claims: dict[int, np.ndarray]) -> None:
        truth = self._true_positions()
        alive = self._alive_mask()
        # What each drone *published* this tick, kept for ClaimCheck.  These
        # are public broadcasts, not privileged data: the verifier audits
        # accusations against exactly the commitments every listening drone
        # already received, and never against ``truth`` above.
        self.published_ranges = {}
        for i in self._live():
            d = self.drones[i]
            ranges, bearings = self.mesh.measure_ranges(i, truth, alive)
            ranges = d.behaviour.corrupt_ranges(
                self.tick, ranges, d.claimed, claims
            )
            self.published_ranges[i] = dict(ranges)
            self.mesh.broadcast(
                RangeReportMsg(
                    sender=i, tick=self.tick, ranges=ranges, bearings=bearings
                ),
                self.tick,
            )

    # ------------------------------------------------------------------
    # 3. evidence
    # ------------------------------------------------------------------
    def _score(self, tick: int) -> None:
        weights = self.credibility.weights()
        leader = self._leader()
        for i in self._contributing():
            d = self.drones[i]
            inbox = self.mesh.inbox(i)
            tele = {m.sender: m for m in Mesh.of_type(inbox, TelemetryMsg)}
            rng_msgs = Mesh.of_type(inbox, RangeReportMsg)

            claims = {s: np.asarray(m.position, float) for s, m in tele.items()}
            claims[i] = self.drones[i].state.position  # first-hand knowledge

            truth = self._true_positions()
            my_ranges, _ = self.mesh.measure_ranges(i, truth, self._alive_mask())
            peer_ranges = {m.sender: m.ranges for m in rng_msgs if m.sender != i}

            # The formation reference is the published anchor, not a peer's
            # claim: E3 must not be steerable by the drone it is judging, nor
            # by whoever happens to be leading.
            anchor = self._anchor
            expectations: dict[int, tuple[np.ndarray, float]] = {}
            for peer in claims:
                if peer == i:
                    continue
                expectations[peer] = self.mission.expected_position(
                    peer, tick, anchor, self._heading, claims.get(peer)
                )

            bundle = ObservationBundle(
                tick=tick,
                claims=claims,
                telemetry=tele,
                my_position=self.drones[i].state.position,
                my_ranges=my_ranges,
                peer_ranges=peer_ranges,
                expectations=expectations,
                phase=self.mission.phase,
                detection_support=self._detection_support(tele),
                credibility=weights,
            )
            d.engine.update(bundle)

    def _detection_support(
        self, tele: dict[int, TelemetryMsg]
    ) -> dict[int, tuple[int, int]]:
        """Per reporter: how many of its detections were confirmed or refuted.

        A detection is only evidence about the reporter when somebody else was
        in a position to see the same thing.  A drone sweeping its own sector
        alone reports targets no peer can corroborate, and that silence says
        nothing about its honesty - counting it as a phantom would convict
        every honest searcher in the swarm the moment it fanned out.

        So each report is judged only against the peers whose own claimed
        position put the reported location inside their detection range.  With
        no such witness the report is unfalsifiable and is scored neither way;
        with witnesses, agreement corroborates and silence refutes.

        Silence, however, is the cheapest thing in the world to manufacture.  A
        drone that lies about where it is can place its claim next to somebody
        else's genuine sighting and then simply say nothing, and the honest
        reporter wears the strike - a framing primitive that costs the attacker
        no risk at all.  Two rules close it.  Witnesses are drawn only from
        drones still contributing to the mission, so a node the swarm has
        already expelled cannot keep voting by staying quiet.  And a strike
        needs corroborated silence: one peer's word against another's is a tie,
        never a conviction, so a lone silent witness scores nothing.  Framing an
        honest reporter therefore takes a quorum of liars whose fabricated
        claims E2 is independently policing.
        """
        reach = self.cfg.mission.target_detect_range
        trusted = set(self._contributing())
        tele = {s: m for s, m in tele.items() if s in trusted}
        seen: dict[str, set[int]] = {}
        where: dict[str, np.ndarray] = {}
        for sender, msg in tele.items():
            for det in msg.detections:
                key = str(det.get("target_id"))
                seen.setdefault(key, set()).add(sender)
                where.setdefault(key, np.asarray(det.get("position"), dtype=float))
        claims = {s: np.asarray(m.position, dtype=float) for s, m in tele.items()}
        out: dict[int, tuple[int, int]] = {}
        for sender, msg in tele.items():
            corrob = uncorrob = 0
            for det in msg.detections:
                key = str(det.get("target_id"))
                spot = where.get(key)
                if spot is None:
                    continue
                witnesses = {
                    peer
                    for peer, pos in claims.items()
                    if peer != sender
                    and float(np.linalg.norm(pos[:2] - spot[:2])) <= reach
                }
                if seen.get(key, set()) & witnesses:
                    corrob += 1
                elif len(witnesses) >= 2:
                    uncorrob += 1     # corroborated silence: real evidence
                # otherwise nobody could have checked, or only one peer could
                # and its silence is unfalsifiable: scored neither way
            out[sender] = (corrob, uncorrob)
        return out

    # ------------------------------------------------------------------
    # 4. runtime trust prior (AdvGuard-Lite)
    # ------------------------------------------------------------------
    def _perception_tick(self) -> None:
        """Feed each drone's perception monitor one frame of its own detector.

        Honest scoping note: in simulation there is no real detector, so the
        confidence stream is synthesised.  A drone whose *perception* is being
        manipulated (false target injection) produces the flatter, less stable
        confidence distribution that adversarial patches are known to cause.
        The signal is deliberately noisy and only weakly informative - it is a
        prior on credibility, never a detector in its own right.
        """
        if not self.cfg.enable_trust_priors:
            return
        for i in self._contributing():
            d = self.drones[i]
            manipulated = d.compromised and self.scenario in {
                "false_target",
                "collusion",
            } and d.behaviour.active(self.tick)
            k = 4
            if manipulated:
                conf = self.rng.uniform(0.25, 0.75, size=k)
                classes = self.rng.integers(0, 3, size=k).tolist()
                expected = k + int(self.rng.integers(0, 2))
            else:
                conf = np.clip(self.rng.normal(0.86, 0.06, size=k), 0.05, 0.99)
                classes = [1] * k
                expected = k
            frame = PerceptionFrame(
                tick=self.tick,
                confidences=[float(c) for c in conf],
                class_ids=[int(c) for c in classes],
                expected_detections=expected,
            )
            report = d.monitor.update(frame)
            self.credibility.set_runtime(i, report.tau_runtime)

    # ------------------------------------------------------------------
    # 5. consensus
    # ------------------------------------------------------------------
    def _accusation_round(self) -> list[ExclusionEvent]:
        live = self._contributing()
        reports: dict[int, dict[int, float]] = {}
        evidence: dict[int, dict[int, ClaimEvidence]] = {}
        for i in live:
            d = self.drones[i]
            peers = [j for j in live if j != i]
            scores = d.engine.suspicion_vector(peers)
            payload = d.engine.evidence_payload(peers)
            scores = d.behaviour.corrupt_suspicion(self.tick, scores)
            # An attacker that fabricates a suspicion score without also
            # fabricating the evidence behind it is a strawman - ClaimCheck's
            # binding test would catch it for free.  ``corrupt_claim_evidence``
            # lets the attacker forge an internally consistent evidence
            # payload too, so the verifier has to beat a competent liar.
            payload = d.behaviour.corrupt_claim_evidence(self.tick, scores, payload)
            # Gossip only reaches drones with a live link.
            if self.mesh.neighbours(i) or len(live) == 1:
                reports[i] = scores
                evidence[i] = {
                    p: ClaimEvidence(
                        z1=float(v.get("z1", 0.0)),
                        z2=float(v.get("z2", 0.0)),
                        z3=float(v.get("z3", 0.0)),
                        cusum=float(v.get("cusum", 0.0)),
                        present=True,
                    )
                    for p, v in payload.items()
                }
        bundle = ClaimBundle(
            tick=self.tick,
            claims={i: np.asarray(self.drones[i].claimed, float) for i in live},
            ranges={
                i: dict(t) for i, t in self.published_ranges.items() if i in live
            },
            evidence=evidence,
            anchors=frozenset(self.cfg.mesh.certified_anchors),
        )
        events = self.consensus.round(self.tick, reports, bundle)
        self._drain_blocks()
        for ev in events:
            self._handle_exclusion(ev)
        return events

    def _drain_blocks(self) -> None:
        """Move new ClaimCheck refusals into the mission event log.

        A refusal is a decision the swarm made, with reasons, and it belongs in
        the flight recorder next to the expulsions - arguably more than they
        do.  An expulsion that happened is visible in the drone list; an
        expulsion that *nearly* happened and was stopped by independent
        verification leaves no other trace, and it is the thing an operator
        reviewing the mission most needs to see.
        """
        seen = getattr(self, "_blocks_seen", 0)
        pending = self.consensus.blocked[seen:]
        for record in pending:
            target = record["target"]
            self.event_log.append(
                {
                    "tick": record["tick"],
                    "kind": "claimcheck_block",
                    "target": target,
                    "action": record["action"],
                    "reason": record["reason"],
                    "honest": target not in self.compromised,
                    "vote_weight": record["vote_weight"],
                    "vote_accusers": record["vote_accusers"],
                    "supported": record["supported"],
                    "refuted": record["refuted"],
                    "quorum_required": record["quorum_required"],
                }
            )
        self._blocks_seen = len(self.consensus.blocked)

    def _handle_exclusion(self, ev: ExclusionEvent) -> None:
        target = ev.target
        d = self.drones.get(target)
        if d is None or d.excluded_at is not None:
            return
        d.excluded_at = ev.tick
        d.returning = True  # return-to-home command
        # Per-accuser evidence-channel snapshot, for the "why was this drone
        # excluded" explanation - must be gathered *before* the forget loop
        # below erases each accuser's assessment of ``target``.  Empty for a
        # ``fabrication_breadth`` exclusion: that mechanism never depends on
        # the target's own evidence being bad (it never accused anyone).
        evidence = []
        for a in ev.accusers:
            assess = self.drones[a].engine.assessments.get(target)
            if assess is None:
                continue
            evidence.append(
                {
                    "accuser": a,
                    "credibility": round(self.credibility.weight(a), 3),
                    "dominant": assess.dominant,
                    "z": [round(assess.z1, 2), round(assess.z2, 2), round(assess.z3, 2)],
                    "offset_m": assess.detail.get("e2_offset_m"),
                    "observers": assess.detail.get("e2_observers"),
                }
            )
        remaining = [j for j in self._contributing() if j != target]
        # Corroboration from a node the swarm has just decided is compromised
        # is not evidence.  Retracting it retroactively is what stops an
        # attacker from banking a confirmed target before it is caught.
        self.mission.purge_observer(target)
        record = self.mission.reallocate(target, remaining, ev.tick)
        if record:
            self.reallocations.append(record)
            for j in remaining:
                self._plan_for(j)
        for j in self._contributing():
            self.drones[j].engine.forget(target)
        self.events.append(ev)
        self.event_log.append(
            {
                "tick": ev.tick,
                "kind": "exclusion",
                "target": target,
                "reason": ev.reason,
                "honest": not d.compromised,
                "accusers": list(ev.accusers),
                "weight": round(ev.weight, 4),
                "quorum_required": ev.quorum_required,
                "detail": ev.detail,
                "evidence": evidence,
            }
        )

    # ------------------------------------------------------------------
    # mission bookkeeping
    # ------------------------------------------------------------------
    def _mission_tick(self) -> None:
        for i in self._contributing():
            d = self.drones[i]
            self.mission.observe(i, d.state.position)
        # Only *trusted* observers confirm a target: an expelled drone's
        # reports are dropped from the shared picture.
        trusted = self._contributing()
        reports: dict[str, dict] = {}
        for i in trusted:
            for det in self.drones[i].detections:
                # Note: ``phantom`` is a simulator bookkeeping flag only - the
                # swarm never reads it.  Fabricated detections enter the shared
                # picture exactly like real ones and must be filtered by the
                # ``target_confirm_observers`` corroboration rule alone.
                rid = str(det["target_id"])
                rec = reports.setdefault(
                    rid, {"position": det["position"], "observers": set()}
                )
                rec["observers"].add(i)
        self.mission.ingest(reports, self.tick)
        claims = {i: self.drones[i].claimed for i in trusted}
        self.mission.assign_confirmations(claims, trusted, self.tick)
        newly = self.mission.confirm(self.tick)
        for tgt in newly:
            self.event_log.append(
                {"tick": self.tick, "kind": "target_confirmed", "target_id": tgt.target_id}
            )

    # ------------------------------------------------------------------
    def _record(self, events: list[ExclusionEvent]) -> None:
        susp: dict[int, float] = {}
        live = self._contributing()
        for j in range(self.n):
            vals = [
                self.drones[i].engine.assessments[j].suspicion
                for i in live
                if j in self.drones[i].engine.assessments and i != j
            ]
            susp[j] = float(np.mean(vals)) if vals else 0.0
        progress = self.mission.progress()
        self.history.append(
            TickRecord(
                tick=self.tick,
                phase=self.mission.phase,
                positions={
                    i: [round(float(v), 2) for v in self.drones[i].state.position]
                    for i in self._live()
                },
                claimed={
                    i: [round(float(v), 2) for v in self.drones[i].claimed]
                    for i in self._live()
                },
                suspicion={k: round(v, 4) for k, v in susp.items()},
                weights={
                    k: round(v, 4) for k, v in self.credibility.weights().items()
                },
                excluded=sorted(self.consensus.excluded),
                coverage=round(progress["coverage"], 4),
                targets_confirmed=int(progress["targets_confirmed"]),
                connectivity=round(self.mesh.connectivity(), 4),
                events=[
                    {"target": e.target, "weight": round(e.weight, 3)} for e in events
                ],
            )
        )

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def step(self) -> TickRecord:
        started = time.perf_counter()
        self.tick += 1

        if self.mission.phase == "formation" and self.tick > self.mission.formation_ticks:
            self.mission.phase = "search"
            for i in self._contributing():
                self._plan_for(i)

        self._step_physics()
        # Topology first: link feasibility and ranging noise are functions of
        # where the airframes actually are *this* tick, so both the broadcast
        # step and the range measurements must see the refreshed geometry.
        self.mesh.update_topology(self._true_positions(), self._alive_mask())
        claims, _ = self._speak()
        self._report_ranges(claims)
        self.mesh.deliver(self.tick)
        self._score(self.tick)
        self._perception_tick()

        events: list[ExclusionEvent] = []
        if self.enable_consensus and self.tick % self.cfg.consensus.gossip_period == 0:
            events = self._accusation_round()

        self._mission_tick()
        self._record(events)
        self.tick_seconds.append(time.perf_counter() - started)
        return self.history[-1]

    def run(self, max_ticks: int | None = None) -> "Swarm":
        limit = max_ticks if max_ticks is not None else self.cfg.max_ticks
        while self.tick < limit and any(d.alive for d in self.drones.values()):
            self.step()
            if self.mission.success and self.mission.phase == "search":
                break
        return self

    # ------------------------------------------------------------------
    def summary(self) -> dict:
        """Everything the metrics layer needs, computed from the recorder."""
        honest = [i for i in range(self.n) if i not in self.compromised]
        excluded = dict(self.consensus.excluded)
        expelled_bad = sorted(i for i in excluded if i in self.compromised)
        expelled_good = sorted(i for i in excluded if i not in self.compromised)
        latencies = [
            excluded[i] - self.attack_start
            for i in expelled_bad
            if excluded[i] >= self.attack_start
        ]
        progress = self.mission.progress()
        per_drone_ms = (
            1000.0 * float(np.mean(self.tick_seconds)) / max(1, len(self._live()))
            if self.tick_seconds
            else 0.0
        )
        quarantined = dict(self.consensus.safety.quarantined)
        blocked = list(self.consensus.blocked)
        return {
            "scenario": self.scenario,
            "n_drones": self.n,
            "compromised": list(self.compromised),
            # ClaimCheck outcomes.  ``blocked_expulsions`` is the headline
            # safety number: expulsions the majority vote had already agreed
            # on and independent verification refused to carry out.
            "quarantined": quarantined,
            "quarantined_honest": sorted(
                i for i in quarantined if i not in self.compromised
            ),
            "blocked_expulsions": len(blocked),
            "blocked_honest": len(
                {b["target"] for b in blocked if b["target"] not in self.compromised}
            ),
            "ticks": self.tick,
            "attack_start": self.attack_start,
            "excluded": excluded,
            "true_positives": expelled_bad,
            "false_exclusions": expelled_good,
            "detection_latency": (
                float(np.mean(latencies)) if latencies else None
            ),
            "detection_latency_s": (
                float(np.mean(latencies)) * self.cfg.dt if latencies else None
            ),
            "false_exclusion_rate": (
                len(expelled_good) / len(honest) if honest else 0.0
            ),
            "recall": (
                len(expelled_bad) / len(self.compromised) if self.compromised else None
            ),
            "mission_success": bool(self.mission.success),
            "coverage": progress["coverage"],
            "targets_confirmed": progress["targets_confirmed"],
            "targets_total": progress["targets_total"],
            "reallocations": len(self.reallocations),
            "ms_per_drone_tick": round(per_drone_ms, 4),
            "config": {
                "enable_consensus": self.cfg.enable_consensus,
                "enable_trust_priors": self.cfg.enable_trust_priors,
                "enable_claim_verifier": self.cfg.enable_claim_verifier,
            },
        }
