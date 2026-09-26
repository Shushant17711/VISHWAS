import numpy as np
from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm
import vishwas.evidence.multilateration as mlat
import vishwas.evidence.e2_cross as e2

REC = {}
orig_ransac = mlat.ransac_solve

def make_probe(n):
    def probe(anchors, ranges, sigmas, guess, **kw):
        sol = orig_ransac(anchors, ranges, sigmas, guess, **kw)
        a = np.asarray(anchors, float).reshape(len(ranges), -1)
        s = np.asarray(sigmas, float).ravel()
        k = len(s)
        # ideal isotropic prediction: sqrt(2)*mean_sigma/sqrt(k)   (2-D fix)
        ideal = np.sqrt(2.0) * float(np.mean(s)) / np.sqrt(max(k, 1))
        REC.setdefault(n, []).append((k, len(sol.inliers), float(np.mean(s)),
                                      float(np.min(s)), sol.gdop, ideal))
        return sol
    return probe

for n in (9, 13):
    mlat.ransac_solve = make_probe(n)
    e2.mlat.ransac_solve = mlat.ransac_solve
    Swarm(SimConfig(n_drones=n, max_ticks=420, seed=7),
          scenario="collusion", n_compromised=2).run()

print(f"{'n':>3} {'calls':>6} {'anch':>6} {'inl':>6} {'sig_mu':>7} {'sig_min':>8} {'gdop':>7} {'ideal':>7} {'ratio':>6}")
for n, rows in REC.items():
    A = np.array([r for r in rows if np.isfinite(r[4])], float)
    print(f"{n:>3} {len(A):>6} {A[:,0].mean():>6.2f} {A[:,1].mean():>6.2f} {A[:,2].mean():>7.3f} "
          f"{A[:,3].mean():>8.3f} {A[:,4].mean():>7.3f} {A[:,5].mean():>7.3f} "
          f"{(A[:,4]/A[:,5]).mean():>6.2f}")
