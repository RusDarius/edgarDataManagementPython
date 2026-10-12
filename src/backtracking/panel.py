from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb
from generic_utils.derive import leftover_pct
from generic_utils.series import qident

from .config import BacktrackingConfig
from .contracts import DecisionWeek, PanelBuildResult

PREDICTION_PROFILE_FIELDS = ("bo", "cont", "fwd", "early", "sms", "recov", "rev", "frag", "exh")
TEXT_RAW_COLUMNS = frozenset({"industry", "sector", "exchange", "market", "type", "company", "name"})

IDENTITY_COLUMN_CANDIDATES: dict[str, tuple[str, ...]] = {
    "company": ("Company", "company"),
    "name": ("name", "description"),
    "exchange": ("exchange", "Exchange"),
    "country": ("country", "Country"),
    "sector": ("sector", "Sector"),
    "industry": ("industry", "Industry"),
    "market": ("market", "Market"),
    "type": ("type", "Type"),
    "close": ("close", "Close"),
    "market_cap_basic": ("market_cap_basic", "market_cap"),
}

EDGE_BASE_COLUMNS = (
    "unified_edge_highlight_score",
    "unified_edge_highlight_rank",
    "unified_consensus_score",
    "unified_best_outlook_name",
    "unified_best_outlook_score",
    "forward_valuation_upside_pct",
    "historical_validation_bucket",
    "historical_validation_score",
    "upside_prediction_score",
    "tradeable_safety_blend_score",
    "safety_companion_score",
    "big_mover_score",
    "confidence_score",
)


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _describe_columns(database: str | Path, table: str) -> set[str]:
    rows = query_move_prediction_duckdb(
        database,
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='main' AND table_name=?
        """,
        [table],
    )
    return {str(row.get("column_name") or "") for row in rows}


def _list_tables(database: str | Path) -> set[str]:
    rows = query_move_prediction_duckdb(
        database,
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema='main'
        """,
    )
    return {str(row.get("table_name") or "") for row in rows}


