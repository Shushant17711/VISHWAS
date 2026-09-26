"""Evidence channel E2 - cross-observation consistency.

This is the strong signal, and the reason VISHWAS works at all.

Every other layer in a drone-security stack ultimately trusts something the
suspect drone emits.  E2 does not.  It builds an *independent* estimate of
where a peer actually is, out of measurements taken by other airframes'
radios: range from time-of-flight assisted RSSI, bearing from onboard sensing
where available, and multilateration from three or more observers.

A drone lying about its position therefore produces a growing, observable
residual - and, crucially, it produces that residual in the measurements of
*every* honest peer simultaneously.  That simultaneity is exactly what the
consensus layer needs: independent corroboration rather than one node's
opinion.  The attacker cannot suppress it because it does not own the radios
that generate it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..config import EvidenceConfig, MeshConfig
from . import multilateration as mlat


@dataclass
class E2Report:
    z: float = 0.0                          # standardised residual, >= 0
    abstained: bool = True                  # too little geometry to judge
    n_observers: int = 0
    n_inliers: int = 0
    direct_z: float = 0.0                   # i's own range check on j
    estimate: np.ndarray | None = None      # peer-derived position of j
    claimed: np.ndarray | None = None
    offset: float = 0.0                     # metres between the two
    sigma: float = float("inf")

    @property
    def evidence(self) -> dict[str, float]:
        return {
            "z": self.z,
            "direct_z": self.direct_z,
            "offset_m": self.offset,
            "observers": float(self.n_observers),
            "inliers": float(self.n_inliers),
        }


class CrossObservationChecker:
    """Fuses one drone's own ranging with gossiped peer range reports."""

    def __init__(
        self,
        cfg: EvidenceConfig,
        mesh: MeshConfig,
        rng: np.random.Generator,
        observer_id: int,
    ) -> None:
        self.cfg = cfg
        self.mesh = mesh
        self.rng = rng
        self.me = observer_id
        self.last: dict[int, E2Report] = {}

    # ------------------------------------------------------------------
    def score(
        self,
        target: int,
        claims: dict[int, np.ndarray],
        my_position: np.ndarray,
        my_ranges: dict[int, tuple[float, float]],
        peer_ranges: dict[int, dict[int, tuple[float, float]]],
        credibility: dict[int, float] | None = None,
    ) -> E2Report:
        """Score peer ``target``'s claimed position against peer observation.

        Parameters
        ----------
        claims
            Latest self-reported position of every peer, ``target`` included.
        my_position
            This drone's own position - the one anchor it knows first-hand.
        my_ranges
            ``peer -> (range_m, sigma_m)`` measured by this drone's radio.
        peer_ranges
            ``observer -> {peer -> (range_m, sigma_m)}`` gossiped by others.
        credibility
            Optional accuser-credibility weights; low-credibility observers
            have their measurement sigma inflated rather than being dropped,
            so a swarm that wrongly distrusts a node degrades gracefully.
        """
        claim = claims.get(target)
        report = E2Report(claimed=None if claim is None else np.asarray(claim, float))
        if claim is None:
            return self._store(target, report)
        claim = np.asarray(claim, dtype=float)

        anchors: list[np.ndarray] = []
        ranges: list[float] = []
        sigmas: list[float] = []

        # -- anchor 0: myself, the only position I know first-hand ------
        if target in my_ranges:
            r, s = my_ranges[target]
            anchors.append(np.asarray(my_position, dtype=float))
            ranges.append(r)
            sigmas.append(s)
            report.direct_z = self._direct(my_position, claim, r, s)

        # -- anchors 1..k: peers, positioned by their own claims --------
        for observer, table in peer_ranges.items():
            if observer in (self.me, target):
                continue
            if target not in table:
                continue
            obs_pos = claims.get(observer)
            if obs_pos is None:
                continue
            r, s = table[target]
            if credibility is not None:
                cred = float(np.clip(credibility.get(observer, 1.0), 0.05, 1.0))
                s = s / cred          # distrusted observers weigh less
            if observer in self.mesh.certified_anchors:
                # A hardware root of trust is not "very credible" in the
                # same sense a well-behaved peer is - it is a reference, not
                # a vote.  Shrink its contributed sigma far past what
                # ordinary credibility (capped at 1.0 above) can reach, so
                # the robust fit below effectively treats it as a
                # near-hard constraint: any candidate position that
                # disagrees with the anchor pays a huge normalised
                # residual, regardless of how many *other* anchors -
                # honest or compromised - agree with each other instead.
                s = s / self.mesh.anchor_sigma_factor
            anchors.append(np.asarray(obs_pos, dtype=float))
            ranges.append(r)
            sigmas.append(s)

        report.n_observers = len(anchors)
        if len(anchors) < max(3, self.cfg.e2_min_observers):
            # Abstain rather than guess.  An honest system says "I don't know".
            report.z = report.direct_z
            report.abstained = report.direct_z <= 0.0
            return self._store(target, report)

        # Solve in the horizontal plane.  A swarm holding a common cruise
        # altitude is a near-coplanar anchor set: the vertical component of a
        # range-only fix is geometrically unobservable, and including it
        # produces an enormous GDOP that would make E2 abstain forever.  The
        # slant ranges are de-projected onto the plane using the *claimed*
        # altitude difference; vertical lies are E1/E3's job, horizontal ones
        # are where the mission actually breaks.
        anchors_2d, ranges_2d, sigmas_2d = flatten_to_plane(anchors, ranges, sigmas, claim)

        solution = mlat.ransac_solve(
            anchors_2d,
            ranges_2d,
            sigmas_2d,
            guess=claim[:2],
            rng=self.rng,
            inlier_sigma=self.cfg.e2_ransac_inlier_sigma,
            huber_delta=self.cfg.e2_huber_delta,
            iterations=self.cfg.e2_ransac_iters,
        )
        if not solution.usable:
            report.z = report.direct_z
            report.abstained = True
            return self._store(target, report)

        sigma = mlat.position_sigma(solution, self.cfg.e2_position_sigma)
        offset = float(np.linalg.norm(solution.position - claim[:2]))
        report.estimate = np.array(
            [solution.position[0], solution.position[1], float(claim[2])]
        )
        report.offset = offset
        report.sigma = sigma
        report.n_inliers = len(solution.inliers)
        report.abstained = False
        # The multilateration fix already *contains* this drone's own range as
        # anchor 0, so naively taking ``max(fix_z, direct_z)`` double-counts
        # that measurement and, because the max of two noisy statistics is
        # biased upward, nudges every honest peer's residual up too.
        #
        # In practice that bias is small and swamped by a much bigger effect:
        # ``direct_z`` is a scale-invariant one-hop check and does not degrade
        # as the swarm grows, while the fused geometric ``z`` does - a
        # colluding accomplice's corrupted ranges pull the multilateration fix
        # toward the lie, and that vote margin erodes with n faster than the
        # quorum shrinks relative to swarm size. Treating ``direct_z`` as a
        # fallback used only when the geometric fix is unavailable throws away
        # the strongest, most n-robust signal E2 has, and recall on the
        # colluding-pair scenario collapses hard once the swarm is large
        # enough for that erosion to outrun the fused statistic (empirically
        # around n=12, see scratch/diag12.py..diag16.py). Fusing the two
        # signals restores recall across n=9..16 at the cost of a modest
        # increase in false accusations (see scratch/ablation_directz.log);
        # net it is a clear improvement, so it is the default.
        fused_z = float(offset / sigma)
        if self.cfg.e2_fuse_direct_z:
            report.z = max(fused_z, report.direct_z)
        else:
            report.z = fused_z
        return self._store(target, report)


    # ------------------------------------------------------------------
    def _direct(
        self, my_position: np.ndarray, claim: np.ndarray, measured: float, sigma: float
    ) -> float:
        """|measured range - range implied by the claim|, in sigma."""
        implied = float(np.linalg.norm(np.asarray(my_position, float) - claim))
        return float(abs(implied - measured) / max(sigma, 1e-3))

    def _store(self, target: int, report: E2Report) -> E2Report:
        self.last[target] = report
        return report


