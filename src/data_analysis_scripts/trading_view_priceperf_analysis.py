from pathlib import Path
from typing import Any, Mapping
from datetime import datetime

from data_analysis_scripts._shared_analysis_utils import (
    coerce_numeric as _coerce_numeric,
)
from generic_utils.log_to_files_util import log_to_file


LOG_DIR = Path(
    r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis"
)


# Helper to get log file suffix with current date and hour
def _log_time_suffix() -> str:
    return datetime.now().strftime("_%Y%m%d_%H")


# Performance metrics ordered from oldest (longest) to latest (shortest) time horizon
PERFORMANCE_PERIODS_ORDERED = [
    "Perf.All",
    "Perf.10Y",
    "Perf.5Y",
    "Perf.3Y",
    "Perf.1Y",
    "Perf.6M",
    "Perf.YTD",
    "Perf.1M",
    "Perf.W",
    "Perf.5D",
    "change",
]

PERIOD_LABELS = {
    "Perf.All": "All Time",
    "Perf.10Y": "10 Year",
    "Perf.5Y": "5 Year",
    "Perf.3Y": "3 Year",
    "Perf.1Y": "1 Year",
    "Perf.6M": "6 Month",
    "Perf.YTD": "Year-to-Date",
    "Perf.1M": "1 Month",
    "Perf.W": "1 Week",
    "Perf.5D": "5 Day",
    "change": "Change Daily",
}


