from statistics import median
from pathlib import Path

from constants.trading_view_constants import TRADING_VIEW_INDUSTRIES
from generic_utils.log_to_files_util import log_to_file

LOG_DIR = Path(
    r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis"
)
LOG_FILE = LOG_DIR / "tradingview_ev_ebitda_deviation.log"
INDUSTRY_ANALYSIS_LOG_FILE = LOG_DIR / "tradingview_global_industry_performance.log"

EV_EBITDA_FIELD = "enterprise_value_ebitda_ttm"
MIN_MARKET_CAP_USD = 10_000_000_000
TRIM_RATIO = 0.05

INDUSTRY_ANALYSIS_FIELDS = [
    "market_cap_basic",
    "Perf.1Y.MarketCap",
    "Perf.Y",
    "price_earnings_ttm",
    "price_earnings_growth_ttm",
    "price_sales_current",
    "price_book_fq",
    "price_to_cash_f_operating_activities_ttm",
    "price_free_cash_flow_ttm",
    "price_to_cash_ratio",
    "enterprise_value_current",
    "enterprise_value_to_revenue_ttm",
    "enterprise_value_to_ebit_ttm",
    "enterprise_value_ebitda_ttm",
]

INDUSTRY_MULTIPLE_FIELDS = [
    "price_earnings_ttm",
    "price_earnings_growth_ttm",
    "price_sales_current",
    "price_book_fq",
    "price_to_cash_f_operating_activities_ttm",
    "price_free_cash_flow_ttm",
    "price_to_cash_ratio",
    "enterprise_value_to_revenue_ttm",
    "enterprise_value_to_ebit_ttm",
    "enterprise_value_ebitda_ttm",
]

FIELD_LABELS = {
    "price_earnings_ttm": "P/E TTM",
    "price_earnings_growth_ttm": "PEG TTM",
    "price_sales_current": "Price/Sales",
    "price_book_fq": "Price/Book",
    "price_to_cash_f_operating_activities_ttm": "Price/CFO",
    "price_free_cash_flow_ttm": "Price/FCF",
    "price_to_cash_ratio": "Price/Cash",
    "enterprise_value_to_revenue_ttm": "EV/Revenue",
    "enterprise_value_to_ebit_ttm": "EV/EBIT",
    "enterprise_value_ebitda_ttm": "EV/EBITDA",
}

MULTIPLE_VALIDATION_FIELDS = [
    "price_earnings_ttm",
    "price_sales_current",
    "enterprise_value_to_revenue_ttm",
    "enterprise_value_to_ebit_ttm",
    "enterprise_value_ebitda_ttm",
]


def _get_tradingview_industry_values() -> set[str]:
    return {
        value
        for key, value in vars(TRADING_VIEW_INDUSTRIES).items()
        if key.isupper() and isinstance(value, str)
    }


KNOWN_TRADINGVIEW_INDUSTRIES = _get_tradingview_industry_values()


def _coerce_numeric(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _resolve_industry(row: dict) -> str:
    industry = row.get("industry") or row.get("industry.tr")
    if not industry:
        return "Unknown"
    if industry in KNOWN_TRADINGVIEW_INDUSTRIES:
        return industry
    return str(industry)


def _trim_values(values: list[float], trim_ratio: float) -> list[float]:
    if not values:
        return []

    if trim_ratio <= 0:
        return list(values)

    trim_count = int(len(values) * trim_ratio)
    if trim_count == 0 or (trim_count * 2) >= len(values):
        return list(values)

    sorted_values = sorted(values)
    return sorted_values[trim_count : len(sorted_values) - trim_count]


def _build_field_stats(
    rows: list[dict],
    field_name: str,
    trim_ratio: float = 0.0,
) -> dict:
    values = []
    for row in rows:
        numeric_value = _coerce_numeric(row.get(field_name))
        if numeric_value is not None:
            values.append(numeric_value)

    original_count = len(values)
    trimmed_values = _trim_values(values, trim_ratio)
    trimmed_count = original_count - len(trimmed_values)

    if not trimmed_values:
        return {
            "source_count": original_count,
            "count": 0,
            "trimmed_count": trimmed_count,
            "mean": None,
            "median": None,
        }

    mean_value = sum(trimmed_values) / len(trimmed_values)
    return {
        "source_count": original_count,
        "count": len(trimmed_values),
        "trimmed_count": trimmed_count,
        "mean": mean_value,
        "median": median(trimmed_values),
    }


def _build_stats_map(rows: list[dict], trim_ratio: float = 0.0) -> dict[str, dict]:
    return {
        field_name: _build_field_stats(rows, field_name, trim_ratio)
        for field_name in INDUSTRY_ANALYSIS_FIELDS
    }


def _format_metric_value(field_name: str, value) -> str:
    if value is None:
        return "N/A"

    if field_name in {"market_cap_basic", "enterprise_value_current"}:
        return f"{value / 1e9:,.2f}B"

    if field_name in {"Perf.1Y.MarketCap", "Perf.Y"}:
        return f"{value:+.2f}%"

    return f"{value:,.2f}"


def _format_multiple_value(field_name: str, value) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.2f}x"


