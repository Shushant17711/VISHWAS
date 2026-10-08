"""Result bundles: what gets written to disk, read by the dashboard, and
pasted into the report.

One rule governs this module: **nothing is computed here that is not also
stored here.**  Every bundle carries the raw per-run rows next to the
aggregated table, plus the seeds, the config and the machine it ran on.  A
judge who doubts a headline figure can recompute it from the same file.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from ..config import SimConfig
from .harness import environment

RESULTS_DIR = Path("data/results")


# ---------------------------------------------------------------------------
# Bundles
# ---------------------------------------------------------------------------


def bundle(
    name: str,
    title: str,
    rows: Sequence[dict[str, Any]],
    table: Sequence[dict[str, Any]],
    notes: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failed = [r for r in rows if not r.get("ok")]
    return {
        "name": name,
        "title": title,
        "notes": notes,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "environment": environment(),
        "baseline_config": SimConfig().to_dict(),
        "n_runs": len(rows),
        "n_failed": len(failed),
        "failures": [
            {"scenario": r.get("scenario"), "seed": r.get("seed"), "error": r.get("error")}
            for r in failed
        ],
        "table": list(table),
        "runs": [_strip(r) for r in rows],
        **(extra or {}),
    }


def _strip(row: dict[str, Any]) -> dict[str, Any]:
    """Drop the per-tick history from a stored row - bundles stay readable."""
    return {k: v for k, v in row.items() if k != "history"}


def write_bundle(data: dict[str, Any], out_dir: Path | str = RESULTS_DIR) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{data['name']}.json"
    path.write_text(json.dumps(data, indent=2, default=_fallback), encoding="utf-8")
    return path


def write_index(bundles: Sequence[dict[str, Any]], out_dir: Path | str = RESULTS_DIR) -> Path:
    """A manifest the dashboard loads first: what exists, and the headlines."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    index = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "environment": environment(),
        "bundles": [
            {
                "name": b["name"],
                "title": b["title"],
                "notes": b["notes"],
                "n_runs": b["n_runs"],
                "n_failed": b["n_failed"],
                "rows": len(b["table"]),
            }
            for b in bundles
        ],
        "headline": headline(bundles),
    }
    path = out / "index.json"
    path.write_text(json.dumps(index, indent=2, default=_fallback), encoding="utf-8")
    return path


def _fallback(obj: Any) -> Any:
    if hasattr(obj, "tolist"):
        return obj.tolist()
    if hasattr(obj, "__dict__"):
        return asdict(obj) if hasattr(obj, "__dataclass_fields__") else vars(obj)
    return str(obj)


# ---------------------------------------------------------------------------
# Headline numbers
# ---------------------------------------------------------------------------


