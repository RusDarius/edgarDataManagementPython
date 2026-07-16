"""Standalone entry point for the upside opportunity momentum backtest.

Runs ALONGSIDE ``run_edge_research_tools.py`` and ``run_upside_opportunity_scan.py``.
Validates the momentum sleeve of the upside opportunity scanner against the
full historical date range available in a symbol-day feature snapshot
database (see ``edge_research_tools.upside_opportunity_backtest`` for the
explicit scope boundary: only the momentum component is backtested).

Usage:
- No CLI args: edit the parameters in
  ``run_upside_opportunity_backtest_local_main`` and run this file directly
  (matches the convention used by ``run_edge_historical_backtest.py``).
- CLI args: ``python src/run_upside_opportunity_backtest.py --snapshot-db <path> ...``
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Sequence

from edge_research_tools.config import DEFAULT_EDGE_RESEARCH_ROOT
from edge_research_tools.upside_opportunity_backtest import (
    DEFAULT_BUCKET_COUNT,
    run_upside_momentum_backtest,
)


def discover_latest_edge_snapshot() -> Path:
    candidates = list(
        DEFAULT_EDGE_RESEARCH_ROOT.glob(
            "**/edge_feature_snapshot_*/symbol_day_feature_snapshot.duckdb"
        )
    )
    if not candidates:
        raise FileNotFoundError("No edge feature snapshot database was found.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _print_backtest_result(result: dict[str, Any]) -> None:
    print(f"Observations: {result['observation_count']}")
    print(f"Resolved horizon: {result['resolved_horizon']}d")
    print(f"Bucket results CSV: {result['buckets_csv']}")
    print(f"Database: {result['database_path']}")
    print(f"Report: {result['report_md']}")


def run_upside_opportunity_backtest_local_main() -> dict[str, Any]:
    """Edit the parameters below, then run this file directly."""
    # ===== edit params here =====
    snapshot_database_path = discover_latest_edge_snapshot()
    output_dir = (
        DEFAULT_EDGE_RESEARCH_ROOT
        / "historical_backtests"
        / "upside_opportunity_momentum_backtest"
    )
    markets: list[str] | None = None
    countries: list[str] | None = None
    exchanges: list[str] | None = None
    us_only = False
    start_date: str | None = None
    end_date: str | None = None
    ranking_horizon = 5
    bucket_count = DEFAULT_BUCKET_COUNT
    duckdb_threads = 16
    # ===== end params =====

    result = run_upside_momentum_backtest(
        output_dir=output_dir,
        snapshot_database_path=snapshot_database_path,
        markets=markets,
        countries=countries,
        exchanges=exchanges,
        us_only=us_only,
        start_date=start_date,
        end_date=end_date,
        ranking_horizon=ranking_horizon,
        bucket_count=bucket_count,
        duckdb_threads=duckdb_threads,
    )
    _print_backtest_result(result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reduced-form historical backtest of the upside opportunity "
            "scanner's momentum sleeve."
        )
    )
    parser.add_argument(
        "--snapshot-db",
        default=None,
        help="Path to symbol_day_feature_snapshot.duckdb. Defaults to auto-discovering the latest one.",
    )
    parser.add_argument(
        "--output-dir", default=None, help="Output folder for the backtest artifacts."
    )
    parser.add_argument("--ranking-horizon", type=int, default=5)
    parser.add_argument("--bucket-count", type=int, default=DEFAULT_BUCKET_COUNT)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--us-only", action="store_true")
    parser.add_argument("--duckdb-threads", type=int, default=16)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    snapshot_database_path = (
        Path(args.snapshot_db) if args.snapshot_db else discover_latest_edge_snapshot()
    )
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else DEFAULT_EDGE_RESEARCH_ROOT
        / "historical_backtests"
        / "upside_opportunity_momentum_backtest"
    )
    result = run_upside_momentum_backtest(
        output_dir=output_dir,
        snapshot_database_path=snapshot_database_path,
        us_only=args.us_only,
        start_date=args.start_date,
        end_date=args.end_date,
        ranking_horizon=args.ranking_horizon,
        bucket_count=args.bucket_count,
        duckdb_threads=args.duckdb_threads,
    )
    _print_backtest_result(result)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        main()
    else:
        run_upside_opportunity_backtest_local_main()
