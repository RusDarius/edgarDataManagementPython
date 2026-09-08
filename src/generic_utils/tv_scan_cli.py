"""Generic TradingView dataset interrogation CLI.

Replaces ad-hoc agent SQL / `_tmp_*.py`. No eligibility cutoffs.
Subcommands: recipes, inventory, summary, group, histogram, counts,
overlap, fetch, focus, pack-focus. Rank-only (no --where): rank_cli.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, default=str)


def _print_payload(payload: dict[str, Any], *, include_rows: bool) -> None:
    printed = dict(payload)
    if not include_rows and "rows" in printed:
        n = len(printed.get("rows") or [])
        printed["rows"] = []
        spec = dict(printed.get("spec") or {})
        spec.setdefault("n", n)
        printed["spec"] = spec
    print(_dumps(printed))


def _qident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _load_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    from generic_utils.scan_sources import rows_from_csv, rows_from_duckdb

    if args.csv and getattr(args, "db", None):
        raise SystemExit("Pass either --csv or --db, not both.")
    if args.csv:
        return rows_from_csv(args.csv)
    if getattr(args, "db", None):
        sql = getattr(args, "sql", None)
        if not sql:
            raise SystemExit("--db requires --sql (or use the fetch command).")
        return rows_from_duckdb(args.db, sql)
    raise SystemExit("Pass --csv or --db.")


def _cmd_recipes(_args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.recipes import list_recipes

    payload = {"recipes": list_recipes()}
    print(_dumps(payload))
    return payload


def _cmd_inventory(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.scan_sources import rows_from_duckdb

    tables = rows_from_duckdb(args.db, "SHOW TABLES")
    names = []
    for row in tables:
        name = row.get("name") or row.get("table_name")
        if name:
            names.append(str(name))
    columns: dict[str, Any] = {}
    for table in names:
        quoted = _qident(table)
        info = rows_from_duckdb(args.db, f"PRAGMA table_info({quoted})")
        col_names = [str(r.get("name")) for r in info if r.get("name")]
        if len(col_names) > 40:
            columns[table] = {
                "count": len(col_names),
                "preview": col_names[:24],
                "note": "Wide table; preview only. Never SELECT *.",
            }
        else:
            columns[table] = {"count": len(col_names), "names": col_names}
    payload = {"db": str(args.db), "tables": names, "columns": columns}
    print(_dumps(payload))
    return payload


def _cmd_summary(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.aggregations import numeric_summary

    rows = _load_rows(args)
    fields = [item.strip() for item in args.fields.split(",") if item.strip()]
    payload = {
        "spec": {"fields": fields, "source_rows": len(rows)},
        "summary": numeric_summary(rows, fields),
    }
    print(_dumps(payload))
    return payload


def _cmd_group(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.aggregations import group_stats

    rows = _load_rows(args)
    metrics = [item.strip() for item in args.metrics.split(",") if item.strip()]
    grouped = group_stats(
        rows,
        group_field=args.by,
        metrics=metrics,
        min_n=args.min_n,
        pct_positive_field=args.pct_positive,
        digits=args.digits,
    )
    payload = {
        "spec": {
            "group_field": args.by,
            "metrics": metrics,
            "min_n": args.min_n,
            "pct_positive_field": args.pct_positive,
            "source_rows": len(rows),
            "groups": len(grouped),
        },
        "rows": grouped,
    }
    print(_dumps(payload))
    return payload


def _cmd_histogram(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.aggregations import histogram

    rows = _load_rows(args)
    bins = [float(item.strip()) for item in args.bins.split(",") if item.strip()]
    payload = {
        "spec": {"field": args.field, "bins": bins, "source_rows": len(rows)},
        "buckets": histogram(rows, field=args.field, bins=bins),
    }
    print(_dumps(payload))
    return payload


def _cmd_counts(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.aggregations import value_counts

    rows = _load_rows(args)
    payload = {
        "spec": {"field": args.field, "top": args.top, "source_rows": len(rows)},
        "rows": value_counts(rows, args.field, top=args.top),
    }
    print(_dumps(payload))
    return payload


def _cmd_overlap(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.aggregations import ids_of, overlap
    from generic_utils.scan_sources import rows_from_csv

    rows_a = rows_from_csv(args.csv_a)
    rows_b = rows_from_csv(args.csv_b)
    payload = {
        "spec": {
            "csv_a": args.csv_a,
            "csv_b": args.csv_b,
            "id_field": args.id_field,
        },
        "overlap": overlap(
            ids_of(rows_a, args.id_field),
            ids_of(rows_b, args.id_field),
            list_limit=args.list_limit,
        ),
    }
    print(_dumps(payload))
    return payload


def _cmd_fetch(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.recipes import render_recipe
    from generic_utils.scan_sources import rows_from_duckdb

    values: dict[str, Any] = {}
    if args.run_id:
        values["run_id"] = args.run_id
    if args.limit is not None:
        values["limit"] = args.limit
    sql, params = render_recipe(args.recipe, **values)
    rows = rows_from_duckdb(args.db, sql, params)
    payload = {
        "spec": {"recipe": args.recipe, "params": values, "n": len(rows)},
        "rows": rows[: args.max_rows],
    }
    if args.out:
        from generic_utils.scan_sources import rows_to_csv

        path = rows_to_csv(args.out, rows)
        payload["out"] = path.as_posix()
    _print_payload(payload, include_rows=not args.out)
    return payload


def _cmd_focus(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.focus import build_focus, write_focus

    rows = _load_rows(args)
    exempt = set(args.exempt.split(",")) if args.exempt else None
    payload = build_focus(
        rows,
        where=args.where,
        field=args.field,
        n=args.top,
        reverse=not args.asc,
        tie_breaker=args.tie_breaker,
        group_field=args.group_field,
        cap=args.cap,
        exempt=exempt,
        id_field=args.id_field,
    )
    if args.out:
        path = write_focus(payload, args.out)
        payload["out"] = path.as_posix()
    _print_payload(payload, include_rows=not args.out)
    return payload


def _cmd_pack_focus(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.focus import pack_path_rows
    from generic_utils.scan_sources import rows_to_csv

    pack = json.loads(Path(args.pack).read_text(encoding="utf-8"))
    rows = pack_path_rows(pack, args.sleeve)
    out_path = None
    if args.out:
        out_path = rows_to_csv(args.out, rows)
    payload = {
        "spec": {
            "pack": args.pack,
            "sleeve": args.sleeve,
            "n": len(rows),
            "pack_run_id": pack.get("run_id"),
        },
        "rows": rows if args.include_rows else [],
        "out": out_path.as_posix() if out_path else None,
    }
    print(_dumps(payload))
    return payload


def _add_source_flags(parser: argparse.ArgumentParser, *, sql_optional: bool = True) -> None:
    parser.add_argument("--csv", default=None)
    parser.add_argument("--db", default=None)
    if sql_optional:
        parser.add_argument("--sql", default=None)


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(
        description="Generic TradingView dataset interrogation. No embedded eligibility cutoffs."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    recipes_p = sub.add_parser("recipes", help="List named SQL recipes.")
    recipes_p.set_defaults(func=_cmd_recipes)

    inv_p = sub.add_parser("inventory", help="SHOW TABLES + PRAGMA table_info.")
    inv_p.add_argument("--db", required=True)
    inv_p.set_defaults(func=_cmd_inventory)

    sum_p = sub.add_parser("summary", help="Numeric summary of named fields.")
    _add_source_flags(sum_p)
    sum_p.add_argument("--fields", required=True)
    sum_p.set_defaults(func=_cmd_summary)

    grp_p = sub.add_parser("group", help="Group means by a field.")
    _add_source_flags(grp_p)
    grp_p.add_argument("--by", required=True)
    grp_p.add_argument("--metrics", required=True)
    grp_p.add_argument("--min-n", type=int, default=1)
    grp_p.add_argument("--pct-positive", default=None)
    grp_p.add_argument("--digits", type=int, default=2)
    grp_p.set_defaults(func=_cmd_group)

    hist_p = sub.add_parser("histogram", help="Histogram with caller bin edges.")
    _add_source_flags(hist_p)
    hist_p.add_argument("--field", required=True)
    hist_p.add_argument("--bins", required=True)
    hist_p.set_defaults(func=_cmd_histogram)

    cnt_p = sub.add_parser("counts", help="Value counts for a field.")
    _add_source_flags(cnt_p)
    cnt_p.add_argument("--field", required=True)
    cnt_p.add_argument("--top", type=int, default=40)
    cnt_p.set_defaults(func=_cmd_counts)

    ov_p = sub.add_parser("overlap", help="Set overlap of two CSVs.")
    ov_p.add_argument("--csv-a", required=True)
    ov_p.add_argument("--csv-b", required=True)
    ov_p.add_argument("--id-field", default="symbol")
    ov_p.add_argument("--list-limit", type=int, default=40)
    ov_p.set_defaults(func=_cmd_overlap)

    fetch_p = sub.add_parser("fetch", help="Run a named recipe against a DuckDB.")
    fetch_p.add_argument("--recipe", required=True)
    fetch_p.add_argument("--db", required=True)
    fetch_p.add_argument("--run-id", default=None)
    fetch_p.add_argument("--limit", type=int, default=None)
    fetch_p.add_argument("--max-rows", type=int, default=50)
    fetch_p.add_argument("--out", default=None)
    fetch_p.set_defaults(func=_cmd_fetch)

    focus_p = sub.add_parser("focus", help="Filter + rank + optional group cap.")
    _add_source_flags(focus_p)
    focus_p.add_argument("--where", default=None)
    focus_p.add_argument("--field", required=True)
    focus_p.add_argument("--top", type=int, default=100)
    focus_p.add_argument("--asc", action="store_true")
    focus_p.add_argument("--tie-breaker", default=None)
    focus_p.add_argument("--group-field", default=None)
    focus_p.add_argument("--cap", type=int, default=None)
    focus_p.add_argument("--exempt", default=None)
    focus_p.add_argument("--id-field", default="symbol")
    focus_p.add_argument("--out", default=None)
    focus_p.set_defaults(func=_cmd_focus)

    pack_p = sub.add_parser("pack-focus", help="Export a briefing-pack sleeve to CSV.")
    pack_p.add_argument("--pack", required=True)
    pack_p.add_argument("--sleeve", default="sleeves.radar_curated_25")
    pack_p.add_argument("--out", required=True)
    pack_p.add_argument("--include-rows", action="store_true")
    pack_p.set_defaults(func=_cmd_pack_focus)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    main()
