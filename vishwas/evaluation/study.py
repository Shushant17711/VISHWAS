"""The four studies, and the arithmetic that turns runs into headline numbers.

Study definitions live here rather than in a script so that the tests, the CLI
and the dashboard all quote the same experiment - there is exactly one place
where "the ablation" is defined.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

import numpy as np

from ..sim.attacks import SCENARIOS
from .harness import RunSpec

# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------

#: Fixed seed ladder.  Monte-Carlo results are only credible if the seeds were
#: chosen before the numbers were seen, so they are a constant in the source
#: rather than something a script picks at run time.
SEEDS: tuple[int, ...] = (7, 11, 13, 17, 19, 23, 29, 31, 37, 41,
                          43, 47, 53, 59, 61, 67, 71, 73, 79, 83)


def seeds(n: int) -> tuple[int, ...]:
    if n > len(SEEDS):
        raise ValueError(f"only {len(SEEDS)} fixed seeds are defined, asked for {n}")
    return SEEDS[:n]


# ---------------------------------------------------------------------------
# Study 1 - the scenario matrix (headline results table)
# ---------------------------------------------------------------------------


def scenario_matrix(
    n_seeds: int = 5,
    n_drones: int = 9,
    max_ticks: int = 3000,
) -> list[RunSpec]:
    """Every attack scenario plus the honest control, over the seed ladder.

    The honest control is not optional decoration: the false-exclusion rate it
    measures is the number that decides whether any of this is deployable.
    """
    specs: list[RunSpec] = []
    cases: list[tuple[str, int]] = [("none", 0)]
    cases += [(str(s["key"]), int(s["n_compromised"])) for s in SCENARIOS]
    for scenario, n_bad in cases:
        for seed in seeds(n_seeds):
            specs.append(
                RunSpec(
                    scenario=scenario,
                    n_compromised=n_bad,
                    seed=seed,
                    n_drones=n_drones,
                    max_ticks=max_ticks,
                    study="scenario",
                )
            )
    return specs


# ---------------------------------------------------------------------------
# Study 2 - three-configuration ablation
# ---------------------------------------------------------------------------

#: A: no VISHWAS at all - evidence is computed but nobody votes.  This is the
#:    baseline a conventional swarm gives you: the liar flies to the end.
#: B: consensus with uniform trust - every accuser's word weighs the same.
#:    Isolates what the outer trust layers (SBOM + perception) are worth.
#: C: full system.
ABLATION_CONFIGS: tuple[tuple[str, bool, bool, str], ...] = (
    ("A", False, False, "No VISHWAS (detection off)"),
    ("B", True, False, "Consensus, uniform trust"),
    ("C", True, True, "Full VISHWAS"),
)


def ablation_matrix(
    n_seeds: int = 5,
    n_drones: int = 9,
    max_ticks: int = 3000,
    scenarios: Sequence[str] | None = None,
) -> list[RunSpec]:
    """Same missions, three configurations, identical seeds.

    Paired seeds matter: A, B and C fly the same weather, the same target
    layout and the same attacker, so a difference between configurations is
    the configuration and not luck.
    """
    if scenarios is None:
        scenarios = ["none"] + [str(s["key"]) for s in SCENARIOS]
    n_bad_for = {str(s["key"]): int(s["n_compromised"]) for s in SCENARIOS}
    n_bad_for["none"] = 0

    specs: list[RunSpec] = []
    for label, consensus, priors, _desc in ABLATION_CONFIGS:
        for scenario in scenarios:
            for seed in seeds(n_seeds):
                specs.append(
                    RunSpec(
                        scenario=scenario,
                        n_compromised=n_bad_for[scenario],
                        seed=seed,
                        n_drones=n_drones,
                        max_ticks=max_ticks,
                        enable_consensus=consensus,
                        enable_trust_priors=priors,
                        study="ablation",
                        config_label=label,
                    )
                )
    return specs


# ---------------------------------------------------------------------------
# Study 3 - graceful degradation past the Byzantine bound
# ---------------------------------------------------------------------------


def degradation_matrix(
    n_seeds: int = 5,
    n_drones: int = 13,
    max_ticks: int = 2200,
    scenario: str = "collusion",
    max_compromised: int | None = None,
) -> list[RunSpec]:
    """Sweep the compromised fraction from 0 up to and past ``f = (n-1)/3``.

    The interesting part of this curve is the right-hand side.  With n = 13 the
    theory tolerates f = 4; the honest claim is not "it always works" but "it
    works up to the bound, and here is exactly how it fails after it".  Runs
    past the bound are kept in the output, flagged, and plotted.
    """
    f_tolerated = max(0, (n_drones - 1) // 3)
    if max_compromised is None:
        max_compromised = f_tolerated + 2
    specs: list[RunSpec] = []
    for n_bad in range(0, max_compromised + 1):
        for seed in seeds(n_seeds):
            specs.append(
                RunSpec(
                    scenario=scenario if n_bad else "none",
                    n_compromised=n_bad,
                    seed=seed,
                    n_drones=n_drones,
                    max_ticks=max_ticks,
                    study="degradation",
                )
            )
    return specs


# ---------------------------------------------------------------------------
# Study 5 - ClaimCheck: verified accusations vs. counted ones
# ---------------------------------------------------------------------------


def claimcheck_matrix(
    n_seeds: int = 5,
    n_drones: int = 9,
    max_ticks: int = 1400,
    scenarios: Sequence[str] = (
        "byzantine_accuser",
        "collusion",
        "position_teleport",
        "spoofed_anchor",
    ),
    max_compromised: int | None = None,
    certified_anchors: Sequence[int] = (),
) -> list[RunSpec]:
    """The verifier on and off, on identical seeds, across the whole sweep.

    Study 3 measures how VISHWAS degrades past the Byzantine bound.  This one
    measures what it degrades *into*, which is the part that matters
    operationally: a system that stops catching attackers has failed at its
    job, and a system that starts expelling honest aircraft has failed at
    something worse.  Every pair of rows here differs only in
    ``enable_claim_verifier``, so the difference between them is attributable
    to the audit and nothing else.

    Two numbers carry the result.  ``honest_expelled`` is the one to read
    first - the pre-ClaimCheck configuration convicts honest drones as soon as
    the compromised side becomes the majority, and the verified configuration
    should not, at any compromise level.  ``recall`` is the honest cost
    column: past the bound the verifier declines to convict on evidence it
    cannot independently re-derive, so it stops catching attackers rather than
    guessing at them.

    The third arm adds a hardware root of trust (``certified_anchors``, drone 0
    by default).  An anchor is a reference from *outside* the vote, so it
    settles which account of the swarm's geometry is real when the swarm alone
    cannot - and that is what lets independently verified evidence exist again
    past the bound.  It is the arm that recovers fabricator detection, which
    the verified-only arm deliberately gives up: measured over this sweep,
    ``byzantine_accuser`` at 3 of 9 compromised goes from 0/9 caught (vote
    only, and also verified-only) to 7/9, still with no honest drone expelled.
    """
    if max_compromised is None:
        max_compromised = n_drones - 2
    anchors = tuple(certified_anchors) if certified_anchors else (0,)
    # Three arms on identical seeds, matching the three assurance modes the
    # dashboard offers, so the table and the live demo cannot disagree.
    arms: tuple[tuple[str, bool, tuple[int, ...]], ...] = (
        ("vote_only", False, ()),
        ("verified", True, ()),
        ("anchored", True, anchors),
    )
    # ``spoofed_anchor`` models *one* attested drone whose GNSS is spoofed, and
    # it is capped here rather than swept to the same depth as the others.
    # Past a handful of spoofed drones it stops being that scenario and
    # becomes mass GNSS spoofing - a different threat with a different name,
    # which VISHWAS does not defend against and which is documented as its own
    # limitation with its own measurement.  Sweeping one scenario past its
    # threat model and reporting the failure under that scenario's name would
    # misattribute the result; naming the other threat separately is both more
    # honest and more useful.
    scenario_caps = {"spoofed_anchor": 3}

    specs: list[RunSpec] = []
    for label, verified, anchor_ids in arms:
        overrides: tuple[tuple[str, Any], ...] = (
            (("mesh.certified_anchors", anchor_ids),) if anchor_ids else ()
        )
        for scenario in scenarios:
            cap = min(max_compromised, scenario_caps.get(scenario, max_compromised))
            for n_bad in range(1, cap + 1):
                for seed in seeds(n_seeds):
                    specs.append(
                        RunSpec(
                            scenario=scenario,
                            n_compromised=n_bad,
                            seed=seed,
                            n_drones=n_drones,
                            max_ticks=max_ticks,
                            enable_claim_verifier=verified,
                            overrides=overrides,
                            study="claimcheck",
                            config_label=label,
                        )
                    )
    return specs


# ---------------------------------------------------------------------------
# Study 4 - CUSUM precision / latency trade-off
# ---------------------------------------------------------------------------

#: Alarm thresholds swept for the operating-curve figure.  Low h detects the
#: slow drift sooner and convicts honest drones more often; high h is patient
#: and safe.  Reporting the curve is the point - a single h is a choice, and a
#: choice made after seeing the test set is not a result.
CUSUM_THRESHOLDS: tuple[float, ...] = (2.0, 3.0, 4.0, 5.0, 6.5, 8.0, 10.0, 13.0)


def cusum_sweep_matrix(
    n_seeds: int = 5,
    n_drones: int = 9,
    max_ticks: int = 2200,
    thresholds: Sequence[float] = CUSUM_THRESHOLDS,
    scenarios: Sequence[str] = ("none", "slow_drift"),
) -> list[RunSpec]:
    n_bad_for = {str(s["key"]): int(s["n_compromised"]) for s in SCENARIOS}
    n_bad_for["none"] = 0
    specs: list[RunSpec] = []
    for h in thresholds:
        for scenario in scenarios:
            for seed in seeds(n_seeds):
                specs.append(
                    RunSpec(
                        scenario=scenario,
                        n_compromised=n_bad_for[scenario],
                        seed=seed,
                        n_drones=n_drones,
                        max_ticks=max_ticks,
                        overrides=(("evidence.cusum_threshold_h", float(h)),),
                        study="cusum",
                    )
                )
    return specs


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _mean(values: Sequence[float]) -> float | None:
    return float(np.mean(values)) if len(values) else None


def _std(values: Sequence[float]) -> float | None:
    return float(np.std(values, ddof=1)) if len(values) > 1 else (0.0 if values else None)


def summarise(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Collapse repeated runs of one condition into the numbers we report.

    Two deliberate choices:

    * Rates are **pooled**, not averaged over runs.  The false-exclusion rate
      is total honest drones expelled over total honest drones flown, so a run
      with more aircraft carries more weight - a mean of per-run ratios would
      quietly flatter small swarms.
    * Latency is averaged **only over runs where detection happened**, and is
      always reported next to ``detected_runs``.  Averaging a miss as if it
      were a fast detection is the most common way a detector is oversold.
    """
    ok = [r for r in rows if r.get("ok")]
    n_runs = len(rows)
    n_ok = len(ok)
    out: dict[str, Any] = {
        "n_runs": n_runs,
        "n_ok": n_ok,
        "n_failed": n_runs - n_ok,
    }
    if not ok:
        return out

    attackers = sum(len(r["compromised"]) for r in ok)
    caught = sum(len(r["true_positives"]) for r in ok)
    honest = sum(int(r["n_drones"]) - len(r["compromised"]) for r in ok)
    wrongly = sum(len(r["false_exclusions"]) for r in ok)

    latencies = [r["detection_latency_s"] for r in ok if r.get("detection_latency_s") is not None]
    detected_runs = sum(
        1 for r in ok if r["compromised"] and len(r["true_positives"]) == len(r["compromised"])
    )
    attacked_runs = sum(1 for r in ok if r["compromised"])

    out.update(
        {
            "attackers_total": attackers,
            "attackers_caught": caught,
            "recall": (caught / attackers) if attackers else None,
            "honest_total": honest,
            "honest_expelled": wrongly,
            "false_exclusion_rate": (wrongly / honest) if honest else None,
            "attacked_runs": attacked_runs,
            "fully_cleared_runs": detected_runs,
            "clearance_rate": (detected_runs / attacked_runs) if attacked_runs else None,
            "detected_runs": len(latencies),
            "detection_latency_s_mean": _mean(latencies),
            "detection_latency_s_std": _std(latencies),
            "detection_latency_s_p90": (
                float(np.percentile(latencies, 90)) if latencies else None
            ),
            "mission_success_rate": _mean([1.0 if r["mission_success"] else 0.0 for r in ok]),
            "coverage_mean": _mean([float(r["coverage"]) for r in ok]),
            "coverage_std": _std([float(r["coverage"]) for r in ok]),
            "targets_confirmed": sum(int(r["targets_confirmed"]) for r in ok),
            "targets_total": sum(int(r["targets_total"]) for r in ok),
            "reallocations_mean": _mean([float(r["reallocations"]) for r in ok]),
            "ms_per_drone_tick_mean": _mean([float(r["ms_per_drone_tick"]) for r in ok]),
            "ms_per_drone_tick_p90": float(
                np.percentile([float(r["ms_per_drone_tick"]) for r in ok], 90)
            ),
            "ticks_mean": _mean([float(r["ticks"]) for r in ok]),
            "wall_s_total": round(sum(float(r.get("wall_s", 0.0)) for r in rows), 2),
        }
    )
    # ClaimCheck outcomes.  Reported next to the exclusion numbers rather than
    # in a separate table because they are the same decision seen from the
    # other side: a honest drone held in quarantine is one the vote wanted
    # expelled and the audit would not certify.
    out.update(
        {
            "blocked_expulsions": sum(int(r.get("blocked_expulsions", 0)) for r in ok),
            "blocked_honest": sum(int(r.get("blocked_honest", 0)) for r in ok),
            "honest_quarantined": sum(
                len(r.get("quarantined_honest", ())) for r in ok
            ),
        }
    )
    return out


