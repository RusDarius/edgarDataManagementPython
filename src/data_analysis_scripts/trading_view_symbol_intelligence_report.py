# === AI GENERATED CODE START (GitHub Copilot - Claude Sonnet 5) ===
# Generated on: 2026-08-26
# Purpose: Aggregate every TradingView-based data flow (all-fields snapshot, move-prediction
#          profiles/consensus/conviction, and the full edge-research lens stack) for ONE symbol
#          into a single, readable log + JSON sidecar for active management / agent use.
"""Single-symbol intelligence report across all TradingView-based data flows.

Given a symbol (``NASDAQ:ABUS`` preferred, bare ticker also accepted), this module:

1. Identifies the company (full name, exchange, sector/industry) primarily from the latest
   ``trading_view_all_fields_data`` snapshot, optionally enriched from the
   ``trading_view_company_data_map`` MySQL table when reachable.
2. Pulls the symbol's full raw TradingView field catalog from the latest all-fields snapshot and
   groups it into value / technical / risk / other sections using the existing semantic classifier.
3. Computes the symbol's percentile positioning against its industry (falling back to sector) peers
   on core valuation/technical/liquidity metrics from that same snapshot.
4. Pulls the symbol's move-prediction scores: per-profile x horizon, blended consensus x horizon,
   and conviction/sleeve rankings from the latest weekly move-prediction DuckDB.
5. Pulls the symbol's rows from every edge-research-tools lens output present in the latest parent
   run (unified highlights, safety, upside prediction, forward valuation, tradeable safety, trade
   plan, screen, blindspot, earnings priority).
6. Writes one plain-text ``.log`` (human + agent readable) and one ``.json`` sidecar (structured) to
   ``logs/tradingview_analysis/symbol_intelligence/<SYMBOL>/<YYYY-MM-DD>/<SYMBOL>__run_<HHMMSS>.{log,json}``,
   so every symbol has its own folder and every scan date its own sub-folder; multiple same-day runs
   simply stack as additional ``run_<HHMMSS>`` files.

Usage:
    python -m src.data_analysis_scripts.trading_view_symbol_intelligence_report --symbol NASDAQ:ABUS

Programmatic:
    from data_analysis_scripts.trading_view_symbol_intelligence_report import (
        build_symbol_intelligence_report,
    )
    result = build_symbol_intelligence_report("NASDAQ:ABUS")
    print(result["log_path"])

Notes:
    - Every data source is optional and independently best-effort: missing databases/CSVs/MySQL
      connectivity add a warning line to the report instead of raising, so the flow always produces
      a log with whatever coverage is actually available.
    - Peer/industry positioning and the raw field dump reuse
      ``trading_view_field_semantic_classifier.classify_semantic_bucket`` so new TradingView fields
      are auto-grouped without maintaining a hardcoded field list here.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
    DEFAULT_ALL_FIELDS_ROOT,
)
from data_analysis_scripts.trading_view_move_prediction_pattern_discovery import (
    DEFAULT_PREDICTION_ROOT,
    discover_latest_all_fields_db,
    discover_latest_prediction_db,
)
from data_analysis_scripts.trading_view_field_semantic_classifier import (
    classify_semantic_bucket,
)
from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb
from edge_research_tools.run_resolution import discover_latest_edge_parent_run_dir
from generic_utils.log_to_files_util import log_to_file

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "logs" / "tradingview_analysis" / "symbol_intelligence"
)

NON_METRIC_ALL_FIELDS_COLUMNS = frozenset({"run_id", "row_number", "symbol"})

# Priority ordering for the raw all-fields dump. Matches the user-facing requirement that
# value / technical / risk are the MAIN sections; everything else is supporting detail.
VALUE_BUCKETS: tuple[str, ...] = (
    "valuation",
    "fundamentals_profitability",
    "fundamentals_growth",
    "fundamentals_balance_sheet",
    "cash_flow_dividend",
    "analyst_expectations",
)
TECHNICAL_BUCKETS: tuple[str, ...] = (
    "technical_trend",
    "technical_oscillator",
    "technical_pattern",
    "momentum_performance",
    "price_ohlc",
)
RISK_BUCKETS: tuple[str, ...] = (
    "volatility_risk",
    "event_catalyst",
    "structural_calendar",
)
OTHER_BUCKETS: tuple[str, ...] = (
    "volume_liquidity",
    "fund_structure",
    "fixed_income",
    "metadata_classification",
    "other",
)

# (all_fields column, display label, interpretation) used for industry/sector peer percentile ranks.
PEER_METRIC_CANDIDATES: tuple[tuple[str, str, str], ...] = (
    ("market_cap_basic", "Market Cap", "higher=larger"),
    ("price_earnings_ttm", "P/E (TTM)", "lower=cheaper"),
    ("price_book_fq", "P/B (FQ)", "lower=cheaper"),
    ("enterprise_value_ebitda_ttm", "EV/EBITDA (TTM)", "lower=cheaper"),
    ("Perf.YTD", "Perf YTD", "higher=stronger"),
    ("Perf.Y", "Perf 1Y", "higher=stronger"),
    ("Perf.W", "Perf 1W", "higher=stronger"),
    ("relative_volume_10d_calc", "Relative Volume (10D)", "higher=more attention"),
    ("ATRP", "ATR %", "context only (volatility)"),
    ("Recommend.All", "Technical Rating", "higher=more bullish"),
    ("float_shares_percent_current", "Float %", "lower=tighter float"),
    ("dividends_yield_current", "Dividend Yield", "higher=more income"),
    ("beta_1_year", "Beta (1Y)", "context only (market sensitivity)"),
)

# (label, relative path under the edge-research parent run dir, short description)
EDGE_RESEARCH_SOURCES: tuple[tuple[str, str, str], ...] = (
    (
        "unified_edge_highlights",
        "edge_unified_highlights/edge_unified_highlights.csv",
        "Unified edge rank/score plus historical validation.",
    ),
    (
        "safety_scored_universe",
        "edge_unified_highlights/edge_unified_highlights_safety_scored_universe.csv",
        "Full safety/quality/value companion score detail.",
    ),
    (
        "big_mover_confidence_shortlist",
        "highlights/edge_name_shortlist.csv",
        "Big-mover and confidence shortlist ranks.",
    ),
    (
        "upside_prediction",
        "upside_prediction_lens/edge_upside_prediction_ranked.csv",
        "Upside prediction lens rank/score.",
    ),
    (
        "forward_upside_valuation",
        "forward_upside_valuation_lens/edge_forward_upside_valuation_ranked.csv",
        "Forward valuation upside overlay.",
    ),
    (
        "tradeable_safety",
        "tradeable_safety_lens/edge_tradeable_safety_overlay.csv",
        "Tradeable-safety blended overlay.",
    ),
    (
        "safety_highlights",
        "safety_highlights/edge_safety_scored.csv",
        "Safety highlights scoring detail.",
    ),
    (
        "earnings_priority",
        "earnings_priority_lens/edge_earnings_priority_candidates.csv",
        "Earnings-priority coverage lens.",
    ),
    (
        "edge_trade_plan",
        "edge_trade_plan/edge_trade_plan_ranked.csv",
        "Edge trade plan ranked candidates.",
    ),
    (
        "screen",
        "screen/edge_screen_ranked.csv",
        "Point-in-time screen ranked universe.",
    ),
    (
        "blindspot_candidates",
        "blindspot_lane/edge_blindspot_symbol_candidates.csv",
        "Blindspot lane symbol candidates.",
    ),
)


def _symbol_candidates(symbol: str) -> tuple[str, str]:
    normalized = str(symbol or "").strip().upper()
    bare = normalized.split(":", 1)[1] if ":" in normalized else normalized
    return normalized, bare


def _symbol_file_token(symbol: str) -> str:
    token = "".join(
        ch if ch.isalnum() else "_" for ch in str(symbol or "").strip().upper()
    )
    return token.strip("_") or "SYMBOL"


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _find_symbol_row(rows: list[dict[str, str]], symbol: str) -> dict[str, str] | None:
    normalized, bare = _symbol_candidates(symbol)
    exact = [
        row for row in rows if str(row.get("symbol", "")).strip().upper() == normalized
    ]
    if exact:
        return exact[0]
    bare_matches = [
        row
        for row in rows
        if str(row.get("symbol", "")).strip().upper().split(":")[-1] == bare
    ]
    return bare_matches[0] if bare_matches else None


def _select_populous_latest_run_id(
    all_fields_db: Path, *, min_rows: int = 1000
) -> str | None:
    """Pick the most recent run_id with a reasonably complete universe.

    Guards against a stray tiny/broken intraday run (for example a failed focused
    export that only returned a handful of rows) silently becoming "the latest
    run" used for symbol lookups and peer/industry comparisons.
    """
    sql = """
        SELECT r.run_id, COUNT(*) AS row_count, MAX(m.created_at_utc) AS created_at_utc
        FROM all_fields_rows AS r
        LEFT JOIN run_metadata AS m USING (run_id)
        GROUP BY r.run_id
        ORDER BY created_at_utc DESC NULLS LAST
    """
    rows = query_move_prediction_duckdb(all_fields_db, sql)
    if not rows:
        return None
    for row in rows:
        if int(row.get("row_count") or 0) >= min_rows:
            return str(row.get("run_id"))
    # Every run this day is small (e.g. a fresh database); fall back to the most
    # recent one rather than reporting no data at all.
    return str(rows[0]["run_id"])


def _query_all_fields_symbol_row(
    all_fields_db: Path,
    *,
    normalized: str,
    bare: str,
    run_id: str | None,
) -> dict[str, Any] | None:
    if run_id is not None:
        sql = """
            SELECT r.*
            FROM all_fields_rows AS r
            WHERE r.run_id = ?
              AND (UPPER(r.symbol) = ? OR UPPER(r.symbol) LIKE '%:' || ?)
        """
        params: list[Any] = [run_id, normalized, bare]
    else:
        # No good run scoped: search every run for this symbol (newest first) so a
        # symbol missing only from the current run still resolves to its most
        # recent available data instead of "not found".
        sql = """
            SELECT r.*
            FROM all_fields_rows AS r
            LEFT JOIN run_metadata AS m USING (run_id)
            WHERE UPPER(r.symbol) = ? OR UPPER(r.symbol) LIKE '%:' || ?
            ORDER BY m.created_at_utc DESC NULLS LAST, r.row_number DESC
            LIMIT 200
        """
        params = [normalized, bare]
    rows = query_move_prediction_duckdb(all_fields_db, sql, params)
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    exact = [
        row for row in rows if str(row.get("symbol", "")).strip().upper() == normalized
    ]
    if exact:
        return exact[0]

    def _market_cap(row: dict[str, Any]) -> float:
        try:
            return float(row.get("market_cap_basic") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    return max(rows, key=_market_cap)


def _load_all_fields_snapshot(
    all_fields_db: Path, symbol: str, *, preferred_run_id: str | None = None
) -> dict[str, Any] | None:
    normalized, bare = _symbol_candidates(symbol)
    if preferred_run_id is not None:
        row = _query_all_fields_symbol_row(
            all_fields_db, normalized=normalized, bare=bare, run_id=preferred_run_id
        )
        if row is not None:
            return row
        # Symbol missing from the resolved "good" run only -- fall back to the
        # most recent run of any size that actually has it.
    return _query_all_fields_symbol_row(
        all_fields_db, normalized=normalized, bare=bare, run_id=None
    )


def _table_columns(database_path: Path, table_name: str) -> set[str]:
    try:
        rows = query_move_prediction_duckdb(database_path, f"DESCRIBE {table_name}")
    except Exception:
        return set()
    return {str(row.get("column_name")) for row in rows if row.get("column_name")}


def _load_industry_peer_context(
    all_fields_db: Path, target_row: dict[str, Any], *, run_id: str | None = None
) -> dict[str, Any] | None:
    industry = str(target_row.get("industry") or "").strip()
    sector = str(target_row.get("sector") or "").strip()
    if not industry and not sector:
        return None

    available = _table_columns(all_fields_db, "all_fields_rows")
    metrics = [
        (column, label, direction)
        for column, label, direction in PEER_METRIC_CANDIDATES
        if column in available
    ]
    if not metrics:
        return None

    resolved_run_id = run_id or _select_populous_latest_run_id(all_fields_db)
    if resolved_run_id is None:
        return None

    def _query_peers(group_column: str, group_value: str) -> list[dict[str, Any]]:
        select_parts = ", ".join(
            f'TRY_CAST(r."{column}" AS DOUBLE) AS "{column}"'
            for column, _, _ in metrics
        )
        sql = f"""
            SELECT r.symbol, {select_parts}
            FROM all_fields_rows AS r
            WHERE r.run_id = ? AND r."{group_column}" = ?
        """
        return query_move_prediction_duckdb(
            all_fields_db, sql, [resolved_run_id, group_value]
        )

    scope_label = ""
    peer_rows: list[dict[str, Any]] = []
    if industry:
        peer_rows = _query_peers("industry", industry)
        scope_label = f"industry='{industry}'"
    if len(peer_rows) < 5 and sector:
        peer_rows = _query_peers("sector", sector)
        scope_label = f"sector='{sector}' (industry peer count was too small)"
    if len(peer_rows) < 2:
        return None

    target_symbol_normalized = str(target_row.get("symbol") or "").strip().upper()
    metric_results: dict[str, Any] = {}
    for column, label, direction in metrics:
        values = [
            (str(row.get("symbol") or "").strip().upper(), row.get(column))
            for row in peer_rows
            if row.get(column) is not None
        ]
        if len(values) < 2:
            continue
        target_value = next(
            (value for sym, value in values if sym == target_symbol_normalized), None
        )
        if target_value is None:
            continue
        sorted_values = sorted(value for _, value in values)
        rank_below = sum(1 for value in sorted_values if value < target_value)
        percentile = (
            rank_below / (len(sorted_values) - 1) if len(sorted_values) > 1 else 0.5
        )
        metric_results[label] = {
            "value": round(target_value, 4),
            "percentile": round(percentile * 100, 1),
            "peer_count": len(sorted_values),
            "direction": direction,
        }

    if not metric_results:
        return None
    return {
        "scope": scope_label,
        "peer_count": len(peer_rows),
        "metrics": metric_results,
    }


def _bucket_all_fields_row(
    all_fields_row: dict[str, Any],
) -> dict[str, list[tuple[str, Any]]]:
    buckets: dict[str, list[tuple[str, Any]]] = {}
    for field_name, value in all_fields_row.items():
        if field_name in NON_METRIC_ALL_FIELDS_COLUMNS:
            continue
        if value in (None, ""):
            continue
        try:
            bucket, _tags = classify_semantic_bucket(field_name, "")
        except Exception:
            bucket = "other"
        buckets.setdefault(bucket, []).append((field_name, value))
    for fields in buckets.values():
        fields.sort(key=lambda item: item[0])
    return buckets


def _load_move_prediction_context(
    prediction_db: Path, symbol: str
) -> dict[str, Any] | None:
    normalized, bare = _symbol_candidates(symbol)
    params = [normalized, bare]
    predicate = "UPPER(symbol) = ? OR UPPER(symbol) LIKE '%:' || ?"

    def _query(table_name: str, order_by: str) -> list[dict[str, Any]]:
        sql = f"""
            WITH latest_run AS (
                SELECT run_id FROM run_metadata
                ORDER BY created_at_utc DESC NULLS LAST LIMIT 1
            )
            SELECT t.*
            FROM {table_name} AS t
            JOIN latest_run USING (run_id)
            WHERE {predicate}
            {order_by}
        """
        try:
            return query_move_prediction_duckdb(prediction_db, sql, params)
        except Exception:
            return []

    profile_horizon_rows = _query(
        "profile_horizon_scores", "ORDER BY profile_name, horizon_name"
    )
    consensus_horizon_rows = _query("consensus_horizon_scores", "ORDER BY horizon_name")
    conviction_rows = _query("conviction_rankings", "ORDER BY sleeve")

    if not (profile_horizon_rows or consensus_horizon_rows or conviction_rows):
        return None
    return {
        "profile_horizon_scores": profile_horizon_rows,
        "consensus_horizon_scores": consensus_horizon_rows,
        "conviction_rankings": conviction_rows,
    }


def _load_edge_research_context(parent_run_dir: Path, symbol: str) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for label, relative_path, notes in EDGE_RESEARCH_SOURCES:
        csv_path = parent_run_dir / relative_path
        if not csv_path.exists():
            continue
        try:
            rows = _read_csv_rows(csv_path)
        except Exception as exc:
            context[label] = {"path": csv_path.as_posix(), "error": str(exc)}
            continue
        context[label] = {
            "path": csv_path.as_posix(),
            "notes": notes,
            "population": len(rows),
            "row": _find_symbol_row(rows, symbol),
        }
    return context


def _resolve_company_identity(
    symbol: str, all_fields_row: dict[str, Any] | None
) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "requested_symbol": symbol,
        "matched_symbol": "",
        "full_name": "",
        "exchange": "",
        "sector": "",
        "industry": "",
        "currency": "",
        "market": "",
        "identity_sources": [],
    }

    if all_fields_row:
        identity["matched_symbol"] = str(all_fields_row.get("symbol") or "").strip()
        identity["full_name"] = str(
            all_fields_row.get("description") or all_fields_row.get("name") or ""
        ).strip()
        identity["exchange"] = str(all_fields_row.get("exchange") or "").strip()
        identity["sector"] = str(all_fields_row.get("sector") or "").strip()
        identity["industry"] = str(all_fields_row.get("industry") or "").strip()
        identity["currency"] = str(all_fields_row.get("currency") or "").strip()
        identity["market"] = str(all_fields_row.get("market") or "").strip()
        identity["identity_sources"].append("trading_view_all_fields_rows")

    try:
        from db.trading_view_company_data_map_operations import (
            get_trading_view_company_by_symbol,
        )

        company_row = get_trading_view_company_by_symbol(symbol)
    except Exception as exc:
        company_row = None
        identity["company_data_map_error"] = str(exc)

    if company_row:
        identity["identity_sources"].append("trading_view_company_data_map (mysql)")
        if not identity["matched_symbol"]:
            identity["matched_symbol"] = str(company_row.get("symbol") or "").strip()
        if not identity["full_name"]:
            identity["full_name"] = str(
                company_row.get("name") or company_row.get("description") or ""
            ).strip()
        identity["exchange"] = (
            identity["exchange"] or str(company_row.get("exchange") or "").strip()
        )
        identity["market_cap_basic_mysql"] = company_row.get("market_cap_basic")
        identity["perf_snapshot_mysql"] = {
            "perf_w": company_row.get("perf_w"),
            "perf_1m": company_row.get("perf_1m"),
            "perf_ytd": company_row.get("perf_ytd"),
            "perf_y": company_row.get("perf_y"),
            "perf_6m": company_row.get("perf_6m"),
            "perf_5y": company_row.get("perf_5y"),
        }

    if not identity["full_name"]:
        identity["full_name"] = (
            "UNKNOWN - not found in any available TradingView data source"
        )
    if not identity["matched_symbol"]:
        identity["matched_symbol"] = str(symbol).strip().upper()

    return identity


def build_symbol_intelligence_report(
    symbol: str,
    *,
    all_fields_root: str | Path | None = None,
    prediction_root: str | Path | None = None,
    edge_research_output_root: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Aggregate all TradingView-based data for ``symbol`` and write a log + JSON report."""
    requested_symbol = str(symbol or "").strip()
    if not requested_symbol:
        raise ValueError("symbol is required")

    generated_at_utc = datetime.now(timezone.utc)
    result: dict[str, Any] = {
        "requested_symbol": requested_symbol,
        "generated_at_utc": generated_at_utc.isoformat(),
        "sources": {},
        "warnings": [],
    }

    resolved_all_fields_root = (
        Path(all_fields_root) if all_fields_root else DEFAULT_ALL_FIELDS_ROOT
    )
    all_fields_db = discover_latest_all_fields_db(resolved_all_fields_root)
    all_fields_row: dict[str, Any] | None = None
    if all_fields_db is None:
        result["warnings"].append(
            f"No tradingview_all_fields_*.duckdb snapshot found under {resolved_all_fields_root.as_posix()}."
        )
    else:
        result["sources"]["all_fields_database"] = str(all_fields_db)
        try:
            primary_all_fields_run_id = _select_populous_latest_run_id(all_fields_db)
        except Exception as exc:
            primary_all_fields_run_id = None
            result["warnings"].append(f"Could not resolve latest all-fields run: {exc}")
        try:
            all_fields_row = _load_all_fields_snapshot(
                all_fields_db,
                requested_symbol,
                preferred_run_id=primary_all_fields_run_id,
            )
        except Exception as exc:
            result["warnings"].append(f"All-fields lookup failed: {exc}")
        if all_fields_row is None:
            result["warnings"].append(
                f"Symbol not found in latest all-fields snapshot: {requested_symbol}"
            )

    identity = _resolve_company_identity(requested_symbol, all_fields_row)
    result["identity"] = identity
    matched_symbol = identity["matched_symbol"]
    result["matched_symbol"] = matched_symbol

    if all_fields_row:
        result["all_fields_buckets"] = _bucket_all_fields_row(all_fields_row)
        result["all_fields_run_context"] = {
            "run_id": all_fields_row.get("run_id"),
            "row_number": all_fields_row.get("row_number"),
        }
        try:
            result["peer_relative_positioning"] = _load_industry_peer_context(
                all_fields_db, all_fields_row, run_id=primary_all_fields_run_id
            )
        except Exception as exc:
            result["warnings"].append(f"Peer/industry positioning failed: {exc}")

    resolved_prediction_root = (
        Path(prediction_root) if prediction_root else DEFAULT_PREDICTION_ROOT
    )
    prediction_db = discover_latest_prediction_db(resolved_prediction_root)
    if prediction_db is None:
        result["warnings"].append(
            f"No move_prediction_*.duckdb weekly database found under {resolved_prediction_root.as_posix()}."
        )
    else:
        result["sources"]["move_prediction_database"] = str(prediction_db)
        try:
            move_prediction_context = _load_move_prediction_context(
                prediction_db, matched_symbol
            )
        except Exception as exc:
            move_prediction_context = None
            result["warnings"].append(f"Move-prediction lookup failed: {exc}")
        if move_prediction_context is None:
            result["warnings"].append(
                f"Symbol not found in latest move-prediction run: {matched_symbol}"
            )
        else:
            result["move_prediction"] = move_prediction_context

    try:
        parent_run_dir = discover_latest_edge_parent_run_dir(
            output_root=edge_research_output_root
        )
        result["sources"]["edge_research_parent_run_dir"] = str(parent_run_dir)
        result["edge_research"] = _load_edge_research_context(
            parent_run_dir, matched_symbol
        )
    except Exception as exc:
        result["warnings"].append(f"Edge-research lookup unavailable: {exc}")

    resolved_output_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_ROOT
    log_path, json_path = _write_symbol_intelligence_log(
        result, resolved_output_dir, generated_at_utc
    )
    result["log_path"] = str(log_path)
    result["json_path"] = str(json_path)
    return result


