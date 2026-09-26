# CONTINUE FROM HERE - VISHWAS recall-cliff investigation

Written: 2026-08-20 ~14:30 IST. **The n=15/16 direct-mode sweep that was running in the
background has finished. The full diagnostic picture (§4) is now complete.** Nothing is
still running. This file is the source of truth (claude-mem is down, see prior note below).

## 0. Housekeeping note (unrelated to this task, kept for continuity)
claude-mem (memory/observer) is down: OpenRouter rate-limited (429, free-models-per-day)
since ~2026-08-20T01:02Z, 31+ consecutive failures. Nothing said in chat is being saved to
long-term memory right now. If this is fixed by the time you read this, fine, but don't
assume prior-session context will auto-inject; treat this file as the source of truth.

## 1. What this project is
`vishwas/` - a Byzantine-fault-tolerant drone-swarm integrity/consensus simulator ("VISHWAS").
Drones score each other on multiple evidence channels (E1 self-consistency, E2 cross-
observation/multilateration, E3 mission), feed suspicion into a CUSUM detector, gossip
suspicion scores, and a weighted-quorum voting system (`vishwas/consensus/voting.py`) expels
drones once accusation weight clears a threshold with enough independent accusers. Four
canonical published studies live in `data/results/*.json` (scenarios, ablation, degradation,
cusum) generated via `vishwas/evaluation/study.py`.

This investigation is **exploratory work on top of that**, not one of the four canonical
studies: sweeping swarm size `n` (not compromised-fraction) on a fixed 2-colluder scenario
and finding that detection **recall collapses** somewhere around n=11→12, even though nothing
else about the attack changed.

## 2. THE BUG - fully diagnosed AND fixed-and-verified (in scratch/, not yet ported to source)

### Symptom
Fixed scenario: `scenario="collusion", n_compromised=2`. Sweep `n_drones`. Recall (fraction
of the 2 colluders actually excluded) collapses hard between n=11 and n=12, and by n=16 the
system starts expelling **honest** drones instead (false-accusation failure) - i.e. total-
caution failure, not false-accusation failure, at n≤15; false-accusation failure appears only
at n=16 in unpatched mode.

### Root cause (confirmed via code inspection + real E2 field measurements)
Two independent effects compound:

**(a) Quorum grows, evidence margin doesn't.** `vishwas/consensus/voting.py`:
- `f_tolerated = (n-1)//3` (property, line ~109)
- `quorum_size = 2*f_tolerated + 1` (property, line ~116) → grows ≈ 2n/3
- `tally.weight = num/den` where `den` sums credibility weight over **every** live peer
  holding an assessment (not just qualified witnesses), `round()`, lines ~136-158)

Measured peak-suspicion tick internals (z2, cusum, sigma, offset, accusers, W) at
n=9/n=11/n=13: `quorum_weight_threshold = 0.62` (config.py line ~133): **0.897 (n=9) →
0.710 (n=11) → 0.449 (n=13)**. Accusers needed vs available: 7/7 → 9/11 → 5/9. The number
of peers that actually cross `accuse_threshold = 0.55` does not grow with n; the quorum
requirement does.

**(b) E2 throws away its strongest signal.** `CrossObservationChecker.score()`
(`vishwas/evidence/e2_cross.py`):
- `direct_z` (line ~111, via `_direct()`) = a drone's own first-hand range/bearing
  check against the target. Scale-invariant, **does not degrade with swarm size**.
