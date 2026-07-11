from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from data_analysis_scripts.trading_view_targets_analysis import (
    DEFAULT_LENS_WEIGHTS,
    LENS_ORDER,
    MAX_UPSIDE_BLEND_MULTIPLE,
    TARGET_HORIZONS,
    _analyst_consensus_targets,
    _blend_horizon_target,
    _book_value_targets,
    _build_company_profile,
    _build_component_scores,
    _build_derived_metrics,
    _build_metric_profiles,
    _build_peer_multiple_stats,
    _build_quality_flags,
    _cost_of_equity,
    _enrich_with_peer_metrics,
    _multiple_anchored_targets,
    _range_anchored_targets,
    _technical_anchored_targets,
    _trajectory_targets,
    _yield_dcf_targets,
    resolve_move_prediction_scoring_profile,
)

from .upside_prediction import compute_upside_prediction_fields

MAX_ACTIVE_VALUATION_LENSES = 4
MIN_USABLE_VALUATION_LENSES = 2
MIN_PEER_GROUP_SIZE = 2

FORWARD_UPSIDE_VALUATION_SAFETY_TRAILING_COLUMNS: tuple[str, ...] = (
    "balance_sheet_safety_score",
    "cash_generation_value_score",
    "safety_companion_score",
    "historical_validation_score",
    "historical_validation_bucket",
    "historical_validation_pass",
    "historical_validation_occurrence_count",
    "historical_validation_win_rate",
    "historical_validation_median_fwd_pct",
    "historical_validation_any_setup_rate",
    "historical_validation_stability_score",
    "safety_bucket",
    "indicator_pass_count",
    "safety_rank_global",
    "safety_shortlist_flag",
    "safety_focus_flag",
    "safety_data_available",
)

FORWARD_UPSIDE_VALUATION_OVERLAY_COLUMNS: tuple[str, ...] = (
    "forward_upside_rank",
    "forward_upside_score",
    "forward_upside_mode",
    "forward_upside_fallback_reason",
    "forward_valuation_upside_pct",
    "forward_rank_horizon_valuation_upside_pct",
    "forward_valuation_bear_upside_pct",
    "forward_valuation_bull_upside_pct",
    "forward_company_style",
    "forward_selected_lenses",
    "forward_selected_lens_count",
    "forward_peer_scope",
    "forward_peer_scope_size",
    "forward_near_term_base_upside_pct",
    "forward_medium_term_base_upside_pct",
    "forward_long_term_base_upside_pct",
    "forward_supporting_upside_prediction_score",
)

STYLE_LENS_PRIORITY: dict[str, tuple[str, ...]] = {
    "growth": ("trajectory", "multiple", "analyst", "technical", "range"),
    "value": ("multiple", "book_value", "analyst", "range", "technical"),
    "income": ("yield_dcf", "multiple", "analyst", "range", "book_value"),
    "risk": ("multiple", "range", "technical", "analyst", "book_value"),
    "balanced": ("multiple", "analyst", "trajectory", "range", "technical"),
}

ROBUST_BACKFILL_ORDER: tuple[str, ...] = (
    "multiple",
    "range",
    "technical",
    "analyst",
    "book_value",
    "trajectory",
    "yield_dcf",
)


def _import_duckdb():
    import duckdb

    return duckdb


def _normalize_symbol_field(row: dict[str, Any]) -> dict[str, Any]:
    """Backfill a missing symbol from exchange:bare_ticker when available.

    Valuation rows are keyed by symbol for peer lookups and downstream joins.
    When the upstream shortlist leaves ``symbol`` blank, reconstruct it from
    ``exchange`` and ``bare_ticker`` so the lens output keeps a usable key.
    """
    symbol = str(row.get("symbol") or "").strip()
    if symbol:
        return row
    exchange = str(row.get("exchange") or "").strip()
    bare_ticker = str(row.get("bare_ticker") or "").strip()
    if ":" in bare_ticker and not exchange:
        row["symbol"] = bare_ticker
    elif exchange and bare_ticker:
        row["symbol"] = f"{exchange}:{bare_ticker}"
    elif bare_ticker:
        row["symbol"] = bare_ticker
    else:
        row["symbol"] = ""
    return row


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _round_float(value: float | None, digits: int = 4) -> float | str:
    if value is None:
        return ""
    return round(float(value), digits)


