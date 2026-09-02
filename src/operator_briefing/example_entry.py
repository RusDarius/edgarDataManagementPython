"""Operator briefing CLI: compile / inspect / lookup / compare / stance.

Usage:
    python src/operator_briefing/example_entry.py
    python src/operator_briefing/example_entry.py compile --pred-run RUN --af-run RUN
    python src/operator_briefing/example_entry.py inspect
    python src/operator_briefing/example_entry.py lookup MU SNDK PGY
    python src/operator_briefing/example_entry.py compare
    python src/operator_briefing/example_entry.py compare --run-a RUN --run-b RUN
    python src/operator_briefing/example_entry.py stance MU SNDK
    python -m operator_briefing inspect
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

COMMANDS = ("compile", "inspect", "lookup", "compare", "stance")


def _dumps(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2, default=str)


def _cmd_compile(argv: list[str]) -> dict[str, object]:
    from operator_briefing.compile import compile_briefing_pack

    parser = argparse.ArgumentParser(description="Lock latest scans into one agent-ready briefing pack.")
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
    primary = pack.get("primary_course") or {}
    print(_dumps(
        {
            "run_id": pack.get("run_id"),
            "json": output.get("json"),
            "md": output.get("md"),
            "counts": pack.get("counts"),
            "us_2b": (pack.get("regime") or {}).get("us_2b"),
            "primary_course": {
                "bias": primary.get("bias"),
                "do": primary.get("do"),
                "do_not": primary.get("do_not"),
            },
            "notes": (pack.get("sources") or {}).get("notes"),
        }
    ))
    return pack


def _cmd_inspect(argv: list[str]) -> dict[str, object]:
    from operator_briefing.inspect_sources import inspect_locked_sources

    parser = argparse.ArgumentParser(description="Describe locked DuckDB sources, tables, and columns.")
    parser.add_argument("--pred-run", default=None)
    parser.add_argument("--af-run", default=None)
    args = parser.parse_args(argv)
    payload = inspect_locked_sources(
        prediction_run_id=args.pred_run,
        all_fields_run_id=args.af_run,
    )
    print(_dumps(payload))
    return payload


def _cmd_lookup(argv: list[str]) -> dict[str, object]:
    from operator_briefing.lookup import lookup_symbols

    parser = argparse.ArgumentParser(description="Dossier: raw + progression + stance for named symbols.")
    parser.add_argument("symbols", nargs="+", help="Tickers or EXCHANGE:TICKER")
    parser.add_argument("--pack", default=None, help="Path to briefing_pack.json")
    args = parser.parse_args(argv)
    payload = lookup_symbols(args.symbols, pack_path=args.pack)
    print(_dumps(payload))
    return payload


def _cmd_compare(argv: list[str]) -> dict[str, object]:
    from operator_briefing.compare import compare_prediction_runs

    parser = argparse.ArgumentParser(description="Compare two move-prediction runs (profile + mix deltas).")
    parser.add_argument("--run-a", default=None, help="Prior run_id (default: previous in run_metadata)")
    parser.add_argument("--run-b", default=None, help="Current run_id (default: latest)")
    parser.add_argument("--top", type=int, default=40)
    args = parser.parse_args(argv)
    payload = compare_prediction_runs(run_a=args.run_a, run_b=args.run_b, top_n=args.top)
    print(_dumps(payload))
    return payload


def _cmd_stance(argv: list[str]) -> dict[str, object]:
    from operator_briefing.lookup import lookup_symbols

    parser = argparse.ArgumentParser(description="Suggested course + support for named symbols.")
    parser.add_argument("symbols", nargs="+")
    parser.add_argument("--pack", default=None)
    args = parser.parse_args(argv)
    lookup = lookup_symbols(args.symbols, pack_path=args.pack)
    payload = {
        "pack_id": lookup.get("pack_id"),
        "missing": lookup.get("missing"),
        "primary_course": lookup.get("primary_course"),
        "rows": [
            {
                "symbol": row.get("symbol"),
                "stance": (row.get("stance") or {}).get("stance"),
                "suggested_conviction": (row.get("stance") or {}).get("suggested_conviction"),
                "course": (row.get("stance") or {}).get("course"),
                "evidence": (row.get("stance") or {}).get("evidence"),
                "conflicts": (row.get("stance") or {}).get("conflicts"),
                "flags": (row.get("stance") or {}).get("flags"),
                "sleeves": (row.get("stance") or {}).get("sleeves"),
                "invalidation": (row.get("stance") or {}).get("invalidation"),
                "next_check": (row.get("stance") or {}).get("next_check"),
                "raw": row.get("raw"),
                "progression": row.get("progression"),
            }
            for row in lookup.get("dossiers") or []
        ],
    }
    print(_dumps(payload))
    return payload


def main(argv: list[str] | None = None) -> dict[str, object]:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in COMMANDS:
        command = args[0]
        rest = args[1:]
    else:
        command = "compile"
        rest = args
    handlers = {
        "compile": _cmd_compile,
        "inspect": _cmd_inspect,
        "lookup": _cmd_lookup,
        "compare": _cmd_compare,
        "stance": _cmd_stance,
    }
    return handlers[command](rest)


if __name__ == "__main__":
    main()
