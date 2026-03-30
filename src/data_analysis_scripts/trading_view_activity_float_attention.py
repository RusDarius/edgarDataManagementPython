from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

from data_analysis_scripts._shared_analysis_utils import (
    REPORT_TIMESTAMP_FORMAT,
    build_report_title as _build_report_title,
    coerce_numeric as _coerce_numeric,
    format_market_cap as _format_market_cap,
    format_number as _format_number,
    format_raw_value as _format_raw_value,
    format_signed_percent as _format_percent,
    get_company_description as _get_company_description,
    median_absolute_deviation as _median_absolute_deviation,
    reset_log_file as _reset_log_file,
    safe_ratio as _safe_ratio,
    slugify as _slugify,
    sort_key_desc as _sort_key_desc,
)
from generic_utils.log_to_files_util import log_to_file, log_rows_to_csv

LOG_DIR = Path(
    r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis"
)

# Comprehensive metric fields covering all dimensions of activity/float/attention analysis
ACTIVITY_FIELDS = [
    # Market structure
    "close",
    "market_cap_basic",
    "float_shares_outstanding",
    # Volume metrics (absolute and relative)
    "volume",
    "average_volume_10d_calc",
    "average_volume_30d_calc",
    "average_volume_60d_calc",
    "average_volume_90d_calc",
    "relative_volume_10d_calc",
    # Dollar value traded
    "Value.Traded",
    "AvgValue.Traded_10d",
    "AvgValue.Traded_30d",
    "AvgValue.Traded_60d",
    "AvgValue.Traded_90d",
    # Volatility and range metrics
    "ADR",
    "ATR",
    "ATRP",
    "Volatility.D",
    "Volatility.W",
    "Volatility.M",
    # Beta (systematic risk)
    "beta_1_year",
    "beta_3_year",
    "beta_5_year",
    # Price action and gaps
    "change",
    "gap",
    "premarket_gap",
    "premarket_change",
    "premarket_volume",
    "postmarket_change",
    "postmarket_volume",
    # Performance across timeframes
    "Perf.5D",
    "Perf.W",
    "Perf.1M",
    "Perf.3M",
    "Perf.YTD",
    "Perf.Y",
    # Momentum indicators
    "RSI",
    "RSI7",
    "MACD.macd",
    "MACD.signal",
    "Mom",
    "ROC",
    # Analyst recommendations
    "Recommend.All",
    "Recommend.MA",
    "Recommend.Other",
    # Moving averages and VWAP
    "VWAP",
    "VWMA",
    "SMA50",
    "SMA200",
    "EMA50",
    "EMA200",
]

ENTRY_METADATA_FIELDS = [
    "symbol",
    "name",
    "exchange",
    "country",
    "sector",
    "industry",
    "earnings_release_date",
    "earnings_release_next_date",
]

TOP_SECTION_ROWS = 12

SUM_AGGREGATED_FIELDS = {
    "market_cap_basic",
    "float_shares_outstanding",
    "volume",
    "average_volume_10d_calc",
    "average_volume_30d_calc",
    "average_volume_60d_calc",
    "average_volume_90d_calc",
    "Value.Traded",
    "AvgValue.Traded_10d",
    "AvgValue.Traded_30d",
    "AvgValue.Traded_60d",
    "AvgValue.Traded_90d",
    "premarket_volume",
    "postmarket_volume",
}


def _build_report_file_name(
    report_slug: str,
    industries: list[str] | None,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
) -> Path:
    industry_segment = "all_industries"
    if industries:
        industry_segment = "__".join(_slugify(industry) for industry in industries)

    min_segment = (
        f"min_{int(min_market_cap_usd)}"
        if min_market_cap_usd is not None
        else "min_none"
    )
    max_segment = (
        f"max_{int(max_market_cap_usd)}"
        if max_market_cap_usd is not None
        else "max_none"
    )
    return (
        LOG_DIR / f"{report_slug}__{industry_segment}__{min_segment}__{max_segment}.log"
    )


def _build_log_file_name(
    industries: list[str] | None,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
) -> Path:
    return _build_report_file_name(
        report_slug="tradingview_activity_float_attention",
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
    )


def _build_csv_file_name(
    industries: list[str] | None,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
) -> Path:
    return _build_log_file_name(
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
    ).with_suffix(".csv")


def _build_grouped_industries_log_file_name(
    industries: list[str] | None,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
) -> Path:
    return _build_report_file_name(
        report_slug="tradingview_activity_float_attention_grouped_industries",
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
    )


def _build_grouped_industries_csv_file_name(
    industries: list[str] | None,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
) -> Path:
    return _build_grouped_industries_log_file_name(
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
    ).with_suffix(".csv")


def _build_metric_profiles(
    scan_data: list[dict[str, Any]],
) -> dict[str, dict[str, float | int | None]]:
    """Build comprehensive profiles for all numeric fields: count, mean, median, MAD, min, max."""
    profiles: dict[str, dict[str, float | int | None]] = {}

    for field in ACTIVITY_FIELDS:
        values = [_coerce_numeric(row.get(field)) for row in scan_data]
        numeric_values = [v for v in values if v is not None]

        if not numeric_values:
            profiles[field] = {
                "count": 0,
                "mean": None,
                "median": None,
                "mad": None,
                "min": None,
                "max": None,
            }
            continue

        sorted_vals = sorted(numeric_values)
        median_val = median(numeric_values)
        mean_val = sum(numeric_values) / len(numeric_values)
        mad_val = _median_absolute_deviation(numeric_values, median_val)

        profiles[field] = {
            "count": len(numeric_values),
            "mean": mean_val,
            "median": median_val,
            "mad": mad_val,
            "min": sorted_vals[0],
            "max": sorted_vals[-1],
        }

    return profiles


