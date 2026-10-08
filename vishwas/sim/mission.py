"""Mission model: formation transit, sector search and target triangulation.

The mission exists for two reasons.  First, it gives Evidence channel E3
something to check against - a drone has an assigned role, and behaviour that
does not match the role is weak but real evidence.  Second, it gives the
evaluation a *mission success rate*, which is the number that actually matters
to an operator: not "did we detect the traitor" but "did we still complete the
task".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..config import MissionConfig


# --------------------------------------------------------------------------
@dataclass
class Target:
    target_id: str
    position: np.ndarray
    confirmed_by: set[int] = field(default_factory=set)
    confirmed_tick: int | None = None

    @property
    def confirmed(self) -> bool:
        return self.confirmed_tick is not None


@dataclass
class Sector:
    """A rectangular slice of the search area assigned to one drone."""

    index: int
    x0: float
    y0: float
    x1: float
    y1: float
    grid: np.ndarray                       # bool coverage grid
    cell: float

    @property
    def centre(self) -> np.ndarray:
        return np.array([(self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2, 0.0])

    @property
    def coverage(self) -> float:
        return float(self.grid.mean()) if self.grid.size else 1.0

    def nearest(self, position: np.ndarray) -> np.ndarray:
        """Closest point of the rectangle to ``position`` (itself, if inside).

        E3 asks "is this drone working the sector it was given", not "is it
        standing on the sector's centre point".  A lawnmower sweep legitimately
        visits every corner, so the centre is the wrong reference and the
        rectangle is the right one.
        """
        return np.array(
            [
                float(np.clip(position[0], self.x0, self.x1)),
                float(np.clip(position[1], self.y0, self.y1)),
                float(position[2]) if len(position) > 2 else 0.0,
            ]
        )

    def mark(self, position: np.ndarray, radius: float) -> None:
        gx = int((position[0] - self.x0) / self.cell)
        gy = int((position[1] - self.y0) / self.cell)
        span = max(1, int(radius / self.cell))
        h, w = self.grid.shape
        x_lo, x_hi = max(0, gx - span), min(w, gx + span + 1)
        y_lo, y_hi = max(0, gy - span), min(h, gy + span + 1)
        if x_lo < x_hi and y_lo < y_hi:
            self.grid[y_lo:y_hi, x_lo:x_hi] = True

    def lawnmower(self, altitude: float, legs: int = 5) -> list[np.ndarray]:
        """Boustrophedon sweep waypoints covering the sector."""
        pts: list[np.ndarray] = []
        ys = np.linspace(self.y0 + 40, self.y1 - 40, legs)
        for i, y in enumerate(ys):
            xs = [self.x0 + 40, self.x1 - 40]
            if i % 2:
                xs.reverse()
            for x in xs:
                pts.append(np.array([x, y, altitude]))
        return pts


# --------------------------------------------------------------------------
class Mission:
    """Owns sector assignment, formation geometry and target confirmation."""

    def __init__(
        self, cfg: MissionConfig, n_drones: int, rng: np.random.Generator
    ) -> None:
        self.cfg = cfg
        self.n = n_drones
        self.rng = rng
        self.cell = 60.0
        self.sectors: list[Sector] = self._build_sectors(n_drones)
        self.targets: list[Target] = self._build_targets()
        # drone -> sector index, drone -> formation slot index
        self.sector_of: dict[int, int] = {i: i for i in range(n_drones)}
        # drone -> every sector it is answerable for, current one first.  A
        # survivor that inherits a dead drone's box does not abandon its own:
        # it finishes the pass it is flying and then rotates onto the next.
        # Sweeping two boxes at half rate is graceful degradation; dropping
        # one of them silently would be a hole in the search the swarm never
        # reports.
        self.sector_queue: dict[int, list[int]] = {i: [i] for i in range(n_drones)}
        self.slot_of: dict[int, int] = {i: i for i in range(n_drones)}
        self.reallocations: list[dict] = []
        self.phase = "formation"          # "formation" -> "search"
        self.formation_ticks = 140
        self.dt = 0.2                     # overwritten by the swarm loop
        # Tick at which each drone's current sector became its responsibility.
        # E3 gives a drone a transit allowance measured from this instant, so a
        # drone that is merely *on its way* to a freshly assigned sector is not
        # accused of abandoning it.  Public plan data: every honest drone can
        # reproduce the same allowance without trusting anyone's telemetry.
        self._assigned_tick: dict[int, int] = {i: self.formation_ticks for i in range(n_drones)}
        # drone -> (tick, metres) the last formation-slot re-pack asked it to fly.
        # A re-pack is a new order just like a sector hand-over, and it carries
        # the same decaying, plan-derived travel credit.
        self._slot_credit: dict[int, tuple[int, float]] = {}
        # drone -> report_id it has been dispatched to corroborate, and the
        # reported location it is flying to (which may be a fabrication).
        self.tasks: dict[int, str] = {}
        self.task_pos: dict[int, np.ndarray] = {}
        # The shared detection picture, keyed by report id.  The swarm has no
        # oracle: a fabricated report lives in here exactly like a real one.
        self.report_observers: dict[str, set[int]] = {}
        self.report_position: dict[str, np.ndarray] = {}
        self.report_first: dict[str, int] = {}
        self.dropped_reports: set[str] = set()
        # A report nobody can corroborate within this many seconds of flying
        # out to look at it is abandoned.  Without it, one phantom pins the
        # swarm to an empty patch of ground forever - the cheapest possible
        # denial-of-service against a corroboration rule.
        self.confirm_timeout_s: float = 150.0
        self.max_confirm_drones: int = max(1, n_drones // 2)

    # ----------------------------------------------------------- geometry
    def _build_sectors(self, count: int) -> list[Sector]:
        size = self.cfg.area_size
        cols = int(np.ceil(np.sqrt(count)))
        rows = int(np.ceil(count / cols))
        w, h = size / cols, size / rows
        sectors: list[Sector] = []
        # The search box is centred on the launch point, so the transit from
        # HOME into the sectors is a realistic few tens of seconds rather than
        # a diagonal crossing that eats the whole simulation.
        ox, oy = -size / 2.0, -size / 2.0
        for idx in range(count):
            r, c = divmod(idx, cols)
            x0, y0 = ox + c * w, oy + r * h
            gw = max(1, int(w / self.cell))
            gh = max(1, int(h / self.cell))
            sectors.append(
                Sector(idx, x0, y0, x0 + w, y0 + h, np.zeros((gh, gw), dtype=bool), self.cell)
            )
        return sectors

    def _build_targets(self) -> list[Target]:
        size = self.cfg.area_size
        out = []
        for k in range(self.cfg.n_targets):
            pos = np.array(
                [
                    self.rng.uniform(-0.38 * size, 0.38 * size),
                    self.rng.uniform(-0.38 * size, 0.38 * size),
                    0.0,
                ]
            )
            out.append(Target(f"T{k + 1}", pos))
        return out

    def formation_slot(self, slot: int, leader: np.ndarray, heading: float) -> np.ndarray:
        """Vee-formation slot position relative to the leader."""
        spacing = self.cfg.formation_spacing
        if slot == 0:
            offset = np.zeros(3)
        else:
            rank = (slot + 1) // 2
            side = -1.0 if slot % 2 == 0 else 1.0
            offset = np.array([-rank * spacing * 0.9, side * rank * spacing * 0.6, 0.0])
        c, s = np.cos(heading), np.sin(heading)
        rot = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
        return leader + rot @ offset

    # -------------------------------------------------------------- update
    def expected_position(
        self,
        drone: int,
        tick: int,
        leader: np.ndarray,
        heading: float,
        claimed: np.ndarray | None = None,
    ) -> tuple[np.ndarray, float]:
        """Where the mission plan says ``drone`` should be, and the tolerance.

        Returned tolerance differs by phase: formation flight is tight,
        free sector search is loose.  E3 is deliberately weak evidence.

        During the join transient the tolerance is wide and shrinks to its
        steady-state value across the formation phase.  A drone that launched
        200 m from its slot cannot be in it on tick 1, and an E3 channel that
        punished it for that would convict the entire swarm before any
        attacker had even started lying.

        In the search phase the reference is the *nearest point of the assigned
        sector*, not its centre.  A boustrophedon sweep spends nearly all its
        time away from the centre; scoring against the centre would charge
        every honest searcher a permanent residual and hand E3 a swarm-wide
        false-accusation floor.  What E3 actually asks here is the honest
        question: are you inside the box you were given?
        """
        if self.phase == "formation":
            slot = self.slot_of.get(drone, drone)
            frac = float(np.clip(tick / max(1, self.formation_ticks), 0.0, 1.0))
            tol = self.cfg.formation_spacing * 6.0 * (1.0 - frac) + self.cfg.waypoint_tolerance * frac
            # Expelling a drone re-packs the vee, and every drone behind the hole
            # is told to slide up.  Without credit for that ordered move, one
            # justified exclusion cascades into a swarm-wide purge of the
            # honest drones that obeyed it.
            tol += self._slot_allowance(drone, tick)
            return self.formation_slot(slot, leader, heading), float(tol)
        task = self.tasks.get(drone)
        if task is not None:
            spot = self.task_pos.get(drone)
            if spot is not None:
                ref = np.array(
                    [float(spot[0]), float(spot[1]), float(self.cfg.cruise_altitude)]
                )
                return ref, float(self._transit_allowance(drone, tick)
                                  + self.cfg.target_detect_range)
        sector = self.sectors[self.sector_of.get(drone, drone) % len(self.sectors)]
        if claimed is not None:
            ref = sector.nearest(np.asarray(claimed, dtype=float))
        else:
            ref = sector.centre.copy()
        ref[2] = self.cfg.cruise_altitude
        # Transit into a freshly assigned sector is legitimate travel, so the
        # tolerance stays at sector scale rather than collapsing to a point,
        # and it carries a decaying transit allowance on top.
        #
        # The allowance starts at the widest separation the search box permits
        # and drains at the honest closure rate.  Two properties matter: it is
        # computed from published plan data (area size, assignment tick, cruise
        # figures) so every drone derives the identical number, and it reaches
        # zero.  A drone still crossing the box is untouchable; a drone that has
        # had ample time and is still outside its sector is not.
        span = max(sector.x1 - sector.x0, sector.y1 - sector.y0)
        floor = max(span / 2.0, self.cfg.waypoint_tolerance * 4.0)
        return ref, float(floor + self._transit_allowance(drone, tick))

    def _slot_allowance(self, drone: int, tick: int) -> float:
        """Travel credit for a formation slot the drone was just re-assigned.

        Identical in spirit to :meth:`_transit_allowance`: it starts at the
        full ordered displacement and drains at the honest closure rate, so a
        drone that is merely *on its way* to its new slot is untouchable, while
        one that has had ample time and is still out of place is not.
        """
        stamp = self._slot_credit.get(drone)
        if stamp is None:
            return 0.0
        start, shift = stamp
        elapsed = max(0, tick - start) * self.dt
        closure = self.cfg.transit_speed / max(self.cfg.transit_margin, 1e-6)
        allowance = shift - closure * elapsed
        if allowance <= 0.0:
            self._slot_credit.pop(drone, None)
            return 0.0
        return float(allowance)

    def _transit_allowance(self, drone: int, tick: int) -> float:
        """Distance credit a drone still has for travelling to new orders.

        The allowance starts at the widest separation the search box permits
        and drains at the honest closure rate.  Two properties matter: it is
        computed from published plan data (area size, assignment tick, cruise
        figures) so every drone derives the identical number, and it reaches
        zero.  A drone still crossing the box is untouchable; a drone that has
        had ample time and is still nowhere near its orders is not.
        """
        elapsed = max(0, tick - self._assigned_tick.get(drone, self.formation_ticks)) * self.dt
        budget = float(self.cfg.area_size * np.sqrt(2.0))
        closure = self.cfg.transit_speed / max(self.cfg.transit_margin, 1e-6)
        return max(0.0, budget - closure * elapsed)

    def observe(self, drone: int, position: np.ndarray) -> None:
        idx = self.sector_of.get(drone)
        if idx is None:
            return
        sector = self.sectors[idx % len(self.sectors)]
        if sector.x0 <= position[0] <= sector.x1 and sector.y0 <= position[1] <= sector.y1:
            sector.mark(position, self.cfg.target_detect_range * 0.45)

    def detect(self, drone: int, position: np.ndarray) -> list[dict]:
        """Genuine sensor detections for a drone at ``position``."""
        out = []
        for tgt in self.targets:
            d = float(np.linalg.norm(tgt.position[:2] - position[:2]))
            if d <= self.cfg.target_detect_range:
                conf = float(np.clip(1.0 - d / self.cfg.target_detect_range, 0.35, 0.99))
                out.append(
                    {
                        "target_id": tgt.target_id,
                        "position": tgt.position.tolist(),
                        "confidence": conf,
                        "phantom": False,
                    }
                )
        return out

    def ingest(self, reports: dict[str, dict], tick: int) -> None:
        """Fold this tick's detection reports into the shared picture.

        ``reports`` maps a report id to ``{"position": ..., "observers": set}``
        and is built from what drones *say* they saw.  Nothing in here is
        trusted: corroboration by ``target_confirm_observers`` independent
        drones is the only thing that turns a report into a target.
        """
        for rid, rep in reports.items():
            if rid in self.dropped_reports:
                continue
            self.report_observers.setdefault(rid, set()).update(rep["observers"])
            self.report_position[rid] = np.asarray(rep["position"], dtype=float)
            self.report_first.setdefault(rid, tick)

    def assign_confirmations(
        self, claims: dict[int, np.ndarray], trusted: list[int], tick: int
    ) -> dict[int, str]:
        """Dispatch the nearest trusted drones to corroborate open reports.

        A target reported by one drone is not a target.  The mission rule is
        that ``target_confirm_observers`` independent drones must each see it
        with their own sensor - that rule is precisely what stops a single
        compromised drone from inventing a target and calling in a strike on
        it.  But disjoint search sectors mean a real target sitting inside one
        drone's box will never accumulate three independent looks by accident,
        so corroboration has to be *flown*, not waited for.

        The assignment is deterministic and derived only from published plan
        data plus the claims every drone already gossips, so every honest drone
        computes the identical task list; there is no dispatcher to compromise.
        An attacker can inject a phantom and buy a detour, which is a real cost
        - bounded by ``max_confirm_drones`` and ``confirm_timeout_s``, and paid
        for with an E3 phantom-report strike when the witnesses arrive and see
        nothing.
        """
        need = self.cfg.target_confirm_observers
        horizon = self.confirm_timeout_s / max(self.dt, 1e-6)
        open_reports: list[tuple[int, str, np.ndarray, set[int]]] = []
        for rid, obs in self.report_observers.items():
            if rid in self.dropped_reports:
                continue
            tgt = self.target_by_id(rid)
            if tgt is not None and tgt.confirmed:
                continue
            if len(obs) >= need:
                continue
            first = self.report_first.get(rid, tick)
            if tick - first > horizon:
                # Chased and never corroborated.  Let it go.
                self.dropped_reports.add(rid)
                continue
            open_reports.append((first, rid, self.report_position[rid], set(obs)))

        open_reports.sort(key=lambda item: item[0])
        tasks: dict[int, str] = {}
        task_pos: dict[int, np.ndarray] = {}
        for _first, rid, spot, obs in open_reports:
            if len(tasks) >= self.max_confirm_drones:
                break
            pool = [d for d in trusted if d not in obs and d not in tasks and d in claims]
            pool.sort(key=lambda d: float(np.linalg.norm(claims[d][:2] - spot[:2])))
            for drone in pool[: need - len(obs)]:
                tasks[drone] = rid
                task_pos[drone] = spot
                if len(tasks) >= self.max_confirm_drones:
                    break

        # Restart the transit allowance for every drone whose orders changed,
        # so E3 never accuses a drone for obeying the plan it was just handed.
        for drone, rid in tasks.items():
            if self.tasks.get(drone) != rid:
                self._assigned_tick[drone] = tick
        for drone in self.tasks:
            if drone not in tasks:
                self._assigned_tick[drone] = tick
        self.tasks, self.task_pos = tasks, task_pos
        return tasks

    def target_by_id(self, target_id: str) -> Target | None:
        for tgt in self.targets:
            if tgt.target_id == target_id:
                return tgt
        return None

    def confirm(self, tick: int) -> list[Target]:
        """Promote reports that enough *trusted* observers have corroborated."""
        newly = []
        for tgt in self.targets:
            if tgt.confirmed:
                continue
            tgt.confirmed_by |= self.report_observers.get(tgt.target_id, set())
            if len(tgt.confirmed_by) >= self.cfg.target_confirm_observers:
                tgt.confirmed_tick = tick
                newly.append(tgt)
        return newly

    def purge_observer(self, drone: int) -> None:
        """Retract everything an expelled drone ever contributed to the picture.

        Corroboration from a node the swarm has since decided is compromised is
        not evidence.  Revoking it retroactively is what stops an attacker from
        buying a permanent confirmation before it is caught.

        Retracting the vote is only half of it.  A report the expelled drone
        raised alone now has nobody at all standing behind it, and left in the
        book it goes on pulling honest drones off their sectors until it times
        out - so a phantom injected once still costs the swarm most of its
        search even after the liar is gone.  An unsupported report is dropped
        outright.  Real sightings are unaffected: anything a live drone has
        also seen keeps that observer and stays open.
        """
        for obs in self.report_observers.values():
            obs.discard(drone)
        for tgt in self.targets:
            if not tgt.confirmed:
                tgt.confirmed_by.discard(drone)
        orphaned = [
            rid
            for rid, obs in self.report_observers.items()
            if not obs and rid not in self.dropped_reports
        ]
        for rid in orphaned:
            self.dropped_reports.add(rid)
            for d, tid in list(self.tasks.items()):
                if tid == rid:
                    self.tasks.pop(d, None)
                    self.task_pos.pop(d, None)

    # -------------------------------------------------- exclusion handling
    def advance_sector(self, drone: int, tick: int) -> bool:
        """Rotate a drone onto the next box it owes a sweep to.

        Called when a lawnmower pass completes.  A drone holding a single
        sector rotates onto itself and nothing changes; one carrying an
        inherited box moves to it and starts a fresh transit allowance, since
        crossing to the other side of the search area is exactly the honest
        travel E3 must not read as desertion.
        """
        queue = self.sector_queue.get(drone)
        if not queue or len(queue) < 2:
            return False
        queue.append(queue.pop(0))
        self.sector_of[drone] = queue[0]
        self._assigned_tick[drone] = tick
        return True

    def reallocate(self, excluded: int, remaining: list[int], tick: int) -> dict:
        """Redistribute an expelled drone's sectors and formation slot."""
        orphans = list(self.sector_queue.pop(excluded, []))
        self.sector_of.pop(excluded, None)
        self.slot_of.pop(excluded, None)
        assignee = None
        # Hand every box the dead drone was answerable for to the survivor
        # carrying the least work, one at a time so the load stays level.
        for sector in orphans:
            if not remaining:
                break
            assignee = min(
                remaining, key=lambda d: (len(self.sector_queue.get(d, [])), d)
            )
            self.sector_queue.setdefault(assignee, []).append(sector)
            if assignee not in self.sector_of:
                self.sector_of[assignee] = sector
                # The clock on the transit allowance restarts: taking over a
                # dead drone's sector is a new journey, and E3 must not convict
                # the one honest drone that just accepted extra work.
                self._assigned_tick[assignee] = tick
        self._assigned_tick.pop(excluded, None)
        # Re-pack formation slots so the vee has no hole in it.  The distance
        # each drone is asked to slide is a pure function of published plan data
        # (slot index and spacing), so every honest drone derives the identical
        # credit and no drone can inflate its own.
        zero, ahead = np.zeros(3), 0.0
        for new_slot, drone in enumerate(sorted(self.slot_of)):
            old_slot = self.slot_of[drone]
            if new_slot != old_slot:
                shift = float(
                    np.linalg.norm(
                        self.formation_slot(new_slot, zero, ahead)
                        - self.formation_slot(old_slot, zero, ahead)
                    )
                )
                self._slot_credit[drone] = (tick, shift)
            self.slot_of[drone] = new_slot
        self._slot_credit.pop(excluded, None)
        record = {
            "tick": tick,
            "excluded": excluded,
            "sector": sector,
            "reassigned_to": assignee,
        }
        self.reallocations.append(record)
        return record

    # ------------------------------------------------------------ scoring
    def coverage(self) -> float:
        if not self.sectors:
            return 0.0
        return float(np.mean([s.coverage for s in self.sectors]))

    @property
    def success(self) -> bool:
        cov = self.coverage() >= self.cfg.sector_coverage_goal
        found = all(t.confirmed for t in self.targets)
        return bool(cov and found)

    def progress(self) -> dict:
        return {
            "coverage": self.coverage(),
            "targets_confirmed": sum(1 for t in self.targets if t.confirmed),
            "targets_total": len(self.targets),
            "phase": self.phase,
        }
