"""End-to-end: a compromised majority must not be able to expel the innocent.

The unit tests pin each verification rule in isolation against a synthetic
bundle.  This file flies real missions, because the property that matters is
not "the audit returns REFUTED for this input" - it is that a full swarm, with
real noise, real mesh dropouts and real evidence engines, does not remove an
honest airframe when the liars outnumber the honest ones.

These are slow by construction (minutes, not milliseconds).  Run the fast
suite with ``-m 'not slow'``.
"""

from __future__ import annotations

import pytest

from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm

pytestmark = pytest.mark.slow

N = 9
TICKS = 600


def fly(scenario: str, n_compromised: int, seed: int, verified: bool) -> dict:
    cfg = SimConfig(
        n_drones=N, max_ticks=TICKS, seed=seed, enable_claim_verifier=verified
    )
    swarm = Swarm(cfg, scenario=scenario, n_compromised=n_compromised)
    swarm.run()
    return swarm.summary()


# ----------------------------------------------------------------------
@pytest.mark.parametrize("n_compromised", [5, 6])
def test_fabricating_majority_expels_nobody(n_compromised):
    """The project's founding failure: liars in the majority removing peers.

    A ``byzantine_accuser`` bloc flies and ranges honestly and lies only in
    gossip - so it can out-vote the honest minority, and it leaves the
    independent re-derivation completely untouched.  Every one of its claims
    is refuted on geometry it does not control, however many of them there
    are.
    """
    summary = fly("byzantine_accuser", n_compromised, seed=7, verified=True)

    assert summary["false_exclusions"] == []


@pytest.mark.parametrize("n_compromised", [5, 6])
def test_colluding_majority_expels_nobody(n_compromised):
    """The harder case, where the liars forge their range reports too.

    Here the audit cannot identify the liars - two internally consistent
    worlds are observationally equivalent under range-only data.  The property
    under test is therefore the safe failure, not detection: no honest drone
    is expelled, whatever the vote wanted.
    """
    summary = fly("collusion", n_compromised, seed=7, verified=True)

    assert summary["false_exclusions"] == []


def test_verifier_attributes_refutations_to_the_liars():
    """Refuted claims must land on the fabricators, not be spread around.

    A verifier that refuted claims at random would also produce zero false
    expulsions, and would be worthless.  The ledger has to name the right
    drones.
    """
    cfg = SimConfig(n_drones=N, max_ticks=300, seed=7)
    swarm = Swarm(cfg, scenario="byzantine_accuser", n_compromised=5)
    swarm.run()

    ledger = swarm.consensus.verifier.ledger
    assert ledger.refuted_by_accuser, "no claim was refuted at all"
    assert set(ledger.refuted_by_accuser) <= set(swarm.compromised)


def test_detection_below_the_bound_is_preserved():
    """Verification must not cost recall where the vote was already sound.

    A safety mechanism that buys "never convicts the innocent" by never
    convicting anyone has not solved the problem.  Below the Byzantine bound
    the honest majority's independent geometry genuinely contradicts the
    liar's claim, so its accusations verify as SUPPORTED and the expulsion
    goes through as before.
    """
    summary = fly("position_teleport", 1, seed=7, verified=True)

    assert summary["true_positives"], "the verifier blocked a sound expulsion"
    assert summary["false_exclusions"] == []


def test_honest_control_run_stays_quiet():
    """Nobody lying means nothing refuted and nobody held."""
    summary = fly("none", 0, seed=7, verified=True)

    assert summary["false_exclusions"] == []
    assert summary["quarantined_honest"] == []


# ----------------------------------------------------------------------
def test_gossip_only_fabricator_is_disarmed_even_when_not_expelled():
    """The deliberate trade-off, pinned so a future change has to argue with it.

    A gossip-only fabricator and an honest drone surrounded by liars are
    observationally identical: both fly honestly, range honestly, and accuse
    peers the swarm declines to corroborate.  The only thing separating them
    is whether the accused are genuinely guilty, which is exactly what cannot
    be established once the compromised drones hold the majority.  The old
    fabricator detector resolved that ambiguity by counting corroboration -
    and counting inverts, so it expelled honest drones for correctly naming
    real attackers.

    Without a reference outside the swarm, VISHWAS therefore *disarms* the
    fabricator rather than expelling it: every claim it makes is refuted, so
    it causes no exclusions, but it keeps flying.  Victims safe, attacker
    still aboard.  That is the trade, and this test states both halves of it.
    """
    summary = fly("byzantine_accuser", 1, seed=7, verified=True)

    assert summary["false_exclusions"] == []       # nobody wrongly expelled
    assert summary["true_positives"] == []         # ...and nobody expelled at all


def test_a_certified_anchor_recovers_fabricator_detection():
    """One hardware root of trust resolves what the swarm alone cannot.

    The anchor is not a better vote - it is a reference outside the vote, so
    it settles which account of the geometry is real and lets verified
    evidence exist again.  With it, the fabricator is caught outright, and
    still nobody honest is lost.
    """
    cfg = SimConfig(n_drones=N, max_ticks=TICKS, seed=7)
    cfg.mesh.certified_anchors = (0,)
    swarm = Swarm(cfg, scenario="byzantine_accuser", n_compromised=1)
    swarm.run()
    summary = swarm.summary()

    assert summary["true_positives"] == list(summary["compromised"])
    assert summary["false_exclusions"] == []
