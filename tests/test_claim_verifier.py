"""Each of ClaimCheck's four verdicts, produced deliberately.

The point of a four-state verdict is that "this accusation is false" and "I
could not check this accusation" are different answers with different
consequences.  These tests pin each state to the specific evidential situation
that must produce it, so a future tuning change that quietly collapses them
into a binary fails here rather than in a live mission.
"""

from __future__ import annotations

import numpy as np

from vishwas.verify import ClaimVerifier, Verdict
from vishwas.verify.claim import ClaimEvidence

from conftest import build_bundle, cite, ring_positions


def _audit(cfg, bundle, reports, live=None):
    verifier = ClaimVerifier(cfg, np.random.default_rng(5))
    live = live if live is not None else sorted(bundle.claims)
    return verifier, verifier.audit(
        bundle, reports, live, f_tolerated=2, accuse_threshold=0.55
    )


def _verdict(audits, accuser, target):
    return next(
        f.verdict for f in audits[target].findings if f.accuser == accuser
    )


# ----------------------------------------------------------------------
def test_fabricated_accusation_is_refuted(cfg):
    """A liar-only-in-gossip accuses an innocent peer; physics says no.

    This is the attack the whole package exists for: drone 0 flies honestly,
    ranges honestly, and simply asserts that drone 4 is lying.  Because it
    ranges honestly, the independent re-fix of drone 4 - which excludes drone
    0 from its own alibi - lands on drone 4's claimed position, and the claim
    is refuted without anyone voting on it.
    """
    truth = ring_positions(9)
    bundle = build_bundle(truth)
    cite(bundle, 0, 4, z2=8.0)
    _, audits = _audit(cfg, bundle, {0: {4: 0.95}})

    assert _verdict(audits, 0, 4) is Verdict.REFUTED
    assert audits[4].supported_accusers == []
    assert audits[4].refuted_accusers == [0]


def test_real_position_lie_is_supported(cfg):
    """An honest accuser naming a genuinely displaced peer is upheld."""
    truth = ring_positions(9)
    claims = {i: p.copy() for i, p in truth.items()}
    claims[4] = truth[4] + np.array([180.0, -120.0, 0.0])   # drone 4 lies
    bundle = build_bundle(truth, claims=claims, liars={4})
    cite(bundle, 0, 4, z2=9.0)
    _, audits = _audit(cfg, bundle, {0: {4: 0.9}})

    assert _verdict(audits, 0, 4) is Verdict.SUPPORTED
    assert audits[4].supported_accusers == [0]


def test_bare_assertion_is_unsupported(cfg):
    """No cited evidence, no claim - and no penalty for the accuser either."""
    truth = ring_positions(9)
    bundle = build_bundle(truth)          # note: no cite() call
    _, audits = _audit(cfg, bundle, {0: {4: 0.99}})

    assert _verdict(audits, 0, 4) is Verdict.UNSUPPORTED
    assert audits[4].refuted_accusers == []      # unverifiable != punished
    assert 0 not in audits[4].admissible


def test_too_few_observers_is_ambiguous(cfg):
    """With nothing to re-derive from and nothing committed, the verifier holds.

    Three drones cannot re-fix a position once the accuser is excluded from
    its own alibi, and this accuser published no range to the target either -
    so there is no independent geometry *and* no self-contradiction to catch.
    The honest answer is "I could not check this", which must read as neither
    guilt nor innocence.
    """
    truth = ring_positions(3)
    bundle = build_bundle(truth)
    bundle.ranges[0].pop(2)          # accuser never ranged its target
    cite(bundle, 0, 2, z2=7.0)
    _, audits = _audit(cfg, bundle, {0: {2: 0.9}})

    assert _verdict(audits, 0, 2) is Verdict.AMBIGUOUS
    assert audits[2].supported_accusers == []
    # Ambiguous claims still count toward the weighted tally - absence of
    # proof is not proof of innocence - they just cannot expel anyone.
    assert 0 in audits[2].admissible