def _format_deviation_pct(local_value, global_value) -> str:
    if local_value is None or global_value is None:
        return "N/A"

    if global_value == 0:
        return "N/A (global value = 0)"

    deviation_pct = ((local_value - global_value) / abs(global_value)) * 100
    direction = "above" if deviation_pct >= 0 else "below"
    return f"{abs(deviation_pct):.2f}% {direction} global value"


def _format_deviation_vs_baseline(value, baseline_value, baseline_label: str) -> str:
    if value is None or baseline_value is None:
        return "N/A"

    if baseline_value == 0:
        return f"N/A ({baseline_label} = 0)"

    deviation_pct = ((value - baseline_value) / abs(baseline_value)) * 100
    direction = "above" if deviation_pct >= 0 else "below"
    return f"{abs(deviation_pct):.2f}% {direction} {baseline_label}"


def _format_signed_deviation_pct(value, baseline_value) -> str:
    if value is None or baseline_value is None:
        return "N/A"

    if baseline_value == 0:
        return "N/A"

    deviation_pct = ((value - baseline_value) / abs(baseline_value)) * 100
    return f"{deviation_pct:+.2f}%"


def _format_trim_info(stats: dict, trim_ratio: float) -> str:
    if trim_ratio <= 0:
        return ""

    return (
        f"  source_n={stats['source_count']:<4}" f" trimmed={stats['trimmed_count']:<4}"
    )


def _get_field_mean(stats: dict) -> float | None:
    return stats.get("mean")


def _get_exclusion_reasons(row: dict) -> list[str]:
    reasons = []

    market_cap = _coerce_numeric(row.get("market_cap_basic"))
    if market_cap is None or market_cap < MIN_MARKET_CAP_USD:
        reasons.append("market_cap_basic below 1B USD")

    price_earnings = _coerce_numeric(row.get("price_earnings_ttm"))
    if price_earnings is not None and price_earnings < 0:
        reasons.append("price_earnings_ttm negative")

    for field_name in MULTIPLE_VALIDATION_FIELDS:
        value = _coerce_numeric(row.get(field_name))
        if value is None:
            continue
        if value < 0:
            reasons.append(f"{field_name} negative")

    return reasons


def _filter_scan_rows(scan_data: list[dict]) -> tuple[list[dict], list[dict]]:
    included_rows = []
    excluded_rows = []

    for row in scan_data:
        reasons = _get_exclusion_reasons(row)
        if reasons:
            excluded_rows.append(
                {
                    "symbol": row.get("symbol", "N/A"),
                    "industry": _resolve_industry(row),
                    "reasons": reasons,
                }
            )
            continue

        included_rows.append(row)

    return included_rows, excluded_rows


def _group_rows_by_industry(rows: list[dict]) -> dict[str, list[dict]]:
    grouped_rows = {}
    for row in rows:
        industry = _resolve_industry(row)
        grouped_rows.setdefault(industry, []).append(row)
    return grouped_rows


def _build_industry_log_file(industry_name: str) -> str:
    slug = "".join(
        char.lower() if char.isalnum() else "_" for char in str(industry_name)
    )
    normalized_slug = "_".join(part for part in slug.split("_") if part)
    log_dir = Path(INDUSTRY_ANALYSIS_LOG_FILE).resolve().parent
    return str(log_dir / f"tradingview_industry_multiples_{normalized_slug}.log")