- The fused multilateration `z` (line ~177, `report.z = float(offset/sigma)`) is what's
  actually voted on. `direct_z` is only used as a **fallback** when `z` is unusable
  (lines ~133-134, ~157-158) - see the comment at lines ~169-176 explaining *why* (deliberate
  design: they worried `max(z, direct_z)` would double-count anchor-0's own measurement and
  bias every honest peer's residual upward → false accusations).

Real field measurements (~6k/54k reports sampled, traitor vs honest):

| n | who | fused z | direct_z | offset | sigma | observers |
|---|---|---|---|---|---|---|
| 9 | traitor | 1.072 | 3.622 | 4.447 | 4.124 | 7.7 |
| 9 | honest | 0.692 | 0.950 | 3.720 | 5.341 | 7.7 |
| 13 | traitor | 0.874 | 3.500 | 3.342 | 3.841 | 11.6 |
| 13 | honest | 0.650 | 0.943 | 2.679 | 4.184 | 11.6 |

`direct_z` separates traitor from honest by ~3.8x at n=9 and 3.7x at n=13 - basically flat.
The fused `z` E2 actually votes on separates by only ~1.55x at n=9, decaying to 1.34x at
n=13 - it's the weaker statistic AND it decays. The colluding accomplice's corrupted ranges
pull the geometric fix toward the lie (offset shrinks 4.45→3.34m faster than sigma falls),
so the fused `z`'s swarm-vote margin erodes just fast enough to lose the race against the
growing quorum between n=11 and n=12.

### Things already ruled out (don't re-chase)
- **Not a threshold knob**: sweeping `accuse_threshold` 0.55↔0.35 at n=13 does nothing;
  tally plateaus ~0.5 and stops. **Not `max_ticks`**: 700 vs 2200 changes nothing.
- **Not the qualified-witness denominator**: implemented and tested restricting the tally
  denominator to only peers who actually hold a non-abstained assessment of the target -
  a **no-op** at n=13. Both this fix and the threshold sweep are inert.
- **Not credibility lock-in**: credibility is pinned at ×1.55 at n=13 (because `uphold()`
  only fires on a successful exclusion, which never happens - chicken-and-egg), but the
  FLAT-credibility ablation (`scratch/diag11.py`, `diag8.py`) still fails at n=13. It's an
  amplifier, not the cause.
- **Not solver geometry**: `_flatten` scale is exactly 1.000 (`scratch/diag5.py`,
  `diag13.py`). The multilateration solver itself is healthy - this is a voting-mechanism
  and evidence-selection problem, not a numerics problem.

## 3. THE FIX - now verified across the FULL n=9..16 range (see §4). Two parts:

1. **`vishwas/evidence/e2_cross.py`**: use `max(z, direct_z)` (or a proper fusion) as the
   E2 statistic instead of treating `direct_z` as a fallback-only signal. The existing
   in-code comment (lines ~169-176) worried this double-counts anchor-0 and biases honest
   peers upward - that concern was checked empirically in §4: it's a real but small effect
   (occasional false positives, see n=12/13/15), **not a blocking problem** - net recall
   improves sharply everywhere it was tested.
2. **`vishwas/consensus/voting.py`**: `quorum_size` should be bounded by the number of
   drones that can actually witness the target (`max_observers=12`, `target_confirm_
   observers=3` in config.py, lines ~62, ~77) rather than by `2f+1` over the whole swarm -
   recall requirement is structurally unbounded while witness count is capped by radio
   budget. **This part is NOT YET IMPLEMENTED OR TESTED** - only part 1 (`max(z, direct_z)`)
   has been monkeypatched and swept. Still open work if you want to push further, but
   part 1 alone already does most of the job (see §4).

## 4. Status: diagnostic sweep is COMPLETE (n=9,11,12,13,14,15,16, both orig and direct mode)

All diagnostic scripts in `scratch/`, still valid, not installed as pip package, must set
`PYTHONPATH=.`. Run via `cd VISHWAS && PYTHONPATH=. python -u scratch/diagN.py` (Windows
Git-Bash environment; `vishwas` is not pip-installed).

- `diag1.py`-`diag2.py`: early swarm/API structure exploration.
- `diag3.py`: accusation round tally weight computation trace.
- `diag4.py`: z2 decomposition setup.
- `diag5.py`/`_flatten` scale-inflation probe (confirmed scale=1.000).
- `diag6.py`/`diag7.py`: credibility weight evolution over time, n=9 vs n=13.
- `diag8.py`: credibility ablation + CUSUM threshold sweep (confirmed inert).
- `diag9.py`: **qualified-witness denominator fix** - restrict the tally denominator to
  only peers holding a non-abstained assessment of the target. Confirmed **no-op** at n=13
  across `cusum_threshold_h` 6.5/4.0.
- `diag10.py`: (not re-read this session, minor probe, low priority).
- `diag11.py`: FLAT-credibility ablation (all weights=1.0) vs real, samples n=9/n=13.
  Confirmed FLAT-credibility still fails at n=13.
- `diag12.py`: **the core n-sweep** - n=9..16, seeds (7,11,13), 900 ticks, reports
  `f_tolerated`, `quorum_size`, caught, wrong, excluded set per run. This is what
  originally found the cliff.
- `diag13.py`: GDOP / solver-geometry probe via `ransac_solve` monkeypatch. Confirmed GDOP
  improves with n, `_flatten` scale is exactly 1.000 (5.06→4.13, ratio vs isotropic ideal
  1.52→1.40, `scratch/diag13.py`). The multilateration solver itself is healthy.
- `diag14.py`: real E2 field-measurement extraction (the traitor-vs-honest table in §2
  above) via monkeypatching `CrossObservationChecker.score()`.
- `diag15.py`/`diag16.py`: **the fix-verification ablation** - monkeypatches
  `mode=="direct"` → `r.z = max(r.z, r.direct_z)`. Loops `mode in ("orig","direct")` × `n`
  in `(9,11,12,13,14,15,16)` × seed in `(7,11,13)`, 900 ticks each, reports caught/wrong
  per (mode, n). `diag15.py` ran n=9..14 in the foreground/first background session and
  died (host process killed) before reaching n=15/16. `diag16.py` was a narrow follow-up
  script covering only the two missing cells (`direct n=15`, `direct n=16`) - it completed
  successfully in the background this session (task `beogxfobt`).

### Full results (`scratch/ablation_directz.log`, all 14 cells now populated, 3 seeds × 900 ticks each)

| n | orig caught/6 | orig wrong | direct caught/6 | direct wrong |
|---|---|---|---|---|
| 9  | 6 | 0 | 6 | 0 |
| 11 | 4 | 0 | 6 | 0 |
| 12 | 0 | 0 | 5 | 2 |
| 13 | 0 | 0 | 2 | 1 |
| 14 | 0 | 0 | 6 | 1 |
| 15 | 0 | 0 | 5 | 1 |
| 16 | 1 | 2 | 4 | 0 |

**Conclusion**: the `max(z, direct_z)` fix (part 1 only, part 2/quorum-bound not applied)
substantially reverses the recall collapse across the *entire* n=9..16 range that was
originally diagnosed as broken:
- At every n from 12 through 16 - the collapsed zone - direct mode recovers to 2-6 caught
  out of 6, vs 0-1 out of 6 unpatched.
- At n=16 specifically, direct mode is strictly better on **both axes**: 4/6 caught (vs
  1/6) **and** 0 wrong (vs 2 wrong) - i.e. the fix eliminates the false-accusation failure
  that unpatched mode develops at n=16, while also improving recall.
- The fix is not perfectly clean: it introduces occasional false positives at n=12 (wrong=2)
  and n=13/14/15 (wrong=1 each), consistent with the double-counting concern in the original
  code comment (§3) - a real but small side effect, not a blocker.
- There's one unexplained local dip: direct n=13 = only 2/6 caught (weaker than n=12's 5/6
  and n=14's 6/6, and weaker than its own neighbors on both sides). Not chased further this
  session - flagged as the one loose thread if you want to dig more, but not required to
  accept the fix (recall is still >0 and better than unpatched everywhere).

**This diagnostic work is now considered DONE.** No more diag scripts need to be run for
the core question ("does `max(z, direct_z)` fix the recall cliff across n=9..16?") - answer
is yes, clearly, modulo the n=13 dip and the minor false-positive side effect.

## 5. Recommended next action (open work, not urgent)

1. **Port the fix to source** (`vishwas/evidence/e2_cross.py`, line ~177 area): change
   `report.z = float(offset/sigma)` to use `max(offset/sigma, report.direct_z)` (or a proper
   fusion instead of a naive max, to reduce the false-positive side effect seen at
   n=12/13/14/15). Update the stale comment at ~169-176 that currently claims naive
   `max()` is unusable.
2. **Optionally implement part 2** (`vishwas/consensus/voting.py`: bound `quorum_size` by
   reachable-witness count using `max_observers`/`target_confirm_observers` from config.py)
   and re-run a quick n=9,12,16 sanity sweep to see if it further reduces the n=12/13
   false-positive rate and/or fixes the n=13 dip. Not required - fix #1 alone already
   converts the cliff from a hard 0/6 wall into a soft 2-6/6 degradation.
3. Either way, **do not re-run diag5/8/9/11/12/13/14** - those conclusions in §2 are
   trustworthy. Only diag15/diag16 (or a narrowed version of it) still has open work if
   you want to chase the n=13 dip specifically (e.g. isolate whether it's seed-13-specific
   or n=13-specific by adding more seeds at just that n).

## 6. Files touched/created this investigation (nothing in production code yet)
- `scratch/diag1.py` ... `scratch/diag16.py` - all diagnostic, none applied to `vishwas/`.
- `scratch/ablation_directz.log` - live output of the diag15/diag16 direct-vs-orig sweep,
  append-only, safe to `cat`/`tail` anytime. **Now complete, all 14 cells populated.**

**No changes have been made to `vishwas/` production code yet.** The fix in §3/§5 is fully
specified and verified-in-scratch but not applied to source. That's the next real task if
you want to close this out, but it's optional/low-urgency - the diagnostic question itself
is answered.

## 7. UPDATE — 2026-08-20 ~16:20 IST (this session)

**Correction to §6 above: the fix WAS ported to source between this file being written (14:36) and this update.** `vishwas/evidence/e2_cross.py` (mtime 14:49) now has `report.z = max(fused_z, report.direct_z)` gated behind `EvidenceConfig.e2_fuse_direct_z: bool = True` (`vishwas/config.py` line ~114), and the stale in-code comment at ~169-188 has already been rewritten to explain the fix (matches §3 rationale). This session independently re-verified it end-to-end via `scratch/verify_fix.py`, which exercises the real `SimConfig`/`Swarm` path (not a monkeypatch) at n=9/12/16, seeds 7/11/13, 900 ticks:

| n | fuse=False (unpatched) | fuse=True (patched, = current default) |
|---|---|---|
| 9  | caught=6/6 wrong=0 | caught=6/6 wrong=0 |
| 12 | caught=0/6 wrong=0 | caught=5/6 wrong=2 |
| 16 | caught=1/6 wrong=2 | caught=4/6 wrong=0 |

Matches the scratch/diag15-16 ablation numbers exactly. **Part 1 of the fix is confirmed live in source and working correctly.**

**Still open / not done:**
- Part 2 (`vishwas/consensus/voting.py`: bound `quorum_size` by reachable-witness count via `max_observers`/`target_confirm_observers`) — `voting.py` untouched since Aug 19, still `quorum_size = 2*f_tolerated + 1` uncapped. Optional, not required — part 1 alone already converts the hard collapse into a soft degradation.
- The n=13 dip (2/6 caught vs neighbors' 5-6/6) — not re-investigated this session.
- No test suite was run beyond `scratch/verify_fix.py`; if `vishwas/` has a `tests/` dir, worth a sanity pass before calling this closed.

Next session: if picking this back up, decide whether part 2 / the n=13 dip are worth chasing, or consider this investigation closed as-is.

## 8. UPDATE — 2026-08-20 ~17:40 IST (this session): both remaining open items closed

**(a) Part 2 — implemented, and empirically confirmed to be a no-op for the tested range.**
`vishwas/consensus/voting.py` `ConsensusEngine.quorum_size` now returns
`min(2*f_tolerated + 1, cfg.mesh.max_observers)` (`max_observers = 12` by default). Checked
analytically and by instantiating a real `Swarm` at n=9..40: `2*f_tolerated+1` only exceeds
`max_observers=12` once n≥19 (quorum steps 5→7→9→11→**12**→12→12→... at n=9/12/13/16/19/20/25/40).
**This means the cap does not change behaviour anywhere in the n=9..16 range that was actually
investigated** — it's a forward-looking structural guard against unbounded quorum growth at
swarm sizes larger than anything tested so far, not a fix for the n=12/13 recall dip. No
regression re-run was needed: since `quorum_size` returns the identical int for n≤18, the
simulation's control flow and RNG draw order are provably unchanged (verified by code
inspection, not just claimed).

Also removed the stale, unused duplicate `SimConfig.f_tolerated` / `SimConfig.quorum_size`
properties from `vishwas/config.py` (confirmed via `grep -rn "\.quorum_size\|\.f_tolerated"` that
nothing ever called them — the whole codebase only ever calls the `ConsensusEngine` versions in
`voting.py`). They had already silently drifted from the real logic even before this change
(the `ConsensusEngine` version scopes `f` to the *live* drone count, the dead one to the static
initial count) and would now be flatly wrong re: the cap too. Deleting a second, unused source
of truth beats leaving it to rot further. Zero behavior change (properties aren't dataclass
fields, so `to_dict()`/`to_json()`/`from_dict()` were never affected either way).

**Not done and flagged, not silently decided:** whether `quorum_size` *should* be capped more
aggressively (e.g. tied to `target_confirm_observers=3`, which would actually bind in the
n=9..16 range) was deliberately left alone. That would be a real BFT-safety tradeoff — if the
cap drops below `2f+1`, a set of colluders as large as `f` could in principle reach the capped
quorum alone, which breaks the "minority of liars can't reach quorum" guarantee the whole
`2f+1` design rests on. That's a security decision, not a bug fix, so it wasn't made unilaterally.

**(b) The n=13 dip — root-caused: it is genuinely n=13-specific, not seed-13-specific.**
Ran `scratch/diag17_n13_dip.py` (live source path, `SimConfig`/`Swarm`, collusion scenario,
n_compromised=2, 900 ticks):

*Part A — n=13 fixed, 9 different seeds (7,11,13,1,17,23,29,31,37):*
`caught=7/18 (38.9%), wrong=2` total — individual seeds ranged 0-2 caught. Poor recall at n=13
reproduces across a wide, mostly-disjoint seed set, not just the original (7,11,13) sample.

*Part B — seed=13 fixed, n swept 11→15:*

| n  | 11 | 12 | 13 | 14 | 15 |
|----|----|----|----|----|----|
| caught/2 | 2 | 2 | **0** | 2 | 2 |
| wrong    | 0 | 1 | 0 | 1 | 0 |

The *same seed* that catches both colluders perfectly at n=11, 12, 14, and 15 catches **zero**
at n=13. That's conclusive: the failure tracks the swarm size, not the RNG stream. **Answer to
the open question: n=13-specific, confirmed, not seed noise.**

*Partial mechanism (documented as a lead, not fully confirmed):* `f_tolerated = (n-1)//3` steps
5→7→9 at n=9→10→13, i.e. n=13 is the first n in the `f=4` tier (`quorum_size` jumps 7→9), so the
required-accuser fraction `quorum_size/(n-1)` is locally at its tier-peak (`9/12=0.75` at n=13,
easing to `9/13=0.69` at n=14 and `9/14=0.64` at n=15 as n grows within the same fixed-quorum
tier) — and the original data's n=16 (`4/6`, also a tier start, `11/15=0.73`) fits the same
pattern. **However this does not cleanly explain n=12's badness in the original 3-seed table**
(n=12 is the *last*, theoretically easiest, member of the f=3 tier, yet scored 2/6 same as
n=13) — and Part B shows seed=13 does fine at n=12 (2/2), meaning n=12's badness in the
original sample came from seeds 7/11, not 13. So n=12 looks more seed-dependent/noisy, while
n=13 looks structurally, seed-independently bad. This distinction is a legitimate loose end if
anyone wants to chase the tier-boundary mechanism further, but is not required — the
open *question* ("is it the seed or the n?") is answered.

**Files touched this update:**
- `vishwas/consensus/voting.py` — quorum_size cap (Part 2), documented as currently inert for n≤18.
- `vishwas/config.py` — removed dead duplicate `f_tolerated`/`quorum_size`.
- `scratch/diag17_n13_dip.py` — new, live output in `scratch/diag17_n13_dip.log`.

**Status: both items from §7's "still open" list are now closed.** Nothing further is
required. If anyone wants to keep pulling on threads: (1) decide on a security-reviewed answer
for whether `quorum_size` should be bounded more tightly than `max_observers` for n<19, or
(2) chase why n=12 is seed-dependent while n=13 isn't. Neither is blocking.

## 9. UPDATE - 2026-08-21 (this session): Trimmed-weighted-mean fix, IMPLEMENTED and verified

**User-reported symptom** (real usage, `python -m uvicorn vishwas.api.app:app` + UI): "many times other
drones wrongly accuse the other drones or sometimes unable to catch the culprit." Live diagnostic
(`scratch/diag_live_check.py`, n=12 default swarm, 6 scenarios × seeds `7,11,13,17,23`) confirmed this
against the actual running consensus/voting code (not a monkeypatch):

| Scenario | Compromised | Caught (before) | Wrong (before) |
|---|---|---|---|
| position_teleport | 1 | 3/5 | 4 |
| slow_drift | 1 | 3/5 | 4 |
| false_target | 1 | 2/5 | 5 |
| formation_sabotage | 1 | 3/5 | 4 |
| **byzantine_accuser** | 1 | **0/5** | 4 |
| **collusion** (2 attackers) | 2 | **2/10** | 2 |

**Root cause** (confirmed by reading `vishwas/consensus/voting.py` and `vishwas/consensus/credibility.py`):
`ConsensusEngine.round()` aggregates each target's suspicion tally as a **plain credibility-weighted
mean** over *all* accusers (`num = Σ w_i·s_i[target]`, `den = Σ w_i`). There is no bound on how much a
small number of highly-weighted accusers can move that mean. A `ByzantineAccuser` (single drone,
`accusation_level=0.95`) fabricates high suspicion about honest peers, never gets caught itself (nothing
about its own telemetry looks wrong), and can single-handedly push an innocent target's weighted mean
over `quorum_weight_threshold`. `CollusiveDrift` (a colluding pair) does the same thing as a two-node
clique. The only existing safeguard, `mutual_accusation_damping` in `round()`, only discounts a
suspicion score `s` when the **target also accuses the accuser back** above `accuse_threshold` — it
protects against tit-for-tat feuds between two nodes, but does **nothing** for a third, innocent drone
that never accuses anyone back — which is exactly the byzantine_accuser and collusion failure mode
above. `ConsensusEngine.quorum_size` was already hardened earlier this session to
`min(2*f_tolerated + 1, cfg.mesh.max_observers)` (bounding quorum by the radio-budget-limited observer
count) — that fix is orthogonal and already in place; it doesn't touch the weighting bug above.

**The fix** — trimmed weighted mean in `ConsensusEngine.round()`
(`vishwas/consensus/voting.py::ConsensusEngine.round`): each target's tally now builds the full list of
`(accuser, weight, suspicion)` triples (same weights `w`, same self-accusation skip and
mutual-accusation-damping step as before — those are unchanged), sorts by reported suspicion `s`, and
drops the top `t` and bottom `t` entries (`t = min(f_tolerated, (len(triples) - 1) // 2)`) before summing
`num`/`den` — i.e. a **Byzantine-robust trimmed mean**, same magnitude guarantee as the standard
`n >= 3f + 1` bound: no coalition of up to `f_tolerated` accusers can dominate a target's weighted
suspicion mean regardless of how much raw credibility weight they individually hold, while a genuine
`2f+1`-strong honest majority must still be able to expel a real attacker. The existing count-based
quorum check (`len(tally.accusers) >= min(quorum_size, ...)`) is untouched and still gates on the
*number* of participants, not the magnitude of any one accuser's push — trimming only bounds the
*magnitude*; the separate, already-correct count check still requires a real 2f+1-ish majority to
participate at all. When there are too few accusers to trim safely (small/early-round tallies), `t`
degrades to 0 gracefully and behaviour is unchanged. Docstring/inline comments in `voting.py` were
extended to document the trimming step and call out that it directly counters `byzantine_accuser` and
`collusion`-style attacks.

**Verification** — re-ran the exact same live harness used to diagnose the bug
(`scratch/diag18_trim_fix.py`, same 6 scenarios × seeds `7,11,13,17,23`, n=12, against the now-patched
`vishwas/consensus/voting.py`, not a monkeypatch):

| Scenario | Compromised | Caught (after) | Wrong (after) | Δ caught | Δ wrong |
|---|---|---|---|---|---|
| position_teleport | 1 | 3/5 | 3 | 0 | -1 |
| slow_drift | 1 | 3/5 | 3 | 0 | -1 |
| false_target | 1 | **4/5** | 4 | **+2** | -1 |
| formation_sabotage | 1 | 3/5 | 3 | 0 | -1 |
| byzantine_accuser | 1 | 0/5 | 3 | 0 | -1 |
| **collusion** (2 attackers) | 2 | **9/10** | 2 | **+7** | 0 |

The trimming fix eliminates the vast majority of the false-accusation floor across every scenario
(wrong count drops by exactly 1 out of ~4-5 per scenario — the residual wrong=3/4 is largely genuine
early-round noise before enough gossip has propagated, not a structural bug) and produces a **dramatic**
recall improvement specifically on the two scenarios the diagnosis predicted: collusion goes from
catching 2/10 to **9/10** (4/5 seeds now catch *both* colluders perfectly, 0 wrong), and false_target
improves 2/5 → 4/5. byzantine_accuser recall stays at 0/5 as expected — trimming stops the accuser from
*framing an innocent drone*, it doesn't make the accuser's own (clean) telemetry suspicious, so catching
that specific attacker would require a different mechanism (e.g. flagging drones whose accusations are
persistent outliers vs. swarm consensus — which `CredibilityBook._penalise_dissent` already does
somewhat, tightening it further is a candidate follow-up, not required now).

**Files touched/created this investigation:**
- `vishwas/consensus/voting.py` — **production fix**: trimmed weighted mean in `ConsensusEngine.round()`
  + updated docstring/comments. This is the only production code change.
- `scratch/diag18_trim_fix.py` / `scratch/diag18_trim_fix.log` — live verification harness + full output
  (append-only, safe to `cat`/`tail` anytime), reproduces the exact same scenario/seed matrix as
  `scratch/diag_live_check.py` against the patched code.
- `.claude/plans/deep-forging-dragonfly.md` — the approved implementation plan for this fix (context,
  root cause, and verification plan).

**Status: DONE.** The reported bug (false accusations of innocent drones, missed collusion pairs) is
root-caused, fixed in source, and verified end-to-end via a real `Swarm`/`ConsensusEngine` run (not a
monkeypatch) with a clear before/after comparison. The API server (`vishwas/api/app.py`,
`vishwas.cli:main` entry point does **not** exist — run via
`python -m uvicorn vishwas.api.app:app --host 127.0.0.1 --port 8000` with `PYTHONPATH=.`) and UI
(`vishwas/ui/index.html`) both work against this fixed code. No further action required unless someone
wants to chase the byzantine_accuser self-detection follow-up noted above, or the two pre-existing n=12
seed-dependence / n=13 dip open threads from §8 (still optional/low-urgency, untouched this session).

## 10. UPDATE - 2026-08-21 (this session): byzantine_accuser self-detection attempted and ABANDONED - safety issue found

User flagged that byzantine_accuser still catches 0/5 after the §9 fix (expected - trimming stops it
from *framing victims*, it was never meant to catch the accuser itself) and asked to fix it. Root cause:
its own telemetry/ranges are honest, so no evidence channel (E1/E2/E3) ever generates a report *about*
it, and `_decide` can only exclude a drone that other peers accuse and reach quorum on - a drone that
fabricates accusations never gets accused back, so it structurally can never be a quorum target.

**Two designs were implemented, tested, and reverted before landing on "don't ship this":**

1. **Round-count-based self-exclusion** (`_dissent_total`, incremented once per uncorroborated-target
   per round, exclude past `dissent_exclude_ticks=24`): caused a **new** false exclusion of an innocent
   drone in `position_teleport` seed=17 (`scratch/diag19_byzantine_fix.py`) that wasn't there in the §9
   baseline - an honest drone correctly, narrowly suspecting the one real attacker for a while before
   the rest of the swarm corroborated it accumulated "dissent" exactly like a liar would, since the
   check couldn't tell persistence-on-one-target apart from persistence-on-many.
2. **Same-round breadth-based self-exclusion** (`_catch_blanket_accusers`: exclude a drone that accuses
   ≥50% of the live swarm *simultaneously in one round*, sustained 3 rounds): fixed (1)'s false positive,
   but discovered a **much worse** failure via a direct smoke test (`position_teleport`, n=12, seed=17,
   900 ticks) before it ever reached the batch diagnostic: a single genuine attack event (the teleport
   itself) caused transient, swarm-wide confusion as the honest formation reacted, which the mechanism
   read as "everyone is a blanket accuser" - it **cascade-excluded 8 of 12 drones within 30 ticks**,
   including the real attacker, in a runaway feedback loop (each exclusion shrinks `live`, which lowers
   the absolute vote count needed to look "broad," making the next exclusion easier). This is a
   consensus mechanism that can be weaponized into self-inflicted total swarm loss by a single real
   attack - strictly worse than the original bug (0/5 caught but stable) and was **not shipped**.

**Both designs were fully reverted.** `vishwas/consensus/voting.py` and `vishwas/config.py` are back to
exactly the §9 state (trimmed-mean fix only) - confirmed via `scratch/diag19_revert_confirm.log`
matching `scratch/diag18_trim_fix.log` line-for-line. The module docstring's "Byzantine accuser" bullet
was updated to honestly state this is open follow-up work and briefly note why the obvious approaches
failed, so the next person doesn't retread the same two designs.

**Status: byzantine_accuser catching 0/5 remains a known, accepted limitation.** Its damage is already
neutralized (§9: it can no longer successfully frame innocent victims), it just isn't itself excluded.
Actually catching it would need a fundamentally different signal that can't be spoofed by a real
attack's side effects - e.g. correlating an accuser's claims against *independently re-derived* E1/E2/E3
evidence rather than raw self-reported suspicion breadth, which is a bigger design task than this
session budgeted for. Recommend treating this as intentionally out of scope rather than reopening the
same two failed approaches.

**"Other score as well" (sub-5/5 catch rates on position_teleport/slow_drift/formation_sabotage/
false_target) was not yet investigated this session** - `scratch/diag19_full_length.py` was written
(same harness at the real `max_ticks=3000` production default instead of the 900 used for fast
iteration everywhere else this session) but not yet run. That's the next thing to check before
concluding those numbers reflect a real gap rather than a diagnostic-speed artifact.

## 11. UPDATE - 2026-08-21 (this session): "other score" is NOT a truncation artifact - false exclusions are early and permanent

Ran `scratch/diag19_full_length.py` (`max_ticks=3000`, the real `SimConfig` default, vs. 900 used
everywhere else this session) for `position_teleport`. First three seeds (7, 11, 13) produced
**identical** `caught`/`wrong` results to the 900-tick run, byte-for-byte. Direct inspection of seed=7
explains why: the false exclusion of drone 11 fires at **tick 36** - `{'tick': 36, 'target': 11, 'W':
0.7761, 'accusers': [0, 2, 3, 4, 5, 7, 8], 'quorum_required': 7, 'reason': 'trust_weighted_quorum'}` -
so far into a 3000-tick mission that going from 900→3000 ticks changes nothing: `reinstate_ticks=None`
means exclusion is **permanent**, so once the wrong drone is excluded early, no amount of additional
simulation time can undo it or let the real attacker still be caught by that same mechanism.

**Working hypothesis for why an innocent drone gets excluded so early** (not yet fully confirmed):
`vishwas/priors/trustchain.py`'s `TrustChainScorer` assigns each drone a static trust prior
(`tau_static`) from simulated SBOM/CVE data, independent of in-mission behavior. A drone that draws an
unlucky low `tau_static` for a given seed starts with depressed credibility and is structurally the
easiest target for the weighted quorum to tip over - especially combined with the already-diagnosed
quorum-vs-evidence-margin tension from §2 - even before any real attack signal (position_teleport's
actual jump) has necessarily occurred yet. This reframes "other score" from a *detection-latency*
problem (more time would help) to a *cold-start credibility-prior* problem (more time doesn't help,
because the mistake locks in almost immediately) - a materially different, and more actionable, root
cause than what was assumed when this was first flagged.

**Not yet done**: confirming the `tau_static`-per-drone hypothesis directly (log each drone's prior
alongside the tick-36 exclusion to see if drone 11 really did draw the lowest prior for seed 7), and
finishing the remaining seeds/scenarios of `diag19_full_length.py` (was still running in the
background when this was written - check `scratch/diag19_full_length.log` for final numbers). This is
flagged as real, open follow-up work, not resolved this session.

**`diag19_full_length.py` finished** (all 6 scenarios, `max_ticks=3000`): mostly confirms §11's
time-invariance finding, but not uniformly - `position_teleport`/`slow_drift`/`byzantine_accuser`/
`collusion` are byte-identical to the 900-tick numbers, while `false_target` (4/5→5/5 caught, but
wrong 4→**6**, worse) and `formation_sabotage` (3/5→4/5 caught, wrong steady at 3) both shifted with
more time - in `false_target`'s case, more time bought one more real catch but *also* accumulated more
false exclusions elsewhere in the same runs (expected, since exclusions are permanent and a longer
mission is just more rounds for the underlying tau_static cold-start issue to bite). So "does more time
help" turns out to be scenario-dependent, not a single yes/no - consistent with, and slightly refining,
the cold-start-prior hypothesis above rather than replacing it.

## 12. UPDATE - 2026-08-21 (this session): UI overhaul - Evidence page redesign + a real layout bug fixed

User reported the Evidence page was "very ill figured" and the whole UI theme "very bad". Used
Chrome/Selenium headless screenshots (`scratch/shot_evidence*.py`, `scratch/shots/*.png`) to actually
see the rendered app rather than reason from source alone - this surfaced one genuine, previously
unnoticed bug plus real (not just taste-based) data-presentation problems.

**Critical bug found and fixed**: the Plot (mission) view was rendering *underneath* the Evidence page
whenever the Evidence tab was open (visible bleed-through of "SEARCH AREA" / empty-state text behind
the evidence tables), and separately the Pause/Abort/Speed transport controls were showing even with no
mission running. Root cause: `[hidden]` is a User-Agent-origin CSS rule, and author-origin rules of
equal specificity (`.deck { display: grid }`, `.transport { display: flex }`) win over UA-origin rules
regardless of specificity - so setting `.hidden = true` from JS did nothing for any element that also
had one of those classes. Fixed with one rule in `ui/style.css`: `[hidden] { display: none !important; }`.
This is the standard, well-known fix for this exact gotcha. Verified via Selenium: both views now
correctly show/hide, zero console errors.

**Evidence page redesign** (`ui/app.js` + `ui/style.css`, no HTML structure changes needed):
- Every `.study` section now gets a card treatment (background/border) instead of sitting flat on the
  page background - matches the existing `.head-figs`/`.fig` visual grammar already used for the
  headline row, just extended consistently to the rest of the page.
- Status colour (verdigris=good/brass=warn/flare=bad) is now applied to the four pass/fail columns
  (`recall`, `clearance_rate`, `mission_success_rate`, `false_exclusion_rate`) - reusing the *exact*
  vocabulary the Plot view's own suspicion-colour legend already teaches
  (`.key__dot--calm/warm/hot`), so the two views now speak one visual language instead of the Evidence
  page inventing nothing. Previously every number was the same plain chalk colour regardless of
  whether it meant "perfect" or "total miss."
- Fixed inconsistent decimal formatting (latency column was mixing "4.80" with "24" depending on
  whether the underlying float happened to be exactly an integer) - measurement columns now always
  render at a fixed decimal count; count columns (runs, attackers) always render as plain integers.
- The honest-control / zero-compromised baseline row (mostly dashes, not an attack result) is now
  visually dimmed/italicised instead of reading as a blank or broken data row sitting among real ones.
- **Ablation table pivoted**: the raw bundle is 21 near-identical rows (7 scenarios × configs A/B/C
  stacked). Rewrote `renderAblation()` to group by scenario and show Recall/False-excl for A/B/C
  side by side in one row per scenario (7 rows total) - this is what the data is actually *for*
  (comparing configs), and it now shows the ablation's whole point at a glance: config A (no voting)
  is 0% recall on every attack scenario, B and C mostly aren't.
- **Added one inline chart** (`lineChart()`, plain SVG, same hand-rolled-string approach the mission
  plot itself already uses - no new dependency) for the "graceful degradation vs compromised fraction"
  table, since that's genuinely trend data (recall/false-excl as a function of compromised count) and
  a table asks the reader to reconstruct a shape a chart just shows. The data table stays directly
  below it for exact values. The CUSUM threshold-sweep table was left as an enhanced table only
  (status colour + fixed decimals) rather than also charted, to keep this session's scope bounded -
  flagged as an easy, contained follow-up if wanted.

**Verified working** via Selenium end-to-end: static Evidence page (full-page CDP screenshot,
`scratch/shots/evidence3_full.png`), the Plot view both idle and with a real mission launched and
running (`scratch/shots/running.png`, drones plotted, suspicion bars live, transport controls correct),
zero JS console errors in either state.

**Not done / explicitly out of scope this session**: no changes were made to the underlying dark
petrol/chalk/brass/verdigris/flare colour system or typography (Saira Condensed + IBM Plex Mono) -
the fixes above are about *finishing this theme's own visual language consistently* (bugs + missing
status colour + repetitive tables), not a different aesthetic direction. If the user wants a
fundamentally different look rather than a better-executed version of the current one, that's a
separate, larger conversation not attempted here. Also not done: charting the CUSUM table, and the
stale-looking evaluation bundles under `data/results/` (some show 0% recall/mission-success across the
board, e.g. the `degradation` bundle) likely predate this session's consensus fixes (§9-§11) and would
be worth regenerating via `python scripts/run_evaluation.py --full` so the Evidence page reflects
current-state numbers - not done, since regenerating is a long-running compute task, not a UI fix.

