"""Ticker-centric backward pattern scan over all-fields DuckDB snapshots.

Starter scope:
- Analyze one ticker across historical all-fields daily DuckDB snapshots.
- Quantify field trajectories (value, delta, pct delta, z-scores).
- Quantify same-day universe relative position (percentile / z-score).
- Relate field states/deltas to realized close forward returns.

This module is intentionally explainable and SQL/Python-native (no ML dependency).
"""

from __future__ import annotations

import csv
import json
import math
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Mapping, Sequence

from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
    ALL_FIELDS_SUITE_NAME,
    DEFAULT_ALL_FIELDS_ROOT,
    DEFAULT_FIELD_CATALOG_CSV,
    AllFieldsAnalysisConfig,
    classify_columns,
    discover_all_fields_daily_databases,
    inventory_all_fields_runs,
    load_field_catalog,
)
from db.trading_view_move_prediction_duckdb import (
    _quote_identifier,
    _quote_path_literal,
    open_move_prediction_duckdb_connection,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TICKER_PATTERN_SCAN_ROOT = (
    DEFAULT_ALL_FIELDS_ROOT / "ticker_pattern_scan" / "runs"
)
DEFAULT_TAXONOMY_PREDICTOR_CSV = (
    PROJECT_ROOT
    / "savedData"
    / "trading_view_field_taxonomy"
    / "variants"
    / "by_usage"
    / "predictor_eligible.csv"
)

DEFAULT_CONTEXT_FIELDS: tuple[str, ...] = (
    "close",
    "Perf.5D",
    "Perf.1M",
    "Perf.3M",
    "volume",
    "relative_volume_10d_calc",
    "market_cap_basic",
    "sector",
    "industry",
    "type",
    "is_primary",
)
DEFAULT_FORWARD_DAYS: tuple[int, ...] = (1, 5, 20)
DEFAULT_FIELD_BUCKET_QUOTAS: dict[str, int] = {
    "momentum": 40,
    "valuation": 40,
    "quality": 40,
    "catalyst": 30,
    "liquidity": 20,
}
TIMEFRAME_CLASS_RANK: dict[str, int] = {
    "daily": 0,
    "weekly": 1,
    "monthly": 2,
    "intraday": 9,
}
SCALE_FIELD_BASE_PATTERN = re.compile(
    r"^(Value\.Traded|AvgValue\.Traded|average_volume)",
    re.IGNORECASE,
)

CATALOG_NUMERIC_TYPES: frozenset[str] = frozenset(
    {
        "number",
        "percent",
        "integer",
        "int",
        "float",
        "double",
        "ratio",
        "currency",
        "price",
    }
)
CATALOG_NON_NUMERIC_TYPES: frozenset[str] = frozenset(
    {
        "text",
        "string",
        "bool",
        "boolean",
        "time",
        "time-yyyymmdd",
        "map",
        "set",
        "interface",
    }
)
TIER_RANK = {"promote": 5, "watch": 4, "neutral": 3, "demote": 2, "ignore": 1}

SNAPSHOT_BASE_COLUMNS: tuple[str, ...] = (
    "snapshot_index",
    "source_day_label",
    "run_id",
    "run_created_at_utc",
    "source_database_path",
    "ticker_requested",
    "ticker_bare",
    "matched_symbol",
    "close",
    "Perf.5D",
    "Perf.1M",
    "Perf.3M",
    "volume",
    "relative_volume_10d_calc",
    "market_cap_basic",
    "sector",
    "industry",
    "type",
    "is_primary",
    "scanned_field_count",
    "anomaly_field_count",
    "top_anomaly_fields",
    "top_anomaly_details_json",
)

FIELD_METRIC_COLUMNS: tuple[str, ...] = (
    "snapshot_index",
    "source_day_label",
    "run_id",
    "run_created_at_utc",
    "matched_symbol",
    "field_name",
    "field_raw_value",
    "field_value",
    "prior_field_value",
    "field_delta",
    "field_pct_delta",
    "history_mean",
    "history_stddev",
    "history_zscore",
    "delta_stddev",
    "delta_zscore",
    "universe_n",
    "universe_mean",
    "universe_stddev",
    "universe_median",
    "percentile_rank",
    "ticker_universe_zscore",
    "event_score",
    "event_flags_json",
    "field_available_in_database",
)

EVENT_COLUMNS: tuple[str, ...] = (
    "snapshot_index",
    "source_day_label",
    "run_id",
    "run_created_at_utc",
    "matched_symbol",
    "field_name",
    "field_value",
    "prior_field_value",
    "field_delta",
    "field_pct_delta",
    "history_zscore",
    "delta_zscore",
    "percentile_rank",
    "ticker_universe_zscore",
    "event_score",
    "event_flags_json",
)

FORWARD_SUMMARY_COLUMNS: tuple[str, ...] = (
    "field_name",
    "horizon_days",
    "sample_n",
    "meets_min_sample",
    "field_value_corr",
    "field_delta_corr",
    "low_quartile_avg_return",
    "high_quartile_avg_return",
    "quartile_return_spread",
    "return_stddev",
    "directional_hit_rate",
    "pattern_score",
    "warnings_json",
)


@dataclass(frozen=True)
class TickerPatternScanConfig:
    ticker: str
    start_day_label: str | None = None
    end_day_label: str | None = None
    all_fields_root: Path = DEFAULT_ALL_FIELDS_ROOT
    output_root: Path = DEFAULT_TICKER_PATTERN_SCAN_ROOT
    field_catalog_csv: Path = DEFAULT_FIELD_CATALOG_CSV
    taxonomy_predictor_csv: Path | None = DEFAULT_TAXONOMY_PREDICTOR_CSV
    min_market_cap_usd: float | None = None
    max_fields: int = 250
    forward_days: tuple[int, ...] = DEFAULT_FORWARD_DAYS
    min_scan_data_count: int = 0
    include_stock_only_universe: bool = True
    include_primary_only_universe: bool = True
    constrain_to_sector: bool = False
    constrain_to_industry: bool = True
    additional_peer_industries: tuple[str, ...] = ()
    min_event_score: float = 2.0
    min_samples_for_association: int = 8
    field_bucket_quotas: dict[str, int] | None = None
    prefer_intraday: bool = False
    requested_forward_days: tuple[int, ...] = ()


@dataclass(frozen=True)
class ResolvedAllFieldsRun:
    day_label: str
    database_path: Path
    run_id: str
    created_at_utc: datetime
    run_label: str
    scan_data_count: int


@dataclass(frozen=True)
class FieldChangeEvent:
    snapshot_index: int
    source_day_label: str
    run_id: str
    run_created_at_utc: str
    matched_symbol: str
    field_name: str
    field_value: float | None
    prior_field_value: float | None
    field_delta: float | None
    field_pct_delta: float | None
    history_zscore: float | None
    delta_zscore: float | None
    percentile_rank: float | None
    ticker_universe_zscore: float | None
    event_score: float
    event_flags_json: str


@dataclass(frozen=True)
class FieldForwardAssociation:
    field_name: str
    horizon_days: int
    sample_n: int
    meets_min_sample: bool
    field_value_corr: float | None
    field_delta_corr: float | None
    low_quartile_avg_return: float | None
    high_quartile_avg_return: float | None
    quartile_return_spread: float | None
    return_stddev: float | None
    directional_hit_rate: float | None
    pattern_score: float | None
    warnings_json: str


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_symbol(symbol: str) -> str:
    normalized = _normalize_text(symbol).upper()
    if ":" in normalized:
        normalized = normalized.split(":", 1)[1]
    return normalized


def _quote_sql_literal(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _numeric_sql_expression(column_sql: str) -> str:
    return (
        "TRY_CAST(REPLACE(TRIM(CAST("
        + column_sql
        + " AS VARCHAR)), ',', '') AS DOUBLE)"
    )


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"none", "null", "nan", "n/a", "na", "-"}:
        return None
    if text.endswith("%"):
        text = text[:-1].strip()
    text = text.replace(",", "")
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _coerce_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _coerce_datetime_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = _normalize_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).isoformat()