def _get_company_description(row: dict) -> str:
    ticker_view = row.get("ticker-view")
    if isinstance(ticker_view, dict):
        description = ticker_view.get("description")
        if description:
            return str(description)
    return ""


def _build_metric_exclusions(
    row: dict,
    field_name: str,
    min_market_cap_usd: float | None = None,
) -> list[str]:
    reasons = []
    market_cap = _coerce_numeric(row.get("market_cap_basic"))

    if min_market_cap_usd is not None:
        if market_cap is None:
            reasons.append("market_cap_basic missing or non-numeric")
        elif market_cap < min_market_cap_usd:
            reasons.append(
                f"market_cap_basic below {min_market_cap_usd / 1e9:.2f}B USD"
            )

    numeric_value = _coerce_numeric(row.get(field_name))

    if numeric_value is None:
        reasons.append(f"{field_name} missing or non-numeric")
    elif numeric_value < 0:
        reasons.append(f"{field_name} negative")

    return reasons


def _filter_rows_for_metric(
    rows: list[dict],
    field_name: str,
    min_market_cap_usd: float | None = None,
) -> tuple[list[dict], list[dict]]:
    included_rows = []
    excluded_rows = []

    for row in rows:
        reasons = _build_metric_exclusions(row, field_name, min_market_cap_usd)
        if reasons:
            excluded_rows.append(
                {
                    "symbol": row.get("symbol", "N/A"),
                    "description": _get_company_description(row),
                    "industry": _resolve_industry(row),
                    "market_cap_basic": row.get("market_cap_basic"),
                    "raw_value": row.get(field_name),
                    "reasons": reasons,
                }
            )
            continue

        included_rows.append(row)

    return included_rows, excluded_rows


def _log_metric_exclusions(log_file: str, excluded_rows: list[dict]) -> None:
    if not excluded_rows:
        log_to_file(log_file, "Excluded for this metric: none")
        return

    log_to_file(log_file, f"Excluded for this metric: {len(excluded_rows)}")
    for excluded_row in excluded_rows:
        description = excluded_row["description"]
        description_segment = f" {description:<35}" if description else ""
        market_cap = _coerce_numeric(excluded_row.get("market_cap_basic"))
        market_cap_segment = (
            f"market_cap: {market_cap / 1e9:,.2f}B | " if market_cap is not None else ""
        )
        log_to_file(
            log_file,
            (
                f"  {excluded_row['symbol']:<20}{description_segment}"
                f"{market_cap_segment}"
                f"raw_value: {excluded_row['raw_value']} | "
                f"Reasons: {', '.join(excluded_row['reasons'])}"
            ),
        )


def _log_industry_multiple_section(
    log_file: str,
    industry_rows: list[dict],
    industry_name: str,
    field_name: str,
    min_market_cap_usd: float | None = None,
) -> None:
    metric_label = FIELD_LABELS.get(field_name, field_name)
    metric_rows, excluded_rows = _filter_rows_for_metric(
        industry_rows,
        field_name,
        min_market_cap_usd=min_market_cap_usd,
    )

    metric_stats = _build_field_stats(metric_rows, field_name)

    log_to_file(log_file, f"Metric: {metric_label} ({field_name})")
    log_to_file(log_file, "-" * 100)
    log_to_file(
        log_file,
        (
            f"Industry: {industry_name} | total companies in industry input: {len(industry_rows)} | "
            f"usable for metric: {metric_stats['count']} | excluded for metric: {len(excluded_rows)}"
        ),
    )
    log_to_file(
        log_file,
        (
            f"Exclusions: {field_name} missing/non-numeric, {field_name} negative"
            f"{f', market_cap_basic below {min_market_cap_usd / 1e9:.2f}B USD' if min_market_cap_usd is not None else ''}."
        ),
    )
    _log_metric_exclusions(log_file, excluded_rows)

    if metric_stats["count"] == 0:
        log_to_file(
            log_file,
            f"No valid {metric_label} observations remain for {industry_name} after exclusions.",
        )
        log_to_file(log_file, "")
        return

    log_to_file(log_file, "Baselines")
    log_to_file(
        log_file,
        (
            f"  Mean:   {_format_multiple_value(field_name, metric_stats['mean']):>14}  "
            f"Median: {_format_multiple_value(field_name, metric_stats['median']):>14}"
        ),
    )
    log_to_file(log_file, "")
    log_to_file(log_file, "Company deviations")

    sorted_rows = sorted(
        metric_rows,
        key=lambda row: (
            _coerce_numeric(row.get("market_cap_basic")) or float("-inf"),
            _coerce_numeric(row.get(field_name)) or float("-inf"),
        ),
        reverse=True,
    )
    for row in sorted_rows:
        value = _coerce_numeric(row.get(field_name))
        symbol = row.get("symbol", "N/A")
        description = _get_company_description(row)
        market_cap = _coerce_numeric(row.get("market_cap_basic"))
        market_cap_text = (
            f"{market_cap / 1e9:>8.2f}B" if market_cap is not None else "     N/A"
        )
        log_to_file(
            log_file,
            (
                f"  {symbol:<20} {description:<35} "
                f"mcap: {market_cap_text}  "
                f"value: {_format_multiple_value(field_name, value):>12}  "
                f"mean: {_format_signed_deviation_pct(value, metric_stats['mean'])}  "
                f"median: {_format_signed_deviation_pct(value, metric_stats['median'])}"
            ),
        )

    log_to_file(log_file, "")


