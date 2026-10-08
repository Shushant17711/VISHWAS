"""Mesh network model: message transport plus physical peer ranging.

Two very different things travel over this mesh and the distinction is the
heart of VISHWAS:

* :class:`TelemetryMsg` carries what a drone *claims*.  A compromised drone
  can put anything in here and it will still be cryptographically valid.
* :class:`RangeReportMsg` carries what a drone *measured* about its peers.
  Those measurements are taken against ground truth by the receiver's own
  radio, so a compromised drone cannot forge what everyone else observes
  about it - only what it observes about others.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

from ..config import MeshConfig


# --------------------------------------------------------------------------
# Message types
# --------------------------------------------------------------------------
@dataclass
class TelemetryMsg:
    """Self-reported state.  Forgeable end-to-end by a compromised node."""

    sender: int
    tick: int
    position: np.ndarray
    velocity: np.ndarray
    yaw: float
    battery_frac: float
    detections: list[dict[str, Any]] = field(default_factory=list)
    assigned_slot: int | None = None
    assigned_sector: int | None = None
    signature_valid: bool = True   # always True: the attacker holds valid keys


@dataclass
class RangeReportMsg:
    """Peer-ranging measurements taken by ``sender`` against its neighbours."""

    sender: int
    tick: int
    # peer_id -> (range_m, sigma_m)
    ranges: dict[int, tuple[float, float]] = field(default_factory=dict)
    # peer_id -> unit bearing vector in world frame (optional)
    bearings: dict[int, np.ndarray] = field(default_factory=dict)


@dataclass
class SuspicionMsg:
    """Gossiped suspicion vector s_sender[*] in [0, 1]."""

    sender: int
    tick: int
    scores: dict[int, float] = field(default_factory=dict)
    evidence: dict[int, dict[str, float]] = field(default_factory=dict)


Message = TelemetryMsg | RangeReportMsg | SuspicionMsg


# --------------------------------------------------------------------------
# The mesh
# --------------------------------------------------------------------------
class Mesh:
    """Broadcast mesh with range-limited links, packet loss and latency.

    Link feasibility and ranging noise are computed from *ground truth*
    positions supplied by the simulator, never from telemetry.
    """

    def __init__(self, cfg: MeshConfig, n_drones: int, rng: np.random.Generator) -> None:
        self.cfg = cfg
        self.n = n_drones
        self.rng = rng
        self._pending: dict[int, list[tuple[int, Message]]] = defaultdict(list)
        self._inbox: dict[int, list[Message]] = defaultdict(list)
        self.link_matrix = np.zeros((n_drones, n_drones), dtype=bool)
        # Pairwise ground-truth distances, refreshed every tick by
        # ``update_topology``.  Seeded here so a mesh is usable before the
        # first topology update rather than raising.
        self._dist = np.zeros((n_drones, n_drones), dtype=float)

    # ------------------------------------------------------------ topology
    def update_topology(self, true_positions: np.ndarray, alive: np.ndarray) -> None:
        """Recompute which pairs currently have a radio link."""
        deltas = true_positions[:, None, :] - true_positions[None, :, :]
        dist = np.linalg.norm(deltas, axis=-1)
        links = dist <= self.cfg.comm_range
        np.fill_diagonal(links, False)
        links &= alive[:, None] & alive[None, :]
        self.link_matrix = links
        self._dist = dist

    def neighbours(self, node: int) -> list[int]:
        return [int(j) for j in np.flatnonzero(self.link_matrix[node])]

    def connectivity(self) -> float:
        """Fraction of possible links that are up - a mission-health metric."""
        possible = self.n * (self.n - 1)
        return float(self.link_matrix.sum() / possible) if possible else 0.0

    # ------------------------------------------------------------- ranging
    def measure_ranges(
        self, observer: int, true_positions: np.ndarray, alive: np.ndarray
    ) -> tuple[dict[int, tuple[float, float]], dict[int, np.ndarray]]:
        """Physical range/bearing measurements taken by ``observer``.

        Returns ``(ranges, bearings)`` keyed by peer id.  Noise grows with
        range, which is what the multilateration solver later weights by.
        """
        cfg = self.cfg
        ranges: dict[int, tuple[float, float]] = {}
        bearings: dict[int, np.ndarray] = {}
        if not alive[observer]:
            return ranges, bearings

        candidates = [
            j
            for j in range(self.n)
            if j != observer and alive[j] and self._dist[observer, j] <= cfg.sensing_range
        ]
        # Radio budget: rank nearest-first, keep the strongest links.
        candidates.sort(key=lambda j: self._dist[observer, j])
        for j in candidates[: cfg.max_observers]:
            true_r = float(self._dist[observer, j])
            sigma = cfg.range_noise_base + cfg.range_noise_slope * true_r
            ranges[j] = (float(true_r + self.rng.normal(0.0, sigma)), sigma)

            delta = true_positions[j] - true_positions[observer]
            norm = float(np.linalg.norm(delta))
            if norm > 1e-6:
                unit = delta / norm
                perturb = self.rng.normal(0.0, cfg.bearing_noise, size=3)
                noisy = unit + perturb
                bearings[j] = noisy / float(np.linalg.norm(noisy))
        return ranges, bearings

    # ------------------------------------------------------------ transport
    def broadcast(self, msg: Message, tick: int) -> None:
        """Queue ``msg`` for every peer with a live link to the sender."""
        for j in self.neighbours(msg.sender):
            if self.rng.random() < self.cfg.packet_loss:
                continue
            self._pending[tick + self.cfg.latency_ticks].append((j, msg))

    def deliver(self, tick: int) -> None:
        """Move messages whose latency has elapsed into per-node inboxes."""
        self._inbox.clear()
        for receiver, msg in self._pending.pop(tick, []):
            self._inbox[receiver].append(msg)

    def inbox(self, node: int) -> list[Message]:
        return self._inbox.get(node, [])

    @staticmethod
    def of_type(messages: Iterable[Message], kind: type) -> list[Any]:
        return [m for m in messages if isinstance(m, kind)]
