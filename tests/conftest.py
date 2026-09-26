"""Synthetic swarms for unit-testing ClaimCheck without flying a mission.

The integration tests fly real missions and take minutes; these fixtures build
a :class:`~vishwas.verify.claim.ClaimBundle` directly so each verification rule
can be exercised in isolation, with the lie under test being the *only*
difference from a clean round.  That matters for a verifier: a test that
cannot say which measurement it corrupted cannot say what the verdict proves.
"""

from __future__ import annotations

import numpy as np
import pytest

from vishwas.config import SimConfig
from vishwas.verify.claim import ClaimBundle, ClaimEvidence

ALT = 120.0
RADIUS = 400.0


def ring_positions(n: int, radius: float = RADIUS) -> dict[int, np.ndarray]:
    """``n`` drones evenly spaced on a circle - good multilateration geometry."""
    return {
        i: np.array(
            [
                radius * np.cos(2 * np.pi * i / n),
                radius * np.sin(2 * np.pi * i / n),
                ALT,
            ]
        )
        for i in range(n)
    }


def sigma_for(distance: float) -> float:
    """Same noise model the mesh uses, so tolerances line up with production."""
    return 1.4 + 0.012 * float(distance)


def build_bundle(
    truth: dict[int, np.ndarray],
    *,
    claims: dict[int, np.ndarray] | None = None,
    liars: set[int] | None = None,
    noise: float = 0.0,
    seed: int = 3,
    anchors: frozenset[int] = frozenset(),
    tick: int = 100,
) -> ClaimBundle:
    """Assemble one round of public commitments.

    ``liars`` publish ranges re-derived from the *claimed* world (the optimal
    forgery in ``AttackBehaviour.corrupt_ranges``); everybody else publishes
    what their radio measured against ``truth``.  That single difference is
    what every verification rule here is ultimately reading.
    """
    rng = np.random.default_rng(seed)
    claims = dict(claims or {i: p.copy() for i, p in truth.items()})
    liars = set(liars or ())
    ranges: dict[int, dict[int, tuple[float, float]]] = {}
    for i in truth:
        table: dict[int, tuple[float, float]] = {}
        for j in truth:
            if i == j:
                continue
            if i in liars:
                d = float(np.linalg.norm(claims[i] - claims[j]))
            else:
                d = float(np.linalg.norm(truth[i] - truth[j]))
            s = sigma_for(d)
            table[j] = (d + (rng.normal(0.0, noise) if noise else 0.0), s)
        ranges[i] = table
    return ClaimBundle(tick=tick, claims=claims, ranges=ranges, anchors=anchors)


def cite(bundle: ClaimBundle, accuser: int, target: int, z2: float) -> None:
    """Attach an evidence payload asserting a ``z2``-sigma E2 anomaly."""
    bundle.evidence.setdefault(accuser, {})[target] = ClaimEvidence(
        z1=0.0, z2=float(z2), z3=0.0, cusum=float(z2) / 6.0, present=True
    )


@pytest.fixture
def cfg() -> SimConfig:
    return SimConfig(n_drones=9, seed=7)


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(19)