def _log_industry_multiples_summary(
    log_file: str,
    industry_rows: list[dict],
    industry_name: str,
    min_market_cap_usd: float | None = None,
) -> None:
    log_to_file(log_file, "Industry stats summary")
    log_to_file(log_file, "-" * 100)
    log_to_file(
        log_file,
        (
            f"Industry: {industry_name} | input companies: {len(industry_rows)}"
            f"{f' | minimum market cap: {min_market_cap_usd / 1e9:.2f}B USD' if min_market_cap_usd is not None else ''}"
        ),
    )

    for field_name in INDUSTRY_MULTIPLE_FIELDS:
        metric_rows, excluded_rows = _filter_rows_for_metric(
            industry_rows,
            field_name,
            min_market_cap_usd=min_market_cap_usd,
        )
        metric_stats = _build_field_stats(metric_rows, field_name)
        metric_label = FIELD_LABELS.get(field_name, field_name)
        log_to_file(
            log_file,
            (
                f"  {metric_label:<18} n={metric_stats['count']:<4} "
                f"excluded={len(excluded_rows):<4} "
                f"mean: {_format_multiple_value(field_name, metric_stats['mean']):>12}  "
                f"median: {_format_multiple_value(field_name, metric_stats['median']):>12}"
            ),
        )

    log_to_file(log_file, "")


def _log_global_stats(
    log_file: str, rows: list[dict], trim_ratio: float, label: str
) -> dict:
    global_stats = _build_stats_map(rows, trim_ratio)

    log_to_file(log_file, label)
    log_to_file(log_file, "-" * 100)
    for field_name in INDUSTRY_ANALYSIS_FIELDS:
        stats = global_stats[field_name]
        log_to_file(
            log_file,
            (
                f"  {field_name:<40} n={stats['count']:<4} "
                f"{_format_trim_info(stats, trim_ratio)}"
                f"mean: { _format_metric_value(field_name, stats['mean']) :>14}  "
                f"median: { _format_metric_value(field_name, stats['median']) :>14}"
            ),
        )
    log_to_file(log_file, "")

    return global_stats


