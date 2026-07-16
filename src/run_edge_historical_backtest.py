from __future__ import annotations

from pathlib import Path
from typing import Any

from edge_research_tools.config import DEFAULT_EDGE_RESEARCH_ROOT
from edge_research_tools.historical_edge_backtest import run_historical_edge_backtest


def discover_latest_edge_snapshot() -> Path:
    candidates = list(
        DEFAULT_EDGE_RESEARCH_ROOT.glob(
            "**/edge_feature_snapshot_*/symbol_day_feature_snapshot.duckdb"
        )
    )
    if not candidates:
        raise FileNotFoundError("No edge feature snapshot database was found.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def run_edge_historical_backtest_local_main() -> dict[str, Any]:
    """Edit the parameters below, then run this file directly."""
    # ===== edit params here =====
    snapshot_database_path = discover_latest_edge_snapshot()
    output_database_path = (
        DEFAULT_EDGE_RESEARCH_ROOT
        / "historical_backtests"
        / "historical_edge_backtest.duckdb"
    )
    anchor_mode = "twice_weekly"  # or "every_n_trading_days"
    spacing_trading_days = 3
    start_date: str | None = None
    end_date: str | None = None
    top_n = 250
    incremental = True
    duckdb_threads = 16
    memory_limit_gb = 24.0
    # ===== end params =====

    result = run_historical_edge_backtest(
        snapshot_database_path=snapshot_database_path,
        output_database_path=output_database_path,
        anchor_mode=anchor_mode,
        spacing_trading_days=spacing_trading_days,
        start_date=start_date,
        end_date=end_date,
        top_n=top_n,
        incremental=incremental,
        duckdb_threads=duckdb_threads,
        memory_limit_gb=memory_limit_gb,
    )
    print(f"Historical edge database: {result['database_path']}")
    print(f"Anchors: {result['anchor_count']} ({result['new_anchor_count']} new)")
    print(f"Candidates: {result['candidate_count']}")
    print(f"Report: {result['report_md']}")
    return result


if __name__ == "__main__":
    run_edge_historical_backtest_local_main()
