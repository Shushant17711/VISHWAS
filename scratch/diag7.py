import numpy as np
from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm
from vishwas.consensus.credibility import CredibilityBook

real_weights = CredibilityBook.weights

def run(n, flat_cred, h, ticks=900):
    if flat_cred:
        CredibilityBook.weights = lambda self: {d: 1.0 for d in real_weights(self)}
    else:
        CredibilityBook.weights = real_weights
    cfg = SimConfig(n_drones=n, max_ticks=ticks, seed=7)
    cfg.evidence.cusum_threshold_h = h
    s = Swarm(cfg, scenario="collusion", n_compromised=2).run()
    m = s.summary()
    bad = set(m['compromised'])
    exc = set(s.consensus.excluded)
    return exc, bad, m.get('true_positives'), m.get('false_exclusions')

print(f"{'cfg':<34} {'excluded':<12} {'caught':<8} {'wrong'}")
for n in (9, 13):
    for flat in (False, True):
        for h in (6.5, 4.0):
            exc, bad, tp, fp = run(n, flat, h)
            tag = f"n={n} cred={'FLAT' if flat else 'real'} h={h}"
            print(f"{tag:<34} {str(sorted(exc)):<12} "
                  f"{str(sorted(exc & bad)):<8} {sorted(exc - bad)}")
