# VISHWAS

**Verified Integrity & Swarm Health Weighted Assurance System**

A Byzantine-fault-tolerant integrity layer for autonomous drone swarms. Drones score each other with
physical evidence, and a verifier called **ClaimCheck** audits every accusation against measurements the
swarm already broadcast. With it, the swarm can find and remove compromised drones even when they
outnumber the honest ones, and it never expels an honest aircraft.

> Indian Army Terrier Cyber Quest 2026 · Track: AI Kavach (Drone / Cyber Defence)

---

## The problem

Weighted-quorum consensus breaks at the Byzantine bound (`n ≥ 3f + 1`). Once colluding drones hold a
majority, they can out-vote the truth and expel honest drones for being right. Tuning thresholds does not
fix this: any decision that counts votes belongs to whoever has the most votes.

## The idea

**An accusation is a claim, and claims can be checked.** ClaimCheck doesn't count accusers. It re-derives
each accusation from the ranging data the swarm has already published:

1. **Claim-to-evidence binding.** An accusation must cite real measurements.
2. **Commitment audit.** An accuser's own published range reports are public commitments it can't contradict later.
3. **Independent re-derivation.** The target's position is re-fixed by multilateration from every *other* drone, and the accuser is left out of its own alibi.
4. **Evidence independence.** Bit-identical "corroboration" is one script broadcast twice, so it collapses to a single claim.

Each accusation gets one of four verdicts:

| Verdict | Action |
|---|---|
| `SUPPORTED` | Counts toward expulsion |
| `REFUTED` | Dropped, and the accuser loses credibility |
| `UNSUPPORTED` | Dropped, no penalty |
| `AMBIGUOUS` | Reversible quarantine, never expulsion |

## Results

538 missions · 9 drones · 5 seeds · 1–7 compromised · three modes flown on identical seeds.

| Mode | Attackers caught | Honest drones wrongly removed |
|---|---|---|
| Vote only (baseline) | 145 / 450 · 0.322 | **92** |
| Verified (no hardware) | 130 / 450 · 0.289 | **0** |
| **Verified + hardware anchor** | **224 / 450 · 0.498** | **0** |

The full write-up, including limitations and where the system degrades, is in
[`docs/REPORT.md`](docs/REPORT.md).

## Architecture

```
Evidence layer      E1 self-consistency · E2 cross-observation · E3 mission-logic
      │                    produces scores, never decisions
      ▼
ClaimCheck          binding · commitment audit · independent re-derivation
(vishwas/verify/)      · evidence independence  →  4 verdicts
      │
      ▼
Safety engine       SUPPORTED → may expel   REFUTED → accuser penalised
                    UNSUPPORTED → dropped   AMBIGUOUS → reversible quarantine
      │
      ▼
Consensus           trimmed mean + 2f+1 quorum, over VERIFIED claims only
```

| Path | Contents |
|---|---|
| `vishwas/sim/` | Swarm kinematics, mesh radio, missions, and the attack scenarios |
| `vishwas/evidence/` | E1/E2/E3 evidence channels, CUSUM change detection, multilateration |
| `vishwas/verify/` | ClaimCheck: claims, re-derivation, independence, verdicts, safety, ledger |
| `vishwas/consensus/` | Credibility-weighted voting and quorum |
| `vishwas/priors/` | Outer trust priors and the adversarial guard |
| `vishwas/evaluation/` | Experiment harness, study matrices, report bundles |
| `vishwas/api/` | FastAPI server: missions, live WebSocket stream, results |
| `ui/` | Zero-build dashboard (plain HTML/JS/CSS) served by the API |
| `scripts/run_evaluation.py` | Regenerates every result bundle in `data/results/` |
| `experiments/` | Diagnostic scripts and logs cited from code comments |
| `docs/` | Report, original plan, ClaimCheck design, full engineering log |

## Quick start

Requires Python ≥ 3.10.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
```

### Run the dashboard

```bash
vishwas serve                      # http://127.0.0.1:8000
```

Choose an attack scenario, set the drone count and the number compromised, then launch. The plot shows
suspicion building, accusations being verified or refuted, and expulsions as they happen. The Evidence
tab loads the committed results from `data/results/`.

Interactive API docs are served at `http://127.0.0.1:8000/api/docs`. On Windows, `run_server.bat` stops
any old server on port 8000 and starts a new one with auto-reload.

### Attack scenarios

`position_teleport` · `slow_drift` · `false_target` · `formation_sabotage` · `byzantine_accuser` ·
`spoofed_anchor` · `collusion`, plus the honest control `none`.

### Tests

```bash
pytest -m 'not slow'               # fast unit tests, < 1 s
pytest                             # adds full-mission integration tests (~8 min)
```

### Reproduce the results

```bash
python scripts/run_evaluation.py --quick     # smoke depth, ~10–15 min
python scripts/run_evaluation.py             # default depth (the numbers above)
python scripts/run_evaluation.py --full      # paper depth
```

Bundles are written to `data/results/` (one JSON file per study, plus `index.json` and `results.md`).

## Scope

Everything here is simulated in software. No hardware was built or flown. The ranging model, the
adversary model, and what is deliberately not attacked are covered in sections 10 to 13 of
[`docs/REPORT.md`](docs/REPORT.md).