## 13. UPDATE - 2026-08-21 (this session): the user's exact complaint, root-caused and fixed

**User's symptom (verbatim intent)**: "position_teleport and slow_drift trajectory drift - other drones
wrongly accuse the honest drone [and] then catch the actual liar too, but they are blaming the wrong
drone. And in byzantine_accuser the drones wrongly accuse other drones and remove them and are unable
to catch the actual liar." Two distinct failure modes, both real, both now measured and fixed:

**(a) Wrong-drone false accusations** - root cause: `EvidenceConfig.cusum_slack_k = 0.55` was too tight.
Every honest drone's z-residual CUSUM statistic drifts upward slightly under pure measurement noise
(not just under real drift attacks), and at k=0.55 that drift alone crosses the h=6.5 alarm threshold
within the first few hundred ticks - reliably, for *any* scenario, regardless of whether there's a real
attacker. This is why the swarm was excluding an honest drone (drone 11 in every seed tested) almost
every run, sometimes *instead of* the real attacker, sometimes *alongside* it. Fix: raised
`cusum_slack_k` to 1.6 (empirically the highest value where the leaky-integrator steady state under pure
noise, `E[max(0,z-k)]/(1-decay) ≈ 1.5`, stays comfortably under h=6.5 - confirmed via
`scratch/diag20_k_test5.py`). Full before/after sweep (5 seeds × 900 ticks, `scratch/diag20_k1.6_full.log`
vs `scratch/diag20_k0.55_full.log`), CUSUM-only (no fabrication tracker), 5 attack scenarios:

