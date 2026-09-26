# VISHWAS ClaimCheck — independent, physics-backed accusation verification

**Status:** implemented (`vishwas/verify/`), 2026-08-27
**Problem owner:** majority-compromise false-expulsion failure, `CONTINUE_FROM_HERE.md` §"too few honest"

---

## 1. The problem, stated precisely

Every mechanism in `vishwas/consensus/` is a **vote**:

- the trimmed weighted mean of suspicion reports,
- the `2f+1`-of-`3f+1` quorum,
- credibility weighting,
- mutual-accusation damping.

Each of them aggregates opinions, and an aggregate of opinions follows whichever
bloc is larger. That is not a tuning defect; it is what an estimator *is*. A
trimmed mean cannot survive >50% contamination, and a quorum cannot tell an
honest majority from a compromised one once the compromised side **is** the
majority.

Measured directly (`scratch/diag24_few_honest.py`, n=9, `collusion`, seeds 7/11):

| compromised | attackers caught | honest wrongly expelled |
|---|---|---|
| 3 of 9 | 6/6 | 0 |
| 4 of 9 | 6/8 | 0 |
| **5 of 9** | **0/10** | **7** |
| 6 of 9 | 0/12 | 6 |

The failure is not "detection gets worse". It flips: the swarm starts
convicting the honest minority. That is the behaviour the Terrier Cyber Quest
pitch has to eliminate — a defence system that turns into the attack once it is
outnumbered is worse than no system.

## 2. A simulator bug found on the way, and fixed

While instrumenting pairwise range reciprocity `|r_ij - r_ji|` to design the
verifier, every pair in every scenario measured ~0.8 sigma — the pure-noise
level — including pairs containing a drone actively lying about its position.
That is impossible if anybody's published ranges disagree with anybody else's.

Cause: `AttackBehaviour.corrupt_ranges` gated only on `active(tick)`, not on
`malicious`. The base class is the **control** behaviour used by every honest
drone, so after `attack_start` every honest drone re-derived its published
ranges from the *claimed* world instead of reporting what its radio measured.
Honest peers were therefore corroborating whatever position a liar asserted,
the multilateration fix reproduced the fabricated world by construction, and
the only surviving E2 evidence was each drone's own first-hand `direct_z`
(which `swarm._score` takes straight from `Mesh.measure_ranges` and never
routes through the attack layer).

This is the same bug `corrupt_suspicion` already documents having fixed, one
method up. Fixed identically: `if not self.malicious or not self.active(tick)`.

**Consequence for published results:** `data/results/*.json` were generated
before this fix and understate E2. They need regenerating; the fix is called
out here so the change in numbers is attributable rather than mysterious.

## 3. Design — an accusation is a claim, not a ballot

`vishwas/verify/` audits every accusation against measurements the swarm
**already broadcast**. Nothing here aggregates anything, and nothing reads
ground truth.

Two kinds of data, and the whole argument rests on the difference:

- **Statements** — the gossiped suspicion score and its cited z-vector. A
  compromised drone writes these freely.
- **Commitments** — `TelemetryMsg` claims and `RangeReportMsg` ranges. Also
  freely chosen by an attacker, but chosen **once, in public, before it knows
  which accusation will be audited**, and not retractable or variable per-audit.

### Four checks

1. **Claim-to-evidence binding** (`verdict.py`). No cited measurement → `UNSUPPORTED`.
   Never punished — an honest drone with no usable geometry is in exactly that
   position — and never counted.
2. **Commitment audit** (`recompute.commitment_residual`). The accuser's own
   published range to the target, plus both claimed positions, reproduces
   exactly the `direct_z` its own E2 checker computed. An accuser cannot both
   publish a range confirming the target's claim and assert that range shows an
   anomaly. Self-contradiction is checkable by anyone listening.
3. **Independent re-derivation** (`recompute.independent_fix`). Re-fix the
   target by multilateration from every *other* drone's published ranges, with
   the accuser excluded from its own alibi. Fix agrees with the claim →
   `REFUTED`; contradicts it → `SUPPORTED`; in between → `AMBIGUOUS`.
   Co-accusers are deliberately **not** excluded: in a normal sub-Byzantine
   round the co-accusers *are* the honest majority, and dropping them would gut
   real detection.
