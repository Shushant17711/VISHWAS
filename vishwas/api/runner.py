"""Live mission runner - one simulated swarm, stepped in the background.

A mission is a few thousand ticks of pure-Python numerics at roughly 60 ms per
tick for a nine-drone swarm.  That is too slow to run inside an event loop and
too fast to matter as a thread: each mission gets one worker thread that steps
the swarm on a wall clock, appends frames to a buffer, and stops.  Readers -
websocket streams and cursor-paginated polls alike - only ever read the buffer,
so a slow client can never stall the simulation and a client that joins late
still gets the whole mission from tick 1.

Playback speed is honest.  ``speed=1`` means one simulated second per real
second (``dt`` is 0.2 s, so five ticks a second).  If the machine cannot keep
up, the mission runs as fast as it can and ``lagging`` is reported rather than
quietly dropping frames.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..config import MeshConfig, SimConfig
from ..sim.attacks import SCENARIOS
from ..sim.swarm import Swarm
from .frames import events_since, frame, mission_meta

MAX_MISSIONS = 8
MAX_SPEED = 50.0


class MissionError(Exception):
    """Bad mission request - carries an API error code and HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class MissionSpec:
    scenario: str = "position_teleport"
    n_compromised: int = 1
    n_drones: int = 9
    max_ticks: int = 1200
    seed: int = 7
    attack_start: int = 60
    enable_consensus: bool = True
    enable_trust_priors: bool = True
    #: ClaimCheck - audit accusations before counting them.  False reproduces
    #: the pure majority-vote behaviour, which is what the dashboard's
    #: side-by-side comparison needs to be able to show.
    enable_claim_verifier: bool = True
    speed: float = 4.0
    # Drones with a hardware root of trust - see MeshConfig.certified_anchors.
    # Empty by default; opt-in only.
    certified_anchors: list[int] = field(default_factory=list)

    def key(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "n_compromised": self.n_compromised,
            "n_drones": self.n_drones,
            "max_ticks": self.max_ticks,
            "seed": self.seed,
            "attack_start": self.attack_start,
            "enable_consensus": self.enable_consensus,
            "enable_trust_priors": self.enable_trust_priors,
            "enable_claim_verifier": self.enable_claim_verifier,
            "certified_anchors": list(self.certified_anchors),
        }

    def validate(self) -> "MissionSpec":
        # "none" is the honest control - it has no attack behaviour of its own,
        # so it is not in the scenario catalogue, but it is a legal mission.
        known = {"none"} | {str(s["key"]) for s in SCENARIOS}
        if self.scenario not in known:
            raise MissionError(
                "unknown_scenario",
                f"No scenario named {self.scenario!r}. Known: {', '.join(sorted(known))}.",
                422,
            )
        if self.n_compromised >= self.n_drones:
            raise MissionError(
                "no_honest_drones",
                "n_compromised must be smaller than n_drones.",
                422,
            )
        if self.scenario == "none" and self.n_compromised:
            raise MissionError(
                "control_has_no_attacker",
                "The honest control scenario runs with n_compromised = 0.",
                422,
            )
        bad_anchors = [a for a in self.certified_anchors if not (0 <= a < self.n_drones)]
        if bad_anchors:
            raise MissionError(
                "bad_anchor",
                f"certified_anchors must each be a valid drone id (0..{self.n_drones - 1}): {bad_anchors}.",
                422,
            )
        non_anchor_drones = self.n_drones - len(set(self.certified_anchors))
        if self.n_compromised > non_anchor_drones:
            raise MissionError(
                "no_room_for_compromise",
                f"n_compromised ({self.n_compromised}) exceeds the {non_anchor_drones} non-anchor "
                "drones available to draw from - certified anchors are never eligible.",
                422,
            )
        self.speed = float(min(max(self.speed, 0.25), MAX_SPEED))
        return self

    def build_config(self) -> SimConfig:
        return SimConfig(
            n_drones=self.n_drones,
            max_ticks=self.max_ticks,
            seed=self.seed,
            mesh=MeshConfig(certified_anchors=tuple(sorted(set(self.certified_anchors)))),
            enable_consensus=self.enable_consensus,
            enable_trust_priors=self.enable_trust_priors,
            enable_claim_verifier=self.enable_claim_verifier,
        )


