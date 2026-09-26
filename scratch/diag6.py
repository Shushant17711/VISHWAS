import numpy as np
from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm

for n in (9, 13):
    s = Swarm(SimConfig(n_drones=n, max_ticks=800, seed=7),
              scenario="collusion", n_compromised=2)
    trace = []
    for t in range(800):
        s.step()
        if t % 100 == 0:
            w = s.credibility.weights()
            live = s.consensus.live_drones()
            vals = [w.get(i, 1.0) for i in live]
            trace.append((t, float(np.mean(vals)), float(np.min(vals)),
                          float(np.max(vals)), len(live)))
    print(f"=== n={n}  excluded={sorted(s.consensus.excluded)}")
    for t, mu, lo, hi, nl in trace:
        print(f"   t={t:4d} live={nl:2d} cred mean={mu:.3f} "
              f"min={lo:.3f} max={hi:.3f}  -> sigma x{1/mu:.2f}")
