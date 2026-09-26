import numpy as np
from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm
from vishwas.consensus.voting import ConsensusEngine, VoteTally

ORIG = ConsensusEngine.round

def patched(self, tick, reports):
    self.rounds += 1
    live = [d for d in self.active if d not in self.excluded]
    self_susp = self._self_suspicion(reports, live)
    weights = self.credibility.effective(self_susp, self.cn.self_suspicion_discount)
    qual = getattr(self, "_qual", {})
    tallies = {t: VoteTally(t) for t in live}
    for target in live:
        tally = tallies[target]
        num = den = 0.0
        nqual = 0
        for accuser in live:
            if accuser == target:
                continue
            if target not in qual.get(accuser, set()):
                continue                      # <-- only witnesses vote
            nqual += 1
            w = weights.get(accuser, self.cn.cred_floor)
            s = float(reports.get(accuser, {}).get(target, 0.0))
            back = float(reports.get(target, {}).get(accuser, 0.0))
            if s >= self.cn.accuse_threshold and back >= self.cn.accuse_threshold:
                s *= self.cn.mutual_accusation_damping
            den += w
            num += w * s
            if s >= self.cn.accuse_threshold:
                tally.accusers.append(accuser)
                tally.accuser_weight += w
        tally.weight = float(num / den) if den > 1e-9 else 0.0
        need = min(self.quorum_size, max(1, nqual))
        tally.quorum_met = (
            tally.weight >= self.cn.quorum_weight_threshold
            and len(tally.accusers) >= need
            and nqual >= 3
        )
    self.tallies = tallies
    events = self._decide(tick, tallies, live)
    self._penalise_dissent(tallies, live)
    self._history.append({"tick": tick, "tallies": {t: v.as_row() for t, v in tallies.items()}})
    return events

ORIG_AR = Swarm._accusation_round
def patched_ar(self):
    live = self._contributing()
    qual = {}
    for i in live:
        d = self.drones[i]
        qual[i] = {j for j in live if j != i
                   and (a := d.engine.assessments.get(j)) is not None and not a.abstained}
    self.consensus._qual = qual
    return ORIG_AR(self)

print(f"{'cfg':<26} {'excluded':<10} {'caught':<8} wrong")
for fix in (False, True):
    ConsensusEngine.round = patched if fix else ORIG
    Swarm._accusation_round = patched_ar if fix else ORIG_AR
    for n in (9, 11, 13):
        for h in (6.5, 4.0):
            cfg = SimConfig(n_drones=n, max_ticks=900, seed=7)
            cfg.evidence.cusum_threshold_h = h
            s = Swarm(cfg, scenario="collusion", n_compromised=2).run()
            m = s.summary()
            bad = set(m['compromised']); exc = set(s.consensus.excluded)
            tag = f"n={n:<2d} h={h:<4} vote={'FIX' if fix else 'orig'}"
            print(f"{tag:<26} {str(sorted(exc)):<10} {str(sorted(exc&bad)):<8} {sorted(exc-bad)}")