def _extract_day_label(database_path: Path) -> str:
    parent_label = database_path.parent.name
    if _try_parse_day_label(parent_label) is not None:
        return parent_label
    stem = database_path.stem
    marker = "tradingview_all_fields_"
    if stem.startswith(marker):
        maybe_label = stem[len(marker) :]
        if _try_parse_day_label(maybe_label) is not None:
            return maybe_label
    return parent_label or stem


def _try_parse_day_label(day_label: str) -> datetime | None:
    normalized = _normalize_text(day_label)
    if not normalized:
        return None
    try:
        parsed = datetime.strptime(normalized, "%d_%m_%Y")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _build_scan_id(prefix: str = "ticker_pattern_scan") -> str:
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M")
    return f"{prefix}_{timestamp}_utc_{uuid.uuid4().hex[:8]}"


def _forward_return_column_key(horizon: int) -> str:
    return f"close_forward_return_{int(horizon)}td"


def _forward_snapshot_index_column_key(horizon: int) -> str:
    return f"forward_snapshot_index_{int(horizon)}td"


def _forward_trading_day_gap_column_key(horizon: int) -> str:
    return f"forward_trading_day_gap_{int(horizon)}td"


def _advance_weekdays(start: datetime, trading_days: int) -> datetime:
    if trading_days <= 0:
        return start
    current = start
    remaining = int(trading_days)
    while remaining > 0:
        current = current + timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def _count_weekdays_between(start: datetime, end: datetime) -> int:
    if end <= start:
        return 0
    count = 0
    current = start
    while current < end:
        current = current + timedelta(days=1)
        if current.weekday() < 5:
            count += 1
    return count


def _snapshot_trading_dates(snapshot_rows: Sequence[Mapping[str, Any]]) -> list[datetime]:
    dates: list[datetime] = []
    for row in snapshot_rows:
        parsed = _try_parse_day_label(_normalize_text(row.get("source_day_label")))
        if parsed is None:
            parsed = _coerce_datetime_utc(row.get("run_created_at_utc"))
        if parsed is None:
            raise ValueError(
                "Unable to parse trading date for snapshot "
                f"{_normalize_text(row.get('source_day_label'))}."
            )
        dates.append(parsed)
    return dates


def _compute_trading_day_span(snapshot_dates: Sequence[datetime]) -> int:
    if len(snapshot_dates) < 2:
        return 0
    return _count_weekdays_between(snapshot_dates[0], snapshot_dates[-1])


def _max_allowed_forward_horizons(trading_day_span: int) -> tuple[int, ...]:
    if trading_day_span < 5:
        return (1, 3)
    if trading_day_span < 20:
        return (1, 5)
    if trading_day_span < 40:
        return (1, 5, 10)
    return (1, 5, 10, 20)


def _resolve_effective_forward_days(
    requested: Sequence[int],
    *,
    snapshot_count: int,
    trading_day_span: int,
) -> tuple[int, ...]:
    if snapshot_count < 2:
        raise ValueError(
            "At least two matched snapshots are required to compute forward returns."
        )
    allowed = _max_allowed_forward_horizons(trading_day_span)
    requested_set = {int(day) for day in requested if int(day) > 0}
    allowed_set = set(allowed)
    effective = tuple(day for day in allowed if day in requested_set)
    if not effective:
        raise ValueError(
            "No forward_days remain after capping to available trading-day span "
            f"(requested={sorted(requested_set)}, allowed={allowed}, "
            f"snapshot_count={snapshot_count}, trading_day_span={trading_day_span})."
        )
    return effective


def _find_forward_snapshot_index(
    snapshot_dates: Sequence[datetime],
    from_index: int,
    trading_days: int,
) -> int | None:
    if from_index >= len(snapshot_dates) - 1:
        return None
    target_date = _advance_weekdays(snapshot_dates[from_index], int(trading_days)).date()
    for index in range(from_index + 1, len(snapshot_dates)):
        if snapshot_dates[index].date() >= target_date:
            return index
    return None


def _is_scale_field(field_name: str) -> bool:
    base_name = field_name.split("|", 1)[0]
    return bool(SCALE_FIELD_BASE_PATTERN.match(base_name))


def _timeframe_suffix_rank(timeframe: str) -> int:
    if not timeframe:
        return 0
    if timeframe == "1W":
        return 1
    if timeframe == "1M":
        return 2
    return 9


def _field_variant_sort_key(
    meta: Mapping[str, Any],
    *,
    prefer_intraday: bool,
) -> tuple[int, int, int, float, str]:
    timeframe_class = _normalize_text(meta.get("timeframe_class")).lower()
    class_rank = TIMEFRAME_CLASS_RANK.get(timeframe_class, 5)
    if prefer_intraday and timeframe_class == "intraday":
        class_rank = 0
    timeframe_rank = _timeframe_suffix_rank(_normalize_text(meta.get("timeframe")))
    tier_rank = -int(meta.get("tier_rank") or 0)
    fill_rate = -float(meta.get("fill_rate") or 0.0)
    field_name = str(meta.get("field_name") or "")
    return (class_rank, timeframe_rank, tier_rank, fill_rate, field_name)


def _map_field_to_selection_bucket(meta: Mapping[str, Any]) -> str:
    semantic_bucket = _normalize_text(meta.get("semantic_bucket")).lower()
    if semantic_bucket == "volume_liquidity":
        return "liquidity"
    components = {
        part.strip().lower()
        for part in _normalize_text(meta.get("move_prediction_component")).split("|")
        if part.strip()
    }
    if components & {"momentum", "trend"}:
        return "momentum"
    if "valuation" in components:
        return "valuation"
    if components & {"quality", "safety"}:
        return "quality"
    if components & {"event", "attention"}:
        return "catalyst"
    if semantic_bucket:
        return "momentum"
    return "momentum"


def _fetch_dict_rows(
    conn: Any,
    sql: str,
    parameters: Sequence[Any] | None = None,
) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, list(parameters or []))
    if cursor.description is None:
        return []
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _resolve_all_fields_runs(config: TickerPatternScanConfig) -> list[ResolvedAllFieldsRun]:
    databases = discover_all_fields_daily_databases(
        all_fields_root=config.all_fields_root,
        start_day_label=config.start_day_label,
        end_day_label=config.end_day_label,
    )
    if not databases:
        raise FileNotFoundError(
            "No all-fields DuckDB files found for the requested range under "
            f"{config.all_fields_root.as_posix()}."
        )

    resolved: list[ResolvedAllFieldsRun] = []
    for database_path in databases:
        inventory = inventory_all_fields_runs(database_path)
        if not inventory:
            continue
        selected = None
        for row in inventory:
            if int(row.scan_data_count) < int(config.min_scan_data_count):
                continue
            selected = row
            break
        if selected is None:
            continue
        day_label = _extract_day_label(database_path)
        fallback_dt = _try_parse_day_label(day_label)
        created_at_utc = (
            selected.created_at_utc
            or fallback_dt
            or datetime.now(tz=timezone.utc).replace(second=0, microsecond=0)
        )
        resolved.append(
            ResolvedAllFieldsRun(
                day_label=day_label,
                database_path=Path(database_path),
                run_id=str(selected.run_id),
                created_at_utc=created_at_utc,
                run_label=str(selected.run_label or ""),
                scan_data_count=int(selected.scan_data_count or 0),
            )
        )

    resolved.sort(
        key=lambda row: (
            row.created_at_utc,
            row.day_label,
            row.run_id,
            row.database_path.as_posix(),
        )
    )
    if not resolved:
        raise ValueError(
            "No all-fields runs met the selection criteria "
            f"(min_scan_data_count={config.min_scan_data_count})."
        )
    return resolved


