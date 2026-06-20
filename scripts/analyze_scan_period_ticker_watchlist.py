#!/usr/bin/env python3
"""Analyze a ticker watchlist against a scan-period close-forward tracking run."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from data_analysis_scripts.trading_view_scan_period_ticker_watchlist import (
    run_scan_period_ticker_watchlist_analysis,
)

DEFAULT_RUN_ROOT = (
    PROJECT_ROOT
    / "logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs"
    / "scan_period_close_forward_tracking_29mar_15jun2026_b108820e"
)
DEFAULT_REGIME_CONTEXT_CONFIG = (
    PROJECT_ROOT / "config" / "regime_context" / "mar_jun_2026_risk_on_v1.json"
)
DEFAULT_REGIME_LOG = (
    PROJECT_ROOT
    / "logs/tradingview_analysis/prediction_analysis/duckdb_runs/iso_year=2026/week=25/runs"
    / "move_prediction_20260618_1442_utc_31b685b4/move_prediction__regime_context_focus.log"
)


def _parse_tickers(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_snapshot_days(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [part.strip() for part in raw.split(",") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Join a ticker watchlist to scan-period returns, predictor snapshots, "
            "and daily progression."
        )
    )
    parser.add_argument(
        "--run-root",
        type=Path,
        default=None,
        help=(
            "scan_period_close_forward_tracking run folder (period returns source). "
            "Optional if --tracking-id or --regime-context-config is set."
        ),
    )
    parser.add_argument(
        "--tracking-id",
        default=None,
        help="Alternative to --run-root when the run lives under pattern_analysis/runs/",
    )
    parser.add_argument(
        "--regime-context-config",
        type=Path,
        default=None,
        help=(
            "Regime JSON with scan_period_run_root (e.g. mar_jun_2026_risk_on_v1.json). "
            "Used when --run-root is omitted."
        ),
    )
    parser.add_argument(
        "--tickers",
        default=None,
        help="Comma-separated ticker list (e.g. SILEX,QS,SVMB)",
    )
    parser.add_argument(
        "--from-regime-log",
        type=Path,
        default=None,
        help="Parse tickers from move_prediction__regime_context_focus.log",
    )
    parser.add_argument(
        "--regime-section",
        default="playbook_a",
        choices=["playbook_a"],
        help="Section to parse when --from-regime-log is set",
    )
    parser.add_argument(
        "--snapshot-days",
        default="29_03_2026,15_06_2026",
        help="Comma-separated day labels for point-in-time predictor snapshots",
    )
    parser.add_argument(
        "--all-fields-root",
        type=Path,
        default=PROJECT_ROOT / "logs/tradingview_analysis/trading_view_all_fields_data",
        help="Root folder containing daily all-fields DuckDB exports",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override output folder (default: {run_root}/ticker_watchlist_analysis/{id}/)",
    )
    parser.add_argument(
        "--watchlist-id",
        default=None,
        help="Optional stable output folder name under ticker_watchlist_analysis/",
    )
    args = parser.parse_args()

    tickers = _parse_tickers(args.tickers)
    if not tickers and args.from_regime_log is None:
        args.from_regime_log = DEFAULT_REGIME_LOG

    run_root = args.run_root
    regime_context_config = args.regime_context_config
    if run_root is None and args.tracking_id is None and regime_context_config is None:
        regime_context_config = DEFAULT_REGIME_CONTEXT_CONFIG

    result = run_scan_period_ticker_watchlist_analysis(
        run_root=run_root,
        tracking_id=args.tracking_id,
        regime_context_config_path=regime_context_config,
        tickers=tickers,
        from_regime_log=args.from_regime_log,
        regime_section=args.regime_section,
        snapshot_day_labels=_parse_snapshot_days(args.snapshot_days),
        all_fields_root=args.all_fields_root,
        output_dir=args.output_dir,
        watchlist_id=args.watchlist_id,
    )
    print(f"Watchlist analysis complete: {result['output_dir']}")
    print(f"Matched {result['symbols_matched']}/{result['symbols_requested']} symbols")
    print(f"Summary log: {result['summary_log']}")
    if result["symbols_unmatched"]:
        print("Unmatched:", ", ".join(result["symbols_unmatched"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
