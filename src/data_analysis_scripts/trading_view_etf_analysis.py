"""Global ETF analysis suite: v1 composite plus the active-management book.

Scores world primary-listed ETFs on fields that actually populate on the ETF
screener (price action, flows, AUM/NAV structure, technicals). Company-fundamental
stock fields are not used because they return no values on that route.

``run_etf_scan_and_analysis_suite`` is the entry point: scan, v1 composite,
then the peer-relative active book (regime tape, sleeve heat, divergences,
catch-up vs extended, vehicle quality, holdings overlay, day-over-day).
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence
import uuid

from data_analysis_scripts._shared_analysis_utils import (
    build_report_title,
    coerce_numeric,
    format_market_cap,
    format_number,
    format_signed_percent,
    median_absolute_deviation,
    reset_log_file,
    slugify,
)
from data_analysis_scripts.trading_view_etf_active_book import (
    DEFAULT_CONFIG_PATH as ETF_ACTIVE_BOOK_CONFIG,
    DEFAULT_HOLDINGS_PATH as ETF_HOLDINGS_CONFIG,
    run_etf_active_book,
)
from data_loaders.api_tradingview_client import ApiTradingViewClient
from db.trading_view_etf_scan_duckdb import EtfScanDuckDBStore
from generic_utils.log_to_files_util import log_rows_to_csv, log_to_file

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_ROOT / "logs" / "tradingview_analysis" / "etf_analysis"
TOP_SECTION_ROWS = 40
SUITE_NAME = "tradingview_etf_analysis_duckdb"

COMPONENT_WEIGHTS = {
    "momentum_near": 0.22,
    "momentum_medium": 0.18,
    "momentum_long": 0.12,
    "trend": 0.18,
    "attention": 0.15,
    "structure": 0.15,
}

MOMENTUM_NEAR_FIELDS = ("Perf.5D", "Perf.W", "Perf.1M")
MOMENTUM_MEDIUM_FIELDS = ("Perf.3M", "Perf.6M", "Perf.YTD")
MOMENTUM_LONG_FIELDS = ("Perf.Y", "Perf.3Y", "Perf.5Y")


@dataclass(frozen=True)
class EtfWeeklyStorageLayout:
    period_dir: Path
    run_output_dir: Path
    database_path: Path
    parquet_dir: Path


def _ensure_utc(value: datetime | None) -> datetime:
    resolved = value or datetime.now(tz=timezone.utc)
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc)


def _build_run_id(
    run_label: str | None = None,
    reference_time: datetime | None = None,
) -> str:
    reference_time = _ensure_utc(reference_time)
    timestamp = reference_time.strftime("%Y%m%d_%H%M")
    unique_suffix = uuid.uuid4().hex[:8]
    if run_label:
        slugified_label = slugify(run_label)
        if slugified_label:
            return f"{slugified_label}_{timestamp}_utc_{unique_suffix}"
    return f"etf_analysis_{timestamp}_utc_{unique_suffix}"


def _build_storage_layout(
    run_id: str,
    created_at_utc: datetime,
    output_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    parquet_dir: str | Path | None = None,
) -> EtfWeeklyStorageLayout:
    created_at_utc = _ensure_utc(created_at_utc)
    base_output_dir = Path(output_dir) if output_dir is not None else LOG_DIR / "duckdb_runs"
    iso_calendar = created_at_utc.isocalendar()
    iso_year = iso_calendar.year
    iso_week = iso_calendar.week
    period_dir = base_output_dir / f"iso_year={iso_year}" / f"week={iso_week:02d}"
    run_output_dir = period_dir / "runs" / run_id
    resolved_database_path = (
        Path(database_path)
        if database_path is not None
        else period_dir / f"etf_analysis_{iso_year}_W{iso_week:02d}.duckdb"
    )
    resolved_parquet_dir = (
        Path(parquet_dir) if parquet_dir is not None else period_dir / "parquet"
    )
    return EtfWeeklyStorageLayout(
        period_dir=period_dir,
        run_output_dir=run_output_dir,
        database_path=resolved_database_path,
        parquet_dir=resolved_parquet_dir,
    )


@contextmanager
def _duckdb_weekly_writer_lock(database_path: Path):
    lock_path = database_path.with_name(f"{database_path.name}.write.lock")
    lock_acquired = False
    try:
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        lock_acquired = True
        with os.fdopen(lock_fd, "w", encoding="utf-8") as lock_file:
            lock_file.write(
                json.dumps(
                    {
                        "database_path": str(database_path),
                        "created_at_utc": datetime.now(tz=timezone.utc).isoformat(),
                    }
                )
            )
        yield
    finally:
        if lock_acquired:
            lock_path.unlink(missing_ok=True)


def _extract_scan_rows(
    scan_input: list[dict[str, Any]] | Mapping[str, Any],
    api_request_metadata: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    metadata: dict[str, Any] = dict(api_request_metadata or {})
    if isinstance(scan_input, Mapping):
        rows = scan_input.get("data") or scan_input.get("rows") or []
        for key, value in scan_input.items():
            if key not in {"data", "rows", "raw_data"}:
                metadata.setdefault(key, value)
        metadata.setdefault("scan_input_type", "tradingview_response_payload")
    else:
        rows = scan_input
        metadata.setdefault("scan_input_type", "mapped_scan_rows")
    metadata.setdefault("captured_at_utc", datetime.now(tz=timezone.utc).isoformat())
    return list(rows or []), metadata


def _serialize_csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _collect_raw_scan_field_names(scan_data: list[dict[str, Any]]) -> list[str]:
    field_names: list[str] = []
    seen_fields: set[str] = set()
    for row in scan_data:
        for key in row.keys():
            if key == "symbol" or key in seen_fields:
                continue
            seen_fields.add(key)
            field_names.append(key)
    return field_names


def _get_symbol_name(row: dict[str, Any]) -> str:
    symbol = str(row.get("symbol") or "").strip()
    if symbol and ":" in symbol:
        exchange, ticker = symbol.split(":", 1)
        if exchange and ticker:
            return f"{exchange.upper()}:{ticker.upper()}"
    ticker_view = row.get("ticker-view")
    tv = ticker_view if isinstance(ticker_view, dict) else {}
    exchange = str(row.get("exchange") or tv.get("exchange") or "").strip()
    ticker = str(tv.get("name") or row.get("name") or "").strip()
    if exchange and ticker:
        return f"{exchange.upper()}:{ticker.upper()}"
    return (symbol or ticker or "N/A").upper()


def _get_fund_name(row: dict[str, Any]) -> str:
    ticker_view = row.get("ticker-view")
    if isinstance(ticker_view, dict):
        description = ticker_view.get("description")
        if description:
            return str(description)
    description = row.get("description")
    if description:
        return str(description)
    name = row.get("name")
    if name:
        return str(name)
    return "N/A"


def _text_field(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if value in (None, ""):
        return ""
    return str(value)


def _label_field(row: dict[str, Any], key: str) -> str:
    translated = row.get(f"{key}.tr")
    if translated not in (None, ""):
        return str(translated)
    return _text_field(row, key)


def _filter_by_aum(
    rows: list[dict[str, Any]],
    min_aum_usd: float | None,
    max_aum_usd: float | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for row in rows:
        aum = coerce_numeric(row.get("aum"))
        if aum is None:
            excluded.append(row)
            continue
        if min_aum_usd is not None and aum < min_aum_usd:
            excluded.append(row)
            continue
        if max_aum_usd is not None and aum > max_aum_usd:
            excluded.append(row)
            continue
        included.append(row)
    return included, excluded


def _robust_z_map(values: list[float | None]) -> list[float | None]:
    finite = [value for value in values if value is not None and math.isfinite(value)]
    if len(finite) < 5:
        return [None for _ in values]
    center = median(finite)
    mad = median_absolute_deviation(finite, center)
    scale = 1.4826 * mad
    if scale == 0:
        return [0.0 if value is not None else None for value in values]
    mapped: list[float | None] = []
    for value in values:
        if value is None or not math.isfinite(value):
            mapped.append(None)
            continue
        z_score = (value - center) / scale
        mapped.append(max(-3.0, min(3.0, z_score)))
    return mapped


def _z_to_score(z_score: float | None) -> float | None:
    if z_score is None:
        return None
    return 50.0 + z_score * (50.0 / 3.0)


def _mean_ignore_none(values: Sequence[float | None]) -> float | None:
    finite = [value for value in values if value is not None]
    if not finite:
        return None
    return sum(finite) / len(finite)


def _price_vs_ma(close: float | None, moving_average: float | None) -> float | None:
    if close is None or moving_average is None or moving_average == 0:
        return None
    return (close / moving_average) - 1.0


def _flow_to_aum(fund_flow: float | None, aum: float | None) -> float | None:
    if fund_flow is None or aum is None or aum == 0:
        return None
    return fund_flow / aum


def _log_aum(aum: float | None) -> float | None:
    if aum is None or aum <= 0:
        return None
    return math.log10(aum)


def _negated(value: float | None) -> float | None:
    if value is None:
        return None
    return -value


def _abs_negated(value: float | None) -> float | None:
    if value is None:
        return None
    return -abs(value)


def _component_from_fields(
    rows: list[dict[str, Any]],
    field_builders: Sequence[Any],
) -> list[float | None]:
    field_z_maps: list[list[float | None]] = []
    for builder in field_builders:
        raw_values = [builder(row) for row in rows]
        z_scores = _robust_z_map(raw_values)
        field_z_maps.append([_z_to_score(z_score) for z_score in z_scores])
    combined: list[float | None] = []
    for row_index in range(len(rows)):
        combined.append(
            _mean_ignore_none([field_map[row_index] for field_map in field_z_maps])
        )
    return combined


def _direction_from_score(score: float | None) -> str:
    if score is None:
        return "N/A"
    if score >= 65:
        return "Up"
    if score >= 55:
        return "Mild Up"
    if score <= 35:
        return "Down"
    if score <= 45:
        return "Mild Down"
    return "Neutral"


def _score_etf_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    momentum_near = _component_from_fields(
        rows,
        [lambda row, field=field: coerce_numeric(row.get(field)) for field in MOMENTUM_NEAR_FIELDS],
    )
    momentum_medium = _component_from_fields(
        rows,
        [
            lambda row, field=field: coerce_numeric(row.get(field))
            for field in MOMENTUM_MEDIUM_FIELDS
        ],
    )
    momentum_long = _component_from_fields(
        rows,
        [lambda row, field=field: coerce_numeric(row.get(field)) for field in MOMENTUM_LONG_FIELDS],
    )
    trend = _component_from_fields(
        rows,
        [
            lambda row: coerce_numeric(row.get("Recommend.All")),
            lambda row: _price_vs_ma(
                coerce_numeric(row.get("close")), coerce_numeric(row.get("SMA50"))
            ),
            lambda row: _price_vs_ma(
                coerce_numeric(row.get("close")), coerce_numeric(row.get("SMA200"))
            ),
            lambda row: coerce_numeric(row.get("RSI")),
        ],
    )
    attention = _component_from_fields(
        rows,
        [
            lambda row: coerce_numeric(row.get("relative_volume_10d_calc")),
            lambda row: _flow_to_aum(
                coerce_numeric(row.get("fund_flows.1M")),
                coerce_numeric(row.get("aum")),
            ),
            lambda row: coerce_numeric(row.get("ChaikinMoneyFlow")),
        ],
    )
    structure = _component_from_fields(
        rows,
        [
            lambda row: _log_aum(coerce_numeric(row.get("aum"))),
            lambda row: _negated(coerce_numeric(row.get("expense_ratio"))),
            lambda row: _abs_negated(coerce_numeric(row.get("nav_discount_premium"))),
        ],
    )

    scored: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        components = {
            "momentum_near": momentum_near[index],
            "momentum_medium": momentum_medium[index],
            "momentum_long": momentum_long[index],
            "trend": trend[index],
            "attention": attention[index],
            "structure": structure[index],
        }
        weighted_total = 0.0
        weight_sum = 0.0
        for component_name, weight in COMPONENT_WEIGHTS.items():
            value = components[component_name]
            if value is None:
                continue
            weighted_total += value * weight
            weight_sum += weight
        composite = weighted_total / weight_sum if weight_sum else None
        coverage = weight_sum / sum(COMPONENT_WEIGHTS.values()) if COMPONENT_WEIGHTS else 0.0
        scored.append(
            {
                "row": row,
                "symbol": _get_symbol_name(row),
                "name": _text_field(row, "name"),
                "description": _get_fund_name(row),
                "category": _label_field(row, "category"),
                "focus": _label_field(row, "focus"),
                "asset_class": _label_field(row, "asset_class"),
                "brand": _text_field(row, "brand"),
                "country": _text_field(row, "country"),
                "market": _text_field(row, "market"),
                "sector": _label_field(row, "sector"),
                "industry": _label_field(row, "industry"),
                "aum": coerce_numeric(row.get("aum")),
                "nav": coerce_numeric(row.get("nav")),
                "expense_ratio": coerce_numeric(row.get("expense_ratio")),
                "nav_discount_premium": coerce_numeric(row.get("nav_discount_premium")),
                "close": coerce_numeric(row.get("close")),
                "relative_volume_10d_calc": coerce_numeric(
                    row.get("relative_volume_10d_calc")
                ),
                "fund_flows_1m": coerce_numeric(row.get("fund_flows.1M")),
                "perf_5d": coerce_numeric(row.get("Perf.5D")),
                "perf_1m": coerce_numeric(row.get("Perf.1M")),
                "perf_3m": coerce_numeric(row.get("Perf.3M")),
                "perf_ytd": coerce_numeric(row.get("Perf.YTD")),
                "perf_1y": coerce_numeric(row.get("Perf.Y")),
                **components,
                "composite_score": composite,
                "direction": _direction_from_score(composite),
                "coverage": coverage,
            }
        )

    scored.sort(
        key=lambda item: (
            item["composite_score"] is not None,
            item["composite_score"] if item["composite_score"] is not None else float("-inf"),
            item["aum"] if item["aum"] is not None else float("-inf"),
        ),
        reverse=True,
    )
    for rank, item in enumerate(scored, start=1):
        item["rank"] = rank
    return scored


def _build_ranked_records(
    run_id: str, scored_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row_number, item in enumerate(scored_rows, start=1):
        records.append(
            {
                "run_id": run_id,
                "row_number": row_number,
                "rank": item["rank"],
                "symbol": item["symbol"],
                "name": item["name"],
                "description": item["description"],
                "category": item["category"],
                "focus": item["focus"],
                "asset_class": item["asset_class"],
                "brand": item["brand"],
                "country": item["country"],
                "market": item["market"],
                "sector": item["sector"],
                "industry": item["industry"],
                "aum": item["aum"],
                "nav": item["nav"],
                "expense_ratio": item["expense_ratio"],
                "nav_discount_premium": item["nav_discount_premium"],
                "close": item["close"],
                "relative_volume_10d_calc": item["relative_volume_10d_calc"],
                "fund_flows_1m": item["fund_flows_1m"],
                "perf_5d": item["perf_5d"],
                "perf_1m": item["perf_1m"],
                "perf_3m": item["perf_3m"],
                "perf_ytd": item["perf_ytd"],
                "perf_1y": item["perf_1y"],
                "momentum_near": item["momentum_near"],
                "momentum_medium": item["momentum_medium"],
                "momentum_long": item["momentum_long"],
                "trend": item["trend"],
                "attention": item["attention"],
                "structure": item["structure"],
                "composite_score": item["composite_score"],
                "direction": item["direction"],
                "coverage": item["coverage"],
            }
        )
    return records


def _build_category_leader_records(
    run_id: str,
    scored_rows: list[dict[str, Any]],
    top_n: int = 5,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in scored_rows:
        category = item["category"] or "Unclassified"
        grouped[category].append(item)

    records: list[dict[str, Any]] = []
    row_number = 0
    for category in sorted(grouped, key=lambda name: (-len(grouped[name]), name)):
        leaders = grouped[category][:top_n]
        for category_rank, item in enumerate(leaders, start=1):
            row_number += 1
            records.append(
                {
                    "run_id": run_id,
                    "row_number": row_number,
                    "category": category,
                    "category_rank": category_rank,
                    "symbol": item["symbol"],
                    "description": item["description"],
                    "aum": item["aum"],
                    "composite_score": item["composite_score"],
                    "direction": item["direction"],
                    "focus": item["focus"],
                    "expense_ratio": item["expense_ratio"],
                }
            )
    return records


def _format_score(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:5.1f}"


def _write_overview_log(
    log_file: Path,
    *,
    scored_rows: list[dict[str, Any]],
    scan_count: int,
    excluded_count: int,
    min_aum_usd: float | None,
    max_aum_usd: float | None,
    request_url: str | None,
) -> None:
    reset_log_file(log_file)
    log_to_file(log_file, build_report_title("TradingView global ETF analysis"))
    log_to_file(log_file, "-" * 160)
    if request_url:
        log_to_file(log_file, f"scan url: {request_url}")
    log_to_file(log_file, "universe: world primary listings (is_primary=true)")
    log_to_file(log_file, f"scan rows: {scan_count}")
    log_to_file(log_file, f"excluded by AUM / missing AUM: {excluded_count}")
    log_to_file(log_file, f"scored rows: {len(scored_rows)}")
    log_to_file(log_file, f"min AUM: {format_market_cap(min_aum_usd)}")
    log_to_file(log_file, f"max AUM: {format_market_cap(max_aum_usd)}")
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        "Components use robust MAD z-scores on ETF-populated fields only: "
        "near/medium/long price momentum, trend (Recommend/SMA/RSI), "
        "attention (relative volume + 1M fund flow / AUM), "
        "structure (log AUM, lower expense ratio, tighter NAV premium/discount).",
    )
    log_to_file(
        log_file,
        "Active book (same run dir): peer-relative lenses, regime_tape, sleeve_heat, "
        "divergences, catch_up_vs_extended, vehicle_quality, holdings_overlay, dod_changes.",
    )
    log_to_file(log_file, "")
    header = (
        f"{'Rk':>3}  {'Symbol':<16} {'Mkt':<10} {'Dir':<10} {'Score':>5}  "
        f"{'Near':>5} {'Med':>5} {'Long':>5} {'Trend':>5} {'Attn':>5} {'Str':>5}  "
        f"{'AUM':>8} {'Exp%':>6} {'1M':>8}  Name"
    )
    log_to_file(log_file, f"Top {TOP_SECTION_ROWS} by composite score")
    log_to_file(log_file, header)
    log_to_file(log_file, "-" * 160)
    for item in scored_rows[:TOP_SECTION_ROWS]:
        log_to_file(
            log_file,
            f"{item['rank']:3d}  {item['symbol']:<16} {str(item.get('market') or ''):<10} "
            f"{item['direction']:<10} "
            f"{_format_score(item['composite_score'])}  "
            f"{_format_score(item['momentum_near'])} "
            f"{_format_score(item['momentum_medium'])} "
            f"{_format_score(item['momentum_long'])} "
            f"{_format_score(item['trend'])} "
            f"{_format_score(item['attention'])} "
            f"{_format_score(item['structure'])}  "
            f"{format_market_cap(item['aum']):>8} "
            f"{format_number(item['expense_ratio'], 2):>6} "
            f"{format_signed_percent(item['perf_1m']):>8}  "
            f"{item['description']}",
        )


def _write_category_log(
    log_file: Path,
    category_records: list[dict[str, Any]],
) -> None:
    reset_log_file(log_file)
    log_to_file(log_file, build_report_title("ETF category leaders"))
    log_to_file(log_file, "-" * 160)
    current_category = None
    for record in category_records:
        if record["category"] != current_category:
            current_category = record["category"]
            log_to_file(log_file, "")
            log_to_file(log_file, current_category)
        log_to_file(
            log_file,
            f"  {record['category_rank']:2d}. {record['symbol']:<14} "
            f"{_format_score(record['composite_score'])} {record['direction']:<10} "
            f"{format_market_cap(record['aum']):>8}  {record['description']}",
        )


def _write_ranked_csv(csv_file: Path, scored_rows: list[dict[str, Any]]) -> None:
    headers = [
        "rank",
        "symbol",
        "description",
        "category",
        "focus",
        "asset_class",
        "brand",
        "country",
        "market",
        "aum",
        "expense_ratio",
        "nav_discount_premium",
        "composite_score",
        "direction",
        "momentum_near",
        "momentum_medium",
        "momentum_long",
        "trend",
        "attention",
        "structure",
        "perf_5d",
        "perf_1m",
        "perf_3m",
        "perf_ytd",
        "perf_1y",
        "relative_volume_10d_calc",
        "fund_flows_1m",
    ]
    rows = [
        [_serialize_csv_value(item.get(header)) for header in headers]
        for item in scored_rows
    ]
    log_rows_to_csv(csv_file, headers, rows)


def _persist_etf_active_book(
    duckdb_store: EtfScanDuckDBStore,
    *,
    run_id: str,
    book_result: Mapping[str, Any],
) -> None:
    duckdb_store.append_book_rows(book_result.get("book_records") or [])
    duckdb_store.append_regime_tape(book_result.get("regime_records") or [])
    duckdb_store.append_sleeve_heat(book_result.get("sleeve_records") or [])
    duckdb_store.append_divergences(book_result.get("divergence_records") or [])
    duckdb_store.append_catch_up(book_result.get("catch_up_records") or [])
    duckdb_store.append_vehicle_quality(book_result.get("vehicle_records") or [])
    duckdb_store.append_lens_leaders(book_result.get("lens_records") or [])
    duckdb_store.append_holdings_overlay(book_result.get("holdings_records") or [])
    duckdb_store.append_dod_changes(book_result.get("dod_records") or [])
    reports = book_result.get("reports") or {}
    report_types = {
        "regime_tape_log": ("regime_tape", "log"),
        "sleeve_heat_log": ("sleeve_heat", "log"),
        "sleeve_heat_csv": ("sleeve_heat_csv", "csv"),
        "divergences_log": ("divergences", "log"),
        "catch_up_log": ("catch_up_vs_extended", "log"),
        "vehicle_quality_log": ("vehicle_quality", "log"),
        "vehicle_quality_csv": ("vehicle_quality_csv", "csv"),
        "lens_leaders_log": ("lens_leaders", "log"),
        "holdings_overlay_log": ("holdings_overlay", "log"),
        "dod_log": ("dod_changes", "log"),
        "book_ranked_csv": ("book_ranked_csv", "csv"),
    }
    for report_key, (stored_key, report_type) in report_types.items():
        file_path = reports.get(report_key)
        if file_path is None:
            continue
        duckdb_store.register_report(
            run_id=run_id,
            report_key=stored_key,
            report_type=report_type,
            file_path=file_path,
        )


def run_etf_analysis_suite_duckdb(
    scan_data: list[dict[str, Any]] | Mapping[str, Any],
    min_aum_usd: float | None = None,
    max_aum_usd: float | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    output_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    parquet_dir: str | Path | None = None,
    run_label: str | None = None,
    reference_time: datetime | None = None,
    api_request_metadata: Mapping[str, Any] | None = None,
    export_parquet: bool = True,
    create_indexes: bool = False,
    include_active_book: bool = True,
    active_book_config_path: str | Path | None = None,
    holdings_config_path: str | Path | None = None,
) -> dict[str, Any]:
    """DuckDB-backed ETF analysis: v1 composite plus the active-management book.

    ``min_market_cap_usd`` / ``max_market_cap_usd`` are aliases for AUM bounds
    so the call site can match ``run_full_analysis_suite_duckdb``.
    """
    resolved_min_aum = min_aum_usd if min_aum_usd is not None else min_market_cap_usd
    resolved_max_aum = max_aum_usd if max_aum_usd is not None else max_market_cap_usd
    created_at_utc = _ensure_utc(reference_time)
    run_id = _build_run_id(run_label, created_at_utc)
    run_id_generated = not bool(slugify(run_label) if run_label else None)
    scan_rows, resolved_metadata = _extract_scan_rows(
        scan_data, api_request_metadata=api_request_metadata
    )
    included_rows, excluded_rows = _filter_by_aum(
        scan_rows, resolved_min_aum, resolved_max_aum
    )
    scored_rows = _score_etf_rows(included_rows) if included_rows else []
    storage_layout = _build_storage_layout(
        run_id=run_id,
        created_at_utc=created_at_utc,
        output_dir=output_dir,
        database_path=database_path,
        parquet_dir=parquet_dir,
    )
    storage_layout.run_output_dir.mkdir(parents=True, exist_ok=True)

    overview_log = storage_layout.run_output_dir / "etf_analysis_overview.log"
    category_log = storage_layout.run_output_dir / "etf_category_leaders.log"
    ranked_csv = storage_layout.run_output_dir / "etf_ranked.csv"
    request_url = None
    request_metadata = resolved_metadata.get("request_metadata")
    if isinstance(request_metadata, dict):
        request_url = request_metadata.get("url")
    _write_overview_log(
        overview_log,
        scored_rows=scored_rows,
        scan_count=len(scan_rows),
        excluded_count=len(excluded_rows),
        min_aum_usd=resolved_min_aum,
        max_aum_usd=resolved_max_aum,
        request_url=str(request_url) if request_url else None,
    )
    category_records = _build_category_leader_records(run_id, scored_rows)
    _write_category_log(category_log, category_records)
    _write_ranked_csv(ranked_csv, scored_rows)

    field_names = _collect_raw_scan_field_names(included_rows)
    raw_headers = ["symbol", "Fund"] + field_names
    raw_rows = [
        [_get_symbol_name(row), _get_fund_name(row)]
        + [_serialize_csv_value(row.get(field_name)) for field_name in field_names]
        for row in included_rows
    ]

    parquet_exports: dict[str, Path] = {}
    book_result: dict[str, Any] | None = None
    with _duckdb_weekly_writer_lock(storage_layout.database_path):
        with EtfScanDuckDBStore(
            database_path=storage_layout.database_path,
            parquet_dir=storage_layout.parquet_dir if export_parquet else None,
        ) as duckdb_store:
            duckdb_store.begin_transaction()
            try:
                duckdb_store.drop_analysis_indexes()
                duckdb_store.delete_run_data(run_id)
                duckdb_store.register_run(
                    run_id=run_id,
                    created_at_utc=created_at_utc,
                    suite_name=SUITE_NAME,
                    scan_data_count=len(included_rows),
                    profile_names=["etf_composite_v1", "etf_active_manager_v1"]
                    if include_active_book
                    else ["etf_composite_v1"],
                    industries=None,
                    min_market_cap_usd=resolved_min_aum,
                    max_market_cap_usd=resolved_max_aum,
                    include_blind_spot_sections=False,
                    api_request_metadata=resolved_metadata,
                    run_label=run_label,
                    run_id_generated=run_id_generated,
                    notes=(
                        "World primary-listing ETF analysis: v1 composite plus "
                        "peer-relative active book. Size filters are AUM. "
                        "Stock fundamental columns are omitted from the scan payload."
                    ),
                )
                duckdb_store.append_tabular_output(
                    "raw_scan_rows",
                    raw_headers,
                    raw_rows,
                    context={"run_id": run_id},
                )
                duckdb_store.append_ranked_scores(
                    _build_ranked_records(run_id, scored_rows)
                )
                duckdb_store.append_category_leaders(category_records)
                duckdb_store.register_report(
                    run_id=run_id,
                    report_key="overview",
                    report_type="log",
                    file_path=overview_log,
                )
                duckdb_store.register_report(
                    run_id=run_id,
                    report_key="category_leaders",
                    report_type="log",
                    file_path=category_log,
                )
                duckdb_store.register_report(
                    run_id=run_id,
                    report_key="ranked_csv",
                    report_type="csv",
                    file_path=ranked_csv,
                )
                if include_active_book:
                    prior_run_id, prior_rows = duckdb_store.fetch_prior_book_rows(
                        suite_name=SUITE_NAME,
                        current_run_id=run_id,
                    )
                    book_result = run_etf_active_book(
                        scored_rows,
                        run_id=run_id,
                        run_output_dir=storage_layout.run_output_dir,
                        config_path=active_book_config_path or ETF_ACTIVE_BOOK_CONFIG,
                        holdings_config_path=holdings_config_path
                        or ETF_HOLDINGS_CONFIG,
                        prior_book_rows=prior_rows if prior_run_id else None,
                    )
                    _persist_etf_active_book(
                        duckdb_store, run_id=run_id, book_result=book_result
                    )
                if create_indexes:
                    duckdb_store.create_analysis_indexes()
                if export_parquet:
                    parquet_exports = duckdb_store.export_tables_to_parquet(
                        run_id,
                        parquet_dir=storage_layout.parquet_dir,
                    )
                duckdb_store.commit()
            except Exception:
                duckdb_store.rollback()
                raise

    book_reports = (book_result or {}).get("reports") or {}
    return {
        "run_id": run_id,
        "suite_name": SUITE_NAME,
        "scan_data_count": len(scan_rows),
        "scored_count": len(scored_rows),
        "excluded_count": len(excluded_rows),
        "min_aum_usd": resolved_min_aum,
        "max_aum_usd": resolved_max_aum,
        "overview_log": overview_log,
        "category_log": category_log,
        "ranked_csv": ranked_csv,
        "run_output_dir": storage_layout.run_output_dir,
        "_duckdb_database": storage_layout.database_path,
        "_parquet_dir": storage_layout.parquet_dir if export_parquet else None,
        "_parquet_exports": parquet_exports,
        "top_symbols": [item["symbol"] for item in scored_rows[:TOP_SECTION_ROWS]],
        "active_book_suite_id": (book_result or {}).get("suite_id"),
        "one_x_count": (book_result or {}).get("one_x_count"),
        "regime_tape_log": book_reports.get("regime_tape_log"),
        "sleeve_heat_log": book_reports.get("sleeve_heat_log"),
        "divergences_log": book_reports.get("divergences_log"),
        "catch_up_log": book_reports.get("catch_up_log"),
        "vehicle_quality_log": book_reports.get("vehicle_quality_log"),
        "holdings_overlay_log": book_reports.get("holdings_overlay_log"),
        "dod_log": book_reports.get("dod_log"),
        "book_ranked_csv": book_reports.get("book_ranked_csv"),
        "lens_leaders_log": book_reports.get("lens_leaders_log"),
    }


def run_etf_scan_and_analysis_suite(
    api_client: ApiTradingViewClient,
    *,
    timeout: int = 90,
    industries: list[str] | None = None,
    categories: list[str] | None = None,
    markets: list[str] | None = None,
    min_aum_usd: float | None = None,
    max_aum_usd: float | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    ticker_filter: str | None = None,
    output_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    parquet_dir: str | Path | None = None,
    run_label: str | None = None,
    reference_time: datetime | None = None,
    export_parquet: bool = True,
    create_indexes: bool = False,
    include_active_book: bool = True,
    active_book_config_path: str | Path | None = None,
    holdings_config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Fetch world primary ETF listings and run the DuckDB analysis suite.

    This is the single entry point for ETF work: scan + v1 composite +
    active-management book + persist. Size filters apply to ``aum`` on both
    the API request and the analysis pass.
    ``min_market_cap_usd`` / ``max_market_cap_usd`` are AUM aliases.
    """
    resolved_min_aum = min_aum_usd if min_aum_usd is not None else min_market_cap_usd
    resolved_max_aum = max_aum_usd if max_aum_usd is not None else max_market_cap_usd
    scan_response = api_client.scan_global_etf_move_prediction(
        timeout=timeout,
        industries=industries,
        categories=categories,
        markets=markets,
        min_aum_usd=resolved_min_aum,
        max_aum_usd=resolved_max_aum,
        ticker_filter=ticker_filter,
    )
    result = run_etf_analysis_suite_duckdb(
        scan_data=scan_response,
        min_aum_usd=resolved_min_aum,
        max_aum_usd=resolved_max_aum,
        output_dir=output_dir,
        database_path=database_path,
        parquet_dir=parquet_dir,
        run_label=run_label,
        reference_time=reference_time,
        export_parquet=export_parquet,
        create_indexes=create_indexes,
        include_active_book=include_active_book,
        active_book_config_path=active_book_config_path,
        holdings_config_path=holdings_config_path,
    )
    request_metadata = scan_response.get("request_metadata")
    if isinstance(request_metadata, Mapping):
        result["scan_url"] = request_metadata.get("url")
        result["scan_markets"] = list(request_metadata.get("markets") or [])
    else:
        result["scan_url"] = None
        result["scan_markets"] = []
    return result
