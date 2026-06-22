"""Market flow screening from daily TradingView all-fields DuckDB snapshots.

Estimates capital inflow/outflow vs rotation at macro, regional, sector, and
industry levels by comparing paired daily exports produced by
``export_all_tradingview_fields_duckdb()``.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from data_analysis_scripts._shared_analysis_utils import (
    build_report_title,
    format_market_cap,
    format_number,
    format_signed_percent,
    get_symbol_name,
    median_absolute_deviation,
    reset_log_file,
    safe_ratio,
    slugify,
)
from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
    ALL_FIELDS_SUITE_NAME,
    DEFAULT_ALL_FIELDS_ROOT,
    discover_all_fields_daily_databases,
    move_prediction_universe_filter,
    normalize_all_fields_universe_filter,
    resolve_all_fields_universe_filter_sql,
    resolve_run_id,
    _extract_day_label_from_database_path,
    _try_parse_day_label,
)
from db.trading_view_all_fields_duckdb import query_tradingview_all_fields_duckdb
from db.trading_view_move_prediction_duckdb import open_move_prediction_duckdb_connection
from generic_utils.log_to_files_util import log_rows_to_csv, log_to_file

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "logs" / "tradingview_analysis" / "market_flow_screening"
)

MAX_STOCKS_PER_INDUSTRY = 10
MATERIAL_GROUP_LIMIT = 15
TOP_HIGHLIGHT_COUNT = 5
FLOW_RESIDUAL_THRESHOLD_PCT = 0.5
ROTATION_RATIO_HIGH = 3.0
UNIVERSE_NET_FLOW_USD_THRESHOLD = 50_000_000.0
RUN_LIFECYCLE_ID_LENGTH = 8
MAX_ABS_PRICE_RETURN_PCT = 150.0
MAX_ABS_FLOW_RESIDUAL_PCT = 200.0
BUYBACK_YIELD_THRESHOLD = 0.02
SHARE_BUYBACK_RATIO_THRESHOLD = 0.02
SHARE_CHANGE_BUYBACK_PCT = -1.0
SHARE_CHANGE_DILUTION_PCT = 1.0
PRICE_ONLY_THRESHOLD_PCT = 0.5
CHAIKIN_CONFIRM_THRESHOLD = 0.0
RELVOL_CONFIRM_FLOOR = -0.1
CHAIKIN_FIELD_CANDIDATES = ("ChaikinMoneyFlow|1M", "ChaikinMoneyFlow")
SHARE_COUNT_FIELD_CANDIDATES = (
    "float_shares_outstanding",
    "diluted_shares_outstanding_fq",
)


def _coerce_field_numeric(value: Any) -> float | None:
    """Parse DuckDB VARCHAR field values (and native numerics) into floats."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


FLOW_SCREENING_FIELDS: tuple[str, ...] = (
    "symbol",
    "name",
    "country",
    "sector",
    "industry",
    "market",
    "exchange",
    "market_cap_basic",
    "close",
    "volume",
    "relative_volume_10d_calc",
    "Value.Traded",
    "AvgValue.Traded_10d",
    "float_shares_outstanding",
    "diluted_shares_outstanding_fq",
    "buyback_yield",
    "share_buyback_ratio_fq",
    "ChaikinMoneyFlow|1M",
    "ChaikinMoneyFlow",
    "Perf.5D",
    "Perf.1M",
    "SMA50",
    "SMA200",
    "Volatility.W",
)


@dataclass(frozen=True)
class FlowRefinementConfig:
    max_abs_price_return_pct: float = MAX_ABS_PRICE_RETURN_PCT
    max_abs_flow_residual_pct: float = MAX_ABS_FLOW_RESIDUAL_PCT
    buyback_yield_threshold: float = BUYBACK_YIELD_THRESHOLD
    share_buyback_ratio_threshold: float = SHARE_BUYBACK_RATIO_THRESHOLD
    share_change_buyback_pct: float = SHARE_CHANGE_BUYBACK_PCT
    share_change_dilution_pct: float = SHARE_CHANGE_DILUTION_PCT
    price_only_threshold_pct: float = PRICE_ONLY_THRESHOLD_PCT
    chaikin_confirm_threshold: float = CHAIKIN_CONFIRM_THRESHOLD
    relvol_confirm_floor: float = RELVOL_CONFIRM_FLOOR


@dataclass
class SymbolFlowRecord:
    symbol: str
    company: str
    country: str
    sector: str
    industry: str
    market: str
    base_market_cap: float | None
    compare_market_cap: float | None
    price_return_pct: float | None
    mcap_delta_pct: float | None
    flow_residual_pct: float | None
    flow_residual_usd: float | None
    relvol_delta: float | None
    participation_delta: float | None
    perf_5d: float | None
    volatility_w_delta: float | None
    signal: str
    share_change_pct: float | None = None
    price_effect_usd: float | None = None
    share_effect_usd: float | None = None
    buyback_yield: float | None = None
    share_buyback_ratio_fq: float | None = None
    chaikin_mf_1m: float | None = None
    flow_driver: str = "unknown"
    data_quality: str = "ok"
    signal_context: str = "neutral"
    flow_residual_usd_adjusted: float | None = None


@dataclass
class GroupFlowRecord:
    group_name: str
    group_field: str
    universe_count: int
    inflow_count: int
    outflow_count: int
    neutral_count: int
    net_flow_usd: float
    cap_weighted_flow_pct: float | None
    median_flow_residual_pct: float | None
    participation_intensity: float | None
    flow_z_score: float | None = None
    net_flow_usd_adjusted: float = 0.0
    confirmed_inflow_count: int = 0
    confirmed_outflow_count: int = 0
    outlier_count: int = 0
    buyback_like_count: int = 0


@dataclass
class UniverseFlowSummary:
    universe_count: int
    universe_net_flow_usd: float
    gross_flow_usd: float
    rotation_ratio: float
    verdict: str
    inflow_count: int
    outflow_count: int
    neutral_count: int
    universe_net_flow_usd_adjusted: float = 0.0
    outlier_count: int = 0
    buyback_like_count: int = 0
    confirmed_inflow_count: int = 0
    confirmed_outflow_count: int = 0


@dataclass
class SectorRotationPair:
    donor_sector: str
    receiver_sector: str
    implied_rotation_usd: float
    donor_net_flow_usd: float
    receiver_net_flow_usd: float


@dataclass
class IndustryParticipationRecord:
    industry: str
    participation_score: float
    participation_z_score: float
    median_relvol: float | None
    median_perf_1m: float | None
    stock_count: int
    exemplar_symbols: list[str] = field(default_factory=list)


@dataclass
class MarketFlowScreeningResult:
    run_id: str
    output_root: Path
    base_day_label: str
    compare_day_label: str
    base_database_path: Path
    compare_database_path: Path
    universe_summary: UniverseFlowSummary
    symbol_records: list[SymbolFlowRecord]
    industry_groups: list[GroupFlowRecord]
    country_groups: list[GroupFlowRecord]
    sector_groups: list[GroupFlowRecord]
    overview_log: Path
    manifest_path: Path


def _new_run_lifecycle_id() -> str:
    return uuid.uuid4().hex[:RUN_LIFECYCLE_ID_LENGTH]


def _resolve_run_lifecycle_id(run_lifecycle_id: str | None) -> str:
    if run_lifecycle_id:
        normalized = str(run_lifecycle_id).strip()
        if normalized:
            return normalized
    return _new_run_lifecycle_id()


def _format_day_range_scope_label(base_day_label: str, compare_day_label: str) -> str:
    base_date = _try_parse_day_label(base_day_label)
    compare_date = _try_parse_day_label(compare_day_label)
    if base_date is None or compare_date is None:
        return f"{base_day_label}_to_{compare_day_label}"
    if base_date.year == compare_date.year:
        return f"{base_date.strftime('%d%b')}_{compare_date.strftime('%d%b%Y')}"
    return f"{base_date.strftime('%d%b%Y')}_{compare_date.strftime('%d%b%Y')}"


