import sys
from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm

scenarios = ['position_teleport', 'slow_drift', 'false_target',
             'formation_sabotage', 'byzantine_accuser', 'collusion']
seeds = [7, 11, 13, 17, 23]

print(f'{"scenario":<20}{"n_comp":>7}{"caught":>10}{"wrong":>10}', flush=True)
for key in scenarios:
    n_comp = 2 if key == 'collusion' else 1
    caught_tot, wrong_tot, runs = 0, 0, 0
    for seed in seeds:
        cfg = SimConfig(n_drones=12, max_ticks=900, seed=seed)
        s = Swarm(cfg, scenario=key, n_compromised=n_comp).run()
        m = s.summary()
        bad = set(m['compromised'])
        exc = set(s.consensus.excluded)
        caught_tot += len(bad & exc)
        wrong_tot += len(exc - bad)
        runs += n_comp
        print(f'  ..{key} seed={seed} caught={len(bad & exc)}/{n_comp} wrong={len(exc - bad)}', flush=True)
    print(f'{key:<20}{n_comp:>7}{caught_tot:>4}/{runs:<5}{wrong_tot:>6}', flush=True)
print("DONE", flush=True)