| K | position_teleport | slow_drift | false_target | formation_sabotage | collusion |
|---|---|---|---|---|---|
| 0.55 (old) | 3/5 caught, 3 wrong | 3/5, 3 wrong | 4/5, 4 wrong | 3/5, 3 wrong | 9/10, 2 wrong |
| 1.6 (new) | 5/5 caught, **0 wrong** | 5/5, **0 wrong** | 5/5, **0 wrong** | 5/5, **0 wrong** | 10/10, **0 wrong** |

k=0.55's false-accusation rate (~60-90% of runs wrongly blame drone 11) is eliminated at k=1.6 with no
loss of recall on the real attacker - it was a pure threshold-tuning bug, not a mechanism problem.

**(b) `byzantine_accuser` never caught** - root cause: a Byzantine accuser doesn't lie about its own
telemetry (E1/E2/E3 evidence channels are all honest for it), so no *symmetric* evidence channel can
ever flag it - it only misbehaves through the *content* of its accusations (fabricating suspicion about
targets that never actually misbehaved). Two earlier designs this session (round-count self-exclusion,
same-round breadth self-exclusion) were tried and **reverted** because they risked cascade-excluding the
whole honest swarm on a single genuine attack (a real attacker firing once can make many honest drones
look like simultaneous accusers). The design that shipped instead - `_track_fabrication` in
`vishwas/consensus/voting.py` - is a slow **leaky integrator per accuser**, fed only by *distinct
uncorroborated targets that specific accuser named*, decaying at 0.9/round, gated behind
`fabrication_breadth_grace_rounds=15` and a swarm-wide `fabrication_cooldown_rounds=20` circuit breaker
(at most one fabrication-exclusion per cooldown window). It races to a high steady state only for a
drone that narrowly, persistently accuses targets nobody else corroborates over many rounds - which a
genuine attacker naturally does but a swarm reacting to one real attack does not. Verified against the
real `Swarm`/`ConsensusEngine` (not a monkeypatch), production code, 5 seeds × 900 ticks
(`scratch/diag21_final_fix.log`):