def _table_exists(connection: Any, *, database_name: str, table_name: str) -> bool:
    row = connection.execute(
        """
        SELECT COUNT(*)
        FROM duckdb_tables()
        WHERE database_name = ? AND table_name = ?
        """,
        [database_name, table_name],
    ).fetchone()
    return bool(row and int(row[0]) > 0)


def _fetch_dict_rows(connection: Any, query: str) -> list[dict[str, Any]]:
    cursor = connection.execute(query)
    columns = [str(item[0]) for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def load_latest_source_rows_for_symbols(
    *,
    source_database_path: str | Path,
    symbols: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """
    Pull one latest all-fields row per requested symbol from the source daily DB.
    """
    unique_symbols = sorted(
        {str(symbol).strip() for symbol in symbols if str(symbol).strip()}
    )
    if not unique_symbols:
        return {}

    db_path = Path(source_database_path)
    if not db_path.exists():
        raise FileNotFoundError(
            f"Source daily database not found: {db_path.as_posix()}"
        )

    duckdb = _import_duckdb()
    conn = duckdb.connect(database=":memory:")
    try:
        conn.execute(f"ATTACH {_q(db_path.as_posix())} AS src_daily (READ_ONLY)")
        if not _table_exists(
            conn,
            database_name="src_daily",
            table_name="all_fields_rows",
        ):
            return {}

        symbol_literals = ", ".join(_q(symbol) for symbol in unique_symbols)
        has_run_metadata = _table_exists(
            conn,
            database_name="src_daily",
            table_name="run_metadata",
        )
        if has_run_metadata:
            query = f"""
                SELECT * EXCLUDE (row_num)
                FROM (
                    SELECT
                        r.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY CAST(r.symbol AS VARCHAR)
                            ORDER BY m.created_at_utc DESC NULLS LAST, r.run_id DESC, r.row_number DESC
                        ) AS row_num
                    FROM src_daily.all_fields_rows AS r
                    LEFT JOIN src_daily.run_metadata AS m USING (run_id)
                    WHERE CAST(r.symbol AS VARCHAR) IN ({symbol_literals})
                )
                WHERE row_num = 1
            """
        else:
            query = f"""
                SELECT * EXCLUDE (row_num)
                FROM (
                    SELECT
                        r.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY CAST(r.symbol AS VARCHAR)
                            ORDER BY r.run_id DESC, r.row_number DESC
                        ) AS row_num
                    FROM src_daily.all_fields_rows AS r
                    WHERE CAST(r.symbol AS VARCHAR) IN ({symbol_literals})
                )
                WHERE row_num = 1
            """
        rows = _fetch_dict_rows(conn, query)
    finally:
        conn.close()

    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if symbol:
            result[symbol] = row
    return result


def classify_company_style(profile: Mapping[str, Any]) -> str:
    """
    Map company profile tags into a deterministic valuation style bucket.
    """
    tags = {
        part.strip()
        for part in str(profile.get("investment_style_profile") or "").split("|")
        if part.strip()
    }
    if "income_or_buyback_yield" in tags:
        return "income"
    if "value_reversion" in tags:
        return "value"
    if {
        "quality_compounder",
        "profitable_or_scaling_growth",
        "expensive_duration_growth",
    } & tags:
        return "growth"
    if "fragile_balance_sheet_or_quality" in tags:
        return "risk"
    return "balanced"


def select_active_lenses_for_company(
    *,
    company_style: str,
    lens_availability: Mapping[str, bool],
    max_active_lenses: int = MAX_ACTIVE_VALUATION_LENSES,
) -> list[str]:
    """
    Choose a company-type-aware subset of valuation lenses capped at max_active_lenses.
    """
    cap = int(max(0, max_active_lenses))
    if cap == 0:
        return []

    priority = STYLE_LENS_PRIORITY.get(company_style, STYLE_LENS_PRIORITY["balanced"])
    selected: list[str] = []
    for lens in priority:
        if lens_availability.get(lens) and lens not in selected:
            selected.append(lens)
        if len(selected) >= cap:
            return selected[:cap]

    # Backfill with robust anchors so sparse names still get a stable subset.
    for lens in ROBUST_BACKFILL_ORDER:
        if lens_availability.get(lens) and lens not in selected:
            selected.append(lens)
        if len(selected) >= cap:
            break
    return selected[:cap]


def _build_selected_horizon_weights(
    *,
    horizon: str,
    selected_lenses: Sequence[str],
) -> dict[str, float]:
    if not selected_lenses:
        return {}
    default_weights = DEFAULT_LENS_WEIGHTS.get(
        horizon,
        DEFAULT_LENS_WEIGHTS["medium_term"],
    )
    selected = {lens: float(default_weights.get(lens, 0.0)) for lens in selected_lenses}
    total = sum(weight for weight in selected.values() if weight > 0)
    if total <= 0:
        even_weight = 1.0 / float(len(selected_lenses))
        return {lens: even_weight for lens in selected_lenses}
    return {lens: weight / total for lens, weight in selected.items() if weight > 0}


def _normalize_upside_pct(
    value: float | None, *, full_score_pct: float = 80.0
) -> float:
    if value is None:
        return 0.0
    if full_score_pct <= 0:
        return 0.0
    return _clamp(float(value) / float(full_score_pct))


def _round_pct(value: float | None, digits: int = 2) -> float | str:
    if value is None:
        return ""
    return round(float(value), digits)


_VALUATION_UPSIDE_BLEND_WEIGHTS: dict[str, float] = {
    "near_term": 0.20,
    "medium_term": 0.25,
    "long_term": 0.55,
}


def _price_upside_pct(price: float | None, close: float | None) -> float | None:
    if price is None or close is None or close <= 0 or price <= 0:
        return None
    return ((float(price) / float(close)) - 1.0) * 100.0


def primary_valuation_upside_pct(
    *,
    near_upside: float | None,
    medium_upside: float | None,
    long_upside: float | None,
) -> float | None:
    """
    Headline valuation upside %: horizon-weighted blend of base-case fair-value
    upside (55% long / 25% medium / 20% near), re-normalized when horizons are
    missing.
    """
    parts = [
        (near_upside, _VALUATION_UPSIDE_BLEND_WEIGHTS["near_term"]),
        (medium_upside, _VALUATION_UPSIDE_BLEND_WEIGHTS["medium_term"]),
        (long_upside, _VALUATION_UPSIDE_BLEND_WEIGHTS["long_term"]),
    ]
    available = [(value, weight) for value, weight in parts if value is not None]
    if not available:
        return None
    total_weight = sum(weight for _, weight in available)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in available) / total_weight


def ranking_horizon_valuation_upside_pct(
    *,
    ranking_horizon: int,
    near_upside: float | None,
    medium_upside: float | None,
    long_upside: float | None,
) -> float | None:
    """Map the suite ranking horizon to the closest valuation horizon upside %."""
    horizon = int(ranking_horizon)
    if horizon <= 20:
        return near_upside
    if horizon <= 126:
        return medium_upside
    return long_upside


def _supporting_upside_signal(row: Mapping[str, Any], *, ranking_horizon: int) -> float:
    existing = _safe_float_or_none(row.get("upside_prediction_score"))
    if existing is not None:
        return _clamp(existing)
    computed = compute_upside_prediction_fields(
        row,
        ranking_horizon=int(ranking_horizon),
    )
    return _clamp(_safe_float(computed.get("upside_prediction_score")))


def _horizon_target_value(
    horizon_targets: Mapping[str, Any],
    horizon: str,
    field_name: str,
) -> Any:
    horizon_target = horizon_targets.get(horizon)
    if horizon_target is None:
        return None
    return getattr(horizon_target, field_name, None)


def _selected_lens_estimates(
    *,
    selected_lenses: Sequence[str],
    lens_estimates: Mapping[str, Sequence[Any]],
    lens_name: str,
) -> list[Any]:
    if lens_name not in selected_lenses:
        return []
    return list(lens_estimates.get(lens_name, []))


def _peer_group_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("sector") or "").strip(),
        str(row.get("industry") or "").strip(),
    )


