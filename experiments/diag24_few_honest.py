import sys
from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm

def f_tol(n): return (n - 1) // 3

configs = [
    (9, list(range(1, 7))),
    (12, list(range(1, 8))),
]
scenarios = ["collusion", "position_teleport"]
seeds = [7, 11, 13]

for n_drones, comp_range in configs:
    for scen in scenarios:
        for n_comp in comp_range:
            if n_comp >= n_drones:
                continue
            caught_tot = wrong_tot = runs = 0
            for seed in seeds:
                cfg = SimConfig(n_drones=n_drones, max_ticks=700, seed=seed)
                s = Swarm(cfg, scenario=scen, n_compromised=n_comp)
                s.run()
                bad = set(s.compromised)
                exc = set(s.consensus.excluded)
                caught_tot += len(bad & exc)
                wrong_tot += len(exc - bad)
                runs += n_comp
            tag = "OK" if wrong_tot == 0 else "WRONG-ACCUSATIONS"
            print(f"n={n_drones:2d} f_tol={f_tol(n_drones)} scen={scen:<18} n_comp={n_comp} "
                  f"honest={n_drones-n_comp:2d} caught={caught_tot}/{runs} wrong={wrong_tot}  [{tag}]", flush=True)
print("DONE", flush=True)
