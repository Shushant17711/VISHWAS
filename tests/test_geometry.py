"""Reciprocity: knowing *when* the swarm can no longer trust its own geometry.

``|r_ij - r_ji|`` is two noisy measurements of one distance, so a pair that
disagrees beyond its combined sigma contains at least one drone reporting a
world it is not in.  That fact is pairwise physics and cannot be voted on,
which is what makes it usable when the vote itself is compromised.

The property under test is deliberately *not* "the verifier identifies the
liars".  Two internally-consistent accounts of the same geometry are
observationally equivalent under range-only data - picking the larger one
would be the majority vote this whole package exists to avoid.  What is tested
is that the split is detected, so the swarm refuses to convict on it.
"""

from __future__ import annotations

import numpy as np

from vishwas.verify.recompute import assess_geometry

from conftest import build_bundle, ring_positions


def test_clean_swarm_is_one_world(cfg):
    truth = ring_positions(9)
    bundle = build_bundle(truth, noise=1.0)
    geo = assess_geometry(bundle, sorted(truth), cfg.claimcheck, f_tolerated=2)

    assert geo.dominant == set(range(9))
    assert geo.outliers == set()
    assert not geo.contested


def test_gossip_only_liars_leave_geometry_intact(cfg):
    """The attack that matters most does not disturb the range picture at all.

    A drone that flies honestly and lies only in gossip publishes true ranges,
    so reciprocity holds everywhere and the geometry stays arbitrable - which
    is exactly why the independent re-derivation can refute its accusations
    even when it has a majority.
    """
    truth = ring_positions(9)
    bundle = build_bundle(truth, noise=1.0)      # nobody forges ranges
    geo = assess_geometry(bundle, sorted(truth), cfg.claimcheck, f_tolerated=2)

    assert not geo.contested
    assert geo.dominant == set(range(9))


def test_minority_forgers_are_peeled_out(cfg):
    """A small colluding clique is isolated, not deferred to."""
    truth = ring_positions(9)
    claims = {i: p.copy() for i, p in truth.items()}
    liars = {2, 5}
    for i in liars:
        claims[i] = truth[i] + np.array([220.0, 160.0, 0.0])
    bundle = build_bundle(truth, claims=claims, liars=liars, noise=1.0)
    geo = assess_geometry(bundle, sorted(truth), cfg.claimcheck, f_tolerated=2)

    assert liars <= geo.outliers
    assert not geo.contested         # 2 outliers is within the budget f=2


def test_a_forging_majority_is_believed_and_that_is_the_known_limit(cfg):
    """The boundary of the whole approach, pinned so it cannot drift silently.

    Five of nine drones project a shared fabricated world.  Both accounts of
    the geometry are internally consistent, and range-only data genuinely
    cannot say which is real: "five honest against four liars" and "four
    honest against five liars" are the *same* measurements with the labels
    swapped.  Any rule that catches the attackers in the first case must
    believe them in the second.

    So this test asserts the uncomfortable thing rather than the thing the
    design would prefer: the forging majority is taken as the surviving world.
    Safety at this point does not come from the geometry - it comes from
    refusing to convict on unverified claims, from the majority cap in
    ``ConsensusEngine``, and ultimately from a certified anchor (see the next
    test).  Writing this down as an expectation is the only way a future
    change that quietly *appears* to fix it gets challenged instead of
    believed.
    """
    truth = ring_positions(9)
    claims = {i: p.copy() for i, p in truth.items()}
    liars = {0, 1, 2, 3, 4}
    for i in liars:
        claims[i] = truth[i] + np.array([240.0, -180.0, 0.0])
    bundle = build_bundle(truth, claims=claims, liars=liars, noise=1.0)
    geo = assess_geometry(bundle, sorted(truth), cfg.claimcheck, f_tolerated=2)

    assert geo.dominant == liars          # the liars, believed
    assert not geo.contested


def test_no_majority_account_is_contested(cfg):
    """When nothing holds a majority, nothing is relied on.

    Worth being precise about why this case has to be built by hand.  The
    optimal forgery in ``AttackBehaviour.corrupt_ranges`` re-derives ranges
    from the *published claims*, and every liar re-derives them from the same
    published claims - so any two liars agree with each other automatically,
    whatever direction each is lying in, while honest drones agree with each
    other on the measured truth.  The reciprocity graph under that attack is
    therefore always exactly two cliques, and the larger one always wins.  Two
    independent colluding pairs against five honest drones resolves cleanly
    for that reason, and six liars against three honest drones is believed for
    the same reason (see the test above).

    So the contested flag exists for uncoordinated corruption - drifting
    clocks, damaged radios, or attackers not sharing one script - where the
    swarm's picture of itself fragments rather than splitting in two.  Here
    that is built directly, by giving two disjoint groups independent range
    biases so neither agrees with anyone outside itself.
    """
    truth = ring_positions(9)
    bundle = build_bundle(truth, noise=1.0)
    for group, bias in (((0, 1, 2), 90.0), ((3, 4, 5), -70.0)):
        for i in group:
            bundle.ranges[i] = {
                peer: (r + bias, sigma)
                for peer, (r, sigma) in bundle.ranges[i].items()
            }
    geo = assess_geometry(bundle, sorted(truth), cfg.claimcheck, f_tolerated=2)

    assert 2 * len(geo.dominant) <= len(geo.live)
    assert geo.contested


def test_certified_anchor_resolves_a_contested_split(cfg):
    """One hardware root of trust turns "cannot tell" back into "can tell".

    The anchor is a reference from outside the vote, so the world it belongs
    to is pinned to reality however large the other one is.  This is the
    escape hatch the mesh config has always described and never had a caller
    for.
    """
    truth = ring_positions(9)
    claims = {i: p.copy() for i, p in truth.items()}
    liars = {0, 1, 2, 3, 4}
    for i in liars:
        claims[i] = truth[i] + np.array([240.0, -180.0, 0.0])
    bundle = build_bundle(
        truth, claims=claims, liars=liars, noise=1.0, anchors=frozenset({8})
    )
    geo = assess_geometry(bundle, sorted(truth), cfg.claimcheck, f_tolerated=2)

    assert 8 in geo.dominant       # the anchor is honest, so it survives peeling
    assert geo.anchor_backed
    assert not geo.contested
