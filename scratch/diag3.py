import numpy as np
from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm

def probe(n, ticks=800):
    s = Swarm(SimConfig(n_drones=n, max_ticks=ticks, seed=7),
              scenario="collusion", n_compromised=2)
    bad = None
    rows = []
    for t in range(ticks):
        s.step()
        if bad is None:
            bad = [i for i in range(n) if s.drones[i].behaviour.malicious]
        honest = [i for i in range(n) if i not in bad]
        tgt = bad[0]
        acc = []
        for i in honest:
            a = s.drones[i].engine.assessments.get(tgt)
            if a is None or a.abstained: continue
            acc.append((a.z1, a.z2, a.z3, a.cusum, a.suspicion))
        base = []
        for i in honest:
            for j in honest:
                if i == j: continue
                a = s.drones[i].engine.assessments.get(j)
                if a is None or a.abstained: continue
                base.append((a.z1, a.z2, a.z3, a.cusum, a.suspicion))
        if not acc: continue
        A = np.array(acc); B = np.array(base) if base else np.zeros((1,5))
        rows.append((t, len(acc), A.mean(0), B.mean(0)))
    if not rows: 
        print(f"n={n}: no non-abstained assessments"); return
    peak = max(rows, key=lambda r: r[2][4])
    print(f"=== n={n} bad={bad} live_end={len(s._live())} excluded={sorted(s.consensus.excluded)} ===")
    for label, r in (("peak-susp", peak),):
        t, k, A, B = r
        print(f" {label} tick={t} voters={k}")
        print(f"   traitor z1={A[0]:.3f} z2={A[1]:.3f} z3={A[2]:.3f} cusum={A[3]:.3f} susp={A[4]:.3f}")
        print(f"   honest  z1={B[0]:.3f} z2={B[1]:.3f} z3={B[2]:.3f} cusum={B[3]:.3f} susp={B[4]:.3f}")
    # sparse trajectory
    for t, k, A, B in rows[::max(1,len(rows)//10)]:
        print(f"   t={t:4d} vot={k:2d} z2={A[1]:.3f}/{B[1]:.3f} cus={A[3]:.3f}/{B[3]:.3f} susp={A[4]:.3f}/{B[4]:.3f}")

for n in (9, 13):
    probe(n)

# ---- dilution check: how many live peers actually score the traitor at all
def dilution(n, ticks=800):
    s = Swarm(SimConfig(n_drones=n, max_ticks=ticks, seed=7),
              scenario="collusion", n_compromised=2)
    best = None
    for t in range(ticks):
        s.step()
        bad = [i for i in range(n) if s.drones[i].behaviour.malicious]
        live = [d for d in s.consensus.active if d not in s.consensus.excluded]
        tgt = bad[0]
        if tgt not in live: break
        tal = s.consensus.tallies.get(tgt)
        if tal is None: continue
        honest = [i for i in live if i not in bad]
        seen = abst = 0
        for i in honest:
            a = s.drones[i].engine.assessments.get(tgt)
            if a is None: continue
            seen += 1
            if a.abstained: abst += 1
        rec = (tal.weight, t, len(live), len(honest), seen, abst,
               len(tal.accusers), s.consensus.quorum_size)
        if best is None or rec[0] > best[0]: best = rec
    w, t, nlive, nhon, seen, abst, nacc, q = best
    print(f"n={n}: peak tally.weight={w:.3f} at t={t} | live={nlive} honest={nhon} "
          f"scored={seen} abstained={abst} accusers={nacc} quorum_needed={q} thr=0.62")

for n in (9, 11, 13):
    dilution(n)
