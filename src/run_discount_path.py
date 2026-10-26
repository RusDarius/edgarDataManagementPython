"""Discount path — own-history multiples, peer upside, balance-sheet strength.

No CLI args: edit the window and the market-cap floor in
``run_discount_path_local_main`` and run:

    python src/run_discount_path.py

CLI overrides those knobs:

    python src/run_discount_path.py --months 3
    python src/run_discount_path.py --months 0
    python src/run_discount_path.py --min-mcap 1000000000

months 3 keeps all-fields day files inside about three months of the newest file.
months 0 keeps every dated file.

DuckDB, queries.txt, and overview.log land under
logs/tradingview_analysis/discount_path/runs/discount_path_<stamp>/.
Open queries.txt. The task SQL is at the top. The field list follows it.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from financial_projection.load_all_fields import (
    discover_latest_all_fields_db,
    load_latest_all_fields_universe,
)
from generic_utils.discount_path import (
    build_discount_tables,
    load_discount_recipe,
    task_queries,
)
from generic_utils.forward_fundamentals import annotate_projection_forward
from generic_utils.ranking import drop_duplicate_id_suffixes, to_float
from generic_utils.run_export import write_run_export
from generic_utils.series import field_history, list_dated_files, series_span, table_columns

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "logs" / "tradingview_analysis" / "discount_path" / "runs"
DEFAULT_ALL_FIELDS_ROOT = PROJECT_ROOT / "logs" / "tradingview_analysis" / "trading_view_all_fields_data"

LOAD_FIELDS: tuple[str, ...] = (
    "symbol",
    "industry",
    "close",
    "market_cap_basic",
    "price_earnings_ttm",
    "enterprise_value_to_revenue_ttm",
    "enterprise_value_ebitda_ttm",
    "price_book_ratio",
    "price_book_fq",
    "enterprise_value_to_free_cash_flow_ttm",
    "operating_margin",
    "net_debt",
    "total_debt",
    "cash_n_equivalents_fq",
    "ebitda_ttm",
    "free_cash_flow_ttm",
    "total_revenue_ttm",
    "price_target_average",
    "RSI",
)

# All-fields names that can be walked across day files. Forward PE is not here.
HISTORY_FIELDS: dict[str, str] = {
    "close": "close",
    "price_earnings_ttm": "pe",
    "enterprise_value_to_revenue_ttm": "evrev",
    "enterprise_value_ebitda_ttm": "evebitda",
    "price_book_ratio": "pb",
    "price_book_fq": "pb_fq",
    "enterprise_value_to_free_cash_flow_ttm": "evfcf",
}


def run_discount_path_method(
    *,
    months: int = 3,
    min_market_cap_usd: float = 2_000_000_000,
    all_fields_db: str | Path | None = None,
    output_root: str | Path | None = None,
    with_history: bool = True,
    recipe_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build the discount path.

    ``months`` is the history window. 0 means every dated all-fields file.
    ``min_market_cap_usd`` is the load floor. It is not a discount cutoff.
    """
    recipe = load_discount_recipe(recipe_path)
    recipe["months"] = int(months)
    recipe["min_market_cap_usd"] = float(min_market_cap_usd)
    database = Path(all_fields_db) if all_fields_db else discover_latest_all_fields_db()
    if database is None:
        raise FileNotFoundError(
            "No tradingview_all_fields_*.duckdb found. Export all-fields first."
        )
    loaded, meta = load_latest_all_fields_universe(
        database_path=database,
        fields=LOAD_FIELDS,
        min_market_cap_usd=recipe["min_market_cap_usd"],
    )
    loaded = [dict(row) for row in drop_duplicate_id_suffixes(loaded)]
    print(f"Loaded {len(loaded)} names from {Path(database).name}", flush=True)
    try:
        loaded = annotate_projection_forward(loaded)
    except Exception as exc:  # noqa: BLE001 — a missing projection must not drop the tape
        meta = dict(meta)
        meta["projection_note"] = f"projection skipped: {exc}"
    span_rows: list[dict[str, Any]] = []
    hist_note = "history skipped"
    if with_history:
        print(f"Walking all-fields history, months={recipe['months']}", flush=True)
        span_rows, hist_note = _history_span(loaded, int(recipe["months"]))
        print(hist_note, flush=True)
    tables = build_discount_tables(loaded, span_rows, recipe=recipe)
    created = datetime.now(tz=timezone.utc)
    run_id = f"discount_path_{created.strftime('%Y%m%d_%H%M')}_utc_{uuid.uuid4().hex[:8]}"
    root = Path(output_root) if output_root else DEFAULT_OUTPUT_ROOT
    out_dir = root / run_id
    sources = {
        "all_fields_db": meta.get("database_path"),
        "all_fields_run_id": meta.get("source_run_id"),
        "all_fields_created_at_utc": meta.get("source_created_at_utc"),
        "months": recipe["months"],
        "min_market_cap_usd": recipe["min_market_cap_usd"],
        "loaded_rows": meta.get("row_count_raw"),
        "history": hist_note,
        "projection": meta.get("projection_note") or "projection attached when the dumps exist",
    }
    notes = [
        "discount_tape is every loaded name. reading is the where in queries.txt item 1.",
        "months 0 uses every dated all-fields file. months 3 is about 91 days back from the newest file.",
        "FIELD_pct negative means that multiple got cheaper on the window. FIELD_path is CHEAPER, RICHER, FLAT, or THIN_HIST.",
        "FIELD_up_pct is the price gap if that multiple went to the industry median. Do not add the gaps.",
        "pe_fwd and evebitda_fwd are the current projection. They are not on the history span.",
        "strength_read is net_cash, covered, levered, open, or thin. The floors are in discount_path.json.",
        "street_left is the street target versus close. It is a different number from FIELD_up_pct.",
        hist_note,
    ]
    exported = write_run_export(
        out_dir,
        tool="discount_path",
        tables=tables,
        sources=sources,
        schema_version="discount_path_v1",
        notes=notes,
        duckdb_name="discount_path.duckdb",
        log_name="overview.log",
        write_csv=True,
    )
    guide = Path(exported["query_guide"])
    guide.write_text(task_queries(str(exported["duckdb"])) + "\n" + guide.read_text(encoding="utf-8"), encoding="utf-8")
    (out_dir / "spec.json").write_text(
        json.dumps({"run_id": run_id, **recipe, **sources}, indent=2, default=str),
        encoding="utf-8",
    )
    return {
        "run_id": run_id,
        "out_dir": out_dir.as_posix(),
        "duckdb": exported["duckdb"],
        "queries": guide.as_posix(),
        "overview_log": exported["overview_log"],
        "tables": exported["tables"],
        "spec": {
            "months": recipe["months"],
            "min_market_cap_usd": recipe["min_market_cap_usd"],
        },
        "sources": sources,
    }