def _build_grouped_peer_multiple_stats(
    peer_scan_rows: Sequence[Mapping[str, Any]],
) -> tuple[
    dict[str, dict[str, float | None]],
    dict[str, dict[str, dict[str, float | None]]],
    dict[tuple[str, str], dict[str, dict[str, float | None]]],
    dict[str, int],
    dict[tuple[str, str], int],
    int,
]:
    global_stats = _build_peer_multiple_stats([dict(row) for row in peer_scan_rows])
    sector_rows: dict[str, list[dict[str, Any]]] = {}
    industry_rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for source_row in peer_scan_rows:
        row = dict(source_row)
        sector, industry = _peer_group_key(row)
        if sector:
            sector_rows.setdefault(sector, []).append(row)
        if sector and industry:
            industry_rows.setdefault((sector, industry), []).append(row)

    sector_stats = {
        sector: _build_peer_multiple_stats(rows) for sector, rows in sector_rows.items()
    }
    industry_stats = {
        key: _build_peer_multiple_stats(rows) for key, rows in industry_rows.items()
    }
    sector_counts = {sector: len(rows) for sector, rows in sector_rows.items()}
    industry_counts = {key: len(rows) for key, rows in industry_rows.items()}
    return (
        global_stats,
        sector_stats,
        industry_stats,
        sector_counts,
        industry_counts,
        len(peer_scan_rows),
    )