def headline(bundles: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The five figures the pitch stands on - pulled from the bundles, never
    typed in by hand, so the slide can never drift from the experiment."""
    out: dict[str, Any] = {}
    by_name = {b["name"]: b for b in bundles}

    scen = by_name.get("scenarios")
    if scen:
        attacked = [r for r in scen["table"] if r.get("scenario") != "none"]
        control = next((r for r in scen["table"] if r.get("scenario") == "none"), None)
        lats = [r["detection_latency_s_mean"] for r in attacked if r.get("detection_latency_s_mean")]
        out["scenarios_evaluated"] = len(attacked)
        out["attackers_caught"] = sum(r.get("attackers_caught", 0) for r in attacked)
        out["attackers_total"] = sum(r.get("attackers_total", 0) for r in attacked)
        out["recall"] = _ratio(out["attackers_caught"], out["attackers_total"])
        out["mean_detection_latency_s"] = round(sum(lats) / len(lats), 2) if lats else None
        out["worst_detection_latency_s"] = round(max(lats), 2) if lats else None
        wrong = sum(r.get("honest_expelled", 0) for r in scen["table"])
        flown = sum(r.get("honest_total", 0) for r in scen["table"])
        out["false_exclusion_rate"] = _ratio(wrong, flown)
        out["honest_drones_flown"] = flown
        if control:
            out["control_false_exclusion_rate"] = control.get("false_exclusion_rate")
            out["control_mission_success_rate"] = control.get("mission_success_rate")
        succ = [r.get("mission_success_rate") for r in attacked if r.get("mission_success_rate") is not None]
        out["mission_success_under_attack"] = round(sum(succ) / len(succ), 3) if succ else None
        cpu = [r.get("ms_per_drone_tick_mean") for r in scen["table"] if r.get("ms_per_drone_tick_mean")]
        out["ms_per_drone_tick"] = round(sum(cpu) / len(cpu), 3) if cpu else None

    abl = by_name.get("ablation")
    if abl:
        out["ablation"] = {
            label: {
                "recall": _agg_ratio(abl["table"], label, "attackers_caught", "attackers_total"),
                "false_exclusion_rate": _agg_ratio(
                    abl["table"], label, "honest_expelled", "honest_total"
                ),
            }
            for label in sorted({r["config_label"] for r in abl["table"]})
        }

    deg = by_name.get("degradation")
    if deg:
        rows = sorted(deg["table"], key=lambda r: r.get("n_compromised", 0))
        holds = [r for r in rows if (r.get("clearance_rate") or 0) >= 1.0]
        out["degradation_last_clean_f"] = max((r["n_compromised"] for r in holds), default=0)
        out["degradation_curve"] = [
            {
                "n_compromised": r.get("n_compromised"),
                "recall": r.get("recall"),
                "false_exclusion_rate": r.get("false_exclusion_rate"),
                "mission_success_rate": r.get("mission_success_rate"),
            }
            for r in rows
        ]

    cc = by_name.get("claimcheck")
    if cc:
        # The one number the ClaimCheck pitch stands on: how many honest
        # aircraft each configuration expelled across the whole sweep.  Kept
        # as absolute counts on purpose - a rate over hundreds of
        # drone-missions rounds a real aircraft away to a decimal.
        # ``bundle()`` spreads ``extra`` at the top level, so ``by_config``
        # lives beside ``table`` rather than nested under it.
        rows = cc.get("by_config") or cc["table"]
        out["claimcheck"] = {
            label: {
                "recall": _agg_ratio(rows, label, "attackers_caught", "attackers_total"),
                "honest_expelled": sum(
                    r.get("honest_expelled", 0) or 0
                    for r in rows
                    if r.get("config_label") == label
                ),
                "honest_quarantined": sum(
                    r.get("honest_quarantined", 0) or 0
                    for r in rows
                    if r.get("config_label") == label
                ),
            }
            for label in sorted({r["config_label"] for r in rows})
        }
    return out


def _ratio(num: float | None, den: float | None) -> float | None:
    if not den:
        return None
    return round(float(num or 0) / float(den), 4)


def _agg_ratio(table: Sequence[dict[str, Any]], label: str, num: str, den: str) -> float | None:
    rows = [r for r in table if r.get("config_label") == label]
    return _ratio(sum(r.get(num, 0) or 0 for r in rows), sum(r.get(den, 0) or 0 for r in rows))


# ---------------------------------------------------------------------------
# Markdown - what goes in the written report
# ---------------------------------------------------------------------------


def markdown_table(
    rows: Sequence[dict[str, Any]],
    columns: Sequence[tuple[str, str]],
) -> str:
    """``columns`` is a sequence of (key, header) pairs."""
    head = "| " + " | ".join(h for _, h in columns) + " |"
    rule = "|" + "|".join("---" for _ in columns) + "|"
    body = [
        "| " + " | ".join(_fmt(r.get(k)) for k, _ in columns) + " |"
        for r in rows
    ]
    return "\n".join([head, rule, *body])


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") if abs(value) < 1000 else f"{value:.0f}"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) if value else "-"
    return str(value)


SCENARIO_COLUMNS: tuple[tuple[str, str], ...] = (
    ("scenario", "Scenario"),
    ("n_runs", "Runs"),
    ("attackers_total", "Attackers"),
    ("attackers_caught", "Caught"),
    ("recall", "Recall"),
    ("detection_latency_s_mean", "Latency (s)"),
    ("detection_latency_s_p90", "p90 (s)"),
    ("false_exclusion_rate", "False excl."),
    ("coverage_mean", "Coverage"),
    ("mission_success_rate", "Mission success"),
)

ABLATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("config_label", "Config"),
    ("scenario", "Scenario"),
    ("recall", "Recall"),
    ("detection_latency_s_mean", "Latency (s)"),
    ("false_exclusion_rate", "False excl."),
    ("mission_success_rate", "Mission success"),
)

DEGRADATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("n_compromised", "Compromised"),
    ("recall", "Recall"),
    ("clearance_rate", "Fully cleared"),
    ("false_exclusion_rate", "False excl."),
    ("coverage_mean", "Coverage"),
    ("mission_success_rate", "Mission success"),
)

#: The ClaimCheck table is read left to right as a single argument: at each
#: compromise level, how many attackers were caught, how many honest drones
#: were wrongly expelled, and - for the verified rows - where the expulsions
#: that did not happen went instead.  ``Honest expelled`` is the column the
#: whole study exists for, and it is deliberately an absolute count rather
#: than a rate: "0.04" invites rounding, "3 honest aircraft" does not.
CLAIMCHECK_COLUMNS: tuple[tuple[str, str], ...] = (
    ("config_label", "Accusations"),
    ("scenario", "Scenario"),
    ("n_compromised", "Compromised"),
    ("recall", "Recall"),
    ("honest_expelled", "Honest expelled"),
    ("honest_quarantined", "Honest held"),
    ("blocked_expulsions", "Expulsions blocked"),
    ("mission_success_rate", "Mission success"),
)

CUSUM_COLUMNS: tuple[tuple[str, str], ...] = (
    ("overrides.cusum_threshold_h", "Threshold h"),
    ("scenario", "Scenario"),
    ("recall", "Recall"),
    ("detection_latency_s_mean", "Latency (s)"),
    ("false_exclusion_rate", "False excl."),
)


def write_markdown(bundles: Sequence[dict[str, Any]], out_dir: Path | str = RESULTS_DIR) -> Path:
    by_name = {b["name"]: b for b in bundles}
    parts = ["# VISHWAS - evaluation results", ""]
    head = headline(bundles)
    parts += [
        "## Headline", "",
        f"- Attack scenarios evaluated: **{head.get('scenarios_evaluated', '-')}**",
        f"- Attackers detected and expelled: **{head.get('attackers_caught', '-')}"
        f"/{head.get('attackers_total', '-')}** (recall {head.get('recall', '-')})",
        f"- Mean detection latency: **{head.get('mean_detection_latency_s', '-')} s** "
        f"(worst scenario mean {head.get('worst_detection_latency_s', '-')} s)",
        f"- False-exclusion rate over {head.get('honest_drones_flown', '-')} honest drone-missions: "
        f"**{head.get('false_exclusion_rate', '-')}**",
        f"- Mission success under attack: **{head.get('mission_success_under_attack', '-')}**",
        f"- Per-drone cost: **{head.get('ms_per_drone_tick', '-')} ms / drone / tick**",
        "",
    ]
    cc_head = head.get("claimcheck")
    if cc_head:
        vote = cc_head.get("vote_only", {})
        ver = cc_head.get("verified", {})
        anc = cc_head.get("anchored", {})
        parts += [
            "### ClaimCheck", "",
            "Three assurance modes, identical missions and identical seeds. "
            "`vote_only` counts accusations; `verified` re-derives each one "
            "from the range measurements the swarm already broadcast; "
            "`anchored` adds one hardware-attested drone as a reference from "
            "outside the vote.",
            "",
            f"- Honest drones expelled — **counted**: "
            f"**{vote.get('honest_expelled', '-')}** · "
            f"**verified**: **{ver.get('honest_expelled', '-')}** · "
            f"**anchored**: **{anc.get('honest_expelled', '-')}**",
            f"- Recall — counted: **{vote.get('recall', '-')}** · "
            f"verified: **{ver.get('recall', '-')}** · "
            f"anchored: **{anc.get('recall', '-')}**",
            "",
            "The verified mode's recall is *lower by design*: past the "
            "Byzantine bound it declines to convict on evidence it cannot "
            "independently re-derive, and a gossip-only fabricator is "
            "disarmed rather than expelled. The anchored mode is what buys "
            "that recall back, because a root of trust settles which account "
            "of the geometry is real when the swarm alone cannot.",
            "",
        ]
    sections = [
        ("scenarios", "Attack scenarios", SCENARIO_COLUMNS),
        ("ablation", "Three-configuration ablation", ABLATION_COLUMNS),
        ("degradation", "Graceful degradation", DEGRADATION_COLUMNS),
        (
            "claimcheck",
            "ClaimCheck: verified accusations vs. counted ones",
            CLAIMCHECK_COLUMNS,
        ),
        ("cusum", "CUSUM precision / latency trade-off", CUSUM_COLUMNS),
    ]
    for name, title, cols in sections:
        b = by_name.get(name)
        if not b:
            continue
        parts += [f"## {title}", "", b.get("notes", ""), "", markdown_table(b["table"], cols), ""]

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "results.md"
    path.write_text("\n".join(parts), encoding="utf-8")
    return path