def _write_bucket_group(
    write_line,
    title: str,
    bucket_names: Sequence[str],
    buckets: dict[str, list[tuple[str, Any]]],
) -> None:
    write_line("-" * 100)
    write_line(title)
    write_line("-" * 100)
    any_written = False
    for bucket_name in bucket_names:
        fields = buckets.get(bucket_name) or []
        if not fields:
            continue
        any_written = True
        write_line(f"  [{bucket_name}]")
        for field_name, value in fields:
            write_line(f"    {field_name} = {value}")
    if not any_written:
        write_line("  (no data available)")
    write_line("")


def _write_symbol_intelligence_log(
    result: dict[str, Any], output_dir: Path, generated_at_utc: datetime
) -> tuple[Path, Path]:
    token = _symbol_file_token(
        result.get("matched_symbol") or result["requested_symbol"]
    )
    date_label = generated_at_utc.strftime("%Y-%m-%d")
    run_timestamp = generated_at_utc.strftime("%H%M%S")
    # One folder per symbol, one sub-folder per scan date; same-day reruns stack as run_<time>.
    run_dir = output_dir / token / date_label
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / f"{token}__run_{run_timestamp}.log"
    json_path = run_dir / f"{token}__run_{run_timestamp}.json"
    if log_path.exists():
        log_path.unlink()

    def write_line(text: str = "") -> None:
        log_to_file(log_path, text)

    identity = result.get("identity") or {}
    write_line("=" * 100)
    write_line(
        f"SYMBOL INTELLIGENCE REPORT - {result.get('matched_symbol','')} "
        f"({identity.get('full_name', 'UNKNOWN')})"
    )
    write_line("=" * 100)
    write_line(f"Requested symbol : {result.get('requested_symbol','')}")
    write_line(f"Matched symbol   : {result.get('matched_symbol','')}")
    write_line(
        f"Exchange/Market  : {identity.get('exchange','')} / {identity.get('market','')}"
    )
    write_line(
        f"Sector/Industry  : {identity.get('sector','')} / {identity.get('industry','')}"
    )
    write_line(f"Currency         : {identity.get('currency','')}")
    write_line(f"Generated (UTC)  : {generated_at_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    if identity.get("identity_sources"):
        write_line(f"Identity sources : {', '.join(identity['identity_sources'])}")
    write_line("")

    warnings = result.get("warnings") or []
    if warnings:
        write_line("-" * 100)
        write_line("DATA COVERAGE WARNINGS")
        write_line("-" * 100)
        for warning in warnings:
            write_line(f"  - {warning}")
        write_line("")

    buckets = result.get("all_fields_buckets") or {}
    _write_bucket_group(
        write_line, "VALUATION & FUNDAMENTALS (VALUE)", VALUE_BUCKETS, buckets
    )
    _write_bucket_group(write_line, "TECHNICALS & MOMENTUM", TECHNICAL_BUCKETS, buckets)
    _write_bucket_group(
        write_line, "RISK / VOLATILITY / CATALYSTS", RISK_BUCKETS, buckets
    )

    peer_context = result.get("peer_relative_positioning")
    write_line("-" * 100)
    write_line("PEER / INDUSTRY RELATIVE POSITIONING")
    write_line("-" * 100)
    if peer_context:
        write_line(
            f"Scope: {peer_context['scope']} | peers considered: {peer_context['peer_count']}"
        )
        for label, info in peer_context["metrics"].items():
            write_line(
                f"  {label:<24} value={info['value']:<14} percentile={info['percentile']}th "
                f"(n={info['peer_count']}, {info['direction']})"
            )
    else:
        write_line(
            "  Not available (insufficient peer population or missing industry/sector)."
        )
    write_line("")

    move_prediction = result.get("move_prediction")
    write_line("-" * 100)
    write_line("MOVE-PREDICTION SCORES (profiles x horizons, consensus, conviction)")
    write_line("-" * 100)
    if move_prediction:
        conviction_rows = move_prediction.get("conviction_rankings") or []
        if conviction_rows:
            write_line("Conviction rankings:")
            for row in conviction_rows:
                write_line(
                    f"  sleeve={row.get('sleeve')} conviction_score={row.get('conviction_score')} "
                    f"rank_overall={row.get('rank_overall')} rank_in_sleeve={row.get('rank_in_sleeve')} "
                    f"entry_readiness={row.get('entry_readiness')} "
                    f"manager_action_signal={row.get('manager_action_signal')} "
                    f"breakout_conviction_tier={row.get('breakout_conviction_tier')}"
                )
            write_line("")
        consensus_rows = move_prediction.get("consensus_horizon_scores") or []
        if consensus_rows:
            write_line("Consensus (all profiles blended), by horizon:")
            for row in consensus_rows:
                write_line(
                    f"  horizon={str(row.get('horizon_name') or ''):<8} score={row.get('score')} "
                    f"direction={row.get('direction')} confidence={row.get('confidence')} "
                    f"agreement_ratio={row.get('agreement_ratio')} opinions={row.get('opinions')} "
                    f"risk_tier={row.get('risk_tier')} "
                    f"manager_action_signal={row.get('manager_action_signal')}"
                )
            write_line("")
        profile_rows = move_prediction.get("profile_horizon_scores") or []
        if profile_rows:
            write_line("Per-profile scores, by horizon:")
            for row in profile_rows:
                write_line(
                    f"  profile={str(row.get('profile_name') or ''):<28} "
                    f"horizon={str(row.get('horizon_name') or ''):<8} score={row.get('score')} "
                    f"direction={row.get('direction')} confidence={row.get('confidence')} "
                    f"setup={row.get('setup')} risk_tier={row.get('risk_tier')}"
                )
    else:
        write_line("  Not available in the latest weekly move-prediction database.")
    write_line("")

    edge_research = result.get("edge_research") or {}
    write_line("-" * 100)
    write_line("EDGE RESEARCH TOOLS (rankings, safety, valuation, trade plan)")
    write_line("-" * 100)
    if edge_research:
        for label, entry in edge_research.items():
            write_line(
                f"[{label}] source={entry.get('path')} (population={entry.get('population', 'n/a')})"
            )
            row = entry.get("row")
            if row:
                for key, value in row.items():
                    if key == "symbol" or value in (None, ""):
                        continue
                    write_line(f"    {key} = {value}")
            elif entry.get("error"):
                write_line(f"    (error reading source: {entry['error']})")
            else:
                write_line("    (symbol not present in this dataset)")
            write_line("")
    else:
        write_line("  No edge-research parent run outputs were found.")
    write_line("")

    write_line("=" * 100)
    write_line("RAW SUPPORTING FIELD DUMP (remaining lower-priority categories)")
    write_line("=" * 100)
    _write_bucket_group(
        write_line, "OTHER / VOLUME / STRUCTURE / METADATA", OTHER_BUCKETS, buckets
    )

    json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return log_path, json_path


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a comprehensive single-symbol intelligence report across all "
            "TradingView-based data flows (all-fields, move-prediction, edge-research)."
        )
    )
    parser.add_argument(
        "--symbol", required=True, help="Symbol to inspect, e.g. NASDAQ:ABUS"
    )
    parser.add_argument("--all-fields-root", type=Path, default=None)
    parser.add_argument("--prediction-root", type=Path, default=None)
    parser.add_argument("--edge-research-output-root", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()
    result = build_symbol_intelligence_report(
        args.symbol,
        all_fields_root=args.all_fields_root,
        prediction_root=args.prediction_root,
        edge_research_output_root=args.edge_research_output_root,
        output_dir=args.output_dir,
    )
    print(f"Matched symbol : {result['matched_symbol']}")
    print(f"Log written to : {result['log_path']}")
    print(f"JSON written to: {result['json_path']}")
    if result.get("warnings"):
        print("Warnings:")
        for warning in result["warnings"]:
            print(f"  - {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# === AI GENERATED CODE END ===