def _resolve_peer_multiple_stats_for_row(
    row: Mapping[str, Any],
    *,
    global_stats: dict[str, dict[str, float | None]],
    sector_stats: Mapping[str, dict[str, dict[str, float | None]]],
    industry_stats: Mapping[tuple[str, str], dict[str, dict[str, float | None]]],
    sector_counts: Mapping[str, int],
    industry_counts: Mapping[tuple[str, str], int],
    global_count: int,
    min_peer_group_size: int = MIN_PEER_GROUP_SIZE,
) -> tuple[dict[str, dict[str, float | None]], str, int]:
    sector, industry = _peer_group_key(row)
    min_count = int(max(1, min_peer_group_size))

    if sector and industry:
        industry_key = (sector, industry)
        industry_count = int(industry_counts.get(industry_key, 0))
        if industry_count >= min_count and industry_key in industry_stats:
            return industry_stats[industry_key], "industry", industry_count

    if sector:
        sector_count = int(sector_counts.get(sector, 0))
        if sector_count >= min_count and sector in sector_stats:
            return sector_stats[sector], "sector", sector_count

    return global_stats, "global", int(global_count)


def build_forward_upside_valuation_rows(
    highlight_rows: Sequence[Mapping[str, Any]],
    *,
    source_rows_by_symbol: Mapping[str, Mapping[str, Any]],
    peer_source_rows: Sequence[Mapping[str, Any]] | None = None,
    ranking_horizon: int,
    max_active_lenses: int = MAX_ACTIVE_VALUATION_LENSES,
    min_valuation_lenses: int = MIN_USABLE_VALUATION_LENSES,
    scoring_profile: str | None = None,
) -> list[dict[str, Any]]:
    """
    Build forward-upside valuation rows using up to four adaptive valuation lenses.
    """
    if not highlight_rows:
        return []

    scan_rows: list[dict[str, Any]] = []
    for highlight_row in highlight_rows:
        row = _normalize_symbol_field(dict(highlight_row))
        symbol = str(row.get("symbol") or "").strip()
        source_row = dict(source_rows_by_symbol.get(symbol) or {})
        if source_row:
            row.update(source_row)
        scan_rows.append(row)

    peer_scan_rows = [
        _normalize_symbol_field(dict(row)) for row in (peer_source_rows or [])
    ]
    if not peer_scan_rows:
        peer_scan_rows = [dict(row) for row in scan_rows]

    _enrich_with_peer_metrics(peer_scan_rows)
    peer_rows_by_symbol = {
        str(row.get("symbol") or "").strip(): row
        for row in peer_scan_rows
        if str(row.get("symbol") or "").strip()
    }
    for row in scan_rows:
        symbol = str(row.get("symbol") or "").strip()
        peer_row = peer_rows_by_symbol.get(symbol)
        if peer_row is not None:
            row.update(peer_row)

    derived_rows = [_build_derived_metrics(row) for row in scan_rows]
    peer_derived_rows = [_build_derived_metrics(row) for row in peer_scan_rows]
    profiles = _build_metric_profiles(peer_scan_rows, peer_derived_rows)
    (
        global_peer_multiple_stats,
        sector_peer_multiple_stats,
        industry_peer_multiple_stats,
        sector_peer_counts,
        industry_peer_counts,
        global_peer_count,
    ) = _build_grouped_peer_multiple_stats(peer_scan_rows)
    resolved_profile = resolve_move_prediction_scoring_profile(scoring_profile)

    output_rows: list[dict[str, Any]] = []
    cap_lenses = int(max(1, max_active_lenses))
    min_lenses = int(max(1, min_valuation_lenses))

    for row, derived_row in zip(scan_rows, derived_rows):
        symbol = str(row.get("symbol") or "").strip()
        close = _safe_float_or_none(row.get("close"))
        if close is None or close <= 0:
            close = _safe_float_or_none(row.get("close_price"))
        supporting_signal = _supporting_upside_signal(
            row,
            ranking_horizon=int(ranking_horizon),
        )
        safety_signal = _clamp(
            _safe_float(row.get("safety_companion_score"), default=0.45)
        )
        confidence_signal = _clamp(_safe_float(row.get("confidence_score")))
        big_mover_signal = _clamp(_safe_float(row.get("big_mover_score")))

        forward_mode = "fallback"
        fallback_reason = "insufficient_valuation_lenses"
        company_style = "balanced"
        selected_lenses: list[str] = []
        available_lens_count = 0
        selected_coverage = 0.0
        valuation_signal = 0.0
        cost_of_equity: float | None = None
        horizon_targets: dict[str, Any] = {}
        near_upside: float | None = None
        medium_upside: float | None = None
        long_upside: float | None = None
        peer_scope = "global"
        peer_scope_size = int(global_peer_count)

        if close is None or close <= 0:
            fallback_reason = "missing_close_price"
        else:
            try:
                market_cap = _safe_float_or_none(row.get("market_cap_basic"))
                (
                    peer_multiple_stats,
                    peer_scope,
                    peer_scope_size,
                ) = _resolve_peer_multiple_stats_for_row(
                    row,
                    global_stats=global_peer_multiple_stats,
                    sector_stats=sector_peer_multiple_stats,
                    industry_stats=industry_peer_multiple_stats,
                    sector_counts=sector_peer_counts,
                    industry_counts=industry_peer_counts,
                    global_count=global_peer_count,
                )
                component_scores = _build_component_scores(
                    row,
                    derived_row,
                    profiles,
                    resolved_profile,
                )
                quality_flags = _build_quality_flags(row)
                company_profile = _build_company_profile(row, quality_flags)
                company_style = classify_company_style(company_profile)
                cost_of_equity = _cost_of_equity(row)

                lens_estimates: dict[str, Sequence[Any]] = {}
                multiple_estimates = _multiple_anchored_targets(
                    row,
                    peer_multiple_stats,
                    close,
                )
                technical_estimates, _ = _technical_anchored_targets(row, close)
                trajectory_estimates = _trajectory_targets(
                    row,
                    peer_multiple_stats,
                    close,
                    market_cap,
                    cost_of_equity,
                )
                range_estimates = _range_anchored_targets(row, close)
                analyst_estimates = _analyst_consensus_targets(row, close)
                book_value_estimates = _book_value_targets(
                    row,
                    peer_multiple_stats,
                    close,
                )
                yield_dcf_estimates = _yield_dcf_targets(row, close, cost_of_equity)

                lens_estimates["multiple"] = multiple_estimates
                lens_estimates["technical"] = technical_estimates
                lens_estimates["trajectory"] = trajectory_estimates
                lens_estimates["range"] = range_estimates
                lens_estimates["analyst"] = analyst_estimates
                lens_estimates["book_value"] = book_value_estimates
                lens_estimates["yield_dcf"] = yield_dcf_estimates

                availability = {
                    lens_name: bool(lens_estimates.get(lens_name))
                    for lens_name in LENS_ORDER
                }
                available_lens_count = sum(
                    1 for active in availability.values() if active
                )
                selected_lenses = select_active_lenses_for_company(
                    company_style=company_style,
                    lens_availability=availability,
                    max_active_lenses=cap_lenses,
                )
                selected_coverage = len(selected_lenses) / float(cap_lenses)

                for horizon in TARGET_HORIZONS:
                    weights = _build_selected_horizon_weights(
                        horizon=horizon,
                        selected_lenses=selected_lenses,
                    )
                    horizon_targets[horizon] = _blend_horizon_target(
                        horizon=horizon,
                        close=close,
                        multiple_estimates=_selected_lens_estimates(
                            selected_lenses=selected_lenses,
                            lens_estimates=lens_estimates,
                            lens_name="multiple",
                        ),
                        technical_estimates=_selected_lens_estimates(
                            selected_lenses=selected_lenses,
                            lens_estimates=lens_estimates,
                            lens_name="technical",
                        ),
                        trajectory_estimates=_selected_lens_estimates(
                            selected_lenses=selected_lenses,
                            lens_estimates=lens_estimates,
                            lens_name="trajectory",
                        ),
                        range_estimates=_selected_lens_estimates(
                            selected_lenses=selected_lenses,
                            lens_estimates=lens_estimates,
                            lens_name="range",
                        ),
                        analyst_estimates=_selected_lens_estimates(
                            selected_lenses=selected_lenses,
                            lens_estimates=lens_estimates,
                            lens_name="analyst",
                        ),
                        book_value_estimates=_selected_lens_estimates(
                            selected_lenses=selected_lenses,
                            lens_estimates=lens_estimates,
                            lens_name="book_value",
                        ),
                        yield_dcf_estimates=_selected_lens_estimates(
                            selected_lenses=selected_lenses,
                            lens_estimates=lens_estimates,
                            lens_name="yield_dcf",
                        ),
                        component_scores=component_scores,
                        quality_flags=quality_flags,
                        lens_weights=weights,
                    )

                near_upside = _safe_float_or_none(
                    _horizon_target_value(
                        horizon_targets,
                        "near_term",
                        "base_upside_pct",
                    )
                )
                medium_upside = _safe_float_or_none(
                    _horizon_target_value(
                        horizon_targets,
                        "medium_term",
                        "base_upside_pct",
                    )
                )
                long_upside = _safe_float_or_none(
                    _horizon_target_value(
                        horizon_targets,
                        "long_term",
                        "base_upside_pct",
                    )
                )

                valuation_signal = _clamp(
                    (0.55 * _normalize_upside_pct(long_upside))
                    + (0.25 * _normalize_upside_pct(medium_upside))
                    + (0.20 * _normalize_upside_pct(near_upside))
                )
                if len(selected_lenses) >= min_lenses and long_upside is not None:
                    forward_mode = "valuation"
                    fallback_reason = ""
                elif len(selected_lenses) < min_lenses:
                    fallback_reason = "insufficient_valuation_lenses"
                else:
                    fallback_reason = "no_long_term_target"
            except Exception:
                fallback_reason = "valuation_error"
                selected_lenses = []
                available_lens_count = 0
                selected_coverage = 0.0
                valuation_signal = 0.0
                horizon_targets = {}

        if forward_mode == "valuation":
            forward_score = _clamp(
                (0.55 * valuation_signal)
                + (0.15 * selected_coverage)
                + (0.15 * supporting_signal)
                + (0.15 * safety_signal)
            )
        else:
            forward_score = _clamp(
                (0.60 * supporting_signal)
                + (0.25 * confidence_signal)
                + (0.15 * big_mover_signal)
            )

        valuation_upside_pct = primary_valuation_upside_pct(
            near_upside=near_upside,
            medium_upside=medium_upside,
            long_upside=long_upside,
        )
        rank_horizon_valuation_upside_pct = ranking_horizon_valuation_upside_pct(
            ranking_horizon=int(ranking_horizon),
            near_upside=near_upside,
            medium_upside=medium_upside,
            long_upside=long_upside,
        )
        valuation_bear_upside_pct = _price_upside_pct(
            _safe_float_or_none(
                _horizon_target_value(horizon_targets, "long_term", "bear_price")
            ),
            close,
        )
        valuation_bull_upside_pct = _price_upside_pct(
            _safe_float_or_none(
                _horizon_target_value(horizon_targets, "long_term", "bull_price")
            ),
            close,
        )

        row_out = dict(row)
        row_out.update(
            {
                "symbol": symbol,
                "forward_upside_score": round(forward_score, 4),
                "forward_upside_mode": forward_mode,
                "forward_upside_fallback_reason": fallback_reason,
                "forward_valuation_upside_pct": _round_pct(valuation_upside_pct),
                "forward_rank_horizon_valuation_upside_pct": _round_pct(
                    rank_horizon_valuation_upside_pct
                ),
                "forward_valuation_bear_upside_pct": _round_pct(
                    valuation_bear_upside_pct
                ),
                "forward_valuation_bull_upside_pct": _round_pct(
                    valuation_bull_upside_pct
                ),
                "forward_company_style": company_style,
                "forward_selected_lenses": "|".join(selected_lenses),
                "forward_selected_lens_count": len(selected_lenses),
                "forward_peer_scope": peer_scope,
                "forward_peer_scope_size": int(peer_scope_size),
                "forward_available_lens_count": int(available_lens_count),
                "forward_selected_coverage": round(selected_coverage, 4),
                "forward_valuation_signal": round(valuation_signal, 4),
                "forward_fallback_signal": round(supporting_signal, 4),
                "forward_supporting_upside_prediction_score": round(
                    supporting_signal,
                    4,
                ),
                "forward_targets_cost_of_equity": _round_float(cost_of_equity),
                "forward_targets_blend_upside_cap_multiple": float(
                    MAX_UPSIDE_BLEND_MULTIPLE
                ),
                "forward_near_term_base_price": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(
                            horizon_targets,
                            "near_term",
                            "base_price",
                        )
                    )
                ),
                "forward_medium_term_base_price": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(
                            horizon_targets,
                            "medium_term",
                            "base_price",
                        )
                    )
                ),
                "forward_long_term_base_price": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(
                            horizon_targets,
                            "long_term",
                            "base_price",
                        )
                    )
                ),
                "forward_near_term_bear_price": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(
                            horizon_targets,
                            "near_term",
                            "bear_price",
                        )
                    )
                ),
                "forward_near_term_bull_price": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(
                            horizon_targets,
                            "near_term",
                            "bull_price",
                        )
                    )
                ),
                "forward_medium_term_bear_price": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(
                            horizon_targets,
                            "medium_term",
                            "bear_price",
                        )
                    )
                ),
                "forward_medium_term_bull_price": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(
                            horizon_targets,
                            "medium_term",
                            "bull_price",
                        )
                    )
                ),
                "forward_long_term_bear_price": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(
                            horizon_targets,
                            "long_term",
                            "bear_price",
                        )
                    )
                ),
                "forward_long_term_bull_price": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(
                            horizon_targets,
                            "long_term",
                            "bull_price",
                        )
                    )
                ),
                "forward_near_term_base_upside_pct": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(
                            horizon_targets,
                            "near_term",
                            "base_upside_pct",
                        )
                    )
                ),
                "forward_medium_term_base_upside_pct": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(
                            horizon_targets,
                            "medium_term",
                            "base_upside_pct",
                        )
                    )
                ),
                "forward_long_term_base_upside_pct": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(
                            horizon_targets,
                            "long_term",
                            "base_upside_pct",
                        )
                    )
                ),
                "forward_near_term_opportunity_score": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(
                            horizon_targets,
                            "near_term",
                            "opportunity_score",
                        )
                    )
                ),
                "forward_medium_term_opportunity_score": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(
                            horizon_targets,
                            "medium_term",
                            "opportunity_score",
                        )
                    )
                ),
                "forward_long_term_opportunity_score": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(
                            horizon_targets,
                            "long_term",
                            "opportunity_score",
                        )
                    )
                ),
                "forward_near_term_lens_coverage": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(horizon_targets, "near_term", "coverage")
                    )
                ),
                "forward_medium_term_lens_coverage": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(
                            horizon_targets,
                            "medium_term",
                            "coverage",
                        )
                    )
                ),
                "forward_long_term_lens_coverage": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(horizon_targets, "long_term", "coverage")
                    )
                ),
                "forward_long_term_lens_dispersion": _round_float(
                    _safe_float_or_none(
                        _horizon_target_value(
                            horizon_targets,
                            "long_term",
                            "lens_dispersion",
                        )
                    )
                ),
            }
        )
        output_rows.append(row_out)

    return output_rows


