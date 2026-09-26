"""The action map: only one verdict may ever remove an airframe.

Expulsion in this system is irreversible - the drone is commanded home, its
sector is reallocated and its detections are retracted.  That is the right
price for a confirmed attacker and the wrong price for a wrongly-accused
honest aircraft, so every uncertain outcome has to resolve to the reversible
state instead.  These tests pin that asymmetry.
"""

from __future__ import annotations

from vishwas.config import ClaimCheckConfig
from vishwas.verify import SafetyEngine, Verdict
from vishwas.verify.verdict import Finding, TargetAudit


def _audit(target: int, verdicts: dict[int, Verdict], **kw) -> TargetAudit:
    audit = TargetAudit(target=target, **kw)
    audit.findings = [
        Finding(accuser=a, target=target, verdict=v) for a, v in verdicts.items()
    ]
    return audit


def test_enough_supported_claims_expels():
    engine = SafetyEngine(ClaimCheckConfig())
    audit = _audit(4, {i: Verdict.SUPPORTED for i in (0, 1, 2, 3, 5)})

    decision = engine.decide(100, 4, audit, quorum_size=5)
    assert decision.action == "expel"
    assert not decision.blocks_expulsion


def test_refuted_claims_never_reach_quorum():
    """Five refuted accusers are worth nothing, not five-fifths of a quorum."""
    engine = SafetyEngine(ClaimCheckConfig())
    audit = _audit(4, {i: Verdict.REFUTED for i in (0, 1, 2, 3, 5)})

    decision = engine.decide(100, 4, audit, quorum_size=5)
    assert decision.action == "quarantine"
    assert decision.supported == []
    assert decision.refuted == [0, 1, 2, 3, 5]


def test_contested_geometry_blocks_even_supported_claims():
    """When the measurements are contested, a supported claim is not enough.

    A bloc large enough to split the geometry is also large enough to make its
    own fabricated world look self-consistent, so the support those claims
    appear to have is exactly what cannot be trusted.
    """
    engine = SafetyEngine(ClaimCheckConfig())
    audit = _audit(
        4, {i: Verdict.SUPPORTED for i in (0, 1, 2, 3, 5)}, contested=True
    )

    decision = engine.decide(100, 4, audit, quorum_size=5)
    assert decision.action == "quarantine"
    assert "outside the swarm" in decision.reason


def test_ambiguous_claims_hold_rather_than_convict():
    engine = SafetyEngine(ClaimCheckConfig())
    audit = _audit(4, {i: Verdict.AMBIGUOUS for i in (0, 1, 2, 3, 5)})

    assert engine.decide(100, 4, audit, quorum_size=5).action == "quarantine"


def test_quarantine_is_reversible():
    """Silence earns release - a held drone is not quietly expelled by timeout."""
    cc = ClaimCheckConfig(quarantine_release_rounds=3)
    engine = SafetyEngine(cc)
    assert engine.quarantine(10, 4, "contested") is True
    assert engine.quarantine(11, 4, "contested") is False   # already held
    assert engine.is_quarantined(4)

    for _ in range(2):
        assert engine.sustain(set()) == []
        assert engine.is_quarantined(4)
    assert engine.sustain(set()) == [4]
    assert not engine.is_quarantined(4)


def test_continued_accusation_keeps_a_drone_held():
    cc = ClaimCheckConfig(quarantine_release_rounds=2)
    engine = SafetyEngine(cc)
    engine.quarantine(10, 4, "contested")

    for _ in range(6):
        assert engine.sustain({4}) == []
    assert engine.is_quarantined(4)
