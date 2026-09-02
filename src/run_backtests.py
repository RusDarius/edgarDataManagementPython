from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from backtests import DEFAULT_CONFIG_PATH, run_backtests  # noqa: E402
from backtests.reporting import finalize_backtest_run  # noqa: E402


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generic backtests for move-predict + all-fields + timing + upside + "
            "financial projection evidence."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Backtests config JSON (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--start-day",
        type=str,
        default=None,
        help="Optional start day label dd_mm_yyyy (e.g. 01_06_2026).",
    )
    parser.add_argument(
        "--end-day",
        type=str,
        default=None,
        help="Optional end day label dd_mm_yyyy (e.g. 30_08_2026).",
    )
    parser.add_argument(
        "--max-days",
        type=int,
        default=None,
        help="Optional cap for the number of latest all-fields days to include.",
    )
    parser.add_argument(
        "--replay-projection",
        action="store_true",
        help="Reserved for future historical projection replay mode (not implemented).",
    )
    parser.add_argument(
        "--replay-timing",
        action="store_true",
        help="Reserved for future historical timing replay mode (not implemented).",
    )
    parser.add_argument(
        "--finalize-run",
        type=Path,
        default=None,
        help=(
            "Write evidence_report.md / run_manifest.json / DuckDB from an existing "
            "run directory whose CSVs already exist (for example after a DuckDB OOM)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.replay_projection:
        raise NotImplementedError(
            "--replay-projection is reserved for a future issue. "
            "Current mode consumes already-written projection artifacts."
        )
    if args.replay_timing:
        raise NotImplementedError(
            "--replay-timing is reserved for a future issue. "
            "Current mode consumes already-written timing artifacts plus reconstruct."
        )
    if args.finalize_run is not None:
        result = finalize_backtest_run(args.finalize_run, config_path=args.config)
        print(f"run_id:            {result['run_id']}")
        print(f"run_dir:           {result['run_dir']}")
        print(f"database_path:     {result['database_path']}")
        print(f"report_path:       {result['report_path']}")
        print(f"manifest_path:     {result['manifest_path']}")
        print(f"calendar_day_count:{result.get('calendar_day_count')}")
        print(f"signal_row_count:  {result.get('signal_row_count')}")
        print(f"outcome_row_count: {result.get('outcome_row_count')}")
        if result.get("duckdb_error"):
            print(f"duckdb_error:      {result['duckdb_error']}")
        return result

    result = run_backtests(
        config_path=args.config,
        start_day_label=args.start_day,
        end_day_label=args.end_day,
        max_days=args.max_days,
    )
    print(f"run_id:            {result['run_id']}")
    print(f"run_dir:           {result['run_dir']}")
    print(f"database_path:     {result['database_path']}")
    print(f"report_path:       {result['report_path']}")
    print(f"manifest_path:     {result['manifest_path']}")
    print(f"calendar_day_count:{result['calendar_day_count']}")
    print(f"signal_row_count:  {result['signal_row_count']}")
    print(f"outcome_row_count: {result['outcome_row_count']}")
    if result.get("duckdb_error"):
        print(f"duckdb_error:      {result['duckdb_error']}")
    return result


if __name__ == "__main__":
    main()