def _log_industry_stats(
    log_file: str,
    rows: list[dict],
    trim_ratio: float,
    label: str,
) -> None:
    grouped_rows = _group_rows_by_industry(rows)
    global_stats = _log_global_stats(
        log_file,
        rows,
        trim_ratio,
        f"{label} - Global baselines",
    )

    log_to_file(log_file, f"{label} - Industry aggregates")
    log_to_file(log_file, "-" * 100)

    perf_means_by_industry = {
        industry_name: _get_field_mean(
            _build_field_stats(industry_rows, "Perf.1Y.MarketCap", trim_ratio)
        )
        for industry_name, industry_rows in grouped_rows.items()
    }

    sorted_industries = sorted(
        grouped_rows.items(),
        key=lambda item: (
            (
                perf_means_by_industry[item[0]]
                if perf_means_by_industry[item[0]] is not None
                else float("-inf")
            ),
            len(item[1]),
        ),
        reverse=True,
    )

    for industry_name, industry_rows in sorted_industries:
        industry_stats = _build_stats_map(industry_rows, trim_ratio)
        log_to_file(
            log_file,
            f"Industry: {industry_name} | companies: {len(industry_rows)}",
        )
        for field_name in INDUSTRY_ANALYSIS_FIELDS:
            industry_mean = industry_stats[field_name]["mean"]
            industry_median = industry_stats[field_name]["median"]
            global_mean = global_stats[field_name]["mean"]
            global_median = global_stats[field_name]["median"]
            log_to_file(
                log_file,
                (
                    f"  {field_name:<40} n={industry_stats[field_name]['count']:<4} "
                    f"{_format_trim_info(industry_stats[field_name], trim_ratio)}"
                    f"industry mean: { _format_metric_value(field_name, industry_mean) :>14}  "
                    f"industry median: { _format_metric_value(field_name, industry_median) :>14}  "
                    f"mean deviation: {_format_deviation_pct(industry_mean, global_mean)}  "
                    f"median deviation: {_format_deviation_pct(industry_median, global_median)}"
                ),
            )
        log_to_file(log_file, "")


def analyze_global_market_performance_by_industry(scan_data: list[dict]) -> None:
    included_rows, excluded_rows = _filter_scan_rows(scan_data)

    log_to_file(
        INDUSTRY_ANALYSIS_LOG_FILE,
        "Global market performance by industry analysis",
    )
    log_to_file(INDUSTRY_ANALYSIS_LOG_FILE, "=" * 100)
    log_to_file(
        INDUSTRY_ANALYSIS_LOG_FILE,
        f"Input companies: {len(scan_data)} | Included after filters: {len(included_rows)} | Excluded: {len(excluded_rows)}",
    )
    log_to_file(
        INDUSTRY_ANALYSIS_LOG_FILE,
        "Applied exclusions: market_cap_basic below 5B USD, negative price_earnings_ttm, and any negative",
    )
    log_to_file(INDUSTRY_ANALYSIS_LOG_FILE, "")

    if excluded_rows:
        log_to_file(INDUSTRY_ANALYSIS_LOG_FILE, "Excluded companies")
        log_to_file(INDUSTRY_ANALYSIS_LOG_FILE, "-" * 100)
        for excluded_row in excluded_rows:
            log_to_file(
                INDUSTRY_ANALYSIS_LOG_FILE,
                (
                    f"  {excluded_row['symbol']:<20} {excluded_row['industry']:<35} "
                    f"Reasons: {', '.join(excluded_row['reasons'])}"
                ),
            )
        log_to_file(INDUSTRY_ANALYSIS_LOG_FILE, "")

    if not included_rows:
        log_to_file(
            INDUSTRY_ANALYSIS_LOG_FILE,
            "No companies remain after applying the requested filters.",
        )
        return

    _log_industry_stats(
        INDUSTRY_ANALYSIS_LOG_FILE,
        included_rows,
        trim_ratio=0.0,
        label="Untrimmed analysis",
    )

    _log_industry_stats(
        INDUSTRY_ANALYSIS_LOG_FILE,
        included_rows,
        trim_ratio=TRIM_RATIO,
        label="Trimmed analysis (top/bottom 5% removed per metric; per-field trimmed counts shown)",
    )


def analyze_industry_multiples(
    scan_data: list[dict],
    industry_name: str,
    min_market_cap_usd: float | None = None,
) -> None:
    log_file = _build_industry_log_file(industry_name)
    industry_rows = [
        row for row in scan_data if _resolve_industry(row) == str(industry_name)
    ]

    log_to_file(
        log_file,
        f"Industry multiples analysis for {industry_name}",
    )
    log_to_file(log_file, "=" * 100)
    log_to_file(
        log_file,
        (
            f"Input rows: {len(scan_data)} | Rows matched to industry '{industry_name}': {len(industry_rows)}"
        ),
    )
    log_to_file(
        log_file,
        "Method: each multiple is analyzed against the industry mean and median only. Exclusions are applied per metric.",
    )
    log_to_file(log_file, "")

    if not industry_rows:
        log_to_file(
            log_file,
            f"No rows found for industry '{industry_name}'. Verify the TradingView industry label passed to the method.",
        )
        return

    _log_industry_multiples_summary(
        log_file=log_file,
        industry_rows=industry_rows,
        industry_name=industry_name,
        min_market_cap_usd=min_market_cap_usd,
    )

    for field_name in INDUSTRY_MULTIPLE_FIELDS:
        _log_industry_multiple_section(
            log_file=log_file,
            industry_rows=industry_rows,
            industry_name=industry_name,
            field_name=field_name,
            min_market_cap_usd=min_market_cap_usd,
        )