def _read_taxonomy_metadata(
    taxonomy_predictor_csv: Path | None,
) -> tuple[dict[str, dict[str, Any]], list[str], dict[str, Any]]:
    if taxonomy_predictor_csv is None:
        return {}, [], {"status": "disabled", "path": None}
    if not taxonomy_predictor_csv.exists():
        return {}, [], {
            "status": "missing",
            "path": taxonomy_predictor_csv.as_posix(),
        }

    with taxonomy_predictor_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    metadata_by_field: dict[str, dict[str, Any]] = {}
    parsed_rows: list[dict[str, Any]] = []
    for row in rows:
        field_name = _normalize_text(row.get("field_name"))
        if not field_name:
            continue
        base_name = _normalize_text(row.get("base_name")) or field_name.split("|", 1)[0]
        meta = {
            "field_name": field_name,
            "base_name": base_name,
            "semantic_bucket": _normalize_text(row.get("semantic_bucket")),
            "move_prediction_component": _normalize_text(
                row.get("move_prediction_component")
            ),
            "timeframe_class": _normalize_text(row.get("timeframe_class")),
            "timeframe": _normalize_text(row.get("timeframe")),
            "is_timeframe_variant": _normalize_text(
                row.get("is_timeframe_variant")
            ).lower()
            in {"true", "1", "yes"},
            "tier_rank": TIER_RANK.get(
                _normalize_text(row.get("relevance_tier")).lower(), 0
            ),
            "relevance_score_abs": abs(
                _coerce_float(row.get("composite_relevance_score")) or 0.0
            ),
            "fill_rate": _coerce_float(row.get("predictor_fill_rate")) or 0.0,
        }
        metadata_by_field[field_name] = meta
        parsed_rows.append(meta)

    parsed_rows.sort(
        key=lambda item: (
            -int(item["tier_rank"]),
            -float(item["relevance_score_abs"]),
            -float(item["fill_rate"]),
            str(item["field_name"]),
        )
    )

    ordered = [str(item["field_name"]) for item in parsed_rows]
    return metadata_by_field, ordered, {
        "status": "loaded",
        "path": taxonomy_predictor_csv.as_posix(),
        "field_count": len(ordered),
    }


def _read_taxonomy_predictor_order(
    taxonomy_predictor_csv: Path | None,
) -> tuple[list[str], dict[str, Any]]:
    _, ordered, meta = _read_taxonomy_metadata(taxonomy_predictor_csv)
    return ordered, meta


def _select_fields_with_bucket_quotas(
    ranked_fields: Sequence[str],
    metadata_by_field: Mapping[str, Mapping[str, Any]],
    *,
    max_fields: int,
    bucket_quotas: Mapping[str, int],
) -> tuple[list[str], dict[str, int]]:
    selected: list[str] = []
    bucket_counts: dict[str, int] = {bucket: 0 for bucket in bucket_quotas}
    seen: set[str] = set()

    bucket_order = sorted(
        bucket_quotas.keys(),
        key=lambda bucket: (-int(bucket_quotas[bucket]), bucket),
    )

    while len(selected) < max_fields:
        progressed = False
        for bucket in bucket_order:
            quota = int(bucket_quotas[bucket])
            if bucket_counts[bucket] >= quota:
                continue
            for field_name in ranked_fields:
                if field_name in seen:
                    continue
                meta = metadata_by_field.get(field_name, {})
                if _map_field_to_selection_bucket(meta) != bucket:
                    continue
                selected.append(field_name)
                seen.add(field_name)
                bucket_counts[bucket] += 1
                progressed = True
                break
            if len(selected) >= max_fields:
                break
        if not progressed:
            break

    for field_name in ranked_fields:
        if len(selected) >= max_fields:
            break
        if field_name in seen:
            continue
        selected.append(field_name)
        seen.add(field_name)
        meta = metadata_by_field.get(field_name, {})
        bucket = _map_field_to_selection_bucket(meta)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

    return selected, bucket_counts


def _is_numeric_candidate_from_catalog(catalog_entry: Any) -> bool:
    if catalog_entry is None:
        return True
    field_type = _normalize_text(getattr(catalog_entry, "field_type", "")).lower()
    if not field_type:
        return True
    if field_type in CATALOG_NUMERIC_TYPES:
        return True
    if field_type in CATALOG_NON_NUMERIC_TYPES:
        return False
    return True


def _resolve_candidate_fields(
    *,
    all_columns: Sequence[str],
    config: TickerPatternScanConfig,
) -> tuple[list[str], list[str], dict[str, Any]]:
    catalog = load_field_catalog(config.field_catalog_csv)
    predictor_fields, _ = classify_columns(
        all_fields_columns=all_columns,
        catalog=catalog,
        config=AllFieldsAnalysisConfig(),
    )
    predictor_set = set(predictor_fields)
    available_columns = set(all_columns)

    metadata_by_field, taxonomy_order, taxonomy_meta = _read_taxonomy_metadata(
        config.taxonomy_predictor_csv
    )
    eligible_fields: list[str] = []
    seen: set[str] = set()

    def _consider_field(field_name: str) -> None:
        if field_name not in available_columns:
            return
        if field_name not in predictor_set:
            return
        if not _is_numeric_candidate_from_catalog(catalog.get(field_name)):
            return
        if field_name in seen:
            return
        seen.add(field_name)
        eligible_fields.append(field_name)

    if taxonomy_order:
        for field_name in taxonomy_order:
            _consider_field(field_name)
    for field_name in predictor_fields:
        _consider_field(field_name)

    families: dict[str, list[str]] = {}
    for field_name in eligible_fields:
        meta = metadata_by_field.get(field_name, {})
        base_name = _normalize_text(meta.get("base_name")) or field_name.split("|", 1)[0]
        families.setdefault(base_name, []).append(field_name)

    family_representatives: list[str] = []
    for base_name in sorted(families):
        variants = families[base_name]
        ranked_variants = sorted(
            variants,
            key=lambda name: _field_variant_sort_key(
                metadata_by_field.get(
                    name,
                    {
                        "field_name": name,
                        "base_name": base_name,
                        "timeframe_class": "",
                        "timeframe": name.split("|", 1)[1]
                        if "|" in name
                        else "",
                        "tier_rank": 0,
                        "fill_rate": 0.0,
                    },
                ),
                prefer_intraday=config.prefer_intraday,
            ),
        )
        family_representatives.append(ranked_variants[0])

    rank_index = {name: index for index, name in enumerate(eligible_fields)}
    family_representatives.sort(key=lambda name: rank_index.get(name, 10**9))

    bucket_quotas = dict(config.field_bucket_quotas or DEFAULT_FIELD_BUCKET_QUOTAS)
    limited_candidates, bucket_counts = _select_fields_with_bucket_quotas(
        family_representatives,
        metadata_by_field,
        max_fields=max(1, int(config.max_fields)),
        bucket_quotas=bucket_quotas,
    )
    context_fields = [field for field in DEFAULT_CONTEXT_FIELDS if field in available_columns]
    selection_meta = {
        "catalog_path": config.field_catalog_csv.as_posix(),
        "taxonomy": taxonomy_meta,
        "predictor_fields_total": len(predictor_fields),
        "eligible_field_count": len(eligible_fields),
        "family_representative_count": len(family_representatives),
        "field_bucket_quotas": bucket_quotas,
        "field_bucket_selected_counts": bucket_counts,
        "prefer_intraday": config.prefer_intraday,
        "selected_candidate_field_count": len(limited_candidates),
        "selected_context_field_count": len(context_fields),
        "selected_candidate_fields": limited_candidates,
        "selected_context_fields": context_fields,
    }
    return limited_candidates, context_fields, selection_meta


def _build_ticker_match_where() -> str:
    symbol_value_sql = "CAST(symbol AS VARCHAR)"
    symbol_bare_sql = (
        "COALESCE("
        f"NULLIF(split_part({symbol_value_sql}, ':', 2), ''), "
        f"{symbol_value_sql})"
    )
    return (
        f"(UPPER({symbol_value_sql}) = ? "
        f"OR UPPER({symbol_bare_sql}) = ?)"
    )


