"""One run in, one row out - plus the machinery to do thousands of them.

A :class:`RunSpec` is a complete, hashable description of a single experiment:
scenario, how many drones are compromised, the seed, which VISHWAS layers are
switched on, and any config overrides.  It is the only thing that needs to be
recorded for a result to be reproducible, so it is written into every output
row and into the result bundles the dashboard reads.
"""

from __future__ import annotations

import os
import platform
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Sequence

from ..config import SimConfig
from ..sim.swarm import Swarm

# ---------------------------------------------------------------------------
# Run specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSpec:
    """Everything needed to reproduce one simulated mission."""

    scenario: str = "none"
    n_compromised: int = 0
    seed: int = 7
    n_drones: int = 9
    max_ticks: int = 3000
    attack_start: int = 60
    enable_consensus: bool = True
    enable_trust_priors: bool = True
    # False => configuration D: accusations are counted, never verified.  This
    # is the pre-ClaimCheck majority-vote behaviour, kept switchable because
    # the whole claim of the ClaimCheck study is the difference between the
    # two settings on the same seeds.
    enable_claim_verifier: bool = True
    # Dotted overrides into the config tree, e.g. ("evidence.cusum_threshold_h", 6.5).
    # Kept as a tuple of pairs so the spec stays hashable and picklable.
    overrides: tuple[tuple[str, float], ...] = ()
    # Free-form labels used to group rows when aggregating.
    study: str = "scenario"
    config_label: str = "C"

    # -- derived ----------------------------------------------------------
    @property
    def compromised_fraction(self) -> float:
        return self.n_compromised / max(1, self.n_drones)

    def build_config(self) -> SimConfig:
        cfg = SimConfig(
            n_drones=self.n_drones,
            max_ticks=self.max_ticks,
            seed=self.seed,
            enable_consensus=self.enable_consensus,
            enable_trust_priors=self.enable_trust_priors,
            enable_claim_verifier=self.enable_claim_verifier,
        )
        if not self.overrides:
            return cfg
        # Round-trip through the serialisable form so nested dataclasses
        # (evidence.*, consensus.*, mission.*) can be patched by dotted path
        # without this module knowing their layout.
        data = cfg.to_dict()
        for path, value in self.overrides:
            head, _, tail = path.partition(".")
            if not tail:
                data[head] = value
            else:
                if head not in data or not isinstance(data[head], dict):
                    raise KeyError(f"unknown config section {head!r} in {path!r}")
                if tail not in data[head]:
                    raise KeyError(f"unknown config key {path!r}")
                data[head][tail] = value
        return SimConfig.from_dict(data)

    def key(self) -> dict[str, Any]:
        """The identifying fields, for grouping and for the output row."""
        return {
            "study": self.study,
            "scenario": self.scenario,
            "n_compromised": self.n_compromised,
            "n_drones": self.n_drones,
            "config_label": self.config_label,
            "enable_consensus": self.enable_consensus,
            "enable_trust_priors": self.enable_trust_priors,
            "enable_claim_verifier": self.enable_claim_verifier,
            "overrides": {k: v for k, v in self.overrides},
            "seed": self.seed,
        }


# ---------------------------------------------------------------------------
# Single run
# ---------------------------------------------------------------------------


def run_one(spec: RunSpec, keep_history: bool = False) -> dict[str, Any]:
    """Fly one mission and return a flat result row.

    A crash in one cell of a sweep must not lose the other several hundred, so
    failures are captured into the row rather than raised.  ``ok: False`` rows
    are counted and reported instead of silently dropped - an evaluation that
    hides its own failures is not evidence.
    """
    started = time.perf_counter()
    row: dict[str, Any] = dict(spec.key())
    try:
        swarm = Swarm(
            spec.build_config(),
            scenario=spec.scenario,
            n_compromised=spec.n_compromised,
            attack_start=spec.attack_start,
        )
        swarm.run()
        summary = swarm.summary()
        row.update(summary)
        row["ok"] = True
        row["error"] = None
        if keep_history:
            row["history"] = [h.__dict__ if hasattr(h, "__dict__") else h for h in swarm.history]
    except Exception as exc:  # pragma: no cover - defensive
        row["ok"] = False
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["wall_s"] = round(time.perf_counter() - started, 3)
    return row


def _run_one_packed(spec: RunSpec) -> dict[str, Any]:
    """Module-level entry point so specs survive the pickle to a worker."""
    return run_one(spec)


# ---------------------------------------------------------------------------
# Sweeps
# ---------------------------------------------------------------------------


def run_many(
    specs: Sequence[RunSpec],
    workers: int | None = None,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Run a batch of specs, in parallel when it is worth the process cost.

    Each mission is a few seconds of pure-Python numerics with no shared state,
    which is the one shape of work that survives multiprocessing on Windows
    cleanly.  Order of the returned rows follows the input, not completion, so
    result bundles are byte-stable across runs with the same seeds.
    """
    total = len(specs)
    if total == 0:
        return []
    if workers is None:
        workers = min(total, max(1, (os.cpu_count() or 2) - 1))

    if workers <= 1 or total == 1:
        rows = []
        for i, spec in enumerate(specs):
            row = run_one(spec)
            rows.append(row)
            if progress:
                progress(i + 1, total, row)
        return rows

    results: dict[int, dict[str, Any]] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_one_packed, s): i for i, s in enumerate(specs)}
        done = 0
        for fut in as_completed(futures):
            idx = futures[fut]
            results[idx] = fut.result()
            done += 1
            if progress:
                progress(done, total, results[idx])
    return [results[i] for i in range(total)]


def environment() -> dict[str, Any]:
    """Recorded alongside timing numbers - ms/drone/tick means nothing without it."""
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count(),
    }
