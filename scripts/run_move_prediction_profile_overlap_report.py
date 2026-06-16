#!/usr/bin/env python3
"""Emit top-N Jaccard overlap report for a move-prediction profile suite."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from data_analysis_scripts.trading_view_move_prediction_overlap_report import (  # noqa: E402
    run_suite_overlap_report,
)
from data_loaders.api_tradingview_client import ApiTradingViewClient  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run move-prediction profile overlap (Jaccard) report."
    )
    parser.add_argument(
        "--suite",
        type=Path,
        default=PROJECT_ROOT
        / "config"
        / "move_prediction_profiles"
        / "suites"
        / "active_manager_v3.json",
        help="Path to suite manifest JSON.",
    )
    parser.add_argument(
        "--scan-json",
        type=Path,
        default=None,
        help="Optional JSON file with scan rows (list of dicts).",
    )
    parser.add_argument(
        "--min-market-cap-usd",
        type=float,
        default=1_000_000_000,
        help="Minimum market cap filter.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Top-N names per profile for Jaccard comparison.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write overlap report JSON to this path.",
    )
    parser.add_argument(
        "--live-scan",
        action="store_true",
        help="Fetch live scan from TradingView API (requires credentials).",
    )
    return parser.parse_args()


def _load_scan_data(args: argparse.Namespace) -> list[dict]:
    if args.scan_json is not None:
        payload = json.loads(args.scan_json.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "data" in payload:
            return list(payload["data"])
        if isinstance(payload, list):
            return payload
        raise ValueError("--scan-json must contain a list or {\"data\": [...]}.")

    if args.live_scan:
        client = ApiTradingViewClient()
        response = client.scan_global_market_move_prediction(
            min_market_cap_usd=args.min_market_cap_usd,
        )
        if isinstance(response, dict) and "data" in response:
            return list(response["data"])
        return list(response)

    raise SystemExit(
        "Provide --scan-json <path> or --live-scan. "
        "Without live API access, pass saved scan rows as JSON."
    )


def main() -> int:
    args = _parse_args()
    scan_data = _load_scan_data(args)
    output_path = args.output
    if output_path is None:
        suite_stem = args.suite.stem
        output_path = (
            PROJECT_ROOT
            / "logs"
            / "tradingview_analysis"
            / "prediction_analysis"
            / f"{suite_stem}__overlap_report.json"
        )

    report = run_suite_overlap_report(
        scan_data,
        profile_suite_path=args.suite,
        top_n=args.top_n,
        min_market_cap_usd=args.min_market_cap_usd,
        output_path=output_path,
    )

    gate_failures = report.get("gate_failures", [])
    print(f"Suite: {report.get('suite_id')} ({len(report.get('profile_names', []))} profiles)")
    print(f"Wrote: {report.get('output_path')}")
    print(f"Gate failures (weeks/months): {len(gate_failures)}")
    for failure in gate_failures[:20]:
        print(
            f"  [{failure['gate']}] {failure['horizon']}: "
            f"{failure['left_profile']} vs {failure['right_profile']} "
            f"Jaccard={failure['jaccard_top_n']:.3f} (max {failure['max_allowed']})"
        )
    return 1 if gate_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
