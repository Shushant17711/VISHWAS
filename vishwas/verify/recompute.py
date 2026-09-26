"""Independent re-derivation - the part of the decision that is not a vote.

Three checks live here, and each one is arithmetic on measurements the swarm
already broadcast.  None of them consults ground truth, and none of them asks
anybody's opinion.

**1. The commitment audit** (:func:`commitment_residual`).
An accuser's ``RangeReportMsg`` is a public, per-round commitment: it says
"my radio measured peer ``j`` at ``r`` metres".  Combined with the two
drones' claimed positions, that reproduces exactly the one-hop residual the
accuser's own E2 checker computed (``E2Report.direct_z``).  The accuser
cannot both publish a range that confirms the target's claim *and* assert its
measurements show that claim is false - the two statements are checkable
against each other by anyone listening, which is what makes a fabricated
accusation self-refuting rather than merely unpopular.

**2. Independent position re-derivation** (:func:`independent_fix`).
The target's position is re-fixed by multilateration from every *other*
drone's published ranges, with the accuser excluded from its own alibi.  This
is the check that survives a hostile majority in the case that matters most:
a drone lying only in gossip flies and ranges honestly, so it leaves this fix
untouched, and the fix lands on the innocent target's real position and
refutes the accusation on geometry rather than on a head count.

**3. Reciprocity split detection** (:func:`assess_geometry`).
Range is a symmetric physical quantity: ``r_ij`` and ``r_ji`` are two noisy
measurements of one distance and must agree within their combined sigma.  A
drone projecting a fabricated world reports claim-implied ranges, which
disagree with what honest peers measure - so reciprocity partitions the swarm
into internally-consistent worlds.  It cannot say *which* world is real (that
would need a reference outside the swarm), and this module does not pretend
otherwise.  What it does is *detect* that the anchor set is contested, so the
verifier can return AMBIGUOUS and the swarm can quarantine instead of convict.
Refusing to convict on contested geometry is the whole safety property.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..config import ClaimCheckConfig, EvidenceConfig, MeshConfig
from .claim import ClaimBundle


@dataclass
class Rederivation:
    """An independently re-derived answer to "where is the target really?"."""

    target: int
    usable: bool = False
    z: float = 0.0                       # offset from the claim, in sigma
    offset: float = 0.0                  # metres
    sigma: float = float("inf")
    n_observers: int = 0
    n_inliers: int = 0
    fix: np.ndarray | None = None
    anchor_backed: bool = False          # a certified anchor contributed
    contributors: list[int] = field(default_factory=list)

    def as_row(self) -> dict:
        return {
            "usable": self.usable,
            "z": round(self.z, 3),
            "offset_m": round(self.offset, 2),
            "observers": self.n_observers,
            "inliers": self.n_inliers,
            "anchor_backed": self.anchor_backed,
            "contributors": list(self.contributors),
        }


# ----------------------------------------------------------------------
def commitment_residual(
    bundle: ClaimBundle, accuser: int, target: int
) -> float | None:
    """The accuser's own published one-hop residual on the target, in sigma.

    ``None`` when the accuser published no range to the target this round -
    a real condition (out of sensing range, radio budget spent) that must not
    be read as either guilt or innocence.

    This is deliberately identical arithmetic to
    ``CrossObservationChecker._direct``: the audit has to reproduce what the
    accuser's own checker would have seen from the accuser's own data, or it
    would be disagreeing about method rather than about evidence.
    """
    table = bundle.ranges.get(accuser)
    if not table or target not in table:
        return None
    if accuser not in bundle.claims or target not in bundle.claims:
        return None
    measured, sigma = table[target]
    implied = float(
        np.linalg.norm(
            np.asarray(bundle.claims[accuser], float)
            - np.asarray(bundle.claims[target], float)
        )
    )
    return float(abs(implied - measured) / max(float(sigma), 1e-3))


# ----------------------------------------------------------------------
def independent_fix(
    bundle: ClaimBundle,
    target: int,
    *,
    exclude: set[int],
    ev: EvidenceConfig,
    mesh: MeshConfig,
    rng: np.random.Generator,
) -> Rederivation:
    """Re-fix ``target``'s position from everyone except ``exclude``.

    Anchors are peers positioned at their *claimed* locations - the verifier
    has no privileged view and must work from the same broadcasts an honest
    drone hears.  That is not a weakness of the audit: a peer whose claim is
    wrong contributes an anchor in the wrong place and shows up as an outlier
    to RANSAC, which is precisely what the robust fit is for.
    """
    # Imported here rather than at module scope on purpose.  ``vishwas.evidence``
    # reaches back into ``vishwas.sim`` (E1 needs the airframe power model),
    # which reaches into ``vishwas.consensus``, which imports this package -
    # so a module-level import makes the cycle depend on which module the
    # process happens to load first.  Deferring the one import that closes the
    # loop keeps ``vishwas.verify`` importable on its own, which is also what
    # lets the unit tests exercise the verifier without building a swarm.
    from ..evidence import multilateration as mlat
    from ..evidence.e2_cross import flatten_to_plane

    out = Rederivation(target=target)
    claim = bundle.claims.get(target)
    if claim is None:
        return out
    claim = np.asarray(claim, dtype=float)

    anchors: list[np.ndarray] = []
    ranges: list[float] = []
    sigmas: list[float] = []
    contributors: list[int] = []
    for observer in bundle.observers_of(target, exclude=exclude):
        r, s = bundle.ranges[observer][target]
        if observer in bundle.anchors:
            # A hardware root of trust is a reference, not a vote: shrink its
            # sigma far past anything credibility can reach so the robust fit
            # treats it as a near-hard constraint.  Same reasoning, and the
            # same factor, as ``e2_cross`` - see MeshConfig.anchor_sigma_factor.
            s = float(s) / mesh.anchor_sigma_factor
            out.anchor_backed = True
        anchors.append(np.asarray(bundle.claims[observer], dtype=float))
        ranges.append(float(r))
        sigmas.append(float(s))
        contributors.append(observer)

    out.n_observers = len(anchors)
    out.contributors = contributors
    if len(anchors) < max(3, ev.e2_min_observers):
        # Not enough independent geometry to say anything.  Abstaining here is
        # what turns "we could not check" into AMBIGUOUS upstream instead of
        # into a conviction.
        return out

    anchors_2d, ranges_2d, sigmas_2d = flatten_to_plane(anchors, ranges, sigmas, claim)
    solution = mlat.ransac_solve(
        anchors_2d,
        ranges_2d,
        sigmas_2d,
        guess=claim[:2],
        rng=rng,
        inlier_sigma=ev.e2_ransac_inlier_sigma,
        huber_delta=ev.e2_huber_delta,
        iterations=ev.e2_ransac_iters,
    )
    if not solution.usable:
        return out

    sigma = mlat.position_sigma(solution, ev.e2_position_sigma)
    offset = float(np.linalg.norm(solution.position - claim[:2]))
    out.usable = True
    out.fix = np.array([solution.position[0], solution.position[1], float(claim[2])])
    out.offset = offset
    out.sigma = sigma
    out.n_inliers = len(solution.inliers)
    out.z = float(offset / sigma) if np.isfinite(sigma) and sigma > 0 else 0.0
    return out


# ----------------------------------------------------------------------
@dataclass
class Geometry:
    """The swarm's range reports, read as a picture of who agrees with whom.

    ``dominant`` is the largest set of drones whose published ranges are
    *mutually* reciprocity-consistent - the largest single self-consistent
    account of where everybody is.  ``contested`` says a second account
    survives alongside it that is too large to dismiss as noise or as a
    handful of faulty radios.
    """

    live: list[int] = field(default_factory=list)
    dominant: set[int] = field(default_factory=set)
    outliers: set[int] = field(default_factory=set)
    contested: bool = False
    consistency: dict[int, float] = field(default_factory=dict)
    anchor_backed: bool = False

    @property
    def dominant_fraction(self) -> float:
        return len(self.dominant) / max(1, len(self.live))

    def as_row(self) -> dict:
        return {
            "dominant": sorted(self.dominant),
            "outliers": sorted(self.outliers),
            "contested": self.contested,
            "dominant_fraction": round(self.dominant_fraction, 3),
            "anchor_backed": self.anchor_backed,
        }


def _pairwise_consistent(
    bundle: ClaimBundle, i: int, j: int, cc: ClaimCheckConfig
) -> bool | None:
    """Do ``i`` and ``j``'s published ranges agree about the distance between
    them?  ``None`` when the pair was not measured both ways this round."""
    a = bundle.ranges.get(i, {}).get(j)
    b = bundle.ranges.get(j, {}).get(i)
    if a is None or b is None:
        return None
    combined = float(np.hypot(max(float(a[1]), 1e-3), max(float(b[1]), 1e-3)))
    return abs(float(a[0]) - float(b[0])) <= cc.reciprocity_z * combined


def assess_geometry(
    bundle: ClaimBundle, live: list[int], cc: ClaimCheckConfig, f_tolerated: int
) -> Geometry:
    """Extract the largest mutually-consistent account of the swarm's geometry.

    Reciprocity is pairwise physics, not opinion: ``r_ij`` and ``r_ji`` are two
    noisy measurements of one distance, so a pair that disagrees beyond their
    combined sigma contains at least one drone reporting a world it is not in.
    Peeling the least-consistent drone until what remains is mutually
    consistent extracts the largest such world without ever asking anyone to
    vote.

    What this deliberately does *not* do is decide that the largest world is
    the true one.  Two internally-consistent worlds are observationally
    equivalent under range-only data - the geometry admits both - and picking
    the bigger one is exactly the majority vote this whole package exists to
    stop doing.  So when the discarded set exceeds the Byzantine budget
    ``f_tolerated``, the geometry is marked ``contested``, and the verifier
    upstream refuses to convict anyone on it.  Resolving a contested split
    needs a reference from outside the swarm, which is what
    ``MeshConfig.certified_anchors`` is for.
    """
    nodes = [d for d in live if d in bundle.claims]
    geo = Geometry(live=list(nodes))
    if len(nodes) < 3:
        geo.dominant = set(nodes)
        return geo

    checked: dict[int, int] = {d: 0 for d in nodes}
    agreed: dict[int, int] = {d: 0 for d in nodes}
    edge: dict[tuple[int, int], bool] = {}
    for i, j in ((a, b) for k, a in enumerate(nodes) for b in nodes[k + 1:]):
        ok = _pairwise_consistent(bundle, i, j, cc)
        if ok is None:
            continue
        edge[(i, j)] = ok
        checked[i] += 1
        checked[j] += 1
        if ok:
            agreed[i] += 1
            agreed[j] += 1

    geo.consistency = {
        d: (agreed[d] / checked[d] if checked[d] else 1.0) for d in nodes
    }

    def conflicts(pool: set[int]) -> dict[int, int]:
        out = {d: 0 for d in pool}
        for (i, j), ok in edge.items():
            if ok or i not in pool or j not in pool:
                continue
            out[i] += 1
            out[j] += 1
        return out

    pool = set(nodes)
    anchors = {d for d in nodes if d in bundle.anchors}

    # Anchors seed the surviving world rather than competing for it.  Plain
    # peel-the-worst-conflicted is a popularity contest in disguise: each
    # member of a large forging bloc conflicts with only the few honest
    # drones, while each honest drone conflicts with the whole bloc, so the
    # honest side is peeled *first* precisely when it is outnumbered - the
    # majority failure this package exists to avoid, reappearing one level
    # down.  A certified anchor breaks that symmetry from outside the swarm:
    # a drone whose ranges contradict the anchor's is reporting a world the
    # anchor is not in, and the anchor's hardware root of trust is not
    # something the compromise draw can reach (``Swarm.__init__`` never
    # selects one).  So anchors are dropped from the peel entirely, and
    # whatever directly contradicts them goes first.
    if anchors:
        for (i, j), ok in edge.items():
            if ok:
                continue
            if i in anchors and j not in anchors:
                pool.discard(j)
            elif j in anchors and i not in anchors:
                pool.discard(i)

    while True:
        bad = conflicts(pool)
        candidates = {d: c for d, c in bad.items() if d not in anchors}
        worst = max(
            candidates, key=lambda d: (candidates[d], -geo.consistency[d], -d),
            default=None,
        )
        if worst is None or candidates[worst] == 0:
            break
        pool.discard(worst)
        if len(pool) <= 2:
            break

    geo.dominant = pool
    geo.outliers = set(nodes) - pool
    geo.anchor_backed = bool(pool & set(bundle.anchors))
    # When is the split unresolvable?
    #
    # The first version of this asked whether the discarded set exceeded
    # ``f_tolerated``, which sounds principled and is badly wrong in practice.
    # At n=9 that is f=2, so *three* liars - cleanly peeled out, with six
    # mutually-consistent honest drones left standing - marked the geometry
    # contested and threw the answer away.  Measured: ``position_teleport``
    # with 3 of 9 compromised produced ``outliers=[0,1,6]``, exactly the
    # attackers, and then resolved all 2880 claims AMBIGUOUS and expelled
    # nobody.  A rule that identifies the liars and then declines to use the
    # identification is not caution, it is a bug.
    #
    # What actually makes a split unresolvable is the two sides being
    # comparable, because then either could be the honest one and range data
    # cannot say which.  So the surviving world has to be a strict majority of
    # the swarm that is still flying.  This is deliberately the weakest claim
    # that is still true: it does *not* assert the majority is honest - at 5
    # liars of 9 it will happily believe the liars - it only asserts that when
    # no account holds a majority, nothing can be relied on at all.  The
    # regime past that is guarded elsewhere (the majority cap in
    # ``ConsensusEngine``, and the refusal to convict on unverified claims);
    # an anchor is what actually resolves it.
    live_count = max(1, len(nodes))
    geo.contested = (
        2 * len(geo.dominant) <= live_count and not geo.anchor_backed
    )
    return geo
