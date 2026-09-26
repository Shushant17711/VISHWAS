from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm

print(f"{'cfg':<34} {'caught':<8} {'wrong':<8} recall")
for n in (9, 11, 13):
    for at in (0.55, 0.45, 0.35):
        cfg = SimConfig(n_drones=n, max_ticks=900, seed=7)
        cfg.consensus.accuse_threshold = at
        s = Swarm(cfg, scenario="collusion", n_compromised=2).run()
        m = s.summary()
        bad = set(m['compromised']); exc = set(s.consensus.excluded)
        print(f"n={n:<2d} accuse_thr={at:<5} {str(sorted(exc&bad)):<8} {str(sorted(exc-bad)):<8} {len(exc&bad)}/{len(bad)}")