# ----------------------------------------------------------------------
def flatten_to_plane(
    anchors: list[np.ndarray],
    ranges: list[float],
    sigmas: list[float],
    claim: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Project slant ranges onto the horizontal plane.

    ``r_h = sqrt(r^2 - dz^2)`` with ``dz`` the claimed altitude difference.
    When the geometry says the slant range is shorter than the vertical
    separation alone (noise, or a lie), the residual is clamped to zero rather
    than dropped - a zero-radius circle still constrains the fix, and dropping
    observations silently would let an attacker prune its own accusers.

    Module-level rather than a method because ClaimCheck
    (:mod:`vishwas.verify.recompute`) has to reproduce E2's geometry *exactly*
    when it re-derives a target's position: an audit that solved a subtly
    different problem from the one it is auditing would disagree with honest
    accusers on arithmetic rather than on evidence.
    """
    a = np.asarray(anchors, dtype=float).reshape(-1, 3)
    r = np.asarray(ranges, dtype=float).ravel()
    s = np.asarray(sigmas, dtype=float).ravel()
    dz = a[:, 2] - float(claim[2])
    r_h = np.sqrt(np.maximum(r**2 - dz**2, 0.0))
    # Sigma grows as the projection becomes ill-conditioned (dz -> r).
    scale = r / np.maximum(r_h, 1e-3)
    return a[:, :2], r_h, s * np.clip(scale, 1.0, 10.0)


# ----------------------------------------------------------------------
def bearing_residual(
    my_position: np.ndarray, claim: np.ndarray, measured_unit: np.ndarray
) -> float:
    """Angular disagreement (radians) between a claim and a measured bearing.

    Kept separate from the range pipeline: bearing is only available when the
    peer is within line of sight of an optical/RF direction finder, so it is
    an opportunistic bonus channel rather than something the design leans on.
    """
    delta = np.asarray(claim, float) - np.asarray(my_position, float)
    norm = float(np.linalg.norm(delta))
    if norm < 1e-6:
        return 0.0
    cos = float(np.clip(np.dot(delta / norm, np.asarray(measured_unit, float)), -1.0, 1.0))
    return float(np.arccos(cos))