def _build_universe_filter_sql(
    *,
    available_columns: set[str],
    config: TickerPatternScanConfig,
    ticker_row: Mapping[str, Any],
) -> str:
    clauses: list[str] = []

    if config.include_stock_only_universe and "type" in available_columns:
        clauses.append(
            f"LOWER(TRIM(CAST({_quote_identifier('type')} AS VARCHAR))) = 'stock'"
        )
    if config.include_primary_only_universe and "is_primary" in available_columns:
        clauses.append(
            f"LOWER(TRIM(CAST({_quote_identifier('is_primary')} AS VARCHAR))) "
            "IN ('true', '1', 'yes', 'y', 't')"
        )
    if config.min_market_cap_usd is not None and "market_cap_basic" in available_columns:
        market_cap_expr = _numeric_sql_expression(_quote_identifier("market_cap_basic"))
        clauses.append(f"{market_cap_expr} >= {float(config.min_market_cap_usd)}")
    if config.constrain_to_sector and not config.constrain_to_industry and "sector" in available_columns:
        sector_value = _normalize_text(ticker_row.get("sector"))
        if sector_value:
            clauses.append(
                f"TRIM(CAST({_quote_identifier('sector')} AS VARCHAR)) "
                f"= {_quote_sql_literal(sector_value)}"
            )
    if config.constrain_to_industry and "industry" in available_columns:
        industry_values: list[str] = []
        ticker_industry = _normalize_text(ticker_row.get("industry"))
        if ticker_industry:
            industry_values.append(ticker_industry)
        for industry in config.additional_peer_industries:
            normalized = _normalize_text(industry)
            if normalized and normalized not in industry_values:
                industry_values.append(normalized)
        if industry_values:
            literals = ", ".join(_quote_sql_literal(value) for value in industry_values)
            clauses.append(
                f"TRIM(CAST({_quote_identifier('industry')} AS VARCHAR)) IN ({literals})"
            )

    if not clauses:
        return ""
    return " AND " + " AND ".join(clauses)


def _fetch_ticker_row(
    conn: Any,
    *,
    run_id: str,
    ticker_requested_upper: str,
    ticker_bare_upper: str,
    selected_columns: Sequence[str],
) -> dict[str, Any] | None:
    sql_columns = ", ".join(_quote_identifier(column) for column in selected_columns)
    sql = (
        "SELECT symbol"
        + (", " + sql_columns if sql_columns else "")
        + " FROM all_fields_rows "
        "WHERE run_id = ? AND "
        + _build_ticker_match_where()
        + " ORDER BY CASE WHEN UPPER(CAST(symbol AS VARCHAR)) = ? THEN 0 ELSE 1 END, row_number "
        "LIMIT 1"
    )
    rows = _fetch_dict_rows(
        conn,
        sql,
        [run_id, ticker_requested_upper, ticker_bare_upper, ticker_requested_upper],
    )
    return rows[0] if rows else None