def forward_upside_valuation_primary_column_names(
    *,
    ranking_horizon: int,
) -> list[str]:
    horizon = int(ranking_horizon)
    return [
        "symbol",
        "bare_ticker",
        "company_name",
        "exchange",
        "country",
        "sector",
        "industry",
        "source_date",
        "lane_setup_name",
        "lane_group_by",
        "lane_group_value",
        "current_lane_flag",
        "forward_upside_rank",
        "forward_upside_score",
        "forward_upside_mode",
        "forward_upside_fallback_reason",
        "forward_valuation_upside_pct",
        "forward_rank_horizon_valuation_upside_pct",
        "forward_valuation_bear_upside_pct",
        "forward_valuation_bull_upside_pct",
        "forward_company_style",
        "forward_selected_lenses",
        "forward_selected_lens_count",
        "forward_peer_scope",
        "forward_peer_scope_size",
        "forward_available_lens_count",
        "forward_selected_coverage",
        "forward_valuation_signal",
        "forward_fallback_signal",
        "forward_targets_cost_of_equity",
        "forward_targets_blend_upside_cap_multiple",
        "forward_near_term_base_price",
        "forward_medium_term_base_price",
        "forward_long_term_base_price",
        "forward_near_term_bear_price",
        "forward_near_term_bull_price",
        "forward_medium_term_bear_price",
        "forward_medium_term_bull_price",
        "forward_long_term_bear_price",
        "forward_long_term_bull_price",
        "forward_near_term_base_upside_pct",
        "forward_medium_term_base_upside_pct",
        "forward_long_term_base_upside_pct",
        "forward_near_term_opportunity_score",
        "forward_medium_term_opportunity_score",
        "forward_long_term_opportunity_score",
        "forward_near_term_lens_coverage",
        "forward_medium_term_lens_coverage",
        "forward_long_term_lens_coverage",
        "forward_long_term_lens_dispersion",
        "forward_supporting_upside_prediction_score",
        "big_mover_score",
        "big_mover_rank",
        "confidence_score",
        "confidence_rank",
        "composite_score",
        f"median_fwd_{horizon}d_in_setup",
        f"win_rate_{horizon}d_in_setup",
        f"target_rate_{horizon}d_in_setup",
        f"lane_sample_count_{horizon}d",
        f"lane_median_fwd_{horizon}d",
        f"lane_win_rate_{horizon}d",
        "lane_context_score",
        "stability_score",
        "any_setup_rate",
    ]


