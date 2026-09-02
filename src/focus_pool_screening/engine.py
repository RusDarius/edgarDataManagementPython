"""Focus pool screening engine.

Deterministic pipeline:
  1. resolve sources (latest move-prediction run, all-fields snapshot,
     edge-research unified highlights)
  2. build the base universe from the prediction run raw scan rows
  3. enrich with all-fields columns, prediction overlays (consensus /
     profile / conviction) and edge-research unified highlights
  4. derive metrics, percentile-score configured fields, blend family
     scores, overlays and a composite focus score
  5. flag lane membership (top movers / value drops / ...) and persist a
     DuckDB focus map + human-readable log

Same inputs + same config => identical scores, ranks and lane membership.
"""

from __future__ import annotations

import functools
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from constants.trading_view_constants import PREFERRED_MARKETS
from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb

from .config import (
    SOURCE_ALL_FIELDS,
    FocusPoolConfig,
    LaneSpec,
    load_focus_pool_config,
)
from .discovery import ResolvedSources, resolve_sources
from .scoring import (
    DIRECTION_BAND,
    apply_valid_range,
    clip_value,
    coerce_float,
    evaluate_condition,
    percentile_scores,
    weighted_average,
    band_score,
)

IDENTITY_RAW_COLUMNS: dict[str, str] = {
    "company": "Company",
    "name": "name",
    "exchange": "exchange",
    "country": "country",
    "sector": "sector",
    "industry": "industry",
    "market": "market",
    "type": "type",
    "close": "close",
    "market_cap_basic": "market_cap_basic",
    "earnings_release_date": "earnings_release_date",
    "earnings_release_next_date": "earnings_release_next_date",
}

STRING_IDENTITY_KEYS = (
    "company",
    "name",
    "exchange",
    "country",
    "sector",
    "industry",
    "market",
    "type",
    "earnings_release_date",
    "earnings_release_next_date",
)

EDGE_OVERLAY_COLUMNS: dict[str, str] = {
    "edge_unified_score": "unified_edge_highlight_score",
    "edge_unified_rank": "unified_edge_highlight_rank",
    "edge_consensus_score": "unified_consensus_score",
    "edge_best_outlook_name": "unified_best_outlook_name",
    "edge_best_outlook_score": "unified_best_outlook_score",
    "edge_forward_valuation_upside_pct": "forward_valuation_upside_pct",
    "edge_historical_validation_bucket": "historical_validation_bucket",
    "edge_historical_validation_score": "historical_validation_score",
    "edge_upside_prediction_score": "upside_prediction_score",
    "edge_tradeable_safety_blend_score": "tradeable_safety_blend_score",
    "edge_safety_companion_score": "safety_companion_score",
    "edge_big_mover_score": "big_mover_score",
    "edge_confidence_score": "confidence_score",
}


def _safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _pct_vs(numerator: float | None, denominator: float | None) -> float | None:
    ratio = _safe_div(numerator, denominator)
    if ratio is None:
        return None
    return (ratio - 1.0) * 100.0


def _binary_above(close: float | None, reference: float | None) -> float | None:
    if close is None or reference is None:
        return None
    return 1.0 if close > reference else 0.0


def _dollar_traded(get: Callable[[str], float | None]) -> float | None:
    direct = get("AvgValue.Traded_10d")
    if direct is not None:
        return direct
    avg_volume = get("average_volume_10d_calc")
    close = get("close")
    if avg_volume is None or close is None:
        return None
    return avg_volume * close


def _fcf_yield(get: Callable[[str], float | None]) -> float | None:
    p_fcf = get("price_free_cash_flow_ttm")
    if p_fcf is None or p_fcf <= 0:
        return None
    return 100.0 / p_fcf


