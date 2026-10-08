"""The Consensus Engine - trust-weighted Byzantine agreement on expulsion.

Detection alone is insufficient.  A single drone's opinion is not
trustworthy, because that drone may itself be the compromised one.  The
decision to expel must be *collective*.

Protocol, per accusation round:

1. **Accusation gossip.**  Every drone broadcasts its suspicion vector
   ``s_i[*]`` over the mesh at a fixed interval.
2. **Trust-weighted, trimmed aggregation.**  Drone ``j`` accumulates
   accusation weight as a *trimmed* weighted mean: the ``f_tolerated`` most
   extreme suspicion reports about ``j`` on each side are dropped before
   averaging the rest,

       W[j] = trimmed_mean_{i != j}( w_i * s_i[j], drop=f_tolerated ) /
              trimmed_mean_{i != j}( w_i,          drop=f_tolerated )

   where ``w_i`` is the accuser's credibility from
   :mod:`vishwas.consensus.credibility` - not all opinions count equally,
   and trimming caps how far any small coalition of accusers can pull the
   mean regardless of their individual weight.  The count-based quorum
   check below is computed from the *untrimmed* accuser set - trimming only
   bounds the tally's magnitude, never the number of participants required.
3. **Independent verification.**  Every accusation is audited by
   :mod:`vishwas.verify` against measurements the swarm already broadcast,
   before it is allowed to count.  Claims the physics refutes are dropped and
   charged to the accuser; claims nothing can check are dropped without
   penalty.  This step is the one part of the decision that is *not* an
   aggregate of opinions - see step 4 for why that matters.
4. **Exclusion decision.**  ``j`` is expelled when ``W[j]`` exceeds the quorum
   threshold *with agreement from at least ``2f + 1`` weighted peers*, under
   the standard Byzantine bound ``n >= 3f + 1``, and sustains that for a
   short grace window - and, since ClaimCheck, only when at least
   ``quorum_size`` of those claims survived independent re-derivation.  Every
   mechanism in steps 1-2 is a vote, and a vote follows whichever bloc is
   larger; past the Byzantine bound that means convicting the honest
   minority, which was measured directly (``experiments/diag24_few_honest.py``).
   The count of *verified* claims is the one quantity a bloc cannot inflate
   by growing, because fabricated claims produce none of them.  When
   verification cannot settle the question, the target is held in a
   reversible quarantine rather than expelled.

Two attacks on the mechanism itself are handled explicitly, because a
consensus layer that cannot defend itself is not a security contribution:

* **Byzantine accuser** - a compromised drone accusing honest peers to get
  them expelled.  Its *victims* are protected by the ``2f+1`` quorum
  requirement, credibility weighting, decaying the accusation weight of any
  node itself under suspicion, and the trimmed-mean aggregation above,
  which discards outlier suspicion values before they can drag an innocent
  target's tally over quorum.  Its own telemetry and ranges are perfectly
  honest, so no evidence channel ever accuses it back and it can never be a
  *quorum* target through the path above - by design, that path only ever
  excludes a drone that other peers accuse and reach quorum on.  A second,
  independent detector (``_track_fabrication``) catches the accuser itself
  by watching accusers rather than targets: a leaky integrator per accuser,
  fed each round by the number of *distinct* victims it accused that the
  rest of the swarm never corroborated.  Breadth, not persistence, is the
  signal - a real fabricator accuses most of the swarm every round it is
  active and the integrator races to a high steady state, while an honest
  drone narrowly, correctly suspecting the one real attacker for a while
  only ever contributes on a single target and decays back down once
  corroboration catches up.  An earlier same-round "broad accusation"
  heuristic was tried and rejected before this: a single genuine attack
  event (e.g. a sudden position teleport) can itself cause transient,
  simultaneous, swarm-wide suspicion as the formation reacts, and that
  heuristic could not tell the two apart - it cascaded into excluding
  nearly the entire honest swarm in testing (see docs/ENGINEERING_LOG.md §10).
  The breadth integrator is deliberately slow (many-round decay, a long
  sustain window, and a hard exclude-at-most-one-per-cooldown circuit
  breaker) specifically so a transient event decays away instead of
  cascading.
* **Collusion** - two or more compromised drones corroborating each other's
  false reports and mutually vouching.  Colluders can suppress their own
  accusations of each other, but they cannot prevent the honest majority's
  independent range measurements from disagreeing with both of them; the
  same trimmed-mean aggregation also bounds how hard a colluding *pair* can
  pull an innocent third drone's tally, since a clique of size <= f_tolerated
  falls entirely within the trimmed band.

Scoping note: the mesh delivers gossip to all live, linked nodes, and the
aggregation rule is deterministic, so every honest node reaches the same
decision in the same round.  The implementation therefore evaluates the
agreement once per round rather than simulating per-node message logs.
Asynchrony and network partition are named future scope, not claimed results.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..config import ConsensusConfig, SimConfig
from ..verify import ClaimBundle, ClaimVerifier, SafetyEngine, TargetAudit
from .credibility import CredibilityBook


@dataclass
class VoteTally:
    target: int
    weight: float = 0.0                     # normalised W[j] in [0, 1]
    accusers: list[int] = field(default_factory=list)
    accuser_weight: float = 0.0
    quorum_met: bool = False
    sustained: int = 0
    # Full (untrimmed) per-accuser (accuser, credibility_weight, suspicion)
    # triples behind ``weight`` above, and how many extreme reports on each
    # side were dropped before averaging - kept for post-hoc explanation
    # (the dashboard's "why was this drone excluded" narrative), not used by
    # the vote itself.
    triples: list[tuple[int, float, float]] = field(default_factory=list)
    trimmed: int = 0

    def as_row(self) -> dict:
        return {
            "target": self.target,
            "W": round(self.weight, 4),
            "accusers": list(self.accusers),
            "quorum_met": self.quorum_met,
            "sustained": self.sustained,
        }


@dataclass
class ExclusionEvent:
    tick: int
    target: int
    weight: float
    accusers: list[int]
    quorum_required: int
    reason: str = "trust_weighted_quorum"
    # Mechanism-specific facts behind this decision (trimmed-mean vote math
    # for ``trust_weighted_quorum``, breadth/persistence figures for
    # ``fabrication_breadth``) - structured data for the dashboard's
    # exclusion-explanation narrative, not consulted by the vote itself.
    detail: dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> dict:
        return {
            "tick": self.tick,
            "target": self.target,
            "W": round(self.weight, 4),
            "accusers": list(self.accusers),
            "quorum_required": self.quorum_required,
            "reason": self.reason,
        }


class ConsensusEngine:
    """Aggregates gossiped suspicion into expulsion decisions."""

    def __init__(self, cfg: SimConfig, drones: list[int], credibility: CredibilityBook) -> None:
        self.cfg = cfg
        self.cn: ConsensusConfig = cfg.consensus
        self.credibility = credibility
        self.active: set[int] = set(drones)
        self.excluded: dict[int, int] = {}           # drone -> tick expelled
        self.events: list[ExclusionEvent] = []
        # ClaimCheck: accusations are audited against independently re-derived
        # physics before they are allowed to count.  Disabled reproduces the
        # pure majority-vote behaviour every study in data/results/ predates
        # this layer and was generated under.
        self.verifier: ClaimVerifier | None = (
            ClaimVerifier(cfg) if cfg.enable_claim_verifier else None
        )
        self.safety = SafetyEngine(cfg.claimcheck)
        self.audits: dict[int, TargetAudit] = {}
        #: Expulsions the verifier prevented - the headline safety metric.
        self.blocked: list[dict] = []
        self.tallies: dict[int, VoteTally] = {}
        self._sustain: dict[int, int] = defaultdict(int)
        self._dissent: dict[int, float] = defaultdict(float)
        self._history: deque[dict] = deque(maxlen=512)
        self.rounds = 0
        # Fabricator (byzantine-accuser) self-detection state - see
        # ConsensusConfig.fabrication_breadth_threshold for the design.
        self._fab_breadth: dict[int, float] = defaultdict(float)
        self._fab_sustain: dict[int, int] = defaultdict(int)
        self._fab_cooldown_until: int = -1
        # Every distinct target each accuser has ever named that the swarm
        # never corroborated - explanation-only bookkeeping, not consulted
        # by the detector itself (which only ever looks at the decaying
        # ``_fab_breadth`` score).
        self._fab_named_targets: dict[int, set[int]] = defaultdict(set)

    # ------------------------------------------------------------------
    @property
    def f_tolerated(self) -> int:
        n = len(self.active)
        if self.cn.assumed_f is not None:
            return int(self.cn.assumed_f)
        return max(0, (n - 1) // 3)

    @property
    def quorum_size(self) -> int:
        """``2f + 1`` weighted accusers required for an exclusion decision.

        ``2f + 1`` grows with swarm size (``f_tolerated`` ≈ n/3), but the
        number of peers that can actually range a given target and gather
        independent evidence on it is capped by the mesh's radio budget
        (``mesh.max_observers``), not by ``n``. Left unbounded, the quorum
        requirement keeps climbing while the achievable accuser count does
        not, and recall on the collusion scenario collapses once the two
        curves cross (empirically around n=12, see experiments/diag12.py and
        docs/ENGINEERING_LOG.md §2(a)). Capping at ``max_observers`` never
        drops the requirement below what an *unbounded* swarm would need for
        small n (the cap only binds once ``2f+1`` would already exceed the
        radio budget), so it does not weaken the Byzantine guarantee for the
        swarm sizes where ``2f+1`` alone was already sufficient.
        """
        ideal = 2 * self.f_tolerated + 1
        return min(ideal, self.cfg.mesh.max_observers)

    @property
    def _majority_cap(self) -> int:
        """Hard ceiling: never let cumulative exclusions reach a majority of
        the *original* roster (``self.active`` is fixed at construction and
        never shrinks, unlike ``live``).

        The trimmed-mean vote and the ``2f+1``-of-``3f+1`` quorum above are
        both majority-vote mechanisms at heart, and no majority-vote
        mechanism can tell an honest minority from a compromised one once
        the compromised side *is* the majority - it structurally sides with
        whichever side is bigger.  Measured directly (n=9, collusion and
        position_teleport scenarios, `experiments/diag24_few_honest.py`):
        recall and false-exclusion rate both stay clean through
        n_compromised=4 of 9 (a bare minority, 44%), then collapse
        instantly at n_compromised=5 of 9 (a bare majority, 56%) - 0/15
        liars caught, 11 honest drones wrongly excluded across three
        seeds. That is not a bug to fix in the vote math; a majority-vote
        estimator cannot out-vote a majority, in the same way a trimmed
        mean cannot survive >50% contamination.  What *is* fixable is the
        failure mode: refusing to act past this point converts "wrongly
        convicts the honest minority" into "stops trying once it can no
        longer trust its own vote" - the same philosophy as the liveness
        guard in ``_decide``, just triggered by *cumulative* exclusions
        against the *original* roster rather than by how few drones are
        currently live, so it engages before a compromised bloc gets the
        chance to become a live-majority in the first place.
        """
        return (len(self.active) - 1) // 2

    # ------------------------------------------------------------------
    def round(
        self,
        tick: int,
        reports: dict[int, dict[int, float]],
        bundle: ClaimBundle | None = None,
    ) -> list[ExclusionEvent]:
        """Run one accusation round.

        ``reports`` maps accuser -> {target: suspicion}, containing only the
        gossip that actually arrived over the mesh this round.  ``bundle``
        carries the public commitments behind that gossip - every drone's
        claimed position, published ranges and cited evidence - which is what
        ClaimCheck audits the accusations against.  It is optional so the
        engine stays usable (as a plain majority vote) without it.
        """
        self.rounds += 1
        live = [d for d in self.active if d not in self.excluded]

        # Verify before aggregating.  Order matters: a claim the physics
        # refutes must never reach the tally in the first place, and a claim
        # nobody can check must never be counted as corroboration.
        self.audits = {}
        if self.verifier is not None and bundle is not None:
            self.audits = self.verifier.audit(
                bundle, reports, live, self.f_tolerated, self.cn.accuse_threshold
            )
            self._penalise_refuted()

        # Self-suspicion: how much the swarm currently distrusts each accuser.
        self_susp = self._self_suspicion(reports, live)
        weights = self.credibility.effective(self_susp, self.cn.self_suspicion_discount)

        tallies: dict[int, VoteTally] = {t: VoteTally(t) for t in live}
        for target in live:
            tally = tallies[target]
            # Accusers whose claim survived the audit.  ``None`` means the
            # verifier is off (or had no bundle), in which case every claim is
            # admissible and this reduces exactly to the pre-ClaimCheck vote.
            audit = self.audits.get(target)
            admissible = audit.admissible if audit is not None else None
            triples: list[tuple[int, float, float]] = []
            for accuser in live:
                if accuser == target:
                    continue
                w = weights.get(accuser, self.cn.cred_floor)
                s = float(reports.get(accuser, {}).get(target, 0.0))
                if admissible is not None and accuser not in admissible:
                    # Refuted or unsupported: the claim contributes nothing.
                    # Dropping it to zero rather than deleting the row keeps
                    # the trimmed mean's denominator honest - a refuted
                    # accuser is evidence *against* the tally, not an absence.
                    s = 0.0
                # Mutual accusation damping: if two nodes accuse each other,
                # neither claim is allowed to dominate on its own.
                back = float(reports.get(target, {}).get(accuser, 0.0))
                if s >= self.cn.accuse_threshold and back >= self.cn.accuse_threshold:
                    s *= self.cn.mutual_accusation_damping
                triples.append((accuser, w, s))
                # The count-based quorum check always sees every accuser who
                # crossed the threshold, independent of trimming below.
                if s >= self.cn.accuse_threshold:
                    tally.accusers.append(accuser)
                    tally.accuser_weight += w

            # Trimmed weighted mean: drop the ``f_tolerated`` most extreme
            # suspicion reports on each side before averaging.  This bounds
            # how far any coalition of up to ``f_tolerated`` accusers can
            # pull the tally, no matter how much raw credibility weight
            # they individually hold - the direct countermeasure for a
            # single fabricating gossiper (byzantine_accuser) or a
            # colluding pair (collusion) ganging up on an innocent third
            # drone.  ``trim`` degrades to 0 when there are too few
            # accusers to trim safely, so small/early-round tallies are
            # unaffected.
            triples.sort(key=lambda triple: triple[2])
            trim = min(self.f_tolerated, (len(triples) - 1) // 2) if triples else 0
            kept = triples[trim: len(triples) - trim] if trim else triples
            num = sum(w * s for _, w, s in kept)
            den = sum(w for _, w, _ in kept)
            tally.weight = float(num / den) if den > 1e-9 else 0.0
            tally.triples = triples
            tally.trimmed = trim
            tally.quorum_met = (
                tally.weight >= self.cn.quorum_weight_threshold
                and len(tally.accusers) >= min(self.quorum_size, max(1, len(live) - 1))
            )

        self.tallies = tallies
        events = self._decide(tick, tallies, live)
        round_targets = self._penalise_dissent(tallies, live)
        if self.verifier is not None:
            # A drone is not a fabricator for naming somebody the swarm can
            # produce real evidence against.
            #
            # ``_penalise_dissent`` feeds this detector with claims the rest
            # of the swarm did not corroborate, and corroboration is a
            # majority opinion.  Past the bound that inverts: the honest
            # minority's *correct* accusations are precisely the ones a
            # compromised majority declines to corroborate, so the detector
            # turns around and expels honest drones for telling the truth.
            # Measured, and it is not marginal - ``collusion``, 5 of 9, seed
            # 7: drones 5, 8, 1 and 2 were all expelled by this path, and the
            # targets each of them had named were *exclusively* real
            # attackers.  Four honest aircraft removed for correctly
            # identifying the entire compromised set.
            #
            # Two earlier attempts failed and are worth not repeating.
            # Feeding refuted claims instead of uncorroborated ones changes
            # nothing, because with a forging majority the re-derivation that
            # refutes is itself computed from the liars' anchors.  Requiring
            # the accused fabricator to sit inside the dominant reciprocity
            # world changes nothing either, because common-mode drift barely
            # perturbs reciprocity at all (the residual is only the drift's
            # projection onto each baseline) so that world contains nearly
            # everyone.
            #
            # What does discriminate is the *target*, not the accuser: strip
            # out every target some peer produced an independently verified
            # claim against.  A fabricator invents victims, so no verified
            # claim against them exists and its count stands.  An honest
            # drone names real attackers, and if the physics can support a
            # claim against those attackers from anywhere in the swarm, the
            # naming is defensible and earns no fabrication credit.
            # Who confers defensibility is itself attackable.  Accepting
            # *any* supported claim hands a compromised bloc a way to shield
            # its own members: manufacture verified-looking claims against
            # the drone you are framing, and your victim becomes
            # "defensible", which strips your fabrication credit for naming
            # it and takes you out of this detector's reach.  Measured
            # (``byzantine_accuser``, 5 of 9, one anchor): four compromised
            # drones raised supported claims against honest drone 5 - and
            # that is precisely why none of the four was ever caught.
            #
            # With a certified anchor in the swarm there is a witness that
            # cannot be recruited into that, so only the anchor's own
            # supported claim confers defensibility.  Without one, fall back
            # to the broader rule: it can be gamed, but the alternative is
            # trusting nobody, and a detector that credits every naming as
            # fabrication convicts honest drones for being right.
            anchors = self._trusted_anchors()
            if anchors:
                # NOTE: ``anchor_named`` below is unioned with the broad rule,
                # not substituted for it.  An anchor can only range
                # ``mesh.max_observers`` peers, so in a swarm larger than that
                # budget it simply cannot see most drones - and an honest
                # accuser naming a real attacker outside the anchor's radio
                # horizon was getting no protection at all.  Measured at n=25:
                # every honest drone lost was lost this way.
                # Two ways an anchor makes a naming defensible, and the second
                # one is load-bearing.  Restricting this to anchor-*supported*
                # claims alone re-opened the very inversion it was meant to
                # close: at ``collusion`` 6-of-9 the honest drones are naming
                # real liars, but with the geometry majority-poisoned those
                # correct accusations get refuted, the liars stopped counting
                # as defensible, and an honest drone was expelled as a
                # fabricator again (measured: drone 2, seed 7,
                # ``reason=fabrication_breadth``).
                #
                # The anchor's own *accusations* close it.  It is honest by
                # construction, so what it names is a real anomaly and what it
                # declines to name is not - measured across both seeds of that
                # run, the anchor accused exactly the six compromised drones
                # and no honest one.  Naming a drone the anchor is itself
                # accusing can never be fabrication; naming one the anchor
                # sees nothing wrong with is exactly what fabrication looks
                # like, which is what still catches the gossip-only attacker.
                anchor_named = {
                    target
                    for anchor in anchors
                    for target, score in reports.get(anchor, {}).items()
                    if float(score) >= self.cn.accuse_threshold
                }
                defensible = anchor_named | {
                    target
                    for target, audit in self.audits.items()
                    if anchors.intersection(audit.supported_accusers)
                    or len(audit.supported_accusers) > self.f_tolerated
                }
            else:
                defensible = {
                    target
                    for target, audit in self.audits.items()
                    if audit.supported_accusers
                }
            trimmed: dict[int, set[int]] = defaultdict(set)
            for accuser, named in round_targets.items():
                remaining = set(named) - defensible
                if remaining:
                    trimmed[accuser] = remaining
            round_targets = trimmed

        just_excluded = {ev.target for ev in events}
        events = events + self._track_fabrication(tick, round_targets, live, just_excluded)
        # Age the reversible holds.  A target stays quarantined only while
        # claims about it keep arriving; silence releases it.
        self.safety.sustain({t for t, a in self.audits.items() if a.findings})
        self._history.append(
            {"tick": tick, "tallies": {t: v.as_row() for t, v in tallies.items()}}
        )
        return events

    # ------------------------------------------------------------------
    def _verified_quorum(self, audit: TargetAudit) -> int:
        """How many verified claims this target's expulsion needs.

        ``2f+1`` is a *vote* threshold: it assumes up to ``f`` signatures are
        lies and demands enough that the honest ones outnumber them.  Applied
        to ClaimCheck it is doubly wrong.  It is too strict, because a
        verified claim is not trusted for who signed it but for surviving
        independent re-derivation - and it is unachievable exactly when it
        matters, since the drones able to produce one are the honest ones, and
        past the majority there are fewer of them than ``2f+1``.  Measured
        (``collusion``, 5 of 9, one anchor): every attacker had 4 verified
        claims against it and needed 5, so the swarm identified all five
        correctly and expelled none.  A counting rule was blocking an answer
        the physics had already produced.

        The obvious relaxation is unsafe, and the measurement says so
        loudly.  With ``byzantine_accuser`` at 5 of 9, four *compromised*
        drones manufactured verified claims against honest drone 5, while the
        best evidence against any real attacker in the same run was two - the
        false case was better supported than the true one.  Lowering the
        threshold on its own would have expelled the innocent drone and still
        missed the attackers.

        What separates the two cases is not how many claims there are but
        *who is among them*.  A certified anchor cannot be compromised (the
        threat model does not reach a hardware root of trust, and
        ``Swarm.__init__`` never draws one into the compromised set), so it
        never signs a fabricated case.  Across every measured run the anchor
        was present in the supporting set whenever the target was genuinely
        an attacker and absent whenever the target was honest.  So the
        relaxation is granted only when the anchor itself is one of the
        verified accusers - and then the requirement drops to ``f+1``, which
        still guarantees the supporting set is not contained within the
        Byzantine budget.

        Without an anchor nothing changes: the swarm has no incorruptible
        witness, so it keeps the full ``2f+1`` and declines to convict.
        """
        anchors = self._trusted_anchors()
        if anchors and anchors.intersection(audit.supported_accusers):
            return min(self.quorum_size, self.cfg.claimcheck.anchor_backed_quorum)

        # No anchor: the requirement is ``2f+1``, but capped by how many peers
        # could physically have observed this target.
        #
        # ``quorum_size`` is already capped at ``mesh.max_observers``, and at
        # n=9 that is a formality because nearly every drone can range every
        # other.  At n=25 it is not: the radio budget means only about a dozen
        # peers ever measure a given target, and *which* dozen churns as the
        # formation moves.  Demanding all of them agree for six consecutive
        # rounds is unsatisfiable, and measurably so - verified-only mode
        # caught 0 of 176 attackers at n=25 while peaking at 16 supported
        # claims against a required 12.  It was not failing to find them; it
        # was failing to find them all at the same instant.
        #
        # A majority of the peers that actually had geometry is the honest
        # requirement: corroboration cannot be demanded from a drone that was
        # never in a position to observe.  Floored at ``f+1`` so it can never
        # drop inside the Byzantine budget.
        observers = max(
            (f.independent_observers for f in audit.findings), default=0
        )
        if observers <= 0:
            return self.quorum_size
        # Relax ONLY when the requirement is at or above the physical ceiling.
        #
        # The first version relaxed unconditionally to a majority of
        # observers, and it cost an honest drone at the easiest point on the
        # whole matrix - ``slow_drift``, one compromised drone of nine, with
        # an anchor - because it dropped the requirement from 5 to 4 in a
        # swarm where 5 was comfortably achievable. Lowering a threshold that
        # was not binding buys nothing and spends safety.
        #
        # At n=9 roughly eight peers can range any target against a quorum of
        # five: ample margin, so nothing changes. At n=25 the radio budget
        # caps observers near twelve against a quorum of twelve - no margin,
        # and the observable set churns below that ceiling as the formation
        # moves, which is what made the requirement unsatisfiable. The
        # relaxation applies exactly there and nowhere else.
        if observers > self.quorum_size:
            return self.quorum_size
        achievable = max(self.f_tolerated + 1, (observers + 1) // 2)
        return min(self.quorum_size, achievable)

    # ------------------------------------------------------------------
    def _trusted_anchors(self) -> set[int]:
        """Anchors still entitled to act as a reference.

        Deliberately *not* ``cfg.mesh.certified_anchors``: an anchor whose own
        claimed position is contradicted by everybody else's ranging has been
        demoted by ``ClaimVerifier._check_anchors``, because attestation
        proves an airframe's identity and not the truth of its sensor data.
        Every privilege the badge confers - the relaxed verified quorum, the
        power to confer defensibility, immunity from expulsion - reads from
        here, so a spoofed anchor loses all of them together rather than
        keeping some by accident.
        """
        if self.verifier is None:
            return set(self.cfg.mesh.certified_anchors)
        return set(self.verifier.trusted_anchors)

    # ------------------------------------------------------------------
    def _penalise_refuted(self) -> None:
        """Make a refuted accusation cost the accuser.

        Being out-voted costs a Byzantine accuser nothing - it just tries
        again next round.  Being *refuted* is different in kind: the swarm has
        shown, from the accuser's own broadcasts and from geometry it does not
        control, that the claim was false.  Charging credibility for that is
        what converts fabrication from a free action into a losing one, and it
        compounds: an accuser that keeps fabricating keeps paying, and its
        vote weight decays toward ``cred_floor`` whatever bloc it belongs to.
        """
        if not self.cfg.claimcheck.penalise_refuted:
            return
        offenders: set[int] = set()
        for audit in self.audits.values():
            offenders.update(audit.refuted_accusers)
        if offenders:
            self.credibility.reject(sorted(offenders))

    # ------------------------------------------------------------------
    def _self_suspicion(self, reports: dict[int, dict[int, float]], live: list[int]) -> dict[int, float]:
        """Unweighted mean suspicion held against each node - a bootstrap.

        Used only to damp the vote of nodes already under broad suspicion;
        the exclusion decision itself always uses the weighted tally.
        """
        out: dict[int, float] = {}
        for node in live:
            vals = [
                float(reports.get(i, {}).get(node, 0.0)) for i in live if i != node
            ]
            out[node] = float(np.mean(vals)) if vals else 0.0
        return out

    # ------------------------------------------------------------------
    def _decide(
        self, tick: int, tallies: dict[int, VoteTally], live: list[int]
    ) -> list[ExclusionEvent]:
        events: list[ExclusionEvent] = []
        for target, tally in tallies.items():
            # A *trusted* anchor is never a quorum target - a hardware root
            # of trust cannot be voted off by its peers.  A demoted one can:
            # once the swarm's own ranging has contradicted its claimed
            # position for several rounds running, the badge no longer shields
            # it, and it is judged on the same evidence as anybody else.
            if target in self._trusted_anchors():
                continue
            # Two ways to reach the grace window, and they are different in
            # kind.  ``tally.quorum_met`` is the vote: enough accusers, and a
            # weighted mean over the whole swarm above threshold.  The second
            # is the verified-evidence path, and it deliberately bypasses
            # both of those - because both are popularity measures and both
            # fail in the same direction exactly when they are needed.
            #
            # The accuser count cannot be met past the majority: producing a
            # verified claim requires honest independent geometry, so the
            # drones able to produce one are the honest ones, and there are
            # fewer of them than ``2f+1``.  The weighted mean fails for a
            # subtler reason - it averages over *every* peer, so a
            # compromised bloc that simply stays silent about its accomplices
            # (which is what collusion does) drags the mean down below
            # threshold no matter how damning the honest minority's evidence
            # is.  Silence is free and it dilutes.
            #
            # Anchor-underwritten verified claims do not need either gate: a
            # claim that survived independent re-derivation with an
            # incorruptible witness among its accusers is evidence on its own
            # terms, not an opinion to be averaged.  ``_verified_quorum``
            # only returns a relaxed requirement when that anchor is present,
            # so this path stays shut in every other configuration.
            audit = self.audits.get(target) if self.verifier is not None else None
            required = (
                self._verified_quorum(audit) if audit is not None else self.quorum_size
            )
            # The path opens whenever enough claims survived verification - it is
            # NOT conditional on an anchor relaxing the requirement.  Gating it
            # that way silently disabled verification at scale: measured at
            # n=25 with 5 of 25 compromised, the attackers had *16* supported
            # claims against a required 12 and nothing was expelled, because
            # without an anchor the only route left was the weighted mean -
            # and verification itself drags that mean down by zeroing the
            # claims of drones that had no usable geometry.  At n=9 almost
            # every peer can range every other, so the effect was invisible;
            # at n=25 the radio budget (``mesh.max_observers``) means most
            # peers cannot see a given target and the dilution is fatal.
            verified_met = (
                audit is not None
                and len(audit.supported_accusers) >= required
            )
            if tally.quorum_met or verified_met:
                self._sustain[target] += 1
            else:
                # Decay rather than reset.
                #
                # The grace window exists so a transient does not convict, and
                # a hard reset delivered that at n=9 where nearly every peer
                # ranges every other and the supporting set is stable. At n=25
                # it stops working: which peers have usable geometry on a
                # given target churns as the formation moves, so the evidence
                # arrives in bursts and a single gap throws away five rounds
                # of accumulated agreement. Measured, that was the difference
                # between catching 15 of 20 attackers and catching one - the
                # swarm was not failing to find them, it was failing to find
                # them all in the same instant, repeatedly.
                #
                # Decaying keeps the requirement itself untouched: a target
                # still needs the full quorum of verified claims, and an
                # honest drone attracts none of those, so this cannot convict
                # one. Sustained absence of evidence still walks the counter
                # back to zero.
                self._sustain[target] = max(0, self._sustain[target] - 1)
            tally.sustained = self._sustain[target]

            if tally.sustained < max(1, self.cn.exclusion_grace_ticks):
                continue
            # Liveness guard: never expel so many nodes that the swarm can no
            # longer form a quorum at all.  Refusing to act is the safer
            # failure here, and the event is logged for post-mission review.
            if len(live) - 1 < 3 * self.f_tolerated + 1 and len(live) <= 4:
                continue
            # Majority guard: never let cumulative exclusions reach a
            # majority of the original roster - see ``_majority_cap``.
            if len(self.excluded) >= self._majority_cap:
                continue
            # ClaimCheck gate.  Everything above this line is the majority
            # vote; this is the point where the decision stops being a head
            # count.  An expulsion needs ``quorum_size`` claims that survived
            # independent re-derivation - a number a bloc cannot inflate by
            # growing, because fabricated claims produce none of them.
            if self.verifier is not None:
                decision = self.safety.decide(
                    tick, target, audit or TargetAudit(target), required
                )
                if decision.blocks_expulsion:
                    # Record the refusal once per hold, not once per round.
                    # The vote keeps re-reaching quorum for as long as the
                    # accusations keep coming, so logging every pass would
                    # bury the flight recorder in hundreds of identical
                    # entries and make the count of "expulsions prevented"
                    # a measure of mission length rather than of decisions.
                    newly_held = self.safety.quarantine(tick, target, decision.reason)
                    if not newly_held:
                        self._sustain[target] = max(
                            1, self.cn.exclusion_grace_ticks
                        )
                        continue
                    record = {
                        "tick": tick,
                        "target": target,
                        "action": decision.action,
                        "reason": decision.reason,
                        "vote_weight": round(tally.weight, 4),
                        "vote_accusers": list(tally.accusers),
                        "supported": decision.supported,
                        "refuted": decision.refuted,
                        "quorum_required": decision.quorum_required,
                    }
                    self.blocked.append(record)
                    self.verifier.ledger.record_block(
                        tick, target, decision.reason, decision.detail
                    )
                    # Hold the sustain counter at the gate rather than
                    # resetting it: the geometry may become arbitrable again
                    # next round, and an honest expulsion should not have to
                    # re-earn its whole grace window because of a transient.
                    self._sustain[target] = max(1, self.cn.exclusion_grace_ticks)
                    continue

            event = ExclusionEvent(
                tick=tick,
                target=target,
                weight=tally.weight,
                accusers=list(tally.accusers),
                quorum_required=self.quorum_size,
                detail={
                    "triples": [[a, round(w, 4), round(s, 4)] for a, w, s in tally.triples],
                    "trimmed": tally.trimmed,
                    "f_tolerated": self.f_tolerated,
                    "quorum_weight_threshold": self.cn.quorum_weight_threshold,
                    "sustained_rounds": tally.sustained,
                    "claimcheck": (
                        None
                        if self.verifier is None
                        else {
                            "verified_accusers": decision.supported,
                            "refuted_accusers": decision.refuted,
                            "verdicts": decision.detail.get("verdicts", []),
                            "geometry": self.verifier.geometry.as_row(),
                        }
                    ),
                },
            )
            self.excluded[target] = tick
            self.events.append(event)
            events.append(event)
            self._sustain[target] = 0
            self.safety.quarantined.pop(target, None)
            # The swarm agreed with these accusers; the expelled node's own
            # accusations are retroactively discounted.
            self.credibility.uphold(tally.accusers)
            self.credibility.reject([target])
        return events

    # ------------------------------------------------------------------
    def _penalise_dissent(
        self, tallies: dict[int, VoteTally], live: list[int]
    ) -> dict[int, set[int]]:
        """Discount nodes that keep accusing peers nobody else doubts.

        This is what makes a Byzantine accuser pay for lying: its accusations
        are persistent outliers against a swarm-wide consensus of innocence,
        and each round of that erodes the weight of its own vote.

        Returns, per accuser, the set of distinct targets it accused this
        round that were *uncorroborated* (below the same ``floor``) - the
        raw material ``_track_fabrication`` uses to catch the accuser
        itself, not just discount its vote.
        """
        floor = 0.25 * self.cn.quorum_weight_threshold
        round_targets: dict[int, set[int]] = defaultdict(set)
        for target, tally in tallies.items():
            if tally.weight >= floor:
                continue
            for accuser in tally.accusers:
                self._dissent[accuser] += 1.0
                if self._dissent[accuser] >= 8.0:
                    self._dissent[accuser] = 0.0
                    self.credibility.reject([accuser])
                round_targets[accuser].add(target)
        return round_targets

    # ------------------------------------------------------------------
    def _track_fabrication(
        self,
        tick: int,
        round_targets: dict[int, set[int]],
        live: list[int],
        just_excluded: set[int],
    ) -> list[ExclusionEvent]:
        """Catch a fabricating accuser directly, by breadth not persistence.

        See ``ConsensusConfig.fabrication_breadth_threshold`` for the full
        design rationale.  In short: a leaky integrator per accuser fed by
        ``len(round_targets[accuser])`` each round - the count of distinct,
        uncorroborated victims it accused *this* round.  A real fabricator
        (``byzantine_accuser``) keeps that count high every round it is
        active and the integrator races to a high steady state; an honest
        drone narrowly, correctly suspecting the one real attacker only ever
        contributes on a single target and decays back down once the rest
        of the swarm corroborates.  Guarded by the same liveness floor as
        normal exclusions, plus a hard cooldown that limits this path to
        excluding at most one drone (the worst offender) per window, so a
        transient swarm-wide confusion event cannot cascade.
        """
        decay = self.cn.fabrication_breadth_decay
        for accuser in live:
            if accuser in just_excluded:
                continue
            named = round_targets.get(accuser, ())
            if named:
                self._fab_named_targets[accuser] |= set(named)
            breadth = decay * self._fab_breadth[accuser] + len(named)
            self._fab_breadth[accuser] = breadth
            if breadth >= self.cn.fabrication_breadth_threshold:
                self._fab_sustain[accuser] += 1
            else:
                self._fab_sustain[accuser] = 0

        # Liveness guard: identical to the normal exclusion path - never
        # expel so many nodes the swarm can no longer form a quorum at all.
        if len(live) - 1 < 3 * self.f_tolerated + 1 and len(live) <= 4:
            return []
        # Majority guard: identical to the normal exclusion path - see
        # ``_majority_cap``.
        if len(self.excluded) >= self._majority_cap:
            return []
        # Circuit breaker: at most one fabrication-exclusion per cooldown
        # window, regardless of how many accusers currently qualify.
        if self.rounds < self._fab_cooldown_until:
            return []
        # Premise guard.  This detector assumes at most ``f`` Byzantine nodes;
        # once it has already removed that many drones, every further
        # expulsion is being made outside the model it is derived from.  It
        # has now inverted under a compromised majority four separate times
        # during development, each time because it was reading a signal a
        # bloc can manufacture, so it gets a hard ceiling rather than another
        # heuristic: past ``f_tolerated`` cumulative exclusions it stops.
        if len(self.excluded) >= self.f_tolerated:
            return []

        candidates = [
            a for a in live
            if a not in just_excluded
            and a not in self._trusted_anchors()
            and self._fab_sustain[a] >= self.cn.fabrication_grace_rounds
        ]
        if self.verifier is not None:
            # Only a drone inside the swarm's own self-consistent picture of
            # itself can be expelled as a fabricator.
            #
            # This detector was the last majority-derived mechanism left
            # ungated, and it inverts past the bound exactly like the quorum
            # does.  Feeding it refutations instead of uncorroborated claims
            # was not enough: with a forging majority the refutations are
            # themselves derived from the liars' world, so the honest minority
            # still accumulated breadth and was still expelled (measured -
            # ``collusion``, 5 of 9, seed 7: drones 5, 8, 1, 2, all honest,
            # all via this path, before and after that change).
            #
            # Membership of the dominant reciprocity world is the signal that
            # does not invert, because it is about *ranges* rather than about
            # accusations.  A gossip-only fabricator flies and ranges
            # honestly, so it always sits inside that world - that is what
            # makes it invisible to every other channel, and here it is what
            # convicts it.  An honest drone in a swarm whose majority is
            # forging sits outside it, and becomes unexpellable by this path
            # for precisely the reason it was in danger: its measurements
            # disagree with the bloc's.
            dominant = self.verifier.geometry.dominant
            if dominant:
                candidates = [a for a in candidates if a in dominant]
        if not candidates:
            return []

        worst = max(candidates, key=lambda a: self._fab_breadth[a])
        event = ExclusionEvent(
            tick=tick,
            target=worst,
            weight=self._fab_breadth[worst],
            accusers=[],
            quorum_required=0,
            reason="fabrication_breadth",
            detail={
                "named_targets": sorted(self._fab_named_targets.get(worst, ())),
                "sustained_rounds": self._fab_sustain[worst],
                "breadth_threshold": self.cn.fabrication_breadth_threshold,
                "breadth_decay": self.cn.fabrication_breadth_decay,
            },
        )
        self.excluded[worst] = tick
        self.events.append(event)
        self._fab_sustain[worst] = 0
        self._fab_breadth[worst] = 0.0
        self._fab_cooldown_until = self.rounds + self.cn.fabrication_cooldown_rounds
        self.credibility.reject([worst])
        return [event]

    # ------------------------------------------------------------------
    def is_excluded(self, drone: int) -> bool:
        return drone in self.excluded

    def live_drones(self) -> list[int]:
        return sorted(d for d in self.active if d not in self.excluded)

    def snapshot(self) -> dict:
        return {
            "round": self.rounds,
            "f_tolerated": self.f_tolerated,
            "quorum_size": self.quorum_size,
            "excluded": dict(self.excluded),
            "quarantined": dict(self.safety.quarantined),
            "tallies": [t.as_row() for t in self.tallies.values()],
            "credibility": self.credibility.table(),
            "claimcheck": (
                None
                if self.verifier is None
                else {
                    "enabled": True,
                    "geometry": self.verifier.geometry.as_row(),
                    "blocked": len(self.blocked),
                    **self.verifier.ledger.summary(),
                }
            ),
        }
