"""Central configuration for the VISHWAS swarm-integrity simulator.

Every tunable number in the system lives here so that experiments are
reproducible from a single serialisable object.  Nothing in this module
imports from the rest of the package, so it can be safely imported anywhere.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


# --------------------------------------------------------------------------
# Physical airframe envelope
# --------------------------------------------------------------------------
@dataclass
class AirframeConfig:
    """Flight envelope of a single quad-class UAV.

    These bounds are what Evidence channel E1 checks a drone's *claimed*
    telemetry against.  An attacker can forge the telemetry, but a real
    airframe cannot exceed these numbers.
    """

    max_speed: float = 22.0          # m/s
    max_accel: float = 9.0           # m/s^2
    max_turn_rate: float = 1.6       # rad/s (yaw)
    max_climb_rate: float = 6.0      # m/s
    max_descent_rate: float = 8.0    # m/s
    min_altitude: float = 15.0       # m AGL
    max_altitude: float = 400.0      # m AGL

    # Energy model: P = p_hover + k_v * |v|^2 + k_c * max(climb, 0)
    hover_power: float = 220.0       # W
    drag_coeff: float = 0.42         # W / (m/s)^2
    climb_coeff: float = 38.0        # W / (m/s)
    battery_capacity: float = 300_000.0  # J (~ 83 Wh)


# --------------------------------------------------------------------------
# Mesh radio / sensing
# --------------------------------------------------------------------------
@dataclass
class MeshConfig:
    """Radio mesh + peer-ranging characteristics."""

    # Ranges are sized against the search area, not against a datasheet.  E2 is
    # the channel the attacker cannot forge, and it only exists while peers can
    # actually hear each other: a mesh whose links break as soon as the swarm
    # fans out into its sectors would hand every liar a private sky.
    comm_range: float = 2000.0       # m, beyond this no link
    sensing_range: float = 1600.0    # m, beyond this no range measurement
    packet_loss: float = 0.02        # per-link probability
    latency_ticks: int = 1           # delivery delay in simulation ticks

    # Peer ranging (time-of-flight assisted RSSI).  Error grows with distance.
    range_noise_base: float = 1.4    # m, 1-sigma at zero range
    range_noise_slope: float = 0.012  # m per metre of true range
    bearing_noise: float = 0.035     # rad, 1-sigma (only when line of sight)
    max_observers: int = 12          # cap on peers ranged per tick (radio budget)

    # Certified anchors: drones with a hardware root of trust (e.g. a signed
    # TPM/secure-element attestation chain) whose position report is treated
    # as ground truth rather than a vote.  This exists because every other
    # mechanism in this file is fundamentally a *majority*-vote mechanism -
    # the trimmed mean, the 2f+1 quorum, the credibility-weighted average -
    # and no majority-vote mechanism can tell a compromised majority from an
    # honest one once the compromised side *is* the majority (measured
    # directly: experiments/diag24_few_honest.py, recall collapses to 0% and
    # false-exclusions spike the instant compromised crosses n/2). A
    # majority-compromised swarm cannot vote its way to the truth - it needs
    # a reference outside the vote. One certified anchor is that reference:
    # `Swarm` never draws it into the compromised set (a real root-of-trust
    # airframe is not attackable by the threat model this simulator scores),
    # `CredibilityBook` pins its vote weight at 1.0 permanently, `voting.py`
    # never excludes it, and E2 (`e2_cross.py`) shrinks its contributed range
    # sigma by `anchor_sigma_factor` beyond normal credibility-based
    # shrinking - making its measurement dominate any multilateration fit it
    # takes part in, the same way a hard constraint dominates a soft one.
    # Empty by default: nothing above activates unless a mission opts in.
    certified_anchors: tuple[int, ...] = ()
    # 8, not 25.  The anchor has to dominate any fit it joins - that is the
    # whole point, and 8 is already far past what ordinary credibility can
    # reach, since that is capped at 1.0.  But a single range circle does not
    # determine a 2D position, and shrinking one observer's sigma far enough
    # to override every other constraint makes the fit ill-conditioned rather
    # than merely anchored: the solution is pinned to one circle and free
    # along it.  Measured at 25.0 that produced a spurious offset on an
    # *honest* drone large enough for five honest peers to independently
    # verify a claim against it, and the swarm expelled one of its own
    # (n=9, collusion, 2 compromised, seed 7 - the only false expulsion in
    # 360 missions).  At 8.0 that case is clean and the anchored gains past
    # the Byzantine bound are unchanged (8/10 at 5 of 9, 8/12 at 6 of 9,
    # identical at 25.0, 8.0 and 4.0) - so the extra dominance was buying
    # nothing and costing an aircraft.
    anchor_sigma_factor: float = 8.0    # extra E2 sigma shrink for an anchor's range


# --------------------------------------------------------------------------
# Mission
# --------------------------------------------------------------------------
@dataclass
class MissionConfig:
    """Area-search mission with formation transit and target triangulation."""

    area_size: float = 1400.0        # m, square search area side
    cruise_altitude: float = 120.0   # m
    formation_spacing: float = 45.0  # m between formation slots
    waypoint_tolerance: float = 25.0  # m
    n_targets: int = 4
    target_confirm_observers: int = 3  # peers needed to confirm a detection
    target_detect_range: float = 260.0  # m
    sector_coverage_goal: float = 0.90  # fraction of sector cells to declare success
    # Closure rate E3 assumes an honest drone can sustain when transiting to a
    # newly assigned sector.  Deliberately below the guidance cruise speed: the
    # allowance it buys is what stops E3 accusing drones that are simply still
    # on their way.
    transit_speed: float = 12.0      # m/s
    transit_margin: float = 1.25     # slack on the honest transit budget


# --------------------------------------------------------------------------
# Evidence engine
# --------------------------------------------------------------------------
@dataclass
class EvidenceConfig:
    """Thresholds and weights for the three orthogonal evidence channels."""

    # --- E1: self-consistency -------------------------------------------
    e1_envelope_tolerance: float = 1.15   # allow 15% slack over the envelope
    e1_integration_sigma: float = 2.5     # m, expected p/v integration mismatch
    e1_energy_sigma: float = 0.06         # fractional battery-model mismatch

    # --- E2: cross-observation ------------------------------------------
    e2_min_observers: int = 3             # below this, E2 abstains
    e2_huber_delta: float = 3.0           # robust-loss knee, in sigma
    e2_ransac_iters: int = 24
    e2_ransac_inlier_sigma: float = 3.0
    e2_position_sigma: float = 4.0        # m, expected multilateration residual
    # Fuse the one-hop direct range check into the reported z-score instead of
    # only falling back to it when the geometric fix is unusable. Fixes a
    # recall collapse in larger swarms (n gtrsim 12) where the fused
    # multilateration statistic decays under collusion faster than the
    # quorum shrinks; costs a small increase in false accusations. See
    # experiments/diag12.py..diag16.py and experiments/ablation_directz.log for the
    # measurements behind this default. Set False to reproduce pre-fix
    # behaviour (e.g. to regenerate the original data/results/*.json studies).
    e2_fuse_direct_z: bool = True

    # --- E3: mission-logic ----------------------------------------------
    e3_slot_sigma: float = 30.0           # m, tolerated formation-slot error
    e3_sector_sigma: float = 120.0        # m, tolerated sector-assignment error

    # --- Sequential change detection (CUSUM) ----------------------------
    # ``k`` is the per-tick drift CUSUM must exceed before it accumulates at
    # all.  The fused statistic it watches is *not* a signed, near-zero-mean
    # Gaussian z-score: E2's dominant term is a 2D Euclidean offset (a
    # multilateration fix vs. a claimed position) divided by a scalar sigma,
    # which is a strictly non-negative, chi-like quantity with an intrinsic
    # null-hypothesis mean around 0.6-0.9 even when nobody is lying (a 2D
    # radial error's expected magnitude is ~1.25x its per-axis sigma, not 0).
    # The previous k=0.55 sat *below* that honest baseline, so CUSUM's
    # "no drift" floor never actually held - every honest pair's statistic
    # drifted upward on pure measurement noise and reliably false-alarmed
    # within the first few dozen ticks (confirmed empirically: with
    # scenario="none" and zero attackers, fused-z has population mean~0.67,
    # median~0.58 across 12-drone swarms; see experiments/diag20_k_test.py).
    # k=1.6 keeps the leaky-integrator steady state under pure noise
    # (``E[max(0, z-k)] / (1-decay)``) at roughly 1.5, well under
    # cusum_threshold_h below, while every tested attack scenario's
    # sustained fused-z stays far enough above it to alarm quickly - see
    # experiments/diag20_k1.6_full.log for the before/after sweep.
    cusum_slack_k: float = 1.6            # in sigma; ignore drift below this
    cusum_threshold_h: float = 6.5        # alarm level
    cusum_decay: float = 0.995            # slow forgetting of stale evidence
    cusum_reset_on_alarm: bool = False    # keep accumulating (persistent alarm)

    # --- Channel fusion --------------------------------------------------
    w_e1: float = 0.22
    w_e2: float = 0.58                    # strongest channel: attacker can't forge it
    w_e3: float = 0.20
    suspicion_gain: float = 0.55           # logistic steepness on fused CUSUM


# --------------------------------------------------------------------------
# Consensus engine
# --------------------------------------------------------------------------
@dataclass
class ConsensusConfig:
    """Trust-weighted Byzantine agreement over suspicion reports."""

    gossip_period: int = 4               # ticks between suspicion broadcasts
    accuse_threshold: float = 0.55       # s_i[j] above this counts as an accusation
    quorum_weight_threshold: float = 0.62  # normalised W[j] needed to expel

    # Byzantine safety: require agreement from at least 2f+1 weighted accusers.
    assumed_f: int | None = None         # None -> floor((n-1)/3)

    # Accuser credibility  w_i = g(tau_static, tau_runtime, history)
    cred_w_static: float = 0.30
    cred_w_runtime: float = 0.30
    cred_w_history: float = 0.40
    cred_floor: float = 0.05             # never fully silence a node
    history_reward: float = 0.08         # accusation later upheld
    history_penalty: float = 0.22        # accusation later rejected
    self_suspicion_discount: float = 0.85  # scale credibility by (1 - s_self)^this

    # A node under suspicion itself gets its vote weight collapsed.
    mutual_accusation_damping: float = 0.5

    exclusion_grace_ticks: int = 6       # min ticks of sustained quorum before expel
    reinstate_ticks: int | None = None   # None = expulsion is permanent

    # Byzantine-accuser (fabricator) self-detection.  A drone that lies only
    # in gossip - never in its own telemetry or ranges - never gets accused
    # back by any evidence channel, so it can never itself become a quorum
    # target through the normal accusation path above.  This is a second,
    # independent detector that watches *accusers* rather than targets.
    #
    # Signal: breadth, not persistence.  Each round, for every accuser,
    # count the *distinct* targets it accused that the rest of the swarm's
    # weighted tally never corroborated (tally.weight below the same
    # dissent floor used by CredibilityBook penalisation).  Feed that count
    # into a slowly-decaying leaky integrator per accuser
    # (``fabrication_breadth_decay``).  A real ByzantineAccuser fabricates
    # suspicion about most of the swarm every single round it is active, so
    # its integrator races to a high steady state and stays there.  An
    # honest drone that narrowly, correctly suspects the one real attacker
    # *usually* only contributes a handful of rounds on a single target
    # before the rest of the swarm corroborates and its integrator decays
    # back down - but corroboration is not guaranteed to arrive at all
    # (e.g. the attacker may evade every other channel for the rest of the
    # mission, as happens in a rare ``false_target`` seed - see
    # docs/ENGINEERING_LOG.md §13), so the threshold must not rely on that
    # happening.  For a *constant* feed of exactly one uncorroborated
    # target every round forever, the integrator's steady state is the
    # closed form ``feed / (1 - decay)`` and it strictly never exceeds that
    # bound (monotonically increasing, asymptotic) - at
    # ``fabrication_breadth_decay = 0.9`` that ceiling is ``1/0.1 = 10.0``.
    # ``fabrication_breadth_threshold`` is therefore set *above* that
    # ceiling, so a single honest drone narrowly, persistently naming one
    # real (but never-corroborated) attacker can mathematically never trip
    # it, no matter how long the mission runs.  A real ByzantineAccuser
    # naming most of the swarm (feed ~= n-2 per round) has a steady state
    # an order of magnitude higher (~100 at n=12) and clears the threshold
    # within a handful of rounds - the two cases stay cleanly separated at
    # any mission length.  (Empirically confirmed via
    # ``experiments/diag22_false_target_probe.py``, which traced the exact
    # per-round ``round_targets`` history that produced the false exclusion
    # this threshold fixes - see docs/ENGINEERING_LOG.md §13.  The
    # breadth/persistence distinction itself is the part the two earlier,
    # abandoned designs each got half of - see docs/ENGINEERING_LOG.md §10.)
    #
    # Safety: the liveness guard from the normal exclusion path applies
    # unchanged, and ``fabrication_cooldown_rounds`` is a hard circuit
    # breaker limiting this mechanism to excluding at most one drone per
    # cooldown window (the single worst offender, if several qualify) - so
    # even a swarm-wide transient confusion event (many drones briefly,
    # simultaneously over-suspicious after a real attack) cannot cascade
    # into a runaway multi-exclusion feedback loop the way the abandoned
    # same-round-breadth design did.
    fabrication_breadth_threshold: float = 15.0
    fabrication_breadth_decay: float = 0.9
    fabrication_grace_rounds: int = 15
    fabrication_cooldown_rounds: int = 20


# --------------------------------------------------------------------------
# ClaimCheck - independent accusation verification
# --------------------------------------------------------------------------
@dataclass
class ClaimCheckConfig:
    """Tolerances for the independent Mathematical Claim Verifier.

    Everything above this point in the consensus stack is a *vote*: a trimmed
    weighted mean, a ``2f+1`` quorum, a credibility average.  Every one of
    them structurally sides with whichever bloc is larger, so once the
    compromised side *is* the majority the swarm convicts the honest minority
    (measured: ``experiments/diag24_few_honest.py``; see
    ``ConsensusConfig._majority_cap`` for the analysis).  ClaimCheck is the
    part of the decision that is deliberately *not* a vote.

    An accusation is treated as a claim with evidence rather than a ballot,
    and every claim is audited against measurements the accuser already
    published to the whole mesh and cannot retract:

    * **Commitment audit.**  The accuser's own ``RangeReportMsg`` to the
      target is a public commitment.  Re-deriving the one-hop residual from
      it and from the two drones' claimed positions reproduces exactly the
      ``direct_z`` the accuser's own E2 checker would have computed.  An
      accuser cannot both publish honest ranges and claim they show an
      anomaly - the arithmetic is reproducible by anyone.
    * **Independent re-derivation.**  The target's position is re-fixed by
      multilateration from every *other* drone's published ranges, with the
      accuser excluded from its own alibi.  A drone that lies only in gossip
      (``byzantine_accuser``) leaves this fix untouched, so the fix lands on
      the innocent target's real position and refutes the accusation on
      physics rather than on a head count.

    The bound this cannot cross is stated honestly rather than hidden: when a
    majority forges its *range reports* too (``collusion`` past ``f``), the
    anchor set splits into two internally-consistent, mutually-contradictory
    worlds and range geometry alone cannot say which is real without a
    reference outside the swarm.  That case is detected (``reciprocity_z``
    below) and resolved by refusing to convict - the target is quarantined,
    reversibly, instead of expelled.  Refusing to act is the safe failure;
    expelling the honest minority is not.
    """

    # Independent re-derivation thresholds, in sigma of the re-derived fix.
    refute_z: float = 2.0        # fix agrees with the claim -> claim refuted
    support_z: float = 4.0       # fix contradicts the claim -> claim supported
    # There is deliberately no separate observer minimum here: the audit
    # abstains at ``EvidenceConfig.e2_min_observers``, the same geometry floor
    # E2 itself abstains at.  A second knob would let the audit and the
    # channel it audits disagree about when a fix is meaningful, and the audit
    # would then be arguing about method rather than about evidence.

    # Commitment audit: how far the accuser's *published* one-hop residual may
    # fall below what an accusation implies before the claim is fabricated.
    # ``accuse_threshold`` worth of suspicion asserts a sustained multi-sigma
    # anomaly; a published range that reproduces the target's claim to within
    # this many sigma asserts the opposite, from the accuser's own radio.
    commitment_z: float = 1.5

    # Evidence-independence: two accusers whose gossiped z-vectors agree this
    # closely (relative L1 distance) are treated as one root claim.  A clique
    # reading from one script is one witness, not many.
    duplicate_tolerance: float = 0.02

    # Contested-geometry detection.  ``r_ij`` and ``r_ji`` are two noisy
    # measurements of one distance, so a pair disagreeing by more than this
    # many combined sigma contains at least one drone reporting a world it is
    # not in.  Peeling on that relation leaves the largest self-consistent
    # account of the swarm's geometry; the geometry is contested - and every
    # claim resolves AMBIGUOUS - when that account is not a strict majority of
    # the live swarm.  See ``recompute.assess_geometry`` for why the first
    # version of this threshold (``outliers > f_tolerated``) destroyed recall.
    #
    # Two limits on how much weight this can carry, both measured rather than
    # assumed, and both spelled out in docs/ENGINEERING_LOG.md §18.3.  Every
    # optimal-forgery liar re-derives its ranges from the same published
    # claims, so all liars agree with each other whatever direction each is
    # lying in - the graph is always two cliques and the larger always wins,
    # meaning contested essentially cannot fire under this attack suite.  And
    # common-mode drift barely perturbs reciprocity at all (the residual is
    # only the drift's projection onto each baseline), so for ``collusion``
    # the split is not visible even in principle.  This mechanism earns its
    # place against *uncoordinated* corruption - drifting clocks, damaged
    # radios, attackers not sharing one script - not against a coordinated
    # bloc, which is what ``certified_anchors`` is for.
    reciprocity_z: float = 4.0   # |r_ij - r_ji| beyond this many sigma = inconsistent

    # How many verified claims an anchor-underwritten expulsion needs.
    #
    # The ordinary requirement is the consensus layer's ``2f+1``, which is a
    # *vote* threshold: it assumes up to ``f`` signatures are lies and demands
    # enough that honest ones outnumber them.  A claim that survived
    # independent re-derivation with an incorruptible witness among its
    # accusers is not that kind of object, and past the majority ``2f+1`` is
    # not merely strict but unreachable - the drones able to produce a
    # verified claim are the honest ones, and there are fewer of them left
    # than ``2f+1``.
    #
    # Two is the smallest number that still means something: the anchor
    # cannot lie, and one independent corroborator is what distinguishes a
    # real anomaly from an anchor that is simply malfunctioning.  Measured
    # margin at n=9 (see docs/ENGINEERING_LOG.md §18): honest targets peaked at
    # a single supporting claim and *never* with the anchor among them, while
    # genuine attackers reached three or four with the anchor present.  The
    # separation is in who signs, not how many - which is why this number can
    # be small without being reckless, and why it is only ever consulted when
    # ``_verified_quorum`` has already confirmed a trusted anchor is one of
    # the accusers.
    anchor_backed_quorum: int = 2

    # Rounds of sustained contradiction before a certified anchor loses its
    # reference status.  Attestation proves the airframe is the one that was
    # shipped; it says nothing about whether its GNSS is being spoofed, and an
    # anchor's contributed sigma is shrunk far enough that a spoofed one would
    # drag every fit it joins.  Not 1: geometry is noisy, and demoting the one
    # trustworthy reference on a transient is its own failure mode.
    anchor_demote_rounds: int = 3

    # Action policy.
    quarantine_release_rounds: int = 25   # rounds of no supported claim -> release
    penalise_refuted: bool = True         # a refuted accuser pays credibility

# --------------------------------------------------------------------------
# Trust priors
# --------------------------------------------------------------------------
@dataclass
class PriorConfig:
    """Weights for the two outer trust layers."""

    # TrustChain-SBOM -> tau_static
    sbom_w_cve: float = 0.45
    sbom_w_provenance: float = 0.30
    sbom_w_maintenance: float = 0.25
    cvss_critical: float = 9.0
    cvss_high: float = 7.0

    # AdvGuard-lite -> tau_runtime
    adv_entropy_window: int = 20         # frames
    adv_entropy_alarm: float = 0.72      # normalised entropy
    adv_instability_alarm: float = 0.35  # class-flip rate
    adv_dropout_alarm: float = 0.30      # detection dropout rate
    adv_w_entropy: float = 0.40
    adv_w_instability: float = 0.35
    adv_w_dropout: float = 0.25


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------
@dataclass
class SimConfig:
    n_drones: int = 12
    dt: float = 0.2                      # s per tick
    max_ticks: int = 3000
    seed: int = 7

    # Which evidence/consensus layers are active (used by the ablation study).
    enable_consensus: bool = True        # False => configuration A (no VISHWAS)
    enable_trust_priors: bool = True     # False => configuration B (uniform trust)
    # False => configuration D (majority vote only): accusations are believed
    # on weight of numbers alone, which is the pre-ClaimCheck behaviour every
    # published study in data/results/ was generated under.
    enable_claim_verifier: bool = True

    airframe: AirframeConfig = field(default_factory=AirframeConfig)
    mesh: MeshConfig = field(default_factory=MeshConfig)
    mission: MissionConfig = field(default_factory=MissionConfig)
    evidence: EvidenceConfig = field(default_factory=EvidenceConfig)
    consensus: ConsensusConfig = field(default_factory=ConsensusConfig)
    claimcheck: ClaimCheckConfig = field(default_factory=ClaimCheckConfig)
    priors: PriorConfig = field(default_factory=PriorConfig)

    # ---------------------------------------------------------------- utils
    # NOTE: f_tolerated / quorum_size intentionally do NOT live here.  The
    # authoritative versions are ConsensusEngine.f_tolerated / .quorum_size
    # (vishwas/consensus/voting.py), which derive f from the *live* active
    # drone count (shrinks as exclusions happen) and cap quorum_size at the
    # mesh's radio budget (mesh.max_observers) rather than leaving it
    # unbounded. A static SimConfig-level copy used to exist here but was
    # unused (grep confirmed zero callers) and had silently drifted out of
    # sync with the real logic - removed rather than fixed in place to avoid
    # re-introducing a second source of truth.

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SimConfig":
        nested = {
            "airframe": AirframeConfig,
            "mesh": MeshConfig,
            "mission": MissionConfig,
            "evidence": EvidenceConfig,
            "consensus": ConsensusConfig,
            "claimcheck": ClaimCheckConfig,
            "priors": PriorConfig,
        }
        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            if key in nested:
                kwargs[key] = nested[key](**value)
            else:
                kwargs[key] = value
        return cls(**kwargs)
