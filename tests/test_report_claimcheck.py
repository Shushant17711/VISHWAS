"""The ClaimCheck study has to survive the trip into results.md.

Small test, specific purpose: the aggregation, headline and markdown layers
address rows by string key, and a mistyped key fails silently - the number
just renders as "-" and the claim quietly disappears from the report. That is
the worst failure mode a results pipeline has, because it looks like a
formatting nit and reads like a result.
"""

from __future__ import annotations

from vishwas.evaluation.report import CLAIMCHECK_COLUMNS, bundle, headline, write_markdown
from vishwas.evaluation.study import aggregate


def _row(label: str, n_bad: int, caught: list[int], wrong: list[int], held: list[int]) -> dict:
    return {
        "ok": True,
        "study": "claimcheck",
        "config_label": label,
        "scenario": "byzantine_accuser",
        "n_compromised": n_bad,
        "n_drones": 9,
        "seed": 7,
        "compromised": list(range(n_bad)),
        "true_positives": caught,
        "false_exclusions": wrong,
        "quarantined_honest": held,
        "blocked_expulsions": len(held),
        "blocked_honest": len(held),
        "detection_latency_s": None,
        "mission_success": True,
        "coverage": 0.9,
        "targets_confirmed": 2,
        "targets_total": 4,
        "reallocations": 0,
        "ms_per_drone_tick": 1.0,
        "ticks": 600,
        "wall_s": 1.0,
    }


def _bundle():
    rows = [
        _row("vote_only", 5, caught=[], wrong=[6, 7, 8], held=[]),
        _row("verified", 5, caught=[], wrong=[], held=[6]),
        _row("anchored", 5, caught=[0, 1], wrong=[], held=[]),
    ]
    table = aggregate(rows, ["config_label", "scenario", "n_compromised"])
    by_config = aggregate(rows, ["config_label", "n_compromised"])
    return bundle(
        "claimcheck", "ClaimCheck", rows, table, "notes", extra={"by_config": by_config}
    )


def test_summarise_carries_the_claimcheck_counts():
    table = _bundle()["table"]
    verified = next(r for r in table if r["config_label"] == "verified")
    vote_only = next(r for r in table if r["config_label"] == "vote_only")

    assert vote_only["honest_expelled"] == 3
    assert verified["honest_expelled"] == 0
    assert verified["honest_quarantined"] == 1
    assert verified["blocked_expulsions"] == 1


def test_headline_reads_the_by_config_rows():
    """Regression guard: ``bundle()`` spreads ``extra`` at the top level."""
    head = headline([_bundle()])

    assert head["claimcheck"]["vote_only"]["honest_expelled"] == 3
    assert head["claimcheck"]["verified"]["honest_expelled"] == 0
    assert head["claimcheck"]["anchored"]["honest_expelled"] == 0


def test_markdown_renders_every_declared_column(tmp_path):
    path = write_markdown([_bundle()], tmp_path)
    text = path.read_text(encoding="utf-8")

    for _, header in CLAIMCHECK_COLUMNS:
        assert header in text, f"column {header!r} missing from results.md"
    assert "**counted**: **3**" in text
    assert "**verified**: **0**" in text
    assert "**anchored**: **0**" in text
