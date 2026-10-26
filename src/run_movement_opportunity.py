"""Movement opportunity — sibling of run_financial_projection.py.

Scores TRADING opportunity (1-10 upside and 1-10 downside) from day-to-day
movement across the project datasets. Upside is the focus.

No CLI args: edit the floor and history window in
``run_movement_opportunity_local_main`` and run:

    python src/run_movement_opportunity.py

Same knobs as CLI, and the financial-projection entrypoint can delegate:

    python src/run_movement_opportunity.py --min-mcap 500000000
    python src/run_financial_projection.py --mode movement

DuckDB, queries.txt, overview.log, and highlights.log land under
logs/tradingview_analysis/movement_opportunity/runs/movement_opportunity_<stamp>/.
Open queries.txt. The task SQL is at the top. The field list follows it.

How to read the 1-10 and the follow-ups:
    documentation/movement_opportunity_usage.md
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
from generic_utils.dataset_lock import latest_family_metrics
from generic_utils.forward_fundamentals import annotate_projection_forward
from generic_utils.movement_opportunity import (
    HISTORY_FIELDS,
    LOAD_FIELDS,
    build_movement_tables,
    highlights_text,
    load_movement_recipe,
    prepare_tape,
    task_queries,
)
from generic_utils.ranking import drop_duplicate_id_suffixes, to_float
from generic_utils.recipes import render_recipe
from generic_utils.run_export import write_run_export
from generic_utils.scan_sources import rows_from_csv, rows_from_duckdb
from generic_utils.series import field_history, list_dated_files, series_span, table_columns
from operator_briefing.extract import load_csv_by_symbol

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "logs" / "tradingview_analysis" / "movement_opportunity" / "runs"
)
DEFAULT_ALL_FIELDS_ROOT = (
    PROJECT_ROOT / "logs" / "tradingview_analysis" / "trading_view_all_fields_data"
)


def _by_symbol(rows: Sequence[dict[str, Any]], key: str = "symbol") -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get(key) or "").strip()
        if symbol:
            out[symbol] = row
    return out


def _lock_overlays() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Prediction weeks, mix, edge leftover, MTP. Missing overlay is a note, not a crash."""
    notes: dict[str, Any] = {}
    pred: dict[str, dict[str, Any]] = {}
    conviction: dict[str, dict[str, Any]] = {}
    edge: dict[str, dict[str, Any]] = {}
    mtp: dict[str, dict[str, Any]] = {}
    try:
        from operator_briefing.discovery import lock_sources

        sources = lock_sources()
    except Exception as exc:  # noqa: BLE001
        notes["lock"] = f"lock_sources skipped: {exc}"
        return pred, conviction, edge, mtp, notes

    notes["prediction_db"] = str(sources.prediction_db) if sources.prediction_db else ""
    notes["prediction_run_id"] = sources.prediction_run_id
    notes["all_fields_run_id"] = sources.all_fields_run_id
    notes["all_fields_day_label"] = sources.all_fields_day_label
    notes["edge_opportunity_csv"] = (
        str(sources.edge_opportunity_csv) if sources.edge_opportunity_csv else ""
    )
    notes["mtp_policy_csv"] = str(sources.mtp_policy_csv) if sources.mtp_policy_csv else ""
    try:
        sql, params = render_recipe(
            "pred.profile_weeks_pivot", run_id=sources.prediction_run_id
        )
        pred = _by_symbol(rows_from_duckdb(sources.prediction_db, sql, params))
        notes["pred_n"] = len(pred)
    except Exception as exc:  # noqa: BLE001
        notes["pred"] = f"prediction weeks skipped: {exc}"
    try:
        sql, params = render_recipe("pred.conviction", run_id=sources.prediction_run_id)
        conviction = _by_symbol(rows_from_duckdb(sources.prediction_db, sql, params))
        notes["conviction_n"] = len(conviction)
    except Exception as exc:  # noqa: BLE001
        notes["conviction"] = f"conviction skipped: {exc}"
    try:
        edge = load_csv_by_symbol(sources.edge_opportunity_csv)
        notes["edge_n"] = len(edge)
    except Exception as exc:  # noqa: BLE001
        notes["edge"] = f"edge skipped: {exc}"
    try:
        mtp = load_csv_by_symbol(sources.mtp_policy_csv)
        notes["mtp_n"] = len(mtp)
    except Exception as exc:  # noqa: BLE001
        notes["mtp"] = f"mtp skipped: {exc}"
    return pred, conviction, edge, mtp, notes


