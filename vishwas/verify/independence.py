"""Evidence independence - a clique reading one script is one witness.

The quorum rule counts accusers.  Counting only means something if the things
being counted were arrived at independently; ``2f+1`` signatures on one
sentence is one claim with 2f+1 signatures, not 2f+1 corroborating
observations.  A colluding bloc gets that for free, because its members can
simply broadcast the same fabricated evidence vector.

Real independent observations never coincide.  Two honest drones watching the
same genuinely anomalous peer compute z-scores from their *own* radios at
their *own* geometry, and those numbers differ by measurement noise every
time.  Evidence vectors that match to within floating-point noise did not
come from two radios; they came from one script running twice.  So near-exact
agreement is treated as duplication and collapsed to a single root claim,
which is the opposite of the intuitive reading - and the correct one.
"""

from __future__ import annotations

import numpy as np

from .claim import Accusation


def collapse_duplicates(
    accusations: list[Accusation], tolerance: float
) -> dict[int, int]:
    """Map accuser -> the accuser whose claim theirs duplicates.

    Only accusers *other* than the root appear in the result; the root of each
    cluster is kept and counts normally.  The lowest drone id in a cluster is
    chosen as the root so the outcome does not depend on iteration order.
    """
    if tolerance <= 0.0 or len(accusations) < 2:
        return {}

    ordered = sorted(accusations, key=lambda a: a.accuser)
    roots: list[Accusation] = []
    duplicate_of: dict[int, int] = {}
    for acc in ordered:
        if not acc.evidence.present:
            # A claim citing nothing has no evidence to be a copy *of*; it
            # resolves as UNSUPPORTED on its own merits.
            continue
        vec = acc.evidence.as_vector()
        for root in roots:
            base = root.evidence.as_vector()
            scale = max(float(np.abs(base).sum()), 1e-6)
            if float(np.abs(vec - base).sum()) / scale <= tolerance:
                duplicate_of[acc.accuser] = root.accuser
                break
        else:
            roots.append(acc)
    return duplicate_of