def aggregate(
    rows: Sequence[dict[str, Any]],
    group_by: Sequence[str],
) -> list[dict[str, Any]]:
    """Group result rows by the given fields and summarise each group."""
    groups: dict[tuple, list[dict[str, Any]]] = {}
    order: list[tuple] = []
    for row in rows:
        key = tuple(_group_value(row, f) for f in group_by)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    out = []
    for key in order:
        record = dict(zip(group_by, key))
        record.update(summarise(groups[key]))
        record["seeds"] = sorted({int(r["seed"]) for r in groups[key]})
        out.append(record)
    return out


def _group_value(row: dict[str, Any], field: str) -> Any:
    if field in row:
        value = row[field]
    elif field.startswith("overrides."):
        # Accept either the full dotted config path ("evidence.cusum_threshold_h")
        # or just the leaf name, so tables can ask for the readable one.
        wanted = field.split(".", 1)[1]
        overrides = row.get("overrides") or {}
        value = overrides.get(wanted)
        if value is None:
            value = next(
                (v for k, v in overrides.items() if k == wanted or k.endswith("." + wanted)),
                None,
            )
    else:
        value = None
    # Dicts and lists cannot key a group; render them stably instead.
    if isinstance(value, (dict, list)):
        return repr(sorted(value.items()) if isinstance(value, dict) else value)
    return value
