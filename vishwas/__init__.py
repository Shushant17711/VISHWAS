"""VISHWAS - Verified Integrity & Swarm Health Weighted Assurance System.

Trust-weighted Byzantine consensus for autonomous drone swarms: enabling a
swarm to detect and expel a compromised member using physical-plausibility
evidence, with no ground station, no central authority, and no cryptographic
assumption that the drone itself is honest.

Layout
------
``vishwas.sim``
    Kinematics, mesh radio, attack injection, mission model, swarm loop.
``vishwas.evidence``
    E1 self-consistency, E2 cross-observation, E3 mission-logic, CUSUM,
    and the per-drone Evidence Engine that fuses them.
``vishwas.consensus``
    Accuser credibility and the trust-weighted quorum that decides expulsion.
``vishwas.priors``
    TrustChain-SBOM (static) and AdvGuard-Lite (runtime) trust layers.
``vishwas.evaluation``
    Scenario runner, metrics, ablation study and Monte Carlo driver.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .config import SimConfig

__all__ = ["SimConfig", "__version__"]
