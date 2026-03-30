"""Shared helpers used across multiple TradingView analysis modules.

Every function here was originally duplicated (identically or near-identically)
in two or more analysis scripts.  Consolidating them into one place removes the
duplication while keeping each analysis module's public API unchanged.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any


REPORT_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def coerce_numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def slugify(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "_" for char in str(value))
    return "_".join(part for part in slug.split("_") if part)


def safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def reset_log_file(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("", encoding="utf-8")


def format_market_cap(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value / 1e9:.2f}B"


def format_signed_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2f}%"


def format_number(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.{decimals}f}"


def format_raw_value(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        formatted = f"{value:.6f}".rstrip("0").rstrip(".")
        return formatted or "0"
    return str(value)


def get_symbol_name(row: dict[str, Any]) -> str:
    """Extract display name from a scan row.

    Uses ticker-view → name first, then falls back to ``name`` → ``symbol``.
    Modules that need a different fallback chain should keep their own helper.
    """
    ticker_view = row.get("ticker-view")
    if isinstance(ticker_view, dict):
        name = ticker_view.get("name")
        if name:
            return str(name)
    return str(row.get("name") or row.get("symbol") or "N/A")


def get_company_description(row: dict[str, Any]) -> str:
    """Return the company description from ticker-view, or empty string."""
    ticker_view = row.get("ticker-view")
    if isinstance(ticker_view, dict):
        description = ticker_view.get("description")
        if description:
            return str(description)
    return ""


def build_report_title(report_name: str) -> str:
    timestamp = datetime.now().strftime(REPORT_TIMESTAMP_FORMAT)
    return f"{report_name} | generated {timestamp}"


def sort_key_desc(value: float | None) -> float:
    if value is None:
        return float("-inf")
    return value


def median_absolute_deviation(values: list[float], center: float) -> float:
    absolute_deviations = [abs(value - center) for value in values]
    return median(absolute_deviations)