def _history_span(
    rows: Sequence[dict[str, Any]],
    history_days: int,
) -> tuple[list[dict[str, Any]], str]:
    symbols = [str(row.get("symbol") or "") for row in rows if row.get("symbol")]
    if not symbols:
        return [], "history skipped: no names"
    sources = list_dated_files(DEFAULT_ALL_FIELDS_ROOT, "**/tradingview_all_fields_*.duckdb")
    sources = _window_days(sources, history_days)
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
    if "close" not in rename:
        return [], "history skipped: close is not on the sampled files"
    try:
        history = field_history(
            sources,
            table="all_fields_rows",
            columns=tuple(columns),
            id_field="symbol",
            ids=symbols,
        )
    except Exception as exc:  # noqa: BLE001
        return [], f"history skipped: {exc}"
    history_rows = history.get("rows") or []
    merged: dict[str, dict[str, Any]] = {}
    for source_name, alias in rename.items():
        observed = [row for row in history_rows if to_float(row.get(source_name)) is not None]
        if not observed:
            continue
        for row in series_span(
            observed,
            id_field="symbol",
            fields=(source_name,),
            include_steps=source_name == "close",
        ):
            symbol = str(row.get("symbol") or "")
            rec = merged.setdefault(symbol, {"symbol": symbol, "n": 0})
            rec[f"{alias}_pct"] = row.get(f"{source_name}_pct")
            rec[f"{alias}_first"] = row.get(f"{source_name}_first")
            rec[f"{alias}_last"] = row.get(f"{source_name}_last")
            rec[f"{alias}_n"] = row.get("n")
            rec[f"{alias}_min"] = row.get(f"{source_name}_min")
            rec[f"{alias}_max"] = row.get(f"{source_name}_max")
            if alias == "close":
                rec["n"] = row.get("n") or 0
                rec["first_as_of"] = row.get("first_as_of")
                rec["last_as_of"] = row.get("last_as_of")
                rec["close_n"] = row.get("n")
                for key, value in row.items():
                    if key.startswith("close_") or key in {
                        "n",
                        "first_as_of",
                        "last_as_of",
                        "symbol",
                    }:
                        rec[key] = value
    for rec in merged.values():
        if not rec.get("n"):
            counts = [int(rec.get(f"{alias}_n") or 0) for alias in rename.values()]
            rec["n"] = max(counts) if counts else 0
    return list(merged.values()), (
        f"history files={len(sources)} ids={len(symbols)} spanned={len(merged)} "
        f"days={history_days}"
    )


