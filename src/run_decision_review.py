"""Review briefing top-100 ranks against later pack closes.

No CLI args: edit the knobs in ``run_decision_review_local_main`` and run:

    python src/run_decision_review.py

CLI overrides those same knobs:

    python src/run_decision_review.py --top 100

DuckDB, queries.txt, overview.log, and highlights.log land under
logs/tradingview_analysis/operator_briefing/runs/decision_review_<stamp>/.

This does not rerun src/run_backtests.py. An existing family_metrics.csv
is attached as signal-family support when one is on disk.
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from generic_utils.decision_review import (
    actions_from_priors,
    build_decision_review,
    format_highlights,
    latest_family_metrics,
    load_briefing_rank_sessions,
    load_signal_support,
    load_spec,
)
from generic_utils.run_export import write_run_export

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs"
DEFAULT_PACK_ROOT = DEFAULT_OUTPUT_ROOT
DEFAULT_PRIORS = PROJECT_ROOT / "logs" / "AI_ANALYSIS_UTILS" / "priors"
DEFAULT_RECIPE = PROJECT_ROOT / "config" / "generic_utils" / "decision_review.json"
DEFAULT_BACKTESTS = PROJECT_ROOT / "logs" / "tradingview_analysis" / "backtests" / "runs"


def run_decision_review_method(
    *,
    top: int = 100,
    head: int = 20,
    tail: int = 20,
    min_n: int = 12,
    gap_pct: float = 1.0,
    runs_root: str | Path | None = None,
    priors_dir: str | Path | None = None,
    family_metrics: str | Path | None = None,
    output_root: str | Path | None = None,
    recipe_path: str | Path | None = None,
) -> dict[str, Any]:
    """Score one top list per briefing day and write the suggestion matrix."""
    spec = load_spec(recipe_path or DEFAULT_RECIPE)
    spec["top"] = float(top)
    spec["head"] = float(head)
    spec["tail"] = float(tail)
    spec["min_n"] = float(min_n)
    spec["gap_pct"] = float(gap_pct)
    packs = Path(runs_root) if runs_root else DEFAULT_PACK_ROOT
    sessions, marks, notes = load_briefing_rank_sessions(packs, top=int(spec["top"]))
    latest_day = sessions[-1]["day"] if sessions else ""
    metrics_path = Path(family_metrics) if family_metrics else latest_family_metrics(DEFAULT_BACKTESTS)
    support = load_signal_support(metrics_path)
    if metrics_path is None:
        notes.append("no family_metrics.csv; signal IC not attached")
    else:
        notes.append(f"signal support {metrics_path.as_posix()}")
    payload = build_decision_review(
        sessions,
        marks,
        spec=spec,
        actions_by_day=actions_from_priors(priors_dir or DEFAULT_PRIORS),
        latest_day=latest_day,
        signal_support=support,
    )
    created = datetime.now(tz=timezone.utc)
    run_id = f"decision_review_{created.strftime('%Y%m%d_%H%M')}_utc_{uuid.uuid4().hex[:8]}"
    root = Path(output_root) if output_root else DEFAULT_OUTPUT_ROOT
    out_dir = root / run_id
    notes.extend(
        [
            "Top list is sleeves.radar_upside_100 in published order.",
            "A day short of --top is filled from that pack's us2b tape by rr. rank_source names the fill.",
            "min_n and gap_pct decide KEEP versus CHANGE. They do not drop a name from the top list.",
            "src/backtests family_metrics is support. It is not a briefing decision score. Do not rerun it for this review.",
            "Edge and prediction profile ranks can use the same scorer later. They are not a second module.",
        ]
    )
    exported = write_run_export(
        out_dir,
        tool="decision_review",
        tables={
            "coverage": payload["coverage"],
            "quality": payload["quality"],
            "ranks": payload["ranks"],
            "decisions": payload["decisions"],
            "matrix": payload["matrix"],
            "matrix_to_latest": payload["matrix_to_latest"],
            "signal_support": payload["signal_support"],
        },
        sources={
            "runs": packs.as_posix(),
            "priors": str(priors_dir or DEFAULT_PRIORS),
            "family_metrics": metrics_path.as_posix() if metrics_path else None,
            "recipe": str(recipe_path or DEFAULT_RECIPE),
        },
        schema_version="decision_review_v1",
        notes=notes,
        extra={
            "n_sessions": payload["spec"]["n_sessions"],
            "n_ranks": payload["spec"]["n_ranks"],
            "days_at_top": payload["spec"]["days_at_top"],
        },
        duckdb_name="decision_review.duckdb",
    )
    highlights_path = out_dir / "highlights.log"
    highlights_path.write_text(format_highlights(payload), encoding="utf-8")
    (out_dir / "spec.json").write_text(
        json.dumps({"run_id": run_id, **payload["spec"]}, indent=2, default=str),
        encoding="utf-8",
    )
    return {
        "run_id": run_id,
        "out_dir": out_dir.as_posix(),
        "duckdb": exported["duckdb"],
        "queries": exported["query_guide"],
        "overview_log": exported["overview_log"],
        "highlights_log": highlights_path.as_posix(),
        "tables": exported["tables"],
        "spec": payload["spec"],
        "matrix": payload["matrix"],
    }


def _print_result(result: dict[str, Any]) -> None:
    print(f"Run: {result['run_id']}")
    print(f"Folder: {result['out_dir']}")
    print(f"DuckDB: {result['duckdb']}")
    print(f"Highlights: {result['highlights_log']}")
    print(
        "Coverage: "
        f"sessions={result['spec']['n_sessions']} "
        f"ranks={result['spec']['n_ranks']} "
        f"days_at_top={result['spec']['days_at_top']}"
    )
    for row in result["matrix"]:
        print(f"  next {row['call']:<6} {row['lever']}")


def run_decision_review_local_main() -> dict[str, Any]:
    """Edit the review size here, then run this file with no arguments."""
    # ===== edit params here =====
    # How many published ranks to keep on each briefing day.
    top = 100
    # Head and tail of that list, compared in the suggestion matrix.
    head = 20
    tail = 20
    # A slice under this count is THIN. It does not remove names from the top list.
    min_n = 12
    # Median gap, in return percent, before KEEP or CHANGE.
    gap_pct = 1.0
    runs_root = None
    priors_dir = None
    family_metrics = None
    output_root = None
    # ===== end params =====

    result = run_decision_review_method(
        top=top,
        head=head,
        tail=tail,
        min_n=min_n,
        gap_pct=gap_pct,
        runs_root=runs_root,
        priors_dir=priors_dir,
        family_metrics=family_metrics,
        output_root=output_root,
    )
    _print_result(result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review briefing top ranks against later closes.")
    parser.add_argument("--top", type=int, default=100)
    parser.add_argument("--head", type=int, default=20)
    parser.add_argument("--tail", type=int, default=20)
    parser.add_argument("--min-n", type=int, default=12)
    parser.add_argument("--gap-pct", type=float, default=1.0)
    parser.add_argument("--runs", default=None, help="operator_briefing/runs folder.")
    parser.add_argument("--priors", default=None)
    parser.add_argument("--family-metrics", default=None, help="Existing family_metrics.csv. Default: newest backtest run.")
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--recipe", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    result = run_decision_review_method(
        top=args.top,
        head=args.head,
        tail=args.tail,
        min_n=args.min_n,
        gap_pct=args.gap_pct,
        runs_root=args.runs,
        priors_dir=args.priors,
        family_metrics=args.family_metrics,
        output_root=args.output_root,
        recipe_path=args.recipe,
    )
    _print_result(result)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        main()
    else:
        run_decision_review_local_main()
