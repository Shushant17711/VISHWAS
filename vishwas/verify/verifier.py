"""ClaimCheck - the independent Mathematical Claim Verifier.

Everything the consensus layer does with an accusation before this module
existed was *count* it, weighted.  Counting is a majority mechanism, and a
majority mechanism sides with whichever bloc is larger by construction: once
the compromised drones outnumber the honest ones they can vote the honest
minority out of the swarm, and no amount of trimming, credibility weighting
or quorum sizing changes that (measured, ``scratch/diag24_few_honest.py``).

The verifier's job is to make an accusation something that can be *checked*
rather than merely counted.  For every claim it asks three questions, all of
them arithmetic on data the swarm already broadcast:

1. **Did the accuser cite anything?**  A bare assertion with no evidence
   payload is UNSUPPORTED - never punished (an honest drone with no usable
   geometry is in exactly that position) and never counted.
2. **Does the accuser's own published ranging back it up?**  A
   ``RangeReportMsg`` is a public commitment.  An accuser whose own broadcast
   range reproduces the target's claimed position, while it asserts a
   multi-sigma anomaly, has contradicted itself in public.
3. **Does anybody else's geometry agree?**  The target's position is re-fixed
   from every *other* drone's ranges, with the accuser excluded from its own
   alibi.  If that independent fix lands on the target's claim, the
   accusation is REFUTED by physics rather than out-voted.

And one question about the evidence as a whole, asked once per round:

4. **Is the swarm's geometry even arbitrable?**  Reciprocity
   (``|r_ij - r_ji|``) partitions the swarm into internally-consistent
   accounts of where everyone is.  When the discarded account is larger than
   the Byzantine budget, two mutually-contradictory worlds both satisfy the
   physics, and range data alone cannot say which is real - picking the bigger
   one would be the same majority vote this package exists to avoid.  Every
   claim then resolves AMBIGUOUS, and the safety layer quarantines instead of
   expelling.  A certified anchor (``MeshConfig.certified_anchors``) is a
   reference from outside the vote and resolves the split when present.

The honest scope of the guarantee: ClaimCheck cannot make the swarm *detect*
attackers past the point where it can no longer trust its own measurements.
What it does is stop the swarm from convicting the innocent there - the
failure mode changes from "expels the honest minority" to "declines to expel
anyone until the geometry is trustworthy again".
"""

from __future__ import annotations

import numpy as np

from ..config import SimConfig
from .claim import Accusation, ClaimBundle, ClaimEvidence
from .independence import collapse_duplicates
from .ledger import ClaimLedger
from .recompute import (
    Geometry,
    Rederivation,
    assess_geometry,
    commitment_residual,
    independent_fix,
)
from .verdict import Finding, TargetAudit, Verdict


