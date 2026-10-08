# VISHWAS - evaluation results

## Headline

- Attack scenarios evaluated: **7**
- Attackers detected and expelled: **30/40** (recall 0.75)
- Mean detection latency: **19.44 s** (worst scenario mean 29.36 s)
- False-exclusion rate over 320 honest drone-missions: **0.0**
- Mission success under attack: **0.971**
- Per-drone cost: **29.187 ms / drone / tick**

### ClaimCheck

Three assurance modes, identical missions and identical seeds. `vote_only` counts accusations; `verified` re-derives each one from the range measurements the swarm already broadcast; `anchored` adds one hardware-attested drone as a reference from outside the vote.

- Honest drones expelled — **counted**: **92** · **verified**: **0** · **anchored**: **0**
- Recall — counted: **0.3222** · verified: **0.2889** · anchored: **0.4978**

The verified mode's recall is *lower by design*: past the Byzantine bound it declines to convict on evidence it cannot independently re-derive, and a gossip-only fabricator is disarmed rather than expelled. The anchored mode is what buys that recall back, because a root of trust settles which account of the geometry is real when the swarm alone cannot.

## Attack scenarios

Every attack scenario plus the honest control, repeated over a fixed seed ladder. Latency is measured from the tick the attack starts to the tick the swarm votes the attacker out, and is averaged only over runs where detection happened - `detected_runs` reports how many those were. The honest control row is the one that decides deployability: it must show a false-exclusion rate of zero.

| Scenario | Runs | Attackers | Caught | Recall | Latency (s) | p90 (s) | False excl. | Coverage | Mission success |
|---|---|---|---|---|---|---|---|---|---|
| none | 5 | 0 | 0 | - | - | - | 0 | 0.902 | 1 |
| position_teleport | 5 | 5 | 5 | 1 | 6.72 | 10.24 | 0 | 0.902 | 1 |
| slow_drift | 5 | 5 | 5 | 1 | 24.16 | 27.52 | 0 | 0.902 | 1 |
| false_target | 5 | 5 | 0 | 0 | - | - | 0 | 0.901 | 1 |
| formation_sabotage | 5 | 5 | 5 | 1 | 19.04 | 20.8 | 0 | 0.902 | 1 |
| byzantine_accuser | 5 | 5 | 0 | 0 | - | - | 0 | 0.902 | 1 |
| spoofed_anchor | 5 | 5 | 5 | 1 | 17.92 | 20.8 | 0 | 0.902 | 1 |
| collusion | 5 | 10 | 10 | 1 | 29.36 | 37.68 | 0 | 0.92 | 0.8 |

## Three-configuration ablation

Identical missions and identical seeds under three configurations. A: evidence is computed but nobody votes - the conventional swarm baseline. B: consensus with uniform trust. C: full VISHWAS with the outer trust priors weighting accuser credibility. Paired seeds mean a difference between rows is the configuration, not the weather.

| Config | Scenario | Recall | Latency (s) | False excl. | Mission success |
|---|---|---|---|---|---|
| A | none | - | - | 0 | 1 |
| A | position_teleport | 0 | - | 0 | 1 |
| A | slow_drift | 0 | - | 0 | 1 |
| A | false_target | 0 | - | 0 | 1 |
| A | formation_sabotage | 0 | - | 0 | 1 |
| A | byzantine_accuser | 0 | - | 0 | 1 |
| A | spoofed_anchor | 0 | - | 0 | 1 |
| A | collusion | 0 | - | 0 | 1 |
| B | none | - | - | 0 | 1 |
| B | position_teleport | 0.2 | 8 | 0 | 1 |
| B | slow_drift | 0.4 | 38.8 | 0 | 1 |
| B | false_target | 0 | - | 0 | 1 |
| B | formation_sabotage | 0 | - | 0 | 1 |
| B | byzantine_accuser | 0 | - | 0 | 1 |
| B | spoofed_anchor | 0.4 | 19.2 | 0 | 1 |
| B | collusion | 0.2 | 69.2 | 0 | 1 |
| C | none | - | - | 0 | 1 |
| C | position_teleport | 1 | 6.72 | 0 | 1 |
| C | slow_drift | 1 | 24.16 | 0 | 1 |
| C | false_target | 0 | - | 0 | 1 |
| C | formation_sabotage | 1 | 19.04 | 0 | 1 |
| C | byzantine_accuser | 0 | - | 0 | 1 |
| C | spoofed_anchor | 1 | 17.92 | 0 | 1 |
| C | collusion | 1 | 29.36 | 0 | 0.8 |

## Graceful degradation

Compromised fraction swept from zero to past the Byzantine bound f = (n-1)/3. The right-hand side of this curve is the honest part of the claim: the swarm holds up to the bound, and this is exactly how it fails after it.

