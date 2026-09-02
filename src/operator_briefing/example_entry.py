"""Compile the operator briefing pack.

Usage:
    python src/operator_briefing/example_entry.py
    python -m operator_briefing
    python src/operator_briefing/example_entry.py --pred-run move_prediction_...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from operator_briefing.compile import compile_briefing_pack  # noqa: E402


def main(argv: list[str] | None = None) -> dict[str, object]:
    parser = argparse.ArgumentParser(
        description="Lock latest scans into one agent-ready briefing pack."
    )
    parser.add_argument("--pred-run", default=None, help="Pin a move-prediction run_id.")
    parser.add_argument("--af-run", default=None, help="Pin an all-fields run_id.")
    parser.add_argument(
        "--output-root",
        default=None,
        help="Override logs/tradingview_analysis/operator_briefing/runs",
    )
    args = parser.parse_args(argv)
    pack = compile_briefing_pack(
        output_root=Path(args.output_root) if args.output_root else None,
        prediction_run_id=args.pred_run,
        all_fields_run_id=args.af_run,
    )
    output = pack.get("output") or {}
    print(json.dumps(
        {
            "run_id": pack.get("run_id"),
            "json": output.get("json"),
            "md": output.get("md"),
            "counts": pack.get("counts"),
            "us_2b": (pack.get("regime") or {}).get("us_2b"),
            "notes": (pack.get("sources") or {}).get("notes"),
        },
        indent=2,
    ))
    return pack


if __name__ == "__main__":
    main()
