"""Follow-up to diag15/16: is the n=13 direct-mode dip (2/6 caught, weaker
than n=12's 5/6 and n=14's 6/6) seed-13-specific or genuinely n=13-specific?

Part A: sweep more seeds at n=13 (fixed) - if the dip persists across many
        seeds, it's a real n=13 effect, not noise from seeds (7,11,13).
Part B: sweep n around 13 at the single seed=13 - if seed=13 is uniformly
        bad/good regardless of n, the dip is seed-specific, not n-specific.

Uses the live source path (SimConfig defaults: e2_fuse_direct_z=True,
quorum_size capped at max_observers - confirmed a no-op for n<=18, so this
is equivalent to testing against the current default config either way).
"""
from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm


def run(n, seed):
    cfg = SimConfig(n_drones=n, max_ticks=900, seed=seed)
    s = Swarm(cfg, scenario="collusion", n_compromised=2).run()
    m = s.summary()
    bad = set(m["compromised"])
    exc = set(s.consensus.excluded)
    caught = len(bad & exc)
    wrong = len(exc - bad)
    return caught, wrong


print("=== Part A: n=13 fixed, more seeds ===", flush=True)
seeds_a = (7, 11, 13, 1, 17, 23, 29, 31, 37)
tot_c = tot_w = 0
for seed in seeds_a:
    c, w = run(13, seed)
    tot_c += c
    tot_w += w
    print(f"n=13 seed={seed:<3} caught={c} wrong={w}", flush=True)
print(f"n=13 TOTAL caught={tot_c}/{2*len(seeds_a)} wrong={tot_w}", flush=True)

print("=== Part B: seed=13 fixed, n around 13 ===", flush=True)
for n in (11, 12, 13, 14, 15):
    c, w = run(n, 13)
    print(f"n={n:<3} seed=13 caught={c} wrong={w}", flush=True)