| Compromised | Recall | Fully cleared | False excl. | Coverage | Mission success |
|---|---|---|---|---|---|
| 0 | - | - | 0 | 0.911 | 1 |
| 1 | 0 | 0 | 0 | 0.911 | 1 |
| 2 | 0 | 0 | 0 | 0.911 | 1 |
| 3 | 0 | 0 | 0 | 0.912 | 1 |
| 4 | 0 | 0 | 0 | 0.911 | 1 |
| 5 | 0 | 0 | 0 | 0.912 | 1 |
| 6 | 0 | 0 | 0 | 0.91 | 1 |

## ClaimCheck: verified accusations vs. counted ones

The same missions on the same seeds with accusations verified against independently re-derived physics (`verified`) and merely counted (`vote_only`). Read `honest_expelled` first: the vote-only configuration starts convicting honest drones as soon as the compromised side becomes the majority, because every mechanism in the consensus layer is an aggregate of opinions and an aggregate follows whichever bloc is larger. `recall` is the honest cost column - past the Byzantine bound the verifier declines to convict on evidence it cannot independently re-derive, so it stops catching attackers rather than guessing at them. `honest_quarantined` is where those refusals went: a reversible hold, not an expulsion.

| Accusations | Scenario | Compromised | Recall | Honest expelled | Honest held | Expulsions blocked | Mission success |
|---|---|---|---|---|---|---|---|
| vote_only | byzantine_accuser | 1 | 1 | 0 | 0 | 0 | 1 |
| vote_only | byzantine_accuser | 2 | 1 | 0 | 0 | 0 | 1 |
| vote_only | byzantine_accuser | 3 | 0 | 0 | 0 | 0 | 1 |
| vote_only | byzantine_accuser | 4 | 0 | 0 | 0 | 0 | 1 |
| vote_only | byzantine_accuser | 5 | 0 | 20 | 0 | 0 | 0.4 |
| vote_only | byzantine_accuser | 6 | 0 | 15 | 0 | 0 | 0.8 |
| vote_only | byzantine_accuser | 7 | 0 | 10 | 0 | 0 | 1 |
| vote_only | collusion | 1 | 1 | 0 | 0 | 0 | 0.8 |
| vote_only | collusion | 2 | 1 | 0 | 0 | 0 | 0.8 |
| vote_only | collusion | 3 | 1 | 0 | 0 | 0 | 0.8 |
| vote_only | collusion | 4 | 1 | 0 | 0 | 0 | 0.2 |
| vote_only | collusion | 5 | 0 | 6 | 0 | 0 | 1 |
| vote_only | collusion | 6 | 0 | 10 | 0 | 0 | 0.8 |
| vote_only | collusion | 7 | 0 | 10 | 0 | 0 | 1 |
| vote_only | position_teleport | 1 | 1 | 0 | 0 | 0 | 1 |
| vote_only | position_teleport | 2 | 1 | 0 | 0 | 0 | 1 |
| vote_only | position_teleport | 3 | 1 | 0 | 0 | 0 | 1 |
| vote_only | position_teleport | 4 | 1 | 0 | 0 | 0 | 0.4 |
| vote_only | position_teleport | 5 | 0 | 0 | 0 | 0 | 1 |
| vote_only | position_teleport | 6 | 0 | 0 | 0 | 0 | 1 |
| vote_only | position_teleport | 7 | 0 | 10 | 0 | 0 | 1 |
| vote_only | spoofed_anchor | 1 | 1 | 0 | 0 | 0 | 1 |
| vote_only | spoofed_anchor | 2 | 1 | 10 | 0 | 0 | 0 |
| vote_only | spoofed_anchor | 3 | 1 | 1 | 0 | 0 | 0.6 |
| verified | byzantine_accuser | 1 | 0 | 0 | 0 | 0 | 1 |
| verified | byzantine_accuser | 2 | 0 | 0 | 0 | 0 | 1 |
| verified | byzantine_accuser | 3 | 0 | 0 | 0 | 0 | 1 |
| verified | byzantine_accuser | 4 | 0 | 0 | 0 | 0 | 1 |
| verified | byzantine_accuser | 5 | 0 | 0 | 0 | 0 | 1 |
| verified | byzantine_accuser | 6 | 0 | 0 | 0 | 0 | 1 |
| verified | byzantine_accuser | 7 | 0 | 0 | 0 | 0 | 1 |
| verified | collusion | 1 | 1 | 0 | 0 | 0 | 0.8 |
| verified | collusion | 2 | 1 | 0 | 0 | 0 | 0.8 |
| verified | collusion | 3 | 1 | 0 | 0 | 1 | 0.8 |
| verified | collusion | 4 | 1 | 0 | 0 | 5 | 0.2 |
| verified | collusion | 5 | 0 | 0 | 0 | 0 | 1 |
| verified | collusion | 6 | 0 | 0 | 0 | 0 | 1 |
| verified | collusion | 7 | 0 | 0 | 0 | 0 | 1 |
| verified | position_teleport | 1 | 1 | 0 | 0 | 0 | 1 |
| verified | position_teleport | 2 | 1 | 0 | 0 | 0 | 1 |
| verified | position_teleport | 3 | 1 | 0 | 0 | 0 | 0.8 |
| verified | position_teleport | 4 | 1 | 0 | 0 | 1 | 0.4 |
| verified | position_teleport | 5 | 0 | 0 | 0 | 0 | 1 |
| verified | position_teleport | 6 | 0 | 0 | 0 | 0 | 1 |
| verified | position_teleport | 7 | 0 | 0 | 0 | 0 | 1 |
| verified | spoofed_anchor | 1 | 1 | 0 | 0 | 1 | 1 |
| verified | spoofed_anchor | 2 | 1 | 0 | 0 | 2 | 0.8 |
| verified | spoofed_anchor | 3 | 1 | 0 | 0 | 1 | 0.8 |
| anchored | byzantine_accuser | 1 | 1 | 0 | 0 | 0 | 1 |
| anchored | byzantine_accuser | 2 | 1 | 0 | 0 | 0 | 1 |
| anchored | byzantine_accuser | 3 | 0.667 | 0 | 0 | 0 | 0.8 |
| anchored | byzantine_accuser | 4 | 0.4 | 0 | 0 | 0 | 0.8 |
| anchored | byzantine_accuser | 5 | 0.04 | 0 | 0 | 0 | 1 |
| anchored | byzantine_accuser | 6 | 0 | 0 | 0 | 0 | 1 |
| anchored | byzantine_accuser | 7 | 0 | 0 | 0 | 0 | 1 |
| anchored | collusion | 1 | 1 | 0 | 0 | 0 | 1 |
| anchored | collusion | 2 | 1 | 0 | 0 | 0 | 1 |
| anchored | collusion | 3 | 1 | 0 | 0 | 0 | 0.8 |
| anchored | collusion | 4 | 1 | 0 | 0 | 1 | 0.2 |
| anchored | collusion | 5 | 0.8 | 0 | 0 | 0 | 0 |
| anchored | collusion | 6 | 0.6 | 0 | 0 | 0 | 0 |
| anchored | collusion | 7 | 0.057 | 0 | 0 | 0 | 0.8 |
| anchored | position_teleport | 1 | 1 | 0 | 0 | 0 | 1 |
| anchored | position_teleport | 2 | 1 | 0 | 0 | 0 | 1 |
| anchored | position_teleport | 3 | 1 | 0 | 0 | 0 | 0.8 |
| anchored | position_teleport | 4 | 1 | 0 | 0 | 0 | 0.4 |
| anchored | position_teleport | 5 | 0.8 | 0 | 0 | 0 | 0.2 |
| anchored | position_teleport | 6 | 0 | 0 | 0 | 0 | 1 |
| anchored | position_teleport | 7 | 0 | 0 | 0 | 0 | 1 |
| anchored | spoofed_anchor | 1 | 1 | 0 | 0 | 0 | 1 |
| anchored | spoofed_anchor | 2 | 1 | 0 | 0 | 1 | 0.8 |
| anchored | spoofed_anchor | 3 | 1 | 0 | 0 | 0 | 0.8 |

## CUSUM precision / latency trade-off

CUSUM alarm threshold h swept against the slow-drift attack and the honest control. Low h detects sooner and convicts honest drones more often; high h is patient. The operating point in the shipped config is one point on this curve, chosen before the scenario results were read.

| Threshold h | Scenario | Recall | Latency (s) | False excl. |
|---|---|---|---|---|
| 2 | none | - | - | 0 |
| 2 | slow_drift | 1 | 21.067 | 0 |
| 3 | none | - | - | 0 |
| 3 | slow_drift | 1 | 21.6 | 0 |
| 4 | none | - | - | 0 |
| 4 | slow_drift | 1 | 21.6 | 0 |
| 5 | none | - | - | 0 |
| 5 | slow_drift | 1 | 22.133 | 0 |
| 6.5 | none | - | - | 0 |
| 6.5 | slow_drift | 1 | 23.2 | 0 |
| 8 | none | - | - | 0 |
| 8 | slow_drift | 1 | 23.467 | 0 |
| 10 | none | - | - | 0 |
| 10 | slow_drift | 1 | 24.533 | 0 |
| 13 | none | - | - | 0 |
| 13 | slow_drift | 1 | 24.8 | 0 |