4. **Evidence independence** (`independence.py`). Two honest drones watching the
   same anomalous peer compute z-scores from their own radios at their own
   geometry; those numbers differ by measurement noise every time. Vectors that
   agree to floating-point noise came from one script broadcast twice, not two
   radios. Near-exact agreement is therefore treated as **duplication** and
   collapsed to one root claim — the counter-intuitive reading, and the correct
   one.

### The round-level question: is the geometry arbitrable at all?

`recompute.assess_geometry` peels the least reciprocity-consistent drone until
what remains is mutually consistent, extracting the largest self-consistent
account of where everybody is. The geometry is `contested` — every claim
resolving `AMBIGUOUS` — when that surviving account is not a strict majority
of the live swarm.

**This threshold was wrong in the first implementation and the mistake is
worth recording.** It originally fired when the discarded set exceeded
`f_tolerated` (= 2 at n = 9). Measured: `position_teleport` with 3 of 9
compromised produced `outliers = [0, 1, 6]` — *exactly* the attackers — and the
rule then discarded that identification, resolved all 2880 claims `AMBIGUOUS`
and expelled nobody. A rule that identifies the liars and declines to use the
identification is not caution.

Two further findings bound what this mechanism can be relied on for:

* **Every optimal-forgery liar agrees with every other liar.** They all
  re-derive ranges from the same published claims, so two liars agree whatever
  direction each is lying in, while honest drones agree with each other on the
  measured truth. The reciprocity graph under this attack suite is therefore
  always exactly two cliques and the larger always wins — so `contested`
  essentially cannot fire here. It is meaningful for *uncoordinated* corruption
  (drifting clocks, damaged radios, attackers not sharing one script).
* **The split is barely visible for slow collusion at all.** Common-mode drift
  produces a reciprocity residual equal only to the drift's *projection* onto
  each baseline, usually under the 4σ threshold, so `dominant` typically
  contains 7–8 drones mixing honest and compromised. Any rule keyed on
  dominant-world membership is inert in that scenario — which is exactly why
  one such rule was tried against the fabricator detector and did nothing (§7).

`MeshConfig.certified_anchors` is what breaks the symmetry from outside the
swarm: anchors are exempt from the peel, and whatever directly contradicts an
anchor goes first.

### Verdicts → actions (`safety.py`)

| Verdict | Action |
|---|---|
| `SUPPORTED` | counts toward quorum; expulsion may proceed |
| `REFUTED` | claim dropped; accuser pays credibility |
| `UNSUPPORTED` | claim dropped; no penalty either way |
| `AMBIGUOUS` | stays in the weighted tally, cannot expel; target **quarantined** |

Quarantine is reversible: the drone keeps flying, keeps being measured, and
**keeps its own vote**. Silencing a quarantined drone's accusations would hand
an attacker a way to mute honest accusers by getting them held — the same
wrongful-removal attack, one step removed.

Expulsion requires `quorum_size` claims that **survived independent
re-derivation**. That is a number a bloc cannot inflate by growing, because
fabricated claims produce none of them.

## 3a. The fabricator detector had to be gated too

`_track_fabrication` (the byzantine-accuser self-detector from
CONTINUE_FROM_HERE.md §10) was the last majority-derived mechanism left
outside the gate, and past the bound it inverts exactly like the quorum does.
Its input was "targets the rest of the swarm did not corroborate" — and once
the compromised drones are the majority, the honest minority's *correct*
accusations are precisely the ones nobody corroborates.

Caught in the act (`collusion`, 5 of 9, seed 7): four honest drones expelled,
and the targets each had named were **exclusively real attackers**. Honest
aircraft removed for correctly identifying the entire compromised set.

