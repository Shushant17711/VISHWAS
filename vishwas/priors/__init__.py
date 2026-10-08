"""The two outer trust layers that feed accuser credibility.

TrustChain-SBOM is static and pre-flight: which airframes carry unpatched
CVEs or unverified-origin components.  AdvGuard-Lite is runtime: whose
perception stack is showing signs of adversarial manipulation.

Neither layer decides anything on its own.  Both produce a scalar in
[0, 1] that the consensus layer uses to weight a vote.
"""

from __future__ import annotations

from .advguard import (
    PatchRobustness,
    PerceptionFrame,
    PerceptionMonitor,
    RuntimeReport,
)
from .trustchain import (
    Component,
    SbomReport,
    TrustChainScorer,
    Vulnerability,
    load_grype,
    load_syft,
)

__all__ = [
    "PatchRobustness",
    "PerceptionFrame",
    "PerceptionMonitor",
    "RuntimeReport",
    "Component",
    "SbomReport",
    "TrustChainScorer",
    "Vulnerability",
    "load_grype",
    "load_syft",
]
