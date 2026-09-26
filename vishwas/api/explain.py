"""Turn one exclusion event into a plain-English explanation.

The dashboard's "why was this drone excluded" panel exists for one audience:
someone who did not watch the whole mission and wants to know, in one
paragraph, whether the swarm got it right and *how it knows*. This module
computes nothing new - every number quoted here already exists on the
:class:`~vishwas.consensus.voting.ExclusionEvent` and the per-accuser
evidence snapshot ``vishwas.sim.swarm.Swarm._handle_exclusion`` records
before it forgets the target. This is prose over that data, not a second
opinion.

Two shapes, matching the two ways :mod:`vishwas.consensus.voting` can expel a
drone:

``trust_weighted_quorum``
    The normal path - other drones' own evidence (E1/E2/E3) turned suspicious
    of the target, and enough independently-credible peers agreed.
``fabrication_breadth``
    The self-detection path - the target's *own* telemetry was clean, but it
    was persistently, broadly accusing peers the rest of the swarm never
    corroborated. See ``ConsensusEngine._track_fabrication`` for the design.
"""

from __future__ import annotations

from typing import Any

CHANNEL_NAMES: dict[str, str] = {
    "E1": "self-consistency (its telemetry vs. its own flight envelope)",
    "E2": "cross-observation ranging (independent peer radios, not the target's own word)",
    "E3": "mission-logic consistency (formation slot and target-confirmation behaviour)",
    "none": "no single dominant channel",
}


def _dominant_channel(evidence: list[dict[str, Any]]) -> str:
    """Majority vote of each accuser's own dominant channel - matches the
    same rule the live Plot view uses (``frames._peer_view``), so the
    explanation never disagrees with what was on screen during the mission.
    """
    votes: dict[str, int] = {}
    for e in evidence:
        ch = e.get("dominant") or "none"
        votes[ch] = votes.get(ch, 0) + 1
    return max(votes, key=lambda k: votes[k]) if votes else "none"


def _mean_offset(evidence: list[dict[str, Any]]) -> float | None:
    vals = [e["offset_m"] for e in evidence if e.get("offset_m") is not None]
    return sum(vals) / len(vals) if vals else None


def _victim_list(named: list[int], cap: int = 6) -> str:
    """Render a named-target list; a fabricator often names most of the
    swarm, and reading eleven drone numbers in a row proves nothing a count
    doesn't already say more clearly.
    """
    if not named:
        return "several different peers"
    names = [f"drone {v}" for v in named[:cap]]
    if len(named) > cap:
        names.append(f"and {len(named) - cap} more")
    return ", ".join(names)


def explain_exclusion(entry: dict[str, Any], *, dt: float = 0.2) -> str:
    """Build the narrative for one ``kind == "exclusion"`` event-log entry.

    ``entry`` is exactly what ``Swarm._handle_exclusion`` appends to
    ``swarm.event_log`` - this function is pure and never touches the
    simulator, so it can run identically over a live stream or a finished
    mission's stored log.
    """
    target = entry["target"]
    tick = entry["tick"]
    seconds = round(tick * dt, 1)
    reason = entry.get("reason", "trust_weighted_quorum")
    detail = entry.get("detail") or {}
    evidence = entry.get("evidence") or []

    if reason == "fabrication_breadth":
        return _explain_fabrication(target, tick, seconds, entry, detail)
    return _explain_quorum(target, tick, seconds, entry, detail, evidence)