def _build_all_columns_table_headers() -> list[str]:
    headers = list(ENTRY_METADATA_FIELDS)
    for field_name in ACTIVITY_FIELDS:
        headers.extend(
            [
                field_name,
                f"{field_name}__dev_mean",
                f"{field_name}__dev_median",
            ]
        )
    return headers


def _build_all_columns_table_row(
    row: dict[str, Any],
    profiles: dict[str, dict[str, float | int | None]],
) -> list[str]:
    row_values: list[str] = []

    for field_name in ENTRY_METADATA_FIELDS:
        row_values.append(_format_raw_value(row.get(field_name)))

    for field_name in ACTIVITY_FIELDS:
        raw_value = row.get(field_name)
        numeric_value = _coerce_numeric(row.get(field_name))
        profile = profiles.get(field_name, {})
        mean_value = profile.get("mean")
        median_value = profile.get("median")

        dev_mean = (
            None
            if numeric_value is None or mean_value is None
            else numeric_value - float(mean_value)
        )
        dev_median = (
            None
            if numeric_value is None or median_value is None
            else numeric_value - float(median_value)
        )

        row_values.extend(
            [
                _format_raw_value(raw_value),
                _format_raw_value(dev_mean),
                _format_raw_value(dev_median),
            ]
        )

    return row_values


def _get_symbol_name(row: dict[str, Any]) -> str:
    ticker_view = row.get("ticker-view")
    if isinstance(ticker_view, dict):
        name = ticker_view.get("name")
        if name:
            return str(name)
    return str(row.get("name") or row.get("symbol") or "N/A")


# === AI MODIFIED CODE START (GitHub Copilot) ===
# Modified on: 2026-03-24
# Model: GPT-5.4
# Changes: Added grouped-industry detection and compact formatting helpers so industry aggregate logs remain column-aligned and readable.
def _is_grouped_industry_row(row: dict[str, Any]) -> bool:
    symbol = str(row.get("symbol") or "")
    return symbol.startswith("industry::")


def _is_grouped_industry_scan(scan_data: list[dict[str, Any]]) -> bool:
    return bool(scan_data) and all(_is_grouped_industry_row(row) for row in scan_data)


def _format_entry_count(row: dict[str, Any]) -> str:
    entry_count = row.get("entry_count")
    if isinstance(entry_count, int):
        return str(entry_count)
    numeric_entry_count = _coerce_numeric(entry_count)
    if numeric_entry_count is None:
        return "N/A"
    return str(int(numeric_entry_count))


def _truncate_label(value: Any, width: int) -> str:
    text = str(value or "N/A")
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return f"{text[: width - 3]}..."


# === AI MODIFIED CODE END ===


def _build_participation_metrics(row: dict[str, Any]) -> dict[str, float | None]:
    volume = _coerce_numeric(row.get("volume"))
    float_shares = _coerce_numeric(row.get("float_shares_outstanding"))
    avg_value_traded_10d = _coerce_numeric(row.get("AvgValue.Traded_10d"))
    market_cap = _coerce_numeric(row.get("market_cap_basic"))
    relative_volume = _coerce_numeric(row.get("relative_volume_10d_calc"))

    return {
        "relative_volume": relative_volume,
        "float_turnover": _safe_ratio(volume, float_shares),
        "dollar_turnover_intensity": _safe_ratio(avg_value_traded_10d, market_cap),
    }


def _build_event_metrics(row: dict[str, Any]) -> dict[str, float | None]:
    gap = _coerce_numeric(row.get("gap"))
    atrp = _coerce_numeric(row.get("ATRP"))
    premarket_volume = _coerce_numeric(row.get("premarket_volume"))
    average_volume_10d = _coerce_numeric(row.get("average_volume_10d_calc"))

    return {
        "gap_severity": _safe_ratio(gap, atrp),
        "event_intensity": _safe_ratio(premarket_volume, average_volume_10d),
        "premarket_gap": _coerce_numeric(row.get("premarket_gap")),
        "postmarket_change": _coerce_numeric(row.get("postmarket_change")),
    }


def _build_momentum_metrics(row: dict[str, Any]) -> dict[str, float | None]:
    perf_1m = _coerce_numeric(row.get("Perf.1M"))
    relative_volume = _coerce_numeric(row.get("relative_volume_10d_calc"))
    return {
        "perf_1m": perf_1m,
        "relative_volume": relative_volume,
        "momentum_participation_score": (
            None
            if perf_1m is None or relative_volume is None
            else perf_1m * relative_volume
        ),
    }


def _build_trend_metrics(row: dict[str, Any]) -> dict[str, float | None | str]:
    close = _coerce_numeric(row.get("close"))
    averages = {
        "SMA50": _coerce_numeric(row.get("SMA50")),
        "SMA200": _coerce_numeric(row.get("SMA200")),
        "EMA50": _coerce_numeric(row.get("EMA50")),
        "EMA200": _coerce_numeric(row.get("EMA200")),
    }

    states = []
    above_count = 0
    for label, average_value in averages.items():
        if close is None or average_value is None:
            states.append(f"{label}:N/A")
            continue
        if close >= average_value:
            above_count += 1
            states.append(f"{label}:above")
        else:
            states.append(f"{label}:below")

    return {
        "above_count": float(above_count),
        "trend_state": ", ".join(states),
    }


def _row_label(row: dict[str, Any]) -> str:
    if _is_grouped_industry_row(row):
        industry_name = str(row.get("name") or row.get("industry") or "N/A")
        return f"{industry_name} ({_format_entry_count(row)} entries)"

    ticker = _get_symbol_name(row)
    company = _get_company_description(row)
    if company:
        return f"{ticker} ({company})"
    return ticker


