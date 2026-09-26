"""The Safety Decision Engine - what to actually *do* about a verdict.

Expulsion is irreversible in this system: an expelled drone is commanded home,
its sector is reallocated, its detections are retracted from the shared
picture, and nothing brings it back.  That is the right cost for a confirmed
attacker and completely the wrong cost for a wrongly-accused honest airframe,
which is a live aircraft with a mission to fly.

So the action map is deliberately asymmetric.  Only one verdict can ever
remove a drone from the swarm, and every uncertain outcome resolves to
*quarantine* instead: a reversible state that flags the drone as unresolved
and blocks its expulsion, while it keeps flying, keeps being measured, and
keeps its own vote.  Silencing a quarantined drone's accusations would be a
mistake with a name: it would hand an attacker a way to mute honest accusers
by getting them held, which is the same wrongful-removal attack this package
exists to stop, one step removed.  Quarantine costs the swarm a little
certainty if it is wrong; expulsion costs it an airframe.

======================  ==================================================
Verdict                 Action
======================  ==================================================
``SUPPORTED``           counts toward quorum; expulsion may proceed
``REFUTED``             claim dropped; the accuser pays credibility
``UNSUPPORTED``         claim dropped; no penalty either way
``AMBIGUOUS``           claim kept in the tally, but cannot expel - the
                        target is quarantined and released when independent
                        evidence stops arriving
======================  ==================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import ClaimCheckConfig
from .verdict import TargetAudit


@dataclass
class SafetyDecision:
    """What the consensus layer should do about one target this round."""

    target: int
    action: str                     # "expel" | "quarantine" | "hold"
    reason: str = ""
    supported: list[int] = field(default_factory=list)
    refuted: list[int] = field(default_factory=list)
    quorum_required: int = 0
    detail: dict = field(default_factory=dict)

    @property
    def blocks_expulsion(self) -> bool:
        return self.action != "expel"


@dataclass
class SafetyEngine:
    """Holds the reversible quarantine state and maps verdicts to actions."""

    cc: ClaimCheckConfig
    #: target -> tick quarantined
    quarantined: dict[int, int] = field(default_factory=dict)
    #: target -> rounds since the last supported claim against it
    _idle: dict[int, int] = field(default_factory=dict)
    #: append-only log of quarantine entries and releases, for the dashboard
    events: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------
    def decide(
        self,
        tick: int,
        target: int,
        audit: TargetAudit,
        quorum_size: int,
    ) -> SafetyDecision:
        """Rule on one target whose weighted tally already met quorum.

        The count that matters here is *supported, independent* claims - not
        accusers.  A bloc of any size produces at most one supported claim per
        member that survives the audit, and a bloc that fabricates produces
        none, so this count cannot be inflated by adding liars.
        """
        supported = audit.supported_accusers
        refuted = audit.refuted_accusers
        detail = {
            "supported": supported,
            "refuted": refuted,
            "contested": audit.contested,
            "anchor_backed": audit.anchor_backed,
            "verdicts": [f.as_row() for f in audit.findings],
        }

        if audit.contested:
            return SafetyDecision(
                target=target,
                action="quarantine",
                reason=(
                    "range reports split into mutually inconsistent worlds; "
                    "no expulsion without a reference outside the swarm"
                ),
                supported=supported,
                refuted=refuted,
                quorum_required=quorum_size,
                detail=detail,
            )

        if len(supported) >= quorum_size:
            return SafetyDecision(
                target=target,
                action="expel",
                reason=(
                    f"{len(supported)} independently re-derived claims survived "
                    f"verification (quorum {quorum_size})"
                ),
                supported=supported,
                refuted=refuted,
                quorum_required=quorum_size,
                detail=detail,
            )

        return SafetyDecision(
            target=target,
            action="quarantine",
            reason=(
                f"only {len(supported)} of {len(audit.findings)} claims survived "
                f"independent verification; {quorum_size} needed to expel"
            ),
            supported=supported,
            refuted=refuted,
            quorum_required=quorum_size,
            detail=detail,
        )

    # ------------------------------------------------------------------
    def quarantine(self, tick: int, target: int, reason: str) -> bool:
        """Place ``target`` in the reversible hold.  True if newly held."""
        self._idle[target] = 0
        if target in self.quarantined:
            return False
        self.quarantined[target] = tick
        self.events.append(
            {"tick": tick, "target": target, "kind": "quarantine", "reason": reason}
        )
        return True

    def sustain(self, held: set[int]) -> list[int]:
        """Age the quarantine set; return targets released this round.

        A drone stays held only while accusations against it keep arriving.
        Silence is what earns release: an honest airframe that was quarantined
        during a contested round returns to full standing once the swarm's
        geometry settles and nobody is claiming anything about it any more.
        """
        released: list[int] = []
        for target in list(self.quarantined):
            if target in held:
                self._idle[target] = 0
                continue
            self._idle[target] = self._idle.get(target, 0) + 1
            if self._idle[target] >= self.cc.quarantine_release_rounds:
                released.append(target)
        for target in released:
            self.quarantined.pop(target, None)
            self._idle.pop(target, None)
            self.events.append({"target": target, "kind": "release"})
        return released

    def is_quarantined(self, target: int) -> bool:
        return target in self.quarantined
