from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm

for n, ticks in ((9, 700), (9, 2200), (13, 700), (13, 2200)):
    s = Swarm(SimConfig(n_drones=n, max_ticks=ticks), scenario='collusion', n_compromised=2).run()
    m = s.summary()
    bad = m['compromised']
    traj = []
    for r in s.history[::40]:
        traj.append((r.tick, round(max((r.suspicion.get(b, 0.0) for b in bad), default=0), 2)))
    print(f"n={n} ticks={ticks} bad={bad} tp={m['true_positives']} fp={m['false_exclusions']} "
          f"cov={m['coverage']:.3f} lat={m['detection_latency_s']}")
    print("   peak-bad-susp:", traj)
    print("   conn:", [round(r.connectivity, 2) for r in s.history[::100]])
