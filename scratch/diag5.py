import numpy as np
from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm
import vishwas.evidence.e2_cross as e2

REC = {}
orig = e2.flatten_to_plane

def patched(anchors, ranges, sigmas, claim):
    a2, rh, s2 = orig(anchors, ranges, sigmas, claim)
    a = np.asarray(anchors, float).reshape(-1, 3)
    r = np.asarray(ranges, float).ravel()
    dz = a[:, 2] - float(claim[2])
    rh_raw = np.sqrt(np.maximum(r**2 - dz**2, 0.0))
    sc = np.clip(r / np.maximum(rh_raw, 1e-3), 1.0, 10.0)
    REC.setdefault(N, []).append((sc, r, rh_raw, np.abs(dz)))
    return a2, rh, s2

e2.flatten_to_plane = patched

for N in (9, 13):
    REC[N] = []
    Swarm(SimConfig(n_drones=N, max_ticks=420, seed=7),
          scenario="collusion", n_compromised=2).run()
    sc = np.concatenate([x[0] for x in REC[N]])
    r = np.concatenate([x[1] for x in REC[N]])
    rh = np.concatenate([x[2] for x in REC[N]])
    dz = np.concatenate([x[3] for x in REC[N]])
    print(f"n={N}  anchors={len(sc)}")
    print(f"   scale : mean={sc.mean():.3f} p50={np.median(sc):.3f} "
          f"p90={np.percentile(sc,90):.3f} max={sc.max():.3f}")
    print(f"   frac scale>1.05 = {(sc>1.05).mean():.3f}   "
          f"frac at clip 10 = {(sc>=9.99).mean():.4f}")
    print(f"   slant r: mean={r.mean():.1f}   r_h: mean={rh.mean():.1f}   "
          f"|dz|: mean={dz.mean():.2f}")