def _fetch_available_columns(conn: Any, table_name: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = ?
        ORDER BY ordinal_position
        """,
        [table_name],
    ).fetchall()
    return [str(row[0]) for row in rows]


def _fetch_universe_field_stats(
    conn: Any,
    *,
    run_id: str,
    ticker_bare_upper: str,
    fields: Sequence[str],
    universe_filter_sql: str,
) -> dict[str, dict[str, Any]]:
    if not fields:
        return {}

    select_numeric: list[str] = []
    long_unions: list[str] = []
    for index, field_name in enumerate(fields):
        alias = f"field_{index:04d}"
        select_numeric.append(
            f"{_numeric_sql_expression(_quote_identifier(field_name))} AS "
            f"{_quote_identifier(alias)}"
        )
        long_unions.append(
            "SELECT symbol_key, "
            + _quote_sql_literal(field_name)
            + " AS field_name, "
            + _quote_identifier(alias)
            + " AS field_value FROM base"
        )

    symbol_key_expr = (
        "UPPER(COALESCE("
        "NULLIF(split_part(CAST(symbol AS VARCHAR), ':', 2), ''), "
        "CAST(symbol AS VARCHAR)"
        "))"
    )

    sql = f"""
    WITH base AS (
        SELECT {symbol_key_expr} AS symbol_key,
            {", ".join(select_numeric)}
        FROM all_fields_rows
        WHERE run_id = ?{universe_filter_sql}
    ),
    long_values AS (
        {" UNION ALL ".join(long_unions)}
    ),
    ticker_values AS (
        SELECT field_name, MAX(field_value) AS ticker_value
        FROM long_values
        WHERE symbol_key = {_quote_sql_literal(ticker_bare_upper)}
        GROUP BY field_name
    ),
    distribution AS (
        SELECT field_name,
            COUNT(field_value) AS universe_n,
            AVG(field_value) AS universe_mean,
            STDDEV_SAMP(field_value) AS universe_stddev,
            MEDIAN(field_value) AS universe_median
        FROM long_values
        WHERE field_value IS NOT NULL
        GROUP BY field_name
    ),
    percentiles AS (
        SELECT d.field_name,
            CASE
                WHEN tv.ticker_value IS NULL OR d.universe_n = 0 THEN NULL
                ELSE SUM(
                    CASE
                        WHEN lv.field_value <= tv.ticker_value THEN 1
                        ELSE 0
                    END
                )::DOUBLE / NULLIF(CAST(d.universe_n AS DOUBLE), 0.0)
            END AS percentile_rank
        FROM distribution d
        LEFT JOIN ticker_values tv ON tv.field_name = d.field_name
        LEFT JOIN long_values lv
            ON lv.field_name = d.field_name
           AND lv.field_value IS NOT NULL
        GROUP BY d.field_name, tv.ticker_value, d.universe_n
    )
    SELECT d.field_name,
        tv.ticker_value,
        d.universe_n,
        d.universe_mean,
        d.universe_stddev,
        d.universe_median,
        p.percentile_rank,
        CASE
            WHEN tv.ticker_value IS NULL OR d.universe_stddev IS NULL OR d.universe_stddev = 0
                THEN NULL
            ELSE (tv.ticker_value - d.universe_mean) / d.universe_stddev
        END AS ticker_universe_zscore
    FROM distribution d
    LEFT JOIN ticker_values tv ON tv.field_name = d.field_name
    LEFT JOIN percentiles p ON p.field_name = d.field_name
    ORDER BY d.field_name
    """
    rows = _fetch_dict_rows(conn, sql, [run_id])
    return {str(row["field_name"]): row for row in rows}


def _safe_mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return float(mean(values))


def _safe_stddev(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    avg = _safe_mean(values)
    if avg is None:
        return None
    variance = sum((value - avg) ** 2 for value in values) / float(len(values) - 1)
    stddev = math.sqrt(variance)
    return stddev if math.isfinite(stddev) and stddev > 0 else None


def _pearson_correlation(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 2 or len(ys) < 2 or len(xs) != len(ys):
        return None
    x_avg = _safe_mean(xs)
    y_avg = _safe_mean(ys)
    if x_avg is None or y_avg is None:
        return None
    cov = sum((x - x_avg) * (y - y_avg) for x, y in zip(xs, ys))
    x_var = sum((x - x_avg) ** 2 for x in xs)
    y_var = sum((y - y_avg) ** 2 for y in ys)
    if x_var <= 0 or y_var <= 0:
        return None
    corr = cov / math.sqrt(x_var * y_var)
    if not math.isfinite(corr):
        return None
    return float(max(min(corr, 1.0), -1.0))


def _write_csv_rows(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    fieldnames: Sequence[str],
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
    return path


def _write_parquet_from_csv(csv_path: Path, parquet_path: Path) -> Path | None:
    try:
        import duckdb
    except ModuleNotFoundError:
        return None
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect()
    try:
        conn.execute(
            f"""
            COPY (
                SELECT *
                FROM read_csv_auto({_quote_path_literal(csv_path)}, header=true)
            ) TO {_quote_path_literal(parquet_path)} (FORMAT PARQUET)
            """
        )
    finally:
        conn.close()
    return parquet_path


def _resolve_output_paths(
    output_root: Path,
    scan_id: str,
) -> dict[str, Path]:
    run_dir = output_root / scan_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return {
        "run_dir": run_dir,
        "snapshot_csv": run_dir / "ticker_snapshot_progression.csv",
        "snapshot_parquet": run_dir / "ticker_snapshot_progression.parquet",
        "field_metrics_csv": run_dir / "ticker_snapshot_field_metrics.csv",
        "field_metrics_parquet": run_dir / "ticker_snapshot_field_metrics.parquet",
        "events_csv": run_dir / "field_change_events.csv",
        "forward_summary_csv": run_dir / "field_forward_return_summary.csv",
        "overview_log": run_dir / "_ticker_pattern_scan_overview.log",
        "context_json": run_dir / "ticker_pattern_scan_context.json",
    }


def _attach_forward_returns(
    snapshot_rows: list[dict[str, Any]],
    forward_days: Sequence[int],
) -> None:
    snapshot_dates = _snapshot_trading_dates(snapshot_rows)
    for index, row in enumerate(snapshot_rows):
        close_now = _coerce_float(row.get("close"))
        for horizon in forward_days:
            return_key = _forward_return_column_key(horizon)
            index_key = _forward_snapshot_index_column_key(horizon)
            gap_key = _forward_trading_day_gap_column_key(horizon)
            target_index = _find_forward_snapshot_index(
                snapshot_dates,
                index,
                int(horizon),
            )
            row[index_key] = target_index
            if (
                close_now is None
                or close_now == 0
                or target_index is None
            ):
                row[return_key] = None
                row[gap_key] = None
                continue
            close_future = _coerce_float(snapshot_rows[target_index].get("close"))
            if close_future is None:
                row[return_key] = None
                row[gap_key] = None
                continue
            row[gap_key] = _count_weekdays_between(
                snapshot_dates[index],
                snapshot_dates[target_index],
            )
            row[return_key] = ((close_future / close_now) - 1.0) * 100.0


def _dedupe_events_by_family(
    events: Sequence[FieldChangeEvent],
    field_base_names: Mapping[str, str],
) -> tuple[list[FieldChangeEvent], int]:
    best_by_key: dict[tuple[int, str], FieldChangeEvent] = {}
    for event in events:
        base_name = field_base_names.get(event.field_name, event.field_name)
        key = (event.snapshot_index, base_name)
        existing = best_by_key.get(key)
        if existing is None or abs(event.event_score) > abs(existing.event_score):
            best_by_key[key] = event
    deduped = sorted(
        best_by_key.values(),
        key=lambda item: abs(float(item.event_score)),
        reverse=True,
    )
    removed = len(events) - len(deduped)
    return deduped, removed


def _annotate_field_metrics(
    *,
    field_metric_rows: list[dict[str, Any]],
    min_event_score: float,
) -> list[FieldChangeEvent]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in field_metric_rows:
        grouped.setdefault(str(row["field_name"]), []).append(row)

    events: list[FieldChangeEvent] = []
    for field_name, rows in grouped.items():
        rows.sort(key=lambda item: int(item["snapshot_index"]))
        values = [value for value in (_coerce_float(row.get("field_value")) for row in rows) if value is not None]
        history_mean = _safe_mean(values)
        history_stddev = _safe_stddev(values)

        deltas: list[float] = []
        prior_value: float | None = None
        for row in rows:
            value = _coerce_float(row.get("field_value"))
            if value is not None and prior_value is not None:
                deltas.append(value - prior_value)
            prior_value = value
        delta_stddev = _safe_stddev(deltas)

        prior_value = None
        for row in rows:
            value = _coerce_float(row.get("field_value"))
            row["history_mean"] = history_mean
            row["history_stddev"] = history_stddev
            row["delta_stddev"] = delta_stddev
            row["prior_field_value"] = prior_value

            if value is not None and history_mean is not None and history_stddev:
                row["history_zscore"] = (value - history_mean) / history_stddev
            else:
                row["history_zscore"] = None

            if value is not None and prior_value is not None:
                delta = value - prior_value
                row["field_delta"] = delta
                row["field_pct_delta"] = (
                    ((delta / abs(prior_value)) * 100.0) if prior_value != 0 else None
                )
                row["delta_zscore"] = (
                    (delta / delta_stddev) if delta_stddev else None
                )
            else:
                row["field_delta"] = None
                row["field_pct_delta"] = None
                row["delta_zscore"] = None

            percentile = _coerce_float(row.get("percentile_rank"))
            history_zscore = _coerce_float(row.get("history_zscore"))
            universe_zscore = _coerce_float(row.get("ticker_universe_zscore"))
            delta_zscore = _coerce_float(row.get("delta_zscore"))
            flags: list[str] = []
            score = 0.0

            if _is_scale_field(field_name):
                flags.append("scale_field_excluded")
                row["event_score"] = 0.0
                row["event_flags_json"] = json.dumps(flags, ensure_ascii=False)
                prior_value = value
                continue

            if prior_value is None and value is not None:
                flags.append("missing_to_present")
                score += 1.25
            if prior_value is not None and value is None:
                flags.append("present_to_missing")
                score += 1.25
            if history_zscore is not None and abs(history_zscore) >= 2.0:
                flags.append("history_zscore_extreme")
                score += min(abs(history_zscore), 6.0) * 0.9
            if universe_zscore is not None and abs(universe_zscore) >= 2.0:
                flags.append("universe_zscore_extreme")
                score += min(abs(universe_zscore), 6.0) * 0.9
            if delta_zscore is not None and abs(delta_zscore) >= 2.0:
                flags.append("delta_spike")
                score += min(abs(delta_zscore), 6.0) * 0.8
            if percentile is not None and (percentile <= 0.10 or percentile >= 0.90):
                flags.append("percentile_tail")
                score += abs(percentile - 0.5) * 2.5

            row["event_score"] = score
            row["event_flags_json"] = json.dumps(flags, ensure_ascii=False)

            if flags and score >= float(min_event_score):
                events.append(
                    FieldChangeEvent(
                        snapshot_index=_coerce_int(row.get("snapshot_index")),
                        source_day_label=_normalize_text(row.get("source_day_label")),
                        run_id=_normalize_text(row.get("run_id")),
                        run_created_at_utc=_normalize_text(row.get("run_created_at_utc")),
                        matched_symbol=_normalize_text(row.get("matched_symbol")),
                        field_name=field_name,
                        field_value=_coerce_float(row.get("field_value")),
                        prior_field_value=_coerce_float(row.get("prior_field_value")),
                        field_delta=_coerce_float(row.get("field_delta")),
                        field_pct_delta=_coerce_float(row.get("field_pct_delta")),
                        history_zscore=_coerce_float(row.get("history_zscore")),
                        delta_zscore=_coerce_float(row.get("delta_zscore")),
                        percentile_rank=percentile,
                        ticker_universe_zscore=universe_zscore,
                        event_score=score,
                        event_flags_json=row["event_flags_json"],
                    )
                )
            prior_value = value

    events.sort(key=lambda item: abs(float(item.event_score)), reverse=True)
    return events


def _attach_snapshot_anomaly_summary(
    *,
    snapshot_rows: list[dict[str, Any]],
    field_metric_rows: Sequence[Mapping[str, Any]],
) -> None:
    per_snapshot: dict[int, list[dict[str, Any]]] = {}
    for row in field_metric_rows:
        snapshot_index = _coerce_int(row.get("snapshot_index"))
        per_snapshot.setdefault(snapshot_index, []).append(dict(row))

    for snapshot in snapshot_rows:
        index = _coerce_int(snapshot.get("snapshot_index"))
        rows = per_snapshot.get(index, [])
        ranked = sorted(
            rows,
            key=lambda item: abs(_coerce_float(item.get("event_score")) or 0.0),
            reverse=True,
        )
        strong = [row for row in ranked if (_coerce_float(row.get("event_score")) or 0.0) > 0]
        top = strong[:5]
        snapshot["scanned_field_count"] = len(rows)
        snapshot["anomaly_field_count"] = len(strong)
        snapshot["top_anomaly_fields"] = "|".join(
            _normalize_text(row.get("field_name")) for row in top
        )
        snapshot["top_anomaly_details_json"] = json.dumps(
            [
                {
                    "field_name": _normalize_text(row.get("field_name")),
                    "event_score": _coerce_float(row.get("event_score")),
                    "history_zscore": _coerce_float(row.get("history_zscore")),
                    "ticker_universe_zscore": _coerce_float(
                        row.get("ticker_universe_zscore")
                    ),
                    "percentile_rank": _coerce_float(row.get("percentile_rank")),
                }
                for row in top
            ],
            ensure_ascii=False,
        )


def _build_forward_associations(
    *,
    field_metric_rows: Sequence[Mapping[str, Any]],
    snapshot_rows: Sequence[Mapping[str, Any]],
    forward_days: Sequence[int],
    min_samples_for_association: int,
) -> list[FieldForwardAssociation]:
    snapshot_by_index = {
        _coerce_int(row.get("snapshot_index")): dict(row) for row in snapshot_rows
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in field_metric_rows:
        grouped.setdefault(_normalize_text(row.get("field_name")), []).append(dict(row))

    associations: list[FieldForwardAssociation] = []
    for field_name, rows in grouped.items():
        rows.sort(key=lambda item: _coerce_int(item.get("snapshot_index")))
        for horizon in forward_days:
            forward_key = _forward_return_column_key(int(horizon))
            value_obs: list[tuple[float, float]] = []
            delta_obs: list[tuple[float, float]] = []
            for row in rows:
                snapshot = snapshot_by_index.get(_coerce_int(row.get("snapshot_index")))
                if snapshot is None:
                    continue
                forward_return = _coerce_float(snapshot.get(forward_key))
                if forward_return is None:
                    continue
                field_value = _coerce_float(row.get("field_value"))
                field_delta = _coerce_float(row.get("field_delta"))
                if field_value is not None:
                    value_obs.append((field_value, forward_return))
                if field_delta is not None:
                    delta_obs.append((field_delta, forward_return))

            if not value_obs:
                continue

            x_values = [item[0] for item in value_obs]
            returns = [item[1] for item in value_obs]
            value_corr = _pearson_correlation(x_values, returns)
            delta_corr = _pearson_correlation(
                [item[0] for item in delta_obs],
                [item[1] for item in delta_obs],
            )
            n = len(value_obs)
            sorted_obs = sorted(value_obs, key=lambda item: item[0])
            bucket = max(1, n // 4)
            low_returns = [item[1] for item in sorted_obs[:bucket]]
            high_returns = [item[1] for item in sorted_obs[-bucket:]]
            low_avg = _safe_mean(low_returns)
            high_avg = _safe_mean(high_returns)
            spread = (
                (high_avg - low_avg)
                if high_avg is not None and low_avg is not None
                else None
            )
            return_stddev = _safe_stddev(returns)

            x_median = median(x_values) if x_values else None
            directional_hits = 0
            directional_total = 0
            if x_median is not None:
                for value, ret in value_obs:
                    directional_total += 1
                    bullish = value >= x_median
                    if (bullish and ret >= 0) or ((not bullish) and ret <= 0):
                        directional_hits += 1
            directional_hit_rate = (
                float(directional_hits) / float(directional_total)
                if directional_total > 0
                else None
            )

            spread_strength = 0.0
            if spread is not None and return_stddev is not None and return_stddev > 0:
                spread_strength = abs(spread / return_stddev)
            elif spread is not None:
                spread_strength = abs(spread) / 10.0

            pattern_score = (
                (abs(value_corr or 0.0) * 0.50)
                + (abs(delta_corr or 0.0) * 0.20)
                + (spread_strength * 0.30)
            )
            sign = 1.0
            if spread is not None and spread < 0:
                sign = -1.0
            elif spread is None and value_corr is not None and value_corr < 0:
                sign = -1.0
            pattern_score = pattern_score * sign if pattern_score else 0.0

            warnings: list[str] = []
            meets_min_sample = n >= int(min_samples_for_association)
            if not meets_min_sample:
                warnings.append("low_sample_size")
            if return_stddev is None:
                warnings.append("insufficient_return_variance")

            associations.append(
                FieldForwardAssociation(
                    field_name=field_name,
                    horizon_days=int(horizon),
                    sample_n=n,
                    meets_min_sample=meets_min_sample,
                    field_value_corr=value_corr,
                    field_delta_corr=delta_corr,
                    low_quartile_avg_return=low_avg,
                    high_quartile_avg_return=high_avg,
                    quartile_return_spread=spread,
                    return_stddev=return_stddev,
                    directional_hit_rate=directional_hit_rate,
                    pattern_score=pattern_score if meets_min_sample else None,
                    warnings_json=json.dumps(warnings, ensure_ascii=False),
                )
            )

    associations.sort(
        key=lambda item: (
            0 if item.meets_min_sample else 1,
            -abs(float(item.pattern_score or 0.0)),
        ),
    )
    return associations


def _partition_associations(
    associations: Sequence[FieldForwardAssociation],
    *,
    min_samples_for_association: int,
) -> tuple[list[FieldForwardAssociation], list[FieldForwardAssociation]]:
    qualified = [
        row for row in associations if row.sample_n >= int(min_samples_for_association)
    ]
    demoted = [
        row for row in associations if row.sample_n < int(min_samples_for_association)
    ]
    qualified.sort(key=lambda item: abs(float(item.pattern_score or 0.0)), reverse=True)
    demoted.sort(key=lambda item: abs(float(item.pattern_score or 0.0)), reverse=True)
    return qualified, demoted


def _build_overview_log_lines(
    *,
    config: TickerPatternScanConfig,
    scan_id: str,
    resolved_runs: Sequence[ResolvedAllFieldsRun],
    snapshot_rows: Sequence[Mapping[str, Any]],
    events: Sequence[FieldChangeEvent],
    qualified_associations: Sequence[FieldForwardAssociation],
    demoted_associations: Sequence[FieldForwardAssociation],
    missing_ticker_day_labels: Sequence[str],
    effective_forward_days: Sequence[int],
    trading_day_span: int,
    events_deduped_removed: int,
) -> list[str]:
    lines: list[str] = []
    lines.append(f"Ticker Pattern Scan | {scan_id}")
    lines.append(f"ticker_requested={config.ticker}")
    lines.append(f"ticker_bare={_normalize_symbol(config.ticker)}")
    lines.append(
        f"date_range={_normalize_text(config.start_day_label)}..{_normalize_text(config.end_day_label)}"
    )
    lines.append(f"resolved_runs={len(resolved_runs)}")
    lines.append(f"matched_snapshots={len(snapshot_rows)}")
    lines.append(f"trading_day_span={trading_day_span}")
    lines.append(
        "forward_horizons="
        + f"requested={list(config.requested_forward_days or config.forward_days)} "
        + f"effective={list(effective_forward_days)} "
        + "(trading days)"
    )
    lines.append(f"field_events={len(events)}")
    if events_deduped_removed:
        lines.append(f"field_events_deduped_removed={events_deduped_removed}")
    lines.append(
        f"forward_associations={len(qualified_associations) + len(demoted_associations)} "
        f"(qualified={len(qualified_associations)}, demoted={len(demoted_associations)})"
    )
    if missing_ticker_day_labels:
        lines.append(
            "missing_ticker_day_labels="
            + ", ".join(sorted(set(missing_ticker_day_labels)))
        )

    lines.append("")
    lines.append("Top Field Events")
    for event in events[:20]:
        lines.append(
            "  - "
            + f"{event.source_day_label} | {event.field_name} | score={event.event_score:.3f} "
            + f"| value={event.field_value} | delta={event.field_delta} "
            + f"| pct={event.field_pct_delta} | percentile={event.percentile_rank} "
            + f"| flags={event.event_flags_json}"
        )

    lines.append("")
    lines.append("Top Forward Associations")
    for row in qualified_associations[:20]:
        lines.append(
            "  - "
            + f"{row.field_name} @ +{row.horizon_days}td | n={row.sample_n} "
            + f"| corr={row.field_value_corr} | delta_corr={row.field_delta_corr} "
            + f"| spread={row.quartile_return_spread} | score={row.pattern_score} "
            + f"| warnings={row.warnings_json}"
        )

    if demoted_associations:
        lines.append("")
        lines.append("Demoted (low sample)")
        for row in demoted_associations[:10]:
            lines.append(
                "  - "
                + f"{row.field_name} @ +{row.horizon_days}td | n={row.sample_n} "
                + f"| corr={row.field_value_corr} | spread={row.quartile_return_spread} "
                + f"| warnings={row.warnings_json}"
            )
    return lines


def run_ticker_field_pattern_scan(
    *,
    ticker: str,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    output_root: str | Path = DEFAULT_TICKER_PATTERN_SCAN_ROOT,
    field_catalog_csv: str | Path = DEFAULT_FIELD_CATALOG_CSV,
    taxonomy_predictor_csv: str | Path | None = DEFAULT_TAXONOMY_PREDICTOR_CSV,
    min_market_cap_usd: float | None = 500_000_000,
    max_fields: int = 250,
    forward_days: Sequence[int] = DEFAULT_FORWARD_DAYS,
    min_scan_data_count: int = 0,
    include_stock_only_universe: bool = True,
    include_primary_only_universe: bool = True,
    constrain_to_sector: bool = False,
    constrain_to_industry: bool = True,
    additional_peer_industries: Sequence[str] = (),
    min_event_score: float = 2.0,
    min_samples_for_association: int = 8,
    field_bucket_quotas: dict[str, int] | None = None,
    prefer_intraday: bool = False,
) -> dict[str, Any]:
    """Scan one ticker for backward field-pattern behavior across all-fields runs."""
    if not _normalize_text(ticker):
        raise ValueError("ticker is required.")
    requested_forward_days = tuple(
        sorted({int(day) for day in forward_days if int(day) > 0})
    )
    if not requested_forward_days:
        raise ValueError("forward_days must contain at least one positive horizon.")

    resolved_additional_industries = tuple(
        _normalize_text(industry)
        for industry in additional_peer_industries
        if _normalize_text(industry)
    )

    config = TickerPatternScanConfig(
        ticker=ticker,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
        all_fields_root=Path(all_fields_root),
        output_root=Path(output_root),
        field_catalog_csv=Path(field_catalog_csv),
        taxonomy_predictor_csv=(
            None if taxonomy_predictor_csv is None else Path(taxonomy_predictor_csv)
        ),
        min_market_cap_usd=min_market_cap_usd,
        max_fields=max_fields,
        forward_days=requested_forward_days,
        min_scan_data_count=min_scan_data_count,
        include_stock_only_universe=include_stock_only_universe,
        include_primary_only_universe=include_primary_only_universe,
        constrain_to_sector=constrain_to_sector,
        constrain_to_industry=constrain_to_industry,
        additional_peer_industries=resolved_additional_industries,
        min_event_score=min_event_score,
        min_samples_for_association=min_samples_for_association,
        field_bucket_quotas=field_bucket_quotas,
        prefer_intraday=prefer_intraday,
        requested_forward_days=requested_forward_days,
    )

    scan_id = _build_scan_id()
    output_paths = _resolve_output_paths(config.output_root, scan_id)
    resolved_runs = _resolve_all_fields_runs(config)
    ticker_requested_upper = _normalize_text(config.ticker).upper()
    ticker_bare_upper = _normalize_symbol(config.ticker)

    metadata_by_field, _, _ = _read_taxonomy_metadata(config.taxonomy_predictor_csv)
    field_base_names = {
        field_name: _normalize_text(meta.get("base_name")) or field_name.split("|", 1)[0]
        for field_name, meta in metadata_by_field.items()
    }

    # Use the first resolved DB to derive candidate fields and context defaults.
    with open_move_prediction_duckdb_connection(
        resolved_runs[0].database_path, read_only=True
    ) as first_conn:
        first_columns = _fetch_available_columns(first_conn, "all_fields_rows")
    candidate_fields, context_fields, selection_meta = _resolve_candidate_fields(
        all_columns=first_columns,
        config=config,
    )
    if not candidate_fields:
        raise ValueError(
            "No candidate numeric fields were selected from the all-fields schema. "
            "Check field catalog/taxonomy availability and max_fields."
        )

    snapshot_rows: list[dict[str, Any]] = []
    field_metric_rows: list[dict[str, Any]] = []
    missing_ticker_day_labels: list[str] = []
    used_runs: list[dict[str, Any]] = []

    for run in resolved_runs:
        with open_move_prediction_duckdb_connection(run.database_path, read_only=True) as conn:
            available_columns = set(_fetch_available_columns(conn, "all_fields_rows"))
            day_fields = [field for field in candidate_fields if field in available_columns]
            day_context_fields = [field for field in context_fields if field in available_columns]
            selected_cols = list(dict.fromkeys(day_context_fields + day_fields))

            ticker_row = _fetch_ticker_row(
                conn,
                run_id=run.run_id,
                ticker_requested_upper=ticker_requested_upper,
                ticker_bare_upper=ticker_bare_upper,
                selected_columns=selected_cols,
            )
            if ticker_row is None:
                missing_ticker_day_labels.append(run.day_label)
                continue

            universe_filter_sql = _build_universe_filter_sql(
                available_columns=available_columns,
                config=config,
                ticker_row=ticker_row,
            )
            day_stats = _fetch_universe_field_stats(
                conn,
                run_id=run.run_id,
                ticker_bare_upper=ticker_bare_upper,
                fields=day_fields,
                universe_filter_sql=universe_filter_sql,
            )

            matched_symbol = _normalize_text(ticker_row.get("symbol"))
            snapshot = {
                "source_day_label": run.day_label,
                "run_id": run.run_id,
                "run_created_at_utc": _safe_iso(run.created_at_utc),
                "source_database_path": run.database_path.as_posix(),
                "ticker_requested": config.ticker,
                "ticker_bare": ticker_bare_upper,
                "matched_symbol": matched_symbol,
            }
            for context_field in DEFAULT_CONTEXT_FIELDS:
                value = ticker_row.get(context_field)
                if context_field in {
                    "close",
                    "Perf.5D",
                    "Perf.1M",
                    "Perf.3M",
                    "volume",
                    "relative_volume_10d_calc",
                    "market_cap_basic",
                }:
                    snapshot[context_field] = _coerce_float(value)
                else:
                    snapshot[context_field] = _normalize_text(value)
            snapshot_rows.append(snapshot)
            used_runs.append(
                {
                    "day_label": run.day_label,
                    "run_id": run.run_id,
                    "run_created_at_utc": _safe_iso(run.created_at_utc),
                    "database_path": run.database_path.as_posix(),
                    "run_label": run.run_label,
                    "scan_data_count": run.scan_data_count,
                    "matched_symbol": matched_symbol,
                    "candidate_field_count_on_day": len(day_fields),
                }
            )

            for field_name in candidate_fields:
                stats_row = day_stats.get(field_name, {})
                raw_value = ticker_row.get(field_name)
                if raw_value is None and stats_row:
                    raw_value = stats_row.get("ticker_value")
                field_metric_rows.append(
                    {
                        "source_day_label": run.day_label,
                        "run_id": run.run_id,
                        "run_created_at_utc": _safe_iso(run.created_at_utc),
                        "matched_symbol": matched_symbol,
                        "field_name": field_name,
                        "field_raw_value": _normalize_text(raw_value),
                        "field_value": _coerce_float(raw_value),
                        "universe_n": _coerce_int(stats_row.get("universe_n")),
                        "universe_mean": _coerce_float(stats_row.get("universe_mean")),
                        "universe_stddev": _coerce_float(stats_row.get("universe_stddev")),
                        "universe_median": _coerce_float(stats_row.get("universe_median")),
                        "percentile_rank": _coerce_float(stats_row.get("percentile_rank")),
                        "ticker_universe_zscore": _coerce_float(
                            stats_row.get("ticker_universe_zscore")
                        ),
                        "field_available_in_database": field_name in available_columns,
                    }
                )

    if not snapshot_rows:
        raise ValueError(
            f"Ticker '{config.ticker}' was not found in resolved all-fields runs "
            f"for the selected date range."
        )

    # Stable chronological order.
    snapshot_rows.sort(
        key=lambda row: (
            _coerce_datetime_utc(row.get("run_created_at_utc"))
            or datetime.min.replace(tzinfo=timezone.utc),
            _normalize_text(row.get("source_day_label")),
            _normalize_text(row.get("run_id")),
        )
    )
    snapshot_key_to_index: dict[tuple[str, str], int] = {}
    for index, row in enumerate(snapshot_rows):
        row["snapshot_index"] = index
        snapshot_key_to_index[
            (_normalize_text(row.get("source_day_label")), _normalize_text(row.get("run_id")))
        ] = index
    for row in field_metric_rows:
        key = (
            _normalize_text(row.get("source_day_label")),
            _normalize_text(row.get("run_id")),
        )
        row["snapshot_index"] = snapshot_key_to_index.get(key, -1)

    snapshot_dates = _snapshot_trading_dates(snapshot_rows)
    trading_day_span = _compute_trading_day_span(snapshot_dates)
    effective_forward_days = _resolve_effective_forward_days(
        config.requested_forward_days or config.forward_days,
        snapshot_count=len(snapshot_rows),
        trading_day_span=trading_day_span,
    )
    config = TickerPatternScanConfig(
        **{
            **asdict(config),
            "forward_days": effective_forward_days,
        }
    )

    _attach_forward_returns(snapshot_rows, config.forward_days)
    raw_events = _annotate_field_metrics(
        field_metric_rows=field_metric_rows,
        min_event_score=config.min_event_score,
    )
    events, events_deduped_removed = _dedupe_events_by_family(
        raw_events,
        field_base_names,
    )
    _attach_snapshot_anomaly_summary(
        snapshot_rows=snapshot_rows,
        field_metric_rows=field_metric_rows,
    )
    associations = _build_forward_associations(
        field_metric_rows=field_metric_rows,
        snapshot_rows=snapshot_rows,
        forward_days=config.forward_days,
        min_samples_for_association=config.min_samples_for_association,
    )
    qualified_associations, demoted_associations = _partition_associations(
        associations,
        min_samples_for_association=config.min_samples_for_association,
    )

    event_rows = [asdict(event) for event in events]
    forward_rows = [asdict(row) for row in associations]
    snapshot_columns = list(SNAPSHOT_BASE_COLUMNS)
    for horizon in config.forward_days:
        snapshot_columns.extend(
            [
                _forward_return_column_key(horizon),
                _forward_snapshot_index_column_key(horizon),
                _forward_trading_day_gap_column_key(horizon),
            ]
        )

    _write_csv_rows(
        output_paths["snapshot_csv"],
        snapshot_rows,
        fieldnames=snapshot_columns,
    )
    _write_csv_rows(
        output_paths["field_metrics_csv"],
        field_metric_rows,
        fieldnames=FIELD_METRIC_COLUMNS,
    )
    _write_csv_rows(
        output_paths["events_csv"],
        event_rows,
        fieldnames=EVENT_COLUMNS,
    )
    _write_csv_rows(
        output_paths["forward_summary_csv"],
        forward_rows,
        fieldnames=FORWARD_SUMMARY_COLUMNS,
    )
    snapshot_parquet = _write_parquet_from_csv(
        output_paths["snapshot_csv"], output_paths["snapshot_parquet"]
    )
    field_metrics_parquet = _write_parquet_from_csv(
        output_paths["field_metrics_csv"], output_paths["field_metrics_parquet"]
    )

    overview_lines = _build_overview_log_lines(
        config=config,
        scan_id=scan_id,
        resolved_runs=resolved_runs,
        snapshot_rows=snapshot_rows,
        events=events,
        qualified_associations=qualified_associations,
        demoted_associations=demoted_associations,
        missing_ticker_day_labels=missing_ticker_day_labels,
        effective_forward_days=config.forward_days,
        trading_day_span=trading_day_span,
        events_deduped_removed=events_deduped_removed,
    )
    output_paths["overview_log"].write_text(
        "\n".join(overview_lines) + "\n", encoding="utf-8"
    )

    context_payload = {
        "scan_id": scan_id,
        "created_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "config": {
            **asdict(config),
            "all_fields_root": config.all_fields_root.as_posix(),
            "output_root": config.output_root.as_posix(),
            "field_catalog_csv": config.field_catalog_csv.as_posix(),
            "taxonomy_predictor_csv": (
                None
                if config.taxonomy_predictor_csv is None
                else config.taxonomy_predictor_csv.as_posix()
            ),
        },
        "selection": selection_meta,
        "resolved_run_count": len(resolved_runs),
        "matched_snapshot_count": len(snapshot_rows),
        "trading_day_span": trading_day_span,
        "requested_forward_days": list(config.requested_forward_days),
        "effective_forward_days": list(config.forward_days),
        "qualified_association_count": len(qualified_associations),
        "demoted_association_count": len(demoted_associations),
        "events_deduped_removed": events_deduped_removed,
        "missing_ticker_day_labels": missing_ticker_day_labels,
        "used_runs": used_runs,
        "output_files": {
            "snapshot_csv": output_paths["snapshot_csv"].as_posix(),
            "snapshot_parquet": snapshot_parquet.as_posix() if snapshot_parquet else None,
            "field_metrics_csv": output_paths["field_metrics_csv"].as_posix(),
            "field_metrics_parquet": (
                field_metrics_parquet.as_posix() if field_metrics_parquet else None
            ),
            "events_csv": output_paths["events_csv"].as_posix(),
            "forward_summary_csv": output_paths["forward_summary_csv"].as_posix(),
            "overview_log": output_paths["overview_log"].as_posix(),
        },
    }
    output_paths["context_json"].write_text(
        json.dumps(context_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "scan_id": scan_id,
        "ticker_requested": config.ticker,
        "ticker_bare": ticker_bare_upper,
        "output_dir": output_paths["run_dir"].as_posix(),
        "snapshot_progression_csv": output_paths["snapshot_csv"].as_posix(),
        "snapshot_progression_parquet": (
            snapshot_parquet.as_posix() if snapshot_parquet else None
        ),
        "field_metrics_csv": output_paths["field_metrics_csv"].as_posix(),
        "field_metrics_parquet": (
            field_metrics_parquet.as_posix() if field_metrics_parquet else None
        ),
        "field_change_events_csv": output_paths["events_csv"].as_posix(),
        "field_forward_return_summary_csv": output_paths["forward_summary_csv"].as_posix(),
        "overview_log": output_paths["overview_log"].as_posix(),
        "context_json": output_paths["context_json"].as_posix(),
        "resolved_run_count": len(resolved_runs),
        "matched_snapshot_count": len(snapshot_rows),
        "trading_day_span": trading_day_span,
        "requested_forward_days": list(config.requested_forward_days),
        "effective_forward_days": list(config.forward_days),
        "qualified_association_count": len(qualified_associations),
        "demoted_association_count": len(demoted_associations),
        "events_deduped_removed": events_deduped_removed,
        "field_event_count": len(event_rows),
        "field_forward_summary_count": len(forward_rows),
        "missing_ticker_day_labels": missing_ticker_day_labels,
        "matched_symbols": sorted(
            {
                _normalize_text(row.get("matched_symbol"))
                for row in snapshot_rows
                if _normalize_text(row.get("matched_symbol"))
            }
        ),
    }


__all__ = ["run_ticker_field_pattern_scan"]
