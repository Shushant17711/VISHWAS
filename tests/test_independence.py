"""Counting only means something when the things counted are independent.

Two honest drones watching the same anomalous peer compute their z-scores from
their own radios at their own geometry; those numbers differ by measurement
noise every single time.  Evidence vectors that agree to floating-point noise
therefore did not come from two radios - they came from one script broadcast
twice, which is what a colluding bloc gets for free.  The counter-intuitive
rule is the correct one: near-exact agreement is duplication, not
corroboration.
"""

from __future__ import annotations

from vishwas.verify.claim import Accusation, ClaimEvidence
from vishwas.verify.independence import collapse_duplicates


def _acc(accuser: int, z2: float, present: bool = True) -> Accusation:
    return Accusation(
        accuser=accuser,
        target=99,
        score=0.9,
        evidence=ClaimEvidence(z2=z2, cusum=z2 / 6.0, present=present),
    )


def test_identical_evidence_collapses_to_one_root():
    accusations = [_acc(i, 7.5) for i in (1, 4, 6)]
    dupes = collapse_duplicates(accusations, tolerance=0.02)

    assert dupes == {4: 1, 6: 1}      # lowest id is the root, kept


def test_independently_measured_evidence_is_not_collapsed():
    """Real observations differ; the collapse must not punish that."""
    accusations = [_acc(1, 7.5), _acc(4, 8.9), _acc(6, 6.2)]
    dupes = collapse_duplicates(accusations, tolerance=0.02)

    assert dupes == {}


def test_root_choice_is_order_independent():
    forward = collapse_duplicates([_acc(i, 7.5) for i in (1, 4, 6)], 0.02)
    backward = collapse_duplicates([_acc(i, 7.5) for i in (6, 4, 1)], 0.02)

    assert forward == backward


def test_claims_citing_nothing_are_left_alone():
    """An empty claim has no evidence to be a copy *of*; it fails on its own."""
    accusations = [_acc(1, 0.0, present=False), _acc(4, 0.0, present=False)]

    assert collapse_duplicates(accusations, tolerance=0.02) == {}


def test_tolerance_zero_disables_collapsing():
    accusations = [_acc(i, 7.5) for i in (1, 4)]

    assert collapse_duplicates(accusations, tolerance=0.0) == {}
