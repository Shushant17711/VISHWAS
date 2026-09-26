"""Point-mass UAV kinematics with a hard flight envelope and an energy model.

The whole VISHWAS argument rests on one asymmetry: an attacker controls what a
drone *says*, but the airframe it is flying still obeys physics.  This module
is the physics.  Nothing here ever consults telemetry - it only ever advances
ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..config import AirframeConfig

# Horizontal speed below which heading is undefined and simply held (m/s).
# Shared with evidence channel E1 so that the detector and the physics agree
# on when a yaw reading carries information.
YAW_HOLD_SPEED = 1.0


def _clip_norm(vec: np.ndarray, limit: float) -> np.ndarray:
    """Scale ``vec`` down so that its Euclidean norm is at most ``limit``."""
    norm = float(np.linalg.norm(vec))
    if norm <= limit or norm == 0.0:
        return vec
    return vec * (limit / norm)


@dataclass
class PhysicalState:
    """Ground-truth state of one airframe.  Never transmitted, never forged."""

    position: np.ndarray                      # [x, y, z] metres, ENU
    velocity: np.ndarray                      # [vx, vy, vz] m/s
    yaw: float = 0.0                          # rad
    energy: float = 0.0                       # J remaining
    alive: bool = True

    # Rolling record used only for scoring/plots, never by the detectors.
    distance_flown: float = 0.0

    def copy(self) -> "PhysicalState":
        return PhysicalState(
            position=self.position.copy(),
            velocity=self.velocity.copy(),
            yaw=self.yaw,
            energy=self.energy,
            alive=self.alive,
            distance_flown=self.distance_flown,
        )


class Airframe:
    """Enforces the flight envelope while integrating a commanded acceleration."""

    def __init__(self, cfg: AirframeConfig, state: PhysicalState) -> None:
        self.cfg = cfg
        self.state = state
        self.state.energy = self.state.energy or cfg.battery_capacity

    # ------------------------------------------------------------------ step
    def step(self, accel_cmd: np.ndarray, dt: float) -> None:
        """Advance one tick under a commanded acceleration (m/s^2)."""
        if not self.state.alive:
            return

        cfg = self.cfg
        accel = _clip_norm(np.asarray(accel_cmd, dtype=float), cfg.max_accel)

        prev_vel = self.state.velocity.copy()
        vel = prev_vel + accel * dt

        # Horizontal speed cap.
        horiz = _clip_norm(vel[:2], cfg.max_speed)
        vel[0], vel[1] = horiz[0], horiz[1]

        # Vertical rate caps (asymmetric: descent can be faster than climb).
        vel[2] = float(np.clip(vel[2], -cfg.max_descent_rate, cfg.max_climb_rate))

        # Yaw-rate cap: the airframe cannot instantaneously reverse heading.
        vel = self._apply_turn_rate_limit(prev_vel, vel, dt)

        new_pos = self.state.position + vel * dt
        new_pos[2] = float(np.clip(new_pos[2], cfg.min_altitude, cfg.max_altitude))
        if new_pos[2] in (cfg.min_altitude, cfg.max_altitude):
            vel[2] = 0.0

        self.state.distance_flown += float(np.linalg.norm(new_pos - self.state.position))
        self.state.position = new_pos
        self.state.velocity = vel
        # Heading is only defined while the airframe is actually translating.
        # A quad holding station at 0.2 m/s has a velocity vector made almost
        # entirely of noise, and reading a heading off it would report wild
        # yaw slews that never physically happened - which E1 would then score
        # as an impossible turn rate.  Below the threshold the last real
        # heading is held.
        if np.linalg.norm(vel[:2]) > YAW_HOLD_SPEED:
            self.state.yaw = float(np.arctan2(vel[1], vel[0]))

        self._drain_energy(vel, dt)

    # ---------------------------------------------------------------- limits
    def _apply_turn_rate_limit(
        self, prev_vel: np.ndarray, vel: np.ndarray, dt: float
    ) -> np.ndarray:
        prev_h, new_h = prev_vel[:2], vel[:2]
        prev_speed = float(np.linalg.norm(prev_h))
        new_speed = float(np.linalg.norm(new_h))
        if prev_speed < 1.0 or new_speed < 1e-6:
            return vel

        prev_ang = np.arctan2(prev_h[1], prev_h[0])
        new_ang = np.arctan2(new_h[1], new_h[0])
        delta = (new_ang - prev_ang + np.pi) % (2 * np.pi) - np.pi
        max_delta = self.cfg.max_turn_rate * dt
        if abs(delta) <= max_delta:
            return vel

        clamped = prev_ang + np.sign(delta) * max_delta
        vel = vel.copy()
        vel[0] = new_speed * np.cos(clamped)
        vel[1] = new_speed * np.sin(clamped)
        return vel

    # ---------------------------------------------------------------- energy
    def _drain_energy(self, vel: np.ndarray, dt: float) -> None:
        cfg = self.cfg
        power = (
            cfg.hover_power
            + cfg.drag_coeff * float(np.dot(vel, vel))
            + cfg.climb_coeff * max(float(vel[2]), 0.0)
        )
        self.state.energy = max(0.0, self.state.energy - power * dt)
        if self.state.energy <= 0.0:
            self.state.alive = False

    # ------------------------------------------------------------- utilities
    def expected_power(self, vel: np.ndarray) -> float:
        """Power the energy model predicts for a given velocity."""
        return expected_power(self.cfg, vel)


def expected_power(cfg: AirframeConfig, vel: np.ndarray) -> float:
    """Power the energy model predicts for a given velocity.

    Evidence channel E1 uses this to ask: does the battery drain a drone
    reports match the manoeuvre profile it claims to be flying?
    """
    vel = np.asarray(vel, dtype=float)
    return (
        cfg.hover_power
        + cfg.drag_coeff * float(np.dot(vel, vel))
        + cfg.climb_coeff * max(float(vel[2]), 0.0)
    )


def seek_waypoint(
    state: PhysicalState,
    waypoint: np.ndarray,
    cfg: AirframeConfig,
    cruise_speed: float | None = None,
    gain: float = 1.4,
    damping: float = 2.1,
) -> np.ndarray:
    """Proportional-derivative guidance toward a waypoint.

    Returns a commanded acceleration; the airframe clamps it to the envelope.
    """
    cruise = cruise_speed if cruise_speed is not None else cfg.max_speed * 0.7
    to_goal = np.asarray(waypoint, dtype=float) - state.position
    dist = float(np.linalg.norm(to_goal))
    if dist < 1e-6:
        desired_vel = np.zeros(3)
    else:
        speed = min(cruise, gain * dist)
        desired_vel = to_goal / dist * speed
    return (desired_vel - state.velocity) * damping


def separation_accel(
    state: PhysicalState,
    neighbours: list[np.ndarray],
    min_sep: float = 18.0,
    strength: float = 5.0,
) -> np.ndarray:
    """Short-range repulsion so honest drones do not collide with each other."""
    accel = np.zeros(3)
    for other in neighbours:
        delta = state.position - other
        dist = float(np.linalg.norm(delta))
        if 1e-6 < dist < min_sep:
            accel += delta / dist * strength * (min_sep - dist) / min_sep
    return accel