@dataclass
class Mission:
    """One running (or finished) simulation and everything readers need."""

    id: str
    spec: MissionSpec
    created_at: float = field(default_factory=time.time)

    swarm: Swarm | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    frames: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] | None = None
    state: str = "starting"  # starting | running | paused | finished | failed
    error: str | None = None
    lagging: bool = False

    _thread: threading.Thread | None = None
    _resume: threading.Event = field(default_factory=threading.Event)
    _stop: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ------------------------------------------------------------------
    def start(self) -> "Mission":
        cfg = self.spec.build_config()
        self.swarm = Swarm(
            cfg,
            scenario=self.spec.scenario,
            n_compromised=self.spec.n_compromised,
            attack_start=self.spec.attack_start,
        )
        self.meta = mission_meta(self.swarm, self.spec.key())
        self._resume.set()
        self.state = "running"
        self._thread = threading.Thread(target=self._fly, name=f"mission-{self.id}", daemon=True)
        self._thread.start()
        return self

    def _fly(self) -> None:
        swarm = self.swarm
        assert swarm is not None
        cursor = 0
        try:
            while not self._stop.is_set() and swarm.tick < swarm.cfg.max_ticks:
                if not self._resume.wait(timeout=0.5):
                    continue
                budget = swarm.cfg.dt / max(self.spec.speed, 1e-6)
                started = time.perf_counter()
                record = swarm.step()
                payload = frame(swarm, record)
                new_events, cursor = events_since(swarm, cursor)
                with self._lock:
                    self.frames.append(payload)
                    self.events.extend(new_events)
                if not any(d.alive for d in swarm.drones.values()):
                    break
                if swarm.mission.success and swarm.mission.phase == "search":
                    break
                spare = budget - (time.perf_counter() - started)
                self.lagging = spare < 0
                if spare > 0:
                    self._stop.wait(spare)
        except Exception as exc:  # pragma: no cover - defensive
            self.error = f"{type(exc).__name__}: {exc}"
            self.state = "failed"
            return
        self.summary = swarm.summary()
        self.state = "stopped" if self._stop.is_set() else "finished"

    # ------------------------------------------------------------------
    def pause(self) -> None:
        if self.state == "running":
            self._resume.clear()
            self.state = "paused"

    def resume(self) -> None:
        if self.state == "paused":
            self._resume.set()
            self.state = "running"

    def set_speed(self, speed: float) -> None:
        self.spec.speed = float(min(max(speed, 0.25), MAX_SPEED))

    def stop(self) -> None:
        self._stop.set()
        self._resume.set()

    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            n = len(self.frames)
            last = self.frames[-1] if self.frames else None
        return {
            "id": self.id,
            "state": self.state,
            "speed": self.spec.speed,
            "lagging": self.lagging,
            "n_frames": n,
            "tick": last["tick"] if last else 0,
            "error": self.error,
            "created_at": round(self.created_at, 3),
            "spec": self.spec.key(),
            "summary": self.summary,
        }

    def page(self, after: int = 0, limit: int = 200) -> dict[str, Any]:
        """Frames after a tick cursor.  Frames are immutable once appended."""
        with self._lock:
            total = len(self.frames)
            start = max(0, min(after, total))
            rows = self.frames[start : start + limit]
            events = [e for e in self.events if e.get("tick", 0) > after]
        cursor = start + len(rows)
        return {
            "data": rows,
            "meta": {
                "cursor": cursor,
                "has_next": cursor < total or self.state in {"running", "paused", "starting"},
                "state": self.state,
                "events": events,
            },
        }


class MissionRegistry:
    """In-process mission store.  Oldest finished mission is evicted first."""

    def __init__(self, capacity: int = MAX_MISSIONS) -> None:
        self.capacity = capacity
        self._missions: dict[str, Mission] = {}
        self._lock = threading.Lock()

    def create(self, spec: MissionSpec) -> Mission:
        spec.validate()
        mission = Mission(id=uuid.uuid4().hex[:12], spec=spec)
        with self._lock:
            self._evict()
            self._missions[mission.id] = mission
        return mission.start()

    def _evict(self) -> None:
        while len(self._missions) >= self.capacity:
            done = [m for m in self._missions.values() if m.state not in {"running", "paused"}]
            victim = min(done or list(self._missions.values()), key=lambda m: m.created_at)
            victim.stop()
            self._missions.pop(victim.id, None)

    def get(self, mission_id: str) -> Mission:
        mission = self._missions.get(mission_id)
        if mission is None:
            raise MissionError("not_found", f"No mission {mission_id!r}.", 404)
        return mission

    def list(self) -> list[Mission]:
        return sorted(self._missions.values(), key=lambda m: m.created_at, reverse=True)

    def delete(self, mission_id: str) -> None:
        mission = self.get(mission_id)
        mission.stop()
        self._missions.pop(mission_id, None)

    def shutdown(self) -> None:
        for mission in list(self._missions.values()):
            mission.stop()
        self._missions.clear()