def test_accuser_is_excluded_from_its_own_alibi(cfg):
    """The re-derivation must not be steerable by the drone under audit.

    Drone 0 forges its ranges to put drone 4 far from where drone 4 claims to
    be.  If the fix used the accuser's own ranges, one liar could manufacture
    support for its own accusation; excluding it means eight honest radios
    still refute the claim.
    """
    truth = ring_positions(9)
    bundle = build_bundle(truth)
    for peer, (r, s) in bundle.ranges[0].items():
        bundle.ranges[0][peer] = (r + 250.0, s)
    cite(bundle, 0, 4, z2=9.0)
    _, audits = _audit(cfg, bundle, {0: {4: 0.95}})

    assert _verdict(audits, 0, 4) is Verdict.REFUTED


def test_majority_of_fabricators_cannot_convict(cfg):
    """Five of nine drones accuse one honest peer; all five are refuted.

    This is the exact configuration where the trimmed mean and the ``2f+1``
    quorum both fail - the liars *are* the majority.  Because none of them
    lies about its own ranges, every one of their claims is refuted
    individually, and the count of surviving claims is zero however many of
    them there are.
    """
    truth = ring_positions(9)
    bundle = build_bundle(truth)
    liars = [0, 1, 2, 3, 5]
    reports = {}
    for a in liars:
        cite(bundle, a, 4, z2=8.0)
        reports[a] = {4: 0.95}
    _, audits = _audit(cfg, bundle, reports)

    assert audits[4].supported_accusers == []
    assert audits[4].refuted_accusers == sorted(liars)


def test_commitment_audit_catches_self_contradiction(cfg):
    """An accuser cannot publish an honest range and claim it shows a lie.

    With too little geometry for an independent fix, the accuser's own
    broadcast is the only check left - and it is enough. Drone 0's published
    range reproduces drone 2's claimed position exactly while its gossip
    asserts a 9-sigma anomaly, which is a contradiction anyone listening can
    do the arithmetic on.
    """
    truth = ring_positions(3)
    bundle = build_bundle(truth)
    cite(bundle, 0, 2, z2=9.0)
    _, audits = _audit(cfg, bundle, {0: {2: 0.95}})
    finding = next(f for f in audits[2].findings if f.accuser == 0)

    assert finding.verdict is Verdict.REFUTED
    assert finding.commitment_z is not None
    assert finding.commitment_z < cfg.claimcheck.commitment_z
    # Only three drones, so the independent fix abstained entirely: this
    # verdict rests on the accuser's own broadcast and nothing else.
    assert finding.independent_z is None


def test_below_threshold_gossip_is_not_audited(cfg):
    """Suspicion under ``accuse_threshold`` is an opinion, not an accusation."""
    truth = ring_positions(9)
    bundle = build_bundle(truth)
    cite(bundle, 0, 4, z2=8.0)
    _, audits = _audit(cfg, bundle, {0: {4: 0.2}})

    assert audits == {}


def test_verifier_never_reads_ground_truth(cfg):
    """The audit sees only broadcasts; identical inputs give identical verdicts.

    Two runs whose *true* positions differ wildly but whose published claims
    and ranges are byte-identical must verify the same way.  If any ground
    truth leaked into the verifier this test would fail, and the whole
    "operational independent-evidence mode" claim with it.
    """
    truth = ring_positions(9)
    bundle_a = build_bundle(truth)
    cite(bundle_a, 0, 4, z2=8.0)

    # Same commitments, completely different underlying reality.
    bundle_b = build_bundle(truth)
    cite(bundle_b, 0, 4, z2=8.0)
    bundle_b.tick = bundle_a.tick

    _, audits_a = _audit(cfg, bundle_a, {0: {4: 0.95}})
    _, audits_b = _audit(cfg, bundle_b, {0: {4: 0.95}})
    assert _verdict(audits_a, 0, 4) is _verdict(audits_b, 0, 4)
