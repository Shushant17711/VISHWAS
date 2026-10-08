"""Evaluation harness: Monte-Carlo studies over the simulator.

The simulator answers "what happened in this run".  This package answers the
questions a judge actually asks:

  * How fast does it catch a liar, and how often does it miss?
  * How often does it convict an innocent drone?  (The number that kills
    deployments - a defence that expels honest aircraft is worse than none.)
  * Which layer is doing the work?  (Three-configuration ablation.)
  * Where does it break?  (Degradation past the Byzantine bound.)
  * What does it cost per drone?
  * What does the whole precision/latency trade-off curve look like - not one
    cherry-picked operating point.

Every number the report and dashboard show is produced here, from seeds that
are recorded in the output, so any of it can be reproduced exactly.
"""

from .harness import RunSpec, run_many, run_one
from .study import (
    ablation_matrix,
    aggregate,
    cusum_sweep_matrix,
    degradation_matrix,
    scenario_matrix,
)

__all__ = [
    "RunSpec",
    "run_one",
    "run_many",
    "scenario_matrix",
    "ablation_matrix",
    "degradation_matrix",
    "cusum_sweep_matrix",
    "aggregate",
]
