# Project VISHWAS
### Verified Integrity & Swarm Health Weighted Assurance System
**Track:** AI Kavach — Drone / Cyber Defence
**Event:** Indian Army Terrier Cyber Quest 2026

> *Vishwas* — trust. The entire project is about one question: **can a drone swarm trust itself?**

---

## 0. The One-Line Contribution

> **Trust-weighted Byzantine consensus for autonomous drone swarms — enabling a swarm to detect and expel a compromised member using physical-plausibility evidence, with no ground station, no central authority, and no cryptographic assumption that the drone itself is honest.**

If a judge asks "what is the novel technical claim," that sentence is the answer. Everything else in this document supports it.

---

## 1. Problem Statement

### 1.1 The gap

Current drone security work concentrates on three places:

| Layer | What's protected | Existing work |
|---|---|---|
| Code | Firmware vulnerabilities, exploitable parsers | Static analysis, fuzzing, automated patching |
| Link | Command channel confidentiality/authenticity | Encryption, MAVLink signing, anti-jam routing |
| Sensor | GPS/RF spoofing of a *single* drone | RAIM, signal-strength heuristics, INS cross-check |

**None of these protect a swarm from one of its own members.**

Consider a swarm of N drones executing a coordinated task — area search, formation flight, multi-angle target triangulation. One drone is compromised: firmware implant, captured and re-flashed, or an insider-supplied unit. Now:

- Its messages are **cryptographically valid** — the compromised drone holds legitimate keys. Signature verification passes. Encryption passes. The link layer sees nothing wrong.
- Its telemetry is **plausible** — the attacker isn't stupid. Reported positions obey rough physics, timestamps are consistent, message formats are correct.
- Its data is **false** — and the rest of the swarm consumes it as ground truth for formation control, collision avoidance, and target localization.

**Cryptography answers "who sent this?" It cannot answer "is this true?"** When the sender's own hardware is the adversary, every authentication mechanism in the stack returns green.

### 1.2 Why this can't be solved by calling home

The obvious answer is "ground control notices and intervenes." In the scenarios that matter, ground control is exactly what's unavailable:

- Contested airspace with active EW — the C2 uplink is the *first* thing jammed
- Beyond-line-of-sight operations in high-altitude terrain with no relay
- Deliberate EMCON (emissions control) during a covert task

The swarm must therefore be able to **police itself, autonomously, in real time, with no external arbiter.** That is a distributed consensus problem, and it is unsolved for physical multi-agent systems.

### 1.3 Why this matters now, for the Indian Army specifically

Swarm drone programs are an active, publicly acknowledged Indian defence priority — DRDO swarm initiatives and Army swarm inductions are already in motion. Every swarm fielded is a swarm with this vulnerability. The defence built today for a 10-drone swarm is the defence needed for a 100-drone swarm tomorrow, and the mathematics of Byzantine tolerance means the problem gets *harder*, not easier, with scale.

---

## 2. Threat Model

Precise threat modelling is what separates a serious security submission from a demo. State this explicitly in your pitch.

**Adversary capability (what we assume the attacker CAN do):**
- Fully compromise up to `f` drones out of `n` in the swarm
- Make compromised drones emit arbitrary telemetry: falsified position, velocity, attitude, sensor detections
- Compromised drones hold valid cryptographic credentials — all messages authenticate correctly
- Compromised drones may **collude** — two or more corroborating each other's false reports
- Compromised drones may **falsely accuse** honest drones, attempting to get honest members expelled
- The C2 link to the ground station is unavailable (jammed or under EMCON)

**Adversary limitation (what we assume the attacker CANNOT do):**
- Cannot control what *other* drones physically measure about it (relative range, bearing, radio propagation characteristics)
- Cannot violate physics — a real airframe still has a real flight envelope, and it is physically somewhere
- Cannot compromise more than `f` drones, where `n ≥ 3f + 1`

**The core insight:** the attacker controls what a drone *says*, but not what the world *is*, and not what peers *observe*. VISHWAS builds its entire evidence base on that gap.