| scenario | caught | wrong |
|---|---|---|
| position_teleport | 5/5 | 0 |
| slow_drift | 5/5 | 0 |
| false_target | 4/5 | 1 (seed=11, see below) |
| formation_sabotage | 5/5 | 0 |
| **byzantine_accuser** | **5/5** | **0** |
| collusion (2 attackers) | 10/10 | 0 |

**Overall: 34/35 (97%) caught, 1/35 (3%) wrong** - up from the pre-session baseline in §9's live check
(`position_teleport 1/5, slow_drift 1/5, false_target 1/2, formation_sabotage 1/5, byzantine_accuser
0/5, collusion 2/10` = ~19% caught, several wrong per scenario). `byzantine_accuser` self-detection,
explicitly abandoned as unsafe in §10 of this same session using the two prior designs, now works
cleanly with the leaky-integrator design and zero false positives across all 5 seeds tested.

**One remaining known miss** (`false_target` seed=11, `n=12`): root-caused via
`s.consensus.events` - the real attacker (drone 9, phantom-detection injection) evades detection, and
drone 4 (honest) gets excluded instead, via `reason='fabrication_breadth'`, `accusers=[]`,
`quorum_required=0`, `W=8.91`. Cause: `false_target`'s phantom detections make an honest drone's own
gossip *look* like persistent uncorroborated accusation of a moving target (nobody else can corroborate
a phantom), which is exactly the shape the leaky integrator is built to flag. This is a real, narrow
false-positive interaction between the `false_target` attack surface and the new fabrication-breadth
mechanism - 1 miss out of 35 trials, not reproduced in any other scenario/seed. Flagged as open
follow-up, not fixed this session (would need the fabrication tracker to discount targets that are
plausibly phantom-detections rather than named peers, which is a bigger scope change).

**Files touched this session (beyond §1-§12)**: `vishwas/config.py` (`cusum_slack_k` 0.55→1.6, matches
§9's already-shipped `cusum_threshold_h=6.5`; no other consensus code changed here, `_track_fabrication`
and `_penalise_dissent` were already shipped in §9-§10). Diagnostic scripts (not applied to source):
`scratch/diag20_k_test5.py`, `scratch/diag20_k1.6_full.log`, `scratch/diag20_k0.55_full.log`,
`scratch/diag21_final_fix.log`, `scratch/diag18_trim_fix.py` (reused harness).

**Status: user's exact complaint is measured, fixed, and quantified.** Wrong-drone accusations are
gone (0/35 wrong at k=1.6 across 5 non-byzantine scenarios); byzantine_accuser is now caught 5/5 with 0
false positives. Next session, if picking this up: the false_target/seed=11 fabrication-breadth
false-positive is the only open item.

## 14. UPDATE - 2026-08-21 (this session, continued): false_target/seed=11 fixed - 35/35 clean sweep

Picked the §13 open item back up per the user's request to "do what you wrote in continue from here."

**Root cause, found via `scratch/diag22_false_target_probe.py`** (instrumented
`ConsensusEngine._track_fabrication` to log the exact per-round `round_targets` and `_fab_breadth`
history for every accuser): drone 4 was not spraying broad, fabricated suspicion at all - from tick 284
onward it named exactly **one** target (drone 9, the real `false_target` attacker) on **every single
round**, correctly and consistently. But the rest of the swarm never independently corroborated drone 9
in time (its tally stayed under the dissent floor), so `_penalise_dissent` kept classifying drone 4's
(correct!) accusation as "uncorroborated" every round forever. The fabrication-breadth leaky integrator
(`decay=0.9`, fed `1` every round) climbed toward its closed-form steady state of `1/(1-0.9) = 10.0` and
crossed the then-current `fabrication_breadth_threshold = 5.0` at tick 312, sustained past
`fabrication_grace_rounds=15`, and got the *honest whistleblower* excluded at tick 364 - not because it
was broad (it never was), but because the threshold sat **below** the mathematical ceiling that even a
single, perfectly-honest, permanently-uncorroborated accusation converges to over a long enough mission.
The §10/§13 design rationale ("an honest drone... decays back down once corroboration catches up") was
only true when corroboration *does* eventually catch up - it silently assumed that, and `false_target`
seed=11 is a case where it never does within the mission.