def _window_days(sources: Sequence[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    dated = [row for row in sources if row.get("as_of_date") is not None]
    if days <= 0 or not dated:
        return list(dated or sources)
    end = dated[-1]["as_of_date"]
    start = end - timedelta(days=int(days))
    return [row for row in dated if row["as_of_date"] >= start]


def _scan_day(day_label: str | None) -> Any:
    if not day_label:
        return None
    parts = str(day_label).replace("-", "_").split("_")
    if len(parts) != 3:
        return None
    try:
        from datetime import date

        return date(int(parts[2]), int(parts[1]), int(parts[0]))
    except ValueError:
        return None


def _load_family_metrics() -> tuple[list[dict[str, Any]], str]:
    path = latest_family_metrics()
    if path is None:
        return [], "family_metrics missing"
    try:
        return rows_from_csv(path), path.as_posix()
    except Exception as exc:  # noqa: BLE001
        return [], f"family_metrics skipped: {exc}"


def run_movement_opportunity_method(
    *,
    min_market_cap_usd: float | None = None,
    history_days: int | None = None,
    all_fields_db: str | Path | None = None,
    output_root: str | Path | None = None,
    with_history: bool = True,
    recipe_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build the movement-opportunity tape and 1-10 shortlists."""
    recipe = load_movement_recipe(recipe_path)
    if min_market_cap_usd is not None:
        recipe["min_market_cap_usd"] = float(min_market_cap_usd)
    if history_days is not None:
        recipe["history_days"] = int(history_days)
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
        projection_note = "projection attached when the dumps exist"
    except Exception as exc:  # noqa: BLE001
        projection_note = f"projection skipped: {exc}"

    pred, conviction, edge, mtp, overlay_notes = _lock_overlays()
    overlay_summary = (
        f"pred={overlay_notes.get('pred_n', 0)} "
        f"mix={overlay_notes.get('conviction_n', 0)} "
        f"edge={overlay_notes.get('edge_n', 0)} "
        f"mtp={overlay_notes.get('mtp_n', 0)}"
    )
    print(f"Overlays {overlay_summary}", flush=True)

    span_rows: list[dict[str, Any]] = []
    hist_note = "history skipped"
    if with_history:
        print(f"Walking all-fields history, days={recipe['history_days']}", flush=True)
        span_rows, hist_note = _history_span(loaded, int(recipe["history_days"]))
        print(hist_note, flush=True)

    family_rows, family_note = _load_family_metrics()
    scan = _scan_day(str(meta.get("source_day_label") or overlay_notes.get("all_fields_day_label") or database.parent.name))
    prepared = prepare_tape(
        loaded,
        recipe,
        scan_day=scan,
        pred=pred,
        conviction=conviction,
        edge=edge,
        mtp=mtp,
        span_rows=span_rows,
    )
    tables = build_movement_tables(prepared, recipe, family_metrics=family_rows)
    created = datetime.now(tz=timezone.utc)
    run_id = f"movement_opportunity_{created.strftime('%Y%m%d_%H%M')}_utc_{uuid.uuid4().hex[:8]}"
    root = Path(output_root) if output_root else DEFAULT_OUTPUT_ROOT
    out_dir = root / run_id
    sources = {
        "all_fields_db": meta.get("database_path"),
        "all_fields_run_id": meta.get("source_run_id") or overlay_notes.get("all_fields_run_id"),
        "all_fields_created_at_utc": meta.get("source_created_at_utc"),
        "prediction_run_id": overlay_notes.get("prediction_run_id"),
        "prediction_db": overlay_notes.get("prediction_db"),
        "edge_opportunity_csv": overlay_notes.get("edge_opportunity_csv"),
        "mtp_policy_csv": overlay_notes.get("mtp_policy_csv"),
        "min_market_cap_usd": recipe["min_market_cap_usd"],
        "history_days": recipe["history_days"],
        "loaded_rows": meta.get("row_count_raw"),
        "history": hist_note,
        "overlays": overlay_summary,
        "projection": projection_note,
        "family_metrics": family_note,
    }
    notes = [
        "upside_10 and downside_10 are 1-10 heuristics from named components. They are not leftover and not a 0-100.",
        "Every up_* and dn_* component stays a column. thin_up / thin_dn means coverage_frac is under 0.4; those ratings cannot print above 6.",
        "path_class is OPENING, DYING, CHEAPER, FLAT, or THIN_HIST. OPENING/DYING/CHEAPER match analysis-style. First-iteration CHEAPER uses trailing PE span (pe_pct).",
        "vol_class is coiled / active_swing / high_velocity / extreme / thin from close_step_abs_mean_pct, else ATRP.",
        "exp_move_Nd is ATRP * sqrt(N). Size of expected move. ATRP has negative IC as a rank, so it is not upside quality.",
        "MTP ENTER_SMALL is climate. Projection fp_* does not gate a row. Quote leftover as \"left\".",
        "down_class is BOUNCE / CONTINUE_DOWN / MIXED on multi-day laggards only (d5/m1/w, not a one-day dip). Bounce table is that sleeve, sorted by upside_10.",
        "us_listings_only is the operator tape (NASDAQ/NYSE/AMEX/ARCA/BATS/CBOE or country United States). Dual listings stay off the ranking.",
        hist_note,
        overlay_summary,
        family_note,
    ]
    exported = write_run_export(
        out_dir,
        tool="movement_opportunity",
        tables=tables,
        sources=sources,
        schema_version="movement_opportunity_v1",
        notes=notes,
        duckdb_name="movement_opportunity.duckdb",
        log_name="overview.log",
        write_csv=True,
    )
    guide = Path(exported["query_guide"])
    guide.write_text(
        task_queries(str(exported["duckdb"])) + "\n" + guide.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    highlights = highlights_text(spec=recipe, tables=tables, sources=sources)
    highlights_path = out_dir / "highlights.log"
    highlights_path.write_text(highlights, encoding="utf-8")
    (out_dir / "spec.json").write_text(
        json.dumps({"run_id": run_id, **recipe, **sources}, indent=2, default=str),
        encoding="utf-8",
    )
    return {
        "run_id": run_id,
        "out_dir": out_dir.as_posix(),
        "run_dir": out_dir.as_posix(),
        "duckdb": exported["duckdb"],
        "database_path": exported["duckdb"],
        "queries": guide.as_posix(),
        "overview_log": exported["overview_log"],
        "highlights_log": highlights_path.as_posix(),
        "tables": exported["tables"],
        "spec": {
            "min_market_cap_usd": recipe["min_market_cap_usd"],
            "history_days": recipe["history_days"],
            "primary_horizon_days": recipe["primary_horizon_days"],
        },
        "sources": sources,
    }


def _print_result(result: dict[str, Any]) -> None:
    print(f"Run: {result['run_id']}")
    print(f"Folder: {result['out_dir']}")
    print(f"DuckDB: {result['duckdb']}")
    print(f"Queries: {result['queries']}")
    print(f"Highlights: {result['highlights_log']}")
    print(f"Tables: {result['tables']}")
    print(
        "Floor: "
        f"mcap>={result['spec']['min_market_cap_usd']:.0f} "
        f"history_days={result['spec']['history_days']}"
    )


def run_movement_opportunity_local_main() -> dict[str, Any]:
    """Edit the floor here, then run this file with no arguments."""
    # ===== edit params here =====
    # Load floor. Names under this market cap are not in the folder.
    min_market_cap_usd = 500_000_000
    # All-fields day files inside this many calendar days of the newest file.
    history_days = 30
    all_fields_db = None
    output_root = None
    # ===== end params =====
    result = run_movement_opportunity_method(
        min_market_cap_usd=min_market_cap_usd,
        history_days=history_days,
        all_fields_db=all_fields_db,
        output_root=output_root,
    )
    _print_result(result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trading movement opportunity: day-to-day variation and 1-10 upside/downside."
    )
    parser.add_argument("--min-mcap", type=float, default=None, help="Load floor in USD. Not a leftover cutoff.")
    parser.add_argument("--history-days", type=int, default=None, help="All-fields window in calendar days. 0 = every dated file.")
    parser.add_argument("--all-fields-db", default=None)
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--out-root", default=None)
    parser.add_argument("--no-history", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> dict[str, Any]:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    recipe = load_movement_recipe()
    result = run_movement_opportunity_method(
        min_market_cap_usd=recipe["min_market_cap_usd"] if args.min_mcap is None else args.min_mcap,
        history_days=recipe["history_days"] if args.history_days is None else args.history_days,
        all_fields_db=args.all_fields_db,
        output_root=args.output_root or args.out_root,
        with_history=not args.no_history,
    )
    _print_result(result)
    return result


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main()
    else:
        run_movement_opportunity_local_main()