def _filter_by_market_cap(
    rows: list[dict],
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Filter rows by market cap constraints.
    Returns (included_rows, excluded_rows).
    """
    included = []
    excluded = []

    for row in rows:
        market_cap = _coerce_numeric(row.get("market_cap_basic"))

        if market_cap is None:
            excluded.append(row)
            continue

        if min_market_cap_usd is not None and market_cap < min_market_cap_usd:
            excluded.append(row)
            continue

        if max_market_cap_usd is not None and market_cap > max_market_cap_usd:
            excluded.append(row)
            continue

        included.append(row)

    return included, excluded


def _filter_by_performance_range(
    rows: list[dict],
    performance_field: str = "Perf.Y",
    min_performance_pct: float | None = None,
    max_performance_pct: float | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Filter rows by a performance field range.
    Returns (included_rows, excluded_rows).
    """
    if min_performance_pct is None and max_performance_pct is None:
        return rows, []

    included = []
    excluded = []

    for row in rows:
        perf_value = _coerce_numeric(row.get(performance_field))

        if perf_value is None:
            excluded.append(row)
            continue

        if min_performance_pct is not None and perf_value < min_performance_pct:
            excluded.append(row)
            continue

        if max_performance_pct is not None and perf_value > max_performance_pct:
            excluded.append(row)
            continue

        included.append(row)

    return included, excluded


def _get_ticker_name(row: dict) -> str:
    """Extract ticker name from row."""
    ticker_view = row.get("ticker-view")
    if isinstance(ticker_view, dict):
        return ticker_view.get("name", "N/A")
    return str(row.get("symbol", "N/A"))


def _format_market_cap(market_cap: float | None) -> str:
    """Format market cap in billions USD."""
    if market_cap is None:
        return "N/A"
    return f"${market_cap / 1e9:.2f}B"


def _format_performance_pct(perf_value: float | None) -> str:
    """Format performance value as percentage."""
    if perf_value is None:
        return "N/A"
    return f"{perf_value:+.2f}%"


def analyze_price_performance_by_market_cap(
    scan_data: list[dict],
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    min_performance_pct: float | Mapping[str, float] | None = None,
    max_performance_pct: float | Mapping[str, float] | None = None,
) -> None:
    """
    Analyze price performance metrics by market cap (descending).

    Creates separate sections for each performance period, ordered from oldest to latest.
    Stocks within each period are sorted by market cap descending.

    Args:
        scan_data: List of stock data from TradingView scan
        min_market_cap_usd: Minimum market cap filter (optional)
        max_market_cap_usd: Maximum market cap filter (optional)
        min_performance_pct: Minimum performance percentage filter applied per period (optional)
        max_performance_pct: Maximum performance percentage filter applied per period (optional)
    """
    included_rows, excluded_rows = _filter_by_market_cap(
        scan_data, min_market_cap_usd, max_market_cap_usd
    )

    if not included_rows:
        return

    # Sort by market cap descending
    included_rows.sort(
        key=lambda r: _coerce_numeric(r.get("market_cap_basic")) or 0, reverse=True
    )

    log_file = str(
        LOG_DIR / f"tradingview_price_performance_by_market_cap{_log_time_suffix()}.log"
    )

    log_to_file(log_file, "=" * 120)
    log_to_file(log_file, "PRICE PERFORMANCE ANALYSIS BY MARKET CAP (DESCENDING)")
    log_to_file(log_file, "=" * 120)
    log_to_file(
        log_file,
        f"Total stocks analyzed: {len(included_rows)} | Excluded: {len(excluded_rows)}\n",
    )

    # Process each performance period
    for period in PERFORMANCE_PERIODS_ORDERED:
        period_label = PERIOD_LABELS.get(period, period)

        # Determine min/max for this period
        min_pct = None
        max_pct = None
        if isinstance(min_performance_pct, dict):
            min_pct = min_performance_pct.get(period)
        elif isinstance(min_performance_pct, (int, float)):
            min_pct = min_performance_pct
        if isinstance(max_performance_pct, dict):
            max_pct = max_performance_pct.get(period)
        elif isinstance(max_performance_pct, (int, float)):
            max_pct = max_performance_pct

        # Check if period data exists in any row
        has_data = any(period in row for row in included_rows)
        if not has_data:
            continue

        log_to_file(log_file, "\n" + "-" * 120)
        log_to_file(log_file, f"PERIOD: {period_label} ({period})")
        log_to_file(log_file, "-" * 120)

        period_rows = []
        for row in included_rows:
            perf_value = _coerce_numeric(row.get(period))
            if perf_value is not None:
                if min_pct is not None and perf_value < min_pct:
                    continue
                if max_pct is not None and perf_value > max_pct:
                    continue
                period_rows.append(row)

        if not period_rows:
            log_to_file(log_file, "No data available for this period.\n")
            continue

        # Log header
        log_to_file(
            log_file,
            f"{'Ticker':<12} {'Name':<10} {'Company Name':<40} {'Market Cap':<15} {'Performance':<15}",
        )
        log_to_file(log_file, "-" * 140)

        # Log each row
        for row in period_rows:
            ticker = _get_ticker_name(row)
            name = row.get("name", "N/A")
            ticker_view = row.get("ticker-view")
            company_name = ""
            if isinstance(ticker_view, dict):
                company_name = ticker_view.get("description", "")[:38]

            market_cap_str = _format_market_cap(
                _coerce_numeric(row.get("market_cap_basic"))
            )
            perf_str = _format_performance_pct(_coerce_numeric(row.get(period)))

            log_to_file(
                log_file,
                f"{ticker:<12} {name:<10} {company_name:<40} {market_cap_str:>15} {perf_str:>15}",
            )

        log_to_file(log_file, "")


def analyze_price_performance_by_metric(
    scan_data: list[dict],
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    min_performance_pct: float | Mapping[str, float] | None = None,
    max_performance_pct: float | Mapping[str, float] | None = None,
) -> None:
    """
    Analyze price performance metrics sorted by performance value (descending).

    Creates table-style output for each performance period, sorted by performance descending.
    Market cap is visible in each row for context.

    Args:
        scan_data: List of stock data from TradingView scan
        min_market_cap_usd: Minimum market cap filter (optional)
        max_market_cap_usd: Maximum market cap filter (optional)
        min_performance_pct: Minimum performance percentage filter applied per period (optional)
        max_performance_pct: Maximum performance percentage filter applied per period (optional)
    """
    included_rows, excluded_rows = _filter_by_market_cap(
        scan_data, min_market_cap_usd, max_market_cap_usd
    )

    if not included_rows:
        return

    log_file = str(
        LOG_DIR
        / f"tradingview_price_performance_ranked_by_metric{_log_time_suffix()}.log"
    )

    log_to_file(log_file, "=" * 140)
    log_to_file(log_file, "PRICE PERFORMANCE ANALYSIS RANKED BY METRIC (DESCENDING)")
    log_to_file(log_file, "=" * 140)
    log_to_file(
        log_file,
        f"Total stocks analyzed: {len(included_rows)} | Excluded: {len(excluded_rows)}\n",
    )

    # Process each performance period
    for period in PERFORMANCE_PERIODS_ORDERED:
        period_label = PERIOD_LABELS.get(period, period)

        # Determine min/max for this period
        min_pct = None
        max_pct = None
        if isinstance(min_performance_pct, dict):
            min_pct = min_performance_pct.get(period)
        elif isinstance(min_performance_pct, (int, float)):
            min_pct = min_performance_pct
        if isinstance(max_performance_pct, dict):
            max_pct = max_performance_pct.get(period)
        elif isinstance(max_performance_pct, (int, float)):
            max_pct = max_performance_pct

        # Check if period data exists in any row
        has_data = any(period in row for row in included_rows)
        if not has_data:
            continue

        # Build list with performance value
        period_rows = []
        for row in included_rows:
            perf_value = _coerce_numeric(row.get(period))
            if perf_value is not None:
                if min_pct is not None and perf_value < min_pct:
                    continue
                if max_pct is not None and perf_value > max_pct:
                    continue
                period_rows.append((row, perf_value))

        if not period_rows:
            continue

        # Sort by performance descending
        period_rows.sort(key=lambda x: x[1], reverse=True)

        log_to_file(log_file, "\n" + "=" * 140)
        log_to_file(log_file, f"PERIOD: {period_label} ({period})")
        log_to_file(log_file, "=" * 140)

        # Log header
        log_to_file(
            log_file,
            f"{'Rank':<6} {'Ticker':<12} {'Name':<10} {'Company Name':<35} {'Market Cap':<18} {'Performance':<15}",
        )
        log_to_file(log_file, "-" * 160)

        # Log each row
        for rank, (row, perf_value) in enumerate(period_rows, 1):
            ticker = _get_ticker_name(row)
            name = row.get("name", "N/A")
            ticker_view = row.get("ticker-view")
            company_name = ""
            if isinstance(ticker_view, dict):
                company_name = ticker_view.get("description", "")[:33]

            market_cap_str = _format_market_cap(
                _coerce_numeric(row.get("market_cap_basic"))
            )
            perf_str = _format_performance_pct(perf_value)

            log_to_file(
                log_file,
                f"{rank:<6} {ticker:<12} {name:<10} {company_name:<35} {market_cap_str:>18} {perf_str:>15}",
            )

        log_to_file(log_file, "")


def analyze_global_price_performance(
    scan_data: list[dict],
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    performance_field: str | None = None,
    min_performance_pct: float | Mapping[str, float] | None = None,
    max_performance_pct: float | Mapping[str, float] | None = None,
) -> None:
    """
    Execute full price performance analysis workflow.

    Generates two output formats:
    1. Performance by market cap (sorted by market cap, separated by period)
    2. Performance by metric (sorted by performance, table format)

    Args:
        scan_data: List of stock data from TradingView scan
        min_market_cap_usd: Minimum market cap filter (optional)
        max_market_cap_usd: Maximum market cap filter (optional)
        performance_field: Optional single field for pre-filtering before all reports
        min_performance_pct: Minimum performance percentage filter (optional)
        max_performance_pct: Maximum performance percentage filter (optional)
    """

    if not scan_data:
        log_file = str(
            LOG_DIR
            / f"tradingview_price_performance_by_market_cap{_log_time_suffix()}.log"
        )
        log_to_file(log_file, "No data available from TradingView scan.")
        return

    filtered_scan_data = scan_data

    # Optional single-field pre-filter (backward-compatible behavior).
    if performance_field is not None:
        filtered_scan_data, excluded_for_performance = _filter_by_performance_range(
            scan_data,
            performance_field=performance_field,
            min_performance_pct=min_performance_pct,
            max_performance_pct=max_performance_pct,
        )

        if not filtered_scan_data:
            log_file = str(
                LOG_DIR
                / f"tradingview_price_performance_by_market_cap{_log_time_suffix()}.log"
            )
            log_to_file(
                log_file,
                (
                    "No data after performance filtering. "
                    f"Field={performance_field}, min={min_performance_pct}, "
                    f"max={max_performance_pct}, excluded={len(excluded_for_performance)}"
                ),
            )
            return

    # Generate both output formats
    analyze_price_performance_by_market_cap(
        filtered_scan_data,
        min_market_cap_usd,
        max_market_cap_usd,
        min_performance_pct,
        max_performance_pct,
    )
    analyze_price_performance_by_metric(
        filtered_scan_data,
        min_market_cap_usd,
        max_market_cap_usd,
        min_performance_pct,
        max_performance_pct,
    )
