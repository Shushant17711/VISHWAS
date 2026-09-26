"""Wire format for the live mission stream.

The dashboard is a flight recorder, not a second implementation of the swarm.
Everything here is read out of a running :class:`~vishwas.sim.swarm.Swarm`;
nothing is recomputed and nothing is smoothed.

Two payload kinds:

``meta``
    Sent once when a mission is created - the fixed geometry (search sectors,
    target layout), the config that produced it, and the ground truth.
``frame``
    One per tick.  Small enough to push at 20 Hz over a websocket for a
    thirteen-drone swarm.

A note on ground truth: ``meta.compromised`` is the simulator's answer key.
It is streamed so the dashboard can score the swarm's decision live - honest
expulsions in green, false ones in red.  No part of VISHWAS reads it; the
drones never see this field, and neither does any metric.
"""

from __future__ import annotations

from typing import Any

from ..sim.swarm import Swarm, TickRecord
from .explain import explain_block, explain_exclusion

CHANNELS = ("E1", "E2", "E3")


def mission_meta(swarm: Swarm, spec: dict[str, Any]) -> dict[str, Any]:
    cfg = swarm.cfg
    mission = swarm.mission
    return {
        "spec": spec,
        "n_drones": swarm.n,
        "dt": cfg.dt,
        "max_ticks": cfg.max_ticks,
        "attack_start": swarm.attack_start,
        "scenario": swarm.scenario,
        "compromised": sorted(swarm.compromised),  # ground truth, see module docstring
        "enable_consensus": cfg.enable_consensus,
        "enable_trust_priors": cfg.enable_trust_priors,
        "enable_claim_verifier": cfg.enable_claim_verifier,
        "area_size": cfg.mission.area_size,
        "formation_ticks": mission.formation_ticks,
        "quorum_size": swarm.consensus.quorum_size,
        "f_tolerated": swarm.consensus.f_tolerated,
        "sectors": [
            {
                "index": s.index,
                "x0": round(s.x0, 1),
                "y0": round(s.y0, 1),
                "x1": round(s.x1, 1),
                "y1": round(s.y1, 1),
            }
            for s in mission.sectors
        ],
        "targets": [
            {
                "id": t.target_id,
                "position": [round(float(v), 1) for v in t.position],
            }
            for t in mission.targets
        ],
        "thresholds": {
            "cusum_h": cfg.evidence.cusum_threshold_h,
            "cusum_k": cfg.evidence.cusum_slack_k,
            "accuse": cfg.consensus.accuse_threshold,
            "quorum_weight": cfg.consensus.quorum_weight_threshold,
        },
    }


def _peer_view(swarm: Swarm, drone_id: int) -> dict[str, Any]:
    """The swarm's aggregate opinion of one drone, and which channel drives it.

    Suspicion in the tick record is the mean over live peers.  The dominant
    channel is the one most often named by those peers - a majority vote, so a
    single loud accuser cannot relabel the evidence.
    """
    live = [i for i in swarm._contributing() if i != drone_id]
    votes: dict[str, int] = {}
    z = [0.0, 0.0, 0.0]
    alarms = 0
    seen = 0
    for i in live:
        a = swarm.drones[i].engine.assessments.get(drone_id)
        if a is None or a.abstained:
            continue
        seen += 1
        votes[a.dominant] = votes.get(a.dominant, 0) + 1
        z[0] += a.z1
        z[1] += a.z2
        z[2] += a.z3
        alarms += int(a.alarmed)
    dominant = max(votes, key=lambda k: votes[k]) if votes else "none"
    return {
        "dominant": dominant,
        "z": [round(v / seen, 2) for v in z] if seen else [0.0, 0.0, 0.0],
        "alarms": alarms,
        "observers": seen,
    }


def frame(swarm: Swarm, record: TickRecord) -> dict[str, Any]:
    """One tick, as the dashboard reads it."""
    excluded = set(record.excluded)
    quarantined = set(swarm.consensus.safety.quarantined)
    drones: list[dict[str, Any]] = []
    for i in range(swarm.n):
        d = swarm.drones[i]
        view = _peer_view(swarm, i)
        drones.append(
            {
                "id": i,
                "pos": record.positions.get(i),
                "claimed": record.claimed.get(i),
                "suspicion": record.suspicion.get(i, 0.0),
                "weight": record.weights.get(i, 0.0),
                "excluded": i in excluded,
                # Reversible hold: the vote wanted this drone gone and
                # ClaimCheck would not certify it.  Distinct from ``excluded``
                # on purpose - the dashboard must not render a drone the swarm
                # declined to convict the same way as one it did.
                "quarantined": i in quarantined,
                "alive": bool(d.state.alive),
                "returning": bool(d.returning),
                "dominant": view["dominant"],
                "z": view["z"],
                "accusers": view["alarms"],
                "observers": view["observers"],
            }
        )
    return {
        "tick": record.tick,
        "t_s": round(record.tick * swarm.cfg.dt, 1),
        "phase": record.phase,
        "coverage": record.coverage,
        "connectivity": record.connectivity,
        "targets_confirmed": record.targets_confirmed,
        "targets_total": len(swarm.mission.targets),
        "excluded": record.excluded,
        "quarantined": sorted(quarantined),
        "claimcheck": _claimcheck_view(swarm),
        "drones": drones,
    }


def _claimcheck_view(swarm: Swarm) -> dict[str, Any] | None:
    """The verifier's current state, or ``None`` when it is switched off.

    ``blocked_expulsions`` is the number the demo turns on: expulsions the
    weighted quorum had already agreed to and independent verification refused
    to carry out.  ``contested`` is the swarm admitting, live, that its own
    range reports no longer describe one consistent world.
    """
    verifier = swarm.consensus.verifier
    if verifier is None:
        return None
    ledger = verifier.ledger
    return {
        "enabled": True,
        "geometry": verifier.geometry.as_row(),
        "blocked_expulsions": len(swarm.consensus.blocked),
        "verdicts": dict(ledger.counts),
        "refuted_by_accuser": dict(ledger.refuted_by_accuser),
    }


def events_since(swarm: Swarm, cursor: int) -> tuple[list[dict[str, Any]], int]:
    """Event-log tail plus the new cursor.  The log only ever appends.

    Exclusion entries get a plain-English ``explanation`` attached here, once,
    computed straight from the facts already on the entry - see
    :mod:`vishwas.api.explain`.  Computed at read time (not written back into
    ``swarm.event_log``) so the explainer stays a pure function of logged
    facts, never a second source of truth for them.
    """
    log = swarm.event_log
    tail = list(log[cursor:])
    dt = swarm.cfg.dt
    out: list[dict[str, Any]] = []
    for entry in tail:
        if entry.get("kind") == "exclusion":
            entry = {**entry, "explanation": explain_exclusion(entry, dt=dt)}
        elif entry.get("kind") == "claimcheck_block":
            entry = {**entry, "explanation": explain_block(entry, dt=dt)}
        out.append(entry)
    return out, len(log)
