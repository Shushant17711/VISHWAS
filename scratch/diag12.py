import numpy as np
from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm
print(f"{'n':>3} {'seed':>4} {'f':>2} {'quorum':>6} {'caught':>7} {'wrong':>6}  excluded")
for n in range(9, 17):
    for seed in (7, 11, 13):
        s = Swarm(SimConfig(n_drones=n, max_ticks=900, seed=seed),
                  scenario="collusion", n_compromised=2).run()
        m = s.summary()
        bad = set(m['compromised']); exc = set(s.consensus.excluded)
        print(f"{n:>3} {seed:>4} {s.consensus.f_tolerated:>2} {s.consensus.quorum_size:>6} "
              f"{len(exc & bad):>7} {len(exc - bad):>6}  {sorted(exc)}")