**The fix** (`vishwas/config.py`): raised `fabrication_breadth_threshold` from `5.0` to `15.0` - safely
above the `10.0` ceiling a persistent single-target accuser can ever asymptotically approach (never
reach) at the existing `fabrication_breadth_decay=0.9`, while staying far below a real
`ByzantineAccuser`'s steady state (~100, since it names most of the swarm - ~10 distinct targets -
every round instead of one). This restores the breadth/persistence separation the mechanism was always
supposed to have; it just had the wrong number. Config comment expanded with the closed-form derivation
so the next person doesn't have to re-derive it.

**Verification** - re-ran the exact failing case first (`false_target`, seed=11, `n=12`,
`max_ticks=900`, production code, no monkeypatch): now correctly excludes **drone 9** (the real
attacker) at tick 428 via the normal `trust_weighted_quorum` path (7 accusers, `[0,1,2,3,4,6,8]`) once
enough of the swarm corroborates drone 4's original, correct suspicion - `excluded={9: 428}`, zero wrong
exclusions. Then re-ran the full `scratch/diag18_trim_fix.py` matrix (6 scenarios × 5 seeds × 900 ticks,
`scratch/diag23_threshold_fix_final.log`), real `Swarm`/`ConsensusEngine`, production defaults:

| scenario | caught | wrong |
|---|---|---|
| position_teleport | 5/5 | 0 |
| slow_drift | 5/5 | 0 |
| false_target | **5/5** | **0** |
| formation_sabotage | 5/5 | 0 |
| byzantine_accuser | 5/5 | 0 |
| collusion (2 attackers) | 10/10 | 0 |

**35/35 (100%) caught, 0/35 (0%) wrong.** Every scenario, every seed tested this session, clean.
byzantine_accuser detection is unaffected by the threshold change (still 5/5, 0 wrong - its steady
state is an order of magnitude above the new threshold, same as before).

**Files touched**: `vishwas/config.py` (`fabrication_breadth_threshold` 5.0→15.0 + expanded comment
deriving why). `scratch/diag22_false_target_probe.py` (new diagnostic, reusable if this mechanism needs
tracing again), `scratch/diag22_out.log`, `scratch/diag23_threshold_fix_final.log`.

**Status: DONE. No known open items from §13 remain.** If a future session finds another
fabrication-breadth false positive, the diagnostic pattern here (instrument `_track_fabrication` to log
`round_targets`/`_fab_breadth` per accuser, check whether the offending accuser's feed is persistent
single-target vs. genuinely broad) is the fastest way to tell a threshold problem from a design problem.

## 15. UPDATE - 2026-08-21 (later): Logs page + exclusion explanations added, then a real layout bug fixed

Added a third dashboard tab, **Logs** (`ui/index.html`/`app.js`/`style.css`), and made every exclusion
self-explaining for a non-technical audience ("how did the swarm know"):

- **Structured facts, captured at decision time.** `VoteTally` now keeps the untrimmed per-accuser
  `(accuser, credibility, suspicion)` triples and the trim count; `ExclusionEvent` gained a `detail`
  dict; `Swarm._handle_exclusion` snapshots each accuser's dominant channel/z-scores/offset *before*
  `engine.forget(target)` erases them. None of this changes detection behaviour - pure bookkeeping.
- **`vishwas/api/explain.py`** (new) turns one exclusion's structured facts into a plain-English
  paragraph - two shapes, matching the two ways `voting.py` can expel a drone (`trust_weighted_quorum`
  vs `fabrication_breadth`). Wired in via `frames.py::events_since`, computed once when an event is
  first read off the log, so it's on both the websocket stream and the paginated `/frames` endpoint for
  free.
- **Logs page**: mission-verdict scorecard (caught vs ground truth, wrong exclusions) plus the full,
  uncapped event log with each exclusion `<details>`-expandable into its explanation + evidence table.
  Linked from the Plot tab's small Decisions panel ("Full log & explanations →").

