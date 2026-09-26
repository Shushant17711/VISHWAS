from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm

def run(n, seed, fuse):
    cfg = SimConfig(n_drones=n, max_ticks=900, seed=seed)
    cfg.evidence.e2_fuse_direct_z = fuse
    s = Swarm(cfg, scenario="collusion", n_compromised=2).run()
    m = s.summary()
    bad = set(m['compromised'])
    exc = set(s.consensus.excluded)
    caught = len(bad & exc)
    wrong = len(exc - bad)
    return caught, wrong

for n in (9, 12, 16):
    for fuse in (False, True):
        rows = [run(n, seed, fuse) for seed in (7, 11, 13)]
        c = sum(a for a, _ in rows)
        w = sum(b for _, b in rows)
        print(f"fuse={fuse!s:5} n={n:<3} caught={c}/6 wrong={w}  {rows}")
