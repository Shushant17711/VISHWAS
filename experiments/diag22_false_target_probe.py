import sys
from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm
from vishwas.consensus.voting import ConsensusEngine

log = []
orig = ConsensusEngine._track_fabrication
def wrapped(self, tick, round_targets, live, just_excluded):
    log.append((tick, {a: sorted(t) for a, t in round_targets.items() if t},
                dict(self._fab_breadth)))
    return orig(self, tick, round_targets, live, just_excluded)
ConsensusEngine._track_fabrication = wrapped

cfg = SimConfig(n_drones=12, max_ticks=900, seed=11)
s = Swarm(cfg, scenario='false_target', n_compromised=1)
s.run()
print('compromised', s.compromised, 'excluded', s.consensus.excluded)
for ev in s.consensus.events:
    print(ev.as_row())

# drone 4's round-by-round targets, and its breadth trajectory
print('--- drone 4 round_targets (non-empty rounds) ---')
for tick, rt, breadth in log:
    if 4 in rt:
        print(tick, 'targets=', rt[4], 'breadth_after=', round(breadth.get(4,0),3))
print('--- drone 9 (real attacker) round_targets (non-empty rounds) ---')
for tick, rt, breadth in log:
    if 9 in rt:
        print(tick, 'targets=', rt[9], 'breadth_after=', round(breadth.get(9,0),3))