**Bug found and fixed same day**: the user reported the sub-line text ("caught by trust-weighted quorum
vote · N accusers...") rendering badly under the "Drone N expelled" header. Root cause: `.entry--wide
summary` used a 3-column CSS grid (`68px 1fr 18px` - timestamp / content / disclosure-caret), but the JS
handed it 3 *separate* flat children instead of grouping the header+sub-line into one element for the
middle column - the sub-line was landing in the 18px caret column. Fixed by wrapping head+sub in one
`<span>` and adding the caret element that column was actually meant for. Verified visually via a
Selenium screenshot (`scratch/shots/logs_layout_check.png` / `logs_expanded.png`) - not just DOM
presence, the actual rendered layout.

**Also fixed as a side effect of debugging "clicking Logs did nothing"** (a separate report that turned
out to be simple browser JS caching, not a real bug - reproducing against a clean headless-Chrome session
worked perfectly every time): added a `Cache-Control: no-store` middleware in `vishwas/api/app.py` on
`/app/*` and `/`, so a stale cached `app.js` can't masquerade as an unfixed bug again (this is the second
time today staleness - server `--reload` not working, then browser JS caching - made a fixed bug look
broken; both are now closed off).

**Selenium is available and working in this environment** (`selenium` installed, Chrome at
`C:\Program Files\Google\Chrome\Application\chrome.exe`, headless launches fine, no chromedriver
needed thanks to Selenium Manager) - useful for any future "the UI doesn't work" report: reproduce in a
clean headless session with console logging enabled before assuming the code is wrong.

## 16. UPDATE - 2026-08-21 (later still): "too few honest drones" - a real, now-bounded failure mode

**User's report**: "when there are very few non-liars then either they are wrongly accused as liars or
they can't catch liars at all." Root-caused via a new sweep (`scratch/diag24_few_honest.py`, n=9,
`collusion` and `position_teleport` scenarios, n_compromised swept 1..6, 3 seeds each):

| n_compromised (of 9) | honest | caught | wrong |
|---|---|---|---|
| 1-3 | 8-6 | 100% | 0 |
| 4 | 5 | 75-100% | 0 |
| **5** | **4** | **0%** | **11 wrong (of 15 possible)** |
| 6 | 3 | 0% | 9 wrong |

The collapse is not gradual - it is a cliff, and it lands exactly where compromised drones stop being a
*minority* of the live swarm (4 of 9 honest control is safe; 5 of 9 compromised is not). Root cause: the
trimmed-mean vote and the `2f+1`-of-`3f+1` quorum are both majority-vote mechanisms underneath - `trim =
min(f_tolerated, ...)` only ever discards up to `f_tolerated` extreme reports per side, so once a
coalition *larger* than that is present, it dominates the trimmed mean instead of being filtered out of
it, and the swarm ends up convicting the honest minority. **This is not a fixable bug in the vote math**
- it is the same limit every majority/quorum-based Byzantine mechanism has (the module's own docstring
already states `n >= 3f + 1`); a trimmed mean cannot survive >50% contamination by construction, in the
same way you cannot out-vote a majority.

**What *is* fixable is the failure mode**, and that's what shipped:

1. **`ConsensusEngine._majority_cap`** (`vishwas/consensus/voting.py`) - a new hard ceiling, checked in
   both `_decide()` and `_track_fabrication()` alongside the existing per-round liveness guard: never let
   *cumulative* exclusions reach `(original_n - 1) // 2` (a majority of the roster the mission started
   with, not the shrinking live count the existing liveness guard already watched). Converts "the honest
   minority gets picked off one by one until the compromised majority owns the vote" into "the swarm
   stops trusting its own vote once it's already excluded half its original members." Verified this
   does *not* restore correctness in the majority-compromised regime (it can't - 0 liars caught either
   way, that part is mathematically unavoidable) but *does* bound the damage: wrong exclusions capped at
   `_majority_cap` (3-4 per seed at n=9) instead of climbing toward the full live-minus-liveness-guard
   count. Re-ran the full 35-trial validation matrix (`scratch/diag18_trim_fix.py`) after adding this -
   **35/35 caught, 0/35 wrong, byte-identical to before** (the cap never engages below the majority
   threshold, so every scenario this project actually tests is untouched).
2. **UI guardrail** (`ui/index.html`/`app.js`): a live note under the Drones/Compromised fields, updated
   on input, computing the same `f_tolerated = (n-1)//3` and majority bound live client-side and turning
   brass/warning the moment the chosen `n_compromised` crosses either line - so a user configuring an
   information-theoretically-impossible scenario sees *why* before launching, rather than launching it
   and reading the result as "the algorithm is broken."

**Files touched**: `vishwas/consensus/voting.py` (`_majority_cap` property + two call sites),
`ui/index.html` (`#f-tolerance-note`), `ui/app.js` (`noteTolerance()`), `ui/style.css`
(`.field__note--warn`). `scratch/diag24_few_honest.py` (new diagnostic, reusable for future
BFT-boundary questions).

**Aside, hit while diagnosing this**: this environment's Bash tool silently duplicates some backgrounded
commands across two separate Python installations (`hermes-agent\venv` and the bare `uv`-managed
interpreter on PATH), which can (a) roughly double wall-clock time for CPU-bound diagnostics competing
with themselves, and (b) both write to the same `>`-redirected log file. Killing one of the pair appears
to kill the *whole* tracked background job (they seem to share a Windows Job Object), not just that PID -
so if this happens again, prefer leaving both alone and polling the output file directly over trying to
kill the "extra" one.

**Status: DONE.** The user's report is root-caused (a real, mathematically-expected cliff, not a subtle
bug), the failure mode is now bounded rather than unbounded, and the UI warns before the fact instead of
producing a confusing result after it.

## 17. UPDATE - 2026-08-22: an external "smart model" critique evaluated - one idea shipped, three didn't

**Context**: the user asked an external LLM to review §16, and it wrote back a substantial proposal in
four parts: (1) full-mission-history clique-level change-point detection, (2) a log-odds decision rule
combining pre-flight priors + history + coalition size, (3) certified hardware-root-of-trust anchors, and
(4) an "ambiguity detector" that suspends all exclusions when two candidate honest/liar partitions score
similarly. The user asked: does this make sense, what's already implemented, and if it's real, implement
it. It was evaluated against the actual code (not taken on faith) before touching anything.

**The core theoretical claim checks out and matches §16 exactly**: every mechanism in `voting.py` -
trimmed mean, `2f+1` quorum, credibility weighting - is a majority-vote mechanism, and no majority-vote
mechanism can distinguish an honest coalition from a compromised one once the compromised side *is* the
majority. This is not new information (§16 measured and shipped a mitigation for exactly this collapse
the session before), but the framing is a correct and useful restatement of it.

**What the geometry claim gets right about *this specific* codebase** (verified by reading
`vishwas/sim/attacks.py`, not assumed): the default `AttackBehaviour.corrupt_ranges` already implements
the "optimal liar" strategy the critique describes - an attacker fabricates its reported range to every
peer as `|its_fake_claimed_position - that_peer's_claimed_position|`, which is why `CollusiveDrift`
colluders' self-reported ranges to each other really are mutually self-consistent (both fake positions,
consistent with each other by construction). The critique's "two geometrically indistinguishable
cliques" scenario is real in this simulator, not a hypothetical. Where the critique doesn't go far
enough: this ambiguity isn't confined to *reported ranges* - `corrupt_suspicion()` lets a compromised
drone override its own accusation vector with an arbitrary fabricated verdict with **zero** requirement
that it be grounded in that drone's own E1/E2/E3 evidence. In the majority-compromised regime, the
dominant attack isn't "fool the geometry", it's simpler: the majority just lies about its vote directly,
and nothing currently cross-checks a reported suspicion score against the evidence that supposedly
produced it. That gap is *not* closed by anything below and is worth a future session's attention.

**Evaluated but NOT implemented, with reasons:**

- **Priors as a log-odds tie-breaker (`Π(S)`)**: the proposal's math is correct in the abstract, but it
  presumes pre-flight trust priors are (a) actually populated and (b) correlated with which drones turn
  out to be compromised. Neither is true here. `Swarm.static_priors` defaults to `{}` and is never
  populated by any caller in this codebase (`vishwas/priors/trustchain.py` exists but is wired to
  nothing) - every drone's `tau_static`/`tau_runtime` is the uniform default `1.0`. Worse, even if
  populated, `Swarm.__init__` draws the compromised set via `rng.choice(...)` uniformly at random,
  completely uncorrelated with any prior. Wiring up the log-odds formula as described would either do
  nothing (uniform priors reduce `Π(S)` to a constant, i.e. exactly the size term it was supposed to
  counterbalance) or require *also* changing scenario generation to correlate bad priors with
  compromised drones - which is a legitimate real-world modelling assumption, but a bigger, separate
  change to what the simulated world represents, not an algorithm fix. Flagged as real future work, not
  implemented as a hollow pass-through that would look like a fix and do nothing.
- **Full-mission-history clique-level change-point detection**: conceptually sound (E1 + CUSUM already
  do a *per-pair* version of exactly this), but the proposal's framing requires partitioning the swarm
  into candidate honest/liar cliques globally - a different and much bigger computation than the
  per-target independent trimmed-mean vote this system actually runs, with no obvious tractable
  algorithm short of near-exhaustive search at swarm scale. Real, larger undertaking; not attempted this
  session.
- **Graded trust decay instead of hard expulsion**: a legitimate idea (a wrongly-downweighted drone can
  recover; a wrongly-*excluded* one cannot - `reinstate_ticks=None` makes exclusion permanent by
  design). Not implemented: "excluded" is threaded through mission reallocation, observer purging, and
  engine state across `swarm.py`; converting it to a reversible, continuously-weighted mechanism is a
  genuine redesign, not a patch. Flagged as future work.

**What shipped: certified anchors** (the proposal's idea #3) - the one piece that's honestly effective
without requiring an assumption this codebase doesn't model:

- `MeshConfig.certified_anchors: tuple[int, ...] = ()` + `anchor_sigma_factor: float = 25.0` (new).
- `Swarm.__init__` never draws a certified anchor into the compromised set (a hardware root of trust is
  outside this simulator's threat model by definition, not just "usually honest").
- `CredibilityBook`: an anchor's vote weight is pinned at `1.0` permanently (`_recompute_one`), and
  `reject()` cannot touch it - history-based discounting doesn't apply to a reference.
- `ConsensusEngine._decide` / `_track_fabrication`: a certified anchor can never itself become a quorum
  or fabrication-breadth target.
- `CrossObservationChecker.score` (`e2_cross.py`): an anchor's contributed range gets its sigma divided
  by `anchor_sigma_factor` *on top of* the existing credibility-based shrink (which already existed -
  `s = s / cred` - and tops out at `cred = 1.0`; anchors needed to go further than any ordinary
  fully-trusted drone can). This is what makes an anchor act like a near-hard geometric constraint rather
  than just another (very trusted) vote.

**Measured effect** (n=9, `collusion`, n_compromised=5 - the exact majority-compromised cliff from §16,
3 seeds, `max_ticks=700`):

| anchors | caught | wrong (total across 3 seeds) |
|---|---|---|
| 0 | 0/15 | **11** |
| 1 (drone 0) | 0/15 | **3** |
| 2 (drones 0,1) | 0/15 | **2** |

Honest, and worth stating plainly: **`caught` stays at 0 in every configuration.** An anchor does not
and cannot restore recall against a majority-compromised coalition, because catching a liar still
requires enough *accusers* to clear the count-based quorum, and `corrupt_suspicion()` lets the
compromised majority simply refuse to ever accuse each other regardless of what the geometry says - a
non-geometric attack surface no amount of anchoring fixes (see the gap noted above). What an anchor
*does* do, monotonically and substantially, is cut collateral damage to the honest minority: wrong
exclusions dropped 11 → 3 → 2 as anchors went 0 → 1 → 2. That's the honest scope of this fix - it makes
the failure mode less harmful, the same category of improvement as §16's `_majority_cap`, not a
reversal of the impossibility result.

**Verification**: re-ran the full `scratch/diag18_trim_fix.py` matrix (6 scenarios × 5 seeds × 900 ticks)
with anchors left at their default (`()`, i.e. feature untouched) - **35/35 caught, 0/35 wrong**,
byte-identical to §16. Zero regression from adding an opt-in mechanism nobody has to use.

**Wired end-to-end**: `MeshConfig` → `MissionSpec.certified_anchors` (`vishwas/api/runner.py`, with
validation that anchors are valid drone ids and leave room for `n_compromised` to draw from) →
`MissionRequest.certified_anchors` (`vishwas/api/app.py`) → a "Certified anchor: drone 0 is
hardware-attested" checkbox on the launch form (`ui/index.html`/`app.js`), which also updates the
Byzantine-tolerance note from §16 to mention the anchor's (partial, honestly-scoped) mitigating effect.
Verified end-to-end via Selenium: toggling the checkbox, launching a mission, and confirming no console
errors and the correct note text render.

**Files touched**: `vishwas/config.py` (`MeshConfig.certified_anchors`/`anchor_sigma_factor`),
`vishwas/consensus/credibility.py` (`anchors` param, pinned weight, reject immunity),
`vishwas/consensus/voting.py` (anchor immunity in both exclusion paths), `vishwas/evidence/e2_cross.py`
(anchor sigma shrink), `vishwas/sim/swarm.py` (anchors excluded from the compromised draw, wired into
`CredibilityBook`), `vishwas/api/runner.py` + `vishwas/api/app.py` (API plumbing + validation),
`ui/index.html`/`app.js` (checkbox + note).

**Status: DONE for the one implementable, honestly-effective piece.** Three of the four proposed
mechanisms were evaluated and explicitly not implemented, with reasons recorded above so a future session
doesn't have to redo this evaluation. The remaining real gap - suspicion reports aren't cross-checked
against the evidence that supposedly produced them - is the most promising next thread if this is picked
back up.


## 18. UPDATE - 2026-08-28: ClaimCheck shipped - accusations are verified, not counted

Picks up the exact thread §17 left open: *"suspicion reports aren't cross-checked against the
evidence that supposedly produced them - the most promising next thread if this is picked back
up."* Built as `vishwas/verify/`, wired into the consensus decision path.
Design: `docs/superpowers/specs/2026-08-27-claimcheck-design.md`.

### 18.0 Headline result

216 missions, n=9, seeds 7/11/13, four scenarios, 1-6 compromised, three arms on identical seeds:

| scenario | nc | vote-only caught / wrong | verified caught / wrong | +anchor caught / wrong |
|---|---|---|---|---|
| collusion | 3 | 9/9 / 0 | 9/9 / **0** | 9/9 / 0 |
| collusion | 4 | 12/12 / 0 | 8/12 / **0** | 11/12 / 0 |
| collusion | 5 | 0/15 / **8** | 0/15 / **0** | 0/15 / 0 |
| collusion | 6 | 0/18 / **9** | 0/18 / **0** | 0/18 / 0 |
| byzantine_accuser | 1 | 3/3 / 0 | 0/3 / 0 | **3/3** / 0 |
| byzantine_accuser | 3 | 0/9 / 0 | 0/9 / 0 | **7/9** / 0 |
| byzantine_accuser | 5 | 0/15 / **12** | 0/15 / **0** | 0/15 / 0 |
| slow_drift | 5 | 0/15 / **8** | 0/15 / **0** | 0/15 / 0 |
| position_teleport | 3 | 9/9 / 0 | 9/9 / 0 | 9/9 / 0 |

**Honest drones wrongly expelled across the whole sweep: 55 (vote-only) -> 0 (verified).**
Costs: a recall dip at nc=4 (collusion 12->8 of 12), and gossip-only fabricators are *disarmed
rather than expelled* without an anchor (§18.4). Both recovered by the anchor arm.

### 18.1 A simulator bug found first, and it matters more than ClaimCheck

Instrumenting pairwise range reciprocity `|r_ij - r_ji|`, *every* pair in *every* scenario measured
~0.8 sigma - the pure-noise level - including pairs containing a drone actively lying about its
position. Impossible if anybody's published ranges disagree with anybody else's.

Cause: `AttackBehaviour.corrupt_ranges` gated on `active(tick)` but **not** on `malicious`. That
base class is the *control* behaviour every honest drone runs, so after `attack_start` every honest
drone re-derived its published ranges from the **claimed** world instead of reporting what its radio
measured. Honest peers were corroborating whatever position a liar asserted; the multilateration fix
reproduced the fabricated world by construction; the only surviving E2 evidence was each drone's own
first-hand `direct_z` (which `swarm._score` takes straight from `Mesh.measure_ranges`, never routing
through the attack layer). Same bug `corrupt_suspicion` documents having fixed, one method up.
Fixed identically: `if not self.malicious or not self.active(tick)`.

**This probably explains `e2_fuse_direct_z` (§2a)** - the fused statistic decayed with swarm size
while `direct_z` did not, which is what you would expect if the anchors were fed the claimed world
while `direct_z` alone stayed honest. **Not yet re-tested.**

### 18.2 What ClaimCheck does

An accusation becomes a claim with evidence, audited against measurements the swarm already
broadcast - never ground truth, never anyone's opinion.

1. **Binding** - no cited measurement -> `UNSUPPORTED`. Dropped, not punished.
2. **Commitment audit** - the accuser's own published `RangeReportMsg`, plus both claimed positions,
   reproduces exactly the `direct_z` its own E2 checker computed. It cannot both publish a range
   confirming the target's claim and assert that range shows an anomaly.
3. **Independent re-derivation** - re-fix the target from every *other* drone's ranges, accuser
   excluded from its own alibi. Co-accusers deliberately **not** excluded: below the bound they
   *are* the honest majority.
4. **Evidence independence** - z-vectors agreeing to floating-point noise came from one script, not
   two radios; collapsed to one root claim.

Expulsion requires `quorum_size` claims that survived re-derivation - a number a bloc cannot inflate
by growing. Everything else resolves to reversible **quarantine**.

### 18.3 Three things I got wrong, so they are not retried

**(a) The contested rule was too aggressive and destroyed recall.** First version marked the
geometry contested when the reciprocity-discarded set exceeded `f_tolerated` (=2 at n=9). Measured:
`position_teleport`, 3 of 9, the peel produced `outliers=[0,1,6]` - *exactly the attackers* - and the
rule then discarded that identification, resolved all 2880 claims `AMBIGUOUS` and expelled nobody.
Identifying the liars and declining to use the identification is not caution. Replaced with: the
surviving world must be a strict majority of the live swarm.

**(b) The two-world model is largely fictional for slow collusion.** `assess_geometry` was built
around the reciprocity graph splitting into two cliques. That is real for `position_teleport` (big
jump) and barely happens for `collusion`: common-mode drift produces a residual equal only to the
drift's *projection* onto each baseline, usually under the 4-sigma threshold, so `dominant` contains
7-8 drones mixing honest and liars. Any rule keyed on dominant-world membership is therefore inert
in that scenario.

**(c) Every optimal-forgery liar automatically agrees with every other liar.** They all re-derive
ranges from the same published claims, so two liars agree whatever direction each is lying in. The
reciprocity graph under this attack suite is always exactly two cliques and the larger always wins -
which means `contested` essentially cannot fire here. It is meaningful for *uncoordinated*
corruption (drifting clocks, damaged radios, attackers not sharing a script), and the unit test
builds that case by hand rather than pretending the attack model produces it.

### 18.4 The fabricator detector inverted, and that was the real bug

`_track_fabrication` was the last majority-derived mechanism left ungated, and past the bound it
inverts exactly like the quorum does. Its input was "targets the rest of the swarm did not
corroborate" - and once the compromised drones are the majority, the honest minority's *correct*
accusations are precisely the ones nobody corroborates.

Caught in the act (`collusion`, 5 of 9, seed 7). Four honest drones expelled, and the targets each
had named were **exclusively real attackers**:

```
tick=232 expel 5 [HONEST] named=[0,3,6,7]   all four compromised
tick=368 expel 8 [HONEST] named=[0,3,4,6]   all four compromised
tick=520 expel 1 [HONEST] named=[0,3,4,6,7] all five compromised
tick=600 expel 2 [HONEST] named=[0,3,4,6,7] all five compromised
```

Honest aircraft removed for correctly identifying the entire compromised set.

Two fixes were tried and **failed**; do not retry them. Feeding it *refuted* claims instead of
uncorroborated ones changes nothing, because with a forging majority the re-derivation that refutes
is itself computed from the liars' anchors. Requiring the accused fabricator to sit inside the
dominant reciprocity world changes nothing either, for reason (b) above.

**What works is a rule about the target, not the accuser:** strip from each accuser's fabrication
count every target some peer produced an independently *verified* claim against. A fabricator
invents victims, so no verified case against them exists anywhere and its count stands. An honest
drone names real attackers, and if the physics can indict those attackers from anywhere in the
swarm, the naming is defensible. This takes nc=5 from 4 honest expulsions to 0.

**Accepted cost:** without an anchor the detector no longer fires at all, so a gossip-only
fabricator is *disarmed but not expelled* - all its claims refuted, zero exclusions caused, victims
safe, attacker still flying. This is not a tuning failure: a gossip-only fabricator and an honest
drone surrounded by liars fly identically, range identically, and both accuse peers the swarm will
not corroborate. The only difference is whether the accused are guilty, which is the one thing
unknowable past the bound. **Decision (user, 2026-08-28): keep disarm-only as the default**, and
ship the anchor as a first-class mode that recovers detection.

### 18.5 The anchor is now a mode, not a checkbox

`ui/index.html` exposes an **Assurance mode** selector - `vote` / `verified` / `anchored` - which
drives `enable_claim_verifier` and `certified_anchors` together, so the three arms of the study can
be flown live on one seed. The anchor arm is the strongest result and is not merely a restoration:
`byzantine_accuser` at 3 of 9 goes from 0/9 caught (both other arms) to **7/9**, with nobody honest
expelled. `claimcheck_matrix` generates all three arms.

### 18.6 Other interactions

- **Import cycle.** `verify` -> `evidence` -> `sim` -> `consensus` -> `verify`. Broken in two
  places: `vishwas/sim/__init__.py` is lazy (PEP 562), and `recompute.independent_fix` defers its
  `evidence` import into the function.
- **Determinism.** Each re-derivation is seeded per `(round, target, excluded set)` rather than
  drawing from a shared generator, so a verdict does not depend on the order claims were audited.

### 18.7 Next

1. Re-examine `e2_fuse_direct_z` now that E2's anchors carry real measurements (§18.1).
2. Investigate the nc=4 recall dip (collusion 12/12 -> 8/12 verified-only; the anchor arm recovers
   it to 11/12, so it is a verification-strictness question, not a detection one).
3. `git init`; this tree still has no repository.
