"""Run the VISHWAS evaluation suite and write the result bundles.

    python scripts/run_evaluation.py --quick        # smoke depth (~10-15 min on 8 cores)
    python scripts/run_evaluation.py                # default depth
    python scripts/run_evaluation.py --full         # paper depth
    python scripts/run_evaluation.py --only scenarios ablation

Output lands in ``data/results/`` as one JSON bundle per study plus
``index.json`` (the manifest the dashboard loads) and ``results.md``.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vishwas.evaluation.harness import run_many  # noqa: E402
from vishwas.evaluation.report import (  # noqa: E402
    RESULTS_DIR,
    bundle,
    write_bundle,
    write_index,
    write_markdown,
)
from vishwas.evaluation.study import (  # noqa: E402
    ablation_matrix,
    aggregate,
    claimcheck_matrix,
    cusum_sweep_matrix,
    degradation_matrix,
    scenario_matrix,
)

# depth -> (seeds, mission ticks, degradation swarm size)
DEPTHS = {
    "quick": {"seeds": 1, "ticks": 700, "deg_ticks": 700, "cusum_seeds": 1},
    "default": {"seeds": 5, "ticks": 2200, "deg_ticks": 1800, "cusum_seeds": 3},
    "full": {"seeds": 12, "ticks": 3000, "deg_ticks": 2500, "cusum_seeds": 8},
}

NOTES = {
    "scenarios": (
        "Every attack scenario plus the honest control, repeated over a fixed seed "
        "ladder. Latency is measured from the tick the attack starts to the tick the "
        "swarm votes the attacker out, and is averaged only over runs where detection "
        "happened - `detected_runs` reports how many those were. The honest control "
        "row is the one that decides deployability: it must show a false-exclusion "
        "rate of zero."
    ),
    "ablation": (
        "Identical missions and identical seeds under three configurations. "
        "A: evidence is computed but nobody votes - the conventional swarm baseline. "
        "B: consensus with uniform trust. C: full VISHWAS with the outer trust "
        "priors weighting accuser credibility. Paired seeds mean a difference "
        "between rows is the configuration, not the weather."
    ),
    "degradation": (
        "Compromised fraction swept from zero to past the Byzantine bound "
        "f = (n-1)/3. The right-hand side of this curve is the honest part of the "
        "claim: the swarm holds up to the bound, and this is exactly how it fails "
        "after it."
    ),
    "claimcheck": (
        "The same missions on the same seeds with accusations verified against "
        "independently re-derived physics (`verified`) and merely counted "
        "(`vote_only`). Read `honest_expelled` first: the vote-only "
        "configuration starts convicting honest drones as soon as the "
        "compromised side becomes the majority, because every mechanism in the "
        "consensus layer is an aggregate of opinions and an aggregate follows "
        "whichever bloc is larger. `recall` is the honest cost column - past "
        "the Byzantine bound the verifier declines to convict on evidence it "
        "cannot independently re-derive, so it stops catching attackers rather "
        "than guessing at them. `honest_quarantined` is where those refusals "
        "went: a reversible hold, not an expulsion."
    ),
    "cusum": (
        "CUSUM alarm threshold h swept against the slow-drift attack and the honest "
        "control. Low h detects sooner and convicts honest drones more often; high h "
        "is patient. The operating point in the shipped config is one point on this "
        "curve, chosen before the scenario results were read."
    ),
}


def _progress(label: str):
    start = time.perf_counter()

    def report(done: int, total: int, row: dict) -> None:
        elapsed = time.perf_counter() - start
        rate = elapsed / max(1, done)
        eta = rate * (total - done)
        mark = " " if row.get("ok") else "!"
        print(
            f"  [{label}] {done:4d}/{total:<4d} {mark} "
            f"{row.get('scenario', ''):<19s} seed={row.get('seed'):<3} "
            f"elapsed {elapsed:5.0f}s eta {eta:5.0f}s",
            flush=True,
        )

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="smoke depth")
    parser.add_argument("--full", action="store_true", help="paper depth")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    parser.add_argument(
        "--only",
        nargs="+",
        choices=["scenarios", "ablation", "degradation", "cusum", "claimcheck"],
        default=["scenarios", "ablation", "degradation", "cusum", "claimcheck"],
    )
    args = parser.parse_args()

    depth = DEPTHS["quick"] if args.quick else DEPTHS["full"] if args.full else DEPTHS["default"]
    print(
        f"VISHWAS evaluation | depth={'quick' if args.quick else 'full' if args.full else 'default'} "
        f"| seeds={depth['seeds']} ticks={depth['ticks']} | studies={', '.join(args.only)}",
        flush=True,
    )

    bundles = []
    t0 = time.perf_counter()

    if "scenarios" in args.only:
        specs = scenario_matrix(n_seeds=depth["seeds"], max_ticks=depth["ticks"])
        rows = run_many(specs, workers=args.workers, progress=_progress("scenarios"))
        table = aggregate(rows, ["scenario"])
        bundles.append(
            bundle("scenarios", "Attack scenarios", rows, table, NOTES["scenarios"])
        )

    if "ablation" in args.only:
        specs = ablation_matrix(n_seeds=depth["seeds"], max_ticks=depth["ticks"])
        rows = run_many(specs, workers=args.workers, progress=_progress("ablation"))
        table = aggregate(rows, ["config_label", "scenario"])
        by_config = aggregate(rows, ["config_label"])
        bundles.append(
            bundle(
                "ablation",
                "Three-configuration ablation",
                rows,
                table,
                NOTES["ablation"],
                extra={"by_config": by_config},
            )
        )

    if "degradation" in args.only:
        specs = degradation_matrix(n_seeds=depth["seeds"], max_ticks=depth["deg_ticks"])
        rows = run_many(specs, workers=args.workers, progress=_progress("degradation"))
        table = aggregate(rows, ["n_compromised"])
        n_drones = specs[0].n_drones if specs else 13
        bundles.append(
            bundle(
                "degradation",
                "Graceful degradation vs compromised fraction",
                rows,
                table,
                NOTES["degradation"],
                extra={"n_drones": n_drones, "f_tolerated": (n_drones - 1) // 3},
            )
        )

    if "claimcheck" in args.only:
        specs = claimcheck_matrix(n_seeds=depth["seeds"], max_ticks=depth["deg_ticks"])
        rows = run_many(specs, workers=args.workers, progress=_progress("claimcheck"))
        table = aggregate(rows, ["config_label", "scenario", "n_compromised"])
        by_config = aggregate(rows, ["config_label", "n_compromised"])
        n_drones = specs[0].n_drones if specs else 9
        bundles.append(
            bundle(
                "claimcheck",
                "ClaimCheck: verified accusations vs. counted ones",
                rows,
                table,
                NOTES["claimcheck"],
                extra={
                    "by_config": by_config,
                    "n_drones": n_drones,
                    "f_tolerated": (n_drones - 1) // 3,
                },
            )
        )

    if "cusum" in args.only:
        specs = cusum_sweep_matrix(n_seeds=depth["cusum_seeds"], max_ticks=depth["deg_ticks"])
        rows = run_many(specs, workers=args.workers, progress=_progress("cusum"))
        table = aggregate(rows, ["overrides.cusum_threshold_h", "scenario"])
        bundles.append(
            bundle(
                "cusum",
                "CUSUM precision / latency trade-off",
                rows,
                table,
                NOTES["cusum"],
            )
        )

    for b in bundles:
        path = write_bundle(b, args.out)
        print(f"wrote {path}  ({b['n_runs']} runs, {b['n_failed']} failed)", flush=True)
    if bundles:
        print(f"wrote {write_index(bundles, args.out)}", flush=True)
        print(f"wrote {write_markdown(bundles, args.out)}", flush=True)

    failed = sum(b["n_failed"] for b in bundles)
    print(f"done in {time.perf_counter() - t0:.0f}s | failed runs: {failed}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
