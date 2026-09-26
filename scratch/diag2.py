import numpy as np
from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm

def probe(n, ticks=700):
    s = Swarm(SimConfig(n_drones=n, max_ticks=ticks, seed=7),
              scenario="collusion", n_compromised=2).run()
    m = s.summary()
    bad = m['compromised']
    print(f"\n=== n={n} bad={bad} tp={m['true_positives']} ===")
    # pick honest observers
    honest = [i for i in range(n) if i not in bad]
    tgt = bad[0]
    for tick in (200, 400, 600):
        rows = []
        for i in honest:
            a = s.drones[i].engine.assessments.get(tgt)
            if a is None: continue
            rows.append((a.z1, a.z2, a.z3, a.cusum, a.suspicion,
                         a.abstained, a.detail.get('e2_observers', 0),
                         a.detail.get('e2_inliers', 0)))
        if not rows: continue
        A = np.array([r[:5] for r in rows], dtype=float)
        ab = sum(1 for r in rows if r[5])
        print(f" (final state) z1={A[:,0].mean():.2f} z2={A[:,1].mean():.2f} "
              f"z3={A[:,2].mean():.2f} cusum={A[:,3].mean():.2f} "
              f"susp={A[:,4].mean():.2f} abstain={ab}/{len(rows)} "
              f"obs={np.mean([r[6] for r in rows]):.1f} inl={np.mean([r[7] for r in rows]):.1f}")
        break
    # honest-vs-honest baseline
    base = []
    for i in honest:
        for j in honest:
            if i==j: continue
            a = s.drones[i].engine.assessments.get(j)
            if a: base.append((a.z1,a.z2,a.z3,a.cusum,a.suspicion))
    B = np.array(base, dtype=float)
    print(f" honest baseline: z1={B[:,0].mean():.2f} z2={B[:,1].mean():.2f} "
          f"z3={B[:,2].mean():.2f} cusum={B[:,3].mean():.2f} susp={B[:,4].mean():.2f}")

for n in (9, 13):
    probe(n)
