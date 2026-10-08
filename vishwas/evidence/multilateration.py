"""Robust multilateration - the geometric core of evidence channel E2.

Three or more independent range measurements to a drone constrain where that
drone *actually is*, regardless of where it says it is.  This is the one
channel a compromised drone cannot control: it does not own its peers' radios.

Two robustness measures matter here, and both are there for adversarial rather
than numerical reasons:

* **RANSAC** over the anchor set, so that a minority of colluding observers
  feeding deliberately corrupted ranges cannot drag the solution onto the
  liar's fabricated position.
* **Huber loss** in the refinement, so that heavy-tailed ranging noise (a real
  property of RSSI/time-of-flight measurement) does not masquerade as an
  attack and inflate the false-accusation rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Solution:
    position: np.ndarray
    residual_rms: float                     # sigma units, over inliers
    n_anchors: int
    inliers: list[int] = field(default_factory=list)
    gdop: float = float("inf")
    converged: bool = False

    @property
    def usable(self) -> bool:
        return self.converged and len(self.inliers) >= 3 and np.isfinite(self.gdop)


# ----------------------------------------------------------------------
def _residuals(x: np.ndarray, anchors: np.ndarray, ranges: np.ndarray,
               sigmas: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    delta = x[None, :] - anchors
    dist = np.linalg.norm(delta, axis=1)
    dist = np.maximum(dist, 1e-6)
    res = (dist - ranges) / sigmas
    jac = (delta / dist[:, None]) / sigmas[:, None]
    return res, jac


def solve(
    anchors: np.ndarray,
    ranges: np.ndarray,
    sigmas: np.ndarray,
    guess: np.ndarray,
    *,
    huber_delta: float = 3.0,
    iterations: int = 24,
    damping: float = 1e-3,
) -> Solution:
    """Huber-weighted Gauss-Newton range-only position fix.

    Works in whatever dimension the anchors are given in.  E2 solves in the
    horizontal plane: a swarm holding a common cruise altitude is a
    near-coplanar anchor set, and the vertical component of a range-only fix
    taken from coplanar anchors is unobservable.  Solving for it anyway
    produces a fix whose error is dominated by a direction nobody measured.
    """
    x = np.asarray(guess, dtype=float).copy().ravel()
    dim = x.size
    anchors = np.asarray(anchors, dtype=float).reshape(-1, dim)
    ranges = np.asarray(ranges, dtype=float).ravel()
    sigmas = np.maximum(np.asarray(sigmas, dtype=float).ravel(), 1e-3)
    m = len(ranges)
    if m < dim:
        return Solution(x, float("inf"), m)

    converged = False
    cov = None
    for _ in range(iterations):
        res, jac = _residuals(x, anchors, ranges, sigmas)
        absr = np.abs(res)
        w = np.where(absr <= huber_delta, 1.0, huber_delta / np.maximum(absr, 1e-9))
        jw = jac * w[:, None]
        hess = jac.T @ jw + damping * np.eye(dim)
        grad = jw.T @ res
        try:
            step = np.linalg.solve(hess, -grad)
        except np.linalg.LinAlgError:
            break
        # Trust-region-ish cap: never jump absurdly far in one iteration.
        norm = float(np.linalg.norm(step))
        if norm > 250.0:
            step *= 250.0 / norm
        x = x + step
        if norm < 1e-3:
            converged = True
            try:
                cov = np.linalg.inv(hess)
            except np.linalg.LinAlgError:
                cov = None
            break

    res, jac = _residuals(x, anchors, ranges, sigmas)
    if cov is None:
        try:
            cov = np.linalg.inv(jac.T @ jac + damping * np.eye(dim))
        except np.linalg.LinAlgError:
            cov = None
    gdop = float(np.sqrt(np.trace(cov))) if cov is not None else float("inf")
    inliers = [i for i in range(m) if abs(res[i]) <= huber_delta]
    used = res[inliers] if inliers else res
    rms = float(np.sqrt(np.mean(used**2))) if len(used) else float("inf")
    return Solution(x, rms, m, inliers, gdop, converged or np.isfinite(rms))


def ransac_solve(
    anchors: np.ndarray,
    ranges: np.ndarray,
    sigmas: np.ndarray,
    guess: np.ndarray,
    *,
    rng: np.random.Generator | None = None,
    inlier_sigma: float = 3.0,
    huber_delta: float = 3.0,
    iterations: int = 24,
) -> Solution:
    """RANSAC anchor selection followed by a Huber refit on the consensus set.

    With ``f`` colluding observers out of ``n``, an honest minimal sample is
    drawn with high probability well before the iteration budget runs out,
    and the honest sample is the one that explains the *majority* of ranges.
    """
    guess = np.asarray(guess, dtype=float).ravel()
    dim = guess.size
    anchors = np.asarray(anchors, dtype=float).reshape(-1, dim)
    ranges = np.asarray(ranges, dtype=float).ravel()
    sigmas = np.maximum(np.asarray(sigmas, dtype=float).ravel(), 1e-3)
    m = len(ranges)
    if m < dim + 2:
        return solve(anchors, ranges, sigmas, guess, huber_delta=huber_delta)

    rng = rng or np.random.default_rng(0)
    best: Solution | None = None
    best_count = -1
    sample_size = min(dim + 1, m)
    for _ in range(iterations):
        idx = rng.choice(m, size=sample_size, replace=False)
        cand = solve(anchors[idx], ranges[idx], sigmas[idx], guess,
                     huber_delta=huber_delta, iterations=12)
        if not np.all(np.isfinite(cand.position)):
            continue
        res, _ = _residuals(cand.position, anchors, ranges, sigmas)
        inliers = [i for i in range(m) if abs(res[i]) <= inlier_sigma]
        if len(inliers) > best_count:
            best_count, best = len(inliers), cand
            best.inliers = inliers
        if best_count == m:
            break

    if best is None or len(best.inliers) < 3:
        return solve(anchors, ranges, sigmas, guess, huber_delta=huber_delta)

    keep = np.asarray(best.inliers, dtype=int)
    final = solve(anchors[keep], ranges[keep], sigmas[keep], best.position,
                  huber_delta=huber_delta)
    final.n_anchors = m
    final.inliers = [int(keep[i]) for i in final.inliers]
    return final


def position_sigma(solution: Solution, base_sigma: float) -> float:
    """Expected 1-sigma error of the fix, used to standardise the E2 residual.

    A fix taken from a poor geometry (large GDOP) is genuinely less certain,
    and accusing on the back of an uncertain fix is how false exclusions
    happen.  Scaling the residual by this term is what makes E2 abstain
    gracefully instead of guessing.
    """
    # ``gdop`` here is already sqrt(trace(cov)) of a sigma-normalised
    # information matrix, so it carries metres.  Floor it with the raw
    # measurement sigma - the fix can never be better than its inputs.
    if not np.isfinite(solution.gdop):
        return float("inf")
    return float(max(solution.gdop, 0.5 * base_sigma))
