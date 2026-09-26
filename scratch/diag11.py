import numpy as np
from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm
from vishwas.consensus.credibility import CredibilityBook

REAL = CredibilityBook.weights

def flatten_cred(on):
    if on:
        CredibilityBook.weights = lambda self: {d: 1.0 for d in REAL(self)}
    else:
        CredibilityBook.weights = REAL

print(f"{'cfg':<22} {'z2tr':>6} {'z2hon':>6} {'sig':>6} {'off':>6} {'cus':>6} {'susp':>6} {'acc':>4} {'W':>6} excluded")
for n in (9, 13):
    for flat in (False, True):
        flatten_cred(flat)
        cfg = SimConfig(n_drones=n, max_ticks=900, seed=7)
        s = Swarm(cfg, scenario="collusion", n_compromised=2).run()
        m = s.summary()
        bad = [i for i in range(n) if s.drones[i].behaviour.malicious]
        tgt = sorted(bad)[0]
        # re-run to sample internals is expensive; instead use history-free direct sampling
        flatten_cred(flat)
        cfg2 = SimConfig(n_drones=n, max_ticks=900, seed=7)
        s2 = Swarm(cfg2, scenario="collusion", n_compromised=2)
        best = None
        for t in range(900):
            s2.step()
            live = [d for d in s2.consensus.live_drones()]
            if tgt not in live: break
            honest = [i for i in live if i not in bad]
            rows = []
            for i in honest:
                a = s2.drones[i].engine.assessments.get(tgt)
                if a is None or a.abstained: continue
                rows.append((a.z2, a.cusum, a.suspicion, a.detail.get("e2_sigma", np.nan),
                             a.detail.get("e2_offset_m", np.nan)))
            hon_rows = []
            for i in honest:
                for j in honest:
                    if i == j: continue
                    a = s2.drones[i].engine.assessments.get(j)
                    if a is None or a.abstained: continue
                    hon_rows.append(a.z2)
            if not rows: continue
            A = np.array(rows, dtype=float)
            tal = s2.consensus.tallies.get(tgt)
            W = tal.weight if tal else 0.0
            nacc = len(tal.accusers) if tal else 0
            susp = np.nanmean(A[:, 2])
            if best is None or susp > best[0]:
                best = (susp, np.nanmean(A[:, 0]), np.nanmean(hon_rows) if hon_rows else np.nan,
                        np.nanmean(A[:, 3]), np.nanmean(A[:, 4]), np.nanmean(A[:, 1]), nacc, W)
        tag = f"n={n} cred={'FLAT' if flat else 'real'}"
        if best:
            print(f"{tag:<22} {best[1]:6.3f} {best[2]:6.3f} {best[3]:6.3f} {best[4]:6.3f} "
                  f"{best[5]:6.3f} {best[0]:6.3f} {best[6]:4d} {best[7]:6.3f} {sorted(s.consensus.excluded)}")
        else:
            print(f"{tag:<22} no rows")
flatten_cred(False)
