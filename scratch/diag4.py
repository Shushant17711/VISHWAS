import numpy as np
from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm

def probe(n, ticks=420):
    s = Swarm(SimConfig(n_drones=n, max_ticks=ticks, seed=7),
              scenario="collusion", n_compromised=2)
    acc = []
    for t in range(ticks):
        s.step()
        bad = [i for i in range(n) if s.drones[i].behaviour.malicious]
        tgt, mate = bad[0], bad[1]
        honest = [i for i in range(n) if i not in bad]
        if t < 150: continue
        for i in honest:
            a = s.drones[i].engine.assessments.get(tgt)
            if a is None or a.abstained: continue
            d = a.detail
            off = float(d.get('e2_offset_m', np.nan))
            sig = off / a.z2 if a.z2 > 1e-9 else np.nan
            acc.append((off, sig, d.get('e2_observers', np.nan),
                        d.get('e2_inliers', np.nan), a.z2))
    A = np.array(acc, dtype=float)
    print(f"n={n} samples={len(A)}  (t>=150, traitor={tgt}, accomplice={mate})")
    for k, name in enumerate(("offset_m","sigma_m","observers","inliers","z2")):
        print(f"   {name:>10}: mean={np.nanmean(A[:,k]):7.3f}  p90={np.nanpercentile(A[:,k],90):7.3f}")

for n in (9, 13):
    probe(n)
