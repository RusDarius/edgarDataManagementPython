"""Standalone upside trading-opportunity scanner entry point.

Runs ALONGSIDE ``run_edge_research_tools.py`` -- it is not wired into that
suite's ``run_integrated_extension_method``. It reuses a prior integrated
edge-research parent run's already-computed unified highlights CSV and raw
daily database (resolved via the earnings-priority lens's own manifest,
which already stores both paths) and produces its own
``upside_opportunity_scan`` folder inside that same parent run directory.

Usage:
- No CLI args: edit the parameters in ``run_upside_opportunity_scan_local_main``
  and run this file directly (matches the convention used by
  ``run_edge_historical_backtest.py``).
- CLI args: ``python src/run_upside_opportunity_scan.py --run-ref <path> ...``
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from edge_research_tools import run_resolution
from edge_research_tools.upside_move_potential_scanner import (
    DEFAULT_MOVE_HORIZON_DAYS,
    build_upside_move_potential_scan,
)
from edge_research_tools.upside_opportunity_scanner import (
    DEFAULT_TOP_COUNT,
    build_upside_opportunity_scan,
)

EARNINGS_PRIORITY_SUBDIR = "earnings_priority_lens"
EARNINGS_PRIORITY_MANIFEST_NAME = "edge_earnings_priority_manifest.json"
EARNINGS_PRIORITY_CANDIDATES_NAME = "edge_earnings_priority_candidates.csv"


def _resolve_lens_root(parent_run_dir: Path) -> Path:
    """Resolve the directory that actually holds the sibling lens folders.

    A bare ``run_integrated_extension_method``/``run_historic_current_aggregate_suite_preferred_markets``
    parent run keeps every lens (``earnings_priority_lens``, ``edge_unified_highlights``, etc.)
    directly under itself. The wrapping ``latest_500m_full_edge_research`` suite
    (``run_latest_500m_full_edge_research_suite``) instead nests those same lenses
    one level down, under an ``aggregate`` child folder. Resolve whichever one
    actually contains the earnings-priority lens.
    """
    if (parent_run_dir / EARNINGS_PRIORITY_SUBDIR).exists():
        return parent_run_dir
    aggregate_dir = parent_run_dir / "aggregate"
    if (aggregate_dir / EARNINGS_PRIORITY_SUBDIR).exists():
        return aggregate_dir
    return parent_run_dir


def _resolve_run_artifacts(lens_root: Path) -> dict[str, Path | None]:
    """Locate the unified highlights CSV and source database for a prior run.

    Reuses the earnings-priority lens's own manifest as the single source of
    truth, since that lens already resolves both paths (``unified_csv_path``
    and ``source_database_path``) for every integrated-suite run. This keeps
    the scanner from having to re-derive that resolution logic.
    """
    manifest_path = (
        lens_root / EARNINGS_PRIORITY_SUBDIR / EARNINGS_PRIORITY_MANIFEST_NAME
    )
    if not manifest_path.exists():
        raise FileNotFoundError(
            "Could not find an earnings-priority-lens manifest under "
            f"{lens_root.as_posix()}. The upside opportunity scanner reuses "
            "that lens's already-resolved unified-highlights CSV and source "
            "database paths, so run the integrated edge-research suite "
            "(run_edge_research_tools.py, which includes the earnings-priority "
            "lens) for this run first."
        )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    unified_csv_path = Path(str(payload["unified_csv_path"]))
    source_database_path = Path(str(payload["source_database_path"]))
    earnings_priority_csv_path = (
        lens_root / EARNINGS_PRIORITY_SUBDIR / EARNINGS_PRIORITY_CANDIDATES_NAME
    )
    return {
        "unified_csv_path": unified_csv_path,
        "source_database_path": source_database_path,
        "earnings_priority_csv_path": (
            earnings_priority_csv_path if earnings_priority_csv_path.exists() else None
        ),
    }


def run_upside_opportunity_scan_method(
    *,
    run_ref: str | Path | None = None,
    output_root: str | Path | None = None,
    top_count: int = DEFAULT_TOP_COUNT,
    use_earnings_priority: bool = True,
    auto_discover_latest: bool = True,
    duckdb_threads: int = 16,
    include_move_potential: bool = True,
    move_horizon_days: int = DEFAULT_MOVE_HORIZON_DAYS,
) -> dict[str, Any]:
    parent_run_dir = run_resolution.resolve_edge_parent_run_dir(
        run_ref=run_ref,
        output_root=output_root,
        auto_discover_latest=auto_discover_latest,
    )
    lens_root = _resolve_lens_root(parent_run_dir)
    artifacts = _resolve_run_artifacts(lens_root)
    output_dir = lens_root / "upside_opportunity_scan"

    result = build_upside_opportunity_scan(
        output_dir=output_dir,
        unified_csv_path=artifacts["unified_csv_path"],
        source_database_path=artifacts["source_database_path"],
        earnings_priority_csv_path=(
            artifacts["earnings_priority_csv_path"] if use_earnings_priority else None
        ),
        top_count=top_count,
        duckdb_threads=duckdb_threads,
    )
    result["parent_run_dir"] = parent_run_dir

    move_result: dict[str, Any] | None = None
    if include_move_potential:
        move_result = build_upside_move_potential_scan(
            output_dir=output_dir,
            unified_csv_path=artifacts["unified_csv_path"],
            source_database_path=artifacts["source_database_path"],
            earnings_priority_csv_path=(
                artifacts["earnings_priority_csv_path"]
                if use_earnings_priority
                else None
            ),
            opportunity_candidates_csv_path=result["candidates_csv"],
            top_count=top_count,
            horizon_days=move_horizon_days,
            duckdb_threads=duckdb_threads,
        )
    result["move_potential_scan_result"] = move_result
    return result


def _print_upside_opportunity_scan_result(result: dict[str, Any]) -> None:
    print(f"Parent run: {result['parent_run_dir']}")
    print(f"Candidates: {result['row_count']}")
    print(f"Candidates CSV: {result['candidates_csv']}")
    print(f"Top CSV: {result['top_csv']}")
    print(f"Database: {result['database_path']}")
    print(f"Report: {result['report_md']}")
    print(f"Tier counts: {result['tier_counts']}")
    move_result = result.get("move_potential_scan_result")
    if move_result:
        print(f"Move-potential candidates: {move_result['row_count']}")
        print(f"Move-potential CSV: {move_result['candidates_csv']}")
        print(f"Move-potential database: {move_result['database_path']}")
        print(f"Move-potential tiers: {move_result['tier_counts']}")

def run_upside_opportunity_scan_local_main() -> dict[str, Any]:
    """Edit the parameters below, then run this file directly."""
    # ===== edit params here =====
    run_ref: str | Path | None = None  # None -> auto-discover latest parent run
    output_root: str | Path | None = None
    top_count = DEFAULT_TOP_COUNT
    use_earnings_priority = True
    duckdb_threads = 16
    include_move_potential = True
    move_horizon_days = DEFAULT_MOVE_HORIZON_DAYS
    # ===== end params =====

    result = run_upside_opportunity_scan_method(
        run_ref=run_ref,
        output_root=output_root,
        top_count=top_count,
        use_earnings_priority=use_earnings_priority,
        duckdb_threads=duckdb_threads,
        include_move_potential=include_move_potential,
        move_horizon_days=move_horizon_days,
    )
    _print_upside_opportunity_scan_result(result)
    return result


def run_edge_research_and_upside_opportunity_scan_local_main() -> dict[str, Any]:
    """Full new-flow entry point: edit the parameters below, then run this file directly.

    Unlike ``run_upside_opportunity_scan_local_main`` (which assumes a prior
    edge-research parent run already exists), this method chains BOTH steps
    of the new flow in one call:

    1. Runs (or reuses, per its own auto-discovery logic) the main
       edge-research suite via ``run_edge_research_local_main()`` from
       ``run_edge_research_tools.py``.
    2. Layers the standalone upside opportunity scan on top of the resulting
       parent run directory.

    This mirrors the "edit params, run with no CLI args" convention used by
    ``run_edge_research_local_main`` itself, but as a single combined entry
    point for the whole new flow.
    """
    # ===== edit params here =====
    run_full_edge_research_suite_first = True
    # Only used when run_full_edge_research_suite_first is False.
    existing_run_ref: str | Path | None = None
    output_root: str | Path | None = None
    top_count = DEFAULT_TOP_COUNT
    use_earnings_priority = True
    duckdb_threads = 16
    include_move_potential = True
    move_horizon_days = DEFAULT_MOVE_HORIZON_DAYS
    # ===== end params =====

    edge_research_result: dict[str, Any] | None = None
    if run_full_edge_research_suite_first:
        from run_edge_research_tools import run_edge_research_local_main

        edge_research_result = run_edge_research_local_main()
        parent_run_dir = edge_research_result.get("parent_run_dir")
        if parent_run_dir is None:
            raise ValueError(
                "run_edge_research_local_main() did not return a parent_run_dir; "
                "cannot chain the upside opportunity scan onto it."
            )
    else:
        parent_run_dir = existing_run_ref

    scan_result = run_upside_opportunity_scan_method(
        run_ref=parent_run_dir,
        output_root=output_root,
        top_count=top_count,
        use_earnings_priority=use_earnings_priority,
        auto_discover_latest=parent_run_dir is None,
        duckdb_threads=duckdb_threads,
        include_move_potential=include_move_potential,
        move_horizon_days=move_horizon_days,
    )
    _print_upside_opportunity_scan_result(scan_result)
    return {
        "edge_research_result": edge_research_result,
        "upside_opportunity_scan_result": scan_result,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Standalone upside trading opportunity scanner. Complements "
            "run_edge_research_tools.py by reusing a prior integrated-suite "
            "parent run's unified highlights and raw daily database."
        )
    )
    parser.add_argument(
        "--run-ref",
        default=None,
        help=(
            "Prior parent run folder, snapshot DB, or suite manifest to reuse. "
            "Defaults to auto-discovering the most recently modified parent run."
        ),
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="Root folder to search for parent runs when --run-ref is omitted.",
    )
    parser.add_argument(
        "--top-count",
        type=int,
        default=DEFAULT_TOP_COUNT,
        help="Number of top-tier rows to write to the focused shortlist CSV.",
    )
    parser.add_argument(
        "--no-earnings-priority",
        action="store_true",
        help="Skip the earnings-priority catalyst-proximity signal even if available.",
    )
    parser.add_argument(
        "--full-flow",
        action="store_true",
        help=(
            "Run the full new flow: run/reuse the main edge-research suite via "
            "run_edge_research_local_main() first, then layer the upside "
            "opportunity scan on top of the resulting parent run."
        ),
    )
    parser.add_argument(
        "--no-move-potential",
        action="store_true",
        help=(
            "Skip the parallel Mag x Tilt x Catalyst move-potential ranking "
            "(separate DuckDB in the same upside_opportunity_scan folder)."
        ),
    )
    parser.add_argument(
        "--move-horizon-days",
        type=int,
        default=DEFAULT_MOVE_HORIZON_DAYS,
        help="Horizon in trading-day units for expected_move_proxy_pct (√T × ATRP).",
    )
    parser.add_argument("--duckdb-threads", type=int, default=16)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    if args.full_flow:
        from run_edge_research_tools import run_edge_research_local_main

        edge_research_result = run_edge_research_local_main()
        parent_run_dir = edge_research_result.get("parent_run_dir")
        if parent_run_dir is None:
            raise ValueError(
                "run_edge_research_local_main() did not return a parent_run_dir; "
                "cannot chain the upside opportunity scan onto it."
            )
        run_ref = parent_run_dir
    else:
        run_ref = args.run_ref

    result = run_upside_opportunity_scan_method(
        run_ref=run_ref,
        output_root=args.output_root,
        top_count=args.top_count,
        use_earnings_priority=not args.no_earnings_priority,
        duckdb_threads=args.duckdb_threads,
        include_move_potential=not args.no_move_potential,
        move_horizon_days=args.move_horizon_days,
    )
    _print_upside_opportunity_scan_result(result)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        main()
    else:
        run_upside_opportunity_scan_local_main()
