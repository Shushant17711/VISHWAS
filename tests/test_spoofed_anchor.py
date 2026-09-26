"""A hardware root of trust proves identity, not truth.

These tests exist because the certified-anchor design invites one obvious
challenge - *if attestation fixes this, why not attest every drone?* - and the
answer has to be demonstrable rather than asserted. GNSS spoofing walks
straight past a secure element: the receiver is fed a counterfeit
constellation, the flight computer believes the position it derives, and it
broadcasts that position in good faith over an authenticated link, from
unmodified firmware, with a valid attestation chain. Every hardware check
passes and the data is still wrong.

So the anchor's privilege has to be conditional on its measurements agreeing
with everybody else's, and that is what is pinned here.
"""

from __future__ import annotations

import pytest

from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm

pytestmark = pytest.mark.slow

N = 9
TICKS = 500


def fly(scenario: str, n_compromised: int, anchors: tuple[int, ...], seed: int = 7):
    cfg = SimConfig(n_drones=N, max_ticks=TICKS, seed=seed)
    cfg.mesh.certified_anchors = anchors
    swarm = Swarm(cfg, scenario=scenario, n_compromised=n_compromised)
    swarm.run()
    return swarm


# ----------------------------------------------------------------------
def test_the_spoofed_drone_is_the_attested_one():
    """The scenario is worthless unless the anchor itself is the liar.

    Everywhere else the compromise draw deliberately skips anchors, which is
    correct for a key-extraction threat model. Sensor spoofing is a different
    threat and needs the opposite draw.
    """
    swarm = fly("spoofed_anchor", 1, anchors=(0,))

    assert swarm.compromised == (0,)


def test_spoofed_anchor_loses_its_reference_status():
    """Sustained contradiction by independent ranging withdraws the badge."""
    swarm = fly("spoofed_anchor", 1, anchors=(0,))
    verifier = swarm.consensus.verifier

    assert 0 in verifier.spoofed_anchors
    assert 0 not in verifier.trusted_anchors


def test_spoofed_anchor_is_caught_and_nobody_honest_is_lost():
    """The headline of the scenario: physics beats the badge, safely.

    Peer ranging is time-of-flight and never consults GNSS, so a spoofed drone
    is still measured correctly by its neighbours even while it is wrong about
    itself. That asymmetry is what makes it catchable at all.
    """
    swarm = fly("spoofed_anchor", 1, anchors=(0,))
    summary = swarm.summary()

    assert summary["true_positives"] == [0]
    assert summary["false_exclusions"] == []


def test_an_honest_anchor_is_never_demoted():
    """The other half of the property, and the easier one to get wrong.

    Demoting the swarm's one trustworthy reference on measurement noise would
    be its own failure mode - it would quietly remove the mechanism that makes
    the anchored mode work at all.
    """
    swarm = fly("collusion", 3, anchors=(0,))
    verifier = swarm.consensus.verifier

    assert verifier.spoofed_anchors == set()
    assert 0 in verifier.trusted_anchors
    assert swarm.summary()["false_exclusions"] == []