Two fixes were tried and failed, and are recorded in the code so they are not
retried: feeding it *refuted* claims instead of uncorroborated ones (inert —
with a forging majority the re-derivation that refutes is itself computed from
the liars' anchors), and requiring the accused fabricator to sit inside the
dominant reciprocity world (inert, for the reason in §3).

What works is a rule about the **target**, not the accuser: strip from each
accuser's fabrication count every target some peer produced an independently
*verified* claim against. A fabricator invents victims, so no verified case
against them exists anywhere and its count stands; an honest drone names real
attackers, and if the physics can indict those attackers from anywhere in the
swarm, the naming is defensible.

**Accepted cost.** Without an anchor the detector no longer fires, so a
gossip-only fabricator is *disarmed but not expelled* — all its claims refuted,
zero exclusions caused, victims safe, attacker still flying. This is not a
tuning failure: a gossip-only fabricator and an honest drone surrounded by
liars fly identically, range identically, and both accuse peers the swarm will
not corroborate. The only difference is whether the accused are guilty, which
is the one thing unknowable past the bound. The anchor mode recovers it.

## 4. Honest scope of the guarantee

ClaimCheck **cannot** make the swarm detect attackers past the point where it
can no longer trust its own measurements. Nothing can, from range data alone —
that is an impossibility result, not an implementation gap.

What it changes is the failure mode:

- **before:** past the bound, the swarm expels the honest minority;
- **after:** past the bound, the swarm declines to expel anyone and says why.

And for the specific attack the project set out to stop — compromised drones
*fabricating accusations* to remove innocent peers — the bound does not apply
at all. A drone that lies only in gossip flies and ranges honestly, so it
leaves the independent fix untouched, and its claims are refuted individually
at **any** compromise ratio. Measured at 5 of 9 compromised: 450 claims
refuted, all attributed to exactly the 5 compromised drones, zero exclusions.

`certified_anchors` is the escape hatch, and it has existed dormant in
`MeshConfig` since before this work — `verify/` is its first caller. It is now
a first-class **assurance mode** (`vote` / `verified` / `anchored`) in the
dashboard and a third arm in `claimcheck_matrix`, so the three configurations
can be flown live on one seed instead of asserted from a slide.

## 4a. Measured

216 missions, n = 9, seeds 7/11/13, four scenarios, 1–6 compromised, three arms
on identical seeds:

* **Honest drones wrongly expelled: 55 (vote-only) → 0 (verified)**, at every
  compromise level tested, in every scenario.
* Recall below the bound is unchanged (3/3, 6/6, 9/9 at 1–3 compromised);
  a dip appears at 4 of 9 (collusion 12/12 → 8/12), which the anchor arm
  recovers to 11/12.
* The anchor arm is not merely a restoration: `byzantine_accuser` at 3 of 9
  goes from **0/9 caught in both other arms to 7/9**, still with nobody honest
  expelled.

## 5. Files

**New:** `vishwas/verify/{__init__,claim,recompute,independence,verdict,ledger,safety,verifier}.py`

**Modified:**
- `vishwas/config.py` — `ClaimCheckConfig`, `SimConfig.enable_claim_verifier`
- `vishwas/consensus/voting.py` — audit before aggregating; ClaimCheck gate in `_decide`; `_penalise_refuted`
- `vishwas/sim/swarm.py` — publish `ClaimBundle`; drain blocks into the event log; quarantine in `summary()`
- `vishwas/sim/attacks.py` — **bugfix** to `corrupt_ranges`; new `corrupt_claim_evidence`
- `vishwas/sim/__init__.py` — lazy re-exports (PEP 562) to break an import cycle
- `vishwas/evidence/e2_cross.py` — `_flatten` promoted to module-level `flatten_to_plane`, shared with the audit
- `vishwas/evaluation/{harness,study}.py` — `enable_claim_verifier`, `claimcheck_matrix`
- `vishwas/api/{runner,frames,explain}.py`, `ui/{index.html,app.js,style.css}`

**Tests:** `tests/{conftest,test_claim_verifier,test_geometry,test_independence,test_safety,test_majority_attack}.py`

## 6. Threat model note — `corrupt_claim_evidence`

Without it ClaimCheck would be fighting a strawman: an attacker gossiping
"peer 4 is 0.95 suspicious" while honestly attaching `z2 = 0.3` refutes itself
in one line. The new capability back-fills a z-vector consistent with whatever
suspicion the attacker chose to broadcast, so the binding test passes and the
verifier has to beat a competent liar. Making the lie self-consistent does not
make it survive — it moves where it breaks, from the statement to the
commitment.
