# === AI GENERATED CODE START (GitHub Copilot - Claude Sonnet 5) ===
# Generated on: 2026-08-26
# Revised on: 2026-08-28 - .log rewritten as a curated insight document (executive read,
#   key-metric snapshot, universe + industry relative positioning, weekly historical
#   progression, condensed move-prediction / edge-research sections, and an
#   entry/upside/downside/risk summary). The full raw field catalog and complete lens
#   rows remain available in the .json sidecar for need-to-check detail.
# Revised on: 2026-08-31 - financial-projection suite integrated (growth lanes + EV/Rev
#   price projections with cross-run tracking) plus street forward guidance from the
#   all-fields row; new log section 5 assesses whether the projected growth actually
#   supports the model's forward value. Full projection rows/grids live in the JSON.
"""Single-symbol intelligence report across all TradingView-based data flows.

Given a symbol (``NASDAQ:ABUS`` preferred, bare ticker also accepted), this module:

1. Identifies the company (full name, exchange, sector/industry) primarily from the latest
   ``trading_view_all_fields_data`` snapshot, optionally enriched from the
   ``trading_view_company_data_map`` MySQL table when reachable.
2. Builds a curated key-metric snapshot (price/tape, valuation, quality/growth,
   technicals, analysts/events) from the latest all-fields snapshot.
3. Computes percentile positioning against BOTH the full scan universe (``type='stock'``)
   and the industry (falling back to sector) peer group on core decision metrics.
4. Reconstructs the weekly historical progression (one scan per ISO week) across the
   all-fields dataset, plus per-week consensus/conviction from the weekly
   move-prediction databases.
5. Condenses the move-prediction suite (conviction, consensus x horizon, profile matrix)
   and every edge-research lens row down to decision-relevant key fields.
6. Integrates the financial-projection suite (``fingrowth_*`` growth lanes and
   ``finproj_*`` EV/Rev price projections): latest-run detail, per-year projected
   fundamentals/price paths, cross-run tracking, street forward guidance from the
   all-fields row (analyst targets, revenue/EPS forecasts), and a growth-support
   assessment of whether the projected growth underwrites the model's forward value.
7. Writes one plain-text ``.log`` (a focused insight document meant to be readable in
   minutes) and one ``.json`` sidecar (full raw field catalog + complete lens rows for
   need-to-check detail) to
   ``logs/tradingview_analysis/symbol_intelligence/<SYMBOL>/<YYYY-MM-DD>/<SYMBOL>__run_<HHMMSS>.{log,json}``,
   so every symbol has its own folder and every scan date its own sub-folder; multiple
   same-day runs simply stack as additional ``run_<HHMMSS>`` files.

Usage:
    python -m src.data_analysis_scripts.trading_view_symbol_intelligence_report --symbol NASDAQ:ABUS

Programmatic:
    from data_analysis_scripts.trading_view_symbol_intelligence_report import (
        build_symbol_intelligence_report,
    )
    result = build_symbol_intelligence_report("NASDAQ:ABUS")
    print(result["log_path"])

Notes:
    - Every data source is optional and independently best-effort: missing databases/CSVs/MySQL
      connectivity add a warning line to the report instead of raising, so the flow always produces
      a log with whatever coverage is actually available.
    - Latest-available fallback: whenever the newest run of a source lacks the symbol
      (all-fields snapshot, move-prediction run, growth/price projection run, individual
      edge-research lens), the report automatically falls back to the most recent run that
      does contain the symbol and flags the fallback in the warnings, the coverage line,
      and the relevant section header.
    - The .log intentionally contains curated/derived insight only. The .json sidecar keeps the
      raw bucketed field catalog (``all_fields_buckets``), unabridged lens rows, and every
      intermediate value used to build the log, for need-to-check verification.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
    DEFAULT_ALL_FIELDS_ROOT,
)
from data_analysis_scripts.trading_view_move_prediction_pattern_discovery import (
    DEFAULT_PREDICTION_ROOT,
    discover_latest_all_fields_db,
    discover_latest_prediction_db,
)
from data_analysis_scripts.trading_view_field_semantic_classifier import (
    classify_semantic_bucket,
)
from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb
from edge_research_tools.run_resolution import discover_latest_edge_parent_run_dir
from generic_utils.log_to_files_util import log_to_file

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "logs" / "tradingview_analysis" / "symbol_intelligence"
)

NON_METRIC_ALL_FIELDS_COLUMNS = frozenset({"run_id", "row_number", "symbol"})

# ---------------------------------------------------------------------------
# Curated field configuration
# ---------------------------------------------------------------------------
# Format codes used across the report:
#   price  -> 134.36 | money -> 13.72B | pct -> -27.6% | ratio -> 13.76
#   score  -> +0.40  | int   -> 747,478 | date -> 2026-11-03 (epoch-safe) | text -> raw

# (column, label, fmt) per group for the KEY METRICS SNAPSHOT log section.
SNAPSHOT_GROUPS: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
    (
        "Price & tape",
        (
            ("close", "Close", "price"),
            ("change", "Change (day)", "pct"),
            ("gap", "Gap (day)", "pct"),
            ("market_cap_basic", "Market cap", "money"),
            ("volume", "Volume", "int"),
            ("relative_volume_10d_calc", "Rel volume (10D)", "ratio"),
            ("ATRP", "ATR % (daily)", "pct_plain"),
            ("beta_1_year", "Beta (1Y)", "ratio"),
        ),
    ),
    (
        "Valuation",
        (
            ("price_earnings_ttm", "P/E (TTM)", "ratio"),
            ("enterprise_value_ebitda_ttm", "EV/EBITDA (TTM)", "ratio"),
            ("price_book_fq", "P/B (FQ)", "ratio"),
            ("price_sales_current", "P/S", "ratio"),
            ("earnings_yield", "Earnings yield", "pct_plain"),
            ("price_earnings_growth_ttm", "PEG (TTM)", "ratio"),
            ("dividends_yield_current", "Dividend yield", "pct_plain"),
            ("graham_numbers_ttm", "Graham number (TTM)", "price"),
        ),
    ),
    (
        "Quality & growth",
        (
            ("gross_margin", "Gross margin", "pct_plain"),
            ("operating_margin", "Operating margin", "pct_plain"),
            ("net_margin", "Net margin", "pct_plain"),
            ("return_on_equity", "ROE", "pct_plain"),
            ("free_cash_flow_margin_ttm", "FCF margin (TTM)", "pct_plain"),
            ("total_revenue_yoy_growth_ttm", "Revenue growth YoY (TTM)", "pct"),
            (
                "earnings_per_share_diluted_yoy_growth_ttm",
                "EPS growth YoY (TTM)",
                "pct",
            ),
            ("ebitda_yoy_growth_ttm", "EBITDA growth YoY (TTM)", "pct"),
            ("net_debt_to_ebitda_fy", "Net debt / EBITDA (FY)", "ratio"),
            ("altman_z_score_ttm", "Altman Z (TTM)", "ratio"),
            ("current_ratio_fq", "Current ratio (FQ)", "ratio"),
        ),
    ),
    (
        "Technicals & trend",
        (
            ("Recommend.All", "Technical rating (-1..+1)", "score"),
            ("Recommend.MA", "MA rating", "score"),
            ("RSI", "RSI (14)", "ratio"),
            ("ADX", "ADX (trend strength)", "ratio"),
            ("SMA20", "SMA20", "price"),
            ("SMA50", "SMA50", "price"),
            ("SMA200", "SMA200", "price"),
            ("price_52_week_high", "52W high", "price"),
            ("price_52_week_low", "52W low", "price"),
            ("Volatility.D", "Volatility (day)", "pct_plain"),
            ("Volatility.M", "Volatility (month)", "pct_plain"),
        ),
    ),
    (
        "Analysts & events",
        (
            ("AnalystRating", "Analyst rating", "text"),
            ("price_target_median", "Price target (median)", "price"),
            ("price_target_1y_delta", "Target delta (1Y)", "pct"),
            ("earnings_release_next_date", "Next earnings", "date"),
            ("earnings_release_date", "Last earnings", "date"),
            ("eps_surprise_percent_fq", "EPS surprise (last Q)", "pct"),
            ("revenue_surprise_percent_fq", "Revenue surprise (last Q)", "pct"),
        ),
    ),
)

# (column, label, direction, fmt, positive_only) for relative positioning.
# direction: "lower_better" | "higher_better" | "context".
# positive_only: percentile computed among positive values only, so loss-makers with
# negative valuation multiples do not distort the "cheap" end of the ranking.
POSITIONING_METRICS: tuple[tuple[str, str, str, str, bool], ...] = (
    ("market_cap_basic", "Market Cap", "context", "money", False),
    ("price_earnings_ttm", "P/E (TTM)", "lower_better", "ratio", True),
    ("enterprise_value_ebitda_ttm", "EV/EBITDA (TTM)", "lower_better", "ratio", True),
    ("price_book_fq", "P/B (FQ)", "lower_better", "ratio", True),
    ("price_sales_current", "P/S", "lower_better", "ratio", True),
    ("dividends_yield_current", "Dividend Yield", "higher_better", "pct_plain", False),
    ("net_debt_to_ebitda_fy", "Net Debt/EBITDA", "lower_better", "ratio", False),
    ("altman_z_score_ttm", "Altman Z", "higher_better", "ratio", False),
    ("Perf.YTD", "Perf YTD", "higher_better", "pct", False),
    ("Perf.1M", "Perf 1M", "higher_better", "pct", False),
    ("Perf.Y", "Perf 1Y", "higher_better", "pct", False),
    ("RSI", "RSI (14)", "context", "ratio", False),
    ("Recommend.All", "Technical Rating", "higher_better", "score", False),
    ("ATRP", "ATR %", "context", "pct_plain", False),
    ("relative_volume_10d_calc", "Rel Volume (10D)", "higher_better", "ratio", False),
    ("beta_1_year", "Beta (1Y)", "context", "ratio", False),
    ("float_shares_percent_current", "Float %", "context", "pct_plain", False),
)

# Weekly historical progression: (group title, (column, label, fmt) tuples).
# Rendered as one sub-table per group so the full key-metric set stays readable;
# every available ISO week in the dataset is covered (bounded by --history-weeks).
HISTORY_COLUMN_GROUPS: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
    (
        "Price & valuation",
        (
            ("close", "Close", "price"),
            ("market_cap_basic", "MktCap", "money"),
            ("price_earnings_ttm", "P/E", "ratio"),
            ("enterprise_value_ebitda_ttm", "EV/EBITDA", "ratio"),
            ("price_book_fq", "P/B", "ratio"),
            ("price_sales_current", "P/S", "ratio"),
            ("price_free_cash_flow_ttm", "P/FCF", "ratio"),
            ("earnings_yield", "EarnYld", "pct_plain"),
            ("Perf.YTD", "YTD", "pct"),
            ("Perf.1M", "1M", "pct"),
        ),
    ),
    (
        "Quality & fundamentals",
        (
            ("gross_margin", "GrossM", "pct_plain"),
            ("operating_margin", "OperM", "pct_plain"),
            ("net_margin", "NetM", "pct_plain"),
            ("return_on_equity", "ROE", "pct_plain"),
            ("free_cash_flow_margin_ttm", "FCFm", "pct_plain"),
            ("total_revenue_yoy_growth_ttm", "RevGr", "pct"),
            ("earnings_per_share_diluted_yoy_growth_ttm", "EPSgr", "pct"),
            ("net_debt_to_ebitda_fy", "NetD/E", "ratio"),
            ("altman_z_score_ttm", "AltZ", "ratio"),
            ("piotroski_f_score_ttm", "PiotF", "ratio"),
        ),
    ),
    (
        "Tape & technicals",
        (
            ("RSI", "RSI", "ratio"),
            ("Recommend.All", "TechR", "score"),
            ("ADX", "ADX", "ratio"),
            ("ATRP", "ATR%", "pct_plain"),
            ("relative_volume_10d_calc", "RelVol", "ratio"),
            ("beta_1_year", "Beta", "ratio"),
            ("Volatility.M", "VolM", "pct_plain"),
            ("Perf.3M", "3M", "pct"),
        ),
    ),
)

# Derived weekly columns computed in the loader: (key, label, fmt).
HISTORY_DERIVED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("dist_sma200_pct", "vsSMA200", "pct"),
    ("dist_52w_high_pct", "vs52wHi", "pct"),
)

# Flat union of all raw history columns (loader queries these).
HISTORY_COLUMNS: tuple[tuple[str, str, str], ...] = tuple(
    column for _group, columns in HISTORY_COLUMN_GROUPS for column in columns
)

# Raw helper columns queried only to compute HISTORY_DERIVED_COLUMNS.
HISTORY_HELPER_COLUMNS: tuple[str, ...] = ("SMA200", "price_52_week_high")

HORIZON_ORDER: tuple[str, ...] = ("days", "weeks", "months", "years")

# (label, relative path under the edge-research parent run dir, short description,
#  priority prefixes) - priority prefixes surface lens-unique fields first in the log.
EDGE_RESEARCH_SOURCES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "unified_edge_highlights",
        "edge_unified_highlights/edge_unified_highlights.csv",
        "Unified edge rank/score plus historical validation.",
        ("unified_", "historical_validation_", "forward_valuation_", "outlook_"),
    ),
    (
        "safety_scored_universe",
        "edge_unified_highlights/edge_unified_highlights_safety_scored_universe.csv",
        "Full safety/quality/value companion score detail.",
        ("safety_", "balance_sheet_", "cash_generation_"),
    ),
    (
        "big_mover_confidence_shortlist",
        "highlights/edge_name_shortlist.csv",
        "Big-mover and confidence shortlist ranks.",
        ("big_mover_", "confidence_", "shortlist_", "lane_", "win_rate", "median_fwd"),
    ),
    (
        "upside_prediction",
        "upside_prediction_lens/edge_upside_prediction_ranked.csv",
        "Upside prediction lens rank/score.",
        ("upside_",),
    ),
    (
        "forward_upside_valuation",
        "forward_upside_valuation_lens/edge_forward_upside_valuation_ranked.csv",
        "Forward valuation upside overlay.",
        ("forward_",),
    ),
    (
        "tradeable_safety",
        "tradeable_safety_lens/edge_tradeable_safety_overlay.csv",
        "Tradeable-safety blended overlay.",
        ("tradeable_", "risk_rank", "safety_rank"),
    ),
    (
        "safety_highlights",
        "safety_highlights/edge_safety_scored.csv",
        "Safety highlights scoring detail.",
        ("safety_",),
    ),
    (
        "earnings_priority",
        "earnings_priority_lens/edge_earnings_priority_candidates.csv",
        "Earnings-priority coverage lens.",
        ("earnings_",),
    ),
    (
        "edge_trade_plan",
        "edge_trade_plan/edge_trade_plan_ranked.csv",
        "Edge trade plan ranked candidates.",
        ("trade_plan_", "entry_", "extension_", "check_", "historical_edge_current"),
    ),
    (
        "screen",
        "screen/edge_screen_ranked.csv",
        "Point-in-time screen ranked universe.",
        ("screen_", "composite_", "hist_"),
    ),
    (
        "blindspot_candidates",
        "blindspot_lane/edge_blindspot_symbol_candidates.csv",
        "Blindspot lane symbol candidates.",
        ("quiet_mover_", "blindspot_", "trend_"),
    ),
)

# Edge lens rows carry hundreds of columns; the log shows only decision-relevant ones
# (ranks/scores/buckets/flags/validation stats). The full row stays in the JSON.
EDGE_KEY_COLUMN_RE = re.compile(
    r"rank|score|rating|bucket|tier|flag|signal|state|setup|mode|style|readiness"
    r"|upside_pct|win_rate|median_fwd|target_rate|percentile|outlook|action",
    re.IGNORECASE,
)
EDGE_EXCLUDE_PREFIXES: tuple[str, ...] = (
    "safety_detail_",
    "lane_leader_",
    "edge_group_",
    "historical_edge_config",
    "historical_edge_database",
)
EDGE_MAX_DISPLAY_FIELDS = 16

# Default: effectively the entire weekly dataset (the all-fields archive currently
# spans ~22 ISO weeks); --history-weeks can narrow it.
DEFAULT_HISTORY_WEEKS = 52

# ---------------------------------------------------------------------------
# Financial-projection integration (growth lanes + EV/Rev price suite)
# ---------------------------------------------------------------------------
DEFAULT_PROJECTION_ROOT = (
    PROJECT_ROOT / "logs" / "tradingview_analysis" / "financial_projection"
)
_PROJECTION_RUN_ID_RE = re.compile(
    r"^(?:fingrowth|finproj)_(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$"
)
GROWTH_LANE_ORDER: tuple[str, ...] = ("own", "peer", "universe")
SCENARIO_ORDER: tuple[str, ...] = ("bear", "base", "bull")

# Curated per-run fields for cross-run projection tracking (progression); the latest
# run's full rows/grids go to the JSON unabridged.
GROWTH_PROGRESSION_FIELDS: tuple[str, ...] = (
    "y1_revenue_growth_pct",
    "revenue_cagr_implied_pct",
    "ebitda_cagr_implied_pct",
    "net_income_cagr_implied_pct",
    "terminal_total_revenue_ttm",
    "terminal_ebitda_margin_ttm",
    "terminal_net_margin_ttm",
    "growth_lane_source",
    "forecast_rejected",
    "valid",
    "invalid_reason",
)
PRICE_PROGRESSION_FIELDS: tuple[str, ...] = (
    "primary_upside_pct",
    "primary_upside_source",
    "terminal_price",
    "terminal_upside_pct",
    "implied_price_cagr_pct",
    "model_y1_price",
    "model_y1_upside_pct",
    "lane_core_upside_pct",
    "lane_street_upside_pct",
    "lane_hist_adjusted_upside_pct",
    "lane_peer_trust",
    "lane_hist_trust",
    "valuation_lens",
    "st_outlook",
    "decision_flags",
    "scenario_width_y5_pp",
    "rank_eligible",
    "valid",
    "invalid_reason",
)

# (column, label, fmt) for the street forward-guidance block (source: all-fields row).
FORWARD_GUIDANCE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("AnalystRating", "Analyst rating", "text"),
    ("price_target_low", "Price target low", "price"),
    ("price_target_median", "Price target median", "price"),
    ("price_target_average", "Price target average", "price"),
    ("price_target_high", "Price target high", "price"),
    ("price_target_1y", "Price target 1Y", "price"),
    ("total_revenue_ttm", "Revenue TTM", "money"),
    ("total_revenue_fq", "Revenue (last FQ)", "money"),
    ("revenue_forecast_next_fq", "Next-FQ revenue forecast", "money"),
    ("revenue_forecast_next_fy", "Next-FY revenue forecast", "money"),
    ("earnings_per_share_diluted_ttm", "EPS diluted TTM", "ratio"),
    ("earnings_per_share_forecast_next_fq", "Next-FQ EPS forecast", "ratio"),
    ("earnings_per_share_forecast_next_fy", "Next-FY EPS forecast", "ratio"),
    ("price_earnings_forward_fy", "Forward P/E (FY)", "ratio"),
    (
        "non_gaap_price_to_earnings_per_share_forecast_next_fy",
        "Forward P/E (non-GAAP next FY)",
        "ratio",
    ),
    ("earnings_release_next_date", "Next earnings", "date"),
    ("sustainable_growth_rate_ttm", "Sustainable growth rate (TTM)", "pct_plain"),
)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ordinal(number: float | int) -> str:
    value = int(round(number))
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def _fmt_money(value: float) -> str:
    abs_value = abs(value)
    if abs_value >= 1e12:
        return f"{value / 1e12:.2f}T"
    if abs_value >= 1e9:
        return f"{value / 1e9:.2f}B"
    if abs_value >= 1e6:
        return f"{value / 1e6:.1f}M"
    if abs_value >= 1e3:
        return f"{value / 1e3:.1f}K"
    return f"{value:.2f}"


def _fmt_value(value: Any, fmt: str) -> str:
    if value in (None, ""):
        return "n/a"
    if fmt == "text":
        return str(value)
    if fmt == "date":
        timestamp = _to_float(value)
        if timestamp is None:
            return str(value)
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return str(value)
    number = _to_float(value)
    if number is None:
        return str(value)
    if fmt == "money":
        return _fmt_money(number)
    if fmt == "pct":
        return f"{number:+.1f}%"
    if fmt == "pct_plain":
        return f"{number:.1f}%"
    if fmt == "score":
        return f"{number:+.2f}"
    if fmt == "int":
        return f"{number:,.0f}"
    return f"{number:,.2f}"


def _percentile_bar(percentile: float | None, width: int = 10) -> str:
    if percentile is None:
        return " " * width
    clamped = min(max(percentile, 0.0), 100.0)
    filled = int(round(width * clamped / 100.0))
    return "#" * filled + "-" * (width - filled)


def _pct_opt(value: Any) -> str:
    """Signed percent for already-percent values (projection suite convention)."""
    number = _to_float(value)
    return f"{number:+.1f}%" if number is not None else "n/a"


def _frac_pct_opt(value: Any) -> str:
    """Unsigned percent for fraction values (0.128 -> 12.8%)."""
    number = _to_float(value)
    return f"{number * 100:.1f}%" if number is not None else "n/a"


def _money_opt(value: Any) -> str:
    number = _to_float(value)
    return _fmt_money(number) if number is not None else "n/a"


def _price_opt(value: Any) -> str:
    number = _to_float(value)
    return f"{number:,.2f}" if number is not None else "n/a"


def _positioning_read(direction: str, percentile: float) -> str:
    """Short qualitative tag for a percentile given the metric's good direction."""
    if direction == "lower_better":
        if percentile <= 10:
            return "deep value (cheapest decile)"
        if percentile <= 25:
            return "cheap vs peers"
        if percentile <= 45:
            return "below median"
        if percentile < 55:
            return "in line"
        if percentile < 75:
            return "above median"
        if percentile < 90:
            return "expensive vs peers"
        return "very expensive (top decile)"
    if direction == "higher_better":
        if percentile >= 90:
            return "top decile"
        if percentile >= 75:
            return "strong"
        if percentile >= 55:
            return "above median"
        if percentile > 45:
            return "in line"
        if percentile > 25:
            return "below median"
        if percentile > 10:
            return "weak"
        return "bottom decile"
    return ""


