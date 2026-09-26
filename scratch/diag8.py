import numpy as np
from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm

print(f"{'cfg':<16} {'peakW':>6} {'acc':>4} {'need':>5} {'live':>5} {'scored':>7} {'maxSus':>7} {'excluded'}")
for n in (9, 11, 13):
    for h in (6.5, 4.0):
        cfg = SimConfig(n_drones=n, max_ticks=900, seed=7)
        cfg.evidence.cusum_threshold_h = h
        s = Swarm(cfg, scenario="collusion", n_compromised=2)
        bad = None; tgt = None
        pw = 0.0; pa = 0; pneed = 0; plive = 0; pscored = 0; msus = 0
        for t in range(900):
            s.step()
            if bad is None:
                bad = [i for i in range(n) if s.drones[i].behaviour.malicious]
                tgt = bad[0]
            live = [d for d in s.consensus.active if d not in s.consensus.excluded]
            if tgt not in live: break
            tal = s.consensus.tallies.get(tgt)
            if tal is None: continue
            honest = [i for i in live if i not in bad]
            scored = 0
            for i in honest:
                a = s.drones[i].engine.assessments.get(tgt)
                if a is not None and not a.abstained: scored += 1
            need = min(s.consensus.quorum_size, max(1, len(live) - 1))
            msus = max(msus, s.consensus._sustain.get(tgt, 0))
            if tal.weight > pw:
                pw, pa, pneed, plive, pscored = tal.weight, len(tal.accusers), need, len(live), scored
        print(f"n={n:<2d} h={h:<4}    {pw:6.3f} {pa:4d} {pneed:5d} {plive:5d} {pscored:7d} {msus:7d} {sorted(s.consensus.excluded)}")
