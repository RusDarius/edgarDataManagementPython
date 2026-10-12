from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from backtracking import DEFAULT_CONFIG_PATH  # noqa: E402
from backtracking.engine import (  # noqa: E402
    best_backtracking_run,
    days_backtracking_run,
    prepare_backtracking_run,
    score_backtracking_run,
    select_backtracking_run,
)


def _print_payload(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Weekly backtracking investing runs: build PIT panel, select picks, "
            "then score on later closes."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser(
        "prepare",
        help="Build weekly PIT panel and write a backtracking run folder.",
    )
    prepare.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Backtracking config JSON (default: {DEFAULT_CONFIG_PATH})",
    )
    prepare.add_argument(
        "--start-day",
        type=str,
        default=None,
        help="Optional start day label dd_mm_yyyy (e.g. 01_04_2026).",
    )
    prepare.add_argument(
        "--end-day",
        type=str,
        default=None,
        help="Optional end day label dd_mm_yyyy (e.g. 01_08_2026).",
    )
    prepare.add_argument(
        "--raw-preset",
        choices=["core", "focused"],
        default=None,
        help="Raw attachment set: core columns or focused-catalog numeric fields.",
    )

    select = subparsers.add_parser(
        "select",
        help="Run deterministic rank selection from panel_rows into picks_<selector>.csv.",
    )
    select.add_argument(
        "--run",
        type=Path,
        required=True,
        help="Backtracking run directory created by prepare.",
    )
    select.add_argument(
        "--selector",
        type=str,
        required=True,
        help="Selector label used in output file name (e.g. program_bo).",
    )
    select.add_argument(
        "--field",
        type=str,
        required=True,
        help="Ranking field from panel_rows (for example bo, left, edge_forward_valuation_upside_pct).",
    )
    select.add_argument("--top", type=int, default=15, help="Max names per decision week.")
    select.add_argument(
        "--where",
        type=str,
        default=None,
        help='Optional where clauses, same syntax as tv_scan_cli focus (for example "left>=25,rsi<=68").',
    )
    select.add_argument(
        "--tie-breaker",
        type=str,
        default=None,
        help="Optional tie-break column.",
    )
    select.add_argument(
        "--group-field",
        type=str,
        default=None,
        help="Optional grouping column for capped selection (requires --cap).",
    )
    select.add_argument("--cap", type=int, default=None, help="Per-group cap when using --group-field.")
    select.add_argument(
        "--ascending",
        action="store_true",
        help="Sort ascending instead of descending.",
    )

    days = subparsers.add_parser(
        "days",
        help="Attach session closes for panel symbols so returns can be marked day to day.",
    )
    days.add_argument("--run", type=Path, required=True, help="Backtracking run directory.")

    best = subparsers.add_parser(
        "best",
        help="Hindsight ceiling: best entry and exit per symbol, at most N names held at once.",
    )
    best.add_argument("--run", type=Path, required=True, help="Backtracking run directory.")
    best.add_argument("--max-names", type=int, default=20, help="Maximum concurrent holdings.")
    best.add_argument(
        "--min-sessions",
        type=int,
        default=5,
        help="Minimum sessions from entry close to exit close.",
    )

    score = subparsers.add_parser(
        "score",
        help="Score a picks CSV using the next decision week close (or window end for last week).",
    )
    score.add_argument(
        "--run",
        type=Path,
        required=True,
        help="Backtracking run directory.",
    )
    score.add_argument(
        "--picks",
        type=Path,
        required=True,
        help="Picks CSV path (relative to run dir or absolute).",
    )
    return parser


def main() -> dict[str, Any]:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "prepare":
        payload = prepare_backtracking_run(
            config_path=args.config,
            start_day_label=args.start_day,
            end_day_label=args.end_day,
            raw_preset=args.raw_preset,
        )
        _print_payload(payload)
        return payload
    if args.command == "select":
        payload = select_backtracking_run(
            run_dir=args.run,
            selector=args.selector,
            field=args.field,
            top=args.top,
            where=args.where,
            tie_breaker=args.tie_breaker,
            group_field=args.group_field,
            cap=args.cap,
            reverse=not args.ascending,
        )
        _print_payload(payload)
        return payload
    if args.command == "days":
        payload = days_backtracking_run(run_dir=args.run)
        _print_payload(payload)
        return payload
    if args.command == "best":
        payload = best_backtracking_run(
            run_dir=args.run,
            max_names=args.max_names,
            min_sessions=args.min_sessions,
        )
        _print_payload(payload)
        return payload
    payload = score_backtracking_run(run_dir=args.run, picks_path=args.picks)
    _print_payload(payload)
    return payload


if __name__ == "__main__":
    main()
