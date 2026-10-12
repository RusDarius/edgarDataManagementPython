"""Growth radar — revenue growers and earnings delivery, from all-fields plus the projection.

No CLI args: edit the two thresholds in ``run_growth_radar_local_main`` and run:

    python src/run_growth_radar.py

CLI overrides those same knobs:

    python src/run_growth_radar.py --min-mcap 1000000000 --min-growth 15

DuckDB, queries.txt, overview.log, and highlights.log land under
logs/tradingview_analysis/growth_radar/runs/growth_radar_<stamp>/.
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from financial_projection.load_all_fields import (
    discover_latest_all_fields_db,
    load_latest_all_fields_universe,
)
from generic_utils.forward_fundamentals import annotate_projection_forward
from generic_utils.growth_radar import (
    GROWTH_LOAD_FIELDS,
    GrowthRadarSpec,
    apply_hist_span,
    build_growth_tables,
    highlights_text,
    prepare_universe,
)
from generic_utils.run_export import write_run_export
from generic_utils.series import field_history, list_dated_files, series_span

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "logs" / "tradingview_analysis" / "growth_radar" / "runs"
DEFAULT_ALL_FIELDS_ROOT = PROJECT_ROOT / "logs" / "tradingview_analysis" / "trading_view_all_fields_data"


def run_growth_radar_method(
    *,
    min_market_cap_usd: float = 1_000_000_000,
    min_growth_pct: float = 15.0,
    top: int = 40,
    industry_cap: int = 4,
    overlooked_rng_max: float = 60.0,
    hist_newest: int = 12,
    all_fields_db: str | Path | None = None,
    output_root: str | Path | None = None,
    with_history: bool = True,
) -> dict[str, Any]:
    """Build the growth radar.

    Threshold 1 is ``min_market_cap_usd``. Names under it are not loaded.
    Threshold 2 is ``min_growth_pct``. A name clears it on revenue, on
    earnings, or on both. The two lanes are ranked separately.
    """
    spec = GrowthRadarSpec(
        min_market_cap_usd=float(min_market_cap_usd),
        min_growth_pct=float(min_growth_pct),
        top=int(top),
        industry_cap=int(industry_cap),
        overlooked_rng_max=float(overlooked_rng_max),
        hist_newest=int(hist_newest),
    )
    database = Path(all_fields_db) if all_fields_db else discover_latest_all_fields_db()
    if database is None:
        raise FileNotFoundError(
            "No tradingview_all_fields_*.duckdb found. Export all-fields first."
        )
    loaded, meta = load_latest_all_fields_universe(
        database_path=database,
        fields=GROWTH_LOAD_FIELDS,
        min_market_cap_usd=spec.min_market_cap_usd,
    )
    projected = annotate_projection_forward(loaded)
    prepared = prepare_universe(projected)
    tables = build_growth_tables(prepared, spec)
    hist_note = "history skipped"
    if with_history:
        hist_note = _attach_history(tables, spec)
    created = datetime.now(tz=timezone.utc)
    run_id = f"growth_radar_{created.strftime('%Y%m%d_%H%M')}_utc_{uuid.uuid4().hex[:8]}"
    root = Path(output_root) if output_root else DEFAULT_OUTPUT_ROOT
    out_dir = root / run_id
    sources = {
        "all_fields_db": meta.get("database_path"),
        "all_fields_run_id": meta.get("source_run_id"),
        "all_fields_full_run_id": meta.get("source_full_run_id"),
        "all_fields_created_at_utc": meta.get("source_created_at_utc"),
        "min_market_cap_usd": spec.min_market_cap_usd,
        "min_growth_pct": spec.min_growth_pct,
        "loaded_rows": meta.get("row_count_raw"),
        "history": hist_note,
    }
    notes = [
        "Threshold 1 is min market cap. Names under it are not in this folder.",
        "Threshold 2 is min growth percent. Revenue lane uses rev_growth. Earnings lane uses earn_growth.",
        "rev_growth_source and earn_growth_source name which input was quoted. Do not average them.",
        "A projection step at the 150% growth clip is skipped. A print beyond 300% is skipped as a base effect.",
        "pe_fwd is the projection next-year PE. pe_fwd_peer uses the median percent only. DISCOUNT is <= -5.",
        "overlooked is a label: forward PE or forward EV/EBITDA DISCOUNT, and rng under the spec max.",
        "pe_hist_pct is the shortlist trailing-PE span. CHEAPER is pe_hist_pct <= -5. Not a 0-100.",
        "Industry cap overflow is revenue_overflow and earnings_overflow. Those names were not dropped silently.",
        hist_note,
    ]
    exported = write_run_export(
        out_dir,
        tool="growth_radar",
        tables=tables,
        sources=sources,
        schema_version="growth_radar_v1",
        notes=notes,
        duckdb_name="growth_radar.duckdb",
        log_name="overview.log",
        write_csv=True,
    )
    highlights = highlights_text(spec=spec, tables=tables, sources=sources)
    highlights_path = out_dir / "highlights.log"
    highlights_path.write_text(highlights, encoding="utf-8")
    (out_dir / "spec.json").write_text(
        json.dumps({"run_id": run_id, **spec.as_dict(), **sources}, indent=2, default=str),
        encoding="utf-8",
    )
    return {
        "run_id": run_id,
        "out_dir": out_dir.as_posix(),
        "duckdb": exported["duckdb"],
        "queries": exported["query_guide"],
        "overview_log": exported["overview_log"],
        "highlights_log": highlights_path.as_posix(),
        "tables": exported["tables"],
        "spec": spec.as_dict(),
        "sources": sources,
    }


def _attach_history(tables: dict[str, list[dict[str, Any]]], spec: GrowthRadarSpec) -> str:
    symbols: list[str] = []
    seen: set[str] = set()
    for name in ("revenue_growth", "earnings_delivery"):
        for row in tables.get(name) or []:
            symbol = str(row.get("symbol") or "")
            if symbol and symbol not in seen:
                seen.add(symbol)
                symbols.append(symbol)
    if not symbols:
        return "history skipped: no lane names"
    sources = list_dated_files(
        DEFAULT_ALL_FIELDS_ROOT,
        "**/tradingview_all_fields_*.duckdb",
        newest=spec.hist_newest,
    )
    if not sources:
        return "history skipped: no all-fields day files"
    try:
        history = field_history(
            sources,
            table="all_fields_rows",
            columns=("symbol", "price_earnings_ttm"),
            id_field="symbol",
            ids=symbols,
        )
    except Exception as exc:  # noqa: BLE001 — a missing column must not drop the radar
        return f"history skipped: {exc}"
    span = series_span(
        history.get("rows") or [],
        id_field="symbol",
        fields=("price_earnings_ttm",),
    )
    renamed = []
    for row in span:
        renamed.append(
            {
                "symbol": row.get("symbol"),
                "n": row.get("n"),
                "pe_pct": row.get("price_earnings_ttm_pct"),
                "pe_first": row.get("price_earnings_ttm_first"),
                "pe_last": row.get("price_earnings_ttm_last"),
                "first_as_of": row.get("first_as_of"),
                "last_as_of": row.get("last_as_of"),
            }
        )
    tables["valuation_span"] = renamed
    apply_hist_span(tables, renamed, spec=spec)
    return f"history span files={len(sources)} ids={len(symbols)} spanned={len(renamed)}"


def _print_result(result: dict[str, Any]) -> None:
    print(f"Run: {result['run_id']}")
    print(f"Folder: {result['out_dir']}")
    print(f"DuckDB: {result['duckdb']}")
    print(f"Queries: {result['queries']}")
    print(f"Highlights: {result['highlights_log']}")
    print(f"Tables: {result['tables']}")
    print(
        "Thresholds: "
        f"mcap>={result['spec']['min_market_cap_usd']:.0f} "
        f"growth>={result['spec']['min_growth_pct']}"
    )


def run_growth_radar_local_main() -> dict[str, Any]:
    """Edit the two thresholds here, then run this file with no arguments."""
    # ===== edit params here =====
    # Threshold 1. Names under this market cap are excluded (nano / illiquid).
    min_market_cap_usd = 1_000_000_000
    # Threshold 2. Clear this percent on quoted revenue growth or quoted earnings growth.
    min_growth_pct = 15.0
    top = 40
    industry_cap = 4
    overlooked_rng_max = 60.0
    hist_newest = 12
    all_fields_db = None
    output_root = None
    # ===== end params =====

    result = run_growth_radar_method(
        min_market_cap_usd=min_market_cap_usd,
        min_growth_pct=min_growth_pct,
        top=top,
        industry_cap=industry_cap,
        overlooked_rng_max=overlooked_rng_max,
        hist_newest=hist_newest,
        all_fields_db=all_fields_db,
        output_root=output_root,
    )
    _print_result(result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Growth radar from all-fields and the projection.")
    parser.add_argument("--min-mcap", type=float, default=1_000_000_000)
    parser.add_argument("--min-growth", type=float, default=15.0)
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument("--industry-cap", type=int, default=4)
    parser.add_argument("--all-fields-db", default=None)
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--no-history", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    result = run_growth_radar_method(
        min_market_cap_usd=args.min_mcap,
        min_growth_pct=args.min_growth,
        top=args.top,
        industry_cap=args.industry_cap,
        all_fields_db=args.all_fields_db,
        output_root=args.output_root,
        with_history=not args.no_history,
    )
    _print_result(result)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        main()
    else:
        run_growth_radar_local_main()
