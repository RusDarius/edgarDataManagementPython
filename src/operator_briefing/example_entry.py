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

Chat copy-paste blocks: src/research_prompt/agent_copy_paste.txt
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

# --brief keeps the fields a course decision reads. Full dossiers stay one flag away.
BRIEF_RAW_FIELDS = (
    "close", "left", "street_left", "rr", "bo", "cont", "fwd", "rsi", "rng", "vs50", "peer",
    "val_vs_ind", "val_label", "dte", "mix", "mtp", "weeks_ras", "opm", "pe", "pe_fwd", "mcap", "ind",
)
BRIEF_COMPARE_FIELDS = ("symbol", "d_bo", "d_cont", "d_fwd", "bo_b", "mix_a", "mix_b", "rank_a", "rank_b")


def _slim(row: dict[str, object] | None, fields: tuple[str, ...]) -> dict[str, object]:
    row = row or {}
    return {key: row.get(key) for key in fields if row.get(key) is not None}


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
            "duckdb": output.get("duckdb"),
            "overview_log": output.get("overview_log"),
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
    parser.add_argument("--sources", action="store_true", help="Print only source paths, run ids, and the latest pack.")
    args = parser.parse_args(argv)
    payload = inspect_locked_sources(
        prediction_run_id=args.pred_run,
        all_fields_run_id=args.af_run,
    )
    if args.sources:
        print(_dumps({"sources": payload.get("sources"), "latest_briefing_pack": payload.get("latest_briefing_pack")}))
        return payload
    print(_dumps(payload))
    return payload


def _cmd_lookup(argv: list[str]) -> dict[str, object]:
    from operator_briefing.lookup import lookup_symbols

    parser = argparse.ArgumentParser(description="Dossier: raw + progression + stance for named symbols.")
    parser.add_argument("symbols", nargs="+", help="Tickers or EXCHANGE:TICKER")
    parser.add_argument("--pack", default=None, help="Path to briefing_pack.json")
    parser.add_argument("--brief", action="store_true", help="Course fields only (stance, support, conflicts, raw subset).")
    parser.add_argument("--line", action="store_true", help="--brief as one text line per name.")
    args = parser.parse_args(argv)
    payload = lookup_symbols(args.symbols, pack_path=args.pack)
    if args.brief or args.line:
        brief = _brief_dossiers(payload)
        print(_brief_lines(brief) if args.line else _dumps(brief))
        return payload
    print(_dumps(payload))
    return payload


def _bo_path(prog: object) -> dict[str, object] | None:
    """First / last weeks bo over the dossier's progression path (oldest to newest)."""
    path = prog.get("path") if isinstance(prog, dict) else None
    points = [p for p in (path or []) if isinstance(p, dict) and p.get("bo") is not None]
    if not points:
        return None
    first, last = float(points[0]["bo"]), float(points[-1]["bo"])
    return {"n": len(points), "bo_first": round(first, 3), "bo_last": round(last, 3), "d_bo": round(last - first, 3)}


def _brief_dossiers(lookup: dict[str, object]) -> dict[str, object]:
    rows = []
    for row in lookup.get("dossiers") or []:
        stance = row.get("stance") or {}
        prog = row.get("progression") or {}
        rows.append({
            "symbol": row.get("symbol"),
            "in_book": bool(row.get("book")),
            "stance": stance.get("stance"),
            "support": stance.get("suggested_conviction"),
            "conflicts": stance.get("conflicts") or [],
            "invalidation": stance.get("invalidation"),
            "next_check": stance.get("next_check"),
            "sleeves": stance.get("sleeves"),
            "raw": _slim(row.get("raw"), BRIEF_RAW_FIELDS),
            "bo_path": _bo_path(prog),
            "d_prior_run": prog.get("delta_vs_prior_run") if isinstance(prog, dict) else None,
        })
    return {"pack_id": lookup.get("pack_id"), "missing": lookup.get("missing"), "rows": rows}


