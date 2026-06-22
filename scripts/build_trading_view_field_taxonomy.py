#!/usr/bin/env python
"""CLI entry point for TradingView field taxonomy generation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from data_analysis_scripts.trading_view_field_taxonomy_builder import (  # noqa: E402
    DEFAULT_FIELD_CATALOG,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PROFILE_DIR,
    DEFAULT_SCAN_RUN_ROOT,
    build_trading_view_field_taxonomy,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build TradingView field taxonomy splits by meaning, usage, and scan relevance."
    )
    parser.add_argument(
        "--field-catalog",
        type=Path,
        default=DEFAULT_FIELD_CATALOG,
        help="Path to trading_view_stock_fields.csv",
    )
    parser.add_argument(
        "--scan-run-root",
        type=Path,
        default=DEFAULT_SCAN_RUN_ROOT,
        help="Scan-period tracking run root with period_total and rolling_windows outputs",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory for taxonomy artifacts",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=DEFAULT_PROFILE_DIR,
        help="Move-prediction profile JSON directory for usage cross-reference",
    )
    parser.add_argument(
        "--stability-min-runs",
        type=int,
        default=None,
        help="Minimum rolling windows required for stability gate",
    )
    parser.add_argument(
        "--sign-consistency",
        type=float,
        default=None,
        help="Minimum sign consistency ratio for stability gate",
    )
    args = parser.parse_args()

    result = build_trading_view_field_taxonomy(
        field_catalog=args.field_catalog,
        scan_run_root=args.scan_run_root,
        output_dir=args.output_dir,
        profile_dir=args.profile_dir,
        stability_min_runs=args.stability_min_runs,
        sign_consistency_gate=args.sign_consistency,
    )
    print(f"Wrote taxonomy to {result['output_dir']}")
    print(f"Manifest: {result['manifest_path']}")
    print(
        f"Variants: {result['variant_count']} | Families: {result['family_count']} | "
        f"Tracking: {result['tracking_id']}"
    )


if __name__ == "__main__":
    main()