def _rsi_zone(rsi: float | None) -> str:
    if rsi is None:
        return ""
    if rsi >= 70:
        return "overbought"
    if rsi >= 60:
        return "firm"
    if rsi >= 40:
        return "neutral"
    if rsi >= 30:
        return "weak"
    return "oversold"


def _altman_zone(z: float | None) -> str:
    if z is None:
        return ""
    if z >= 2.99:
        return "safe zone"
    if z >= 1.81:
        return "grey zone"
    return "distress zone"


def _leverage_read(net_debt_ebitda: float | None) -> str:
    if net_debt_ebitda is None:
        return ""
    if net_debt_ebitda < 0:
        return "net cash"
    if net_debt_ebitda < 2:
        return "low leverage"
    if net_debt_ebitda < 4:
        return "moderate leverage"
    return "high leverage"


def _direction_marker(direction: Any) -> str:
    text = str(direction or "").strip().lower()
    if text == "up":
        return "U"
    if text == "down":
        return "D"
    return "-"


def _horizon_sort_key(name: Any) -> tuple[int, str]:
    label = str(name or "")
    return (
        HORIZON_ORDER.index(label) if label in HORIZON_ORDER else len(HORIZON_ORDER),
        label,
    )


def _wrap_text(text: str, indent: str = "    ", width: int = 108) -> list[str]:
    return textwrap.wrap(
        text, width=width, initial_indent="", subsequent_indent=indent
    ) or [""]


# ---------------------------------------------------------------------------
# Symbol / file helpers
# ---------------------------------------------------------------------------
def _symbol_candidates(symbol: str) -> tuple[str, str]:
    normalized = str(symbol or "").strip().upper()
    bare = normalized.split(":", 1)[1] if ":" in normalized else normalized
    return normalized, bare


def _symbol_file_token(symbol: str) -> str:
    token = "".join(
        ch if ch.isalnum() else "_" for ch in str(symbol or "").strip().upper()
    )
    return token.strip("_") or "SYMBOL"


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _find_symbol_row(rows: list[dict[str, str]], symbol: str) -> dict[str, str] | None:
    normalized, bare = _symbol_candidates(symbol)
    exact = [
        row for row in rows if str(row.get("symbol", "")).strip().upper() == normalized
    ]
    if exact:
        return exact[0]
    bare_matches = [
        row
        for row in rows
        if str(row.get("symbol", "")).strip().upper().split(":")[-1] == bare
    ]
    return bare_matches[0] if bare_matches else None


def _select_populous_latest_run_id(
    all_fields_db: Path, *, min_rows: int = 1000
) -> str | None:
    """Pick the most recent run_id with a reasonably complete universe.

    Guards against a stray tiny/broken intraday run (for example a failed focused
    export that only returned a handful of rows) silently becoming "the latest
    run" used for symbol lookups and peer/industry comparisons.
    """
    sql = """
        SELECT r.run_id, COUNT(*) AS row_count, MAX(m.created_at_utc) AS created_at_utc
        FROM all_fields_rows AS r
        LEFT JOIN run_metadata AS m USING (run_id)
        GROUP BY r.run_id
        ORDER BY created_at_utc DESC NULLS LAST
    """
    rows = query_move_prediction_duckdb(all_fields_db, sql)
    if not rows:
        return None
    for row in rows:
        if int(row.get("row_count") or 0) >= min_rows:
            return str(row.get("run_id"))
    # Every run this day is small (e.g. a fresh database); fall back to the most
    # recent one rather than reporting no data at all.
    return str(rows[0]["run_id"])