def _history_span(rows: Sequence[dict[str, Any]], months: int) -> tuple[list[dict[str, Any]], str]:
    symbols = [str(row.get("symbol") or "") for row in rows if row.get("symbol")]
    if not symbols:
        return [], "history skipped: no names"
    sources = list_dated_files(DEFAULT_ALL_FIELDS_ROOT, "**/tradingview_all_fields_*.duckdb")
    sources = _window(sources, months)
    if not sources:
        return [], "history skipped: no all-fields day files"
    available: set[str] = set()
    sample = (sources[0], sources[len(sources) // 2], sources[-1])
    for source in sample:
        available |= table_columns(source["path"], "all_fields_rows")
    columns = ["symbol"]
    rename: dict[str, str] = {}
    for source_name, alias in HISTORY_FIELDS.items():
        if source_name in available:
            columns.append(source_name)
            rename[source_name] = alias
    if len(columns) < 2:
        return [], "history skipped: none of the multiple columns are on the sampled files"
    try:
        history = field_history(
            sources,
            table="all_fields_rows",
            columns=tuple(columns),
            id_field="symbol",
            ids=symbols,
        )
    except Exception as exc:  # noqa: BLE001 — a missing column must not drop the tape
        return [], f"history skipped: {exc}"
    history_rows = history.get("rows") or []
    merged: dict[str, dict[str, Any]] = {}
    for source_name, alias in rename.items():
        observed = [row for row in history_rows if to_float(row.get(source_name)) is not None]
        if not observed:
            continue
        for row in series_span(observed, id_field="symbol", fields=(source_name,)):
            symbol = str(row.get("symbol") or "")
            rec = merged.setdefault(symbol, {"symbol": symbol, "n": 0})
            rec[f"{alias}_pct"] = row.get(f"{source_name}_pct")
            rec[f"{alias}_first"] = row.get(f"{source_name}_first")
            rec[f"{alias}_last"] = row.get(f"{source_name}_last")
            rec[f"{alias}_n"] = row.get("n")
            if alias == "close":
                rec["n"] = row.get("n") or 0
                rec["first_as_of"] = row.get("first_as_of")
                rec["last_as_of"] = row.get("last_as_of")
    for rec in merged.values():
        if not rec.get("n"):
            counts = [int(rec.get(f"{alias}_n") or 0) for alias in rename.values()]
            rec["n"] = max(counts) if counts else 0
    return list(merged.values()), (
        f"history files={len(sources)} ids={len(symbols)} spanned={len(merged)} months={months}"
    )


def _window(sources: Sequence[dict[str, Any]], months: int) -> list[dict[str, Any]]:
    dated = [row for row in sources if row.get("as_of_date") is not None]
    if months <= 0 or not dated:
        return list(dated or sources)
    end = dated[-1]["as_of_date"]
    start = end - timedelta(days=int(round(months * 30.5)))
    return [row for row in dated if row["as_of_date"] >= start]


def _print_result(result: dict[str, Any]) -> None:
    print(f"Run: {result['run_id']}")
    print(f"Folder: {result['out_dir']}")
    print(f"DuckDB: {result['duckdb']}")
    print(f"Queries: {result['queries']}")
    print(f"Tables: {result['tables']}")
    print(
        "Window: "
        f"months={result['spec']['months']} "
        f"mcap>={result['spec']['min_market_cap_usd']:.0f}"
    )


def run_discount_path_local_main() -> dict[str, Any]:
    """Edit the window here, then run this file with no arguments."""
    # ===== edit params here =====
    # 3 is about the last three months. 0 keeps every dated all-fields file.
    months = 3
    # Load floor. Names under this market cap are not in the folder.
    # It is not a discount cutoff. 2000000000 matches the US $2B peer set.
    min_market_cap_usd = 2_000_000_000
    all_fields_db = None
    output_root = None
    # ===== end params =====
    return run_discount_path_method(
        months=months,
        min_market_cap_usd=min_market_cap_usd,
        all_fields_db=all_fields_db,
        output_root=output_root,
    )


def main(argv: Sequence[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Discount path: history, peer upside, balance-sheet strength.")
    parser.add_argument("--months", type=int, default=None, help="History window. 0 = every dated file. Default is the local main.")
    parser.add_argument("--min-mcap", type=float, default=None, help="Load floor in USD. Not a discount cutoff.")
    parser.add_argument("--all-fields-db", default=None)
    parser.add_argument("--out-root", default=None)
    parser.add_argument("--no-history", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.months is None and args.min_mcap is None and argv is None:
        result = run_discount_path_local_main()
    else:
        recipe = load_discount_recipe()
        result = run_discount_path_method(
            months=recipe["months"] if args.months is None else args.months,
            min_market_cap_usd=recipe["min_market_cap_usd"] if args.min_mcap is None else args.min_mcap,
            all_fields_db=args.all_fields_db,
            output_root=args.out_root,
            with_history=not args.no_history,
        )
    _print_result(result)
    return result


if __name__ == "__main__":
    main()
