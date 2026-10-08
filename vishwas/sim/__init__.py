"""The world: physics, radio, attackers, mission, and the swarm loop.

Nothing in this package is a detector.  It exists to produce a stream of
*claims* (which a compromised drone controls) and a stream of *observations*
(which it does not), so that the evidence layer has something real to work on.

Re-exports are resolved lazily (PEP 562).  ``vishwas.evidence.e1_self`` needs
``sim.kinematics`` for the airframe power model, and ``sim.swarm`` needs the
whole evidence and consensus stack back - so eagerly importing ``.swarm`` here
meant that merely reaching for ``kinematics`` pulled in a cycle, and whether
it resolved depended on which module the process imported first.  Binding the
names on first attribute access keeps the public surface identical while
letting each submodule be imported on its own.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

_EXPORTS: dict[str, str] = {
    "BEHAVIOURS": ".attacks",
    "SCENARIOS": ".attacks",
    "AttackBehaviour": ".attacks",
    "build_behaviour": ".attacks",
    "Airframe": ".kinematics",
    "PhysicalState": ".kinematics",
    "seek_waypoint": ".kinematics",
    "separation_accel": ".kinematics",
    "Mesh": ".mesh",
    "RangeReportMsg": ".mesh",
    "SuspicionMsg": ".mesh",
    "TelemetryMsg": ".mesh",
    "Mission": ".mission",
    "Sector": ".mission",
    "Target": ".mission",
    "Drone": ".swarm",
    "Swarm": ".swarm",
    "TickRecord": ".swarm",
}

__all__ = list(_EXPORTS)

if TYPE_CHECKING:  # pragma: no cover - for type checkers and IDEs only
    from .attacks import BEHAVIOURS, SCENARIOS, AttackBehaviour, build_behaviour
    from .kinematics import Airframe, PhysicalState, seek_waypoint, separation_accel
    from .mesh import Mesh, RangeReportMsg, SuspicionMsg, TelemetryMsg
    from .mission import Mission, Sector, Target
    from .swarm import Drone, Swarm, TickRecord


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module, __name__), name)
    globals()[name] = value          # bind once; later lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