def allocate_run_output_dir(
    base_day_label: str,
    compare_day_label: str,
    *,
    output_root: str | Path | None = None,
    run_lifecycle_id: str | None = None,
) -> tuple[str, Path]:
    lifecycle_id = _resolve_run_lifecycle_id(run_lifecycle_id)
    scope = _format_day_range_scope_label(base_day_label, compare_day_label)
    run_id = f"flow_{scope}_{lifecycle_id}"
    parent = Path(output_root) if output_root is not None else DEFAULT_OUTPUT_ROOT / "runs"
    output_dir = parent / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    for subfolder in ("macro", "industries", "exports"):
        (output_dir / subfolder).mkdir(parents=True, exist_ok=True)
    return run_id, output_dir


def resolve_day_database(
    day_label: str,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
) -> Path:
    normalized = str(day_label or "").strip()
    if not normalized:
        raise ValueError("day_label is required")
    root = Path(all_fields_root)
    expected = root / normalized / f"tradingview_all_fields_{normalized}.duckdb"
    if expected.is_file():
        return expected
    matches = [
        candidate
        for candidate in root.rglob(f"tradingview_all_fields_{normalized}.duckdb")
        if candidate.is_file()
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(
            f"No all-fields DuckDB found for day_label={normalized!r} under {root}"
        )
    raise ValueError(
        f"Multiple all-fields DuckDB files found for day_label={normalized!r}: {matches}"
    )


def discover_prior_day_label(
    day_label: str,
    *,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    lookback_days: int = 10,
) -> str | None:
    anchor = _try_parse_day_label(day_label)
    if anchor is None:
        return None
    root = Path(all_fields_root)
    if not root.exists():
        return None
    candidates: list[tuple[date, str]] = []
    for database_path in root.rglob("tradingview_all_fields_*.duckdb"):
        if not database_path.is_file():
            continue
        label = _extract_day_label_from_database_path(database_path)
        day_value = _try_parse_day_label(label)
        if day_value is None or day_value >= anchor:
            continue
        if (anchor - day_value).days > lookback_days:
            continue
        candidates.append((day_value, label))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def discover_latest_day_label(
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
) -> str | None:
    root = Path(all_fields_root)
    if not root.exists():
        return None
    candidates: list[tuple[date, str]] = []
    for database_path in root.rglob("tradingview_all_fields_*.duckdb"):
        if not database_path.is_file():
            continue
        label = _extract_day_label_from_database_path(database_path)
        day_value = _try_parse_day_label(label)
        if day_value is None:
            continue
        candidates.append((day_value, label))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _bare_symbol(symbol: str) -> str:
    text = str(symbol or "").strip()
    if ":" in text:
        return text.split(":", 1)[-1]
    return text


def _symbol_lookup_keys(symbol: str) -> list[str]:
    keys: list[str] = []
    for candidate in (symbol, _bare_symbol(symbol)):
        normalized = str(candidate or "").strip()
        if normalized and normalized not in keys:
            keys.append(normalized)
    return keys


def _build_row_index(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = dict(row)
        symbol = str(payload.get("symbol") or "").strip()
        if not symbol:
            continue
        for key in _symbol_lookup_keys(symbol):
            index.setdefault(key, payload)
    return index


def align_snapshot_pairs(
    base_rows: Sequence[Mapping[str, Any]],
    compare_rows: Sequence[Mapping[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    compare_index = _build_row_index(compare_rows)
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen_compare_symbols: set[str] = set()
    for base_row in base_rows:
        base_payload = dict(base_row)
        symbol = str(base_payload.get("symbol") or "").strip()
        if not symbol:
            continue
        compare_payload: dict[str, Any] | None = None
        for key in _symbol_lookup_keys(symbol):
            candidate = compare_index.get(key)
            if candidate is not None:
                compare_payload = candidate
                break
        if compare_payload is None:
            continue
        compare_symbol = str(compare_payload.get("symbol") or "").strip()
        if compare_symbol in seen_compare_symbols:
            continue
        seen_compare_symbols.add(compare_symbol)
        pairs.append((base_payload, compare_payload))
    return pairs


def _list_table_columns(database_path: Path, table_name: str = "all_fields_rows") -> list[str]:
    with open_move_prediction_duckdb_connection(database_path, read_only=True) as conn:
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


def load_snapshot_rows(
    database_path: str | Path,
    *,
    run_id: str | None = None,
    fields: Sequence[str] | None = None,
    universe_filter: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    database = Path(database_path)
    resolved_run_id = resolve_run_id(database, run_id)
    available_columns = _list_table_columns(database)
    requested_fields = list(fields or FLOW_SCREENING_FIELDS)
    selected_fields = [
        field_name
        for field_name in requested_fields
        if field_name in available_columns or field_name == "symbol"
    ]
    if "symbol" not in selected_fields:
        selected_fields.insert(0, "symbol")
    selected_fields = list(dict.fromkeys(selected_fields))

    filter_resolution = resolve_all_fields_universe_filter_sql(
        universe_filter,
        available_columns=available_columns,
        table_alias=None,
    )
    columns_sql = ", ".join(f'"{column}"' for column in selected_fields)
    where_sql = f"run_id = ?{filter_resolution.sql_fragment}"
    query = f"SELECT {columns_sql} FROM all_fields_rows WHERE {where_sql}"
    rows = query_tradingview_all_fields_duckdb(database, query, [resolved_run_id])
    return [dict(row) for row in rows]


def _percent_change(new_value: float | None, old_value: float | None) -> float | None:
    if new_value is None or old_value is None or old_value == 0:
        return None
    return ((new_value - old_value) / abs(old_value)) * 100.0


def _participation_ratio(row: Mapping[str, Any]) -> float | None:
    avg_value = _coerce_field_numeric(row.get("AvgValue.Traded_10d"))
    market_cap = _coerce_field_numeric(row.get("market_cap_basic"))
    return safe_ratio(avg_value, market_cap)


def _classify_signal(
    flow_residual_pct: float | None,
    relvol_delta: float | None,
    *,
    threshold_pct: float = FLOW_RESIDUAL_THRESHOLD_PCT,
) -> str:
    if flow_residual_pct is None:
        return "neutral"
    if flow_residual_pct >= threshold_pct:
        if relvol_delta is not None and relvol_delta < -0.25:
            return "neutral"
        return "inflow"
    if flow_residual_pct <= -threshold_pct:
        return "outflow"
    return "neutral"


def _resolve_share_count(row: Mapping[str, Any]) -> float | None:
    for field_name in SHARE_COUNT_FIELD_CANDIDATES:
        value = _coerce_field_numeric(row.get(field_name))
        if value is not None and value > 0:
            return value
    close = _coerce_field_numeric(row.get("close"))
    market_cap = _coerce_field_numeric(row.get("market_cap_basic"))
    if close is not None and close > 0 and market_cap is not None:
        return market_cap / close
    return None


def _resolve_chaikin_mf(row: Mapping[str, Any]) -> float | None:
    for field_name in CHAIKIN_FIELD_CANDIDATES:
        value = _coerce_field_numeric(row.get(field_name))
        if value is not None:
            return value
    return None


def _classify_data_quality(
    *,
    price_return_pct: float | None,
    flow_residual_pct: float | None,
    base_close: float | None,
    compare_close: float | None,
    base_mcap: float | None,
    compare_mcap: float | None,
    config: FlowRefinementConfig,
) -> str:
    if any(
        value is None
        for value in (price_return_pct, flow_residual_pct, base_close, compare_close, base_mcap, compare_mcap)
    ):
        return "missing_inputs"
    assert price_return_pct is not None
    assert flow_residual_pct is not None
    if abs(price_return_pct) > config.max_abs_price_return_pct:
        return "outlier_price"
    if abs(flow_residual_pct) > config.max_abs_flow_residual_pct:
        return "outlier_residual"
    return "ok"


def _classify_flow_driver(
    *,
    share_change_pct: float | None,
    flow_residual_pct: float | None,
    relvol_delta: float | None,
    chaikin_mf_1m: float | None,
    buyback_yield: float | None,
    share_buyback_ratio_fq: float | None,
    config: FlowRefinementConfig,
) -> str:
    if flow_residual_pct is None:
        return "unknown"
    share_delta = share_change_pct if share_change_pct is not None else 0.0
    if (
        abs(share_delta) < config.price_only_threshold_pct
        and abs(flow_residual_pct) < config.price_only_threshold_pct
    ):
        return "price_only"
    buyback_signal = (
        (buyback_yield is not None and buyback_yield >= config.buyback_yield_threshold)
        or (
            share_buyback_ratio_fq is not None
            and share_buyback_ratio_fq >= config.share_buyback_ratio_threshold
        )
    )
    if share_delta <= config.share_change_buyback_pct and buyback_signal:
        return "buyback_like"
    if share_delta >= config.share_change_dilution_pct and flow_residual_pct > 0:
        return "dilution_like"
    if (
        flow_residual_pct > 0
        and (relvol_delta is None or relvol_delta >= 0)
        and chaikin_mf_1m is not None
        and chaikin_mf_1m > config.chaikin_confirm_threshold
    ):
        return "demand_like"
    if flow_residual_pct < 0 and chaikin_mf_1m is not None and chaikin_mf_1m < 0:
        return "mixed"
    return "mixed"


def _classify_signal_context(
    *,
    signal: str,
    flow_driver: str,
    data_quality: str,
    relvol_delta: float | None,
    chaikin_mf_1m: float | None,
    config: FlowRefinementConfig,
) -> str:
    if data_quality != "ok":
        return "excluded_outlier"
    if signal == "inflow":
        if flow_driver == "buyback_like":
            return "inflow_buyback"
        if (
            chaikin_mf_1m is not None
            and chaikin_mf_1m > config.chaikin_confirm_threshold
            and (relvol_delta is None or relvol_delta >= config.relvol_confirm_floor)
        ):
            return "inflow_confirmed"
        return "inflow_unconfirmed"
    if signal == "outflow":
        if (
            chaikin_mf_1m is not None
            and chaikin_mf_1m < -config.chaikin_confirm_threshold
        ):
            return "outflow_confirmed"
        return "outflow_unconfirmed"
    return "neutral"


def _compute_flow_residual_usd_adjusted(
    flow_residual_usd: float | None,
    data_quality: str,
) -> float | None:
    if flow_residual_usd is None:
        return None
    if data_quality != "ok":
        return 0.0
    return flow_residual_usd


def compute_symbol_flow_record(
    base_row: Mapping[str, Any],
    compare_row: Mapping[str, Any],
    *,
    threshold_pct: float = FLOW_RESIDUAL_THRESHOLD_PCT,
    refinement: FlowRefinementConfig | None = None,
) -> SymbolFlowRecord | None:
    config = refinement or FlowRefinementConfig()
    symbol = str(compare_row.get("symbol") or base_row.get("symbol") or "").strip()
    if not symbol:
        return None

    base_close = _coerce_field_numeric(base_row.get("close"))
    compare_close = _coerce_field_numeric(compare_row.get("close"))
    base_mcap = _coerce_field_numeric(base_row.get("market_cap_basic"))
    compare_mcap = _coerce_field_numeric(compare_row.get("market_cap_basic"))

    price_return_pct = _percent_change(compare_close, base_close)
    mcap_delta_pct = _percent_change(compare_mcap, base_mcap)
    flow_residual_pct = (
        None
        if price_return_pct is None or mcap_delta_pct is None
        else mcap_delta_pct - price_return_pct
    )
    flow_residual_usd = None
    price_effect_usd = None
    share_effect_usd = None
    if (
        compare_mcap is not None
        and base_mcap is not None
        and base_close is not None
        and compare_close is not None
        and base_close != 0
    ):
        price_effect_usd = base_mcap * ((compare_close / base_close) - 1.0)
        flow_residual_usd = compare_mcap - base_mcap * (compare_close / base_close)
        share_effect_usd = flow_residual_usd
    elif flow_residual_pct is not None and compare_mcap is not None:
        flow_residual_usd = compare_mcap * (flow_residual_pct / 100.0)
        share_effect_usd = flow_residual_usd

    base_shares = _resolve_share_count(base_row)
    compare_shares = _resolve_share_count(compare_row)
    share_change_pct = _percent_change(compare_shares, base_shares)

    base_relvol = _coerce_field_numeric(base_row.get("relative_volume_10d_calc"))
    compare_relvol = _coerce_field_numeric(compare_row.get("relative_volume_10d_calc"))
    relvol_delta = (
        None
        if base_relvol is None or compare_relvol is None
        else compare_relvol - base_relvol
    )
    base_participation = _participation_ratio(base_row)
    compare_participation = _participation_ratio(compare_row)
    participation_delta = (
        None
        if base_participation is None or compare_participation is None
        else compare_participation - base_participation
    )

    base_vol_w = _coerce_field_numeric(base_row.get("Volatility.W"))
    compare_vol_w = _coerce_field_numeric(compare_row.get("Volatility.W"))
    volatility_w_delta = (
        None
        if base_vol_w is None or compare_vol_w is None
        else compare_vol_w - base_vol_w
    )

    buyback_yield = _coerce_field_numeric(compare_row.get("buyback_yield"))
    share_buyback_ratio_fq = _coerce_field_numeric(compare_row.get("share_buyback_ratio_fq"))
    chaikin_mf_1m = _resolve_chaikin_mf(compare_row)

    signal = _classify_signal(flow_residual_pct, relvol_delta, threshold_pct=threshold_pct)
    data_quality = _classify_data_quality(
        price_return_pct=price_return_pct,
        flow_residual_pct=flow_residual_pct,
        base_close=base_close,
        compare_close=compare_close,
        base_mcap=base_mcap,
        compare_mcap=compare_mcap,
        config=config,
    )
    flow_driver = _classify_flow_driver(
        share_change_pct=share_change_pct,
        flow_residual_pct=flow_residual_pct,
        relvol_delta=relvol_delta,
        chaikin_mf_1m=chaikin_mf_1m,
        buyback_yield=buyback_yield,
        share_buyback_ratio_fq=share_buyback_ratio_fq,
        config=config,
    )
    signal_context = _classify_signal_context(
        signal=signal,
        flow_driver=flow_driver,
        data_quality=data_quality,
        relvol_delta=relvol_delta,
        chaikin_mf_1m=chaikin_mf_1m,
        config=config,
    )
    flow_residual_usd_adjusted = _compute_flow_residual_usd_adjusted(
        flow_residual_usd,
        data_quality,
    )

    return SymbolFlowRecord(
        symbol=symbol,
        company=get_symbol_name(compare_row),
        country=str(compare_row.get("country") or "Unknown"),
        sector=str(compare_row.get("sector") or "Unknown"),
        industry=str(compare_row.get("industry") or "Unknown"),
        market=str(compare_row.get("market") or "Unknown"),
        base_market_cap=base_mcap,
        compare_market_cap=compare_mcap,
        price_return_pct=price_return_pct,
        mcap_delta_pct=mcap_delta_pct,
        flow_residual_pct=flow_residual_pct,
        flow_residual_usd=flow_residual_usd,
        relvol_delta=relvol_delta,
        participation_delta=participation_delta,
        perf_5d=_coerce_field_numeric(compare_row.get("Perf.5D")),
        volatility_w_delta=volatility_w_delta,
        signal=signal,
        share_change_pct=share_change_pct,
        price_effect_usd=price_effect_usd,
        share_effect_usd=share_effect_usd,
        buyback_yield=buyback_yield,
        share_buyback_ratio_fq=share_buyback_ratio_fq,
        chaikin_mf_1m=chaikin_mf_1m,
        flow_driver=flow_driver,
        data_quality=data_quality,
        signal_context=signal_context,
        flow_residual_usd_adjusted=flow_residual_usd_adjusted,
    )


def build_symbol_flow_records(
    base_rows: Sequence[Mapping[str, Any]],
    compare_rows: Sequence[Mapping[str, Any]],
    *,
    threshold_pct: float = FLOW_RESIDUAL_THRESHOLD_PCT,
    refinement: FlowRefinementConfig | None = None,
) -> list[SymbolFlowRecord]:
    records: list[SymbolFlowRecord] = []
    for base_row, compare_row in align_snapshot_pairs(base_rows, compare_rows):
        record = compute_symbol_flow_record(
            base_row,
            compare_row,
            threshold_pct=threshold_pct,
            refinement=refinement,
        )
        if record is not None:
            records.append(record)
    return records


def _median_or_none(values: Sequence[float]) -> float | None:
    numeric = [value for value in values if value is not None and math.isfinite(value)]
    if not numeric:
        return None
    return float(median(numeric))


def _cap_weighted_mean(
    values: Sequence[float | None],
    weights: Sequence[float | None],
) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for value, weight in zip(values, weights):
        if value is None or weight is None or weight <= 0:
            continue
        numerator += value * weight
        denominator += weight
    if denominator <= 0:
        return None
    return numerator / denominator


def aggregate_group_flows(
    symbol_records: Sequence[SymbolFlowRecord],
    group_field: str,
) -> list[GroupFlowRecord]:
    grouped: dict[str, list[SymbolFlowRecord]] = {}
    for record in symbol_records:
        group_value = getattr(record, group_field, None)
        if group_value is None:
            group_name = "Unknown"
        else:
            group_name = str(group_value).strip() or "Unknown"
        grouped.setdefault(group_name, []).append(record)

    group_records: list[GroupFlowRecord] = []
    for group_name, rows in grouped.items():
        flow_usd_values = [
            row.flow_residual_usd for row in rows if row.flow_residual_usd is not None
        ]
        net_flow_usd = sum(flow_usd_values) if flow_usd_values else 0.0
        cap_weighted_flow_pct = _cap_weighted_mean(
            [row.flow_residual_pct for row in rows],
            [row.compare_market_cap for row in rows],
        )
        median_flow_residual_pct = _median_or_none(
            [row.flow_residual_pct for row in rows if row.flow_residual_pct is not None]
        )
        relvol_deltas = [
            row.relvol_delta for row in rows if row.relvol_delta is not None
        ]
        participation_intensity = (
            sum(relvol_deltas) / len(relvol_deltas) if relvol_deltas else None
        )
        adjusted_values = [
            row.flow_residual_usd_adjusted
            for row in rows
            if row.flow_residual_usd_adjusted is not None
        ]
        net_flow_usd_adjusted = sum(adjusted_values) if adjusted_values else 0.0
        group_records.append(
            GroupFlowRecord(
                group_name=group_name,
                group_field=group_field,
                universe_count=len(rows),
                inflow_count=sum(1 for row in rows if row.signal == "inflow"),
                outflow_count=sum(1 for row in rows if row.signal == "outflow"),
                neutral_count=sum(1 for row in rows if row.signal == "neutral"),
                net_flow_usd=net_flow_usd,
                cap_weighted_flow_pct=cap_weighted_flow_pct,
                median_flow_residual_pct=median_flow_residual_pct,
                participation_intensity=participation_intensity,
                net_flow_usd_adjusted=net_flow_usd_adjusted,
                confirmed_inflow_count=sum(
                    1 for row in rows if row.signal_context == "inflow_confirmed"
                ),
                confirmed_outflow_count=sum(
                    1 for row in rows if row.signal_context == "outflow_confirmed"
                ),
                outlier_count=sum(1 for row in rows if row.data_quality != "ok"),
                buyback_like_count=sum(1 for row in rows if row.flow_driver == "buyback_like"),
            )
        )
    return group_records


def _attach_group_flow_z_scores(group_records: list[GroupFlowRecord]) -> None:
    net_values = [record.net_flow_usd for record in group_records]
    if not net_values:
        return
    center = float(median(net_values))
    spread = median_absolute_deviation(net_values, center)
    if spread <= 0:
        spread = max(abs(value - center) for value in net_values) or 1.0
    for record in group_records:
        record.flow_z_score = (record.net_flow_usd - center) / spread


def summarize_universe_flows(
    symbol_records: Sequence[SymbolFlowRecord],
    industry_groups: Sequence[GroupFlowRecord],
    *,
    rotation_ratio_high: float = ROTATION_RATIO_HIGH,
    universe_net_threshold_usd: float = UNIVERSE_NET_FLOW_USD_THRESHOLD,
) -> UniverseFlowSummary:
    universe_net_flow_usd = sum(
        record.flow_residual_usd
        for record in symbol_records
        if record.flow_residual_usd is not None
    )
    gross_flow_usd = sum(abs(group.net_flow_usd) for group in industry_groups)
    rotation_ratio = gross_flow_usd / (2.0 * abs(universe_net_flow_usd) + 1.0)

    if rotation_ratio >= rotation_ratio_high:
        verdict = "rotation_dominant"
    elif universe_net_flow_usd >= universe_net_threshold_usd:
        verdict = "net_inflow"
    elif universe_net_flow_usd <= -universe_net_threshold_usd:
        verdict = "net_outflow"
    else:
        verdict = "mixed_neutral"

    return UniverseFlowSummary(
        universe_count=len(symbol_records),
        universe_net_flow_usd=universe_net_flow_usd,
        gross_flow_usd=gross_flow_usd,
        rotation_ratio=rotation_ratio,
        verdict=verdict,
        inflow_count=sum(1 for record in symbol_records if record.signal == "inflow"),
        outflow_count=sum(1 for record in symbol_records if record.signal == "outflow"),
        neutral_count=sum(1 for record in symbol_records if record.signal == "neutral"),
        universe_net_flow_usd_adjusted=sum(
            record.flow_residual_usd_adjusted or 0.0 for record in symbol_records
        ),
        outlier_count=sum(1 for record in symbol_records if record.data_quality != "ok"),
        buyback_like_count=sum(
            1 for record in symbol_records if record.flow_driver == "buyback_like"
        ),
        confirmed_inflow_count=sum(
            1 for record in symbol_records if record.signal_context == "inflow_confirmed"
        ),
        confirmed_outflow_count=sum(
            1 for record in symbol_records if record.signal_context == "outflow_confirmed"
        ),
    )


def build_sector_rotation_pairs(
    sector_groups: Sequence[GroupFlowRecord],
    *,
    limit: int = 5,
) -> list[SectorRotationPair]:
    donors = sorted(
        [group for group in sector_groups if group.net_flow_usd < 0],
        key=lambda group: group.net_flow_usd,
    )
    receivers = sorted(
        [group for group in sector_groups if group.net_flow_usd > 0],
        key=lambda group: group.net_flow_usd,
        reverse=True,
    )
    pairs: list[SectorRotationPair] = []
    for donor in donors:
        for receiver in receivers:
            implied_rotation_usd = min(abs(donor.net_flow_usd), receiver.net_flow_usd)
            if implied_rotation_usd <= 0:
                continue
            pairs.append(
                SectorRotationPair(
                    donor_sector=donor.group_name,
                    receiver_sector=receiver.group_name,
                    implied_rotation_usd=implied_rotation_usd,
                    donor_net_flow_usd=donor.net_flow_usd,
                    receiver_net_flow_usd=receiver.net_flow_usd,
                )
            )
    pairs.sort(key=lambda pair: pair.implied_rotation_usd, reverse=True)
    return pairs[:limit]


def _sanitize_industry_folder_name(industry: str) -> str:
    safe_name = str(industry).strip()
    for old_value, new_value in {
        "/": " - ",
        "\\": " - ",
        ":": " - ",
        "*": "_",
        "?": "_",
        '"': "_",
        "<": "_",
        ">": "_",
        "|": "_",
    }.items():
        safe_name = safe_name.replace(old_value, new_value)
    return safe_name.rstrip(". ") or slugify(industry)


def _truncate_label(value: Any, width: int) -> str:
    text = str(value or "N/A")
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return f"{text[: width - 3]}..."


def _format_group_line(record: GroupFlowRecord) -> str:
    return (
        f"{record.group_name:<28} "
        f"net={format_number(record.net_flow_usd, 0):>14} "
        f"net_adj={format_number(record.net_flow_usd_adjusted, 0):>14} "
        f"z={format_number(record.flow_z_score, 2):>7} "
        f"in/out/neu={record.inflow_count:>3}/{record.outflow_count:>3}/{record.neutral_count:>3} "
        f"conf={record.confirmed_inflow_count:>3}/{record.confirmed_outflow_count:<3} "
        f"outlier={record.outlier_count:>3} "
        f"med_res={format_signed_percent(record.median_flow_residual_pct):>8} "
        f"relvol_d={format_number(record.participation_intensity, 2):>7}"
    )


def _format_stock_flow_line(record: SymbolFlowRecord) -> str:
    bare = _bare_symbol(record.symbol)
    return (
        f"{bare:<10} {_truncate_label(record.company, 18):<18} "
        f"{format_market_cap(record.compare_market_cap):>8} "
        f"{format_signed_percent(record.price_return_pct):>7} "
        f"{format_signed_percent(record.flow_residual_pct):>7} "
        f"{format_signed_percent(record.share_change_pct):>7} "
        f"{format_number(record.relvol_delta, 2):>6} "
        f"{record.flow_driver[:12]:<12} "
        f"{record.signal_context[:18]:<18} "
        f"{record.signal:<8}"
    )


def _select_material_groups(
    group_records: Sequence[GroupFlowRecord],
    *,
    limit: int = MATERIAL_GROUP_LIMIT,
) -> list[GroupFlowRecord]:
    ranked = sorted(
        group_records,
        key=lambda record: abs(record.net_flow_usd_adjusted),
        reverse=True,
    )
    return ranked[:limit]


def _top_inflow_groups(
    group_records: Sequence[GroupFlowRecord],
    *,
    limit: int = TOP_HIGHLIGHT_COUNT,
) -> list[GroupFlowRecord]:
    positive = sorted(
        [record for record in group_records if record.net_flow_usd > 0],
        key=lambda record: record.net_flow_usd,
        reverse=True,
    )
    if positive:
        return positive[:limit]
    return sorted(
        group_records,
        key=lambda record: abs(record.net_flow_usd),
        reverse=True,
    )[:limit]


def _top_outflow_groups(
    group_records: Sequence[GroupFlowRecord],
    *,
    limit: int = TOP_HIGHLIGHT_COUNT,
) -> list[GroupFlowRecord]:
    negative = sorted(
        [record for record in group_records if record.net_flow_usd < 0],
        key=lambda record: record.net_flow_usd,
    )
    if negative:
        return negative[:limit]
    return sorted(
        group_records,
        key=lambda record: abs(record.net_flow_usd),
        reverse=True,
    )[:limit]


def _write_overview_log(
    output_root: Path,
    *,
    base_day_label: str,
    compare_day_label: str,
    universe_summary: UniverseFlowSummary,
    industry_groups: Sequence[GroupFlowRecord],
    country_groups: Sequence[GroupFlowRecord],
    universe_filter: Mapping[str, Any] | None,
) -> Path:
    log_file = output_root / "overview.log"
    reset_log_file(log_file)
    log_to_file(log_file, build_report_title("Market flow screening overview"))
    log_to_file(log_file, "=" * 120)
    log_to_file(log_file, f"Window: {base_day_label} -> {compare_day_label}")
    log_to_file(log_file, f"Universe filter: {json.dumps(universe_filter or {}, sort_keys=True)}")
    log_to_file(log_file, "")
    log_to_file(log_file, "Universe verdict")
    log_to_file(log_file, "-" * 120)
    log_to_file(
        log_file,
        f"Symbols paired: {universe_summary.universe_count} | "
        f"verdict={universe_summary.verdict} | "
        f"net_flow_usd={format_number(universe_summary.universe_net_flow_usd, 0)} | "
        f"gross_flow_usd={format_number(universe_summary.gross_flow_usd, 0)} | "
        f"rotation_ratio={format_number(universe_summary.rotation_ratio, 2)}",
    )
    log_to_file(
        log_file,
        f"Signal mix: inflow={universe_summary.inflow_count} | "
        f"outflow={universe_summary.outflow_count} | "
        f"neutral={universe_summary.neutral_count}",
    )
    log_to_file(log_file, "")
    log_to_file(log_file, "Quality-adjusted view")
    log_to_file(log_file, "-" * 120)
    log_to_file(
        log_file,
        f"net_flow_usd_adjusted={format_number(universe_summary.universe_net_flow_usd_adjusted, 0)} | "
        f"outliers_excluded={universe_summary.outlier_count} | "
        f"buyback_like={universe_summary.buyback_like_count} | "
        f"confirmed_inflow/outflow="
        f"{universe_summary.confirmed_inflow_count}/{universe_summary.confirmed_outflow_count}",
    )
    log_to_file(log_file, "")
    log_to_file(log_file, "Top industry net flows")
    log_to_file(log_file, "-" * 120)
    log_to_file(log_file, "Inflow leaders:")
    for record in _top_inflow_groups(industry_groups):
        log_to_file(log_file, f"  {_format_group_line(record)}")
    log_to_file(log_file, "Outflow leaders:")
    for record in _top_outflow_groups(industry_groups):
        log_to_file(log_file, f"  {_format_group_line(record)}")
    log_to_file(log_file, "")
    log_to_file(log_file, "Top country net flows")
    log_to_file(log_file, "-" * 120)
    log_to_file(log_file, "Inflow leaders:")
    for record in _top_inflow_groups(country_groups):
        log_to_file(log_file, f"  {_format_group_line(record)}")
    log_to_file(log_file, "Outflow leaders:")
    for record in _top_outflow_groups(country_groups):
        log_to_file(log_file, f"  {_format_group_line(record)}")
    return log_file


def _write_industry_logs(
    output_root: Path,
    symbol_records: Sequence[SymbolFlowRecord],
    industry_groups: Sequence[GroupFlowRecord],
) -> list[Path]:
    industries_dir = output_root / "industries"
    written: list[Path] = []
    material_groups = _select_material_groups(industry_groups)
    records_by_industry: dict[str, list[SymbolFlowRecord]] = {}
    for record in symbol_records:
        records_by_industry.setdefault(record.industry, []).append(record)

    header = (
        f"{'Ticker':<10} {'Company':<18} {'MCap':>8} {'PriceΔ':>7} {'FlowRes':>7} "
        f"{'ShareΔ':>7} {'RelVol':>6} {'Driver':<12} {'Context':<18} {'Signal':<8}"
    )
    for group in material_groups:
        industry_name = group.group_name
        industry_rows = records_by_industry.get(industry_name, [])
        ranked_rows = sorted(
            industry_rows,
            key=lambda row: (
                abs(row.flow_residual_usd_adjusted or 0.0),
                abs(row.flow_residual_usd or 0.0),
            ),
            reverse=True,
        )[:MAX_STOCKS_PER_INDUSTRY]
        if not ranked_rows:
            continue
        log_file = industries_dir / f"{_sanitize_industry_folder_name(industry_name)}.log"
        reset_log_file(log_file)
        log_to_file(log_file, build_report_title(f"Industry flow | {industry_name}"))
        log_to_file(log_file, "-" * 120)
        log_to_file(log_file, _format_group_line(group))
        log_to_file(
            log_file,
            "Driver/Context: flow_driver label + confirmation (inflow_confirmed, inflow_buyback, excluded_outlier, ...).",
        )
        log_to_file(log_file, "")
        log_to_file(log_file, header)
        for row in ranked_rows:
            log_to_file(log_file, _format_stock_flow_line(row))
        written.append(log_file)
    return written


def _write_group_csv(
    path: Path,
    group_records: Sequence[GroupFlowRecord],
) -> None:
    headers = [
        "group_name",
        "group_field",
        "universe_count",
        "inflow_count",
        "outflow_count",
        "neutral_count",
        "net_flow_usd",
        "cap_weighted_flow_pct",
        "median_flow_residual_pct",
        "participation_intensity",
        "flow_z_score",
        "net_flow_usd_adjusted",
        "confirmed_inflow_count",
        "confirmed_outflow_count",
        "outlier_count",
        "buyback_like_count",
    ]
    rows = [
        [
            record.group_name,
            record.group_field,
            str(record.universe_count),
            str(record.inflow_count),
            str(record.outflow_count),
            str(record.neutral_count),
            format_number(record.net_flow_usd, 4),
            format_number(record.cap_weighted_flow_pct, 4),
            format_number(record.median_flow_residual_pct, 4),
            format_number(record.participation_intensity, 4),
            format_number(record.flow_z_score, 4),
            format_number(record.net_flow_usd_adjusted, 4),
            str(record.confirmed_inflow_count),
            str(record.confirmed_outflow_count),
            str(record.outlier_count),
            str(record.buyback_like_count),
        ]
        for record in group_records
    ]
    log_rows_to_csv(path, headers, rows)


def _write_symbol_csv(path: Path, symbol_records: Sequence[SymbolFlowRecord]) -> None:
    headers = [
        "symbol",
        "company",
        "country",
        "sector",
        "industry",
        "market",
        "base_market_cap",
        "compare_market_cap",
        "price_return_pct",
        "mcap_delta_pct",
        "flow_residual_pct",
        "flow_residual_usd",
        "relvol_delta",
        "participation_delta",
        "perf_5d",
        "volatility_w_delta",
        "signal",
        "share_change_pct",
        "price_effect_usd",
        "share_effect_usd",
        "buyback_yield",
        "share_buyback_ratio_fq",
        "chaikin_mf_1m",
        "flow_driver",
        "data_quality",
        "signal_context",
        "flow_residual_usd_adjusted",
    ]
    rows = [
        [
            record.symbol,
            record.company,
            record.country,
            record.sector,
            record.industry,
            record.market,
            format_number(record.base_market_cap, 2),
            format_number(record.compare_market_cap, 2),
            format_number(record.price_return_pct, 4),
            format_number(record.mcap_delta_pct, 4),
            format_number(record.flow_residual_pct, 4),
            format_number(record.flow_residual_usd, 2),
            format_number(record.relvol_delta, 4),
            format_number(record.participation_delta, 6),
            format_number(record.perf_5d, 4),
            format_number(record.volatility_w_delta, 4),
            record.signal,
            format_number(record.share_change_pct, 4),
            format_number(record.price_effect_usd, 2),
            format_number(record.share_effect_usd, 2),
            format_number(record.buyback_yield, 6),
            format_number(record.share_buyback_ratio_fq, 6),
            format_number(record.chaikin_mf_1m, 6),
            record.flow_driver,
            record.data_quality,
            record.signal_context,
            format_number(record.flow_residual_usd_adjusted, 2),
        ]
        for record in symbol_records
    ]
    log_rows_to_csv(path, headers, rows)


def _write_run_manifest(
    path: Path,
    *,
    run_id: str,
    base_day_label: str,
    compare_day_label: str,
    base_database_path: Path,
    compare_database_path: Path,
    universe_summary: UniverseFlowSummary,
    universe_filter: Mapping[str, Any] | None,
) -> None:
    payload = {
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_day_label": base_day_label,
        "compare_day_label": compare_day_label,
        "base_database_path": base_database_path.as_posix(),
        "compare_database_path": compare_database_path.as_posix(),
        "suite_name": ALL_FIELDS_SUITE_NAME,
        "universe_filter": universe_filter,
        "universe_summary": asdict(universe_summary),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def analyze_macro_breadth_snapshot(
    compare_rows: Sequence[Mapping[str, Any]],
    *,
    output_root: Path,
    compare_day_label: str,
) -> Path:
    log_file = output_root / "macro" / "breadth_snapshot.log"
    reset_log_file(log_file)
    log_to_file(log_file, build_report_title(f"Macro breadth | {compare_day_label}"))
    log_to_file(log_file, "-" * 120)

    perf_values = [_coerce_field_numeric(row.get("Perf.5D")) for row in compare_rows]
    perf_numeric = [value for value in perf_values if value is not None]
    advances = sum(1 for value in perf_numeric if value > 0)
    declines = sum(1 for value in perf_numeric if value < 0)
    flat = sum(1 for value in perf_numeric if value == 0)

    cap_advances = 0.0
    cap_declines = 0.0
    for row in compare_rows:
        perf = _coerce_field_numeric(row.get("Perf.5D"))
        market_cap = _coerce_field_numeric(row.get("market_cap_basic"))
        if perf is None or market_cap is None:
            continue
        if perf > 0:
            cap_advances += market_cap
        elif perf < 0:
            cap_declines += market_cap

    above_sma50 = 0
    above_sma200 = 0
    trend_total = 0
    for row in compare_rows:
        close = _coerce_field_numeric(row.get("close"))
        sma50 = _coerce_field_numeric(row.get("SMA50"))
        sma200 = _coerce_field_numeric(row.get("SMA200"))
        if close is None:
            continue
        if sma50 is not None:
            trend_total += 1
            if close >= sma50:
                above_sma50 += 1
        if sma200 is not None:
            if close >= sma200:
                above_sma200 += 1

    industry_perf: dict[str, list[float]] = {}
    for row in compare_rows:
        industry = str(row.get("industry") or "Unknown")
        perf_1m = _coerce_field_numeric(row.get("Perf.1M"))
        if perf_1m is None:
            continue
        industry_perf.setdefault(industry, []).append(perf_1m)
    industry_medians = {
        industry: float(median(values))
        for industry, values in industry_perf.items()
        if values
    }
    dispersion = None
    if len(industry_medians) >= 2:
        median_values = list(industry_medians.values())
        center = float(median(median_values))
        dispersion = math.sqrt(
            sum((value - center) ** 2 for value in median_values) / len(median_values)
        )

    log_to_file(
        log_file,
        f"Universe size: {len(compare_rows)} | 5D advances/declines/flat: "
        f"{advances}/{declines}/{flat}",
    )
    log_to_file(
        log_file,
        f"Cap-weighted 5D breadth: advancing_mcap={format_market_cap(cap_advances)} | "
        f"declining_mcap={format_market_cap(cap_declines)}",
    )
    if trend_total > 0:
        log_to_file(
            log_file,
            f"Above SMA50: {above_sma50}/{trend_total} "
            f"({(above_sma50 / trend_total) * 100:.1f}%) | "
            f"Above SMA200: {above_sma200}/{trend_total} "
            f"({(above_sma200 / trend_total) * 100:.1f}%)",
        )
    log_to_file(
        log_file,
        f"Industry Perf.1M dispersion (std of industry medians): "
        f"{format_number(dispersion, 2)} across {len(industry_medians)} industries",
    )
    log_to_file(log_file, "")
    log_to_file(log_file, "Narrative")
    if advances > declines * 1.2:
        tone = "Risk-on breadth: more advancers than decliners on Perf.5D."
    elif declines > advances * 1.2:
        tone = "Risk-off breadth: decliners dominate short-term performance."
    else:
        tone = "Mixed breadth: neither side has a decisive 5D edge."
    if dispersion is not None and dispersion >= 8:
        tone += " Industry performance is dispersed — stock picking matters."
    elif dispersion is not None and dispersion <= 3:
        tone += " Industry performance is clustered — macro factor likely dominant."
    log_to_file(log_file, tone)
    return log_file


def analyze_industry_participation_leaders(
    compare_rows: Sequence[Mapping[str, Any]],
    *,
    output_root: Path,
    compare_day_label: str,
    top_n: int = 20,
) -> tuple[Path, list[IndustryParticipationRecord]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in compare_rows:
        industry = str(row.get("industry") or "Unknown")
        grouped.setdefault(industry, []).append(row)

    raw_scores: list[tuple[str, float, float | None, float | None, int]] = []
    for industry, rows in grouped.items():
        relvol_values = [
            value
            for value in (_coerce_field_numeric(row.get("relative_volume_10d_calc")) for row in rows)
            if value is not None
        ]
        perf_values = [
            value
            for value in (_coerce_field_numeric(row.get("Perf.1M")) for row in rows)
            if value is not None
        ]
        median_relvol = _median_or_none(relvol_values)
        median_perf = _median_or_none(perf_values)
        if median_relvol is None or median_perf is None:
            continue
        score = median_relvol * median_perf
        raw_scores.append((industry, score, median_relvol, median_perf, len(rows)))

    score_values = [item[1] for item in raw_scores]
    center = float(median(score_values)) if score_values else 0.0
    spread = median_absolute_deviation(score_values, center) if score_values else 1.0
    if spread <= 0:
        spread = 1.0

    participation_records: list[IndustryParticipationRecord] = []
    for industry, score, median_relvol, median_perf, stock_count in raw_scores:
        z_score = (score - center) / spread
        industry_rows = grouped[industry]
        exemplars = sorted(
            industry_rows,
            key=lambda row: (
                (_coerce_field_numeric(row.get("relative_volume_10d_calc")) or 0.0)
                * (_coerce_field_numeric(row.get("Perf.1M")) or 0.0)
            ),
            reverse=True,
        )[:MAX_STOCKS_PER_INDUSTRY]
        participation_records.append(
            IndustryParticipationRecord(
                industry=industry,
                participation_score=score,
                participation_z_score=z_score,
                median_relvol=median_relvol,
                median_perf_1m=median_perf,
                stock_count=stock_count,
                exemplar_symbols=[
                    _bare_symbol(str(row.get("symbol") or "")) for row in exemplars
                ],
            )
        )

    participation_records.sort(
        key=lambda record: record.participation_score,
        reverse=True,
    )
    leaders = participation_records[:top_n]

    log_file = output_root / "macro" / "industry_participation_leaders.log"
    reset_log_file(log_file)
    log_to_file(
        log_file,
        build_report_title(f"Industry participation leaders | {compare_day_label}"),
    )
    log_to_file(log_file, "-" * 120)
    log_to_file(
        log_file,
        f"{'Industry':<34} {'Score':>8} {'Z':>7} {'MedRelVol':>10} {'MedPerf1M':>10} "
        f"{'Count':>6} Exemplars",
    )
    for record in leaders:
        exemplar_text = ", ".join(record.exemplar_symbols[:MAX_STOCKS_PER_INDUSTRY])
        log_to_file(
            log_file,
            f"{_truncate_label(record.industry, 34):<34} "
            f"{format_number(record.participation_score, 2):>8} "
            f"{format_number(record.participation_z_score, 2):>7} "
            f"{format_number(record.median_relvol, 2):>10} "
            f"{format_signed_percent(record.median_perf_1m):>10} "
            f"{record.stock_count:>6} {exemplar_text}",
        )
    return log_file, leaders


def analyze_regional_exposure_shift(
    symbol_records: Sequence[SymbolFlowRecord],
    country_groups: Sequence[GroupFlowRecord],
    *,
    output_root: Path,
    base_day_label: str,
    compare_day_label: str,
) -> Path:
    log_file = output_root / "macro" / "country_flow.log"
    reset_log_file(log_file)
    log_to_file(
        log_file,
        build_report_title(
            f"Regional exposure shift | {base_day_label} -> {compare_day_label}"
        ),
    )
    log_to_file(log_file, "-" * 120)
    ranked = sorted(country_groups, key=lambda group: group.net_flow_usd, reverse=True)
    log_to_file(log_file, "Country net flows (cap-weighted residual USD)")
    for group in ranked[:TOP_HIGHLIGHT_COUNT]:
        log_to_file(log_file, f"  {_format_group_line(group)}")
    log_to_file(log_file, "")
    log_to_file(log_file, "Country outflow leaders")
    for group in sorted(country_groups, key=lambda group: group.net_flow_usd)[:TOP_HIGHLIGHT_COUNT]:
        log_to_file(log_file, f"  {_format_group_line(group)}")

    exit_pressure: list[str] = []
    records_by_country: dict[str, list[SymbolFlowRecord]] = {}
    for record in symbol_records:
        records_by_country.setdefault(record.country, []).append(record)
    for group in country_groups:
        if group.net_flow_usd >= 0:
            continue
        country_rows = records_by_country.get(group.group_name, [])
        vol_deltas = [
            row.volatility_w_delta
            for row in country_rows
            if row.volatility_w_delta is not None
        ]
        if not vol_deltas:
            continue
        if float(median(vol_deltas)) > 0:
            exit_pressure.append(group.group_name)

    log_to_file(log_file, "")
    log_to_file(log_file, "Exit-pressure watch (outflow + rising Volatility.W)")
    if exit_pressure:
        for country in exit_pressure[:TOP_HIGHLIGHT_COUNT]:
            log_to_file(log_file, f"  - {country}")
    else:
        log_to_file(log_file, "  None flagged on current thresholds.")
    return log_file


def analyze_flow_quality_summary(
    symbol_records: Sequence[SymbolFlowRecord],
    industry_groups: Sequence[GroupFlowRecord],
    *,
    output_root: Path,
    base_day_label: str,
    compare_day_label: str,
) -> Path:
    log_file = output_root / "macro" / "flow_quality_summary.log"
    reset_log_file(log_file)
    log_to_file(
        log_file,
        build_report_title(
            f"Flow quality summary | {base_day_label} -> {compare_day_label}"
        ),
    )
    log_to_file(log_file, "-" * 120)

    total = len(symbol_records) or 1
    driver_counts: dict[str, int] = {}
    quality_counts: dict[str, int] = {}
    for record in symbol_records:
        driver_counts[record.flow_driver] = driver_counts.get(record.flow_driver, 0) + 1
        quality_counts[record.data_quality] = quality_counts.get(record.data_quality, 0) + 1

    log_to_file(log_file, "Flow driver mix")
    for driver, count in sorted(driver_counts.items(), key=lambda item: item[1], reverse=True):
        log_to_file(
            log_file,
            f"  {driver:<16} {count:>5} ({(count / total) * 100:5.1f}%)",
        )
    log_to_file(log_file, "")
    log_to_file(log_file, "Data quality mix")
    for quality, count in sorted(quality_counts.items(), key=lambda item: item[1], reverse=True):
        log_to_file(
            log_file,
            f"  {quality:<16} {count:>5} ({(count / total) * 100:5.1f}%)",
        )
    log_to_file(log_file, "")
    log_to_file(log_file, "Top confirmed inflow industries")
    confirmed_inflow = sorted(
        industry_groups,
        key=lambda group: group.confirmed_inflow_count,
        reverse=True,
    )
    for group in confirmed_inflow[:TOP_HIGHLIGHT_COUNT]:
        if group.confirmed_inflow_count <= 0:
            continue
        log_to_file(
            log_file,
            f"  {group.group_name:<28} confirmed_inflow={group.confirmed_inflow_count:>4} "
            f"net_adj={format_number(group.net_flow_usd_adjusted, 0):>14}",
        )
    log_to_file(log_file, "")
    log_to_file(log_file, "Top confirmed outflow industries")
    confirmed_outflow = sorted(
        industry_groups,
        key=lambda group: group.confirmed_outflow_count,
        reverse=True,
    )
    for group in confirmed_outflow[:TOP_HIGHLIGHT_COUNT]:
        if group.confirmed_outflow_count <= 0:
            continue
        log_to_file(
            log_file,
            f"  {group.group_name:<28} confirmed_outflow={group.confirmed_outflow_count:>4} "
            f"net_adj={format_number(group.net_flow_usd_adjusted, 0):>14}",
        )
    return log_file


def analyze_sector_rotation_map(
    sector_groups: Sequence[GroupFlowRecord],
    *,
    output_root: Path,
    base_day_label: str,
    compare_day_label: str,
    limit: int = 5,
) -> Path:
    log_file = output_root / "macro" / "sector_rotation.log"
    reset_log_file(log_file)
    log_to_file(
        log_file,
        build_report_title(f"Sector rotation map | {base_day_label} -> {compare_day_label}"),
    )
    log_to_file(log_file, "-" * 120)
    pairs = build_sector_rotation_pairs(sector_groups, limit=limit)
    if not pairs:
        log_to_file(log_file, "No meaningful donor/receiver sector pairs detected.")
        return log_file
    for index, pair in enumerate(pairs, start=1):
        log_to_file(
            log_file,
            (
                f"{index}. {pair.donor_sector} -> {pair.receiver_sector} | "
                f"implied_rotation_usd={format_number(pair.implied_rotation_usd, 0)} | "
                f"donor_net={format_number(pair.donor_net_flow_usd, 0)} | "
                f"receiver_net={format_number(pair.receiver_net_flow_usd, 0)}"
            ),
        )
    return log_file


def _resolve_day_pair(
    *,
    base_day_label: str | None,
    compare_day_label: str | None,
    all_fields_root: str | Path,
    lookback_days: int,
) -> tuple[str, str]:
    resolved_compare = (compare_day_label or "").strip() or discover_latest_day_label(
        all_fields_root
    )
    if not resolved_compare:
        raise FileNotFoundError(
            f"No daily all-fields DuckDB folders found under {all_fields_root}"
        )
    resolved_base = (base_day_label or "").strip() or discover_prior_day_label(
        resolved_compare,
        all_fields_root=all_fields_root,
        lookback_days=lookback_days,
    )
    if not resolved_base:
        raise FileNotFoundError(
            f"No prior trading-day DuckDB found before {resolved_compare} "
            f"within {lookback_days} days"
        )
    base_date = _try_parse_day_label(resolved_base)
    compare_date = _try_parse_day_label(resolved_compare)
    if base_date and compare_date and base_date >= compare_date:
        raise ValueError(
            f"base_day_label ({resolved_base}) must be strictly before "
            f"compare_day_label ({resolved_compare})"
        )
    return resolved_base, resolved_compare


def _build_refinement_config(
    *,
    max_abs_price_return_pct: float | None = None,
    max_abs_flow_residual_pct: float | None = None,
    buyback_yield_threshold: float | None = None,
    share_buyback_ratio_threshold: float | None = None,
) -> FlowRefinementConfig:
    return FlowRefinementConfig(
        max_abs_price_return_pct=max_abs_price_return_pct or MAX_ABS_PRICE_RETURN_PCT,
        max_abs_flow_residual_pct=max_abs_flow_residual_pct or MAX_ABS_FLOW_RESIDUAL_PCT,
        buyback_yield_threshold=buyback_yield_threshold or BUYBACK_YIELD_THRESHOLD,
        share_buyback_ratio_threshold=(
            share_buyback_ratio_threshold or SHARE_BUYBACK_RATIO_THRESHOLD
        ),
    )


def run_capital_flow_screening_suite(
    *,
    base_day_label: str | None = None,
    compare_day_label: str | None = None,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    output_root: str | Path | None = None,
    min_market_cap_usd: float | None = 500_000_000,
    max_market_cap_usd: float | None = None,
    markets: Sequence[str] | None = None,
    universe_filter: Mapping[str, Any] | None = None,
    lookback_days: int = 10,
    run_lifecycle_id: str | None = None,
    threshold_pct: float = FLOW_RESIDUAL_THRESHOLD_PCT,
    max_abs_price_return_pct: float | None = None,
    max_abs_flow_residual_pct: float | None = None,
    buyback_yield_threshold: float | None = None,
    share_buyback_ratio_threshold: float | None = None,
) -> MarketFlowScreeningResult:
    resolved_base, resolved_compare = _resolve_day_pair(
        base_day_label=base_day_label,
        compare_day_label=compare_day_label,
        all_fields_root=all_fields_root,
        lookback_days=lookback_days,
    )
    resolved_universe_filter = universe_filter or move_prediction_universe_filter(
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        markets=markets,
    )
    normalize_all_fields_universe_filter(resolved_universe_filter)

    base_database = resolve_day_database(resolved_base, all_fields_root)
    compare_database = resolve_day_database(resolved_compare, all_fields_root)
    base_rows = load_snapshot_rows(
        base_database,
        universe_filter=resolved_universe_filter,
    )
    compare_rows = load_snapshot_rows(
        compare_database,
        universe_filter=resolved_universe_filter,
    )
    symbol_records = build_symbol_flow_records(
        base_rows,
        compare_rows,
        threshold_pct=threshold_pct,
        refinement=_build_refinement_config(
            max_abs_price_return_pct=max_abs_price_return_pct,
            max_abs_flow_residual_pct=max_abs_flow_residual_pct,
            buyback_yield_threshold=buyback_yield_threshold,
            share_buyback_ratio_threshold=share_buyback_ratio_threshold,
        ),
    )
    industry_groups = aggregate_group_flows(symbol_records, "industry")
    country_groups = aggregate_group_flows(symbol_records, "country")
    sector_groups = aggregate_group_flows(symbol_records, "sector")
    _attach_group_flow_z_scores(industry_groups)
    _attach_group_flow_z_scores(country_groups)
    _attach_group_flow_z_scores(sector_groups)
    universe_summary = summarize_universe_flows(symbol_records, industry_groups)

    run_id, run_output_root = allocate_run_output_dir(
        resolved_base,
        resolved_compare,
        output_root=output_root,
        run_lifecycle_id=run_lifecycle_id,
    )
    overview_log = _write_overview_log(
        run_output_root,
        base_day_label=resolved_base,
        compare_day_label=resolved_compare,
        universe_summary=universe_summary,
        industry_groups=industry_groups,
        country_groups=country_groups,
        universe_filter=resolved_universe_filter,
    )
    _write_industry_logs(run_output_root, symbol_records, industry_groups)
    analyze_flow_quality_summary(
        symbol_records,
        industry_groups,
        output_root=run_output_root,
        base_day_label=resolved_base,
        compare_day_label=resolved_compare,
    )
    exports_dir = run_output_root / "exports"
    _write_group_csv(exports_dir / "industry_flow_summary.csv", industry_groups)
    _write_group_csv(exports_dir / "country_flow_summary.csv", country_groups)
    _write_group_csv(exports_dir / "sector_flow_summary.csv", sector_groups)
    _write_symbol_csv(exports_dir / "symbol_flow_detail.csv", symbol_records)
    manifest_path = run_output_root / "run_manifest.json"
    _write_run_manifest(
        manifest_path,
        run_id=run_id,
        base_day_label=resolved_base,
        compare_day_label=resolved_compare,
        base_database_path=base_database,
        compare_database_path=compare_database,
        universe_summary=universe_summary,
        universe_filter=resolved_universe_filter,
    )
    return MarketFlowScreeningResult(
        run_id=run_id,
        output_root=run_output_root,
        base_day_label=resolved_base,
        compare_day_label=resolved_compare,
        base_database_path=base_database,
        compare_database_path=compare_database,
        universe_summary=universe_summary,
        symbol_records=symbol_records,
        industry_groups=industry_groups,
        country_groups=country_groups,
        sector_groups=sector_groups,
        overview_log=overview_log,
        manifest_path=manifest_path,
    )


def run_market_flow_screening_suite(
    *,
    base_day_label: str | None = None,
    compare_day_label: str | None = None,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    output_root: str | Path | None = None,
    min_market_cap_usd: float | None = 500_000_000,
    max_market_cap_usd: float | None = None,
    markets: Sequence[str] | None = None,
    universe_filter: Mapping[str, Any] | None = None,
    lookback_days: int = 10,
    participation_top_n: int = 20,
    threshold_pct: float = FLOW_RESIDUAL_THRESHOLD_PCT,
    max_abs_price_return_pct: float | None = None,
    max_abs_flow_residual_pct: float | None = None,
    buyback_yield_threshold: float | None = None,
    share_buyback_ratio_threshold: float | None = None,
) -> MarketFlowScreeningResult:
    lifecycle_id = _new_run_lifecycle_id()
    result = run_capital_flow_screening_suite(
        base_day_label=base_day_label,
        compare_day_label=compare_day_label,
        all_fields_root=all_fields_root,
        output_root=output_root,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        markets=markets,
        universe_filter=universe_filter,
        lookback_days=lookback_days,
        run_lifecycle_id=lifecycle_id,
        threshold_pct=threshold_pct,
        max_abs_price_return_pct=max_abs_price_return_pct,
        max_abs_flow_residual_pct=max_abs_flow_residual_pct,
        buyback_yield_threshold=buyback_yield_threshold,
        share_buyback_ratio_threshold=share_buyback_ratio_threshold,
    )
    resolved_universe_filter = universe_filter or move_prediction_universe_filter(
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        markets=markets,
    )
    compare_rows = load_snapshot_rows(
        result.compare_database_path,
        universe_filter=resolved_universe_filter,
    )
    analyze_macro_breadth_snapshot(
        compare_rows,
        output_root=result.output_root,
        compare_day_label=result.compare_day_label,
    )
    analyze_industry_participation_leaders(
        compare_rows,
        output_root=result.output_root,
        compare_day_label=result.compare_day_label,
        top_n=participation_top_n,
    )
    analyze_regional_exposure_shift(
        result.symbol_records,
        result.country_groups,
        output_root=result.output_root,
        base_day_label=result.base_day_label,
        compare_day_label=result.compare_day_label,
    )
    analyze_sector_rotation_map(
        result.sector_groups,
        output_root=result.output_root,
        base_day_label=result.base_day_label,
        compare_day_label=result.compare_day_label,
    )
    return result