DERIVED_METRICS: dict[str, tuple[tuple[str, ...], Callable[[Callable[[str], float | None]], float | None]]] = {
    "pct_off_52w_high": (
        ("close", "price_52_week_high"),
        lambda get: _pct_vs(get("close"), get("price_52_week_high")),
    ),
    "pct_above_52w_low": (
        ("close", "price_52_week_low"),
        lambda get: _pct_vs(get("close"), get("price_52_week_low")),
    ),
    "price_target_upside_pct": (
        ("price_target_1y", "close"),
        lambda get: _pct_vs(get("price_target_1y"), get("close")),
    ),
    "macd_hist": (
        ("MACD.macd", "MACD.signal"),
        lambda get: (
            None
            if get("MACD.macd") is None or get("MACD.signal") is None
            else get("MACD.macd") - get("MACD.signal")
        ),
    ),
    "above_sma50": (
        ("close", "SMA50"),
        lambda get: _binary_above(get("close"), get("SMA50")),
    ),
    "above_sma200": (
        ("close", "SMA200"),
        lambda get: _binary_above(get("close"), get("SMA200")),
    ),
    "avg_dollar_traded_10d": (
        ("AvgValue.Traded_10d", "average_volume_10d_calc", "close"),
        _dollar_traded,
    ),
    "fcf_yield_ttm": (("price_free_cash_flow_ttm",), _fcf_yield),
}


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _describe_columns(database_path: Path, table_name: str) -> set[str]:
    rows = query_move_prediction_duckdb(
        str(database_path), f"DESCRIBE {_quote_ident(table_name)}"
    )
    return {str(row["column_name"]) for row in rows}


def _collect_required_tv_columns(config: FocusPoolConfig) -> dict[str, list[str]]:
    """TV columns needed per source, from field specs + derived dependencies."""
    needed: dict[str, set[str]] = {
        SOURCE_ALL_FIELDS: set(),
        "prediction_raw": set(),
    }
    derived_keys = set()
    for family in config.families:
        for spec in family.fields:
            if spec.column:
                for source in spec.sources:
                    if source in needed:
                        needed[source].add(spec.column)
            elif spec.derived:
                derived_keys.add(spec.derived)
    # universe liquidity filter uses the derived dollar-traded metric
    if config.universe.min_avg_dollar_traded_10d > 0:
        derived_keys.add("avg_dollar_traded_10d")
    for key in sorted(derived_keys):
        if key not in DERIVED_METRICS:
            raise ValueError(
                f"Unknown derived metric {key!r}. Available: {sorted(DERIVED_METRICS)}"
            )
        for dependency in DERIVED_METRICS[key][0]:
            # dependencies are fetched from both sources; coalesce prefers all_fields
            needed[SOURCE_ALL_FIELDS].add(dependency)
            needed["prediction_raw"].add(dependency)
    return {source: sorted(cols) for source, cols in needed.items()}