def _log_section_intro(log_file: Path, title: str, description: str) -> None:
    log_to_file(log_file, title)
    log_to_file(log_file, "-" * 180)
    log_to_file(log_file, "")


def _normalize_industry_name(industry: Any) -> str:
    normalized = str(industry or "").strip()
    return normalized or "Unknown Industry"


def _mean_numeric_field(rows: list[dict[str, Any]], field_name: str) -> float | None:
    numeric_values = [
        value
        for value in (_coerce_numeric(row.get(field_name)) for row in rows)
        if value is not None
    ]
    if not numeric_values:
        return None
    return sum(numeric_values) / len(numeric_values)


def _sum_numeric_field(rows: list[dict[str, Any]], field_name: str) -> float | None:
    numeric_values = [
        value
        for value in (_coerce_numeric(row.get(field_name)) for row in rows)
        if value is not None
    ]
    if not numeric_values:
        return None
    return sum(numeric_values)


def _summarize_group_label(rows: list[dict[str, Any]], field_name: str) -> str:
    values = sorted(
        {
            str(value).strip()
            for row in rows
            if (value := row.get(field_name)) not in (None, "")
        }
    )
    if not values:
        return "N/A"
    if len(values) == 1:
        return values[0]
    if len(values) <= 3:
        return ", ".join(values)
    return f"multiple({len(values)})"