class ClaimVerifier:
    """Audits accusations against independently re-derived physics."""

    def __init__(self, cfg: SimConfig, rng: np.random.Generator | None = None) -> None:
        self.cfg = cfg
        self.cc = cfg.claimcheck
        self.ev = cfg.evidence
        self.mesh = cfg.mesh
        self.rng = rng if rng is not None else np.random.default_rng(cfg.seed + 977)
        self.ledger = ClaimLedger()
        self.geometry = Geometry()
        #: Anchors whose own claimed position is contradicted by everyone
        #: else's ranging - attested hardware reporting spoofed sensor data.
        self.spoofed_anchors: set[int] = set()
        #: Anchors still entitled to act as a reference this round.
        self.trusted_anchors: frozenset[int] = frozenset()
        self._anchor_contradicted: dict[int, int] = {}

    # ------------------------------------------------------------------
    def audit(
        self,
        bundle: ClaimBundle,
        reports: dict[int, dict[int, float]],
        live: list[int],
        f_tolerated: int,
        accuse_threshold: float,
    ) -> dict[int, TargetAudit]:
        """Audit every accusation in ``reports`` that crosses the threshold."""
        # An anchor's privilege is conditional on its data, never on its
        # badge.  Check it first, before anything downstream leans on it.
        self._check_anchors(bundle, live)
        bundle.anchors = self.trusted_anchors
        self.geometry = assess_geometry(bundle, live, self.cc, f_tolerated)

        by_target: dict[int, list[Accusation]] = {}
        for accuser, table in reports.items():
            if accuser not in live:
                continue
            for target, score in table.items():
                if target not in live or target == accuser:
                    continue
                if float(score) < accuse_threshold:
                    continue
                by_target.setdefault(target, []).append(
                    Accusation(
                        accuser=accuser,
                        target=target,
                        score=float(score),
                        evidence=bundle.evidence_for(accuser, target),
                    )
                )

        audits: dict[int, TargetAudit] = {}
        for target, accusations in by_target.items():
            audit = self._audit_target(bundle, target, accusations)
            audits[target] = audit
            self.ledger.record(bundle.tick, audit)
        return audits

    # ------------------------------------------------------------------
    def _check_anchors(self, bundle: ClaimBundle, live: list[int]) -> None:
        """Withdraw reference status from an anchor the physics contradicts.

        A certified anchor is trusted because a hardware root of trust proves
        the airframe is the one that was shipped, running signed firmware.
        That is a claim about *identity*, and this method exists because the
        system must not quietly upgrade it into a claim about *truth*.

        GNSS spoofing is the case that separates the two: feed a counterfeit
        constellation to an attested drone and it broadcasts a false position
        in good faith, over an authenticated link, from unmodified firmware,
        with a valid attestation chain.  Every hardware check passes.  Treating
        attestation as a warrant for the data would then let one spoofed
        receiver drag the whole swarm's geometry with it - and it would do the
        most damage precisely where the anchor is most useful, because
        ``e2_cross`` shrinks an anchor's contributed sigma by a large factor
        and lets it dominate any fit it joins.

        So the anchor is audited exactly like everybody else: re-derive its
        position from every *other* drone's published ranges and compare
        against what it claims.  Peer ranging is time-of-flight and never
        consults GNSS, so a spoofed anchor is still measured correctly by its
        neighbours even while it is wrong about itself - the disagreement is
        visible, and it is visible in measurements the spoofer does not
        control.  Sustained contradiction withdraws the reference status; a
        single bad round does not, because geometry is noisy and demoting the
        one trustworthy reference on a transient is its own failure.

        The airframe is not written off - it keeps flying and keeps being
        measured.  What it loses is the *authority* its badge conferred.
        """
        anchors = {a for a in bundle.anchors if a in live}
        if not anchors:
            # No anchor is flying this round - but a demotion already earned
            # must survive that.  Recomputing trust from ``bundle.anchors``
            # alone would silently reinstate a spoofed anchor the moment it
            # dropped out of ``live`` for a round, which is exactly the state
            # it is in right after being acted on.
            self.trusted_anchors = frozenset(
                a for a in bundle.anchors if a not in self.spoofed_anchors
            )
            return

        for anchor in sorted(anchors):
            fix = independent_fix(
                bundle,
                anchor,
                exclude={anchor},
                ev=self.ev,
                mesh=self.mesh,
                rng=np.random.default_rng(
                    hash((self.cfg.seed, bundle.tick, anchor, -1)) & 0xFFFF_FFFF
                ),
            )
            contradicted = fix.usable and fix.z >= self.cc.support_z
            if contradicted:
                self._anchor_contradicted[anchor] = (
                    self._anchor_contradicted.get(anchor, 0) + 1
                )
            else:
                self._anchor_contradicted[anchor] = 0
            if self._anchor_contradicted[anchor] >= self.cc.anchor_demote_rounds:
                if anchor not in self.spoofed_anchors:
                    self.spoofed_anchors.add(anchor)
                    self.ledger.record_block(
                        bundle.tick,
                        anchor,
                        "certified anchor demoted: its own claimed position is "
                        "contradicted by independent ranging from the rest of "
                        "the swarm - attested hardware, spoofed sensor data",
                        {"independent_z": round(fix.z, 2),
                         "observers": fix.n_observers},
                    )
        self.trusted_anchors = frozenset(
            a for a in bundle.anchors if a not in self.spoofed_anchors
        )

    # ------------------------------------------------------------------
    def _audit_target(
        self, bundle: ClaimBundle, target: int, accusations: list[Accusation]
    ) -> TargetAudit:
        geo = self.geometry
        audit = TargetAudit(target=target)
        audit.contested = geo.contested
        audit.anchor_backed = geo.anchor_backed
        audit.agreeing_fraction = geo.consistency.get(target, 1.0)

        duplicates = collapse_duplicates(accusations, self.cc.duplicate_tolerance)

        # The re-derivation is a RANSAC multilateration per claim, and under a
        # broad fabrication attack there are ``n * (n-1)`` claims a round - so
        # the one case where the work is provably redundant is worth skipping.
        # Excluding an accuser that contributed no range to this target
        # removes nothing from the anchor set, so the fix is identical to the
        # unexcluded one and is computed once and shared.
        contributors = set(bundle.observers_of(target))
        shared: Rederivation | None = None
        for acc in sorted(accusations, key=lambda a: a.accuser):
            if geo.contested:
                # Contested geometry decides every claim on its own, so the
                # fix would be computed and then discarded - and this is
                # precisely the regime with the most claims to audit.
                fix = Rederivation(target=target)
            elif acc.accuser in contributors:
                fix = self._fix(bundle, target, exclude={acc.accuser})
            else:
                if shared is None:
                    shared = self._fix(bundle, target, exclude=set())
                fix = shared
            finding = self._audit_claim(bundle, acc, geo, fix)
            finding.duplicate_of = duplicates.get(acc.accuser)
            if finding.duplicate_of is not None and finding.verdict is Verdict.SUPPORTED:
                finding.reason = (
                    f"{finding.reason}; collapsed into accuser "
                    f"{finding.duplicate_of}'s identical evidence"
                )
            audit.findings.append(finding)
        return audit

    # ------------------------------------------------------------------
    def _fix(
        self, bundle: ClaimBundle, target: int, exclude: set[int]
    ) -> Rederivation:
        """Re-derive ``target``'s position, deterministically.

        RANSAC draws random minimal samples, so a single shared generator
        would make each fix depend on how many fits happened to run before it -
        and therefore on the order claims arrived and on how many of them
        there were.  A verifier whose answer depends on the order it was asked
        is not reproducible, and reproducibility is most of what makes an
        audit trail worth keeping: any drone replaying the same broadcasts has
        to reach the same verdicts.  Seeding per (round, target, excluded set)
        gives every fix its own stream, so an audit is a pure function of the
        commitments it read.
        """
        seed = hash(
            (self.cfg.seed, bundle.tick, target, tuple(sorted(exclude)))
        ) & 0xFFFF_FFFF
        return independent_fix(
            bundle,
            target,
            exclude=exclude,
            ev=self.ev,
            mesh=self.mesh,
            rng=np.random.default_rng(seed),
        )

    # ------------------------------------------------------------------
    def _audit_claim(
        self, bundle: ClaimBundle, acc: Accusation, geo: Geometry, fix: Rederivation
    ) -> Finding:
        cc = self.cc
        evidence: ClaimEvidence = acc.evidence
        finding = Finding(
            accuser=acc.accuser,
            target=acc.target,
            verdict=Verdict.UNSUPPORTED,
            score=acc.score,
            cited_z=evidence.cited_magnitude,
            anchor_backed=geo.anchor_backed,
            agreeing_fraction=geo.consistency.get(acc.target, 1.0),
        )

        # -- 1. claim-to-evidence binding --------------------------------
        if not evidence.present or evidence.cited_magnitude <= 0.0:
            finding.reason = "accuser cited no measurement for this target"
            return finding

        # -- 2. commitment audit -----------------------------------------
        own = commitment_residual(bundle, acc.accuser, acc.target)
        finding.commitment_z = own

        # -- 3. independent re-derivation --------------------------------
        # Computed by the caller, with the accuser excluded from the evidence
        # that would vindicate its own accusation.  Nothing else is excluded:
        # dropping the accuser's co-accusers as well would gut honest
        # detection, because in a normal (sub-Byzantine) round the co-accusers
        # *are* the honest majority.
        finding.independent_observers = fix.n_observers
        finding.anchor_backed = finding.anchor_backed or fix.anchor_backed
        if fix.usable:
            finding.independent_z = fix.z

        # -- 4. is the geometry arbitrable at all? -----------------------
        if geo.contested:
            finding.verdict = Verdict.AMBIGUOUS
            finding.reason = (
                "swarm range reports split into "
                f"{len(geo.dominant)} vs {len(geo.outliers)} mutually "
                "inconsistent worlds; range geometry cannot arbitrate without "
                "a reference outside the swarm"
            )
            return finding

        # -- synthesis ----------------------------------------------------
        if fix.usable:
            if fix.z <= cc.refute_z:
                finding.verdict = Verdict.REFUTED
                if own is not None and own <= cc.commitment_z:
                    finding.reason = (
                        f"independent fix from {fix.n_observers} peers places the "
                        f"target within {fix.z:.1f} sigma of its claim, and the "
                        f"accuser's own published range agrees ({own:.1f} sigma) "
                        "while it asserts an anomaly"
                    )
                else:
                    finding.reason = (
                        f"independent fix from {fix.n_observers} peers places the "
                        f"target within {fix.z:.1f} sigma of its claim"
                    )
            elif fix.z >= cc.support_z:
                finding.verdict = Verdict.SUPPORTED
                finding.reason = (
                    f"independent fix from {fix.n_observers} peers contradicts the "
                    f"target's claim by {fix.z:.1f} sigma"
                )
            else:
                finding.verdict = Verdict.AMBIGUOUS
                finding.reason = (
                    f"independent fix inconclusive at {fix.z:.1f} sigma "
                    f"(refute below {cc.refute_z}, support above {cc.support_z})"
                )
            return finding

        # No usable independent geometry.  The commitment audit is the only
        # check left, and it can only ever refute - an accuser contradicting
        # its own broadcast is decisive, an accuser merely being unverifiable
        # is not.
        if own is not None and own <= cc.commitment_z and evidence.cited_magnitude >= cc.support_z:
            finding.verdict = Verdict.REFUTED
            finding.reason = (
                f"accuser's own published range reproduces the target's claim to "
                f"{own:.1f} sigma while its gossiped evidence asserts "
                f"{evidence.cited_magnitude:.1f} sigma"
            )
            return finding

        finding.verdict = Verdict.AMBIGUOUS
        finding.reason = (
            f"only {fix.n_observers} independent observers; "
            f"{max(3, self.ev.e2_min_observers)} needed to re-derive the fix"
        )
        return finding