def analyze_ev_ebitda_deviation(scan_data: list[dict]) -> None:
    """
    Run valuation analysis across all valuation multiple fields in the received selection.

    For each multiple field:
    - compute mean and median
    - list companies sorted by market cap descending
    - highlight standouts (cheapest / richest / deviation extremes)
    - track elements that can distort interpretation
    """

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text("", encoding="utf-8")

    if not scan_data:
        log_to_file(LOG_FILE, "No scan data received for valuation analysis.")
        return

    log_to_file(LOG_FILE, "Valuation Multiples Analysis")
    log_to_file(LOG_FILE, "=" * 120)
    log_to_file(LOG_FILE, f"Input companies: {len(scan_data)}")
    log_to_file(
        LOG_FILE,
        "Method: analyze each valuation multiple independently with metric-specific exclusions.",
    )
    log_to_file(
        LOG_FILE,
        "Company rows in each metric section are sorted by market_cap_basic descending.",
    )
    log_to_file(LOG_FILE, "")

    for field_name in INDUSTRY_MULTIPLE_FIELDS:
        metric_label = FIELD_LABELS.get(field_name, field_name)
        metric_rows, excluded_rows = _filter_rows_for_metric(scan_data, field_name)
        metric_stats = _build_field_stats(metric_rows, field_name)

        log_to_file(LOG_FILE, f"Metric: {metric_label} ({field_name})")
        log_to_file(LOG_FILE, "-" * 120)
        log_to_file(
            LOG_FILE,
            (
                f"Usable rows: {metric_stats['count']} | Excluded rows: {len(excluded_rows)} | "
                f"Mean: {_format_multiple_value(field_name, metric_stats['mean'])} | "
                f"Median: {_format_multiple_value(field_name, metric_stats['median'])}"
            ),
        )

        if metric_stats["count"] == 0:
            log_to_file(LOG_FILE, "No valid rows for this metric after exclusions.")
            reason_counts = {}
            for excluded in excluded_rows:
                for reason in excluded["reasons"]:
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if reason_counts:
                log_to_file(LOG_FILE, "Distortion tracking")
                for reason, count in sorted(
                    reason_counts.items(), key=lambda item: item[1], reverse=True
                ):
                    log_to_file(LOG_FILE, f"  - {reason}: {count}")
            log_to_file(LOG_FILE, "")
            continue

        sorted_rows = sorted(
            metric_rows,
            key=lambda row: (_coerce_numeric(row.get("market_cap_basic")) or 0),
            reverse=True,
        )

        log_to_file(
            LOG_FILE,
            (
                f"{'Ticker':<20} {'Company':<35} {'MCap':>10} {'Perf1Y':>10} "
                f"{'Value':>12} {'DevMean':>10} {'DevMedian':>10}"
            ),
        )
        log_to_file(LOG_FILE, "-" * 120)
        for row in sorted_rows:
            symbol = row.get("symbol", "N/A")
            description = _get_company_description(row)[:35]
            market_cap = _coerce_numeric(row.get("market_cap_basic"))
            market_cap_text = (
                f"{market_cap / 1e9:.2f}B" if market_cap is not None else "N/A"
            )
            perf_1y = _coerce_numeric(row.get("Perf.1Y.MarketCap"))
            perf_1y_text = f"{perf_1y:+.2f}%" if perf_1y is not None else "N/A"
            metric_value = _coerce_numeric(row.get(field_name))
            log_to_file(
                LOG_FILE,
                (
                    f"{symbol:<20} {description:<35} {market_cap_text:>10} {perf_1y_text:>10} "
                    f"{_format_multiple_value(field_name, metric_value):>12} "
                    f"{_format_signed_deviation_pct(metric_value, metric_stats['mean']):>10} "
                    f"{_format_signed_deviation_pct(metric_value, metric_stats['median']):>10}"
                ),
            )

        cheapest_row = min(
            metric_rows,
            key=lambda row: (
                _coerce_numeric(row.get(field_name))
                if _coerce_numeric(row.get(field_name)) is not None
                else float("inf")
            ),
        )
        richest_row = max(
            metric_rows,
            key=lambda row: (
                _coerce_numeric(row.get(field_name))
                if _coerce_numeric(row.get(field_name)) is not None
                else float("-inf")
            ),
        )
        most_discounted_row = min(
            metric_rows,
            key=lambda row: (
                (_coerce_numeric(row.get(field_name)) - metric_stats["median"])
                / abs(metric_stats["median"])
                if (
                    _coerce_numeric(row.get(field_name)) is not None
                    and metric_stats["median"] not in (None, 0)
                )
                else float("inf")
            ),
        )
        most_premium_row = max(
            metric_rows,
            key=lambda row: (
                (_coerce_numeric(row.get(field_name)) - metric_stats["median"])
                / abs(metric_stats["median"])
                if (
                    _coerce_numeric(row.get(field_name)) is not None
                    and metric_stats["median"] not in (None, 0)
                )
                else float("-inf")
            ),
        )

        log_to_file(LOG_FILE, "")
        log_to_file(LOG_FILE, "STANDOUTS")
        log_to_file(
            LOG_FILE,
            (
                f"- Cheapest on {metric_label}: {cheapest_row.get('symbol', 'N/A')} "
                f"at {_format_multiple_value(field_name, _coerce_numeric(cheapest_row.get(field_name)))}"
            ),
        )
        log_to_file(
            LOG_FILE,
            (
                f"- Richest on {metric_label}: {richest_row.get('symbol', 'N/A')} "
                f"at {_format_multiple_value(field_name, _coerce_numeric(richest_row.get(field_name)))}"
            ),
        )
        log_to_file(
            LOG_FILE,
            (
                f"- Most discounted vs median: {most_discounted_row.get('symbol', 'N/A')} "
                f"({_format_signed_deviation_pct(_coerce_numeric(most_discounted_row.get(field_name)), metric_stats['median'])})"
            ),
        )
        log_to_file(
            LOG_FILE,
            (
                f"- Most premium vs median: {most_premium_row.get('symbol', 'N/A')} "
                f"({_format_signed_deviation_pct(_coerce_numeric(most_premium_row.get(field_name)), metric_stats['median'])})"
            ),
        )

        reason_counts = {}
        for excluded in excluded_rows:
            for reason in excluded["reasons"]:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1

        outlier_rows = [
            row
            for row in metric_rows
            if (
                _coerce_numeric(row.get(field_name)) is not None
                and metric_stats["median"] not in (None, 0)
                and (
                    _coerce_numeric(row.get(field_name)) > (metric_stats["median"] * 3)
                    or _coerce_numeric(row.get(field_name))
                    < (metric_stats["median"] / 3)
                )
            )
        ]

        total_metric_mcap = sum(
            _coerce_numeric(row.get("market_cap_basic")) or 0 for row in metric_rows
        )
        largest_metric_row = max(
            metric_rows,
            key=lambda row: _coerce_numeric(row.get("market_cap_basic")) or 0,
        )
        largest_mcap = _coerce_numeric(largest_metric_row.get("market_cap_basic")) or 0
        largest_mcap_share = (
            (largest_mcap / total_metric_mcap) * 100 if total_metric_mcap > 0 else None
        )

        log_to_file(LOG_FILE, "")
        log_to_file(LOG_FILE, "DISTORTION TRACKING")
        if reason_counts:
            for reason, count in sorted(
                reason_counts.items(), key=lambda item: item[1], reverse=True
            ):
                log_to_file(LOG_FILE, f"- Excluded ({reason}): {count}")
        else:
            log_to_file(LOG_FILE, "- Excluded rows: none")

        log_to_file(
            LOG_FILE,
            (
                f"- Extreme-value outliers (>3x median or <1/3 median): {len(outlier_rows)}"
            ),
        )
        if largest_mcap_share is not None:
            log_to_file(
                LOG_FILE,
                (
                    f"- Largest market-cap concentration in usable set: {largest_metric_row.get('symbol', 'N/A')} "
                    f"at {largest_mcap_share:.2f}% of usable metric market cap"
                ),
            )
        log_to_file(LOG_FILE, "")