def _fetch_prediction_base_rows(
    config: FocusPoolConfig,
    sources: ResolvedSources,
    raw_columns: Sequence[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    db_path = sources.prediction_db
    assert db_path is not None and sources.prediction_run_id is not None
    available = _describe_columns(db_path, "raw_scan_rows")
    notes: list[str] = []

    select_parts = ['r."symbol" AS symbol']
    for out_key, raw_col in IDENTITY_RAW_COLUMNS.items():
        if raw_col in available:
            select_parts.append(f"r.{_quote_ident(raw_col)} AS {_quote_ident(out_key)}")
        else:
            select_parts.append(f"CAST(NULL AS VARCHAR) AS {_quote_ident(out_key)}")
            notes.append(f"prediction raw_scan_rows missing identity column {raw_col!r}")
    fetched_raw = []
    for col in raw_columns:
        if col in available:
            select_parts.append(f"r.{_quote_ident(col)} AS {_quote_ident(col)}")
            fetched_raw.append(col)
        else:
            notes.append(f"prediction raw_scan_rows missing column {col!r} (skipped)")

    where_parts = ["r.run_id = ?"]
    params: list[Any] = [sources.prediction_run_id]
    universe = config.universe
    if universe.min_market_cap_usd > 0 and "market_cap_basic" in available:
        where_parts.append(
            "TRY_CAST(r.market_cap_basic AS DOUBLE) >= "
            f"{float(universe.min_market_cap_usd)}"
        )
    if universe.max_market_cap_usd is not None and "market_cap_basic" in available:
        where_parts.append(
            "TRY_CAST(r.market_cap_basic AS DOUBLE) <= "
            f"{float(universe.max_market_cap_usd)}"
        )
    markets = universe.markets
    if markets is None:
        markets = tuple(PREFERRED_MARKETS)
    if markets and "market" in available:
        literals = ", ".join(_sql_literal(m) for m in markets)
        where_parts.append(f"lower(CAST(r.market AS VARCHAR)) IN ({literals})")

    sql = f"""
        SELECT {", ".join(select_parts)}
        FROM raw_scan_rows AS r
        WHERE {" AND ".join(where_parts)}
        ORDER BY r.row_number
    """
    rows = query_move_prediction_duckdb(str(db_path), sql, params)

    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol or symbol in deduped:
            continue
        row["symbol"] = symbol
        deduped[symbol] = row
    return list(deduped.values()), notes


def _fetch_prediction_overlays(
    config: FocusPoolConfig, sources: ResolvedSources
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    db_path = sources.prediction_db
    assert db_path is not None and sources.prediction_run_id is not None
    notes: list[str] = []
    overlay: dict[str, dict[str, Any]] = {}
    mp = config.sources.move_prediction
    horizon = mp.consensus_horizon

    consensus_rows = query_move_prediction_duckdb(
        str(db_path),
        """
        SELECT symbol, score, risk_adjusted_score, direction, confidence,
               agreement_ratio, manager_action_signal
        FROM consensus_horizon_scores
        WHERE run_id = ? AND horizon_name = ?
        """,
        [sources.prediction_run_id, horizon],
    )
    for row in consensus_rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        overlay.setdefault(symbol, {}).update(
            {
                "pred_consensus_score": coerce_float(row.get("score")),
                "pred_consensus_ras": coerce_float(row.get("risk_adjusted_score")),
                "pred_consensus_direction": row.get("direction"),
                "pred_consensus_confidence": coerce_float(row.get("confidence")),
                "pred_agreement_ratio": coerce_float(row.get("agreement_ratio")),
                "pred_manager_action_signal": row.get("manager_action_signal"),
            }
        )
    notes.append(
        f"consensus overlay rows: {len(consensus_rows)} (horizon={horizon})"
    )

    if mp.profile_columns:
        tables = {
            str(r["table_name"])
            for r in query_move_prediction_duckdb(
                str(db_path),
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'",
            )
        }
        if "profile_horizon_scores" in tables:
            literals = ", ".join(_sql_literal(p) for p in mp.profile_columns)
            profile_rows = query_move_prediction_duckdb(
                str(db_path),
                f"""
                SELECT symbol, profile_name, score, risk_adjusted_score
                FROM profile_horizon_scores
                WHERE run_id = ? AND horizon_name = ? AND profile_name IN ({literals})
                """,
                [sources.prediction_run_id, horizon],
            )
            for row in profile_rows:
                symbol = str(row.get("symbol") or "").strip().upper()
                profile = str(row.get("profile_name") or "").strip()
                if not symbol or not profile:
                    continue
                safe_profile = profile.replace("-", "_").replace(".", "_")
                overlay.setdefault(symbol, {})[
                    f"pred_profile_{safe_profile}_score"
                ] = coerce_float(row.get("score"))
                overlay[symbol][f"pred_profile_{safe_profile}_ras"] = coerce_float(
                    row.get("risk_adjusted_score")
                )
            notes.append(f"profile overlay rows: {len(profile_rows)}")

    if mp.include_conviction:
        tables = {
            str(r["table_name"])
            for r in query_move_prediction_duckdb(
                str(db_path),
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'",
            )
        }
        if "conviction_rankings" in tables:
            conviction_rows = query_move_prediction_duckdb(
                str(db_path),
                """
                SELECT symbol, conviction_score, rank_overall, entry_readiness, size_tier
                FROM conviction_rankings
                WHERE run_id = ?
                """,
                [sources.prediction_run_id],
            )
            for row in conviction_rows:
                symbol = str(row.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                overlay.setdefault(symbol, {}).update(
                    {
                        "pred_conviction_score": coerce_float(row.get("conviction_score")),
                        "pred_conviction_rank_overall": coerce_float(row.get("rank_overall")),
                        "pred_entry_readiness": row.get("entry_readiness"),
                        "pred_size_tier": row.get("size_tier"),
                    }
                )
            notes.append(f"conviction overlay rows: {len(conviction_rows)}")
        else:
            notes.append("conviction_rankings table absent; conviction overlay skipped")
    return overlay, notes


def _fetch_all_fields_rows(
    config: FocusPoolConfig,
    sources: ResolvedSources,
    columns: Sequence[str],
    symbols: Sequence[str],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if not symbols:
        return {}, ["empty base universe; all-fields fetch skipped"]
    db_path = sources.all_fields_db
    assert db_path is not None and sources.all_fields_run_id is not None
    available = _describe_columns(db_path, "all_fields_rows")
    notes: list[str] = []

    fetched = []
    select_parts = ['r."symbol" AS symbol']
    for col in columns:
        if col in available:
            select_parts.append(f"r.{_quote_ident(col)} AS {_quote_ident(col)}")
            fetched.append(col)
        else:
            notes.append(f"all_fields_rows missing column {col!r} (skipped)")

    symbol_literals = ", ".join(_sql_literal(s) for s in sorted(symbols))
    sql = f"""
        SELECT {", ".join(select_parts)}
        FROM all_fields_rows AS r
        WHERE r.run_id = ? AND r.symbol IN ({symbol_literals})
    """
    rows = query_move_prediction_duckdb(str(db_path), sql, [sources.all_fields_run_id])
    by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        payload = {col: row.get(col) for col in fetched}
        by_symbol[symbol] = payload
    notes.append(
        f"all_fields rows joined: {len(by_symbol)}/{len(symbols)} base symbols"
    )
    return by_symbol, notes


def _fetch_edge_overlay(
    sources: ResolvedSources,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if sources.edge_unified_db is None:
        return {}, []
    db_path = sources.edge_unified_db
    available = _describe_columns(db_path, "symbol_unified_highlights")
    notes: list[str] = []
    select_parts = ['"symbol" AS symbol']
    for out_key, col in EDGE_OVERLAY_COLUMNS.items():
        if col in available:
            select_parts.append(f"{_quote_ident(col)} AS {_quote_ident(out_key)}")
        else:
            notes.append(f"symbol_unified_highlights missing column {col!r} (skipped)")
    rows = query_move_prediction_duckdb(
        str(db_path), f"SELECT {', '.join(select_parts)} FROM symbol_unified_highlights"
    )
    by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        by_symbol[symbol] = {
            key: row.get(key) for key in EDGE_OVERLAY_COLUMNS if key in row
        }
    notes.append(f"edge unified highlight rows: {len(by_symbol)}")
    return by_symbol, notes


def _normalize_prediction_row(
    row: Mapping[str, Any], raw_columns: Sequence[str]
) -> tuple[dict[str, Any], dict[str, float | None]]:
    identity: dict[str, Any] = {"symbol": row["symbol"]}
    for key in IDENTITY_RAW_COLUMNS:
        value = row.get(key)
        if key in STRING_IDENTITY_KEYS:
            identity[key] = None if value is None else str(value).strip()
        else:
            identity[key] = coerce_float(value)
    raw_values = {col: coerce_float(row.get(col)) for col in raw_columns}
    return identity, raw_values


def _passes_post_merge_universe(
    config: FocusPoolConfig, metrics_view: Mapping[str, Any]
) -> bool:
    universe = config.universe
    if universe.min_close > 0:
        close = metrics_view.get("close")
        if close is None or close < universe.min_close:
            return False
    if universe.min_avg_dollar_traded_10d > 0:
        traded = metrics_view.get("avg_dollar_traded_10d")
        if traded is None or traded < universe.min_avg_dollar_traded_10d:
            return False
    return True


def _direction_sort_key(value: Any, direction: str) -> tuple[int, Any]:
    """None always sorts last; direction applied on the value."""
    if value is None:
        return (1, 0.0)
    if isinstance(value, str):
        return (0, value)
    numeric = float(value)
    return (0, numeric if direction == "asc" else -numeric)


def _lane_sort(rows: list[Mapping[str, Any]], lane: LaneSpec) -> list[Mapping[str, Any]]:
    def compare(a: Mapping[str, Any], b: Mapping[str, Any]) -> int:
        for order in lane.order_by:
            ka = _direction_sort_key(a.get(order.key), order.direction)
            kb = _direction_sort_key(b.get(order.key), order.direction)
            if ka != kb:
                return -1 if ka < kb else 1
        sa, sb = str(a.get("symbol") or ""), str(b.get("symbol") or "")
        return (sa > sb) - (sa < sb)

    return sorted(rows, key=functools.cmp_to_key(compare))


def _normalize_overlay(value: float | None, low: float, high: float) -> float | None:
    if value is None:
        return None
    clamped = min(max(value, low), high)
    return (clamped - low) / (high - low) * 100.0


def run_focus_pool_screening(
    config_path: str | Path | None = None,
    *,
    top_n_override: int | None = None,
    write_csv_override: bool | None = None,
) -> dict[str, Any]:
    """Run the full screening pipeline and persist all outputs."""
    from . import output

    config = load_focus_pool_config(config_path)
    sources = resolve_sources(config)
    notes: list[str] = list(sources.notes)

    if sources.prediction_db is None:
        raise ValueError(
            "sources.move_prediction must be enabled: the prediction run raw scan "
            "defines the screening universe."
        )

    required = _collect_required_tv_columns(config)

    base_rows, base_notes = _fetch_prediction_base_rows(
        config, sources, required["prediction_raw"]
    )
    notes.extend(base_notes)
    pred_overlay, overlay_notes = _fetch_prediction_overlays(config, sources)
    notes.extend(overlay_notes)

    base_symbols = sorted(str(row["symbol"]) for row in base_rows)
    all_fields_rows: dict[str, dict[str, Any]] = {}
    if config.sources.all_fields.enabled and sources.all_fields_db is not None:
        all_fields_rows, af_notes = _fetch_all_fields_rows(
            config, sources, required[SOURCE_ALL_FIELDS], base_symbols
        )
        notes.extend(af_notes)

    edge_overlay: dict[str, dict[str, Any]] = {}
    edge_enabled = config.sources.edge_research.enabled and sources.edge_unified_db is not None
    if edge_enabled:
        edge_overlay, edge_notes = _fetch_edge_overlay(sources)
        notes.extend(edge_notes)

    exclude_symbols = {s.strip().upper() for s in config.universe.exclude_symbols}

    # ── merge + derive ────────────────────────────────────────────────
    merged: list[dict[str, Any]] = []
    for row in base_rows:
        identity, pred_raw = _normalize_prediction_row(row, required["prediction_raw"])
        symbol = identity["symbol"]
        if symbol in exclude_symbols:
            continue
        af_raw = {
            col: coerce_float(value)
            for col, value in (all_fields_rows.get(symbol) or {}).items()
        }
        combined_raw = dict(pred_raw)
        combined_raw.update({k: v for k, v in af_raw.items() if v is not None})

        def get(col: str, _combined: Mapping[str, float | None] = combined_raw) -> float | None:
            return _combined.get(col)

        derived_values = {
            key: fn(get) for key, (_, fn) in DERIVED_METRICS.items()
        }
        merged.append(
            {
                "identity": identity,
                "pred_raw": pred_raw,
                "combined_raw": combined_raw,
                "derived": derived_values,
                "pred": pred_overlay.get(symbol) or {},
                "edge": edge_overlay.get(symbol) or {},
            }
        )

    universe_rows = [
        entry
        for entry in merged
        if _passes_post_merge_universe(
            config,
            {
                "close": entry["identity"].get("close"),
                "avg_dollar_traded_10d": entry["derived"].get("avg_dollar_traded_10d"),
            },
        )
    ]
    notes.append(
        f"universe: {len(base_rows)} base rows -> {len(universe_rows)} after filters"
    )

    # ── metric extraction per configured field ────────────────────────
    universe_size = len(universe_rows)
    field_notes: list[dict[str, Any]] = []
    field_values: dict[str, dict[str, float | None]] = {}
    for family in config.families:
        for spec in family.fields:
            values: dict[str, float | None] = {}
            source_hits = {source: 0 for source in spec.sources}
            for entry in universe_rows:
                symbol = entry["identity"]["symbol"]
                if spec.derived:
                    value = entry["derived"].get(spec.derived)
                else:
                    value = None
                    for source in spec.sources:
                        candidate = None
                        if source == SOURCE_ALL_FIELDS:
                            candidate = (all_fields_rows.get(symbol) or {}).get(spec.column)
                        elif source == "prediction_raw":
                            candidate = entry["pred_raw"].get(spec.column)
                        candidate = coerce_float(candidate)
                        if candidate is not None:
                            source_hits[source] += 1
                            value = candidate
                            break
                value = apply_valid_range(value, spec.valid_range)
                value = clip_value(value, spec.clip)
                values[symbol] = value
            non_null = sum(1 for v in values.values() if v is not None)
            fill = (non_null / universe_size) if universe_size else 0.0
            included = universe_size > 0 and fill >= spec.min_fill
            field_values[spec.key] = values
            field_notes.append(
                {
                    "key": spec.key,
                    "family": family.name,
                    "column": spec.column,
                    "derived": spec.derived,
                    "direction": spec.direction,
                    "weight": spec.weight,
                    "fill_rate": round(fill, 4),
                    "included": included,
                    "note": (
                        "included"
                        if included
                        else f"excluded: fill {fill:.1%} < min_fill {spec.min_fill:.0%}"
                    ),
                    "source_hits": source_hits,
                }
            )

    # ── per-field scoring ─────────────────────────────────────────────
    field_score_lookups: dict[str, dict[str, float | None]] = {}
    spec_by_key = {spec.key: spec for fam in config.families for spec in fam.fields}
    for note in field_notes:
        key = note["key"]
        spec = spec_by_key[key]
        if not note["included"]:
            field_score_lookups[key] = {symbol: None for symbol in field_values[key]}
            continue
        if spec.direction == DIRECTION_BAND:
            assert spec.band is not None
            field_score_lookups[key] = {
                symbol: band_score(value, spec.band[0], spec.band[1], spec.decay_span)
                for symbol, value in field_values[key].items()
            }
        else:
            field_score_lookups[key] = percentile_scores(
                field_values[key], direction=spec.direction
            )

    # ── family + composite scores ─────────────────────────────────────
    pred_weight = config.overlays.prediction.weight if sources.prediction_db else 0.0
    edge_weight = (
        config.overlays.edge.weight if edge_enabled and edge_overlay else 0.0
    )
    if config.sources.edge_research.enabled and not edge_overlay:
        notes.append("edge overlay empty/unavailable; weight redistributed")

    scored_rows: list[dict[str, Any]] = []
    for entry in universe_rows:
        symbol = entry["identity"]["symbol"]
        row: dict[str, Any] = dict(entry["identity"])
        for key, values in field_values.items():
            row[key] = values.get(symbol)
        for key, value in entry["derived"].items():
            row[key] = value
        row.update(entry["pred"])
        row.update(entry["edge"])

        included_keys = {n["key"] for n in field_notes if n["included"]}
        family_components: list[tuple[float, float | None]] = []
        for family in config.families:
            per_field = [
                (spec.weight, field_score_lookups[spec.key].get(symbol))
                for spec in family.fields
                if spec.key in included_keys
            ]
            family_score, family_coverage = weighted_average(per_field)
            row[f"{family.name}_score"] = family_score
            row[f"{family.name}_coverage"] = family_coverage
            family_components.append((family.weight, family_score))

        pred_component = _normalize_overlay(
            row.get("pred_consensus_score"),
            config.overlays.prediction.score_min,
            config.overlays.prediction.score_max,
        )
        edge_component_raw = coerce_float(row.get("edge_unified_score"))
        edge_component = (
            edge_component_raw * 100.0 if edge_component_raw is not None else None
        )
        row["prediction_component"] = pred_component if pred_weight > 0 else None
        row["edge_component"] = edge_component if edge_weight > 0 else None

        composite_components = list(family_components)
        if pred_weight > 0:
            composite_components.append((pred_weight, pred_component))
        if edge_weight > 0:
            composite_components.append((edge_weight, edge_component))
        focus_score, composite_coverage = weighted_average(composite_components)
        if composite_coverage < config.output.min_composite_coverage:
            focus_score = None
        row["focus_score"] = focus_score
        row["composite_coverage"] = composite_coverage
        scored_rows.append(row)

    # focus rank: score desc, None last, symbol tie-break
    rankable = sorted(
        scored_rows,
        key=lambda r: (
            0 if r["focus_score"] is not None else 1,
            -(r["focus_score"] or 0.0),
            str(r["symbol"]),
        ),
    )
    for index, row in enumerate(rankable, start=1):
        row["focus_rank"] = index if row["focus_score"] is not None else None

    # ── lanes ─────────────────────────────────────────────────────────
    lane_results: dict[str, dict[str, Any]] = {}
    for lane in config.lanes:
        flag_key = f"lane_{lane.lane_id}"
        rank_key = f"lane_{lane.lane_id}_rank"
        if not lane.enabled:
            for row in scored_rows:
                row[flag_key] = False
                row[rank_key] = None
            lane_results[lane.lane_id] = {"members": [], "lane": lane}
            continue
        members: list[Mapping[str, Any]] = []
        for row in scored_rows:
            is_member = bool(evaluate_condition(lane.conditions, row))
            row[flag_key] = is_member
            if is_member:
                members.append(row)
        ordered = _lane_sort(members, lane)
        for index, member in enumerate(ordered, start=1):
            member[rank_key] = index
        lane_results[lane.lane_id] = {"members": ordered, "lane": lane}

    for row in scored_rows:
        row["lanes"] = ",".join(
            lane.lane_id for lane in config.lanes if row.get(f"lane_{lane.lane_id}")
        )

    config_hash = hashlib.sha256(
        json.dumps(config.raw_payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]

    result = output.persist_outputs(
        config=config,
        sources=sources,
        rows=scored_rows,
        field_notes=field_notes,
        lane_results=lane_results,
        notes=notes,
        config_hash=config_hash,
        top_n_override=top_n_override,
        write_csv_override=write_csv_override,
    )
    return result
