import numpy as np
from vishwas.config import SimConfig
from vishwas.sim.swarm import Swarm
import vishwas.evidence.e2_cross as e2

ORIG = e2.CrossObservationChecker.score
REC = {}

def patched(self, target, claims, my_position, my_ranges, peer_ranges,
            credibility=None, **kw):
    r = ORIG(self, target, claims, my_position, my_ranges, peer_ranges,
             credibility=credibility, **kw)
    REC.setdefault(KEY, []).append(
        (target, r.z, r.direct_z, r.offset, r.sigma,
         r.n_observers, r.n_inliers, 1.0 if r.abstained else 0.0))
    return r

e2.CrossObservationChecker.score = patched

print(f"{'n':>3} {'who':>7} {'N':>7} {'abst%':>6} {'z':>6} {'dirz':>6} {'off':>7} "
      f"{'sigma':>7} {'obs':>6} {'inl':>6}")
for n in (9, 13):
    KEY = n
    s = Swarm(SimConfig(n_drones=n, max_ticks=420, seed=7),
              scenario="collusion", n_compromised=2).run()
    bad = set(s.summary()['compromised'])
    A = np.array(REC[n], dtype=float)
    for who, mask in (("traitor", np.isin(A[:, 0], list(bad))),
                      ("honest", ~np.isin(A[:, 0], list(bad)))):
        S = A[mask]
        live = S[S[:, 7] == 0.0]
        print(f"{n:>3} {who:>7} {len(S):>7} {100*S[:,7].mean():>6.1f} "
              f"{np.nanmean(live[:,1]):>6.3f} {np.nanmean(live[:,2]):>6.3f} "
              f"{np.nanmean(live[:,3]):>7.3f} {np.nanmean(live[:,4]):>7.3f} "
              f"{np.nanmean(live[:,5]):>6.2f} {np.nanmean(live[:,6]):>6.2f}")