def _query_all_fields_symbol_row(
    all_fields_db: Path,
    *,
    normalized: str,
    bare: str,
    run_id: str | None,
) -> dict[str, Any] | None:
    if run_id is not None:
        sql = """
            SELECT r.*
            FROM all_fields_rows AS r
            WHERE r.run_id = ?
              AND (UPPER(r.symbol) = ? OR UPPER(r.symbol) LIKE '%:' || ?)
        """
        params: list[Any] = [run_id, normalized, bare]
    else:
        # No good run scoped: search every run for this symbol (newest first) so a
        # symbol missing only from the current run still resolves to its most
        # recent available data instead of "not found".
        sql = """
            SELECT r.*
            FROM all_fields_rows AS r
            LEFT JOIN run_metadata AS m USING (run_id)
            WHERE UPPER(r.symbol) = ? OR UPPER(r.symbol) LIKE '%:' || ?
            ORDER BY m.created_at_utc DESC NULLS LAST, r.row_number DESC
            LIMIT 200
        """
        params = [normalized, bare]
    rows = query_move_prediction_duckdb(all_fields_db, sql, params)
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    exact = [
        row for row in rows if str(row.get("symbol", "")).strip().upper() == normalized
    ]
    if exact:
        return exact[0]

    def _market_cap(row: dict[str, Any]) -> float:
        try:
            return float(row.get("market_cap_basic") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    return max(rows, key=_market_cap)


def _load_all_fields_snapshot(
    all_fields_db: Path, symbol: str, *, preferred_run_id: str | None = None
) -> dict[str, Any] | None:
    normalized, bare = _symbol_candidates(symbol)
    if preferred_run_id is not None:
        row = _query_all_fields_symbol_row(
            all_fields_db, normalized=normalized, bare=bare, run_id=preferred_run_id
        )
        if row is not None:
            return row
        # Symbol missing from the resolved "good" run only -- fall back to the
        # most recent run of any size that actually has it.
    return _query_all_fields_symbol_row(
        all_fields_db, normalized=normalized, bare=bare, run_id=None
    )


def _table_columns(database_path: Path, table_name: str) -> set[str]:
    try:
        rows = query_move_prediction_duckdb(database_path, f"DESCRIBE {table_name}")
    except Exception:
        return set()
    return {str(row.get("column_name")) for row in rows if row.get("column_name")}


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------
def _extract_snapshot(all_fields_row: dict[str, Any]) -> dict[str, Any]:
    """Curated key metrics as {column: {label, group, value, display}}."""
    snapshot: dict[str, Any] = {}
    for group_name, fields in SNAPSHOT_GROUPS:
        for column, label, fmt in fields:
            value = all_fields_row.get(column)
            if value in (None, ""):
                continue
            snapshot[column] = {
                "label": label,
                "group": group_name,
                "fmt": fmt,
                "value": value,
                "display": _fmt_value(value, fmt),
            }
    return snapshot


def _load_relative_positioning(
    all_fields_db: Path, target_row: dict[str, Any], *, run_id: str | None = None
) -> dict[str, Any] | None:
    """Percentile positioning vs industry peers AND vs the full stock universe."""
    industry = str(target_row.get("industry") or "").strip()
    sector = str(target_row.get("sector") or "").strip()

    available = _table_columns(all_fields_db, "all_fields_rows")
    metrics = [m for m in POSITIONING_METRICS if m[0] in available]
    if not metrics:
        return None

    resolved_run_id = run_id or _select_populous_latest_run_id(all_fields_db)
    if resolved_run_id is None:
        return None

    select_parts = ", ".join(
        f'TRY_CAST(r."{column}" AS DOUBLE) AS "{column}"' for column, *_ in metrics
    )

    def _query_scope(where_clause: str, params: list[Any]) -> list[dict[str, Any]]:
        sql = f"""
            SELECT r.symbol, {select_parts}
            FROM all_fields_rows AS r
            WHERE r.run_id = ? AND {where_clause}
        """
        return query_move_prediction_duckdb(
            all_fields_db, sql, [resolved_run_id, *params]
        )

    scopes: dict[str, dict[str, Any]] = {}
    if industry or sector:
        peer_rows: list[dict[str, Any]] = []
        scope_label = ""
        if industry:
            peer_rows = _query_scope('r."industry" = ?', [industry])
            scope_label = f"industry '{industry}'"
        if len(peer_rows) < 5 and sector:
            peer_rows = _query_scope('r."sector" = ?', [sector])
            scope_label = f"sector '{sector}' (industry peer count was too small)"
        if len(peer_rows) >= 2:
            scopes["industry"] = {"scope": scope_label, "rows": peer_rows}

    if "type" in available:
        universe_rows = _query_scope("r.\"type\" = 'stock'", [])
        universe_label = "all stocks in scan"
    else:
        universe_rows = _query_scope("TRUE", [])
        universe_label = "full scan universe"
    if len(universe_rows) >= 2:
        scopes["universe"] = {"scope": universe_label, "rows": universe_rows}

    target_symbol_normalized = str(target_row.get("symbol") or "").strip().upper()
    positioning: dict[str, Any] = {}
    for scope_name, scope_info in scopes.items():
        rows = scope_info["rows"]
        metric_results: dict[str, Any] = {}
        for column, label, direction, fmt, positive_only in metrics:
            values = [
                (str(row.get("symbol") or "").strip().upper(), row.get(column))
                for row in rows
                if row.get(column) is not None
                and (not positive_only or float(row.get(column)) > 0)
            ]
            if len(values) < 2:
                continue
            target_value = next(
                (value for sym, value in values if sym == target_symbol_normalized),
                None,
            )
            if target_value is None:
                continue
            sorted_values = sorted(value for _, value in values)
            rank_below = sum(1 for value in sorted_values if value < target_value)
            percentile = (
                rank_below / (len(sorted_values) - 1) if len(sorted_values) > 1 else 0.5
            ) * 100
            metric_results[label] = {
                "column": column,
                "value": round(float(target_value), 4),
                "display": _fmt_value(target_value, fmt),
                "percentile": round(percentile, 1),
                "peer_count": len(sorted_values),
                "direction": direction,
                "read": _positioning_read(direction, percentile),
            }
        if metric_results:
            positioning[scope_name] = {
                "scope": scope_info["scope"],
                "peer_count": len(rows),
                "metrics": metric_results,
            }
    return positioning or None


_ALL_FIELDS_FILE_RE = re.compile(r"tradingview_all_fields_(\d{2})_(\d{2})_(\d{4})")


def _all_fields_file_date(db_file: Path) -> datetime | None:
    match = _ALL_FIELDS_FILE_RE.search(db_file.name)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    return datetime(year, month, day, tzinfo=timezone.utc)


def _load_weekly_history(
    all_fields_root: Path,
    symbol: str,
    *,
    weeks: int = DEFAULT_HISTORY_WEEKS,
) -> dict[str, Any] | None:
    """One all-fields scan per ISO week, going back ``weeks`` weeks.

    Files are binned into ISO weeks by their filename date (cheap); only the latest
    file per week is opened, so at most ``weeks`` databases are touched.
    """
    db_files = sorted(
        Path(all_fields_root).rglob("tradingview_all_fields_*.duckdb"),
        key=lambda path: path.stat().st_mtime,
    )
    if not db_files:
        return None

    by_week: dict[tuple[int, int], dict[str, Any]] = {}
    for db_file in db_files:
        file_date = _all_fields_file_date(db_file)
        if file_date is None:
            file_date = datetime.fromtimestamp(
                db_file.stat().st_mtime, tz=timezone.utc
            )
        iso = file_date.isocalendar()
        key = (iso[0], iso[1])
        if key not in by_week or db_file.stat().st_mtime > by_week[key]["mtime"]:
            by_week[key] = {"db": db_file, "date": file_date, "mtime": db_file.stat().st_mtime}

    selected = sorted(by_week.values(), key=lambda entry: entry["date"])[-weeks:]
    normalized, bare = _symbol_candidates(symbol)

    history_rows: list[dict[str, Any]] = []
    weeks_with_symbol = 0
    for entry in selected:
        iso = entry["date"].isocalendar()
        week_label = f"{iso[0]}-W{iso[1]:02d}"
        record: dict[str, Any] = {
            "week": week_label,
            "scan_date": entry["date"].strftime("%Y-%m-%d"),
            "database": entry["db"].name,
            "found": False,
            "metrics": {},
        }
        try:
            run_id = _select_populous_latest_run_id(entry["db"])
            available = _table_columns(entry["db"], "all_fields_rows")
            columns = [c for c, _, _ in HISTORY_COLUMNS if c in available]
            helper_columns = [c for c in HISTORY_HELPER_COLUMNS if c in available]
            if run_id is not None and columns:
                select_parts = ", ".join(
                    f'TRY_CAST(r."{column}" AS DOUBLE) AS "{column}"'
                    for column in columns + helper_columns
                )
                sql = f"""
                    SELECT {select_parts}
                    FROM all_fields_rows AS r
                    WHERE r.run_id = ?
                      AND (UPPER(r.symbol) = ? OR UPPER(r.symbol) LIKE '%:' || ?)
                    LIMIT 1
                """
                rows = query_move_prediction_duckdb(
                    entry["db"], sql, [run_id, normalized, bare]
                )
                if rows:
                    metrics = rows[0]
                    close = _to_float(metrics.get("close"))
                    sma200 = _to_float(metrics.pop("SMA200", None))
                    high_52w = _to_float(
                        metrics.pop("price_52_week_high", None)
                    )
                    if close and sma200:
                        metrics["dist_sma200_pct"] = (close / sma200 - 1) * 100
                    if close and high_52w:
                        metrics["dist_52w_high_pct"] = (close / high_52w - 1) * 100
                    record["found"] = True
                    record["metrics"] = metrics
                    weeks_with_symbol += 1
        except Exception:
            # A single unreadable historical file must not kill the whole report.
            pass
        history_rows.append(record)

    return {
        "weeks_requested": weeks,
        "weeks_available": len(selected),
        "weeks_with_symbol": weeks_with_symbol,
        "rows": history_rows,
    }


def _load_move_prediction_context(
    prediction_root: Path,
    symbol: str,
    *,
    latest_db: Path | None = None,
) -> dict[str, Any] | None:
    """Current move-prediction suite for ``symbol`` with latest-available fallback.

    Walks runs newest-first inside each weekly database, then older weekly databases
    newest-first, and returns the first run that actually contains the symbol. The
    provenance fields (``source_database``/``source_run_id``/``is_latest_*``) let the
    log flag when a fallback was needed.
    """
    normalized, bare = _symbol_candidates(symbol)
    predicate = "UPPER(t.symbol) = ? OR UPPER(t.symbol) LIKE '%:' || ?"
    db_files = sorted(
        Path(prediction_root).rglob("move_prediction_*.duckdb"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not db_files:
        return None
    resolved_latest_db = latest_db or db_files[0]

    for db_file in db_files:
        try:
            run_rows = query_move_prediction_duckdb(
                db_file,
                """
                SELECT run_id, created_at_utc FROM run_metadata
                ORDER BY created_at_utc DESC NULLS LAST
                """,
            )
        except Exception:
            continue
        for run_row in run_rows:
            run_id = run_row.get("run_id")

            def _query(table_name: str, order_by: str) -> list[dict[str, Any]]:
                sql = f"""
                    SELECT t.*
                    FROM {table_name} AS t
                    WHERE t.run_id = ? AND ({predicate})
                    {order_by}
                """
                try:
                    return query_move_prediction_duckdb(
                        db_file, sql, [run_id, normalized, bare]
                    )
                except Exception:
                    return []

            profile_horizon_rows = _query(
                "profile_horizon_scores", "ORDER BY profile_name, horizon_name"
            )
            consensus_horizon_rows = _query(
                "consensus_horizon_scores", "ORDER BY horizon_name"
            )
            conviction_rows = _query("conviction_rankings", "ORDER BY sleeve")
            if not (profile_horizon_rows or consensus_horizon_rows or conviction_rows):
                continue
            is_latest_db = db_file == resolved_latest_db
            return {
                "profile_horizon_scores": profile_horizon_rows,
                "consensus_horizon_scores": consensus_horizon_rows,
                "conviction_rankings": conviction_rows,
                "source_database": db_file.as_posix(),
                "source_run_id": run_id,
                "source_run_created_at_utc": str(run_row.get("created_at_utc") or ""),
                "is_latest_database": is_latest_db,
                "is_latest_run": bool(
                    run_rows and run_id == run_rows[0].get("run_id")
                ),
            }
    return None


def _load_prediction_weekly_history(
    prediction_root: Path, symbol: str
) -> list[dict[str, Any]]:
    """Consensus + conviction for the symbol from each weekly move-prediction db."""
    db_files = sorted(
        Path(prediction_root).rglob("move_prediction_*.duckdb"),
        key=lambda path: path.stat().st_mtime,
    )
    normalized, bare = _symbol_candidates(symbol)
    predicate = "UPPER(symbol) = ? OR UPPER(symbol) LIKE '%:' || ?"
    history: list[dict[str, Any]] = []
    for db_file in db_files:
        try:
            meta = query_move_prediction_duckdb(
                db_file,
                """
                SELECT run_id, created_at_utc FROM run_metadata
                ORDER BY created_at_utc DESC NULLS LAST LIMIT 1
                """,
            )
            if not meta:
                continue
            run_id = meta[0]["run_id"]
            consensus = query_move_prediction_duckdb(
                db_file,
                f"""
                SELECT horizon_name, score, direction, confidence, agreement_ratio,
                       risk_tier, manager_action_signal
                FROM consensus_horizon_scores
                WHERE run_id = ? AND ({predicate})
                ORDER BY horizon_name
                """,
                [run_id, normalized, bare],
            )
            conviction = query_move_prediction_duckdb(
                db_file,
                f"""
                SELECT sleeve, conviction_score, rank_overall, rank_in_sleeve,
                       entry_readiness, manager_action_signal
                FROM conviction_rankings
                WHERE run_id = ? AND ({predicate})
                ORDER BY sleeve
                """,
                [run_id, normalized, bare],
            )
        except Exception:
            continue
        if not consensus and not conviction:
            continue
        history.append(
            {
                "week": db_file.stem.replace("move_prediction_", ""),
                "database": db_file.name,
                "run_created_at_utc": str(meta[0].get("created_at_utc") or ""),
                "consensus": consensus,
                "conviction": conviction,
            }
        )
    return history


def _discover_edge_parent_run_dirs(
    latest_parent_dir: Path, *, limit: int = 6
) -> list[Path]:
    """Newest-first parent run dirs (siblings of the latest one, itself included)."""
    root = latest_parent_dir.parent
    try:
        candidates = [
            path
            for path in root.iterdir()
            if path.is_dir() and (path / "parent_run_manifest.json").exists()
        ]
    except OSError:
        return [latest_parent_dir]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[:limit]


def _load_edge_research_context(
    parent_run_dir: Path,
    symbol: str,
    *,
    fallback_parent_dirs: Sequence[Path] = (),
) -> dict[str, Any]:
    """Edge-research lens rows for ``symbol`` with per-lens latest-available fallback.

    When the symbol is absent from a lens CSV in the latest parent run, older parent
    runs (``fallback_parent_dirs``, newest-first) are searched for the same lens and
    the first hit is used, flagged via ``source_is_latest``.
    """
    context: dict[str, Any] = {}
    for label, relative_path, notes, priority_prefixes in EDGE_RESEARCH_SOURCES:
        csv_path = parent_run_dir / relative_path
        if not csv_path.exists():
            # Newer parent runs nest lens outputs under aggregate/.
            aggregate_path = parent_run_dir / "aggregate" / relative_path
            if aggregate_path.exists():
                csv_path = aggregate_path
            else:
                continue
        try:
            rows = _read_csv_rows(csv_path)
        except Exception as exc:
            context[label] = {"path": csv_path.as_posix(), "error": str(exc)}
            continue
        row = _find_symbol_row(rows, symbol)
        source_dir = parent_run_dir
        source_is_latest = True
        if row is None:
            for alt_dir in fallback_parent_dirs:
                alt_csv = alt_dir / relative_path
                if not alt_csv.exists():
                    alt_csv = alt_dir / "aggregate" / relative_path
                if not alt_csv.exists():
                    continue
                try:
                    alt_rows = _read_csv_rows(alt_csv)
                except Exception:
                    continue
                alt_row = _find_symbol_row(alt_rows, symbol)
                if alt_row is None:
                    continue
                row = alt_row
                rows = alt_rows
                csv_path = alt_csv
                source_dir = alt_dir
                source_is_latest = False
                break
        context[label] = {
            "path": csv_path.as_posix(),
            "notes": notes,
            "population": len(rows),
            "priority_prefixes": priority_prefixes,
            "row": row,
            "source_run_dir": source_dir.as_posix(),
            "source_is_latest": source_is_latest,
        }
    return context


def _edge_key_fields(
    row: dict[str, str], priority_prefixes: Sequence[str] = ()
) -> list[tuple[str, str]]:
    """Decision-relevant (rank/score/bucket/validation) fields from a lens row.

    Lens-unique fields (matching ``priority_prefixes``) sort first, then ranks, then
    scores, then anything else; the shared cross-lens columns sink to the bottom.
    """
    items = [
        (key, value)
        for key, value in row.items()
        if key != "symbol"
        and value not in (None, "")
        and not key.startswith(EDGE_EXCLUDE_PREFIXES)
        and EDGE_KEY_COLUMN_RE.search(key)
    ]

    def _sort_key(column: str) -> tuple[int, int]:
        lowered = column.lower()
        priority = 0 if column.startswith(tuple(priority_prefixes)) else 1
        if "rank" in lowered:
            group = 0
        elif "score" in lowered:
            group = 1
        else:
            group = 2
        return (priority, group)

    items.sort(key=lambda item: _sort_key(item[0]))
    return items[:EDGE_MAX_DISPLAY_FIELDS]


def _resolve_company_identity(
    symbol: str, all_fields_row: dict[str, Any] | None
) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "requested_symbol": symbol,
        "matched_symbol": "",
        "full_name": "",
        "exchange": "",
        "sector": "",
        "industry": "",
        "currency": "",
        "market": "",
        "identity_sources": [],
    }

    if all_fields_row:
        identity["matched_symbol"] = str(all_fields_row.get("symbol") or "").strip()
        identity["full_name"] = str(
            all_fields_row.get("description") or all_fields_row.get("name") or ""
        ).strip()
        identity["exchange"] = str(all_fields_row.get("exchange") or "").strip()
        identity["sector"] = str(all_fields_row.get("sector") or "").strip()
        identity["industry"] = str(all_fields_row.get("industry") or "").strip()
        identity["currency"] = str(all_fields_row.get("currency") or "").strip()
        identity["market"] = str(all_fields_row.get("market") or "").strip()
        identity["identity_sources"].append("trading_view_all_fields_rows")

    try:
        from db.trading_view_company_data_map_operations import (
            get_trading_view_company_by_symbol,
        )

        company_row = get_trading_view_company_by_symbol(symbol)
    except Exception as exc:
        company_row = None
        identity["company_data_map_error"] = str(exc)

    if company_row:
        identity["identity_sources"].append("trading_view_company_data_map (mysql)")
        if not identity["matched_symbol"]:
            identity["matched_symbol"] = str(company_row.get("symbol") or "").strip()
        if not identity["full_name"]:
            identity["full_name"] = str(
                company_row.get("name") or company_row.get("description") or ""
            ).strip()
        identity["exchange"] = (
            identity["exchange"] or str(company_row.get("exchange") or "").strip()
        )
        identity["market_cap_basic_mysql"] = company_row.get("market_cap_basic")
        identity["perf_snapshot_mysql"] = {
            "perf_w": company_row.get("perf_w"),
            "perf_1m": company_row.get("perf_1m"),
            "perf_ytd": company_row.get("perf_ytd"),
            "perf_y": company_row.get("perf_y"),
            "perf_6m": company_row.get("perf_6m"),
            "perf_5y": company_row.get("perf_5y"),
        }

    if not identity["full_name"]:
        identity["full_name"] = (
            "UNKNOWN - not found in any available TradingView data source"
        )
    if not identity["matched_symbol"]:
        identity["matched_symbol"] = str(symbol).strip().upper()

    return identity


def _bucket_all_fields_row(
    all_fields_row: dict[str, Any],
) -> dict[str, list[tuple[str, Any]]]:
    """Raw field catalog for the JSON sidecar (need-to-check basis only)."""
    buckets: dict[str, list[tuple[str, Any]]] = {}
    for field_name, value in all_fields_row.items():
        if field_name in NON_METRIC_ALL_FIELDS_COLUMNS:
            continue
        if value in (None, ""):
            continue
        try:
            bucket, _tags = classify_semantic_bucket(field_name, "")
        except Exception:
            bucket = "other"
        buckets.setdefault(bucket, []).append((field_name, value))
    for fields in buckets.values():
        fields.sort(key=lambda item: item[0])
    return buckets


# ---------------------------------------------------------------------------
# Financial-projection loaders (growth lanes + EV/Rev price suite) and guidance
# ---------------------------------------------------------------------------
def _discover_projection_runs(projection_root: Path, prefix: str) -> list[dict[str, Any]]:
    """All run DuckDBs for one projection suite (``fingrowth``/``finproj``), oldest first."""
    runs: list[dict[str, Any]] = []
    if not Path(projection_root).exists():
        return runs
    for db_file in sorted(
        Path(projection_root).rglob(f"{prefix}_*.duckdb"),
        key=lambda path: path.stat().st_mtime,
    ):
        match = _PROJECTION_RUN_ID_RE.match(db_file.stem)
        day_label = (
            f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else ""
        )
        runs.append(
            {"run_id": db_file.stem, "database": db_file, "day_label": day_label}
        )
    return runs


def _projection_symbol_rows(
    db_file: Path,
    table: str,
    normalized: str,
    bare: str,
    *,
    extra_where: str = "",
    order_by: str = "",
) -> list[dict[str, Any]]:
    sql = f"""
        SELECT t.*
        FROM {table} AS t
        WHERE (UPPER(t.symbol) = ? OR UPPER(t.symbol) LIKE '%:' || ?)
        {extra_where}
        {order_by}
    """
    return query_move_prediction_duckdb(db_file, sql, [normalized, bare])


def _load_growth_projection_context(
    projection_root: Path, symbol: str
) -> dict[str, Any] | None:
    """Growth lanes for ``symbol``: newest run containing it in full + tracking per run.

    The detail block falls back to the latest AVAILABLE run: if the newest run lacks
    the symbol, the most recent older run that contains it is used instead (flagged
    via ``detail_run.is_latest``).
    """
    runs = _discover_projection_runs(projection_root, "fingrowth")
    if not runs:
        return None
    normalized, bare = _symbol_candidates(symbol)

    progression: list[dict[str, Any]] = []
    for run in runs:
        record: dict[str, Any] = {
            "run_id": run["run_id"],
            "day_label": run["day_label"],
            "database": run["database"].as_posix(),
            "found": False,
            "lanes": {},
        }
        progression.append(record)
        try:
            rows = _projection_symbol_rows(
                run["database"],
                "growth_summary",
                normalized,
                bare,
                extra_where="AND t.scenario = 'base'",
            )
            for row in rows:
                lane = str(row.get("growth_lane") or "")
                record["lanes"][lane] = {
                    field: row.get(field) for field in GROWTH_PROGRESSION_FIELDS
                }
            record["found"] = bool(rows)
        except Exception:
            # One unreadable historical run must not kill the tracking table.
            continue

    # Detail block: newest run that actually contains the symbol.
    detail_entry = next(
        (entry for entry in reversed(progression) if entry["found"]), None
    )
    summary_rows: list[dict[str, Any]] = []
    year_grid_rows: list[dict[str, Any]] = []
    universe_priors: dict[str, Any] | None = None
    if detail_entry is not None:
        detail_db = Path(detail_entry["database"])
        try:
            summary_rows = _projection_symbol_rows(
                detail_db,
                "growth_summary",
                normalized,
                bare,
                order_by="ORDER BY t.growth_lane, t.scenario",
            )
            year_grid_rows = _projection_symbol_rows(
                detail_db,
                "growth_year_grid",
                normalized,
                bare,
                order_by="ORDER BY t.growth_lane, t.scenario, t.year",
            )
            metadata_file = detail_db.parent / "run_metadata.json"
            if metadata_file.exists():
                universe_priors = (
                    json.loads(metadata_file.read_text(encoding="utf-8")) or {}
                ).get("universe_growth_priors")
        except Exception:
            pass

    latest = runs[-1]
    return {
        "suite": "growth",
        "runs_available": len(runs),
        "latest_run": {
            "run_id": latest["run_id"],
            "day_label": latest["day_label"],
            "database": latest["database"].as_posix(),
            "symbol_present": bool(progression[-1]["found"]),
        },
        "detail_run": (
            {
                "run_id": detail_entry["run_id"],
                "day_label": detail_entry["day_label"],
                "database": detail_entry["database"],
                "is_latest": detail_entry["run_id"] == latest["run_id"],
            }
            if detail_entry is not None
            else None
        ),
        "universe_priors": universe_priors,
        "summary_rows": summary_rows,
        "year_grid_rows": year_grid_rows,
        "run_progression": progression,
    }


def _load_price_projection_context(
    projection_root: Path, symbol: str
) -> dict[str, Any] | None:
    """EV/Rev price projections for ``symbol``: newest containing run + tracking.

    Same latest-available fallback as the growth loader: the detail block comes from
    the newest run containing the symbol, flagged via ``detail_run.is_latest``.
    """
    runs = _discover_projection_runs(projection_root, "finproj")
    if not runs:
        return None
    normalized, bare = _symbol_candidates(symbol)

    progression: list[dict[str, Any]] = []
    for run in runs:
        record: dict[str, Any] = {
            "run_id": run["run_id"],
            "day_label": run["day_label"],
            "database": run["database"].as_posix(),
            "found": False,
            "base": {},
        }
        progression.append(record)
        try:
            rows = _projection_symbol_rows(
                run["database"],
                "projection_summary",
                normalized,
                bare,
                extra_where="AND t.scenario = 'base'",
            )
            if rows:
                record["base"] = {
                    field: rows[0].get(field) for field in PRICE_PROGRESSION_FIELDS
                }
            record["found"] = bool(rows)
        except Exception:
            continue

    # Detail block: newest run that actually contains the symbol.
    detail_entry = next(
        (entry for entry in reversed(progression) if entry["found"]), None
    )
    summary_rows: list[dict[str, Any]] = []
    year_grid_rows: list[dict[str, Any]] = []
    if detail_entry is not None:
        detail_db = Path(detail_entry["database"])
        try:
            summary_rows = _projection_symbol_rows(
                detail_db,
                "projection_summary",
                normalized,
                bare,
                order_by="ORDER BY t.scenario",
            )
            year_grid_rows = _projection_symbol_rows(
                detail_db,
                "projection_year_grid",
                normalized,
                bare,
                order_by="ORDER BY t.scenario, t.year",
            )
        except Exception:
            pass

    latest = runs[-1]
    return {
        "suite": "price",
        "runs_available": len(runs),
        "latest_run": {
            "run_id": latest["run_id"],
            "day_label": latest["day_label"],
            "database": latest["database"].as_posix(),
            "symbol_present": bool(progression[-1]["found"]),
        },
        "detail_run": (
            {
                "run_id": detail_entry["run_id"],
                "day_label": detail_entry["day_label"],
                "database": detail_entry["database"],
                "is_latest": detail_entry["run_id"] == latest["run_id"],
            }
            if detail_entry is not None
            else None
        ),
        "summary_rows": summary_rows,
        "year_grid_rows": year_grid_rows,
        "run_progression": progression,
    }


def _extract_forward_guidance(all_fields_row: dict[str, Any]) -> dict[str, Any]:
    """Street / analyst forward consensus from the all-fields snapshot.

    Keeps the raw forecast/target fields (``fields``) and derives the implied growth
    the consensus already prices in (``derived``) so the report can cross the street
    view against the model's growth lanes and price projections.
    """
    fields: dict[str, Any] = {}
    for column, label, fmt in FORWARD_GUIDANCE_FIELDS:
        value = all_fields_row.get(column)
        if value in (None, ""):
            continue
        fields[column] = {
            "label": label,
            "fmt": fmt,
            "value": value,
            "display": _fmt_value(value, fmt),
        }

    derived: dict[str, Any] = {}
    close = _to_float(all_fields_row.get("close"))
    for column in (
        "price_target_low",
        "price_target_median",
        "price_target_average",
        "price_target_1y",
        "price_target_high",
    ):
        target = _to_float(all_fields_row.get(column))
        if target is not None and close:
            derived[f"{column}_upside_pct"] = (target / close - 1) * 100
    revenue_ttm = _to_float(all_fields_row.get("total_revenue_ttm"))
    revenue_fq = _to_float(all_fields_row.get("total_revenue_fq"))
    forecast_fy = _to_float(all_fields_row.get("revenue_forecast_next_fy"))
    forecast_fq = _to_float(all_fields_row.get("revenue_forecast_next_fq"))
    if forecast_fy is not None and revenue_ttm:
        derived["street_next_fy_revenue_growth_pct"] = (
            forecast_fy / revenue_ttm - 1
        ) * 100
    if forecast_fq is not None and revenue_fq:
        derived["street_next_fq_revenue_growth_pct"] = (
            forecast_fq / revenue_fq - 1
        ) * 100
    eps_ttm = _to_float(all_fields_row.get("earnings_per_share_diluted_ttm"))
    eps_next_fy = _to_float(all_fields_row.get("earnings_per_share_forecast_next_fy"))
    if eps_next_fy is not None and eps_ttm:
        derived["street_next_fy_eps_growth_pct"] = (eps_next_fy / eps_ttm - 1) * 100

    return {"fields": fields, "derived": derived}


# ---------------------------------------------------------------------------
# Derived insight builders (executive read + entry/upside/downside/risk)
# ---------------------------------------------------------------------------
def _snapshot_number(snapshot: dict[str, Any], column: str) -> float | None:
    entry = snapshot.get(column)
    if not entry:
        return None
    return _to_float(entry.get("value"))


def _pick_row(
    rows: list[dict[str, Any]], *, scenario: str = "base", lane: str | None = None
) -> dict[str, Any]:
    for row in rows:
        if str(row.get("scenario")) != scenario:
            continue
        if lane is not None and str(row.get("growth_lane")) != lane:
            continue
        return row
    return {}


def _build_growth_support_assessment(
    guidance: dict[str, Any],
    growth: dict[str, Any] | None,
    price: dict[str, Any] | None,
) -> dict[str, Any]:
    """Cross street consensus x model growth lanes x model price upside.

    Answers the report's core forward question: does the projected growth actually
    support the model's forward value, or is the terminal value a multiple re-rating
    that the growth path cannot underwrite?
    """
    growth_rows = (growth or {}).get("summary_rows") or []
    price_rows = (price or {}).get("summary_rows") or []
    price_grid = (price or {}).get("year_grid_rows") or []
    derived = guidance.get("derived") or {}

    own = _pick_row(growth_rows, lane="own")
    peer = _pick_row(growth_rows, lane="peer")
    universe = _pick_row(growth_rows, lane="universe")
    base_price = _pick_row(price_rows)

    own_cagr = _to_float(own.get("revenue_cagr_implied_pct"))
    peer_cagr = _to_float(peer.get("revenue_cagr_implied_pct"))
    univ_cagr = _to_float(universe.get("revenue_cagr_implied_pct"))
    own_y1 = _to_float(own.get("y1_revenue_growth_pct"))
    street_fy_growth = derived.get("street_next_fy_revenue_growth_pct")
    street_eps_growth = derived.get("street_next_fy_eps_growth_pct")
    primary_upside = _to_float(base_price.get("primary_upside_pct"))
    implied_price_cagr = _to_float(base_price.get("implied_price_cagr_pct"))
    terminal_price = _to_float(base_price.get("terminal_price"))

    checks: list[dict[str, Any]] = []

    # 1) Growth needed to support the terminal price at TODAY's multiple vs projected.
    grid_t5 = next(
        (
            row
            for row in price_grid
            if str(row.get("scenario")) == "base"
            and int(_to_float(row.get("year")) or -1) == 5
        ),
        {},
    )
    grid_t0 = next(
        (
            row
            for row in price_grid
            if str(row.get("scenario")) == "base"
            and int(_to_float(row.get("year")) or -1) == 0
        ),
        {},
    )
    current_ev_rev = _to_float(base_price.get("enterprise_value_to_revenue_ttm"))
    terminal_ev_rev = _to_float(base_price.get("terminal_ev_rev"))
    peer_ev_rev = _to_float(base_price.get("ev_rev_peer_median"))
    equity_t5 = _to_float(grid_t5.get("equity_value"))
    net_debt_t5 = _to_float(grid_t5.get("net_debt"))
    rev_t0 = _to_float(grid_t0.get("total_revenue_ttm")) or _to_float(
        base_price.get("total_revenue_ttm")
    )
    required: dict[str, Any] | None = None
    if equity_t5 is not None and current_ev_rev and rev_t0 and rev_t0 > 0:
        ev_required = equity_t5 + (net_debt_t5 or 0.0)
        required_revenue = ev_required / current_ev_rev
        if required_revenue > 0:
            required = {
                "required_terminal_revenue": required_revenue,
                "required_revenue_cagr_pct": ((required_revenue / rev_t0) ** 0.2 - 1)
                * 100,
                "current_ev_rev": current_ev_rev,
                "terminal_ev_rev": terminal_ev_rev,
                "peer_median_ev_rev": peer_ev_rev,
            }
    if required and own_cagr is not None:
        lane_cagrs = [c for c in (own_cagr, peer_cagr, univ_cagr) if c is not None]
        best_projected = max(lane_cagrs) if lane_cagrs else own_cagr
        growth_supported = required["required_revenue_cagr_pct"] <= best_projected + 2
        terminal_upside = _to_float(base_price.get("terminal_upside_pct"))
        if terminal_upside is not None and terminal_upside < 0:
            summary = (
                f"the base terminal price {_price_opt(terminal_price)} sits BELOW spot "
                f"({_pct_opt(terminal_upside)}): projected growth (own {own_cagr:+.1f}%, "
                f"peer {_pct_opt(peer_cagr)}, universe {_pct_opt(univ_cagr)}) does not "
                f"offset the modeled multiple de-rating from {current_ev_rev:.2f}x"
            )
            if terminal_ev_rev is not None:
                summary += f" toward {terminal_ev_rev:.2f}x"
                if peer_ev_rev is not None:
                    summary += f" (peer median {peer_ev_rev:.2f}x)"
            summary += ". Growth support is insufficient against valuation compression."
        elif growth_supported:
            summary = (
                f"growth needed for the base terminal price {_price_opt(terminal_price)} "
                f"at today's EV/Rev {current_ev_rev:.2f}x is "
                f"{required['required_revenue_cagr_pct']:+.1f}%/yr - within reach of the "
                f"projected lanes (own {own_cagr:+.1f}%, peer {_pct_opt(peer_cagr)}, "
                f"universe {_pct_opt(univ_cagr)}): the forward value IS growth-supported."
            )
        else:
            summary = (
                f"growth needed for the base terminal price {_price_opt(terminal_price)} "
                f"at today's EV/Rev {current_ev_rev:.2f}x: revenue ~"
                f"{_fmt_money(required['required_terminal_revenue'])} by Y5 "
                f"({required['required_revenue_cagr_pct']:+.1f}%/yr) - vs projected CAGRs "
                f"own {own_cagr:+.1f}% / peer {_pct_opt(peer_cagr)} / universe "
                f"{_pct_opt(univ_cagr)}. Growth alone CANNOT underwrite the terminal "
                f"value"
            )
            if terminal_ev_rev is not None:
                summary += f"; the model re-rates the multiple to {terminal_ev_rev:.2f}x"
                if peer_ev_rev is not None:
                    summary += f" (peer median {peer_ev_rev:.2f}x)"
            summary += ". This is a valuation-normalization thesis, not a growth thesis."
        checks.append(
            {
                "name": "growth_needed_vs_projected",
                "values": {
                    **required,
                    "own_cagr_pct": own_cagr,
                    "peer_cagr_pct": peer_cagr,
                    "universe_cagr_pct": univ_cagr,
                    "terminal_upside_pct": terminal_upside,
                    "growth_supported": growth_supported,
                },
                "summary": summary,
            }
        )

    # 2) Street vs model growth alignment.
    if street_fy_growth is not None or own_cagr is not None:
        bits = []
        if street_fy_growth is not None:
            bits.append(
                f"street next-FY revenue forecast implies {street_fy_growth:+.1f}% growth"
            )
        if own_y1 is not None and own_cagr is not None:
            bits.append(
                f"model own lane Y1 {own_y1:+.1f}% / 5y CAGR {own_cagr:+.1f}% "
                f"(source {own.get('growth_lane_source') or 'n/a'})"
            )
        read = ""
        if own_cagr is not None and peer_cagr is not None:
            gap = own_cagr - peer_cagr
            if gap <= -2:
                read = f"the name is projected to undergrow its peers by {abs(gap):.1f} pp/yr"
            elif gap >= 2:
                read = f"the name is projected to outgrow its peers by {gap:.1f} pp/yr"
            else:
                read = "projected growth is roughly in line with peers"
        if own.get("forecast_rejected"):
            read += ("; " if read else "") + (
                "street forecast was rejected as extreme - own lane fell back to history"
            )
        checks.append(
            {
                "name": "street_vs_model_growth",
                "values": {
                    "street_next_fy_revenue_growth_pct": street_fy_growth,
                    "own_y1_growth_pct": own_y1,
                    "own_cagr_pct": own_cagr,
                    "peer_cagr_pct": peer_cagr,
                    "universe_cagr_pct": univ_cagr,
                    "forecast_rejected": own.get("forecast_rejected"),
                },
                "summary": "; ".join(bits) + (f" - {read}." if read else "."),
            }
        )

    # 3) Street EPS-vs-revenue divergence (quality of the consensus earnings path).
    if street_eps_growth is not None and street_fy_growth is not None:
        gap = street_eps_growth - street_fy_growth
        if gap >= 15:
            summary = (
                f"street earnings path is margin/share-count driven, not revenue driven: "
                f"next-FY EPS {street_eps_growth:+.1f}% on revenue only "
                f"{street_fy_growth:+.1f}% - treat the implied earnings jump as "
                f"execution-sensitive."
            )
        elif gap <= -15:
            summary = (
                f"street next-FY EPS {street_eps_growth:+.1f}% trails revenue "
                f"{street_fy_growth:+.1f}% - consensus prices margin compression."
            )
        else:
            summary = (
                f"street next-FY EPS ({street_eps_growth:+.1f}%) and revenue "
                f"({street_fy_growth:+.1f}%) forecasts move together."
            )
        checks.append(
            {
                "name": "street_eps_revenue_divergence",
                "values": {
                    "street_next_fy_eps_growth_pct": street_eps_growth,
                    "street_next_fy_revenue_growth_pct": street_fy_growth,
                    "divergence_pp": gap,
                },
                "summary": summary,
            }
        )

    # 4) Model vs street price gap.
    street_price_upside = _to_float(base_price.get("st_street_price_upside_pct"))
    street_ref = (
        street_price_upside
        if street_price_upside is not None
        else derived.get("price_target_median_upside_pct")
    )
    if primary_upside is not None and street_ref is not None:
        gap = primary_upside - street_ref
        if gap >= 15:
            summary = (
                f"model primary upside {primary_upside:+.1f}% is far above street "
                f"({street_ref:+.1f}%) - the model sees value consensus does not; expect "
                f"slow, milestone-driven repricing rather than quick convergence."
            )
        elif gap <= -15:
            summary = (
                f"model primary upside {primary_upside:+.1f}% is well below street "
                f"({street_ref:+.1f}%) - the model is the conservative voice here."
            )
        else:
            summary = (
                f"model primary upside {primary_upside:+.1f}% is broadly aligned with "
                f"street ({street_ref:+.1f}%)."
            )
        checks.append(
            {
                "name": "model_vs_street_price",
                "values": {
                    "primary_upside_pct": primary_upside,
                    "street_upside_pct": street_ref,
                    "gap_pp": gap,
                },
                "summary": summary,
            }
        )

    # 5) Scenario band / downside cushion.
    bear_up = _to_float(_pick_row(price_rows, scenario="bear").get("primary_upside_pct"))
    bull_up = _to_float(_pick_row(price_rows, scenario="bull").get("primary_upside_pct"))
    width = _to_float(base_price.get("scenario_width_y5_pp"))
    if bear_up is not None and bull_up is not None:
        summary = (
            f"scenario band: bear {bear_up:+.1f}% / base {_pct_opt(primary_upside)} / "
            f"bull {bull_up:+.1f}%"
        )
        if bear_up >= 0:
            summary += " - even the bear case is upside (downside cushioned in model terms)"
        else:
            summary += f" - the bear case is a {bear_up:+.1f}% drawdown (real downside)"
        if width is not None and width >= 50:
            summary += f"; scenario width {width:.0f} pp is wide - high outcome dispersion"
        checks.append(
            {
                "name": "scenario_band",
                "values": {
                    "bear_primary_upside_pct": bear_up,
                    "base_primary_upside_pct": primary_upside,
                    "bull_primary_upside_pct": bull_up,
                    "scenario_width_y5_pp": width,
                },
                "summary": summary + ".",
            }
        )

    # 6) Model integrity / trust flags.
    integrity_bits = []
    peer_trust = _to_float(base_price.get("lane_peer_trust"))
    hist_trust = _to_float(base_price.get("lane_hist_trust"))
    if peer_trust is not None:
        integrity_bits.append(f"peer trust {peer_trust:.2f}")
    if hist_trust is not None:
        integrity_bits.append(f"history trust {hist_trust:.2f}")
    flags = [f for f in str(base_price.get("decision_flags") or "").split("|") if f]
    if flags:
        integrity_bits.append("flags: " + ", ".join(flags))
    flagged = [
        label
        for key, label in (
            ("margin_risk_flag", "margin risk"),
            ("outlier_flag", "outlier"),
            ("thin_peer_set_flag", "thin peer set"),
        )
        if base_price.get(key) in (True, 1, "True", "true")
    ]
    if flagged:
        integrity_bits.append("FLAGGED: " + ", ".join(flagged))
    if integrity_bits:
        checks.append(
            {
                "name": "model_integrity",
                "values": {
                    "lane_peer_trust": peer_trust,
                    "lane_hist_trust": hist_trust,
                    "decision_flags": flags,
                    "hard_flags": flagged,
                },
                "summary": "model integrity: " + "; ".join(integrity_bits) + ".",
            }
        )

    headline = None
    if primary_upside is not None:
        headline = (
            f"model primary upside {primary_upside:+.1f}% "
            f"(implied price CAGR {_pct_opt(implied_price_cagr)}) vs street "
            f"{_pct_opt(street_ref) if street_ref is not None else 'n/a'}"
        )
        growth_check = next(
            (c for c in checks if c["name"] == "growth_needed_vs_projected"), None
        )
        if growth_check:
            check_values = growth_check["values"]
            terminal_upside_value = check_values.get("terminal_upside_pct")
            if terminal_upside_value is not None and terminal_upside_value < 0:
                headline += (
                    "; model terminal price sits below spot - multiple de-rating "
                    "not offset by projected growth"
                )
            elif check_values.get("growth_supported"):
                headline += "; terminal value is growth-supported"
            else:
                headline += (
                    "; terminal value rests on multiple re-rating, not growth delivery"
                )
        headline += "."

    return {"checks": checks, "headline": headline}


def _build_executive_read(
    snapshot: dict[str, Any],
    positioning: dict[str, Any] | None,
    move_prediction: dict[str, Any] | None,
    edge_research: dict[str, Any],
    generated_at_utc: datetime,
    projection: dict[str, Any] | None = None,
) -> list[str]:
    bullets: list[str] = []
    industry_metrics = ((positioning or {}).get("industry") or {}).get("metrics") or {}

    # Valuation stance vs industry peers.
    valuation_parts = []
    for label in ("P/E (TTM)", "EV/EBITDA (TTM)", "P/B (FQ)", "P/S"):
        metric = industry_metrics.get(label)
        if metric:
            valuation_parts.append(
                f"{label} {metric['display']} ({_ordinal(metric['percentile'])} pct"
                f"{', ' + metric['read'] if metric['read'] else ''})"
            )
    if valuation_parts:
        bullets.append("Valuation vs industry: " + "; ".join(valuation_parts) + ".")

    # Tape / momentum stance.
    perf_ytd = industry_metrics.get("Perf YTD")
    perf_1m = industry_metrics.get("Perf 1M")
    rating_metric = industry_metrics.get("Technical Rating")
    rsi = _snapshot_number(snapshot, "RSI")
    tape_parts = []
    if perf_ytd:
        tape_parts.append(
            f"YTD {perf_ytd['display']} ({_ordinal(perf_ytd['percentile'])} pct industry)"
        )
    if perf_1m:
        tape_parts.append(f"1M {perf_1m['display']}")
    if rsi is not None:
        tape_parts.append(f"RSI {rsi:.0f} ({_rsi_zone(rsi)})")
    if rating_metric:
        tape_parts.append(
            f"technical rating {rating_metric['display']} "
            f"({_ordinal(rating_metric['percentile'])} pct)"
        )
    if tape_parts:
        bullets.append("Tape: " + "; ".join(tape_parts) + ".")

    # Quality stance (absolute levels; cross-industry percentiles are not meaningful).
    quality_parts = []
    for column, label in (
        ("gross_margin", "gross margin"),
        ("net_margin", "net margin"),
        ("return_on_equity", "ROE"),
        ("free_cash_flow_margin_ttm", "FCF margin"),
    ):
        value = _snapshot_number(snapshot, column)
        if value is not None:
            quality_parts.append(f"{label} {value:.1f}%")
    net_debt = _snapshot_number(snapshot, "net_debt_to_ebitda_fy")
    if net_debt is not None:
        quality_parts.append(f"{_leverage_read(net_debt)} (net debt/EBITDA {net_debt:.2f})")
    altman = _snapshot_number(snapshot, "altman_z_score_ttm")
    if altman is not None:
        quality_parts.append(f"Altman Z {altman:.1f} ({_altman_zone(altman)})")
    if quality_parts:
        bullets.append("Quality: " + "; ".join(quality_parts) + ".")

    # Model stance.
    if move_prediction:
        consensus_rows = move_prediction.get("consensus_horizon_scores") or []
        conviction_rows = move_prediction.get("conviction_rankings") or []
        model_parts = []
        if consensus_rows:
            directions = {str(r.get("direction") or "") for r in consensus_rows}
            confidences = [
                _to_float(r.get("confidence")) for r in consensus_rows
            ]
            confidences = [c for c in confidences if c is not None]
            avg_conf = (
                sum(confidences) / len(confidences) if confidences else None
            )
            direction_text = (
                next(iter(directions)) if len(directions) == 1 else "mixed"
            )
            model_parts.append(
                f"consensus {direction_text} on {len(consensus_rows)} horizons"
                + (f" (avg confidence ~{avg_conf:.0f}%)" if avg_conf else "")
            )
            risk_tiers = {str(r.get("risk_tier") or "") for r in consensus_rows}
            if len(risk_tiers) == 1:
                model_parts.append(f"risk tier {next(iter(risk_tiers))}")
        if conviction_rows:
            row = conviction_rows[0]
            model_parts.append(
                f"conviction sleeve={row.get('sleeve')} "
                f"entry={row.get('entry_readiness')} "
                f"signal={row.get('manager_action_signal')}"
            )
        if model_parts:
            bullets.append("Models: " + "; ".join(model_parts) + ".")

    # Upside anchors.
    upside_parts = []
    target = _snapshot_number(snapshot, "price_target_median")
    target_delta = _snapshot_number(snapshot, "price_target_1y_delta")
    close = _snapshot_number(snapshot, "close")
    if target is not None:
        delta_text = (
            f"{target_delta:+.1f}%" if target_delta is not None
            else (f"{(target / close - 1) * 100:+.1f}%" if close else "")
        )
        upside_parts.append(f"median analyst target {target:,.2f} ({delta_text})")
    graham = _snapshot_number(snapshot, "graham_numbers_ttm")
    if graham is not None and close:
        upside_parts.append(
            f"Graham number {graham:,.2f} ({(graham / close - 1) * 100:+.1f}% vs close)"
        )
    unified_row = (edge_research.get("unified_edge_highlights") or {}).get("row") or {}
    fwd_upside = _to_float(unified_row.get("forward_valuation_upside_pct"))
    if fwd_upside is not None:
        upside_parts.append(f"edge forward-valuation upside {fwd_upside:+.1f}%")
    if upside_parts:
        bullets.append("Upside anchors: " + "; ".join(upside_parts) + ".")

    # Forward value (financial-projection suite x street consensus).
    assessment = ((projection or {}).get("growth_support_assessment") or {})
    headline = assessment.get("headline")
    if headline:
        bullets.append("Forward value: " + headline)

    # Risk anchors.
    risk_parts = []
    high_52w = _snapshot_number(snapshot, "price_52_week_high")
    low_52w = _snapshot_number(snapshot, "price_52_week_low")
    if close and high_52w:
        risk_parts.append(f"{(close / high_52w - 1) * 100:+.1f}% vs 52W high")
    if close and low_52w:
        risk_parts.append(f"{(close / low_52w - 1) * 100:+.1f}% vs 52W low")
    atrp = _snapshot_number(snapshot, "ATRP")
    if atrp is not None:
        risk_parts.append(f"ATR {atrp:.1f}%/day")
    beta = _snapshot_number(snapshot, "beta_1_year")
    if beta is not None:
        risk_parts.append(f"beta {beta:.2f}")
    next_earnings = _snapshot_number(snapshot, "earnings_release_next_date")
    if next_earnings is not None:
        try:
            earnings_date = datetime.fromtimestamp(next_earnings, tz=timezone.utc)
            days_to = (earnings_date.date() - generated_at_utc.date()).days
            risk_parts.append(
                f"next earnings {earnings_date.strftime('%Y-%m-%d')} (in {days_to}d)"
            )
        except (OverflowError, OSError, ValueError):
            pass
    if risk_parts:
        bullets.append("Risk anchors: " + "; ".join(risk_parts) + ".")

    return bullets


def _build_entry_upside_downside_risk(
    snapshot: dict[str, Any],
    positioning: dict[str, Any] | None,
    move_prediction: dict[str, Any] | None,
    edge_research: dict[str, Any],
    generated_at_utc: datetime,
    projection: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    upside: list[str] = []
    downside: list[str] = []
    entry: list[str] = []

    close = _snapshot_number(snapshot, "close")

    # ---- UPSIDE ----
    target = _snapshot_number(snapshot, "price_target_median")
    target_delta = _snapshot_number(snapshot, "price_target_1y_delta")
    analyst_rating = (snapshot.get("AnalystRating") or {}).get("display")
    if target is not None and close:
        delta_text = (
            f"{target_delta:+.1f}%" if target_delta is not None
            else f"{(target / close - 1) * 100:+.1f}%"
        )
        upside.append(
            f"Analyst median target {target:,.2f} ({delta_text} vs close {close:,.2f})"
            + (f", rating {analyst_rating}" if analyst_rating else "")
        )
    graham = _snapshot_number(snapshot, "graham_numbers_ttm")
    if graham is not None and close:
        note = "asset-value support" if graham >= close else "no asset-value support"
        upside.append(
            f"Graham number {graham:,.2f} ({(graham / close - 1) * 100:+.1f}% vs close) - {note}"
        )
    unified_row = (edge_research.get("unified_edge_highlights") or {}).get("row") or {}
    fwd_upside = _to_float(unified_row.get("forward_valuation_upside_pct"))
    if fwd_upside is not None:
        upside.append(f"Edge forward-valuation upside: {fwd_upside:+.1f}% (base mode)")
    upside_win_rate = _to_float(unified_row.get("upside_hist_win_rate"))
    upside_median_fwd = _to_float(unified_row.get("upside_hist_median_fwd_pct"))
    if upside_win_rate is not None and upside_median_fwd is not None:
        upside.append(
            f"Edge upside-prediction history: win rate {upside_win_rate:.0%}, "
            f"median forward move {upside_median_fwd:+.1f}%"
        )
    if move_prediction:
        consensus_rows = move_prediction.get("consensus_horizon_scores") or []
        if consensus_rows:
            horizon_bits = [
                f"{r.get('horizon_name')} {r.get('direction')} "
                f"{_to_float(r.get('score')) or 0.0:.2f}"
                for r in sorted(
                    consensus_rows, key=lambda r: _horizon_sort_key(r.get("horizon_name"))
                )
            ]
            upside.append("Model consensus by horizon: " + " | ".join(horizon_bits))

    price_ctx = (projection or {}).get("price") or {}
    growth_ctx = (projection or {}).get("growth") or {}
    base_price_row = _pick_row(price_ctx.get("summary_rows") or [])
    primary_upside = _to_float(base_price_row.get("primary_upside_pct"))
    if primary_upside is not None:
        terminal_price = _to_float(base_price_row.get("terminal_price"))
        implied_cagr = _to_float(base_price_row.get("implied_price_cagr_pct"))
        model_y1 = _to_float(base_price_row.get("model_y1_upside_pct"))
        upside.append(
            f"EV/Rev projection (base): primary upside {primary_upside:+.1f}%"
            + (
                f", terminal price {terminal_price:,.2f}"
                if terminal_price is not None
                else ""
            )
            + (
                f", implied price CAGR {implied_cagr:+.1f}%"
                if implied_cagr is not None
                else ""
            )
            + (f"; Y1 model upside {model_y1:+.1f}%" if model_y1 is not None else "")
        )

    # ---- DOWNSIDE / RISK ----
    high_52w = _snapshot_number(snapshot, "price_52_week_high")
    low_52w = _snapshot_number(snapshot, "price_52_week_low")
    if close and high_52w and low_52w:
        downside.append(
            f"Range position: {(close / high_52w - 1) * 100:+.1f}% vs 52W high "
            f"({high_52w:,.2f}), {(close / low_52w - 1) * 100:+.1f}% vs 52W low "
            f"({low_52w:,.2f})"
        )
    vol_parts = []
    atrp = _snapshot_number(snapshot, "ATRP")
    if atrp is not None:
        vol_parts.append(f"ATR {atrp:.1f}%/day")
    vol_m = _snapshot_number(snapshot, "Volatility.M")
    if vol_m is not None:
        vol_parts.append(f"monthly volatility {vol_m:.1f}%")
    beta = _snapshot_number(snapshot, "beta_1_year")
    if beta is not None:
        vol_parts.append(f"beta {beta:.2f}")
    if vol_parts:
        downside.append("Volatility: " + "; ".join(vol_parts))
    balance_parts = []
    net_debt = _snapshot_number(snapshot, "net_debt_to_ebitda_fy")
    if net_debt is not None:
        balance_parts.append(
            f"{_leverage_read(net_debt)} (net debt/EBITDA {net_debt:.2f})"
        )
    altman = _snapshot_number(snapshot, "altman_z_score_ttm")
    if altman is not None:
        balance_parts.append(f"Altman Z {altman:.1f} ({_altman_zone(altman)})")
    current_ratio = _snapshot_number(snapshot, "current_ratio_fq")
    if current_ratio is not None:
        balance_parts.append(f"current ratio {current_ratio:.2f}")
    if balance_parts:
        downside.append("Balance sheet: " + "; ".join(balance_parts))
    next_earnings = _snapshot_number(snapshot, "earnings_release_next_date")
    if next_earnings is not None:
        try:
            earnings_date = datetime.fromtimestamp(next_earnings, tz=timezone.utc)
            days_to = (earnings_date.date() - generated_at_utc.date()).days
            surprise_bits = []
            eps_surprise = _snapshot_number(snapshot, "eps_surprise_percent_fq")
            rev_surprise = _snapshot_number(snapshot, "revenue_surprise_percent_fq")
            if eps_surprise is not None:
                surprise_bits.append(f"EPS {eps_surprise:+.1f}%")
            if rev_surprise is not None:
                surprise_bits.append(f"revenue {rev_surprise:+.1f}%")
            downside.append(
                f"Event risk: next earnings {earnings_date.strftime('%Y-%m-%d')} "
                f"(in {days_to}d)"
                + ("; last-quarter surprises: " + ", ".join(surprise_bits) if surprise_bits else "")
            )
        except (OverflowError, OSError, ValueError):
            pass
    industry_metrics = ((positioning or {}).get("industry") or {}).get("metrics") or {}
    perf_ytd = industry_metrics.get("Perf YTD")
    if perf_ytd and perf_ytd["percentile"] <= 35:
        downside.append(
            f"Momentum risk: YTD {perf_ytd['display']} "
            f"({_ordinal(perf_ytd['percentile'])} pct industry - bottom of peer group)"
        )
    bear_price_row = _pick_row(price_ctx.get("summary_rows") or [], scenario="bear")
    bear_upside = _to_float(bear_price_row.get("primary_upside_pct"))
    if bear_upside is not None:
        note = (
            "model bear case still positive"
            if bear_upside >= 0
            else "model bear case is a real drawdown"
        )
        width = _to_float(base_price_row.get("scenario_width_y5_pp"))
        downside.append(
            f"Projection bear case: {bear_upside:+.1f}% primary upside - {note}"
            + (
                f"; scenario width {width:.0f} pp (wide dispersion)"
                if width is not None and width >= 50
                else ""
            )
        )
    own_growth_row = _pick_row(growth_ctx.get("summary_rows") or [], lane="own")
    if own_growth_row.get("forecast_rejected"):
        downside.append(
            "Street revenue forecast was rejected as extreme by the growth model - "
            "the projection relies on fallback history, not consensus."
        )

    # ---- ENTRY ----
    rsi = _snapshot_number(snapshot, "RSI")
    if rsi is not None:
        entry.append(f"RSI(14) {rsi:.1f} - {_rsi_zone(rsi)} zone")
    sma_bits = []
    for column, label in (("SMA20", "SMA20"), ("SMA50", "SMA50"), ("SMA200", "SMA200")):
        sma = _snapshot_number(snapshot, column)
        if sma is not None and close:
            sma_bits.append(f"{label} {(close / sma - 1) * 100:+.1f}%")
    if sma_bits:
        sma200 = _snapshot_number(snapshot, "SMA200")
        trend_note = ""
        if sma200 is not None and close:
            trend_note = (
                " - above long-term trend"
                if close >= sma200
                else " - below SMA200, long-term trend not repaired"
            )
        entry.append("Price vs " + " | ".join(sma_bits) + trend_note)
    relvol = _snapshot_number(snapshot, "relative_volume_10d_calc")
    if relvol is not None:
        attention = (
            "elevated attention" if relvol >= 1.5
            else "quiet tape" if relvol < 1.0
            else "normal participation"
        )
        entry.append(f"Rel volume (10D) {relvol:.2f} - {attention}")
    rating = _snapshot_number(snapshot, "Recommend.All")
    if rating is not None:
        zone = (
            "buy zone" if rating >= 0.25
            else "sell zone" if rating <= -0.25
            else "neutral"
        )
        entry.append(f"Technical rating {rating:+.2f} ({zone})")
    if move_prediction:
        conviction_rows = move_prediction.get("conviction_rankings") or []
        if conviction_rows:
            row = conviction_rows[0]
            entry.append(
                f"Model entry_readiness={row.get('entry_readiness')}, "
                f"manager_action_signal={row.get('manager_action_signal')}, "
                f"conviction_score={_to_float(row.get('conviction_score')) or 0.0:.2f}"
            )

    return {"upside": upside, "downside_risk": downside, "entry": entry}


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------
def build_symbol_intelligence_report(
    symbol: str,
    *,
    all_fields_root: str | Path | None = None,
    prediction_root: str | Path | None = None,
    edge_research_output_root: str | Path | None = None,
    projection_root: str | Path | None = None,
    output_dir: str | Path | None = None,
    history_weeks: int = DEFAULT_HISTORY_WEEKS,
) -> dict[str, Any]:
    """Aggregate all TradingView-based data for ``symbol`` and write a log + JSON report."""
    requested_symbol = str(symbol or "").strip()
    if not requested_symbol:
        raise ValueError("symbol is required")

    generated_at_utc = datetime.now(timezone.utc)
    result: dict[str, Any] = {
        "requested_symbol": requested_symbol,
        "generated_at_utc": generated_at_utc.isoformat(),
        "sources": {},
        "warnings": [],
    }

    resolved_all_fields_root = (
        Path(all_fields_root) if all_fields_root else DEFAULT_ALL_FIELDS_ROOT
    )
    all_fields_db = discover_latest_all_fields_db(resolved_all_fields_root)
    all_fields_row: dict[str, Any] | None = None
    primary_all_fields_run_id: str | None = None
    if all_fields_db is None:
        result["warnings"].append(
            f"No tradingview_all_fields_*.duckdb snapshot found under {resolved_all_fields_root.as_posix()}."
        )
    else:
        result["sources"]["all_fields_database"] = str(all_fields_db)
        try:
            primary_all_fields_run_id = _select_populous_latest_run_id(all_fields_db)
        except Exception as exc:
            primary_all_fields_run_id = None
            result["warnings"].append(f"Could not resolve latest all-fields run: {exc}")
        try:
            all_fields_row = _load_all_fields_snapshot(
                all_fields_db,
                requested_symbol,
                preferred_run_id=primary_all_fields_run_id,
            )
        except Exception as exc:
            result["warnings"].append(f"All-fields lookup failed: {exc}")
        if all_fields_row is None:
            result["warnings"].append(
                f"Symbol not found in latest all-fields snapshot: {requested_symbol}"
            )

    identity = _resolve_company_identity(requested_symbol, all_fields_row)
    result["identity"] = identity
    matched_symbol = identity["matched_symbol"]
    result["matched_symbol"] = matched_symbol

    if all_fields_row:
        # Raw catalog: JSON sidecar only (need-to-check basis).
        result["all_fields_buckets"] = _bucket_all_fields_row(all_fields_row)
        result["all_fields_run_context"] = {
            "run_id": all_fields_row.get("run_id"),
            "row_number": all_fields_row.get("row_number"),
        }
        if all_fields_db is not None and primary_all_fields_run_id is not None:
            try:
                meta = query_move_prediction_duckdb(
                    all_fields_db,
                    """
                    SELECT created_at_utc FROM run_metadata WHERE run_id = ? LIMIT 1
                    """,
                    [primary_all_fields_run_id],
                )
                row_count = query_move_prediction_duckdb(
                    all_fields_db,
                    "SELECT COUNT(*) AS n FROM all_fields_rows WHERE run_id = ?",
                    [primary_all_fields_run_id],
                )
                result["all_fields_run_context"]["created_at_utc"] = str(
                    meta[0].get("created_at_utc") if meta else ""
                )
                result["all_fields_run_context"]["run_row_count"] = (
                    row_count[0].get("n") if row_count else None
                )
            except Exception:
                pass
        result["snapshot_metrics"] = _extract_snapshot(all_fields_row)
        try:
            result["relative_positioning"] = _load_relative_positioning(
                all_fields_db, all_fields_row, run_id=primary_all_fields_run_id
            )
        except Exception as exc:
            result["warnings"].append(f"Relative positioning failed: {exc}")
        try:
            result["weekly_history"] = _load_weekly_history(
                resolved_all_fields_root, matched_symbol, weeks=history_weeks
            )
        except Exception as exc:
            result["warnings"].append(f"Weekly history failed: {exc}")

    resolved_prediction_root = (
        Path(prediction_root) if prediction_root else DEFAULT_PREDICTION_ROOT
    )
    prediction_db = discover_latest_prediction_db(resolved_prediction_root)
    if prediction_db is None:
        result["warnings"].append(
            f"No move_prediction_*.duckdb weekly database found under {resolved_prediction_root.as_posix()}."
        )
    else:
        result["sources"]["move_prediction_database"] = str(prediction_db)
        try:
            move_prediction_context = _load_move_prediction_context(
                resolved_prediction_root, matched_symbol, latest_db=prediction_db
            )
        except Exception as exc:
            move_prediction_context = None
            result["warnings"].append(f"Move-prediction lookup failed: {exc}")
        if move_prediction_context is None:
            result["warnings"].append(
                f"Symbol not found in any move-prediction run: {matched_symbol}"
            )
        else:
            if not (
                move_prediction_context.get("is_latest_database")
                and move_prediction_context.get("is_latest_run")
            ):
                result["warnings"].append(
                    "Latest move-prediction run lacks the symbol; fell back to run "
                    f"{move_prediction_context.get('source_run_id')} in "
                    f"{Path(str(move_prediction_context.get('source_database') or '')).name}."
                )
            result["move_prediction"] = move_prediction_context
        try:
            result["prediction_weekly_history"] = _load_prediction_weekly_history(
                resolved_prediction_root, matched_symbol
            )
        except Exception as exc:
            result["warnings"].append(f"Prediction weekly history failed: {exc}")

    edge_research: dict[str, Any] = {}
    try:
        parent_run_dir = discover_latest_edge_parent_run_dir(
            output_root=edge_research_output_root
        )
        result["sources"]["edge_research_parent_run_dir"] = str(parent_run_dir)
        fallback_dirs = [
            path
            for path in _discover_edge_parent_run_dirs(parent_run_dir)
            if path != parent_run_dir
        ]
        edge_research = _load_edge_research_context(
            parent_run_dir, matched_symbol, fallback_parent_dirs=fallback_dirs
        )
        fallback_lenses = [
            label
            for label, entry in edge_research.items()
            if isinstance(entry, dict)
            and entry.get("row")
            and entry.get("source_is_latest") is False
        ]
        if fallback_lenses:
            result["warnings"].append(
                "Latest edge-research run lacks the symbol in "
                f"{len(fallback_lenses)} lens(es); fell back to older runs for: "
                + ", ".join(fallback_lenses)
                + "."
            )
        result["edge_research"] = edge_research
    except Exception as exc:
        result["warnings"].append(f"Edge-research lookup unavailable: {exc}")

    # Financial-projection suite (growth lanes + EV/Rev price) + street guidance.
    if all_fields_row:
        result["forward_guidance"] = _extract_forward_guidance(all_fields_row)
    resolved_projection_root = (
        Path(projection_root) if projection_root else DEFAULT_PROJECTION_ROOT
    )
    growth_projection: dict[str, Any] | None = None
    price_projection: dict[str, Any] | None = None
    if not resolved_projection_root.exists():
        result["warnings"].append(
            "No financial-projection output found under "
            f"{resolved_projection_root.as_posix()}."
        )
    else:
        result["sources"]["financial_projection_root"] = str(resolved_projection_root)
        try:
            growth_projection = _load_growth_projection_context(
                resolved_projection_root, matched_symbol
            )
        except Exception as exc:
            result["warnings"].append(f"Growth-projection lookup failed: {exc}")
        try:
            price_projection = _load_price_projection_context(
                resolved_projection_root, matched_symbol
            )
        except Exception as exc:
            result["warnings"].append(f"Price-projection lookup failed: {exc}")
    projection_payload: dict[str, Any] = {}
    for suite_label, suite_ctx in (
        ("growth", growth_projection),
        ("price", price_projection),
    ):
        if not suite_ctx:
            continue
        projection_payload[suite_label] = suite_ctx
        detail_run = suite_ctx.get("detail_run")
        latest_run_id = (suite_ctx.get("latest_run") or {}).get("run_id")
        if detail_run is None:
            result["warnings"].append(
                f"Symbol not found in any {suite_label}-projection run "
                f"(latest: {latest_run_id})."
            )
        elif not detail_run.get("is_latest"):
            result["warnings"].append(
                f"Latest {suite_label}-projection run ({latest_run_id}) lacks the "
                f"symbol; fell back to {detail_run.get('run_id')} "
                f"({detail_run.get('day_label')})."
            )
    if projection_payload:
        try:
            projection_payload["growth_support_assessment"] = (
                _build_growth_support_assessment(
                    result.get("forward_guidance") or {},
                    growth_projection,
                    price_projection,
                )
            )
        except Exception as exc:
            result["warnings"].append(f"Growth-support assessment failed: {exc}")
        result["financial_projection"] = projection_payload

    # Derived insight layers (rendered in the log, stored in the JSON for checking).
    result["executive_read"] = _build_executive_read(
        result.get("snapshot_metrics") or {},
        result.get("relative_positioning"),
        result.get("move_prediction"),
        edge_research,
        generated_at_utc,
        projection=result.get("financial_projection"),
    )
    result["entry_upside_downside_risk"] = _build_entry_upside_downside_risk(
        result.get("snapshot_metrics") or {},
        result.get("relative_positioning"),
        result.get("move_prediction"),
        edge_research,
        generated_at_utc,
        projection=result.get("financial_projection"),
    )

    resolved_output_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_ROOT
    log_path, json_path = _write_symbol_intelligence_log(
        result, resolved_output_dir, generated_at_utc
    )
    result["log_path"] = str(log_path)
    result["json_path"] = str(json_path)
    return result


# ---------------------------------------------------------------------------
# Log rendering
# ---------------------------------------------------------------------------
def _write_symbol_intelligence_log(
    result: dict[str, Any], output_dir: Path, generated_at_utc: datetime
) -> tuple[Path, Path]:
    token = _symbol_file_token(
        result.get("matched_symbol") or result["requested_symbol"]
    )
    date_label = generated_at_utc.strftime("%Y-%m-%d")
    run_timestamp = generated_at_utc.strftime("%H%M%S")
    # One folder per symbol, one sub-folder per scan date; same-day reruns stack as run_<time>.
    run_dir = output_dir / token / date_label
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / f"{token}__run_{run_timestamp}.log"
    json_path = run_dir / f"{token}__run_{run_timestamp}.json"
    if log_path.exists():
        log_path.unlink()

    lines: list[str] = []

    def write_line(text: str = "") -> None:
        lines.append(text)

    identity = result.get("identity") or {}
    snapshot = result.get("snapshot_metrics") or {}
    positioning = result.get("relative_positioning") or {}
    move_prediction = result.get("move_prediction") or {}
    edge_research = result.get("edge_research") or {}
    warnings = result.get("warnings") or []

    # ---------------------------------------------------------------- header
    write_line("=" * 100)
    write_line(
        f"SYMBOL INTELLIGENCE - {result.get('matched_symbol','')} "
        f"({identity.get('full_name', 'UNKNOWN')})"
    )
    write_line("=" * 100)
    write_line(f"Requested symbol : {result.get('requested_symbol','')}")
    write_line(f"Matched symbol   : {result.get('matched_symbol','')}")
    write_line(
        f"Exchange/Market  : {identity.get('exchange','')} / {identity.get('market','')}"
    )
    write_line(
        f"Sector/Industry  : {identity.get('sector','')} / {identity.get('industry','')}"
    )
    write_line(f"Currency         : {identity.get('currency','')}")
    write_line(f"Generated (UTC)  : {generated_at_utc.strftime('%Y-%m-%d %H:%M:%S')}")

    coverage_bits = []
    run_context = result.get("all_fields_run_context") or {}
    if run_context.get("run_id"):
        created = str(run_context.get("created_at_utc") or "")[:16]
        row_count = run_context.get("run_row_count")
        coverage_bits.append(
            f"all-fields OK (scan {created} UTC"
            + (f", {int(row_count):,} rows" if row_count else "")
            + ")"
        )
    elif result.get("sources", {}).get("all_fields_database"):
        coverage_bits.append("all-fields: symbol not found")
    else:
        coverage_bits.append("all-fields MISSING")
    prediction_db_path = result.get("sources", {}).get("move_prediction_database")
    if prediction_db_path:
        week_label = Path(str(prediction_db_path)).stem.replace("move_prediction_", "")
        mp_ctx = result.get("move_prediction")
        if mp_ctx:
            mp_bit = f"move-prediction OK ({week_label})"
            if not (
                mp_ctx.get("is_latest_database") and mp_ctx.get("is_latest_run")
            ):
                mp_bit += (
                    f" via fallback "
                    f"{Path(str(mp_ctx.get('source_database') or '')).stem}"
                )
            coverage_bits.append(mp_bit)
        else:
            coverage_bits.append(f"move-prediction: symbol not found ({week_label})")
    else:
        coverage_bits.append("move-prediction MISSING")
    lenses_present = sum(1 for entry in edge_research.values() if entry.get("row"))
    if edge_research:
        coverage_bits.append(
            f"edge-research OK (symbol in {lenses_present}/{len(edge_research)} lenses)"
        )
    else:
        coverage_bits.append("edge-research MISSING")
    projection_payload = result.get("financial_projection") or {}
    if projection_payload:
        growth_ctx = projection_payload.get("growth") or {}
        price_ctx = projection_payload.get("price") or {}
        projection_bits = []
        for suite_label, suite_ctx in (("growth", growth_ctx), ("price", price_ctx)):
            if not suite_ctx:
                continue
            bit = f"{suite_label} {suite_ctx.get('runs_available', 0)} runs"
            detail_run = suite_ctx.get("detail_run")
            if detail_run is None:
                bit += " (symbol absent)"
            elif not detail_run.get("is_latest"):
                bit += f" (fallback {detail_run.get('day_label')})"
            projection_bits.append(bit)
        coverage_bits.append(
            "projections " + (", ".join(projection_bits) or "suites empty")
        )
    else:
        coverage_bits.append("projections MISSING")
    write_line(f"Coverage         : {' | '.join(coverage_bits)}")
    write_line(
        "Raw data         : full field catalog + unabridged lens rows are in the JSON "
        "sidecar (need-to-check basis):"
    )
    write_line(f"                   {json_path.name}")
    write_line("")

    if warnings:
        write_line("DATA COVERAGE WARNINGS")
        for warning in warnings:
            write_line(f"  - {warning}")
        write_line("")

    # ------------------------------------------------------- 1) executive read
    write_line("-" * 100)
    write_line("1) EXECUTIVE READ")
    write_line("-" * 100)
    executive_read = result.get("executive_read") or []
    if executive_read:
        for bullet in executive_read:
            for wrapped in _wrap_text("- " + bullet):
                write_line(wrapped)
    else:
        write_line("  (insufficient data for an executive read)")
    write_line("")

    # -------------------------------------------------- 2) key metrics snapshot
    write_line("-" * 100)
    write_line("2) KEY METRICS SNAPSHOT (curated - full raw catalog lives in the JSON)")
    write_line("-" * 100)
    if snapshot:
        for group_name, fields in SNAPSHOT_GROUPS:
            group_lines = []
            for column, label, _fmt in fields:
                entry = snapshot.get(column)
                if not entry:
                    continue
                group_lines.append(f"    {label:<28} {entry['display']}")
            if group_lines:
                write_line(f"  [{group_name}]")
                write_line("\n".join(group_lines))
    else:
        write_line("  (no all-fields snapshot available for this symbol)")
    write_line("")

    # --------------------------------------------- 3) relative positioning table
    write_line("-" * 100)
    write_line("3) RELATIVE POSITIONING - UNIVERSE & INDUSTRY (percentile ranks)")
    write_line("-" * 100)
    industry_scope = positioning.get("industry") or {}
    universe_scope = positioning.get("universe") or {}
    if industry_scope or universe_scope:
        industry_metrics = industry_scope.get("metrics") or {}
        universe_metrics = universe_scope.get("metrics") or {}
        all_labels = list(
            dict.fromkeys(list(universe_metrics) + list(industry_metrics))
        )
        write_line(
            f"  {'Metric':<22} {'Value':>10}   {'Universe':<18} {'Industry':<18} Read"
        )
        for label in all_labels:
            uni = universe_metrics.get(label)
            ind = industry_metrics.get(label)
            value_text = (ind or uni or {}).get("display", "n/a")
            uni_text = (
                f"{_ordinal(uni['percentile']):>5} [{_percentile_bar(uni['percentile'])}]"
                if uni
                else " " * 18
            )
            ind_text = (
                f"{_ordinal(ind['percentile']):>5} [{_percentile_bar(ind['percentile'])}]"
                if ind
                else " " * 18
            )
            read = (ind or uni or {}).get("read", "")
            write_line(
                f"  {label:<22} {value_text:>10}   {uni_text:<18} {ind_text:<18} {read}"
            )
        basis_bits = []
        if universe_scope:
            basis_bits.append(
                f"universe = {universe_scope['scope']} "
                f"({universe_scope['peer_count']:,} symbols)"
            )
        if industry_scope:
            basis_bits.append(
                f"industry = {industry_scope['scope']} "
                f"({industry_scope['peer_count']:,} peers)"
            )
        write_line("  Basis: " + " | ".join(basis_bits))
        write_line(
            "  Note: valuation percentiles are computed among positive values only; "
            "lower = cheaper for valuation multiples."
        )
    else:
        write_line("  Not available (insufficient peer population or missing industry/sector).")
    write_line("")

    # ------------------------------------------- 4) weekly historical progression
    write_line("-" * 100)
    write_line(
        "4) HISTORICAL PROGRESSION (entire weekly dataset: all-fields key metrics + model scoring)"
    )
    write_line("-" * 100)
    weekly_history = result.get("weekly_history") or {}
    history_rows = weekly_history.get("rows") or []
    prediction_history = result.get("prediction_weekly_history") or []
    if history_rows:
        write_line(
            f"  Coverage: symbol in {weekly_history.get('weeks_with_symbol', 0)}"
            f"/{weekly_history.get('weeks_available', 0)} weekly all-fields scans; "
            f"model scoring from {len(prediction_history)} weekly prediction databases."
        )
        write_line("")
        group_trends = {
            "Price & valuation": (
                ("close", "price", "ratio"),
                ("price_earnings_ttm", "P/E", "rating"),
                ("Perf.YTD", "YTD perf", "pct_points"),
            ),
            "Quality & fundamentals": (
                ("gross_margin", "gross margin", "pct_points"),
                ("free_cash_flow_margin_ttm", "FCF margin", "pct_points"),
                ("net_debt_to_ebitda_fy", "net debt/EBITDA", "plain"),
                ("altman_z_score_ttm", "Altman Z", "plain"),
            ),
            "Tape & technicals": (
                ("RSI", "RSI", "pct_points"),
                ("Recommend.All", "technical rating", "trend_dir"),
                ("ATRP", "ATR%", "pct_points"),
                ("relative_volume_10d_calc", "rel volume", "ratio"),
            ),
        }
        for group_name, columns in HISTORY_COLUMN_GROUPS:
            group_columns = [
                (column, label, fmt)
                for column, label, fmt in columns
                if any(
                    (row.get("metrics") or {}).get(column) is not None
                    for row in history_rows
                )
            ]
            if group_name == "Tape & technicals":
                for derived_key, derived_label, derived_fmt in HISTORY_DERIVED_COLUMNS:
                    if any(
                        (row.get("metrics") or {}).get(derived_key) is not None
                        for row in history_rows
                    ):
                        group_columns.append((derived_key, derived_label, derived_fmt))
            if not group_columns:
                continue
            write_line(f"  [{group_name}] (weekly all-fields scans)")
            header = f"  {'Week':<9} {'Scan':<10}"
            for _column, label, _fmt in group_columns:
                header += f" {label:>9}"
            write_line(header)
            for row in history_rows:
                metrics = row.get("metrics") or {}
                if not row.get("found"):
                    write_line(
                        f"  {row['week']:<9} {row['scan_date']:<10}  (not in this scan)"
                    )
                    continue
                line = f"  {row['week']:<9} {row['scan_date']:<10}"
                for column, _label, fmt in group_columns:
                    line += f" {_fmt_value(metrics.get(column), fmt):>9}"
                write_line(line)
            trend = _history_group_trend(
                history_rows, group_trends.get(group_name) or ()
            )
            if trend:
                for wrapped in _wrap_text("  Trend: " + trend):
                    write_line(wrapped)
            write_line("")
    else:
        write_line("  No historical all-fields scans available.")
        write_line("")

    if prediction_history:
        write_line("  [Model scoring] (weekly move-prediction databases)")
        write_line(
            f"  {'Week':<10} {'Run':<10} {'Days':>7} {'Weeks':>7} {'Months':>7} "
            f"{'Years':>7} {'ConfW':>6} {'AgreeW':>6} {'Conv':>6}  {'Sleeve':<7} Entry"
        )
        for entry in prediction_history:
            consensus_by_horizon = {
                str(row.get("horizon_name") or ""): row
                for row in (entry.get("consensus") or [])
            }

            def _cell(horizon: str) -> str:
                row = consensus_by_horizon.get(horizon)
                if not row:
                    return "-"
                score = _to_float(row.get("score"))
                if score is None:
                    return "-"
                return f"{score:.2f}{_direction_marker(row.get('direction'))}"

            weeks_row = consensus_by_horizon.get("weeks") or {}
            weeks_conf = _to_float(weeks_row.get("confidence"))
            weeks_agree = _to_float(weeks_row.get("agreement_ratio"))
            conviction_row = (entry.get("conviction") or [{}])[0]
            conv_score = _to_float(conviction_row.get("conviction_score"))
            write_line(
                f"  {entry['week']:<10} {str(entry.get('run_created_at_utc') or '')[:10]:<10} "
                f"{_cell('days'):>7} {_cell('weeks'):>7} {_cell('months'):>7} "
                f"{_cell('years'):>7} "
                f"{(f'{weeks_conf:.0f}%' if weeks_conf is not None else '-'):>6} "
                f"{(f'{weeks_agree:.0%}' if weeks_agree is not None else '-'):>6} "
                f"{(f'{conv_score:.2f}' if conv_score is not None else '-'):>6}  "
                f"{str(conviction_row.get('sleeve') or '-'):<7} "
                f"{conviction_row.get('entry_readiness') or '-'}"
            )
        scoring_trend = _scoring_trend_read(prediction_history)
        if scoring_trend:
            for wrapped in _wrap_text("  Trend: " + scoring_trend):
                write_line(wrapped)
        write_line("")

    # ---------------------------------- 5) forward projections & growth support
    write_line("-" * 100)
    write_line(
        "5) FORWARD PROJECTIONS & GROWTH SUPPORT (projection suite x street consensus)"
    )
    write_line("-" * 100)
    for projection_line in _projection_log_lines(result):
        write_line(projection_line)

    # --------------------------------------------------- 6) move-prediction suite
    write_line("-" * 100)
    write_line("6) MOVE-PREDICTION MODEL SUITE (weekly)")
    write_line("-" * 100)
    if move_prediction:
        if not (
            move_prediction.get("is_latest_database")
            and move_prediction.get("is_latest_run")
        ):
            write_line(
                f"  Source: run {move_prediction.get('source_run_id')} in "
                f"{Path(str(move_prediction.get('source_database') or '')).name} "
                "(latest-available fallback - the newest run lacks the symbol)."
            )
        conviction_rows = move_prediction.get("conviction_rankings") or []
        for row in conviction_rows:
            score = _to_float(row.get("conviction_score"))
            write_line(
                f"  Conviction: sleeve={row.get('sleeve')} "
                f"score={f'{score:.2f}' if score is not None else 'n/a'} "
                f"rank #{row.get('rank_overall')} overall / "
                f"#{row.get('rank_in_sleeve')} in sleeve | "
                f"entry={row.get('entry_readiness')} "
                f"signal={row.get('manager_action_signal')} "
                f"tier={row.get('breakout_conviction_tier')}"
            )
        consensus_rows = move_prediction.get("consensus_horizon_scores") or []
        if consensus_rows:
            write_line("")
            write_line("  Consensus (all profiles blended) by horizon:")
            for row in sorted(
                consensus_rows, key=lambda r: _horizon_sort_key(r.get("horizon_name"))
            ):
                score = _to_float(row.get("score"))
                confidence = _to_float(row.get("confidence"))
                agreement = _to_float(row.get("agreement_ratio"))
                write_line(
                    f"    {str(row.get('horizon_name') or ''):<8} "
                    f"{str(row.get('direction') or ''):<5} "
                    f"score={f'{score:.2f}' if score is not None else 'n/a'} "
                    f"conf={f'{confidence:.0f}%' if confidence is not None else 'n/a'} "
                    f"agree={f'{agreement:.0%}' if agreement is not None else 'n/a'} "
                    f"risk={row.get('risk_tier')} signal={row.get('manager_action_signal')}"
                )
        profile_rows = move_prediction.get("profile_horizon_scores") or []
        if profile_rows:
            write_line("")
            write_line("  Profile score matrix (score + direction by horizon):")
            profiles: dict[str, dict[str, Any]] = {}
            horizons_seen: list[str] = []
            for row in profile_rows:
                profile_name = str(row.get("profile_name") or "")
                horizon_name = str(row.get("horizon_name") or "")
                if horizon_name not in horizons_seen:
                    horizons_seen.append(horizon_name)
                profile_entry = profiles.setdefault(
                    profile_name, {"horizons": {}, "setup": "", "risk_tier": ""}
                )
                profile_entry["horizons"][horizon_name] = (
                    _to_float(row.get("score")),
                    row.get("direction"),
                )
                if horizon_name == "weeks" or not profile_entry["setup"]:
                    profile_entry["setup"] = str(row.get("setup") or "")
                    profile_entry["risk_tier"] = str(row.get("risk_tier") or "")
            horizons_seen.sort(key=_horizon_sort_key)
            header = f"    {'profile':<30}"
            for horizon in horizons_seen:
                header += f" {horizon:>7}"
            header += "  setup / risk tier"
            write_line(header)
            for profile_name in sorted(profiles):
                info = profiles[profile_name]
                line = f"    {profile_name:<30}"
                for horizon in horizons_seen:
                    cell = info["horizons"].get(horizon)
                    if cell and cell[0] is not None:
                        line += f" {f'{cell[0]:.2f}{_direction_marker(cell[1])}':>7}"
                    else:
                        line += f" {'-':>7}"
                line += f"  {info['setup']} / {info['risk_tier']}"
                write_line(line)
    else:
        write_line("  Not available in the latest weekly move-prediction database.")
    write_line("")
    write_line("  (Weekly model-scoring progression lives in section 4.)")
    write_line("")

    # ------------------------------------------------------ 7) edge-research lenses
    write_line("-" * 100)
    write_line("7) EDGE-RESEARCH LENSES (key fields only - full rows in the JSON)")
    write_line("-" * 100)
    if edge_research:
        for label, entry in edge_research.items():
            fallback_note = ""
            if entry.get("row") and entry.get("source_is_latest") is False:
                fallback_note = (
                    f" | row from older run "
                    f"{Path(str(entry.get('source_run_dir') or '')).name}"
                )
            write_line(
                f"  [{label}] population={entry.get('population', 'n/a')} - "
                f"{entry.get('notes', '')}{fallback_note}"
            )
            row = entry.get("row")
            if row:
                key_fields = _edge_key_fields(
                    row, entry.get("priority_prefixes") or ()
                )
                if key_fields:
                    for key, value in key_fields:
                        write_line(f"    {key} = {value}")
                    write_line(
                        f"    ... ({len(row) - len(key_fields) - 1} more fields in JSON)"
                    )
                else:
                    write_line("    (present, but no key rank/score fields)")
            elif entry.get("error"):
                write_line(f"    (error reading source: {entry['error']})")
            else:
                write_line("    (symbol not present in this dataset)")
            write_line("")
    else:
        write_line("  No edge-research parent run outputs were found.")
        write_line("")

    # --------------------------------------- 8) entry / upside / downside / risk
    write_line("-" * 100)
    write_line("8) ENTRY / UPSIDE / DOWNSIDE / RISK")
    write_line("-" * 100)
    sections = result.get("entry_upside_downside_risk") or {}
    for section_key, section_title in (
        ("upside", "UPSIDE"),
        ("downside_risk", "DOWNSIDE / RISK"),
        ("entry", "ENTRY"),
    ):
        items = sections.get(section_key) or []
        write_line(f"  {section_title}")
        if items:
            for item in items:
                for index, wrapped in enumerate(textwrap.wrap(item, width=102)):
                    write_line(("    - " if index == 0 else "      ") + wrapped)
        else:
            write_line("    (no data)")
        write_line("")

    write_line("=" * 100)
    write_line(
        "Raw field catalog (all_buckets), complete lens rows, and every intermediate "
        "value used above:"
    )
    write_line(f"  JSON sidecar: {json_path.name}")
    write_line("=" * 100)

    for line in lines:
        log_to_file(log_path, line)

    json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return log_path, json_path


def _history_group_trend(
    history_rows: list[dict[str, Any]],
    specs: Sequence[tuple[str, str, str]],
) -> str | None:
    """First-vs-last delta summary for one metric group across the weekly progression.

    Modes: ``ratio`` (x -> y with % change), ``pct_points`` (point delta), ``plain``
    (x -> y), ``rating`` (appends de-rating/re-rating), ``trend_dir`` (appends
    improving/deteriorating).
    """
    found_rows = [row for row in history_rows if row.get("found")]
    if len(found_rows) < 2 or not specs:
        return None
    first, last = found_rows[0], found_rows[-1]
    parts: list[str] = []
    for column, label, mode in specs:
        first_value = _to_float(first["metrics"].get(column))
        last_value = _to_float(last["metrics"].get(column))
        if first_value is None or last_value is None:
            continue
        if mode == "pct_points":
            parts.append(
                f"{label} {first_value:.1f} -> {last_value:.1f} "
                f"({last_value - first_value:+.1f} pts)"
            )
        elif mode == "rating":
            direction = "de-rating" if last_value < first_value else "re-rating"
            parts.append(f"{label} {first_value:.1f} -> {last_value:.1f} ({direction})")
        elif mode == "trend_dir":
            direction = "improving" if last_value > first_value else "deteriorating"
            parts.append(
                f"{label} {first_value:+.2f} -> {last_value:+.2f} ({direction})"
            )
        elif mode == "plain":
            parts.append(f"{label} {first_value:.2f} -> {last_value:.2f}")
        else:
            change_pct = (
                (last_value / first_value - 1) * 100 if first_value else None
            )
            suffix = f" ({change_pct:+.1f}%)" if change_pct is not None else ""
            parts.append(f"{label} {first_value:.2f} -> {last_value:.2f}{suffix}")
    if not parts:
        return None
    return (
        f"over {len(found_rows)} weekly scans "
        f"({first['scan_date']} -> {last['scan_date']}): " + "; ".join(parts) + "."
    )


def _scoring_trend_read(prediction_history: list[dict[str, Any]]) -> str | None:
    """First-vs-last summary of the weekly model-scoring progression."""
    if len(prediction_history) < 2:
        return None

    def _weeks_consensus(entry: dict[str, Any]) -> dict[str, Any]:
        for row in entry.get("consensus") or []:
            if str(row.get("horizon_name") or "") == "weeks":
                return row
        return {}

    first, last = prediction_history[0], prediction_history[-1]
    parts: list[str] = []
    first_weeks = _weeks_consensus(first)
    last_weeks = _weeks_consensus(last)
    first_score = _to_float(first_weeks.get("score"))
    last_score = _to_float(last_weeks.get("score"))
    if first_score is not None and last_score is not None:
        direction = "strengthening" if last_score > first_score else "fading"
        parts.append(
            f"weeks-horizon consensus {first_score:.2f}"
            f"{_direction_marker(first_weeks.get('direction'))} -> "
            f"{last_score:.2f}{_direction_marker(last_weeks.get('direction'))} "
            f"({direction})"
        )
    entry_states = [
        str((entry.get("conviction") or [{}])[0].get("entry_readiness") or "none")
        for entry in prediction_history
    ]
    ready_weeks = sum(1 for state in entry_states if state in ("ready", "ready_now"))
    parts.append(
        f"entry readiness: {entry_states[0]} -> {entry_states[-1]} "
        f"(actionable in {ready_weeks}/{len(entry_states)} weeks)"
    )
    sleeves = {
        str((entry.get("conviction") or [{}])[0].get("sleeve") or "")
        for entry in prediction_history
    } - {""}
    if len(sleeves) > 1:
        parts.append(f"sleeve flipped across weeks ({', '.join(sorted(sleeves))})")
    if not parts:
        return None
    return (
        f"over {len(prediction_history)} weekly model runs "
        f"({first['week']} -> {last['week']}): " + "; ".join(parts) + "."
    )


def _projection_tracking_trend(
    growth_progression: list[dict[str, Any]],
    price_progression: list[dict[str, Any]],
) -> str | None:
    """First-vs-last drift summary across projection runs (per suite)."""
    parts: list[str] = []
    found_growth = [
        entry
        for entry in growth_progression
        if entry.get("found") and (entry.get("lanes") or {}).get("own")
    ]
    if len(found_growth) >= 2:
        first, last = found_growth[0], found_growth[-1]
        first_cagr = _to_float(first["lanes"]["own"].get("revenue_cagr_implied_pct"))
        last_cagr = _to_float(last["lanes"]["own"].get("revenue_cagr_implied_pct"))
        first_rev = _to_float(first["lanes"]["own"].get("terminal_total_revenue_ttm"))
        last_rev = _to_float(last["lanes"]["own"].get("terminal_total_revenue_ttm"))
        if first_cagr is not None and last_cagr is not None:
            drift = last_cagr - first_cagr
            stability = (
                "stable"
                if abs(drift) < 0.5
                else ("upgrading" if drift > 0 else "downgrading")
            )
            parts.append(
                f"own-lane growth projection {stability} across {len(found_growth)} "
                f"growth runs ({first.get('day_label')} -> {last.get('day_label')}): "
                f"rev CAGR {first_cagr:+.1f}% -> {last_cagr:+.1f}%"
                + (
                    f", terminal revenue {_fmt_money(first_rev)} -> {_fmt_money(last_rev)}"
                    if first_rev and last_rev
                    else ""
                )
            )
    found_price = [
        entry for entry in price_progression if entry.get("found") and entry.get("base")
    ]
    if len(found_price) >= 2:
        first, last = found_price[0], found_price[-1]
        first_up = _to_float(first["base"].get("primary_upside_pct"))
        last_up = _to_float(last["base"].get("primary_upside_pct"))
        if first_up is not None and last_up is not None:
            direction = "expanding" if last_up > first_up else "compressing"
            parts.append(
                f"primary upside {direction}: {first_up:+.1f}% -> {last_up:+.1f}% "
                f"across {len(found_price)} price runs "
                f"({first.get('day_label')} -> {last.get('day_label')})"
            )
    elif price_progression and len(found_price) <= 1:
        parts.append(
            "price-suite coverage is thin (symbol in at most one run so far) - "
            "tracking becomes meaningful from the next price run"
        )
    if not parts:
        return None
    return "; ".join(parts) + "."


def _projection_log_lines(result: dict[str, Any]) -> list[str]:
    """Section 5: street forward guidance, growth lanes, projected year paths,
    EV/Rev price projection, the growth-support assessment, and cross-run tracking."""
    lines: list[str] = []
    guidance = result.get("forward_guidance") or {}
    projection = result.get("financial_projection") or {}
    growth = projection.get("growth") or {}
    price = projection.get("price") or {}
    assessment = projection.get("growth_support_assessment") or {}
    snapshot = result.get("snapshot_metrics") or {}

    if not guidance and not growth and not price:
        lines.append("  No financial-projection runs or forward guidance available.")
        lines.append("")
        return lines

    lines.append(
        f"  Coverage: growth suite {growth.get('runs_available') or 0} run(s), "
        f"price suite {price.get('runs_available') or 0} run(s); street guidance from "
        "the latest all-fields scan. Full projection rows + year grids are in the JSON."
    )
    lines.append("")

    # ------------------------------------------------ street forward guidance
    g_fields = guidance.get("fields") or {}
    g_derived = guidance.get("derived") or {}
    if g_fields:
        scan_date = str(
            (result.get("all_fields_run_context") or {}).get("created_at_utc") or ""
        )[:10]
        lines.append(
            f"  [Street forward guidance] (analyst consensus - all-fields scan {scan_date})"
        )
        rating = (g_fields.get("AnalystRating") or {}).get("display")
        target_bits = []
        for column, short in (
            ("price_target_low", "low"),
            ("price_target_median", "median"),
            ("price_target_average", "avg"),
            ("price_target_high", "high"),
        ):
            entry = g_fields.get(column)
            if not entry:
                continue
            upside = g_derived.get(f"{column}_upside_pct")
            target_bits.append(
                f"{short} {entry['display']}"
                + (f" ({upside:+.1f}%)" if upside is not None else "")
            )
        header_bits = []
        if rating:
            header_bits.append(f"rating {rating}")
        if target_bits:
            header_bits.append("targets " + " / ".join(target_bits))
        if header_bits:
            lines.append("    " + " | ".join(header_bits))

        rev_ttm = (g_fields.get("total_revenue_ttm") or {}).get("display")
        fq_fc = (g_fields.get("revenue_forecast_next_fq") or {}).get("display")
        fy_fc = (g_fields.get("revenue_forecast_next_fy") or {}).get("display")
        fq_gr = g_derived.get("street_next_fq_revenue_growth_pct")
        fy_gr = g_derived.get("street_next_fy_revenue_growth_pct")
        if rev_ttm or fy_fc:
            line = f"    Revenue: TTM {rev_ttm or 'n/a'}"
            if fq_fc:
                line += f" | next-FQ fcst {fq_fc}"
                if fq_gr is not None:
                    line += f" ({fq_gr:+.1f}% vs last FQ)"
            if fy_fc:
                line += f" | next-FY fcst {fy_fc}"
                if fy_gr is not None:
                    line += f" (street-implied {fy_gr:+.1f}%)"
            lines.append(line)

        eps_ttm = (g_fields.get("earnings_per_share_diluted_ttm") or {}).get("display")
        eps_fy = (g_fields.get("earnings_per_share_forecast_next_fy") or {}).get(
            "display"
        )
        eps_gr = g_derived.get("street_next_fy_eps_growth_pct")
        fwd_pe = (
            g_fields.get("non_gaap_price_to_earnings_per_share_forecast_next_fy") or {}
        ).get("display") or (g_fields.get("price_earnings_forward_fy") or {}).get(
            "display"
        )
        ttm_pe = (snapshot.get("price_earnings_ttm") or {}).get("display")
        if eps_ttm or eps_fy:
            line = f"    EPS: TTM {eps_ttm or 'n/a'}"
            if eps_fy:
                line += f" | next-FY fcst {eps_fy}"
                if eps_gr is not None:
                    line += f" (street-implied {eps_gr:+.1f}%)"
            if fwd_pe:
                line += f" | forward P/E {fwd_pe}"
            if ttm_pe:
                line += f" vs TTM P/E {ttm_pe}"
            lines.append(line)

        extra_bits = []
        next_earnings = (g_fields.get("earnings_release_next_date") or {}).get("display")
        if next_earnings:
            extra_bits.append(f"next earnings {next_earnings}")
        sgr = (g_fields.get("sustainable_growth_rate_ttm") or {}).get("display")
        if sgr:
            extra_bits.append(f"sustainable growth rate {sgr}")
        if extra_bits:
            lines.append("    " + " | ".join(extra_bits))
        lines.append("")

    # ------------------------------------------------ growth lanes (latest run)
    growth_rows = growth.get("summary_rows") or []
    if growth_rows:
        detail_growth = growth.get("detail_run") or growth.get("latest_run") or {}
        growth_fallback_note = ""
        if detail_growth and not detail_growth.get("is_latest", True):
            growth_fallback_note = (
                f" - latest available with symbol; newer run "
                f"{(growth.get('latest_run') or {}).get('run_id', '')} lacks it"
            )
        lines.append(
            f"  [Growth lanes - base scenario] (5y revenue/EBITDA/margin paths; "
            f"run {detail_growth.get('run_id', '')}{growth_fallback_note})"
        )
        lines.append(
            f"    {'Lane':<9} {'Y1 rev gr':>9} {'5y rev CAGR':>12} {'5y EBITDA CAGR':>15} "
            f"{'Term rev':>9} {'Term EBITDA m':>14}  Source"
        )
        for lane in GROWTH_LANE_ORDER:
            row = _pick_row(growth_rows, lane=lane)
            if not row:
                continue
            lines.append(
                f"    {lane:<9} {_pct_opt(row.get('y1_revenue_growth_pct')):>9} "
                f"{_pct_opt(row.get('revenue_cagr_implied_pct')):>12} "
                f"{_pct_opt(row.get('ebitda_cagr_implied_pct')):>15} "
                f"{_money_opt(row.get('terminal_total_revenue_ttm')):>9} "
                f"{_frac_pct_opt(row.get('terminal_ebitda_margin_ttm')):>14}  "
                f"{row.get('growth_lane_source') or ''}"
            )
        band_bits = []
        for scenario in SCENARIO_ORDER:
            row = _pick_row(growth_rows, scenario=scenario, lane="own")
            if row:
                band_bits.append(
                    f"{scenario} {_pct_opt(row.get('revenue_cagr_implied_pct'))}"
                )
        if len(band_bits) > 1:
            lines.append("    Own-lane rev CAGR band: " + " / ".join(band_bits))
        priors = growth.get("universe_priors") or {}
        universe_prior = _to_float(priors.get("universe_growth_fraction"))
        if universe_prior is not None:
            lines.append(
                f"    Universe absolute prior this run: {universe_prior * 100:.1f}% "
                "revenue growth (median of peer medians)."
            )
        lines.append("")

    year_rows = [
        row
        for row in (growth.get("year_grid_rows") or [])
        if str(row.get("growth_lane")) == "own" and str(row.get("scenario")) == "base"
    ]
    if year_rows:
        lines.append(
            "  [Projected fundamentals path - own lane, base] "
            "(the growth the model actually embeds)"
        )
        lines.append(
            f"    {'Year':<5} {'Revenue':>9} {'gr%':>7} {'EBITDA':>9} {'EBIT':>9} "
            f"{'Net':>9} {'EBITDA%':>8} {'OperM%':>7} {'NetM%':>7}"
        )
        for row in sorted(year_rows, key=lambda r: _to_float(r.get("year")) or 0):
            year = int(_to_float(row.get("year")) or 0)
            growth_fraction = _to_float(row.get("growth_fraction"))
            growth_cell = (
                f"{growth_fraction * 100:+.1f}%"
                if year > 0 and growth_fraction is not None
                else "-"
            )
            lines.append(
                f"    {f'Y{year}':<5} {_money_opt(row.get('total_revenue_ttm')):>9} "
                f"{growth_cell:>7} {_money_opt(row.get('ebitda')):>9} "
                f"{_money_opt(row.get('ebit_ttm')):>9} "
                f"{_money_opt(row.get('net_income_ttm')):>9} "
                f"{_frac_pct_opt(row.get('ebitda_margin_ttm')):>8} "
                f"{_frac_pct_opt(row.get('operating_margin_ttm')):>7} "
                f"{_frac_pct_opt(row.get('net_margin_ttm')):>7}"
            )
        lines.append("")

    # ------------------------------------------------ price projection (latest run)
    price_rows = price.get("summary_rows") or []
    base_price_row = _pick_row(price_rows)
    if base_price_row:
        detail_price = price.get("detail_run") or price.get("latest_run") or {}
        price_fallback_note = ""
        if detail_price and not detail_price.get("is_latest", True):
            price_fallback_note = (
                f" - latest available with symbol; newer run "
                f"{(price.get('latest_run') or {}).get('run_id', '')} lacks it"
            )
        lines.append(
            f"  [Price projection - EV/Rev suite] (run {detail_price.get('run_id', '')}"
            f"{price_fallback_note})"
        )
        lines.append(
            f"    Primary upside {_pct_opt(base_price_row.get('primary_upside_pct'))} "
            f"(source {base_price_row.get('primary_upside_source')}) | terminal price "
            f"{_price_opt(base_price_row.get('terminal_price'))} (terminal upside "
            f"{_pct_opt(base_price_row.get('terminal_upside_pct'))}) | implied price "
            f"CAGR {_pct_opt(base_price_row.get('implied_price_cagr_pct'))}"
        )
        lines.append(
            f"    Y1 model price {_price_opt(base_price_row.get('model_y1_price'))} "
            f"({_pct_opt(base_price_row.get('model_y1_upside_pct'))}) | EV/EBITDA "
            f"crosscheck {_pct_opt(base_price_row.get('ev_ebitda_crosscheck_upside_pct'))} "
            f"(px {_price_opt(base_price_row.get('ev_ebitda_crosscheck_price_t5'))})"
        )
        peer_trust = _to_float(base_price_row.get("lane_peer_trust"))
        hist_trust = _to_float(base_price_row.get("lane_hist_trust"))
        trust_bits = []
        if peer_trust is not None:
            trust_bits.append(f"peer trust {peer_trust:.2f}")
        if hist_trust is not None:
            trust_bits.append(f"hist trust {hist_trust:.2f}")
        lines.append(
            f"    Lanes: core {_pct_opt(base_price_row.get('lane_core_upside_pct'))} | "
            f"street {_pct_opt(base_price_row.get('lane_street_upside_pct'))} | "
            f"history-adjusted "
            f"{_pct_opt(base_price_row.get('lane_hist_adjusted_upside_pct'))}"
            + (" | " + ", ".join(trust_bits) if trust_bits else "")
        )
        flags = str(base_price_row.get("decision_flags") or "").replace("|", ", ")
        lines.append(
            f"    Valuation lens {base_price_row.get('valuation_lens') or 'n/a'} | "
            f"street outlook {base_price_row.get('st_outlook') or 'n/a'} | "
            f"flags: {flags or 'none'} | rank-eligible "
            f"{base_price_row.get('rank_eligible')}"
        )
        band_bits = []
        for scenario in SCENARIO_ORDER:
            row = _pick_row(price_rows, scenario=scenario)
            if row:
                band_bits.append(
                    f"{scenario} {_pct_opt(row.get('primary_upside_pct'))} "
                    f"(px {_price_opt(row.get('terminal_price'))})"
                )
        width = _to_float(base_price_row.get("scenario_width_y5_pp"))
        if band_bits:
            band_line = "    Scenario band: " + " / ".join(band_bits)
            if width is not None:
                band_line += (
                    f" - width {width:.0f} pp (wide)"
                    if width >= 50
                    else f" - width {width:.0f} pp"
                )
            lines.append(band_line)
        lines.append("")

    price_year_rows = [
        row
        for row in (price.get("year_grid_rows") or [])
        if str(row.get("scenario")) == "base"
    ]
    if price_year_rows:
        lines.append(
            "  [Projected price path - base scenario] (EV/Rev -> EV -> equity -> price)"
        )
        lines.append(
            f"    {'Year':<5} {'EV/Rev':>7} {'EV':>9} {'Equity':>9} {'Price':>9} "
            f"{'Upside':>8}"
        )
        for row in sorted(
            price_year_rows, key=lambda r: _to_float(r.get("year")) or 0
        ):
            year = int(_to_float(row.get("year")) or 0)
            ev_rev = _to_float(row.get("enterprise_value_to_revenue_ttm"))
            upside = _to_float(row.get("upside_pct"))
            lines.append(
                f"    {f'Y{year}':<5} "
                f"{(f'{ev_rev:.2f}x' if ev_rev is not None else '-'):>7} "
                f"{_money_opt(row.get('enterprise_value_current')):>9} "
                f"{_money_opt(row.get('equity_value')):>9} "
                f"{_price_opt(row.get('price')):>9} "
                f"{(f'{upside:+.1f}%' if upside is not None else '-'):>8}"
            )
        current_mult = _to_float(
            base_price_row.get("enterprise_value_to_revenue_ttm")
        )
        peer_mult = _to_float(base_price_row.get("ev_rev_peer_median"))
        if current_mult is not None:
            lines.append(
                f"    Y0 is the model's blended start anchor (current EV/Rev "
                f"{current_mult:.2f}x blended toward the peer median "
                f"{peer_mult:.2f}x), not the spot price; upside is vs the scan close."
                if peer_mult is not None
                else "    Y0 is the model's blended start anchor, not the spot price; "
                "upside is vs the scan close."
            )
        lines.append("")

    # ------------------------------------------------ assessment + tracking
    checks = assessment.get("checks") or []
    if checks:
        lines.append("  [Growth needed vs projected - assessment]")
        for check in checks:
            summary = check.get("summary")
            if not summary:
                continue
            for wrapped in _wrap_text("- " + summary, indent="    "):
                lines.append("  " + wrapped)
        lines.append("")

    growth_progression = growth.get("run_progression") or []
    price_progression = price.get("run_progression") or []
    if growth_progression or price_progression:
        lines.append("  [Projection tracking across runs]")
        if growth_progression:
            lines.append(
                f"    {'Growth run':<12} {'own CAGR':>9} {'peer CAGR':>10} "
                f"{'univ CAGR':>10} {'term rev':>9}  own source"
            )
            for entry in growth_progression:
                label = entry.get("day_label") or entry.get("run_id") or ""
                if not entry.get("found"):
                    lines.append(f"    {label:<12}  (symbol not in this run)")
                    continue
                lanes = entry.get("lanes") or {}
                own = lanes.get("own") or {}
                peer = lanes.get("peer") or {}
                universe = lanes.get("universe") or {}
                lines.append(
                    f"    {label:<12} "
                    f"{_pct_opt(own.get('revenue_cagr_implied_pct')):>9} "
                    f"{_pct_opt(peer.get('revenue_cagr_implied_pct')):>10} "
                    f"{_pct_opt(universe.get('revenue_cagr_implied_pct')):>10} "
                    f"{_money_opt(own.get('terminal_total_revenue_ttm')):>9}  "
                    f"{own.get('growth_lane_source') or ''}"
                )
        if price_progression:
            lines.append(
                f"    {'Price run':<12} {'primary':>9} {'terminal px':>12} "
                f"{'impl CAGR':>10} {'core':>9} {'street':>9}"
            )
            for entry in price_progression:
                label = entry.get("day_label") or entry.get("run_id") or ""
                if not entry.get("found"):
                    lines.append(f"    {label:<12}  (symbol not in this run)")
                    continue
                row = entry.get("base") or {}
                lines.append(
                    f"    {label:<12} {_pct_opt(row.get('primary_upside_pct')):>9} "
                    f"{_price_opt(row.get('terminal_price')):>12} "
                    f"{_pct_opt(row.get('implied_price_cagr_pct')):>10} "
                    f"{_pct_opt(row.get('lane_core_upside_pct')):>9} "
                    f"{_pct_opt(row.get('lane_street_upside_pct')):>9}"
                )
        trend = _projection_tracking_trend(growth_progression, price_progression)
        if trend:
            for wrapped in _wrap_text("Trend: " + trend, indent="    "):
                lines.append("  " + wrapped)
        lines.append("")

    return lines


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a comprehensive single-symbol intelligence report across all "
            "TradingView-based data flows (all-fields, move-prediction, edge-research)."
        )
    )
    parser.add_argument(
        "--symbol", required=True, help="Symbol to inspect, e.g. NASDAQ:ABUS"
    )
    parser.add_argument("--all-fields-root", type=Path, default=None)
    parser.add_argument("--prediction-root", type=Path, default=None)
    parser.add_argument("--edge-research-output-root", type=Path, default=None)
    parser.add_argument(
        "--projection-root",
        type=Path,
        default=None,
        help=(
            "Financial-projection output root holding fingrowth_*/finproj_* runs "
            f"(default: {DEFAULT_PROJECTION_ROOT.as_posix()})"
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument(
        "--history-weeks",
        type=int,
        default=DEFAULT_HISTORY_WEEKS,
        help=(
            "Number of ISO weeks of all-fields history to include "
            f"(default {DEFAULT_HISTORY_WEEKS}, i.e. effectively the entire dataset)."
        ),
    )
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()
    result = build_symbol_intelligence_report(
        args.symbol,
        all_fields_root=args.all_fields_root,
        prediction_root=args.prediction_root,
        edge_research_output_root=args.edge_research_output_root,
        projection_root=args.projection_root,
        output_dir=args.output_dir,
        history_weeks=args.history_weeks,
    )
    print(f"Matched symbol : {result['matched_symbol']}")
    print(f"Log written to : {result['log_path']}")
    print(f"JSON written to: {result['json_path']}")
    if result.get("warnings"):
        print("Warnings:")
        for warning in result["warnings"]:
            print(f"  - {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# === AI GENERATED CODE END ===