def _explain_quorum(
    target: int,
    tick: int,
    seconds: float,
    entry: dict[str, Any],
    detail: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> str:
    accusers = entry.get("accusers") or []
    weight = float(entry.get("weight", 0.0))
    quorum_required = entry.get("quorum_required", 0)
    trimmed = int(detail.get("trimmed", 0) or 0)
    f_tol = detail.get("f_tolerated")
    threshold = detail.get("quorum_weight_threshold")
    sustained = detail.get("sustained_rounds")
    n_reports = len(detail.get("triples") or [])

    dominant = _dominant_channel(evidence)
    mean_offset = _mean_offset(evidence)

    sentences = [
        f"At tick {tick} (t+{seconds}s), {len(accusers)} of the swarm's live drones "
        f"independently accused drone {target}, clearing the required quorum of "
        f"{quorum_required} independently-credible witnesses"
        + (f" (the Byzantine bound tolerates up to f = {f_tol} liars at this swarm size)." if f_tol is not None else "."),
        f"The dominant evidence channel behind those accusations was {CHANNEL_NAMES.get(dominant, dominant)}"
        + (
            f", with accusing peers measuring an average {mean_offset:.1f} m gap between where "
            f"drone {target} claimed to be and where independent peer ranging actually placed it."
            if mean_offset is not None
            else "."
        ),
        "Each accuser's report was weighted by its own earned credibility (trust priors plus its "
        "track record of accusations the swarm later upheld or rejected), and "
        + (
            f"the {trimmed} highest and {trimmed} lowest of the {n_reports} reports received were "
            "trimmed before averaging - a Byzantine-robust safeguard so no small coalition of "
            "accusers, however loud, could dominate the vote on its own."
            if trimmed
            else f"none of the {n_reports} reports needed trimming at this swarm size."
        ),
        f"The resulting trust-weighted suspicion score reached {weight:.2f}"
        + (f" against an exclusion threshold of {threshold:.2f}" if threshold is not None else "")
        + (
            f", and stayed above it for {sustained} consecutive accusation rounds before the "
            "swarm acted - a grace window so a single noisy tick can't trigger an expulsion."
            if sustained
            else "."
        ),
    ]
    return " ".join(sentences)


def _explain_fabrication(
    target: int,
    tick: int,
    seconds: float,
    entry: dict[str, Any],
    detail: dict[str, Any],
) -> str:
    named = detail.get("named_targets") or []
    sustained = detail.get("sustained_rounds")
    breadth = float(entry.get("weight", 0.0))
    threshold = detail.get("breadth_threshold")

    victims = _victim_list(named)
    persistence = f"over {sustained} consecutive gossip rounds, " if sustained else "consistently, "

    sentences = [
        f"Drone {target} was not excluded because its own telemetry looked wrong - every evidence "
        "channel (self-consistency, cross-observation ranging, mission logic) stayed clean for it "
        "for the whole mission, so the normal accusation path above could never target it.",
        "A second, independent detector catches this shape directly by watching what a drone "
        f"*accuses*, not what it does: {persistence}drone {target} kept naming victims ({victims}) "
        "that no other drone in the swarm ever independently corroborated.",
        f"That persistent, uncorroborated breadth score reached {breadth:.1f}"
        + (f" against a threshold of {threshold:.1f}" if threshold is not None else "")
        + " - the signature of a drone fabricating suspicion about its peers to get them wrongly "
        "expelled, not of an honest drone making one mistake.",
        f"At tick {tick} (t+{seconds}s), the swarm expelled drone {target} for that pattern alone.",
    ]
    return " ".join(sentences)


# ----------------------------------------------------------------------
def explain_block(entry: dict[str, Any], dt: float) -> str:
    """Explain an expulsion that ClaimCheck refused to carry out.

    This is the event the whole verifier exists to produce, so the prose has a
    specific job: make clear that the swarm *did* reach a majority, and that
    the majority is not what decided the outcome.  Same discipline as
    ``explain_exclusion`` - every number quoted is already on the entry.
    """
    target = entry.get("target")
    honest = entry.get("honest")
    accusers = entry.get("vote_accusers") or []
    supported = entry.get("supported") or []
    refuted = entry.get("refuted") or []
    required = entry.get("quorum_required", 0)
    weight = entry.get("vote_weight")
    when = f"at t = {entry.get('tick', 0) * dt:.1f} s"

    lead = (
        f"Drone {target} was **not** expelled {when}, although the weighted "
        f"quorum had already agreed to it"
    )
    if weight is not None:
        lead += f" (tally {weight:.2f} from {len(accusers)} accusers)"
    lead += "."

    if refuted and not supported:
        body = (
            f" Independent re-derivation refuted every one of the "
            f"{len(refuted)} claims: for each accuser, the target's position "
            f"was re-fixed by multilateration from the *other* drones' "
            f"published ranges - with that accuser excluded from its own "
            f"alibi - and the result agreed with where the target said it "
            f"was. Accusers {', '.join(f'drone {a}' for a in refuted)} each "
            f"paid credibility for the false claim."
        )
    elif entry.get("reason", "").startswith("range reports split"):
        body = (
            " The swarm's own range reports had split into two internally "
            "consistent but mutually contradictory accounts of where "
            "everybody is. Range geometry alone cannot say which account is "
            "real, and choosing the larger one would be a majority vote - "
            "exactly the mechanism a compromised majority defeats. VISHWAS "
            "therefore declined to convict anyone on it."
        )
    else:
        body = (
            f" Only {len(supported)} of {len(accusers)} accusations survived "
            f"independent verification; {required} are required to expel."
        )

    tail = (
        f" Ground truth: drone {target} was **honest**, so this refusal "
        f"prevented a false expulsion."
        if honest
        else (
            f" Ground truth: drone {target} was in fact compromised, so this "
            f"is the cost of the safe default - VISHWAS held rather than "
            f"convicted on evidence it could not independently verify."
        )
    )
    return lead + body + tail