def _brief_lines(brief: dict[str, object]) -> str:
    """One text line per --brief row (about 250 bytes vs 1 KB of JSON). Same fields, nothing added."""
    lines = [f"pack {brief.get('pack_id')}"]
    for row in brief.get("rows") or []:
        raw = row.get("raw") or {}
        cells = [f"{k}={round(v, 2) if isinstance(v, float) else v}" for k, v in raw.items() if k != "mcap"]
        bo = row.get("bo_path") or {}
        tail = [f"d_bo={bo['d_bo']}"] if bo.get("d_bo") is not None else []
        tail += [f"conflicts={'; '.join(row['conflicts'])}"] if row.get("conflicts") else []
        lines.append(f"{row.get('symbol')} {'BOOK ' if row.get('in_book') else ''}{row.get('stance')} "
                     f"support={row.get('support')} " + " ".join(cells + tail))
    if brief.get("missing"):
        lines.append("missing (not a pack name; try tv_scan_cli.py named --csv PACK/movers/us2b_tape.csv): "
                     + ", ".join(map(str, brief["missing"])))
    return "\n".join(lines)


def _cmd_compare(argv: list[str]) -> dict[str, object]:
    from operator_briefing.compare import compare_prediction_runs

    parser = argparse.ArgumentParser(description="Compare two move-prediction runs (profile + mix deltas).")
    parser.add_argument("--run-a", default=None, help="Prior run_id (default: previous in run_metadata)")
    parser.add_argument("--run-b", default=None, help="Current run_id (default: latest)")
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument(
        "--session",
        action="store_true",
        help="Prior run from a different calendar day, not the last intra-day scan.",
    )
    parser.add_argument(
        "--exchanges",
        default=None,
        help="Comma exchanges to keep (DAILY: NASDAQ,NYSE,AMEX).",
    )
    parser.add_argument("--ids", default=None, help="Comma symbols or tickers to keep.")
    parser.add_argument("--brief", action="store_true", help="Delta fields only, lists cut to --brief-n rows.")
    parser.add_argument("--brief-n", type=int, default=8)
    args = parser.parse_args(argv)
    exchanges = [item.strip() for item in (args.exchanges or "").split(",") if item.strip()] or None
    ids = [item.strip() for item in (args.ids or "").split(",") if item.strip()] or None
    payload = compare_prediction_runs(
        run_a=args.run_a,
        run_b=args.run_b,
        top_n=args.top,
        session=args.session,
        exchanges=exchanges,
        ids=ids,
    )
    if args.brief:
        brief: dict[str, object] = {k: payload.get(k) for k in ("run_a", "run_b", "session", "n")}
        for key in ("gainers", "losers", "new_in_top", "dropped_from_top", "mix_flips", "rising_actionable"):
            value = payload.get(key)
            if isinstance(value, list):
                brief[key] = [_slim(r, BRIEF_COMPARE_FIELDS) if isinstance(r, dict) else r for r in value[: args.brief_n]]
        print(_dumps(brief))
        return payload
    print(_dumps(payload))
    return payload


def _cmd_stance(argv: list[str]) -> dict[str, object]:
    from operator_briefing.lookup import lookup_symbols

    parser = argparse.ArgumentParser(description="Suggested course + support for named symbols.")
    parser.add_argument("symbols", nargs="+")
    parser.add_argument("--pack", default=None)
    parser.add_argument("--brief", action="store_true", help="Course fields only (stance, support, conflicts, raw subset).")
    parser.add_argument("--line", action="store_true", help="--brief as one text line per name.")
    args = parser.parse_args(argv)
    lookup = lookup_symbols(args.symbols, pack_path=args.pack)
    if args.brief or args.line:
        brief = _brief_dossiers(lookup)
        brief["primary_course"] = {k: (lookup.get("primary_course") or {}).get(k) for k in ("bias",)}
        print(_brief_lines(brief) if args.line else _dumps(brief))
        return brief
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