def _pick_identity_column(available: set[str], candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        if candidate in available:
            return candidate
    return None


def _fetch_prediction_universe(week: DecisionWeek) -> tuple[dict[str, dict[str, Any]], list[str]]:
    available = _describe_columns(week.prediction_database, "raw_scan_rows")
    if "symbol" not in available:
        raise ValueError(
            f"prediction raw_scan_rows missing symbol in {week.prediction_database.as_posix()}"
        )
    order_by = "r.row_number" if "row_number" in available else "r.symbol"
    select_parts = ['r."symbol" AS "symbol"']
    notes: list[str] = []
    for out_key, candidates in IDENTITY_COLUMN_CANDIDATES.items():
        col = _pick_identity_column(available, candidates)
        if col is None:
            select_parts.append(f"CAST(NULL AS VARCHAR) AS {qident(out_key)}")
            notes.append(f"raw_scan_rows missing identity column for {out_key}")
            continue
        select_parts.append(f"r.{qident(col)} AS {qident(out_key)}")
    sql = (
        f"SELECT {', '.join(select_parts)} "
        "FROM raw_scan_rows AS r "
        "WHERE r.run_id = ? "
        f"ORDER BY {order_by}"
    )
    rows = query_move_prediction_duckdb(week.prediction_database, sql, [week.prediction_run_id])
    by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = _normalize_symbol(row.get("symbol"))
        if not symbol or symbol in by_symbol:
            continue
        cleaned = {"symbol": symbol}
        for key in IDENTITY_COLUMN_CANDIDATES:
            value = row.get(key)
            if key in TEXT_RAW_COLUMNS:
                cleaned[key] = str(value).strip() if value not in (None, "") else None
            else:
                cleaned[key] = _coerce_float(value)
        by_symbol[symbol] = cleaned
    notes.append(f"prediction universe rows: {len(by_symbol)}")
    return by_symbol, notes


def _fetch_prediction_weeks_scores(week: DecisionWeek) -> dict[str, dict[str, Any]]:
    if "profile_horizon_scores" not in _list_tables(week.prediction_database):
        return {}
    rows = query_move_prediction_duckdb(
        week.prediction_database,
        """
        SELECT symbol,
               max(CASE WHEN profile_name IN ('breakout_long_v1', 'breakout_long') THEN score END) AS bo,
               max(CASE WHEN profile_name IN ('quality_continuation_v1', 'quality_continuation') THEN score END) AS cont,
               max(CASE WHEN profile_name IN ('forward_edge_active_v2', 'forward_edge_active') THEN score END) AS fwd,
               max(CASE WHEN profile_name IN ('early_momentum_inflection_v1', 'early_momentum_inflection') THEN score END) AS early,
               max(CASE WHEN profile_name IN ('sustained_momentum_safety_v1', 'sustained_momentum_safety') THEN score END) AS sms,
               max(CASE WHEN profile_name IN ('value_recovery_v3', 'value_recovery') THEN score END) AS recov,
               max(CASE WHEN profile_name IN ('upside_reversal_v1', 'upside_reversal') THEN score END) AS rev,
               max(CASE WHEN profile_name IN ('fragility_short', 'fragility_short_v1') THEN score END) AS frag,
               max(CASE WHEN profile_name IN ('mean_reversion_exhaustion_v1', 'mean_reversion_exhaustion') THEN score END) AS exh
        FROM profile_horizon_scores
        WHERE run_id = ?
          AND horizon_name = 'weeks'
        GROUP BY 1
        """,
        [week.prediction_run_id],
    )
    by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = _normalize_symbol(row.get("symbol"))
        if not symbol:
            continue
        by_symbol[symbol] = {field: _coerce_float(row.get(field)) for field in PREDICTION_PROFILE_FIELDS}
    return by_symbol


def _fetch_conviction(week: DecisionWeek) -> dict[str, dict[str, Any]]:
    if "conviction_rankings" not in _list_tables(week.prediction_database):
        return {}
    rows = query_move_prediction_duckdb(
        week.prediction_database,
        """
        SELECT symbol, conviction_score, rank_overall, manager_action_signal
        FROM conviction_rankings
        WHERE run_id = ?
        """,
        [week.prediction_run_id],
    )
    payload: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = _normalize_symbol(row.get("symbol"))
        if not symbol:
            continue
        payload[symbol] = {
            "conviction_score": _coerce_float(row.get("conviction_score")),
            "rank_overall": _coerce_float(row.get("rank_overall")),
            "manager_action_signal": (
                str(row.get("manager_action_signal")).strip()
                if row.get("manager_action_signal") not in (None, "")
                else None
            ),
        }
    return payload


def _load_focused_catalog(path: Path) -> list[str]:
    if not path.exists():
        return []
    fields: list[str] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        key = "Name" if "Name" in (reader.fieldnames or []) else (reader.fieldnames or [""])[0]
        for row in reader:
            value = str(row.get(key) or "").strip()
            if not value or value.lower() == "symbol" or value in seen:
                continue
            seen.add(value)
            fields.append(value)
    return fields


def _filter_numeric_columns(
    *,
    database: Path,
    run_id: str | None,
    candidates: Sequence[str],
) -> list[str]:
    if not candidates:
        return []
    select_sql = ", ".join(qident(col) for col in candidates)
    where_sql = "WHERE run_id = ?" if run_id else ""
    params: list[Any] = [run_id] if run_id else []
    rows = query_move_prediction_duckdb(
        database,
        f"SELECT {select_sql} FROM all_fields_rows {where_sql} LIMIT 250",
        params,
    )
    if not rows:
        return []
    numeric: list[str] = []
    for col in candidates:
        has_numeric = any(_coerce_float(row.get(col)) is not None for row in rows)
        if has_numeric:
            numeric.append(col)
    return numeric


def _resolve_raw_columns(
    config: BacktrackingConfig,
    *,
    week: DecisionWeek,
    raw_preset: str,
) -> tuple[list[str], list[str]]:
    if week.all_fields_database is None:
        return [], ["all-fields snapshot unavailable for this week"]
    available = _describe_columns(week.all_fields_database, "all_fields_rows")
    notes: list[str] = []

    core_present = [col for col in config.raw_fields.core_fields if col in available]
    missing_core = [col for col in config.raw_fields.core_fields if col not in available]
    for col in missing_core:
        notes.append(f"all_fields_rows missing core column {col!r}")

    if raw_preset != "focused":
        return core_present, notes

    focused_catalog = _load_focused_catalog(config.paths.focused_catalog_csv)
    focused_present = [col for col in focused_catalog if col in available and col not in core_present]
    numeric_focus = _filter_numeric_columns(
        database=week.all_fields_database,
        run_id=week.all_fields_run_id,
        candidates=focused_present,
    )
    notes.append(f"focused preset numeric fields attached: {len(numeric_focus)}")
    return core_present + numeric_focus, notes


def _fetch_all_fields_rows(
    *,
    week: DecisionWeek,
    symbols: Sequence[str],
    raw_columns: Sequence[str],
) -> dict[str, dict[str, Any]]:
    if week.all_fields_database is None or not symbols or not raw_columns:
        return {}
    available = _describe_columns(week.all_fields_database, "all_fields_rows")
    present = [col for col in raw_columns if col in available]
    if not present:
        return {}
    select_sql = ", ".join(['r."symbol" AS "symbol"'] + [f"r.{qident(col)} AS {qident(col)}" for col in present])
    where_sql = "WHERE r.run_id = ?" if week.all_fields_run_id else ""
    params: list[Any] = [week.all_fields_run_id] if week.all_fields_run_id else []
    rows = query_move_prediction_duckdb(
        week.all_fields_database,
        f"SELECT {select_sql} FROM all_fields_rows AS r {where_sql}",
        params,
    )
    universe = {_normalize_symbol(value) for value in symbols}
    by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = _normalize_symbol(row.get("symbol"))
        if not symbol or symbol not in universe:
            continue
        payload: dict[str, Any] = {}
        for col in present:
            value = row.get(col)
            if col in TEXT_RAW_COLUMNS:
                payload[col] = str(value).strip() if value not in (None, "") else None
            else:
                payload[col] = _coerce_float(value)
        by_symbol[symbol] = payload
    return by_symbol


def _fetch_edge_overlay(
    *,
    week: DecisionWeek,
    symbols: Sequence[str],
) -> tuple[dict[str, dict[str, Any]], tuple[str, ...]]:
    if week.edge_unified_database is None:
        return {}, ()
    if "symbol_unified_highlights" not in _list_tables(week.edge_unified_database):
        return {}, ()
    available = _describe_columns(week.edge_unified_database, "symbol_unified_highlights")
    ordered_edge_cols: list[str] = []
    for col in EDGE_BASE_COLUMNS:
        if col in available:
            ordered_edge_cols.append(col)
    # Keep remaining columns for visibility, but after known priority columns.
    for col in sorted(available):
        if col == "symbol" or col in ordered_edge_cols:
            continue
        ordered_edge_cols.append(col)

    select_sql = ", ".join([qident("symbol")] + [qident(col) for col in ordered_edge_cols])
    rows = query_move_prediction_duckdb(
        week.edge_unified_database,
        f"SELECT {select_sql} FROM symbol_unified_highlights",
    )
    universe = {_normalize_symbol(value) for value in symbols}
    by_symbol: dict[str, dict[str, Any]] = {}
    seen_columns: set[str] = set()
    for row in rows:
        symbol = _normalize_symbol(row.get("symbol"))
        if not symbol or symbol not in universe:
            continue
        payload: dict[str, Any] = {}
        for col in ordered_edge_cols:
            key = col if col.startswith("edge_") else f"edge_{col}"
            value = row.get(col)
            numeric = _coerce_float(value)
            payload[key] = numeric if numeric is not None else (
                str(value).strip() if value not in (None, "") else None
            )
            seen_columns.add(key)
        by_symbol[symbol] = payload
    return by_symbol, tuple(sorted(seen_columns))


def build_panel_rows(
    config: BacktrackingConfig,
    *,
    decision_weeks: Sequence[DecisionWeek],
    raw_preset: str,
) -> PanelBuildResult:
    rows: list[dict[str, Any]] = []
    notes: list[str] = []
    raw_columns_seen: set[str] = set()
    edge_columns_seen: set[str] = set()

    for week in decision_weeks:
        week_rows, week_notes, week_raw_cols, week_edge_cols = _build_week_rows(
            config,
            week=week,
            raw_preset=raw_preset,
        )
        rows.extend(week_rows)
        notes.extend(week_notes)
        raw_columns_seen.update(week_raw_cols)
        edge_columns_seen.update(week_edge_cols)

    return PanelBuildResult(
        rows=rows,
        notes=notes,
        raw_columns=tuple(sorted(raw_columns_seen)),
        edge_columns=tuple(sorted(edge_columns_seen)),
    )


def _build_week_rows(
    config: BacktrackingConfig,
    *,
    week: DecisionWeek,
    raw_preset: str,
) -> tuple[list[dict[str, Any]], list[str], tuple[str, ...], tuple[str, ...]]:
    notes: list[str] = [
        f"week {week.iso_year}-W{week.iso_week:02d} run_id={week.prediction_run_id}"
    ]
    universe_rows, universe_notes = _fetch_prediction_universe(week)
    notes.extend(universe_notes)
    symbols = sorted(universe_rows)
    if not symbols:
        return [], notes, (), ()

    score_rows = _fetch_prediction_weeks_scores(week)
    conviction_rows = _fetch_conviction(week)
    raw_columns, raw_notes = _resolve_raw_columns(config, week=week, raw_preset=raw_preset)
    notes.extend(raw_notes)
    all_fields_rows = _fetch_all_fields_rows(
        week=week,
        symbols=symbols,
        raw_columns=raw_columns,
    )
    edge_rows, edge_columns = _fetch_edge_overlay(week=week, symbols=symbols)

    week_payload: list[dict[str, Any]] = []
    for symbol in symbols:
        payload: dict[str, Any] = {
            "decision_date": week.decision_date.isoformat(),
            "decision_day_label": week.decision_day_label,
            "iso_year": week.iso_year,
            "iso_week": week.iso_week,
            "symbol": symbol,
            "prediction_run_id": week.prediction_run_id,
            "prediction_database": week.prediction_database.as_posix(),
            "all_fields_run_id": week.all_fields_run_id,
            "all_fields_day_label": week.all_fields_day_label,
            "all_fields_database": (
                week.all_fields_database.as_posix()
                if week.all_fields_database is not None
                else None
            ),
            "all_fields_lag_days": week.all_fields_lag_days,
            "has_edge": int(week.edge_unified_database is not None),
            "raw_preset": raw_preset,
        }
        payload.update(universe_rows.get(symbol, {}))
        payload.update(score_rows.get(symbol, {}))
        payload.update(conviction_rows.get(symbol, {}))
        payload.update(edge_rows.get(symbol, {}))
        payload.update(all_fields_rows.get(symbol, {}))

        close = _coerce_float(payload.get("close"))
        street_pt = _coerce_float(payload.get("price_target_average"))
        if street_pt is None:
            street_pt = _coerce_float(payload.get("price_target"))
        edge_upside = _coerce_float(payload.get("edge_forward_valuation_upside_pct"))
        payload["left"] = leftover_pct(close, street_pt, edge_upside)
        week_payload.append(payload)

    notes.append(f"week panel rows: {len(week_payload)}")
    return week_payload, notes, tuple(raw_columns), edge_columns
