import numpy as np
from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm
import vishwas.evidence.e2_cross as e2

ORIG = e2.CrossObservationChecker.score

def make(mode):
    def patched(self, target, claims, my_position, my_ranges, peer_ranges,
                credibility=None, **kw):
        r = ORIG(self, target, claims, my_position, my_ranges, peer_ranges,
                 credibility=credibility, **kw)
        if mode == "direct":
            if not r.abstained and r.direct_z > r.z:
                r.z = r.direct_z
                self.last[target] = r
        return r
    return patched

for mode in ("orig", "direct"):
    e2.CrossObservationChecker.score = ORIG if mode == "orig" else make(mode)
    for n in (9, 11, 12, 13, 14, 15, 16):
        row = []
        for seed in (7, 11, 13):
            s = Swarm(SimConfig(n_drones=n, max_ticks=900, seed=seed),
                      scenario="collusion", n_compromised=2).run()
            m = s.summary()
            bad = set(m['compromised'])
            exc = set(s.consensus.excluded)
            row.append((len(exc & bad), len(exc - bad)))
        c = sum(a for a, _ in row); w = sum(b for _, b in row)
        print(f"{mode:>7} n={n:<3} caught={c}/6  wrong={w}  {row}")
