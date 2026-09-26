# VISHWAS — Current Working Session (continuity document)

> **Purpose:** hand-off doc. If a session ends mid-work, another AI or another session
> can pick up from here without re-deriving anything. Updated 2026-08-28.
>
> **Read these three files in order to get current:**
> 1. this file (state, what's in flight, what to do next)
> 2. `CONTINUE_FROM_HERE.md` §18 (the engineering log — every wrong turn recorded)
> 3. `docs/superpowers/specs/2026-08-27-claimcheck-design.md` (the design)
>
> `REPORT.md` is the hackathon-facing writeup (also published as an artifact).

---

## 0. Environment — how to run anything

```bash
cd /home/shushant/Projects/VISHWAS
PYTHONPATH=. .venv/bin/python -m pytest tests/ -q            # 38 tests (29 fast + 9 slow)
PYTHONPATH=. .venv/bin/python -m pytest tests/ -q -m "not slow"   # fast only, <1s
PYTHONPATH=. .venv/bin/python scripts/run_evaluation.py      # full regen, ~2.2 h
PYTHONPATH=. .venv/bin/python scripts/run_evaluation.py --quick   # ~17 min smoke
```

- **`PYTHONPATH=.` is required** for ad-hoc scripts (pytest picks it up from `pyproject.toml`).
- venv at `.venv`, numpy 2.5.2, Python 3.14. No system deps installed.
- **Multiprocessing:** Python 3.14's default `forkserver` start method **fails** in this
  environment (`ConnectionResetError`). Ad-hoc parallel scripts must call
  `multiprocessing.set_start_method("fork", force=True)` before using `run_many`.
  `scripts/run_evaluation.py` works as-is.
- **Still no git repository.** `git init` remains undone.
- Dashboard: `PYTHONPATH=. .venv/bin/python -m uvicorn vishwas.api.app:app` then open `ui/index.html`.

---

## 1. What this project is

Byzantine-fault-tolerant drone-swarm integrity simulator. Terrier Cyber Quest 2026,
AI Kavach track. The original problem the user brought:

> when >50% of drones are compromised, they out-vote the honest ones and expel *innocent*
> drones. Stop a majority coalition from wrongly expelling honest drones.

**Solved.** `vishwas/verify/` (ClaimCheck) audits each accusation against independently
re-derived physics instead of counting votes. Honest drones wrongly expelled: **90 → 0**
over 538 missions.

---

## 2. Current state — DONE and verified

### Shipped and measured

- **`vishwas/verify/`** — 8 modules, ~1200 lines. Claim/commitment audit, independent
  re-derivation, evidence-independence collapse, 4 verdicts, reversible quarantine, ledger.
- **Wired into** `consensus/voting.py` (audit before aggregating, ClaimCheck gate in
  `_decide`), `sim/swarm.py` (publishes `ClaimBundle`, drains blocks to event log),
  `config.py` (`ClaimCheckConfig`, `SimConfig.enable_claim_verifier`),
  `api/{runner,frames,explain,app}.py`, `evaluation/{harness,study,report}.py`,
  `scripts/run_evaluation.py`, `ui/{index.html,app.js,style.css}`.
- **Assurance mode selector** in the dashboard: `vote` / `verified` / `anchored`,
  driving `enable_claim_verifier` + `certified_anchors` together.
- **38 tests**, all passing. `tests/test_majority_attack.py` is the slow integration guard.
- **`data/results/*.json` regenerated** 2026-08-28 (538 runs, 0 failed) against the
  `corrupt_ranges` bugfix.

### Two bugs found in the *existing* system (both fixed)

1. **`sim/attacks.py::corrupt_ranges` gated on `active(tick)` but not `malicious`.**
   The base class is the control behaviour every honest drone runs, so after
   `attack_start` every honest drone published claim-derived ranges instead of measured
   ones — silently disarming E2. Found by measuring reciprocity `|r_ij − r_ji|` at the
   pure-noise level for *every* pair including liars.
   **Open follow-up:** this probably explains why `e2_fuse_direct_z` was needed (§2a of
   CONTINUE_FROM_HERE). **Not yet re-tested.**

2. **`_track_fabrication` inverted past the bound** — it expelled honest drones for
   correctly naming the entire compromised set (measured: 4 honest drones,
   `collusion` 5-of-9 seed 7, each naming *only* real attackers).

---

## 3. Session of 2026-08-28/29 — scale work and the anchor hunt

Goal: *"make anchor actually detect and catch the compromised, not just save the innocent,
while never removing an innocent drone"* — plus a reviewer challenge that n=9 is not a scale
claim.

### Changes that SURVIVED validation (all in `consensus/voting.py` unless noted)

1. **Verified-evidence path** — `_verified_quorum()` + a second route into the grace window
   that bypasses both vote gates (accuser count, weighted mean). A verified claim is trusted
   for surviving independent re-derivation, not for who signed it. Relaxes to
   `anchor_backed_quorum = 2` **only when a trusted anchor is among the supporting accusers**
   — do not remove that gate, see §4.
2. **Anchor-authoritative defensibility, unioned** — `defensible` = targets the anchor accuses
   ∪ targets with > `f_tolerated` supported claims. Both halves are load-bearing; each alone
   regressed.
3. **Fabricator ceiling** — `_track_fabrication` stops once cumulative exclusions reach
   `f_tolerated`. Saved 14 honest drones at n=25 (92 → 78 even in vote-only).
4. **Conditional observer-relative quorum** — cap the requirement by how many peers could
   physically observe the target, **only when that ceiling binds** (`observers <= quorum_size`).
5. **Decaying sustain counter** — `_sustain[target]` decrements instead of resetting to 0.
   The single biggest scale win: `collusion` 10-of-25 went **1/20 → 17/20 caught**.
6. **Spoofed-anchor scenario** (`sim/attacks.py`) + anchor demotion
   (`verify/verifier.py::_check_anchors`) + `_trusted_anchors()`.

### THE LAST BUG — `anchor_sigma_factor` was 25.0 and should be 8.0

Found by the 5-seed regeneration after a 2-seed gate had passed. **`vishwas/config.py`.**

The anchor must dominate any multilateration it joins - that is its purpose, and 8 is already
far past what credibility can reach (capped at 1.0). But a single range circle does not
determine a 2D position, so shrinking one observer's sigma far enough to override every other
constraint makes the fit *ill-conditioned* rather than merely anchored: pinned to one circle
and free along it. At 25.0 that produced a spurious offset on an **honest** drone large enough
that five honest peers each independently verified a claim against it, and the swarm expelled
one of its own (n=9, `collusion`, 2 compromised, seed 7 — the only false expulsion in 360
missions).

Three-point sweep, 1800 ticks, seeds 7/11: at **25.0** `nc=2` loses a drone; at **8.0** and
**4.0** it does not; and `nc=5` (8/10) and `nc=6` (8/12) are **identical at all three**. The
extra dominance was buying nothing and costing an aircraft.

**Two validation-method lessons from this one, both worth more than the fix:**

- **My gate had a hole.** `verify3.py` swept `nc in (1,3,5,6,7)` — it never tested 2 or 4, and
  the failure lived at 2. A "32 of 32 clean" result was reported over a grid that skipped the
  case. The grid is now `nc=1..7` across seeds 7/11/13 (45 rows) and **must stay that way**.
- **Two seeds is not enough to certify a zero.** The 5-seed study found what the 2-seed gate
  could not. Gate with 3 seeds minimum; trust only the full study for the headline.

### FINAL VALIDATED NUMBERS (build of 2026-08-29)

**n = 9 — widened gate: 45 configurations (`nc=1..7` x seeds 7/11/13), `wrong=0` on every row.**

**n = 9 study — 360 missions, 5 seeds (`data/results/claimcheck.json`):**

| Mode | Recall | Honest removed |
|---|---|---|
| vote_only | 145/450 · 0.322 | **92** |
| verified | 130/450 · 0.289 | **0** |
| **anchored** | **224/450 · 0.498** | **0** |

Both verified modes are back to **zero**. Anchored: collusion 5-of-9 **20/25**, 6-of-9
**18/30**; byzantine_accuser 2-of-9 **10/10**, 3-of-9 **10/15**; spoofed_anchor all clean.
Main suite: recall 0.75, false-exclusion rate **0.0**, latency 19.4 s, 29.2 ms/drone/tick.

**n = 25 — 48 missions, 2 seeds:**

| Mode | Recall | Honest removed (of 224) |
|---|---|---|
| vote_only | 0.307 | 78 |
| verified | 0.148 | 5 |
| **anchored** | **0.653** | **1** |

**24/32 caught at 16-of-25 compromised (64% hostile), 0 honest lost in that cell.**
Uncontended cost 30-80 ms/drone/tick (the 69-170 figures in `scale25e.log` were measured
alongside the regeneration and are inflated).

### REVERTED — the user's accuser-reputation idea

Their proposal: repeated refuted accusations should cost an accuser its standing. Implemented
as decaying strikes + muting (not expulsion) from the verified quorum. **Reverted after
measurement** — do not re-implement in this form:

- `byzantine_accuser` 5-of-9: muted the whole compromised set **plus one honest drone**.
- `collusion` 3-of-9: muted **five honest drones and zero liars**, well inside the Byzantine
  bound where everything else is perfect.

**Why it cannot work as a count:** refuted claims are *normal*. An honest drone with imperfect
geometry makes claims that are correctly refuted all the time, so the metric measures "how
often were you wrong", and honest drones are wrong at a high baseline. Meanwhile `collusion`
attackers do not fabricate gossip at all, so they generate *fewer* refuted claims than honest
drones — the metric points the wrong way for the attack it was meant to catch. Two guards were
tried (anchor-backed refutations only; then also skipping targets the anchor itself accuses)
and neither fixed it.

**What might work, if revisited:** a *relative* measure — is this accuser's refutation **rate**
far above the swarm median? Honest drones cluster at a shared baseline set by sensor noise; a
fabricator sits in the tail. Needs its own design pass and validation cycle.

**Keep their framing though:** the consequence should be *muting, not expulsion*. That choice
is why this failed experiment cost nothing — five honest drones lost witness standing and all
five kept flying, `wrong=[]`. With expulsion as the consequence it would have destroyed the
headline result.

## 4. Rules learned the hard way — do not re-try these

Recorded because each cost a measurement cycle. Full detail in `CONTINUE_FROM_HERE.md` §18.3–18.4.

1. **Contested-geometry rule keyed on `outliers > f_tolerated`** — destroyed recall. At
   `position_teleport` 3-of-9 the peel produced `outliers=[0,1,6]` (exactly the attackers)
   and the rule then discarded that identification and expelled nobody. Now: the surviving
   world must be a strict majority of the live swarm.
2. **Feeding `_track_fabrication` refuted claims instead of uncorroborated ones** — inert.
   With a forging majority the re-derivation that refutes is itself computed from the liars'
   anchors.
3. **Requiring the accused fabricator to sit in the dominant reciprocity world** — inert.
   Common-mode drift barely perturbs reciprocity (the residual is only the drift's
   *projection* onto each baseline), so that world contains nearly everyone.
4. **The two-clique model is largely fictional for `collusion`.** Real for
   `position_teleport`. Also: every optimal-forgery liar automatically agrees with every
   *other* liar (they all re-derive from the same published claims), so the graph is always
   two cliques and the larger always wins — `contested` essentially cannot fire under this
   attack suite.
5. **The recurring bug class: demanding evidence from drones that structurally cannot
   provide it.** This has now appeared four times wearing different clothes, and it is worth
   checking any new rule against it before writing the code:

   | Instance | The impossible demand | Fix |
   |---|---|---|
   | `2f+1` verified claims past the majority | Only honest drones can produce a verified claim, and there are fewer than `2f+1` left | Anchor-gated relaxed quorum |
   | Weighted-mean gate | A bloc that stays *silent* about its accomplices dilutes the mean below threshold | Verified-evidence path bypasses it |
   | Contested-geometry rule keyed on `f_tolerated` | Three cleanly-identified liars tripped it and discarded the identification | Strict-majority-of-live rule |
   | Quorum vs radio budget at n=25 | Only ~12 of 25 peers ever range a target, and *which* 12 churns — 12 simultaneous agreeing observers is unsatisfiable | Quorum = majority of peers that actually had geometry, floored at `f+1` |

   The tell is always the same: a threshold expressed against the *roster* rather than
   against the set of drones physically able to answer. When adding any new requirement, ask
   who can satisfy it and whether that set shrinks under attack.

6. **The fabricator detector has inverted FOUR times**, each time because it was reading
   a signal a compromised bloc can manufacture: uncorroborated accusations, then refuted
   claims, then anchor-supported defensibility, then anchor defensibility again at a scale
   where the anchor cannot see most of the swarm. Every fix that held routes it through
   something the attacker does not control. It now also carries a hard ceiling — it may not
   expel more than `f_tolerated` drones total, because past that it is operating outside its
   own premise. **This costs `byzantine_accuser` recall** (3/10 -> 2/10 at 5-of-9 anchored)
   and that trade was made deliberately: honest aircraft are worth more than detection on
   one scenario that ClaimCheck already disarms.
7. **Never report numbers from the fast unit tests alone.** They test audit rules on
   synthetic bundles and cannot see integration failures. Run
   `pytest -m slow` **and** a scenario sweep before claiming any result. (This produced a
   wrong claim to the user once already.)

---

## 5. Honest limitations to keep in the writeup

1. Past the majority, detection stops without an anchor — by design. The failure mode
   changes from "expels the honest minority" to "declines to expel anyone, and records why".
2. A gossip-only fabricator is **disarmed, not expelled** without an anchor (all claims
   refuted, zero exclusions caused, but it keeps flying). Not a tuning failure: it and an
   honest drone surrounded by liars are observationally identical. **User decision
   2026-08-28: keep disarm-only as the default.**
3. "5 honest vs 4 liars" and "4 honest vs 5 liars" are the same measurements with labels
   swapped — an information-theoretic wall, which is *why* an outside reference is the answer.
4. The anchor gate in `_verified_quorum` is **inferred from measured runs, not proven**.
   An attacker engineered to bait the anchor into supporting a false claim is untested.
5. A previously-reported "recall cost at 4-of-9" (12/12 → 8/12) **was an artifact of a
   700-tick sweep**; at full mission length it is 20/20 → 20/20. Corrected in REPORT.md.

---

## 5a. Mass GNSS spoofing — a real, documented gap

Found while sweeping the new `spoofed_anchor` scenario past its threat model.
**Measured boundary (n=9, anchored, 2 seeds, 900 ticks):** `nc=1` 2/2 caught 0 wrong ·
`nc=3` 4/6 0 wrong · `nc=5` 0/10 **0 wrong** (stops catching, harms nobody) ·
`nc=6` 0/12 **3 of 3 honest expelled**, all via `trust_weighted_quorum`.
The safe-failure property therefore survives past the majority and breaks only at 6 of 9.
The cap of 3 in `scenario_caps` is conservative — 4 and 5 are also clean and could be
included if a future run wants the extra rows. `claimcheck_matrix` therefore caps `spoofed_anchor` at `n_compromised = 3`
(`scenario_caps` in `study.py`) — past that it is no longer that scenario, it is mass GNSS
spoofing, a different threat that gets named separately rather than misattributed.

Three defences were worked through and all fail the same way, so **do not spend time
re-deriving them**:

- **Reciprocity** is blind: spoofed drones report *honest ranges*, so `|r_ij − r_ji|` is
  clean everywhere and `assess_geometry` sees one consistent world.
- **Claim-vs-range consistency** (do claimed positions reproduce measured distances?) splits
  into two cliques — all spoofed drones share one GNSS offset, so spoofed↔spoofed pairs stay
  consistent. Larger clique wins.
- **Per-drone self-consistency** inverts: at 6-of-9 a spoofed drone agrees with 5
  accomplices and disagrees with 3 honest peers (62%), an honest drone disagrees with all 6
  spoofers (25%) — honest scores *worse*.

The anchor cannot break the tie because it is itself spoofed; `_check_anchors` correctly
demotes it, which leaves no external reference at all. **Closing this needs a second
independent reference** (ground station, surveyed landmark, terrain-relative or celestial
navigation), not a better estimator. Documented as limitation #4 in `REPORT.md`.

## 6. Next actions, in order

1. Confirm the final validation is clean (`/tmp/.../scratchpad/verify3.py`, prints `DONE`).
   **If any row shows `<<< WRONG`, revert change (3) first** — it is the loosest threshold.
2. Re-run `scripts/run_evaluation.py` (~2.2 h, 538 missions) into `data/results/`.
3. Update `REPORT.md` §5 and the artifact with the regenerated numbers
   (`https://claude.ai/code/artifact/78b879bd-fbf5-4c27-95f3-cb9dbcb554a4` — republish the
   same file path to keep the URL).
4. Add `spoofed_anchor` to the dashboard scenario dropdown copy if it needs a friendlier
   label than the auto-generated one.
5. Re-examine `e2_fuse_direct_z` now E2's anchors carry real measurements (§2 above).
6. `git init` and commit.

**Invariant for every change: `wrong == 0` in every configuration. If a change costs a
single honest drone, revert it — the zero is worth more than the extra detection.**