---

## 3. System Architecture

VISHWAS is one system with three trust layers, feeding a single consensus decision.

```
┌─────────────────────────────────────────────────────────────┐
│  PRE-FLIGHT (once, on the ground)                           │
│                                                             │
│  ┌──────────────────────────┐                               │
│  │  TrustChain-SBOM         │  Firmware dependency graph    │
│  │  Supply-chain scoring    │  → CVE exposure               │
│  └──────────┬───────────────┘  → provenance scoring         │
│             │                                               │
│             │  static trust prior  τ_static[j] ∈ [0,1]      │
└─────────────┼───────────────────────────────────────────────┘
              │
┌─────────────┼───────────────────────────────────────────────┐
│  IN-FLIGHT (continuous, per drone)                          │
│             │                                               │
│  ┌──────────▼───────────────┐                               │
│  │  AdvGuard-lite           │  Vision model confidence      │
│  │  Perception integrity    │  entropy monitoring           │
│  └──────────┬───────────────┘  → adversarial-manipulation   │
│             │                     flag                      │
│             │  runtime trust signal  τ_runtime[j] ∈ [0,1]   │
│             │                                               │
│  ┌──────────▼───────────────────────────────────────────┐   │
│  │  ★ SENTINEL-SWARM CORE  ★                            │   │
│  │                                                      │   │
│  │  Evidence Engine        →  Consensus Engine          │   │
│  │  ─────────────────         ────────────────          │   │
│  │  E1 self-consistency       suspicion gossip          │   │
│  │  E2 cross-observation      trust-weighted quorum     │   │
│  │  E3 mission-consistency    exclusion decision        │   │
│  │       ↓                          ↓                   │   │
│  │  CUSUM divergence          task reallocation         │   │
│  │  score  s_i[j]             + RTH command             │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**The starred block is the novel contribution.** The two outer modules are deliberately lightweight — they produce a scalar trust prior each, and their job is to make the consensus engine *smarter*, not to be separate projects.

---

## 4. Detailed Methodology

### 4.1 Layer 1 — TrustChain-SBOM (pre-flight, static)

**Purpose:** establish which drones in the swarm were *more likely to be compromisable* before they ever took off.

**Steps:**
1. Ingest the firmware build tree / binary for each airframe variant in the swarm
2. Generate a Software Bill of Materials using existing open tooling (Syft or equivalent), extracting the full third-party dependency graph
3. Cross-reference every component against the NVD/CVE database (Grype or direct NVD API) — count and severity-weight known vulnerabilities
4. Provenance scoring per component: maintainer activity, last audit date, licence class, unverified/unknown-origin flag
5. Aggregate to a single **static trust prior** `τ_static[j] ∈ [0,1]` per airframe, plus a human-readable procurement-style report

**Output consumed by the core:** one scalar per drone. That's it. Deliberately narrow scope.

**Honest scoping note:** this module is built on existing open-source scanners. Your contribution here is the *scoring function and its use as a consensus prior*, not the scanning itself. Say so in the pitch — judges respect accurate attribution far more than inflated claims.

### 4.2 Layer 2 — AdvGuard-lite (in-flight, runtime)

**Purpose:** detect that a drone's perception stack is being manipulated, which is both a threat in itself and a strong signal that the drone's reports are unreliable.

**Background:** onboard object detectors are provably vulnerable to adversarial patches — an optimised printed pattern can cause a detector to miss a vehicle entirely. This is reproducible, published research, not speculation.

**Steps:**
1. **Offline robustness scoring:** generate adversarial patches against the swarm's detection model using an Expectation-over-Transformation patch attack (optimising a patch to survive varied angle, scale, and lighting). Measure detection-accuracy degradation → per-model robustness score.
2. **Runtime integrity flag:** monitor the statistical distribution of detection confidences during flight. Adversarial manipulation produces characteristic signatures — anomalous confidence entropy, unstable class assignment across consecutive frames on a static object, detection dropout inconsistent with scene continuity. Flag deviation from the drone's own established baseline.
3. Emit **runtime trust signal** `τ_runtime[j] ∈ [0,1]`

**Honest scoping note:** the full version of this idea includes adversarial *hardening* (retraining the model on generated patches). In hackathon scope you build detection and scoring; list hardening as future scope. Do not claim the hardened pipeline unless you actually build it.

### 4.3 Layer 3 — Sentinel-Swarm Core (the flagship)

#### 4.3.1 Evidence Engine

Every drone `i` independently scores every peer `j` across three orthogonal evidence channels. Orthogonality matters: an attacker who defeats one channel is still caught by the others.

**E1 — Self-consistency (cheap, catches unsophisticated spoofs)**

Does drone `j`'s reported trajectory obey its own declared flight envelope? Check reported position/velocity/attitude sequences against:
- Maximum acceleration and jerk limits for the airframe class
- Maximum turn rate and climb rate
- Energy plausibility: does the claimed manoeuvre profile match the reported battery drain?

A teleporting or physically impossible trajectory fails immediately.

**E2 — Cross-observation consistency (the strong signal)**

This is the channel the attacker cannot control. Each drone derives an *independent estimate* of where its peers actually are:
- **Range estimation** from mesh radio signal characteristics (RSSI/time-of-flight based distance estimate)
- **Relative bearing** from onboard sensing where available
- **Multilateration:** with three or more observers, peer-derived range estimates geometrically constrain drone `j`'s true position regardless of what `j` claims

Compare claimed position against the peer-derived position estimate. A drone lying about its position produces a growing, *observable* residual — and crucially, it produces that residual in the measurements of *every* peer simultaneously, which is exactly what the consensus layer needs.

**E3 — Mission-logic consistency**

Does the drone's behaviour match its assigned role? Formation slot occupancy, assigned search-sector coverage, waypoint adherence. Deviation is weak evidence alone but valuable in combination.

**Temporal filtering — a critical design choice**

Do **not** threshold instantaneous divergence. Sensor noise, wind gusts, and legitimate manoeuvres all produce transient spikes; naive thresholding produces a high false-accusation rate, which is catastrophic (see §6.2).

Instead, apply **sequential change detection (CUSUM)** to the divergence signal per peer. CUSUM accumulates evidence over time and detects a persistent shift in the mean, distinguishing a genuine sustained falsification from noise. This is also what makes the system catch the hardest attack class — **slow trajectory drift**, where the attacker moves the reported position away from truth by a small amount per timestep, staying below any instantaneous threshold. CUSUM accumulates that drift; a naive detector never fires.

Output: suspicion score `s_i[j]` for every peer, updated continuously.

#### 4.3.2 Consensus Engine

Detection alone is insufficient — a single drone's opinion is not trustworthy, because that drone may itself be the compromised one. The decision to expel must be collective.

**Protocol:**

1. **Accusation gossip:** at fixed intervals, each drone broadcasts its suspicion vector over the mesh
2. **Trust-weighted aggregation:** drone `j` accumulates accusation weight
   ```
   W[j] = Σ  ω_i · s_i[j]
          i≠j
   ```
   where `ω_i` is the *accuser's* credibility weight — not all opinions count equally
3. **Accuser credibility** is itself derived from the trust priors:
   ```
   ω_i = g( τ_static[i], τ_runtime[i], accusation_history[i] )
   ```
   A drone with poor supply-chain posture, an active perception-integrity flag, or a history of accusations that the swarm later rejected has its voice discounted. **This is where the three layers actually compose** — the outer modules aren't decoration, they determine whose vote counts.
4. **Exclusion decision:** drone `j` is expelled when `W[j]` exceeds a quorum threshold requiring agreement from at least `2f + 1` weighted peers, under the standard Byzantine bound `n ≥ 3f + 1`
5. **Post-exclusion response:**
   - `j`'s telemetry is dropped from all formation control and target-triangulation inputs
   - Search sectors / formation slots are reallocated among the remaining honest drones
   - `j` is issued a return-to-home command
   - The exclusion event is logged for post-mission forensic review

**Defending the consensus itself**

The consensus mechanism is a target. Two specific attacks must be handled, and handling them explicitly is a strong signal of technical maturity:

- **Byzantine accuser:** a compromised drone accuses honest peers to get them expelled. Countered by the `2f+1` quorum requirement (a minority of liars cannot reach quorum) combined with credibility weighting, and by decaying the accusation weight of any node that is itself under suspicion.
- **Collusion:** two or more compromised drones corroborate each other's false reports, mutually vouching. Countered because E2 evidence comes from *every* peer independently — colluders can suppress their own accusations of each other, but they cannot prevent the honest majority's independent range measurements from disagreeing with both of them. Demonstrating survival under collusion is your strongest single experimental result.

**Safety and liveness properties to state:**
- **Safety:** no honest drone is expelled, with high probability, for `f < n/3`
- **Liveness:** a persistently lying drone is expelled within bounded time from attack onset

---

## 5. Technology Stack

**Everything is software. No hardware dependency at any stage.**

| Component | Tooling |
|---|---|
| Fast simulation (metrics) | Custom Python: NumPy kinematics, NetworkX mesh topology, SimPy discrete-event scheduling |
| High-fidelity simulation (demo) | PX4 SITL multi-vehicle + Gazebo, 4–6 drones |
| Consensus implementation | PBFT-inspired protocol, custom implementation adapted for kinematic evidence |
| Change detection | CUSUM / EWMA sequential detectors (SciPy) |
| SBOM + vulnerability scanning | Syft, Grype, NVD API |
| Adversarial ML | PyTorch, YOLOv8-class detector, Adversarial Robustness Toolbox for EoT patch generation |
| Mesh network modelling | Simplified propagation + latency model in the custom sim; optionally ns-3 |
| Dashboard / demo UI | Streamlit or a lightweight web front-end for live swarm-state visualisation |

**Why two simulators — and say this in the pitch, it reads as engineering maturity:**

The custom Python simulator runs a full swarm scenario in well under a second, which lets you execute **thousands of Monte Carlo trials** for statistically meaningful metrics and ablation studies. PX4 SITL is far too slow for that, but it produces a *visually convincing, physically realistic* demo. You use each for what it's good at: statistics from the fast sim, spectacle from the fidelity sim.

**Equipment:** development laptop with a GPU (needed only for the AdvGuard adversarial-patch generation step). Nothing else.

---

## 6. Evaluation Plan

This section is where you can decisively out-perform a competing submission. Ideas are cheap; **measured results are not.** Every claim below should appear in your final report as a number or a plot.

### 6.1 Attack scenarios to implement

| # | Scenario | What it tests |
|---|---|---|
| 1 | Position teleport | Baseline sanity — E1 should catch instantly |
| 2 | **Slow trajectory drift** | The hard case — only CUSUM catches it |
| 3 | False target injection | Corrupts multi-drone triangulation |
| 4 | Formation sabotage | Induced collision risk |
| 5 | **Byzantine accuser** | Robustness of the consensus mechanism itself |
| 6 | **Colluding pair** | Hardest case — two drones corroborating |

Scenarios 2, 5, and 6 are the ones that distinguish a serious system from a toy. Prioritise them.

### 6.2 Metrics

1. **Detection latency** — time from attack onset to exclusion, per scenario
2. **False exclusion rate** — fraction of honest drones wrongly expelled. **This must be near zero.** An over-eager system that expels honest drones is worse than no system at all, because it hands the attacker a denial-of-service vector. State this trade-off explicitly; it shows you understand the real operational cost.
3. **Mission success rate**, with vs. without the consensus layer, across all six scenarios
4. **Graceful degradation curve** — mission success as a function of compromised fraction `f/n`, showing behaviour up to and beyond the `n ≥ 3f+1` bound
5. **Computational footprint** — per-drone CPU/memory cost of running the evidence and consensus engines, demonstrating it's light enough for a companion computer

### 6.3 The ablation study — your single most important experiment

Run every scenario in three configurations:

| Configuration | Purpose |
|---|---|
| **A.** No consensus layer (baseline) | Shows the swarm fails without VISHWAS |
| **B.** Consensus with uniform trust weights | Shows the consensus core works alone |
| **C.** Consensus with SBOM + AdvGuard trust priors | Shows the integration *earns its place* |

**Why this matters more than anything else in the project:** if C measurably beats B — faster detection, lower false-exclusion rate, tolerance of a higher compromised fraction — then you have *empirically proven* that combining the three layers produces something none of them achieve alone. That converts your project from "three ideas in one submission" into "an integrated system with a demonstrated synergistic effect."

If C does **not** beat B, report that honestly and explain why. A negative result reported with integrity impresses good judges far more than a claim they suspect is inflated. It also gives you an obvious, credible future-work section.

---

## 7. Novelty

**Positioned honestly — built on existing work, not claiming to replace it.**

| Existing field | What it provides | What it does not address |
|---|---|---|
| Byzantine Fault Tolerance (PBFT and successors) | Consensus under malicious nodes in abstract distributed systems | Evidence is *message disagreement*; assumes no physical ground truth to appeal to |
| Swarm resilience research | Routing around jamming, comms redundancy | Assumes swarm members are honest; protects the *channel*, not the *content* |
| Single-UAV spoofing detection | INS/GPS cross-checks on one airframe | No multi-agent dimension; cannot use peer observation |
| Adversarial ML robustness | Attacks and defences for vision models | Model-level only; no link to system-level trust decisions |
| Software supply-chain security | SBOM, CVE scanning, provenance | Static, pre-deployment; no runtime consequence |

**Our contribution:**

1. **Physical plausibility as the evidence base for Byzantine consensus.** Classical BFT votes on message agreement. VISHWAS votes on *kinematic and observational consistency* — grounding a distributed-systems protocol in physics that the attacker cannot forge. This reframing is the core intellectual claim.
2. **Trust-weighted quorum using cross-layer security posture.** Accuser credibility derived from supply-chain and perception-integrity signals, so the consensus mechanism knows *in advance* which members are more likely to be lying.
3. **Demonstrated end-to-end on a defence swarm scenario**, with quantified detection latency, false-exclusion rate, and collusion tolerance.

**USP for the pitch:** *A drone swarm that can identify and expel a traitor in its own ranks — with no ground station, no central authority, and without trusting a single word the suspect says about itself.*

> **Citations — do this properly.** Locate and cite the actual foundational papers before submission: Castro & Liskov (1999) for PBFT; Brown et al. / Athalye et al. for adversarial patch attacks under EoT; current literature on UAV swarm security and multilateration-based position verification. **Do not invent citation identifiers.** A fabricated reference discovered by a technically literate judge destroys your credibility instantly and irrecoverably — verify every single one.

---

## 8. Build Roadmap

Phased so that you have a working, demonstrable system at the end of every phase. If you run out of time, you stop at a phase boundary with something that works — never mid-integration with nothing to show.

**Phase 0 — Ideation submission (immediate)**
- This document, condensed to the required submission format
- Flow diagram, threat model, novelty positioning

**Phase 1 — Simulation foundation**
- Custom Python swarm simulator: N-drone kinematics, mesh messaging with realistic latency, mission task (area search or formation flight)
- Attack injection framework — the ability to designate any drone as compromised with a chosen attack behaviour
- *Exit criterion:* you can run a clean mission and a compromised mission and see the difference in mission success

**Phase 2 — Evidence Engine**
- E1 self-consistency checks
- E2 cross-observation / multilateration residuals
- E3 mission-logic deviation
- CUSUM temporal filtering
- *Exit criterion:* a single drone correctly identifies a lying peer in scenarios 1 and 2

**Phase 3 — Consensus Engine**
- Suspicion gossip protocol
- Weighted quorum voting, `2f+1` threshold
- Exclusion + task reallocation
- Byzantine-accuser and collusion handling
- *Exit criterion:* the swarm collectively expels a liar in scenarios 1–6 and never expels an honest drone

**Phase 4 — Trust priors (the outer layers)**
- TrustChain-SBOM: scanner integration + scoring function → `τ_static`
- AdvGuard-lite: offline patch-attack robustness scoring + runtime confidence-entropy flag → `τ_runtime`
- Wire both into accuser credibility weighting
- *Exit criterion:* ablation study configuration C runs

**Phase 5 — Evaluation + demo**
- Monte Carlo runs across all scenarios; generate all plots
- The three-way ablation
- PX4 SITL multi-vehicle visual demo, 4–6 drones
- Live dashboard, demo video, final report

**Critical sequencing note:** Phase 4 is deliberately *late*. If time runs short, you cut the outer layers and still have a complete, defensible flagship system — you present Sentinel-Swarm alone with the trust-prior integration as designed-and-specified future scope. You never want to be in a position where all three modules are half-built.

---

## 9. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| PX4 SITL multi-vehicle setup consumes days | Custom Python sim is the primary deliverable and is built first; SITL is a Phase 5 nice-to-have for visual polish only |
| False exclusion rate too high to be credible | CUSUM tuning plus a conservative quorum threshold; report the full precision/latency trade-off curve rather than a single cherry-picked operating point |
| Cross-observation (E2) is unrealistically clean in simulation | Inject realistic noise into peer range estimates; run a sensitivity analysis showing at what noise level detection degrades. Reporting the *limit* of your system is a strength, not a weakness |
| Scope creep across three modules | Hard phase gates; Phase 4 explicitly cuttable |
| Judges perceive overlap with other swarm/UAV submissions | Lead every conversation with the threat model, not the solution — "cryptography tells you who sent it, not whether it's true" is a line nobody else will have |

---

## 10. Deliverables

**Prototype**
- End-to-end working system: attack injection → evidence engine → trust-weighted consensus → exclusion → mission recovery

**Software**
- Swarm simulator with attack injection framework
- Evidence engine (E1/E2/E3 + CUSUM)
- Consensus engine (gossip, weighted quorum, exclusion, reallocation)
- TrustChain-SBOM scorer
- AdvGuard-lite robustness scorer and runtime flag

**Results**
- Detection latency, false-exclusion rate, and mission-success figures across all six attack scenarios
- Graceful degradation curve vs. compromised fraction
- The three-configuration ablation study
- Per-drone computational footprint measurement

**Demonstration**
- Live dashboard showing suspicion scores rising, the vote occurring, and the traitor being expelled in real time
- PX4 SITL visual demo, 4–6 drones
- Short demo video and full technical documentation

---

## 11. How to Pitch This

**Open with the threat model, not the solution.** Ninety seconds, in this order:

1. *"Every drone security system in the field today answers the question: is this message authentic? None of them answer: is this message true."*
2. *"When the compromised device is a swarm member holding valid keys, every authentication check in the stack returns green — and the swarm consumes its lies as ground truth."*
3. *"In contested airspace, the C2 link is the first thing jammed. So the swarm has to work this out for itself, with nobody to appeal to."*
4. *"VISHWAS makes the swarm vote — on evidence the attacker cannot forge, because it comes from what everyone else physically measures."*
5. Then the demo: suspicion scores climbing, the vote, the expulsion, the mission continuing.

**When asked what's genuinely new:** physical plausibility as the evidence base for Byzantine consensus, plus cross-layer security posture determining whose vote counts.

**When asked about limitations — and you will be asked — answer straight.** Name the `n ≥ 3f+1` bound. Name your measured false-exclusion rate. Name the noise level at which E2 degrades. Candour about limits is read by experienced judges as command of the material; evasion is read as not knowing.
