"""Generic top-N ranking CLI. Thin wrapper around `focus.build_focus`
with no `--where`. No embedded eligibility cutoffs.

Usage:
    python src/generic_utils/rank_cli.py --csv PATH --field left --top 100
    python src/generic_utils/rank_cli.py --db PATH.duckdb --sql "SELECT symbol, d5 FROM conviction_rankings WHERE run_id = 'RUN'" --field d5 --top 50 --asc
    python src/generic_utils/rank_cli.py --csv PATH --field left --top 25 --group-field industry --cap 5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def main(argv: list[str] | None = None) -> dict[str, object]:
    from generic_utils.focus import build_focus
    from generic_utils.scan_sources import rows_from_csv, rows_from_duckdb

    parser = argparse.ArgumentParser(
        description=(
            "Generic top-N ranking over a CSV or DuckDB source. No embedded "
            "eligibility cutoffs -- you choose the field, direction, top-N, "
            "and optional group cap. Filter+rank+cap with --where lives on "
            "tv_scan_cli.py focus."
        )
    )
    parser.add_argument("--csv", default=None, help="Path to a CSV file.")
    parser.add_argument(
        "--db", default=None, help="Path to a DuckDB file (use with --sql)."
    )
    parser.add_argument("--sql", default=None, help="SQL to run against --db.")
    parser.add_argument("--field", required=True, help="Field name to rank by.")
    parser.add_argument("--top", type=int, default=100, help="How many rows to return.")
    parser.add_argument(
        "--asc", action="store_true", help="Ascending sort (default is descending)."
    )
    parser.add_argument("--tie-breaker", default=None, help="Secondary field for ties.")
    parser.add_argument(
        "--group-field", default=None, help="Cap rows per distinct value of this field."
    )
    parser.add_argument(
        "--cap",
        type=int,
        default=None,
        help="Max rows per group-field value (required with --group-field).",
    )
    parser.add_argument(
        "--exempt",
        default=None,
        help="Comma-separated id-field values exempt from the group cap.",
    )
    parser.add_argument(
        "--id-field",
        default="symbol",
        help="Row id field used for --exempt (default: symbol).",
    )
    args = parser.parse_args(argv)

    if args.csv and args.db:
        parser.error("Pass either --csv or --db, not both.")
    if args.csv:
        rows = rows_from_csv(args.csv)
    elif args.db:
        if not args.sql:
            parser.error("--db requires --sql.")
        rows = rows_from_duckdb(args.db, args.sql)
    else:
        parser.error("Pass --csv or --db.")

    if args.group_field and args.cap is None:
        parser.error("--group-field requires --cap.")

    payload = build_focus(
        rows,
        field=args.field,
        n=args.top,
        reverse=not args.asc,
        tie_breaker=args.tie_breaker,
        group_field=args.group_field,
        cap=args.cap,
        exempt=set(args.exempt.split(",")) if args.exempt else None,
        id_field=args.id_field,
    )
    print(json.dumps(payload, indent=2, default=str))
    return payload


if __name__ == "__main__":
    main()