def project_forward_upside_valuation_row(
    row: Mapping[str, Any],
    *,
    ranking_horizon: int,
    include_safety: bool,
) -> dict[str, Any]:
    primary_names = forward_upside_valuation_primary_column_names(
        ranking_horizon=ranking_horizon
    )
    trailing_names = (
        list(FORWARD_UPSIDE_VALUATION_SAFETY_TRAILING_COLUMNS) if include_safety else []
    )
    ordered_names = primary_names + trailing_names
    return {name: row.get(name, "") for name in ordered_names}


def extract_forward_upside_valuation_overlay_fields(
    row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project forward valuation return fields for companion overlays (e.g. safety lens)."""
    source = row or {}
    return {
        name: source.get(name, "") for name in FORWARD_UPSIDE_VALUATION_OVERLAY_COLUMNS
    }


def sort_rows_by_forward_upside_valuation(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    materialized = [dict(row) for row in rows]
    materialized.sort(
        key=lambda row: (
            _safe_float(row.get("forward_upside_score")),
            _safe_float(row.get("forward_valuation_upside_pct")),
            _safe_float(row.get("forward_long_term_base_upside_pct")),
            _safe_float(row.get("forward_supporting_upside_prediction_score")),
            _safe_float(row.get("big_mover_score")),
        ),
        reverse=True,
    )
    for index, row in enumerate(materialized, start=1):
        row["forward_upside_rank"] = index
    return materialized