def _build_industry_aggregate_row(
    industry_name: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    aggregate_row: dict[str, Any] = {
        "symbol": f"industry::{_slugify(industry_name)}",
        "name": industry_name,
        "exchange": _summarize_group_label(rows, "exchange"),
        "country": _summarize_group_label(rows, "country"),
        "sector": _summarize_group_label(rows, "sector"),
        "industry": industry_name,
        "market": _summarize_group_label(rows, "market"),
        "earnings_release_date": None,
        "earnings_release_next_date": None,
        "ticker-view": {
            "name": industry_name,
            "description": f"Industry aggregate | entries={len(rows)}",
        },
        "entry_count": len(rows),
    }

    for field_name in ACTIVITY_FIELDS:
        if field_name == "relative_volume_10d_calc":
            continue

        if field_name in SUM_AGGREGATED_FIELDS:
            aggregate_row[field_name] = _sum_numeric_field(rows, field_name)
        else:
            aggregate_row[field_name] = _mean_numeric_field(rows, field_name)

    aggregate_row["relative_volume_10d_calc"] = _safe_ratio(
        _coerce_numeric(aggregate_row.get("volume")),
        _coerce_numeric(aggregate_row.get("average_volume_10d_calc")),
    )
    return aggregate_row


def _group_scan_data_by_industry(
    scan_data: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scan_data:
        grouped_rows[_normalize_industry_name(row.get("industry"))].append(row)

    industry_rows = [
        _build_industry_aggregate_row(industry_name, rows)
        for industry_name, rows in grouped_rows.items()
    ]
    return sorted(
        industry_rows,
        key=lambda row: _sort_key_desc(
            _coerce_numeric(row.get("relative_volume_10d_calc"))
        ),
        reverse=True,
    )


def _export_all_columns_per_ticker_csv(
    csv_file: Path,
    scan_data: list[dict[str, Any]],
    profiles: dict[str, dict[str, float | int | None]],
) -> None:
    headers = _build_all_columns_table_headers()
    rows = [_build_all_columns_table_row(row, profiles) for row in scan_data]
    log_rows_to_csv(csv_file, headers, rows)


def _log_participation_section(
    log_file: Path,
    scan_data: list[dict[str, Any]],
    is_grouped_industry_report: bool = False,
) -> None:
    _log_section_intro(
        log_file,
        "Section 1: Participation and Turnover",
        (
            "Purpose: identify where participation is unusually strong using relative volume, "
            "float turnover, and dollar-turnover intensity. Higher readings suggest stronger market attention and cleaner price discovery."
        ),
    )
    ranked_rows_with_metrics = sorted(
        ((row, _build_participation_metrics(row)) for row in scan_data),
        key=lambda item: _sort_key_desc(item[1]["relative_volume"]),
        reverse=True,
    )
    if is_grouped_industry_report:
        log_to_file(
            log_file,
            (
                f"{'Industry':<32} {'Entries':>7} {'Market':<18} {'RelVol':>10} {'FloatTurn':>12} {'DollarTurn10D':>14} {'MCap':>10}"
            ),
        )
    else:
        log_to_file(
            log_file,
            (
                f"{'Ticker':<12} {'Name':<10} {'Industry':<28} {'RelVol':>10} {'FloatTurn':>12} {'DollarTurn10D':>14} {'MCap':>10}"
            ),
        )
    log_to_file(log_file, "-" * 180)
    for row, metrics in ranked_rows_with_metrics[:TOP_SECTION_ROWS]:
        if is_grouped_industry_report:
            log_to_file(
                log_file,
                (
                    f"{_truncate_label(row.get('name') or row.get('industry'), 32):<32} {_format_entry_count(row):>7} "
                    f"{_truncate_label(row.get('market'), 18):<18} {_format_number(metrics['relative_volume']):>10} "
                    f"{_format_number(metrics['float_turnover']):>12} {_format_number(metrics['dollar_turnover_intensity']):>14} "
                    f"{_format_market_cap(_coerce_numeric(row.get('market_cap_basic'))):>10}"
                ),
            )
        else:
            log_to_file(
                log_file,
                (
                    f"{_get_symbol_name(row):<12} {str(row.get('name') or 'N/A')[:10]:<10} {str(row.get('industry') or '')[:28]:<28} "
                    f"{_format_number(metrics['relative_volume']):>10} {_format_number(metrics['float_turnover']):>12} "
                    f"{_format_number(metrics['dollar_turnover_intensity']):>14} {_format_market_cap(_coerce_numeric(row.get('market_cap_basic'))):>10}"
                ),
            )
    log_to_file(log_file, "")

    highest_relvol_row, highest_relvol_metrics = max(
        ranked_rows_with_metrics,
        key=lambda item: _sort_key_desc(item[1]["relative_volume"]),
    )
    highest_float_turnover_row, highest_float_turnover_metrics = max(
        ranked_rows_with_metrics,
        key=lambda item: _sort_key_desc(item[1]["float_turnover"]),
    )
    highest_dollar_turnover_row, highest_dollar_turnover_metrics = max(
        ranked_rows_with_metrics,
        key=lambda item: _sort_key_desc(item[1]["dollar_turnover_intensity"]),
    )

    log_to_file(log_file, "STANDOUTS")
    log_to_file(
        log_file,
        (
            f"- Highest relative volume: {_row_label(highest_relvol_row)} | relvol={_format_number(highest_relvol_metrics['relative_volume'])}. "
            "This stands out because current trading participation is most elevated versus its recent normal volume baseline."
        ),
    )
    log_to_file(
        log_file,
        (
            f"- Highest float turnover proxy: {_row_label(highest_float_turnover_row)} | volume/float={_format_number(highest_float_turnover_metrics['float_turnover'])}. "
            "This stands out because a larger share of the tradable float appears to be changing hands."
        ),
    )
    log_to_file(
        log_file,
        (
            f"- Highest 10D dollar-turnover intensity: {_row_label(highest_dollar_turnover_row)} | AvgValue.Traded_10d / market_cap={_format_number(highest_dollar_turnover_metrics['dollar_turnover_intensity'])}. "
            "This stands out because trading value is large relative to company size."
        ),
    )
    log_to_file(log_file, "")


def _log_event_section(
    log_file: Path,
    scan_data: list[dict[str, Any]],
    is_grouped_industry_report: bool = False,
) -> None:
    _log_section_intro(
        log_file,
        "Section 2: Event and Repricing Pressure",
        (
            "Purpose: identify names where gap size, premarket activity, and after-hours movement suggest active repricing around information or sentiment shifts."
        ),
    )
    rows_with_metrics = [(row, _build_event_metrics(row)) for row in scan_data]
    ranked_rows_with_metrics = sorted(
        rows_with_metrics,
        key=lambda item: max(
            _sort_key_desc(
                abs(item[1]["premarket_gap"])
                if item[1]["premarket_gap"] is not None
                else None
            ),
            _sort_key_desc(
                abs(item[1]["gap_severity"])
                if item[1]["gap_severity"] is not None
                else None
            ),
            _sort_key_desc(item[1]["event_intensity"]),
            _sort_key_desc(
                abs(item[1]["postmarket_change"])
                if item[1]["postmarket_change"] is not None
                else None
            ),
        ),
        reverse=True,
    )
    if is_grouped_industry_report:
        log_to_file(
            log_file,
            (
                f"{'Industry':<32} {'Entries':>7} {'Market':<18} {'PreGap':>10} {'PreChg':>10} {'PreVol':>12} {'Gap/ATRP':>10} {'PostChg':>10}"
            ),
        )
    else:
        log_to_file(
            log_file,
            (
                f"{'Ticker':<12} {'Name':<10} {'Industry':<28} {'PreGap':>10} {'PreChg':>10} {'PreVol':>12} {'Gap/ATRP':>10} {'PostChg':>10}"
            ),
        )
    log_to_file(log_file, "-" * 180)
    for row, metrics in ranked_rows_with_metrics[:TOP_SECTION_ROWS]:
        if is_grouped_industry_report:
            log_to_file(
                log_file,
                (
                    f"{_truncate_label(row.get('name') or row.get('industry'), 32):<32} {_format_entry_count(row):>7} "
                    f"{_truncate_label(row.get('market'), 18):<18} {_format_percent(metrics['premarket_gap']):>10} "
                    f"{_format_percent(_coerce_numeric(row.get('premarket_change'))):>10} {_format_number(_coerce_numeric(row.get('premarket_volume'))):>12} "
                    f"{_format_number(metrics['gap_severity']):>10} {_format_percent(metrics['postmarket_change']):>10}"
                ),
            )
        else:
            log_to_file(
                log_file,
                (
                    f"{_get_symbol_name(row):<12} {str(row.get('name') or 'N/A')[:10]:<10} {str(row.get('industry') or '')[:28]:<28} "
                    f"{_format_percent(metrics['premarket_gap']):>10} {_format_percent(_coerce_numeric(row.get('premarket_change'))):>10} "
                    f"{_format_number(_coerce_numeric(row.get('premarket_volume'))):>12} {_format_number(metrics['gap_severity']):>10} "
                    f"{_format_percent(metrics['postmarket_change']):>10}"
                ),
            )
    log_to_file(log_file, "")

    premarket_gap_rows = [
        (row, metrics)
        for row, metrics in rows_with_metrics
        if metrics["premarket_gap"] is not None
    ]
    event_intensity_rows = [
        (row, metrics)
        for row, metrics in rows_with_metrics
        if metrics["event_intensity"] is not None
    ]
    gap_severity_rows = [
        (row, metrics)
        for row, metrics in rows_with_metrics
        if metrics["gap_severity"] is not None
    ]

    log_to_file(log_file, "STANDOUTS")
    if premarket_gap_rows:
        highest_abs_premarket_gap_row, highest_abs_premarket_gap_metrics = max(
            premarket_gap_rows,
            key=lambda item: abs(item[1]["premarket_gap"]),
        )
        log_to_file(
            log_file,
            (
                f"- Largest absolute premarket gap: {_row_label(highest_abs_premarket_gap_row)} | premarket_gap={_format_percent(highest_abs_premarket_gap_metrics['premarket_gap'])}. "
                "This stands out because the off-session price reset is the strongest in the group."
            ),
        )
    else:
        log_to_file(
            log_file,
            "- Largest absolute premarket gap: unavailable. No valid premarket_gap values were returned in this scan.",
        )

    if event_intensity_rows:
        highest_event_intensity_row, highest_event_intensity_metrics = max(
            event_intensity_rows,
            key=lambda item: item[1]["event_intensity"],
        )
        log_to_file(
            log_file,
            (
                f"- Highest event intensity: {_row_label(highest_event_intensity_row)} | premarket_volume / average_volume_10d={_format_number(highest_event_intensity_metrics['event_intensity'])}. "
                "This stands out because premarket participation is most elevated versus normal trading activity."
            ),
        )
    else:
        log_to_file(
            log_file,
            "- Highest event intensity: unavailable. No valid premarket_volume and average_volume_10d_calc combination was returned in this scan.",
        )

    if gap_severity_rows:
        highest_gap_severity_row, highest_gap_severity_metrics = max(
            gap_severity_rows,
            key=lambda item: abs(item[1]["gap_severity"]),
        )
        log_to_file(
            log_file,
            (
                f"- Highest gap severity: {_row_label(highest_gap_severity_row)} | gap / ATRP={_format_number(highest_gap_severity_metrics['gap_severity'])}. "
                "This stands out because the opening move is largest relative to the name's recent trading range."
            ),
        )
    else:
        log_to_file(
            log_file,
            "- Highest gap severity: unavailable. No valid gap and ATRP combination was returned in this scan.",
        )
    log_to_file(log_file, "")


def _log_momentum_section(
    log_file: Path,
    scan_data: list[dict[str, Any]],
    is_grouped_industry_report: bool = False,
) -> None:
    _log_section_intro(
        log_file,
        "Section 3: Momentum with Participation",
        (
            "Purpose: combine recent performance with elevated relative volume to identify names where price movement is being confirmed by actual market participation."
        ),
    )
    rows_with_metrics = [(row, _build_momentum_metrics(row)) for row in scan_data]
    ranked_rows_with_metrics = sorted(
        rows_with_metrics,
        key=lambda item: _sort_key_desc(item[1]["momentum_participation_score"]),
        reverse=True,
    )
    if is_grouped_industry_report:
        log_to_file(
            log_file,
            (
                f"{'Industry':<32} {'Entries':>7} {'Market':<18} {'Perf5D':>10} {'Perf1M':>10} {'PerfYTD':>10} {'PerfY':>10} {'RelVol':>10} {'Score':>12}"
            ),
        )
    else:
        log_to_file(
            log_file,
            (
                f"{'Ticker':<12} {'Name':<10} {'Industry':<28} {'Perf5D':>10} {'Perf1M':>10} {'PerfYTD':>10} {'PerfY':>10} {'RelVol':>10} {'Score':>12}"
            ),
        )
    log_to_file(log_file, "-" * 180)
    for row, metrics in ranked_rows_with_metrics[:TOP_SECTION_ROWS]:
        if is_grouped_industry_report:
            log_to_file(
                log_file,
                (
                    f"{_truncate_label(row.get('name') or row.get('industry'), 32):<32} {_format_entry_count(row):>7} "
                    f"{_truncate_label(row.get('market'), 18):<18} {_format_percent(_coerce_numeric(row.get('Perf.5D'))):>10} "
                    f"{_format_percent(metrics['perf_1m']):>10} {_format_percent(_coerce_numeric(row.get('Perf.YTD'))):>10} "
                    f"{_format_percent(_coerce_numeric(row.get('Perf.Y'))):>10} {_format_number(metrics['relative_volume']):>10} "
                    f"{_format_number(metrics['momentum_participation_score']):>12}"
                ),
            )
        else:
            log_to_file(
                log_file,
                (
                    f"{_get_symbol_name(row):<12} {str(row.get('name') or 'N/A')[:10]:<10} {str(row.get('industry') or '')[:28]:<28} "
                    f"{_format_percent(_coerce_numeric(row.get('Perf.5D'))):>10} {_format_percent(metrics['perf_1m']):>10} "
                    f"{_format_percent(_coerce_numeric(row.get('Perf.YTD'))):>10} {_format_percent(_coerce_numeric(row.get('Perf.Y'))):>10} "
                    f"{_format_number(metrics['relative_volume']):>10} {_format_number(metrics['momentum_participation_score']):>12}"
                ),
            )
    log_to_file(log_file, "")

    highest_score_row, highest_score_metrics = max(
        rows_with_metrics,
        key=lambda item: _sort_key_desc(item[1]["momentum_participation_score"]),
    )
    highest_perf_1m_row, highest_perf_1m_metrics = max(
        rows_with_metrics,
        key=lambda item: _sort_key_desc(item[1]["perf_1m"]),
    )
    highest_perf_y = max(
        scan_data,
        key=lambda row: _sort_key_desc(_coerce_numeric(row.get("Perf.Y"))),
    )

    log_to_file(log_file, "STANDOUTS")
    log_to_file(
        log_file,
        (
            f"- Strongest momentum with participation: {_row_label(highest_score_row)} | score={_format_number(highest_score_metrics['momentum_participation_score'])}. "
            "This stands out because recent 1M performance is being reinforced by elevated relative volume."
        ),
    )
    log_to_file(
        log_file,
        (
            f"- Highest 1M price move: {_row_label(highest_perf_1m_row)} | Perf.1M={_format_percent(highest_perf_1m_metrics['perf_1m'])}. "
            "This stands out because it has the strongest recent monthly price acceleration."
        ),
    )
    log_to_file(
        log_file,
        (
            f"- Highest yearly performance: {_row_label(highest_perf_y)} | Perf.Y={_format_percent(_coerce_numeric(highest_perf_y.get('Perf.Y')))}. "
            "This stands out because it leads the group on trailing yearly performance."
        ),
    )
    log_to_file(log_file, "")


def _log_trend_section(
    log_file: Path,
    scan_data: list[dict[str, Any]],
    is_grouped_industry_report: bool = False,
) -> None:
    _log_section_intro(
        log_file,
        "Section 4: Trend Confirmation",
        (
            "Purpose: check whether price is trading above or below major moving averages. More averages confirmed to the upside usually indicates a stronger technical trend backdrop."
        ),
    )
    rows_with_metrics = [(row, _build_trend_metrics(row)) for row in scan_data]
    ranked_rows_with_metrics = sorted(
        rows_with_metrics,
        key=lambda item: _sort_key_desc(item[1]["above_count"]),
        reverse=True,
    )
    if is_grouped_industry_report:
        log_to_file(
            log_file,
            f"{'Industry':<32} {'Entries':>7} {'Market':<18} {'Close':>10} {'AboveCnt':>10} {'TrendState':<62}",
        )
    else:
        log_to_file(
            log_file,
            f"{'Ticker':<12} {'Name':<10} {'Industry':<28} {'Close':>10} {'AboveCount':>10} {'TrendState':<80}",
        )
    log_to_file(log_file, "-" * 180)
    for row, metrics in ranked_rows_with_metrics[:TOP_SECTION_ROWS]:
        if is_grouped_industry_report:
            log_to_file(
                log_file,
                (
                    f"{_truncate_label(row.get('name') or row.get('industry'), 32):<32} {_format_entry_count(row):>7} "
                    f"{_truncate_label(row.get('market'), 18):<18} {_format_number(_coerce_numeric(row.get('close'))):>10} "
                    f"{_format_number(metrics['above_count']):>10} {_truncate_label(metrics['trend_state'], 62):<62}"
                ),
            )
        else:
            log_to_file(
                log_file,
                (
                    f"{_get_symbol_name(row):<12} {str(row.get('name') or 'N/A')[:10]:<10} {str(row.get('industry') or '')[:28]:<28} "
                    f"{_format_number(_coerce_numeric(row.get('close'))):>10} {_format_number(metrics['above_count']):>10} {str(metrics['trend_state'])[:80]:<80}"
                ),
            )
    log_to_file(log_file, "")

    strongest_trend_row, strongest_trend_metrics = max(
        rows_with_metrics,
        key=lambda item: _sort_key_desc(item[1]["above_count"]),
    )
    weakest_trend_row, weakest_trend_metrics = min(
        rows_with_metrics,
        key=lambda item: _sort_key_desc(item[1]["above_count"]),
    )

    log_to_file(log_file, "STANDOUTS")
    log_to_file(
        log_file,
        (
            f"- Strongest trend confirmation: {_row_label(strongest_trend_row)} | averages_above={_format_number(strongest_trend_metrics['above_count'])}. "
            "This stands out because price is above the largest number of key moving-average references."
        ),
    )
    log_to_file(
        log_file,
        (
            f"- Weakest trend confirmation: {_row_label(weakest_trend_row)} | averages_above={_format_number(weakest_trend_metrics['above_count'])}. "
            "This stands out because price is above the fewest key moving-average references."
        ),
    )
    log_to_file(log_file, "")


def _log_top_activity_table(
    log_file: Path,
    scan_data: list[dict[str, Any]],
    is_grouped_industry_report: bool = False,
) -> None:
    log_to_file(log_file, "Overview Table")
    log_to_file(log_file, "-" * 180)
    log_to_file(log_file, "Top rows by relative volume")
    log_to_file(log_file, "-" * 180)
    if is_grouped_industry_report:
        log_to_file(
            log_file,
            (
                f"{'Industry':<32} {'Entries':>7} {'Market':<18} {'MCap':>10} {'RelVol':>10} {'Vol':>14} {'ATRP':>10} {'Vol.W':>10} {'Perf5D':>10} {'Perf1M':>10} {'PerfY':>10}"
            ),
        )
    else:
        log_to_file(
            log_file,
            (
                f"{'Ticker':<12} {'Name':<10} {'Company':<34} {'Industry':<28} {'Market':<28} {'MCap':>10} "
                f"{'RelVol':>10} {'Vol':>14} {'PreVol/Float':>14} {'ATRP':>10} {'Vol.W':>10} {'Perf5D':>10} {'Perf1M':>10} {'PerfY':>10}"
            ),
        )
    log_to_file(log_file, "-" * 180)

    for row in scan_data:
        market_cap = _format_market_cap(_coerce_numeric(row.get("market_cap_basic")))
        relative_volume = _format_number(
            _coerce_numeric(row.get("relative_volume_10d_calc"))
        )
        volume = _format_number(_coerce_numeric(row.get("volume")))
        premarket_float_turnover = _format_number(
            _safe_ratio(
                _coerce_numeric(row.get("premarket_volume")),
                _coerce_numeric(row.get("float_shares_outstanding")),
            )
        )
        atrp = _format_number(_coerce_numeric(row.get("ATRP")))
        volatility_w = _format_number(_coerce_numeric(row.get("Volatility.W")))
        perf_5d = _format_percent(_coerce_numeric(row.get("Perf.5D")))
        perf_1m = _format_percent(_coerce_numeric(row.get("Perf.1M")))
        perf_y = _format_percent(_coerce_numeric(row.get("Perf.Y")))

        if is_grouped_industry_report:
            log_to_file(
                log_file,
                (
                    f"{_truncate_label(row.get('name') or row.get('industry'), 32):<32} {_format_entry_count(row):>7} "
                    f"{_truncate_label(row.get('market'), 18):<18} {market_cap:>10} {relative_volume:>10} {volume:>14} "
                    f"{atrp:>10} {volatility_w:>10} {perf_5d:>10} {perf_1m:>10} {perf_y:>10}"
                ),
            )
        else:
            ticker = _get_symbol_name(row)
            name = str(row.get("name") or "N/A")[:10]
            company = _get_company_description(row)[:34]
            industry = str(row.get("industry") or "")[:28]
            market = str(row.get("market") or "")[:28]
            log_to_file(
                log_file,
                (
                    f"{ticker:<12} {name:<10} {company:<34} {industry:<28} {market:<28} {market_cap:>10} "
                    f"{relative_volume:>10} {volume:>14} {premarket_float_turnover:>14} {atrp:>10} {volatility_w:>10} {perf_5d:>10} {perf_1m:>10} {perf_y:>10}"
                ),
            )

    log_to_file(log_file, "")

    top_row = scan_data[0]
    log_to_file(log_file, "STANDOUTS")
    log_to_file(
        log_file,
        (
            f"- First row leader: {_row_label(top_row)} | relvol={_format_number(_coerce_numeric(top_row.get('relative_volume_10d_calc')))} | "
            f"Perf.1M={_format_percent(_coerce_numeric(top_row.get('Perf.1M')))} | Perf.Y={_format_percent(_coerce_numeric(top_row.get('Perf.Y')))}. "
            "This stands out because the query itself is sorted by relative volume descending."
        ),
    )
    log_to_file(log_file, "")


def _log_metric_summaries(
    log_file: Path,
    scan_data: list[dict[str, Any]],
    profiles: dict[str, dict[str, float | int | None]],
) -> None:
    log_to_file(log_file, "All-Field Universe Statistics")
    log_to_file(log_file, "-" * 200)
    log_to_file(
        log_file,
        (
            f"{'Field':<32} {'Count':>6} {'Mean':>16} {'Median':>16} {'MAD':>16} {'Min':>16} {'Max':>16}"
        ),
    )
    log_to_file(log_file, "-" * 200)

    for field_name in ACTIVITY_FIELDS:
        profile = profiles.get(field_name, {})
        count = int(profile.get("count", 0))
        mean_val = profile.get("mean")
        median_val = profile.get("median")
        mad_val = profile.get("mad")
        min_val = profile.get("min")
        max_val = profile.get("max")

        mean_str = _format_number(mean_val) if mean_val is not None else "N/A"
        median_str = _format_number(median_val) if median_val is not None else "N/A"
        mad_str = _format_number(mad_val) if mad_val is not None else "N/A"
        min_str = _format_number(min_val) if min_val is not None else "N/A"
        max_str = _format_number(max_val) if max_val is not None else "N/A"

        log_to_file(
            log_file,
            (
                f"{field_name[:32]:<32} {count:>6} {mean_str:>16} {median_str:>16} "
                f"{mad_str:>16} {min_str:>16} {max_str:>16}"
            ),
        )

    log_to_file(log_file, "")

    highest_relvol = max(
        scan_data,
        key=lambda row: _sort_key_desc(
            _coerce_numeric(row.get("relative_volume_10d_calc"))
        ),
    )
    highest_atrp = max(
        scan_data,
        key=lambda row: _sort_key_desc(_coerce_numeric(row.get("ATRP"))),
    )
    highest_perf_1m = max(
        scan_data,
        key=lambda row: _sort_key_desc(_coerce_numeric(row.get("Perf.1M"))),
    )
    log_to_file(log_file, "STANDOUTS BY UNIVERSE POSITION")
    log_to_file(
        log_file,
        (
            f"- Highest relative volume: {_row_label(highest_relvol)} | relvol={_format_number(_coerce_numeric(highest_relvol.get('relative_volume_10d_calc')))}. "
            "Strongest abnormal participation vs. normal trading baseline."
        ),
    )
    log_to_file(
        log_file,
        (
            f"- Highest ATRP: {_row_label(highest_atrp)} | ATRP={_format_number(_coerce_numeric(highest_atrp.get('ATRP')))}. "
            "Widest average true range relative to price-most volatile."
        ),
    )
    log_to_file(
        log_file,
        (
            f"- Highest 1M performance: {_row_label(highest_perf_1m)} | Perf.1M={_format_percent(_coerce_numeric(highest_perf_1m.get('Perf.1M')))}. "
            "Strongest recent price acceleration in dataset."
        ),
    )
    log_to_file(log_file, "")


def _log_all_columns_per_ticker_table(
    log_file: Path,
    scan_data: list[dict[str, Any]],
    profiles: dict[str, dict[str, float | int | None]],
) -> None:
    _log_section_intro(log_file, "Dedicated Table: All Columns Per Ticker", "")

    headers = _build_all_columns_table_headers()
    log_to_file(log_file, " | ".join(headers))
    log_to_file(log_file, "-" * 240)

    for row in scan_data:
        row_values = _build_all_columns_table_row(row, profiles)
        log_to_file(log_file, " | ".join(row_values))

    log_to_file(log_file, "")


def _log_per_row_metric_deviations(
    log_file: Path,
    row: dict[str, Any],
    profiles: dict[str, dict[str, float | int | None]],
    max_deviations: int = 10,
) -> None:
    """Log top deviations for a row compared to universe medians using robust z-score."""
    deviations: list[tuple[str, float, float, float, float]] = []

    for field_name in ACTIVITY_FIELDS:
        profile = profiles.get(field_name, {})
        value = _coerce_numeric(row.get(field_name))
        if value is None:
            continue

        count = int(profile.get("count", 0))
        if count < 3:  # Need minimum sample size
            continue

        median_val = profile.get("median")
        mad_val = profile.get("mad")
        if median_val is None or mad_val is None or mad_val <= 0:
            continue

        # Robust z-score using MAD × 1.4826
        robust_sigma = mad_val * 1.4826
        robust_z = abs(value - median_val) / robust_sigma
        deviations.append((field_name, robust_z, value, median_val, mad_val))

    if not deviations:
        log_to_file(
            log_file, "      Metric deviations: none computed (insufficient data)"
        )
        return

    deviations.sort(key=lambda x: x[1], reverse=True)
    log_to_file(
        log_file, f"      Top metric deviations (robust z | value | median | MAD)"
    )
    for field, z_score, val, med, mad in deviations[:max_deviations]:
        log_to_file(
            log_file,
            (
                f"        - {field}: z={z_score:.2f} | val={_format_number(val)} | "
                f"median={_format_number(med)} | MAD={_format_number(mad)}"
            ),
        )
    log_to_file(log_file, "")


def _run_activity_float_attention_analysis(
    scan_data: list[dict[str, Any]],
    log_file: Path,
    csv_file: Path,
    title: str,
    metadata_lines: list[str],
) -> Path:
    _reset_log_file(log_file)
    is_grouped_industry_report = _is_grouped_industry_scan(scan_data)

    log_to_file(log_file, title)
    log_to_file(log_file, "=" * 180)
    for line in metadata_lines:
        log_to_file(log_file, line)
    log_to_file(log_file, "")

    if not scan_data:
        log_to_file(log_file, "No rows returned for this scan.")
        return log_file

    metric_profiles = _build_metric_profiles(scan_data)
    _export_all_columns_per_ticker_csv(csv_file, scan_data, metric_profiles)

    _log_top_activity_table(log_file, scan_data, is_grouped_industry_report)
    _log_metric_summaries(log_file, scan_data, metric_profiles)

    log_to_file(log_file, "Per-Row Metric Deviations (Top by Relative Volume)")
    log_to_file(log_file, "-" * 180)
    log_to_file(log_file, "")
    for idx, row in enumerate(scan_data[:TOP_SECTION_ROWS], 1):
        log_to_file(
            log_file,
            f"[{idx}] {_row_label(row)} | relvol={_format_number(_coerce_numeric(row.get('relative_volume_10d_calc')))}",
        )
        _log_per_row_metric_deviations(log_file, row, metric_profiles, max_deviations=8)
    log_to_file(log_file, "")

    _log_participation_section(log_file, scan_data, is_grouped_industry_report)
    _log_event_section(log_file, scan_data, is_grouped_industry_report)
    _log_momentum_section(log_file, scan_data, is_grouped_industry_report)
    _log_trend_section(log_file, scan_data, is_grouped_industry_report)
    return log_file


def analyze_activity_float_attention_scan(
    scan_data: list[dict[str, Any]],
    industries: list[str] | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
) -> Path:
    """Log a simple activity and float attention analysis for an already-fetched scan."""
    log_file = _build_log_file_name(industries, min_market_cap_usd, max_market_cap_usd)
    csv_file = _build_csv_file_name(industries, min_market_cap_usd, max_market_cap_usd)
    return _run_activity_float_attention_analysis(
        scan_data=scan_data,
        log_file=log_file,
        csv_file=csv_file,
        title=_build_report_title("TradingView activity / float / attention scan"),
        metadata_lines=[
            (
                f"Rows returned: {len(scan_data)} | industries={industries or 'all'} | "
                f"min_market_cap_usd={min_market_cap_usd} | max_market_cap_usd={max_market_cap_usd}"
            ),
            "Sorted by query on relative_volume_10d_calc descending.",
        ],
    )


def analyze_activity_float_attention_scan_grouped_industries(
    scan_data: list[dict[str, Any]],
    industries: list[str] | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
) -> Path:
    """Aggregate activity scan rows by industry and run the same analysis on industry-level rows."""
    grouped_scan_data = _group_scan_data_by_industry(scan_data)
    log_file = _build_grouped_industries_log_file_name(
        industries,
        min_market_cap_usd,
        max_market_cap_usd,
    )
    csv_file = _build_grouped_industries_csv_file_name(
        industries,
        min_market_cap_usd,
        max_market_cap_usd,
    )
    return _run_activity_float_attention_analysis(
        scan_data=grouped_scan_data,
        log_file=log_file,
        csv_file=csv_file,
        title=_build_report_title(
            "TradingView activity / float / attention scan grouped by industry"
        ),
        metadata_lines=[
            (
                f"Industry groups returned: {len(grouped_scan_data)} | source_rows={len(scan_data)} | "
                f"industries={industries or 'all'} | min_market_cap_usd={min_market_cap_usd} | "
                f"max_market_cap_usd={max_market_cap_usd}"
            ),
            (
                "Grouped from the raw all-industry scan using the industry field, then sorted by "
                "aggregate relative_volume_10d_calc descending."
            ),
        ],
    )


def run_activity_float_attention_scan(
    scan_data: list[dict[str, Any]],
    industries: list[str] | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
) -> Path:
    """Execute the TradingView scan and log the returned data under logs/tradingview_analysis."""
    return analyze_activity_float_attention_scan(
        scan_data=scan_data,
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
    )


def run_activity_float_attention_scan_grouped_industries(
    scan_data: list[dict[str, Any]],
    industries: list[str] | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
) -> Path:
    """Execute the activity scan analysis after aggregating rows by industry."""
    return analyze_activity_float_attention_scan_grouped_industries(
        scan_data=scan_data,
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
    )
