"""Run the focus pool screening flow.

Usage:
    python src/focus_pool_screening/example_entry.py
    python src/focus_pool_screening/example_entry.py --config src/focus_pool_screening/configs/default_wide_pool.json
    python src/focus_pool_screening/example_entry.py --top-n 25 --no-csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from focus_pool_screening.engine import run_focus_pool_screening  # noqa: E402


def main(argv: list[str] | None = None) -> dict[str, object]:
    parser = argparse.ArgumentParser(
        description="Deterministic focus pool screening (value / fundamental / "
        "technical / momentum + prediction & edge overlays)."
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to a focus_pool_screening_v1 JSON config "
        "(default: configs/default_wide_pool.json inside this package).",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=None,
        help="Override the per-lane / focus-section top-N in the log.",
    )
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help="Skip the focus_pool_rows.csv export (DuckDB is always written).",
    )
    args = parser.parse_args(argv)

    result = run_focus_pool_screening(
        config_path=args.config,
        top_n_override=args.top_n,
        write_csv_override=False if args.no_csv else None,
    )

    print(f"run_id:          {result['run_id']}")
    print(f"config_id:       {result['config_id']} (sha {result['config_hash']})")
    print(f"universe_size:   {result['universe_size']}")
    print(f"scored_rows:     {result['scored_rows']}")
    print(f"lane_counts:     {result['lane_counts']}")
    print(f"run_dir:         {result['run_dir']}")
    print(f"duckdb:          {result['duckdb_path']}")
    if result.get("csv_path"):
        print(f"csv:             {result['csv_path']}")
    print(f"overview_log:    {result['overview_log']}")
    return result


if __name__ == "__main__":
    main()
