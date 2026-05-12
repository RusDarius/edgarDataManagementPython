"""Forward target estimation engine for TradingView-sourced equity data.

This module builds on the same data pipeline used by the move-prediction
analysis but shifts the objective from directional scoring to concrete
forward price and valuation targets expressed as bear / base / bull ranges.

Seven independent estimation lenses are blended per horizon:

=================  ============================================================
Lens               What it captures
=================  ============================================================
multiple           Peer-relative valuation reversion across ten standard
                   price-to-fundamental and EV-to-fundamental multiples
                   (P/E, P/S, P/B, P/FCF, P/OCF, EV/Revenue, EV/EBIT,
                   EV/EBITDA, EV/FCF, EV/GP).
technical          Tape-driven envelope from moving averages, VWAP/VWMA,
                   Bollinger Bands, classic + Camarilla pivots, and multi-
                   period high/low ranges.
trajectory         Forward fundamentals: project EPS / revenue / EBITDA
                   using growth rates capped by the company's sustainable
                   growth rate (ROE × retention proxy), then apply current
                   or peer multiples and discount via a CAPM-style cost of
                   equity for long horizons.
range              Mean-reversion anchor inside the 6-month and 52-week
                   range — caps overextended momentum names, pulls oversold
                   names back toward the midpoint.
analyst            Sell-side consensus: ``price_target_low/median/high``
                   used as bear/base/bull anchors directly. The
                   ``AnalystRating`` (Buy/Hold/Sell) tilts confidence.
book_value         Graham-style fair value: ``√(22.5 × EPS × BVPS)`` plus
                   peer-median P/B reversion. Especially informative for
                   financials, REITs, and deep-value names.
yield_dcf          Gordon dividend discount model + shareholder-yield
                   reversion for income-paying names. Inactive when the
                   security pays no dividend.
=================  ============================================================

Within each lens the per-anchor estimates are aggregated by their
confidence weights, then the seven lens prices are weighted-blended into a
bear / base / bull target. Scenario bands tilt with prediction-module
component scores (quality + safety → tighter bear band; momentum + trend
→ wider bull band; high lens dispersion → wider both ways) and with
fundamental quality flags (Piotroski / Altman Z / leverage gates).

A full per-lens contribution table is logged so each blended target can
be decomposed into its source evidence — every weight in the final
forecast is observable.

The module also writes a companion ``__lens_cases.csv`` export.  That file
contains every raw target anchor by lens, horizon, input field snapshot,
formula summary, raw implied price, blend-used price, lens-fit tags, and
active-management signal.  Upside outliers may be capped only inside the
blend; downside anchors are preserved raw so adverse cases remain visible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Mapping

from data_analysis_scripts._shared_analysis_utils import (
    REPORT_TIMESTAMP_FORMAT,
    build_report_title as _build_report_title,
    coerce_numeric as _coerce_numeric,
    format_market_cap as _format_market_cap,
    median_absolute_deviation as _median_absolute_deviation,
    reset_log_file as _reset_log_file,
    safe_ratio as _safe_ratio,
    slugify as _slugify,
    sort_key_desc as _sort_key_desc,
)
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    COMPONENT_LABELS,
    COMPONENT_ORDER,
    ENTRY_METADATA_FIELDS,
    RAW_PROFILE_FIELDS,
    DERIVED_PROFILE_FIELDS,
    HORIZON_TITLES,
    ScoringProfile,
    resolve_move_prediction_scoring_profile,
    _build_derived_metrics,
    _build_metric_profiles,
    _build_component_scores,
    _enrich_with_peer_metrics,
    _get_symbol_name,
    _get_company_name,
    _get_company_description,
    _resolve_horizon_weights,
    _build_horizon_prediction,
    _clamp,
    _normalize_industries,
    _parse_event_days,
)
from generic_utils.log_to_files_util import log_to_file, log_rows_to_csv
from data_loaders.api_tradingview_client import ApiTradingViewClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_DIR = Path(
    r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\targets_analysis"
)

TOP_SECTION_ROWS = 30

# Valuation multiples used for the multiple-anchored lens.  Each entry maps
# the TradingView field name to a short display label.  Logic:
#   For both price-based (P/E, P/S, ...) and EV-based (EV/Revenue, ...)
#   multiples we use a ratio simplification:
#       implied_price = current_price * (peer_median_multiple / company_multiple)
#   This is valid when the scan universe is uniform enough that capital-
#   structure differences are second-order, which is the design assumption
#   of an industry-scoped scan.
MULTIPLE_FIELDS: list[tuple[str, str]] = [
    ("price_earnings_ttm", "P/E TTM"),
    ("price_sales_current", "P/S"),
    ("price_book_fq", "P/B"),
    ("price_free_cash_flow_ttm", "P/FCF"),
    ("price_to_cash_f_operating_activities_ttm", "P/OCF"),
    ("enterprise_value_to_revenue_ttm", "EV/Revenue"),
    ("enterprise_value_to_ebit_ttm", "EV/EBIT"),
    ("enterprise_value_ebitda_ttm", "EV/EBITDA"),
    ("enterprise_value_to_free_cash_flow_ttm", "EV/FCF"),
    ("enterprise_value_to_gross_profit_ttm", "EV/GP"),
]

# Growth fields used for fundamental-trajectory projection.
GROWTH_PROJECTION_FIELDS: list[tuple[str, str, str]] = [
    ("total_revenue_yoy_growth_ttm", "total_revenue", "Revenue YoY"),
    ("ebitda_yoy_growth_ttm", "ebitda", "EBITDA YoY"),
    ("net_income_yoy_growth_ttm", "net_income", "Net Income YoY"),
    ("free_cash_flow_yoy_growth_ttm", "net_income", "FCF YoY (proxy)"),
]

# Technical anchor fields.
TECHNICAL_ANCHOR_FIELDS = [
    "SMA10",
    "SMA20",
    "SMA30",
    "SMA50",
    "SMA200",
    "EMA10",
    "EMA20",
    "EMA30",
    "EMA50",
    "EMA200",
    "VWAP",
    "VWMA",
    "BB.upper",
    "BB.lower",
    "Pivot.M.Classic.Middle",
    # Camarilla pivots: tighter intraday-style support/resistance levels.
    "Pivot.M.Camarilla.S1",
    "Pivot.M.Camarilla.S2",
    "Pivot.M.Camarilla.R1",
    "Pivot.M.Camarilla.R2",
    "price_52_week_high",
    "price_52_week_low",
    "High.6M",
    "Low.6M",
    "High.3M",
    "Low.3M",
    "High.1M",
    "Low.1M",
]

# Horizons for target estimation — shorter list than the prediction module
# because targets are forward-looking projections, not tape readings.
TARGET_HORIZONS = ["near_term", "medium_term", "long_term"]

TARGET_HORIZON_LABELS = {
    "near_term": "Near-Term (1-4 weeks)",
    "medium_term": "Medium-Term (1-6 months)",
    "long_term": "Long-Term (6-24 months)",
}

# Order of all lenses considered by the blender.  Treated as the canonical
# ordering in the report and CSV so columns line up across horizons.
LENS_ORDER: list[str] = [
    "technical",
    "multiple",
    "trajectory",
    "range",
    "analyst",
    "book_value",
    "yield_dcf",
]

LENS_LABELS: dict[str, str] = {
    "technical": "Technical envelope",
    "multiple": "Peer multiples",
    "trajectory": "Forward fundamentals",
    "range": "Range mean-reversion",
    "analyst": "Analyst consensus",
    "book_value": "Graham / book value",
    "yield_dcf": "Dividend / yield model",
}

# Per-horizon lens weights.  Each row sums to 1.0 across the seven lenses
# (lenses that produce no estimate for a given name are dropped from the
# normalisation at runtime, so missing lenses do not bias the blend).
#
# Design principles per horizon:
#   near_term   → tape dominates; analyst targets get partial weight (street
#                 anchors anchor expectations); trajectory and book value have
#                 minimal influence over weeks.
#   medium_term → valuation reversion takes the lead (peer multiples + analyst
#                 + Graham); technical retains a meaningful share for entry
#                 timing; trajectory becomes material as the earnings catch-up
#                 plays out.
#   long_term   → fundamentals + analyst dominate; book value and yield models
#                 act as quality / income floors; technical fades to a minor
#                 anchor since multi-year prices are driven by compounding.
DEFAULT_LENS_WEIGHTS: dict[str, dict[str, float]] = {
    "near_term": {
        "technical": 0.42,
        "multiple": 0.16,
        "trajectory": 0.06,
        "range": 0.14,
        "analyst": 0.16,
        "book_value": 0.03,
        "yield_dcf": 0.03,
    },
    "medium_term": {
        "technical": 0.16,
        "multiple": 0.28,
        "trajectory": 0.20,
        "range": 0.08,
        "analyst": 0.18,
        "book_value": 0.05,
        "yield_dcf": 0.05,
    },
    "long_term": {
        "technical": 0.04,
        "multiple": 0.20,
        "trajectory": 0.40,
        "range": 0.06,
        "analyst": 0.18,
        "book_value": 0.06,
        "yield_dcf": 0.06,
    },
}

# Lens-level confidence floors.  Some lenses (book_value, yield_dcf) carry
# narrower applicability and should not dominate even when they happen to
# fire; multipliers below scale their effective weight after the per-name
# coverage check has been applied.
LENS_GLOBAL_CONFIDENCE: dict[str, float] = {
    "technical": 1.00,
    "multiple": 1.00,
    "trajectory": 0.95,
    "range": 0.85,
    "analyst": 1.00,
    "book_value": 0.80,
    "yield_dcf": 0.75,
}

# Growth cap: clamp extreme growth rates so projections stay grounded.
MAX_GROWTH_RATE = 2.00  # 200%
MIN_GROWTH_RATE = -0.80  # -80%

# Sustainable-growth-rate cap multiplier: the trajectory lens caps its
# applied long-horizon growth rate at this multiple of the company's
# sustainable growth rate (ROE × retention proxy).  Prevents projecting
# multi-year compounding from a temporary growth burst that the balance
# sheet cannot fund.
SUSTAINABLE_GROWTH_CAP_MULTIPLIER = 1.50

# CAPM-style cost-of-equity inputs used to discount long-horizon trajectory
# targets back to a present-value anchor.  Defaults are deliberately
# conservative — risk-free rate roughly matches mid-cycle 10-year yields
# and the equity-risk premium uses the long-run U.S. consensus.
DEFAULT_RISK_FREE_RATE = 0.045  # 4.5%
DEFAULT_EQUITY_RISK_PREMIUM = 0.055  # 5.5%
MIN_DISCOUNT_RATE = 0.06  # 6% floor so high-beta names don't blow up small
MAX_DISCOUNT_RATE = 0.20  # 20% cap so penny names don't get zeroed out

# Scenario multipliers applied to base-case targets.
SCENARIO_MULTIPLIERS = {
    "bear": 0.85,
    "base": 1.00,
    "bull": 1.18,
}

# Quality-flag thresholds (Piotroski + Altman Z + Leverage).  When a name
# trips a flag the bear band tightens (good fundamentals) or widens (poor
# fundamentals); see ``_build_quality_flags``.
PIOTROSKI_STRONG = 7  # F-score >= 7 → quality bias
PIOTROSKI_WEAK = 3  # F-score <= 3 → fragility bias
ALTMAN_SAFE = 3.0  # Z-score >= 3 → safe zone
ALTMAN_DISTRESS = 1.8  # Z-score <= 1.8 → distress zone
DEBT_TO_EBITDA_HIGH = 5.0  # over-levered

# Opportunity score thresholds.
STRONG_OPPORTUNITY_THRESHOLD = 25.0  # percent implied upside
MODERATE_OPPORTUNITY_THRESHOLD = 10.0

# Peer statistics are robust-trimmed only when the peer set is large enough
# for percentile trimming to be meaningful.  Raw anchor rows remain exported
# so the analysis never hides the underlying data.
PEER_MULTIPLE_TRIM_FRACTION = 0.05
PEER_MULTIPLE_MIN_COUNT_FOR_TRIM = 20

# Upside-only cap used inside the blend.  This prevents a single mechanically
# huge target from dominating the blended base price while preserving the raw
# target in the lens-case CSV.  Downside cases are deliberately not capped.
MAX_UPSIDE_BLEND_MULTIPLE = 4.0

# Active-management risk thresholds used in the new lens-case export and log
# sections.  They do not suppress rows; they make downside cases easier to see.
DOWNSIDE_CASE_THRESHOLD = -20.0
SEVERE_DOWNSIDE_CASE_THRESHOLD = -35.0
HIGH_LENS_DISPERSION_THRESHOLD = 0.25

# Current-cycle profiling gates.  The intent is to surface risks that have
# mattered in recent higher-rate / narrower-breadth markets: duration-like
# valuation risk, balance-sheet stress, liquidity decay, and growth slowdown.
SMALL_CAP_MARKET_CAP_USD = 2_000_000_000.0
MID_CAP_MARKET_CAP_USD = 10_000_000_000.0
LARGE_CAP_MARKET_CAP_USD = 200_000_000_000.0
HIGH_BETA_THRESHOLD = 1.80
EXPENSIVE_PRICE_SALES_THRESHOLD = 10.0
EXPENSIVE_PRICE_EARNINGS_THRESHOLD = 60.0
LOW_SHAREHOLDER_YIELD_THRESHOLD = 1.0
REVENUE_DECLINE_RISK_PCT = -10.0
EBITDA_DECLINE_RISK_PCT = -10.0
LOW_RELATIVE_VOLUME_THRESHOLD = 0.60


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetEstimate:
    """A single price target from one estimation lens."""

    lens: str
    label: str
    implied_price: float | None
    implied_upside_pct: float | None
    confidence_weight: float


@dataclass(frozen=True)
class HorizonTarget:
    """Blended bear / base / bull targets for a single horizon."""

    horizon: str
    bear_price: float | None
    base_price: float | None
    bull_price: float | None
    bear_upside_pct: float | None
    base_upside_pct: float | None
    bull_upside_pct: float | None
    opportunity_score: float | None
    opportunity_label: str
    lens_estimates: list[TargetEstimate]
    coverage: float
    # Per-lens aggregated implied prices (None when lens contributed nothing).
    # Cross-scanner uses these to detect lens disagreement on a single name.
    lens_prices: dict[str, float | None] = field(default_factory=dict)
    # Spread of lens prices around the base, as a fraction of base price.
    # 0.0 = all lenses agree; 0.4 = 40% spread (low confidence).
    lens_dispersion: float | None = None


@dataclass(frozen=True)
class CompanyTargetResult:
    """Complete target result for one company."""

    symbol: str
    company_name: str
    industry: str
    close: float | None
    market_cap: float | None
    horizon_targets: dict[str, HorizonTarget]
    valuation_summary: dict[str, float | None]
    technical_envelope: dict[str, float | None]
    component_scores: dict[str, float | None]
    raw_row: dict[str, Any]
    # New: quality / fragility flags computed from Piotroski, Altman Z, and
    # Debt/EBITDA.  Drive scenario-band tightening or widening in the
    # blender; surfaced in the report and CSV for filtering.
    quality_flags: dict[str, Any] = field(default_factory=dict)
    # New: snapshot of the analyst-consensus inputs (price targets +
    # rating) so the report can decompose model vs street side-by-side.
    analyst_summary: dict[str, float | str | None] = field(default_factory=dict)
    # New: CAPM-style cost of equity used by the trajectory + yield_dcf
    # lenses for this name.  Logged so users can see the discount rate
    # baked into the long-horizon targets.
    cost_of_equity: float | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_price(value: float | None) -> str:
    if value is None:
        return "N/A"
    if abs(value) >= 1000:
        return f"{value:,.2f}"
    if abs(value) >= 1:
        return f"{value:.2f}"
    return f"{value:.4f}"


def _format_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2f}%"


def _format_score(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2f}"


def _format_multiple(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}x"


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else ""
    return str(value)


def _clamped_growth(value: float | None) -> float | None:
    if value is None:
        return None
    rate = value / 100.0 if abs(value) > 5 else value
    return _clamp(rate, MIN_GROWTH_RATE, MAX_GROWTH_RATE)


def _weighted_blend(
    values_and_weights: list[tuple[float | None, float]],
) -> float | None:
    total_value = 0.0
    total_weight = 0.0
    for value, weight in values_and_weights:
        if value is None or weight <= 0:
            continue
        total_value += value * weight
        total_weight += weight
    if total_weight == 0:
        return None
    return total_value / total_weight


def _estimate_price_for_blend(estimate: TargetEstimate, close: float) -> float | None:
    """Return the price used in the blend after upside-only outlier control.

    The raw estimate remains untouched in ``TargetEstimate`` and is exported
    to the lens-case CSV.  Only extreme upside contribution is capped inside
    the blend; downside estimates are intentionally preserved.
    """
    price = estimate.implied_price
    if price is None or price <= 0:
        return None
    if close > 0 and price > close * MAX_UPSIDE_BLEND_MULTIPLE:
        return close * MAX_UPSIDE_BLEND_MULTIPLE
    return price


def _estimate_blend_adjustment(estimate: TargetEstimate, close: float) -> str:
    price = estimate.implied_price
    if price is None or price <= 0 or close <= 0:
        return ""
    if price > close * MAX_UPSIDE_BLEND_MULTIPLE:
        return "upside_capped_for_blend_raw_case_retained"
    return ""


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * pct
    floor_k = int(k)
    ceil_k = min(floor_k + 1, len(sorted_values) - 1)
    frac = k - floor_k
    return sorted_values[floor_k] + frac * (
        sorted_values[ceil_k] - sorted_values[floor_k]
    )


def _market_cap_bucket(market_cap: float | None) -> str:
    if market_cap is None:
        return "unknown"
    if market_cap >= LARGE_CAP_MARKET_CAP_USD:
        return "mega_cap"
    if market_cap >= MID_CAP_MARKET_CAP_USD:
        return "large_cap"
    if market_cap >= SMALL_CAP_MARKET_CAP_USD:
        return "mid_cap"
    if market_cap >= 300_000_000.0:
        return "small_cap"
    return "micro_cap"


def _build_company_profile(
    row: dict[str, Any],
    quality_flags: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify a name for active-manager filtering and lens suitability."""
    market_cap = _coerce_numeric(row.get("market_cap_basic"))
    revenue_growth = _coerce_numeric(row.get("total_revenue_yoy_growth_ttm"))
    ebitda_growth = _coerce_numeric(row.get("ebitda_yoy_growth_ttm"))
    net_income_growth = _coerce_numeric(row.get("net_income_yoy_growth_ttm"))
    operating_margin = _coerce_numeric(row.get("operating_margin"))
    free_cash_flow_margin = _coerce_numeric(row.get("free_cash_flow_margin_ttm"))
    price_sales = _coerce_numeric(row.get("price_sales_current"))
    price_earnings = _coerce_numeric(row.get("price_earnings_ttm"))
    price_book = _coerce_numeric(row.get("price_book_fq"))
    price_free_cash_flow = _coerce_numeric(row.get("price_free_cash_flow_ttm"))
    dividend_yield = _coerce_numeric(
        row.get("dividends_yield_current")
    ) or _coerce_numeric(row.get("dividend_yield_recent"))
    buyback_yield = _coerce_numeric(row.get("buyback_yield"))
    total_shareholder_yield = (dividend_yield or 0.0) + (buyback_yield or 0.0)
    perf_1m = _coerce_numeric(row.get("Perf.1M"))
    perf_6m = _coerce_numeric(row.get("Perf.6M"))
    rsi = _coerce_numeric(row.get("RSI"))
    quality_bias = (quality_flags or {}).get("quality_bias", 0)
    cycle_flags = list((quality_flags or {}).get("cycle_flags", []) or [])

    style_tags: list[str] = []
    if (
        quality_bias
        and quality_bias > 0
        and revenue_growth is not None
        and revenue_growth >= 8
    ):
        style_tags.append("quality_compounder")
    if (
        revenue_growth is not None
        and revenue_growth >= 15
        and (operating_margin is None or operating_margin >= 0)
    ):
        style_tags.append("profitable_or_scaling_growth")
    if (
        (price_earnings is not None and 0 < price_earnings <= 12)
        or (price_book is not None and 0 < price_book <= 1.2)
        or (price_free_cash_flow is not None and 0 < price_free_cash_flow <= 12)
    ):
        style_tags.append("value_reversion")
    if total_shareholder_yield >= 4.0:
        style_tags.append("income_or_buyback_yield")
    if perf_6m is not None and perf_6m >= 20 and (rsi is None or rsi >= 50):
        style_tags.append("momentum_leader")
    if (perf_1m is not None and perf_1m <= -15) or (rsi is not None and rsi <= 35):
        style_tags.append("oversold_recovery_candidate")
    if (price_sales is not None and price_sales >= EXPENSIVE_PRICE_SALES_THRESHOLD) or (
        price_earnings is not None
        and price_earnings >= EXPENSIVE_PRICE_EARNINGS_THRESHOLD
    ):
        style_tags.append("expensive_duration_growth")
    if quality_bias and quality_bias < 0:
        style_tags.append("fragile_balance_sheet_or_quality")
    if not style_tags:
        style_tags.append("balanced_core")

    deterioration_inputs = [revenue_growth, ebitda_growth, net_income_growth]
    has_deterioration = any(
        value is not None and value <= REVENUE_DECLINE_RISK_PCT
        for value in deterioration_inputs
    )
    if has_deterioration and "fundamental_deterioration" not in cycle_flags:
        cycle_flags.append("fundamental_deterioration")

    if (
        operating_margin is not None
        and operating_margin < 0
        and free_cash_flow_margin is not None
        and free_cash_flow_margin < 0
        and "unprofitable_cash_burn" not in cycle_flags
    ):
        cycle_flags.append("unprofitable_cash_burn")

    return {
        "market_cap_bucket": _market_cap_bucket(market_cap),
        "investment_style_profile": "|".join(style_tags[:5]),
        "cycle_risk_flags": "|".join(cycle_flags),
    }


def _estimate_case_role(estimate: TargetEstimate) -> str:
    label = estimate.label.lower()
    if "bear" in label or label.endswith("_low") or "_low" in label:
        return "bear_or_downside_case"
    if "bull" in label or label.endswith("_high") or "_high" in label:
        return "bull_or_upside_case"
    if "base" in label or "median" in label or "average" in label:
        return "base_case"
    if "1y" in label:
        return "long_horizon_anchor"
    return "single_anchor_case"


def _estimate_horizon_scope(estimate: TargetEstimate) -> str:
    label = estimate.label.lower()
    if "near" in label:
        return "near_term"
    if "medium" in label or "med" in label:
        return "medium_term"
    if "long" in label or "1y" in label:
        return "long_term"
    return "all_horizons"


def _estimate_formula_summary(estimate: TargetEstimate) -> str:
    label = estimate.label
    if estimate.lens == "multiple":
        return "close * peer_median_multiple / company_multiple"
    if estimate.lens == "technical":
        return "percentile or median of horizon-specific technical anchors"
    if estimate.lens == "trajectory" and label.startswith("eps_proj"):
        return "projected EPS * current_or_peer_P/E, discounted on long horizon"
    if estimate.lens == "trajectory" and label.startswith("rev_proj"):
        return "projected revenue * current_or_peer_P/S, converted through market cap"
    if estimate.lens == "trajectory" and label.startswith("ebitda_proj"):
        return "projected EBITDA * current_or_peer_EV/EBITDA less net debt"
    if estimate.lens == "range":
        return "close pulled toward range midpoint by extremity * horizon pull"
    if estimate.lens == "analyst":
        return "TradingView analyst target used directly with rating-scaled confidence"
    if estimate.lens == "book_value" and label == "graham_number":
        return "sqrt(22.5 * EPS * book value per share)"
    if estimate.lens == "book_value":
        return "book value per share * peer median P/B"
    if estimate.lens == "yield_dcf" and label == "gordon_ddm":
        return "next annual dividend / (cost of equity - dividend growth)"
    if estimate.lens == "yield_dcf":
        return "shareholder-yield dollars / cost of equity"
    return "lens-specific target anchor"


# ---------------------------------------------------------------------------
# Peer statistics builder
# ---------------------------------------------------------------------------


def _build_peer_multiple_stats(
    scan_data: list[dict[str, Any]],
) -> dict[str, dict[str, float | None]]:
    """Compute robust peer-group statistics for each valuation multiple field.

    Positive multiples are sorted and, when enough peers are available, the
    top/bottom 5% are removed before computing the median and percentiles.
    This controls peer-set data glitches without hiding the raw row-level
    evidence, which is still exported in the lens-case CSV.
    """
    stats: dict[str, dict[str, float | None]] = {}
    for field_name, _label in MULTIPLE_FIELDS:
        raw_values = [
            v
            for v in (_coerce_numeric(row.get(field_name)) for row in scan_data)
            if v is not None and v > 0
        ]
        if not raw_values:
            stats[field_name] = {
                "median": None,
                "p25": None,
                "p75": None,
                "mean": None,
                "count": 0,
                "raw_count": 0,
                "trimmed_count": 0,
                "min": None,
                "max": None,
            }
            continue
        sorted_vals = sorted(raw_values)
        trimmed_vals = sorted_vals
        trimmed_count = 0
        if len(sorted_vals) >= PEER_MULTIPLE_MIN_COUNT_FOR_TRIM:
            trim_each_side = int(len(sorted_vals) * PEER_MULTIPLE_TRIM_FRACTION)
            if trim_each_side > 0 and len(sorted_vals) > trim_each_side * 2:
                trimmed_vals = sorted_vals[trim_each_side:-trim_each_side]
                trimmed_count = len(sorted_vals) - len(trimmed_vals)
        stats[field_name] = {
            "median": median(trimmed_vals),
            "p25": _percentile(trimmed_vals, 0.25),
            "p75": _percentile(trimmed_vals, 0.75),
            "mean": sum(trimmed_vals) / len(trimmed_vals),
            "count": len(trimmed_vals),
            "raw_count": len(sorted_vals),
            "trimmed_count": trimmed_count,
            "min": sorted_vals[0],
            "max": sorted_vals[-1],
        }
    return stats


# ---------------------------------------------------------------------------
# Lens 1: Multiple-anchored targets
# ---------------------------------------------------------------------------


def _multiple_anchored_targets(
    row: dict[str, Any],
    peer_multiple_stats: dict[str, dict[str, float | None]],
    close: float,
) -> list[TargetEstimate]:
    """Estimate implied prices by applying peer-median multiples.

    For price-based multiples (P/E, P/S, P/B, P/FCF, P/OCF):
        implied_price = peer_median_multiple * (company_fundamental / shares)
    Simplified: we ratio-compare the company's own multiple to the peer median
    and project the price that would bring the company in line.
        implied_price = close * (peer_median_multiple / company_multiple)

    For EV-based multiples (EV/Revenue, EV/EBIT, EV/EBITDA):
        implied_ev = peer_median_multiple * company_fundamental
        implied_equity = implied_ev - net_debt
        implied_price = implied_equity / shares ≈ close * (peer_median / company_multiple)
        (same simplification — the ratio approach is consistent when the scan
        universe is uniform enough that capital-structure differences are second-order.)
    """
    estimates: list[TargetEstimate] = []
    for field_name, label in MULTIPLE_FIELDS:
        company_multiple = _coerce_numeric(row.get(field_name))
        peer_stats = peer_multiple_stats.get(field_name, {})
        peer_median = peer_stats.get("median")
        if company_multiple is None or peer_median is None:
            continue
        if company_multiple <= 0 or peer_median <= 0:
            continue

        ratio = peer_median / company_multiple
        implied_price = close * ratio
        implied_upside = ((implied_price / close) - 1.0) * 100.0

        # Confidence weight based on how many peers contributed.
        peer_count = peer_stats.get("count") or 0
        conf = min(1.0, peer_count / 20.0)  # saturates at 20 peers

        estimates.append(
            TargetEstimate(
                lens="multiple",
                label=label,
                implied_price=implied_price,
                implied_upside_pct=implied_upside,
                confidence_weight=conf,
            )
        )
    return estimates


# ---------------------------------------------------------------------------
# Lens 2: Technical-anchored targets
# ---------------------------------------------------------------------------


def _technical_anchored_targets(
    row: dict[str, Any],
    close: float,
) -> tuple[list[TargetEstimate], dict[str, float | None]]:
    """Derive a technical price envelope from MAs, BBs, pivots, and highs/lows.

    Near-term targets anchor to short MAs and Bollinger Bands.
    Medium-term targets anchor to SMA50/EMA50 and 3-6 month extremes.
    Long-term targets anchor to SMA200/EMA200 and 52-week extremes.

    Returns a list of TargetEstimates tagged by horizon and the raw envelope dict.
    """
    envelope: dict[str, float | None] = {}
    estimates: list[TargetEstimate] = []

    for field_name in TECHNICAL_ANCHOR_FIELDS:
        value = _coerce_numeric(row.get(field_name))
        envelope[field_name] = value

    # Near-term anchors: short MAs, BBs, classic + Camarilla pivots, 1M range.
    near_prices: list[float] = []
    for f in [
        "SMA10",
        "SMA20",
        "EMA10",
        "EMA20",
        "BB.upper",
        "BB.lower",
        "Pivot.M.Classic.Middle",
        "Pivot.M.Camarilla.S1",
        "Pivot.M.Camarilla.S2",
        "Pivot.M.Camarilla.R1",
        "Pivot.M.Camarilla.R2",
        "High.1M",
        "Low.1M",
        "VWAP",
    ]:
        v = envelope.get(f)
        if v is not None and v > 0:
            near_prices.append(v)

    if near_prices:
        near_sorted = sorted(near_prices)
        near_base = median(near_prices)
        near_bear = _percentile(near_sorted, 0.20)
        near_bull = _percentile(near_sorted, 0.80)
        for label, price in [
            ("near_bear", near_bear),
            ("near_base", near_base),
            ("near_bull", near_bull),
        ]:
            upside = ((price / close) - 1.0) * 100.0
            estimates.append(
                TargetEstimate(
                    lens="technical",
                    label=f"tech_{label}",
                    implied_price=price,
                    implied_upside_pct=upside,
                    confidence_weight=0.9,
                )
            )

    # Medium-term anchors: SMA50/EMA50, 3M-6M ranges.
    med_prices: list[float] = []
    for f in [
        "SMA30",
        "SMA50",
        "EMA30",
        "EMA50",
        "VWMA",
        "High.3M",
        "Low.3M",
        "High.6M",
        "Low.6M",
    ]:
        v = envelope.get(f)
        if v is not None and v > 0:
            med_prices.append(v)

    if med_prices:
        med_sorted = sorted(med_prices)
        med_base = median(med_prices)
        med_bear = _percentile(med_sorted, 0.15)
        med_bull = _percentile(med_sorted, 0.85)
        for label, price in [
            ("med_bear", med_bear),
            ("med_base", med_base),
            ("med_bull", med_bull),
        ]:
            upside = ((price / close) - 1.0) * 100.0
            estimates.append(
                TargetEstimate(
                    lens="technical",
                    label=f"tech_{label}",
                    implied_price=price,
                    implied_upside_pct=upside,
                    confidence_weight=0.8,
                )
            )

    # Long-term anchors: SMA200/EMA200, 52-week range.
    long_prices: list[float] = []
    for f in ["SMA200", "EMA200", "price_52_week_high", "price_52_week_low"]:
        v = envelope.get(f)
        if v is not None and v > 0:
            long_prices.append(v)

    if long_prices:
        long_sorted = sorted(long_prices)
        long_base = median(long_prices)
        long_bear = _percentile(long_sorted, 0.10)
        long_bull = _percentile(long_sorted, 0.90)
        for label, price in [
            ("long_bear", long_bear),
            ("long_base", long_base),
            ("long_bull", long_bull),
        ]:
            upside = ((price / close) - 1.0) * 100.0
            estimates.append(
                TargetEstimate(
                    lens="technical",
                    label=f"tech_{label}",
                    implied_price=price,
                    implied_upside_pct=upside,
                    confidence_weight=0.7,
                )
            )

    return estimates, envelope


# ---------------------------------------------------------------------------
# Lens 4: Range / mean-reversion anchor
# ---------------------------------------------------------------------------


# Strength of the pull toward the range midpoint per horizon.  Near-term keeps
# the pull mild because short-term moves often run further before reverting;
# long-term applies the strongest pull because multi-year mean reversion is
# the most robust empirical anchor.
_RANGE_PULL_STRENGTH: dict[str, float] = {
    "near_term": 0.30,
    "medium_term": 0.45,
    "long_term": 0.55,
}

# Map horizon → which range to use and what label tag to attach so
# ``_filter_horizon_estimates`` in the blender can pick the right one.
_RANGE_HORIZON_FIELDS: dict[str, tuple[str, str, str]] = {
    "near_term": ("Low.6M", "High.6M", "near"),
    "medium_term": ("price_52_week_low", "price_52_week_high", "med"),
    "long_term": ("price_52_week_low", "price_52_week_high", "long"),
}


def _range_anchored_targets(
    row: dict[str, Any],
    close: float,
) -> list[TargetEstimate]:
    """Mean-reversion anchor based on 52-week and 6-month price ranges.

    Logic:
      * Compute the company's position inside the range as
        ``position = (close - low) / (high - low)`` clamped to ``[0, 1]``.
      * Define ``extremity = |position - 0.5| * 2`` ∈ ``[0, 1]``: 0 in the
        middle of the range, 1 sitting at either extreme.
      * The implied price linearly interpolates between ``close`` and the
        range midpoint, with the pull weighted by ``extremity * strength``.
      * Stocks at the midpoint are barely nudged.  Stocks pinned to a 52w
        high get pulled DOWN toward the midpoint (caps unrealistic upside).
        Stocks pinned to a 52w low get pulled UP toward the midpoint
        (assumes mean-reversion bounce).

    The lens is intentionally conservative: a stock 40% above its range
    midpoint at a 52w high gets pulled back roughly 12-22% depending on
    horizon, which acts as a soft anti-extension cap on the blended target.
    """
    estimates: list[TargetEstimate] = []
    for horizon, (low_field, high_field, tag) in _RANGE_HORIZON_FIELDS.items():
        low = _coerce_numeric(row.get(low_field))
        high = _coerce_numeric(row.get(high_field))
        if low is None or high is None or low <= 0 or high <= low:
            continue

        midpoint = (low + high) / 2.0
        position = _clamp((close - low) / (high - low), 0.0, 1.0)
        extremity = abs(position - 0.5) * 2.0
        pull = extremity * _RANGE_PULL_STRENGTH[horizon]

        implied_price = close * (1.0 - pull) + midpoint * pull
        if implied_price <= 0:
            continue

        upside = ((implied_price / close) - 1.0) * 100.0
        # Higher confidence when the range is wide enough to be informative
        # (a stock that's barely moved gets a lower-confidence range estimate).
        range_breadth = (high - low) / midpoint if midpoint > 0 else 0.0
        conf = _clamp(0.5 + range_breadth, 0.5, 0.9)

        estimates.append(
            TargetEstimate(
                lens="range",
                label=f"range_{tag}",
                implied_price=implied_price,
                implied_upside_pct=upside,
                confidence_weight=conf,
            )
        )
    return estimates


# ---------------------------------------------------------------------------
# Lens 5: Analyst consensus
# ---------------------------------------------------------------------------


# Confidence boost / haircut applied to the analyst lens by AnalystRating.
# Values mirror TradingView's normalized rating in [-1, +1].
_ANALYST_RATING_CONFIDENCE: dict[str, float] = {
    "strong buy": 1.00,
    "buy": 0.95,
    "outperform": 0.90,
    "hold": 0.75,
    "neutral": 0.75,
    "underperform": 0.65,
    "sell": 0.55,
    "strong sell": 0.50,
}


def _analyst_consensus_targets(
    row: dict[str, Any],
    close: float,
) -> list[TargetEstimate]:
    """Lens 5 — sell-side consensus targets.

    Pulls TradingView's bundled ``price_target_low`` / ``price_target_median``
    (or ``price_target_average``) / ``price_target_high`` directly and
    treats them as bear / base / bull anchors. The base anchor confers most
    of the lens weight; the high/low anchors widen the eventual scenario
    band but contribute partial weight on their own.

    Confidence is scaled by ``AnalystRating`` (Strong Buy/Buy keep the full
    weight; Sell/Strong Sell keep only ~50%) and capped to 1.0. When no
    target is published the lens stays silent so it cannot bias the blend.
    """
    estimates: list[TargetEstimate] = []
    target_low = _coerce_numeric(row.get("price_target_low"))
    target_median = _coerce_numeric(row.get("price_target_median"))
    target_average = _coerce_numeric(row.get("price_target_average"))
    target_high = _coerce_numeric(row.get("price_target_high"))
    target_1y = _coerce_numeric(row.get("price_target_1y"))

    base_anchor = target_median or target_average
    rating = str(row.get("AnalystRating") or "").strip().lower()
    rating_conf = _ANALYST_RATING_CONFIDENCE.get(rating, 0.85)

    def _add(label: str, price: float | None, base_weight: float) -> None:
        if price is None or price <= 0:
            return
        upside = ((price / close) - 1.0) * 100.0
        estimates.append(
            TargetEstimate(
                lens="analyst",
                label=label,
                implied_price=price,
                implied_upside_pct=upside,
                confidence_weight=_clamp(base_weight * rating_conf, 0.0, 1.0),
            )
        )

    # Base anchor (heaviest weight) — analyst median or average.
    _add("analyst_median", base_anchor, 1.00)
    # Range anchors — partial weight, used both for blending and to widen
    # the scenario band when analysts disagree among themselves.
    _add("analyst_low", target_low, 0.55)
    _add("analyst_high", target_high, 0.55)
    # 1-year price target carries similar weight to the base when present
    # but is a separate evidence point, so keep it slightly lower.
    _add("analyst_1y", target_1y, 0.85)
    return estimates


# ---------------------------------------------------------------------------
# Lens 6: Book-value / Graham-style anchors
# ---------------------------------------------------------------------------


# Graham's classic earnings/book-value formula assumes a fair price of
# sqrt(22.5 * EPS * BVPS); the constant blends a 15x P/E with a 1.5x P/B.
GRAHAM_EARNINGS_BOOK_CONSTANT = 22.5


def _book_value_targets(
    row: dict[str, Any],
    peer_multiple_stats: dict[str, dict[str, float | None]],
    close: float,
) -> list[TargetEstimate]:
    """Lens 6 — Graham fair value + peer-relative P/B reversion.

    Two sub-anchors:

    * **Graham number** — ``\u221a(22.5 \u00d7 EPS \u00d7 BVPS)``. Capped at 0 when
      either input is non-positive (loss-makers and negative-equity firms
      drop out). Especially anchoring for financials, REITs, and slow-growth
      industrials.
    * **Peer-median P/B reversion** — implied price that would bring the
      company's P/B in line with the peer-median P/B, computed only when
      ``book_value_per_share_fq`` is available so the price can be expressed
      in absolute terms (rather than as a ratio simplification).

    Both anchors are intentionally conservative; they form a "value floor"
    that resists momentum-driven overshoot and keeps the blended target
    grounded for asset-heavy businesses.
    """
    estimates: list[TargetEstimate] = []
    bvps = _coerce_numeric(row.get("book_value_per_share_fq"))
    eps_ttm = _coerce_numeric(
        row.get("earnings_per_share_diluted_ttm")
    ) or _coerce_numeric(row.get("earnings_per_share_basic_ttm"))
    if eps_ttm is None:
        eps_ttm = _coerce_numeric(row.get("last_annual_eps"))

    # Sub-anchor 1: Graham number.
    if bvps is not None and bvps > 0 and eps_ttm is not None and eps_ttm > 0:
        graham_price = (GRAHAM_EARNINGS_BOOK_CONSTANT * eps_ttm * bvps) ** 0.5
        if graham_price > 0:
            upside = ((graham_price / close) - 1.0) * 100.0
            estimates.append(
                TargetEstimate(
                    lens="book_value",
                    label="graham_number",
                    implied_price=graham_price,
                    implied_upside_pct=upside,
                    confidence_weight=0.80,
                )
            )

    # Sub-anchor 2: peer-median P/B reversion (absolute).
    pb_peer = peer_multiple_stats.get("price_book_fq", {}).get("median")
    if bvps is not None and bvps > 0 and pb_peer is not None and pb_peer > 0:
        pb_price = pb_peer * bvps
        if pb_price > 0:
            upside = ((pb_price / close) - 1.0) * 100.0
            estimates.append(
                TargetEstimate(
                    lens="book_value",
                    label="peer_pb",
                    implied_price=pb_price,
                    implied_upside_pct=upside,
                    confidence_weight=0.65,
                )
            )

    return estimates


# ---------------------------------------------------------------------------
# Lens 7: Yield / dividend discount model
# ---------------------------------------------------------------------------


def _yield_dcf_targets(
    row: dict[str, Any],
    close: float,
    cost_of_equity: float,
) -> list[TargetEstimate]:
    """Lens 7 — Gordon dividend discount model + shareholder-yield reversion.

    Two sub-anchors that only fire for income-paying names:

    * **Gordon DDM** — ``P = D\u2081 / (k \u2212 g)`` where ``D\u2081`` is the next-period
      dividend (current DPS grown by the dividend growth rate), ``k`` is
      the company's CAPM cost of equity, and ``g`` is the dividend growth
      rate. The growth rate is capped at ``k - 1%`` so the denominator
      stays positive.
    * **Shareholder-yield reversion** — combines dividend yield with
      buyback yield (when available) and reverts the total shareholder
      yield to the cost of equity, implying ``price = (yield_$ / k)``.
      Acts as a sanity check on the DDM anchor.\n
    Both anchors are skipped silently when the security pays no dividend,
    keeping the lens dormant for non-payers (no spurious downward bias).
    """
    estimates: list[TargetEstimate] = []
    dps = _coerce_numeric(row.get("dividends_per_share_fq"))
    div_yield_pct = _coerce_numeric(
        row.get("dividends_yield_current")
    ) or _coerce_numeric(row.get("dividend_yield_recent"))
    buyback_yield_pct = _coerce_numeric(row.get("buyback_yield"))
    div_growth_pct = _coerce_numeric(
        row.get("dps_common_stock_prim_issue_yoy_growth_fy")
    )

    if dps is not None:
        annual_dps = dps * 4.0  # MRQ \u00d7 4 quarters
    elif div_yield_pct is not None and div_yield_pct > 0:
        annual_dps = (div_yield_pct / 100.0) * close
    else:
        annual_dps = None

    if annual_dps is None or annual_dps <= 0:
        return estimates

    g = (div_growth_pct or 0.0) / 100.0 if div_growth_pct is not None else 0.0
    g = _clamp(g, -0.05, cost_of_equity - 0.01)  # keep denominator positive

    # Sub-anchor 1: Gordon DDM.
    next_dividend = annual_dps * (1.0 + g)
    denominator = cost_of_equity - g
    if denominator > 0:
        ddm_price = next_dividend / denominator
        if ddm_price > 0:
            upside = ((ddm_price / close) - 1.0) * 100.0
            estimates.append(
                TargetEstimate(
                    lens="yield_dcf",
                    label="gordon_ddm",
                    implied_price=ddm_price,
                    implied_upside_pct=upside,
                    confidence_weight=0.75,
                )
            )

    # Sub-anchor 2: shareholder-yield reversion to cost of equity.
    total_yield_pct = (div_yield_pct or 0.0) + (buyback_yield_pct or 0.0)
    if total_yield_pct > 0:
        total_yield = total_yield_pct / 100.0
        shareholder_dollars = total_yield * close
        if shareholder_dollars > 0 and cost_of_equity > 0:
            sy_price = shareholder_dollars / cost_of_equity
            if sy_price > 0:
                upside = ((sy_price / close) - 1.0) * 100.0
                estimates.append(
                    TargetEstimate(
                        lens="yield_dcf",
                        label="shareholder_yield_reversion",
                        implied_price=sy_price,
                        implied_upside_pct=upside,
                        confidence_weight=0.55,
                    )
                )

    return estimates


# ---------------------------------------------------------------------------
# Cost of equity (CAPM)
# ---------------------------------------------------------------------------


def _cost_of_equity(row: dict[str, Any]) -> float:
    """CAPM-style cost of equity used by trajectory and yield_dcf lenses.

    ``k = risk_free_rate + beta * equity_risk_premium`` clamped to the
    ``[MIN_DISCOUNT_RATE, MAX_DISCOUNT_RATE]`` envelope so missing or extreme
    betas cannot blow up the discount factor.  Falls back to a beta of 1.0
    when no beta data is available so the lens still produces a value.
    """
    beta = _coerce_numeric(row.get("beta_1_year"))
    if beta is None:
        beta = _coerce_numeric(row.get("beta_3_year"))
    if beta is None:
        beta = _coerce_numeric(row.get("beta_5_year"))
    if beta is None:
        beta = 1.0
    beta = _clamp(beta, -0.5, 3.0)
    k = DEFAULT_RISK_FREE_RATE + beta * DEFAULT_EQUITY_RISK_PREMIUM
    return _clamp(k, MIN_DISCOUNT_RATE, MAX_DISCOUNT_RATE)


# ---------------------------------------------------------------------------
# Quality / fragility flags
# ---------------------------------------------------------------------------


def _build_quality_flags(row: dict[str, Any]) -> dict[str, Any]:
    """Inspect the row for quality and distress indicators.

    Returns a dict with:
      * ``piotroski`` -- raw Piotroski F-score (0-9) or ``None``
      * ``altman_z`` -- raw Altman Z-score or ``None``
      * ``debt_to_ebitda`` -- raw leverage ratio or ``None``
      * ``quality_bias`` -- ``+2`` strong, ``-2`` weak, ``0`` neutral.
        Used by the blender to tighten or widen the bear / bull bands.
      * ``cycle_risk_bias`` -- downside-only bias from current-cycle risks
        (duration valuation, deterioration, liquidity decay, cash burn).
      * ``flags`` -- list of human-readable tags for the report.
    """
    piotroski = _coerce_numeric(row.get("piotroski_f_score_ttm"))
    altman = _coerce_numeric(row.get("altman_z_score_ttm"))
    debt_ebitda = _coerce_numeric(row.get("total_debt_to_ebitda_fq"))
    beta = _coerce_numeric(row.get("beta_1_year"))
    market_cap = _coerce_numeric(row.get("market_cap_basic"))
    price_sales = _coerce_numeric(row.get("price_sales_current"))
    price_earnings = _coerce_numeric(row.get("price_earnings_ttm"))
    dividend_yield = _coerce_numeric(
        row.get("dividends_yield_current")
    ) or _coerce_numeric(row.get("dividend_yield_recent"))
    buyback_yield = _coerce_numeric(row.get("buyback_yield"))
    revenue_growth = _coerce_numeric(row.get("total_revenue_yoy_growth_ttm"))
    ebitda_growth = _coerce_numeric(row.get("ebitda_yoy_growth_ttm"))
    net_income_growth = _coerce_numeric(row.get("net_income_yoy_growth_ttm"))
    operating_margin = _coerce_numeric(row.get("operating_margin"))
    free_cash_flow_margin = _coerce_numeric(row.get("free_cash_flow_margin_ttm"))
    relative_volume = _coerce_numeric(row.get("relative_volume_10d_calc"))

    flags: list[str] = []
    cycle_flags: list[str] = []
    bias = 0
    cycle_risk_bias = 0
    if piotroski is not None:
        if piotroski >= PIOTROSKI_STRONG:
            flags.append("piotroski_strong")
            bias += 1
        elif piotroski <= PIOTROSKI_WEAK:
            flags.append("piotroski_weak")
            bias -= 1
    if altman is not None:
        if altman >= ALTMAN_SAFE:
            flags.append("altman_safe")
            bias += 1
        elif altman <= ALTMAN_DISTRESS:
            flags.append("altman_distress")
            bias -= 1
    if debt_ebitda is not None and debt_ebitda > DEBT_TO_EBITDA_HIGH:
        flags.append("over_levered")
        bias -= 1

    total_shareholder_yield = (dividend_yield or 0.0) + (buyback_yield or 0.0)
    high_valuation = (
        price_sales is not None and price_sales >= EXPENSIVE_PRICE_SALES_THRESHOLD
    ) or (
        price_earnings is not None
        and price_earnings >= EXPENSIVE_PRICE_EARNINGS_THRESHOLD
    )
    if (
        beta is not None
        and beta >= HIGH_BETA_THRESHOLD
        and high_valuation
        and total_shareholder_yield < LOW_SHAREHOLDER_YIELD_THRESHOLD
    ):
        cycle_flags.append("duration_valuation_risk")
        cycle_risk_bias -= 1

    if revenue_growth is not None and revenue_growth <= REVENUE_DECLINE_RISK_PCT:
        if (
            ebitda_growth is None
            or ebitda_growth <= EBITDA_DECLINE_RISK_PCT
            or (net_income_growth is not None and net_income_growth < 0)
        ):
            cycle_flags.append("fundamental_deterioration")
            cycle_risk_bias -= 1

    if (
        operating_margin is not None
        and operating_margin < 0
        and free_cash_flow_margin is not None
        and free_cash_flow_margin < 0
    ):
        cycle_flags.append("unprofitable_cash_burn")
        cycle_risk_bias -= 1

    if (
        market_cap is not None
        and market_cap < SMALL_CAP_MARKET_CAP_USD
        and relative_volume is not None
        and relative_volume < LOW_RELATIVE_VOLUME_THRESHOLD
    ):
        cycle_flags.append("small_cap_liquidity_decay")
        cycle_risk_bias -= 1

    return {
        "piotroski": piotroski,
        "altman_z": altman,
        "debt_to_ebitda": debt_ebitda,
        "quality_bias": _clamp(bias, -2, 2),
        "cycle_risk_bias": _clamp(cycle_risk_bias, -4, 0),
        "cycle_flags": cycle_flags,
        "flags": flags + cycle_flags,
    }


# ---------------------------------------------------------------------------
# Lens 3: Fundamental-trajectory targets
# ---------------------------------------------------------------------------


def _trajectory_targets(
    row: dict[str, Any],
    peer_multiple_stats: dict[str, dict[str, float | None]],
    close: float,
    market_cap: float | None,
    cost_of_equity: float,
) -> list[TargetEstimate]:
    """Project fundamentals forward using the company's own growth rates,
    apply current or peer multiples to the projected figures, and discount
    the long-horizon result back to present value via CAPM cost of equity.

    Three sub-models per horizon:

    * **EPS-based** -- projected_EPS x applied_PE (current PE preferred,
      peer PE fallback).  Most informative for asset-light earners.
    * **Revenue-based** -- projected_revenue x applied_PS converted to
      market-cap and back to price via the current price/mcap ratio.  Best
      for high-growth names where the bottom line lags revenue.
    * **EBITDA-based** -- projected_EBITDA x applied_EV/EBITDA, minus net
      debt, then converted to price.  Capital-structure neutral; most
      informative for levered or capex-heavy names.

    Growth rates are subject to two caps:
      1. Hard clamp ``[MIN_GROWTH_RATE, MAX_GROWTH_RATE]`` to keep the
         projection numerically stable.
      2. Soft cap at ``SUSTAINABLE_GROWTH_CAP_MULTIPLIER * SGR`` (when SGR
         from ``sustainable_growth_rate_ttm`` is positive) so multi-year
         projections cannot outrun what the balance sheet can fund.

    Long-horizon (``years > 1``) implied prices are discounted by
    ``(1 + k)^(years - 1)`` so present-value targets reflect the cost of
    waiting.  Near-term targets skip the discount (the projection horizon
    is short enough that discount dominates noise).

    Horizon factors:
        near_term   = 0.25 yr (no decay, no discount)
        medium_term = 1.00 yr (decay 0.90, no discount)
        long_term   = 2.00 yr (decay 0.70, discounted)
    """
    estimates: list[TargetEstimate] = []

    # Extract growth rates.
    rev_growth_yoy = _clamped_growth(
        _coerce_numeric(row.get("total_revenue_yoy_growth_ttm"))
    )
    ebitda_growth = _clamped_growth(_coerce_numeric(row.get("ebitda_yoy_growth_ttm")))
    ni_growth = _clamped_growth(_coerce_numeric(row.get("net_income_yoy_growth_ttm")))
    fcf_growth = _clamped_growth(
        _coerce_numeric(row.get("free_cash_flow_yoy_growth_ttm"))
    )
    eps_growth = _clamped_growth(
        _coerce_numeric(row.get("earnings_per_share_diluted_yoy_growth_ttm"))
    )
    eps_fwd_growth = _clamped_growth(
        _safe_ratio(
            (_coerce_numeric(row.get("earnings_per_share_forecast_next_fq")) or 0)
            - (_coerce_numeric(row.get("earnings_per_share_fq")) or 0),
            abs(_coerce_numeric(row.get("earnings_per_share_fq")) or 0) or None,
        )
    )

    # Sustainable growth rate (ROE x retention) used as a soft cap on the
    # primary growth driver for long horizons.  TradingView reports SGR as a
    # percentage; we convert to a fraction.
    sgr_pct = _coerce_numeric(row.get("sustainable_growth_rate_ttm"))
    sgr_cap = (
        (sgr_pct / 100.0) * SUSTAINABLE_GROWTH_CAP_MULTIPLIER
        if sgr_pct is not None and sgr_pct > 0
        else None
    )

    # Best available growth rate (prefer forward EPS, then revenue, then earnings).
    primary_growth = eps_fwd_growth or rev_growth_yoy or eps_growth
    secondary_growth = ebitda_growth or ni_growth or fcf_growth

    if primary_growth is None and secondary_growth is None:
        return estimates

    best_growth = primary_growth if primary_growth is not None else secondary_growth
    # Apply the sustainable-growth-rate cap to the primary driver.  Only
    # caps to the upside (downside growth is preserved as-is).
    if sgr_cap is not None and best_growth > sgr_cap:
        best_growth = sgr_cap

    # Revenue and EPS anchors for projection.
    total_revenue = _coerce_numeric(row.get("total_revenue"))
    ebitda_val = _coerce_numeric(row.get("ebitda"))
    eps_actual = _coerce_numeric(row.get("earnings_per_share_fq"))
    pe_current = _coerce_numeric(row.get("price_earnings_ttm"))
    ps_current = _coerce_numeric(row.get("price_sales_current"))
    ev_ebitda_current = _coerce_numeric(row.get("enterprise_value_ebitda_ttm"))

    pe_peer = peer_multiple_stats.get("price_earnings_ttm", {}).get("median")
    ps_peer = peer_multiple_stats.get("price_sales_current", {}).get("median")
    ev_ebitda_peer = peer_multiple_stats.get("enterprise_value_ebitda_ttm", {}).get(
        "median"
    )

    # Horizon projection factors and per-horizon decay / discount.
    horizon_factors = {
        "near_term": 0.25,
        "medium_term": 1.0,
        "long_term": 2.0,
    }
    growth_decay = {
        "near_term": 1.0,
        "medium_term": 0.90,
        "long_term": 0.70,
    }

    def _present_value(price: float, years: float) -> float:
        # Only discount long-horizon (>= 1.5 yr) targets.  Shorter horizons
        # leave the projected price untouched -- the projection itself is
        # already conservative enough.
        if years < 1.5 or cost_of_equity <= 0:
            return price
        return price / ((1.0 + cost_of_equity) ** (years - 1.0))

    for horizon, years in horizon_factors.items():
        decay = growth_decay[horizon]
        effective_growth = best_growth * decay

        # EPS-based projection.
        if eps_actual is not None and eps_actual > 0:
            projected_eps = eps_actual * ((1.0 + effective_growth) ** years)
            applied_pe = pe_current if pe_current and pe_current > 0 else pe_peer
            if applied_pe is not None and applied_pe > 0:
                implied_price = _present_value(projected_eps * applied_pe, years)
                if implied_price > 0:
                    upside = ((implied_price / close) - 1.0) * 100.0
                    estimates.append(
                        TargetEstimate(
                            lens="trajectory",
                            label=f"eps_proj_{horizon}",
                            implied_price=implied_price,
                            implied_upside_pct=upside,
                            confidence_weight=0.85 * decay,
                        )
                    )

        # Revenue-based projection (use P/S).
        if (
            total_revenue is not None
            and total_revenue > 0
            and market_cap is not None
            and market_cap > 0
        ):
            rev_growth_applied = (rev_growth_yoy or best_growth) * decay
            if sgr_cap is not None and rev_growth_applied > sgr_cap:
                rev_growth_applied = sgr_cap
            projected_rev = total_revenue * ((1.0 + rev_growth_applied) ** years)
            applied_ps = ps_current if ps_current and ps_current > 0 else ps_peer
            if applied_ps is not None and applied_ps > 0:
                implied_mcap = projected_rev * applied_ps
                implied_price = _present_value(
                    close * (implied_mcap / market_cap), years
                )
                if implied_price > 0:
                    upside = ((implied_price / close) - 1.0) * 100.0
                    estimates.append(
                        TargetEstimate(
                            lens="trajectory",
                            label=f"rev_proj_{horizon}",
                            implied_price=implied_price,
                            implied_upside_pct=upside,
                            confidence_weight=0.75 * decay,
                        )
                    )

        # EBITDA-based projection (use EV/EBITDA).
        if (
            ebitda_val is not None
            and ebitda_val > 0
            and market_cap is not None
            and market_cap > 0
        ):
            ebitda_growth_applied = (ebitda_growth or best_growth) * decay
            if sgr_cap is not None and ebitda_growth_applied > sgr_cap:
                ebitda_growth_applied = sgr_cap
            projected_ebitda = ebitda_val * ((1.0 + ebitda_growth_applied) ** years)
            applied_ev_ebitda = (
                ev_ebitda_current
                if ev_ebitda_current and ev_ebitda_current > 0
                else ev_ebitda_peer
            )
            if applied_ev_ebitda is not None and applied_ev_ebitda > 0:
                net_debt = _coerce_numeric(row.get("net_debt")) or 0.0
                implied_ev = projected_ebitda * applied_ev_ebitda
                implied_equity = implied_ev - net_debt
                if implied_equity > 0 and market_cap > 0:
                    implied_price = _present_value(
                        close * (implied_equity / market_cap), years
                    )
                    if implied_price > 0:
                        upside = ((implied_price / close) - 1.0) * 100.0
                        estimates.append(
                            TargetEstimate(
                                lens="trajectory",
                                label=f"ebitda_proj_{horizon}",
                                implied_price=implied_price,
                                implied_upside_pct=upside,
                                confidence_weight=0.80 * decay,
                            )
                        )

    return estimates


# ---------------------------------------------------------------------------
# Target blending
# ---------------------------------------------------------------------------


def _blend_horizon_target(
    horizon: str,
    close: float,
    multiple_estimates: list[TargetEstimate],
    technical_estimates: list[TargetEstimate],
    trajectory_estimates: list[TargetEstimate],
    range_estimates: list[TargetEstimate],
    analyst_estimates: list[TargetEstimate],
    book_value_estimates: list[TargetEstimate],
    yield_dcf_estimates: list[TargetEstimate],
    component_scores: dict[str, float | None],
    quality_flags: dict[str, Any],
    lens_weights: dict[str, float] | None = None,
) -> HorizonTarget:
    """Blend estimates from all seven lenses into a bear / base / bull target.

    Pipeline
    --------
    1. **Within-lens aggregation** -- collapse each lens's per-anchor
       estimates into a single confidence-weighted price.  Lenses that
       carry a horizon tag in their label (technical, trajectory, range,
       analyst-1y) are filtered to keep only horizon-appropriate anchors;
       lenses without a tag (multiple, book_value, yield_dcf, analyst-base)
       contribute the same price across all horizons.
    2. **Cross-lens normalisation** -- the configured horizon weights are
       multiplied by the per-lens global confidence (``LENS_GLOBAL_CONFIDENCE``)
       and renormalised over only the lenses that actually fired, so missing
       lenses do not bias the blend toward whichever lenses happen to be
       present.
    3. **Base price** -- weighted average of the per-lens prices using the
       normalised weights.
    4. **Lens dispersion** -- ``(max - min) / base`` across contributing
       lenses.  Surfaces disagreement (low confidence => wide scenario fan).
    5. **Scenario bands** -- bear and bull multipliers shift with:
         * component scores (quality+safety tighten bear, momentum+trend
           widen bull),
         * lens dispersion (widens both ways),
         * quality_flags.quality_bias (positive: tighter bear; negative:
           wider bear, tighter bull -- distress + over-leverage assumptions),
         * analyst spread (price_target_high - low / base widens both ways
           when analysts disagree among themselves).
    6. **Opportunity score** -- ``base_upside * (0.6 + 0.4 * coverage)``.
    """
    weights = lens_weights or DEFAULT_LENS_WEIGHTS.get(
        horizon, DEFAULT_LENS_WEIGHTS["medium_term"]
    )

    def _aggregate_lens(estimates: list[TargetEstimate]) -> float | None:
        if not estimates:
            return None
        return _weighted_blend(
            [
                (_estimate_price_for_blend(e, close), e.confidence_weight)
                for e in estimates
            ]
        )

    # Filter estimates by horizon tag for technical / trajectory / range
    # lenses (they emit per-horizon-tagged anchors).
    def _filter_horizon_estimates(
        estimates: list[TargetEstimate], horizon_key: str
    ) -> list[TargetEstimate]:
        horizon_tags = {
            "near_term": ["near"],
            "medium_term": ["med", "medium"],
            "long_term": ["long"],
        }
        tags = horizon_tags.get(horizon_key, [])
        filtered = [e for e in estimates if any(tag in e.label for tag in tags)]
        return filtered if filtered else estimates  # fallback to all if no match

    tech_for_horizon = _filter_horizon_estimates(technical_estimates, horizon)
    traj_for_horizon = _filter_horizon_estimates(trajectory_estimates, horizon)
    range_for_horizon = _filter_horizon_estimates(range_estimates, horizon)
    # Analyst lens: ``analyst_1y`` is treated as long-term anchor only.
    if horizon == "long_term":
        analyst_for_horizon = analyst_estimates  # use all (incl. 1y)
    else:
        analyst_for_horizon = [e for e in analyst_estimates if e.label != "analyst_1y"]

    tech_price = _aggregate_lens(tech_for_horizon)
    mult_price = _aggregate_lens(multiple_estimates)
    traj_price = _aggregate_lens(traj_for_horizon)
    range_price = _aggregate_lens(range_for_horizon)
    analyst_price = _aggregate_lens(analyst_for_horizon)
    bv_price = _aggregate_lens(book_value_estimates)
    yld_price = _aggregate_lens(yield_dcf_estimates)

    all_lens_estimates = (
        tech_for_horizon
        + multiple_estimates
        + traj_for_horizon
        + range_for_horizon
        + analyst_for_horizon
        + book_value_estimates
        + yield_dcf_estimates
    )

    lens_prices: dict[str, float | None] = {
        "technical": tech_price,
        "multiple": mult_price,
        "trajectory": traj_price,
        "range": range_price,
        "analyst": analyst_price,
        "book_value": bv_price,
        "yield_dcf": yld_price,
    }

    # Coverage: fraction of the seven lenses that produced a price.
    contributing = [p for p in lens_prices.values() if p is not None]
    lens_coverage = len(contributing) / float(len(LENS_ORDER))

    # Build the weighted blend using normalised weights so missing lenses
    # don't bias the result.
    weighted_pairs: list[tuple[float | None, float]] = []
    for lens_name, price in lens_prices.items():
        w = weights.get(lens_name, 0.0) * LENS_GLOBAL_CONFIDENCE.get(lens_name, 1.0)
        weighted_pairs.append((price, w))
    base_price = _weighted_blend(weighted_pairs)

    if base_price is None:
        return HorizonTarget(
            horizon=horizon,
            bear_price=None,
            base_price=None,
            bull_price=None,
            bear_upside_pct=None,
            base_upside_pct=None,
            bull_upside_pct=None,
            opportunity_score=None,
            opportunity_label="insufficient-data",
            lens_estimates=all_lens_estimates,
            coverage=lens_coverage,
            lens_prices=lens_prices,
            lens_dispersion=None,
        )

    # Lens dispersion: spread of lens prices around the base, as a fraction.
    if len(contributing) >= 2 and base_price > 0:
        lens_dispersion = (max(contributing) - min(contributing)) / base_price
    else:
        lens_dispersion = None

    # Tilt scenario band based on component scores.
    quality_score = component_scores.get("quality") or 0.0
    safety_score = component_scores.get("safety") or 0.0
    momentum_score = component_scores.get("momentum") or 0.0
    trend_score = component_scores.get("trend") or 0.0

    bear_adj = SCENARIO_MULTIPLIERS["bear"]
    bear_adj += _clamp((quality_score + safety_score) * 0.02, -0.05, 0.05)
    bull_adj = SCENARIO_MULTIPLIERS["bull"]
    bull_adj += _clamp((momentum_score + trend_score) * 0.02, -0.05, 0.08)

    # Quality flags: strong fundamentals tighten the bear band, while weak
    # fundamentals explicitly widen downside and reduce the bull case.  This
    # is intentionally asymmetric so bad cases are not softened away.
    quality_bias = (quality_flags or {}).get("quality_bias", 0)
    if quality_bias and quality_bias > 0:
        bear_adj += _clamp(quality_bias * 0.03, 0.0, 0.06)
    elif quality_bias and quality_bias < 0:
        bear_adj += _clamp(quality_bias * 0.035, -0.08, 0.0)
        bull_adj += _clamp(quality_bias * 0.02, -0.05, 0.0)

    # Current-cycle risks (duration-like valuation, deteriorating growth,
    # cash burn, liquidity decay) are downside-only modifiers.
    cycle_risk_bias = (quality_flags or {}).get("cycle_risk_bias", 0)
    if cycle_risk_bias and cycle_risk_bias < 0:
        bear_adj += _clamp(cycle_risk_bias * 0.025, -0.10, 0.0)
        bull_adj += _clamp(cycle_risk_bias * 0.015, -0.06, 0.0)

    # Lens dispersion widens both bands proportionally.
    if lens_dispersion is not None:
        dispersion_widening = _clamp(lens_dispersion * 0.15, 0.0, 0.10)
        bear_adj -= dispersion_widening
        bull_adj += dispersion_widening

    # Analyst spread: when the sell side itself disagrees (high vs low /
    # base ratio) widen the bands so the band reflects external uncertainty.
    analyst_low_est = next(
        (e for e in analyst_estimates if e.label == "analyst_low"), None
    )
    analyst_high_est = next(
        (e for e in analyst_estimates if e.label == "analyst_high"), None
    )
    if (
        analyst_low_est is not None
        and analyst_high_est is not None
        and analyst_low_est.implied_price
        and analyst_high_est.implied_price
        and base_price > 0
    ):
        analyst_spread = (
            analyst_high_est.implied_price - analyst_low_est.implied_price
        ) / base_price
        analyst_widening = _clamp(analyst_spread * 0.10, 0.0, 0.08)
        bear_adj -= analyst_widening
        bull_adj += analyst_widening

    bear_price = base_price * bear_adj
    bull_price = base_price * bull_adj

    bear_upside = ((bear_price / close) - 1.0) * 100.0
    base_upside = ((base_price / close) - 1.0) * 100.0
    bull_upside = ((bull_price / close) - 1.0) * 100.0

    raw_opportunity = base_upside * (0.6 + 0.4 * lens_coverage)
    opportunity_score = _clamp(raw_opportunity, -100.0, 200.0)

    if opportunity_score >= STRONG_OPPORTUNITY_THRESHOLD:
        opportunity_label = "Strong Opportunity"
    elif opportunity_score >= MODERATE_OPPORTUNITY_THRESHOLD:
        opportunity_label = "Moderate Opportunity"
    elif opportunity_score <= -STRONG_OPPORTUNITY_THRESHOLD:
        opportunity_label = "Strong Overvaluation"
    elif opportunity_score <= -MODERATE_OPPORTUNITY_THRESHOLD:
        opportunity_label = "Moderate Overvaluation"
    else:
        opportunity_label = "Fair Value Range"

    return HorizonTarget(
        horizon=horizon,
        bear_price=bear_price,
        base_price=base_price,
        bull_price=bull_price,
        bear_upside_pct=bear_upside,
        base_upside_pct=base_upside,
        bull_upside_pct=bull_upside,
        opportunity_score=opportunity_score,
        opportunity_label=opportunity_label,
        lens_estimates=all_lens_estimates,
        coverage=lens_coverage,
        lens_prices=lens_prices,
        lens_dispersion=lens_dispersion,
    )


# ---------------------------------------------------------------------------
# Valuation summary
# ---------------------------------------------------------------------------


def _build_valuation_summary(
    row: dict[str, Any],
    peer_multiple_stats: dict[str, dict[str, float | None]],
) -> dict[str, float | None]:
    """Build a summary dict comparing the company's multiples to peer medians."""
    summary: dict[str, float | None] = {}
    for field_name, label in MULTIPLE_FIELDS:
        company_val = _coerce_numeric(row.get(field_name))
        peer_median = peer_multiple_stats.get(field_name, {}).get("median")
        summary[f"{label}_company"] = company_val
        summary[f"{label}_peer_median"] = peer_median
        if company_val is not None and peer_median is not None and peer_median != 0:
            summary[f"{label}_vs_peer"] = ((company_val / peer_median) - 1.0) * 100.0
        else:
            summary[f"{label}_vs_peer"] = None
    return summary


# ---------------------------------------------------------------------------
# Full company target builder
# ---------------------------------------------------------------------------


def _build_analyst_summary(
    row: dict[str, Any],
    close: float,
) -> dict[str, float | str | None]:
    """Snapshot the raw analyst-consensus inputs and derive a base upside %.

    The summary is logged side-by-side with the model-blended target so users
    can see exactly how far the model deviates from the street view.
    """
    target_low = _coerce_numeric(row.get("price_target_low"))
    target_median = _coerce_numeric(row.get("price_target_median"))
    target_average = _coerce_numeric(row.get("price_target_average"))
    target_high = _coerce_numeric(row.get("price_target_high"))
    target_1y = _coerce_numeric(row.get("price_target_1y"))
    rating = row.get("AnalystRating")

    base = target_median or target_average
    upside_pct = ((base / close) - 1.0) * 100.0 if base and close > 0 else None

    return {
        "analyst_low": target_low,
        "analyst_median": target_median,
        "analyst_average": target_average,
        "analyst_high": target_high,
        "analyst_1y": target_1y,
        "analyst_rating": str(rating) if rating else None,
        "analyst_base_upside_pct": upside_pct,
    }


def _build_company_targets(
    row: dict[str, Any],
    derived_row: dict[str, float | None],
    profiles: dict[str, dict[str, float | int | None]],
    peer_multiple_stats: dict[str, dict[str, float | None]],
    scoring_profile: ScoringProfile,
) -> CompanyTargetResult | None:
    """Build complete target estimates for one company.

    Order of operations:
      1. Pre-compute the per-name CAPM cost of equity and the quality flags
         so they can be passed to the discount-aware lenses and the blender.
      2. Run all seven lenses independently (each silently emits zero
         estimates when the underlying data is missing).
      3. Blend per horizon with the configured weights, lens-coverage
         normalisation, and quality-bias-aware scenario bands.
      4. Bundle the result with valuation, analyst, and quality side-data.
    """
    close = _coerce_numeric(row.get("close"))
    if close is None or close <= 0:
        return None

    market_cap = _coerce_numeric(row.get("market_cap_basic"))
    symbol = _get_symbol_name(row)
    company_name = _get_company_name(row)
    industry = str(row.get("industry") or "N/A")

    # Component scores from prediction module.
    component_scores = _build_component_scores(
        row, derived_row, profiles, scoring_profile
    )

    # Per-name discount rate + fundamental-quality flags (shared inputs).
    cost_of_equity = _cost_of_equity(row)
    quality_flags = _build_quality_flags(row)

    # Lens 1: Multiple-anchored (peer-reversion across 10 multiples).
    multiple_estimates = _multiple_anchored_targets(row, peer_multiple_stats, close)

    # Lens 2: Technical-anchored (MAs, BBs, classic + Camarilla pivots).
    technical_estimates, envelope = _technical_anchored_targets(row, close)

    # Lens 3: Fundamental-trajectory (forward fundamentals, SGR-capped,
    # CAPM-discounted on long horizon).
    trajectory_estimates = _trajectory_targets(
        row, peer_multiple_stats, close, market_cap, cost_of_equity
    )

    # Lens 4: Range / mean-reversion anchor.
    range_estimates = _range_anchored_targets(row, close)

    # Lens 5: Analyst consensus targets.
    analyst_estimates = _analyst_consensus_targets(row, close)

    # Lens 6: Book-value / Graham-style anchor.
    book_value_estimates = _book_value_targets(row, peer_multiple_stats, close)

    # Lens 7: Yield / dividend-discount model (only for income payers).
    yield_dcf_estimates = _yield_dcf_targets(row, close, cost_of_equity)

    # Blend into horizon targets.
    horizon_targets: dict[str, HorizonTarget] = {}
    for horizon in TARGET_HORIZONS:
        horizon_targets[horizon] = _blend_horizon_target(
            horizon=horizon,
            close=close,
            multiple_estimates=multiple_estimates,
            technical_estimates=technical_estimates,
            trajectory_estimates=trajectory_estimates,
            range_estimates=range_estimates,
            analyst_estimates=analyst_estimates,
            book_value_estimates=book_value_estimates,
            yield_dcf_estimates=yield_dcf_estimates,
            component_scores=component_scores,
            quality_flags=quality_flags,
        )

    # Side-data summaries.
    valuation_summary = _build_valuation_summary(row, peer_multiple_stats)
    analyst_summary = _build_analyst_summary(row, close)

    return CompanyTargetResult(
        symbol=symbol,
        company_name=company_name,
        industry=industry,
        close=close,
        market_cap=market_cap,
        horizon_targets=horizon_targets,
        valuation_summary=valuation_summary,
        technical_envelope=envelope,
        component_scores=component_scores,
        raw_row=row,
        quality_flags=quality_flags,
        analyst_summary=analyst_summary,
        cost_of_equity=cost_of_equity,
    )


# ---------------------------------------------------------------------------
# File naming
# ---------------------------------------------------------------------------


def _build_report_file_name(
    report_slug: str,
    industries: list[str] | str | None,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
    output_dir: Path | None = None,
) -> Path:
    base_output_dir = output_dir or LOG_DIR
    industries_list = _normalize_industries(industries)
    industry_segment = "all_industries"
    if industries_list:
        industry_segment = "__".join(_slugify(i) for i in industries_list)
    min_seg = (
        f"min_{int(min_market_cap_usd)}"
        if min_market_cap_usd is not None
        else "min_none"
    )
    max_seg = (
        f"max_{int(max_market_cap_usd)}"
        if max_market_cap_usd is not None
        else "max_none"
    )
    return (
        base_output_dir / f"{report_slug}__{industry_segment}__{min_seg}__{max_seg}.log"
    )


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
    return safe_name.rstrip(". ") or _slugify(industry)


# ---------------------------------------------------------------------------
# Report logging
# ---------------------------------------------------------------------------


def _log_methodology(log_file: Path) -> None:
    log_to_file(log_file, "Methodology")
    log_to_file(log_file, "-" * 160)
    log_to_file(
        log_file,
        "This report estimates forward price targets using SEVEN independent lenses blended per horizon. "
        "Each lens contributes evidence from a different angle (peer valuation, tape, fundamentals, range, "
        "analyst consensus, book value, dividend yield); the blender weights them per horizon and tilts the "
        "scenario bands using component scores, fundamental quality flags, current-cycle risk flags, "
        "lens dispersion, and analyst spread.",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        "Lens 1 -- Multiple-anchored: peer-median reversion across ten standard valuation multiples "
        "(P/E TTM, P/S, P/B, P/FCF, P/OCF, EV/Revenue, EV/EBIT, EV/EBITDA, EV/FCF, EV/GP). "
        "implied_price = current_price * (peer_median / company_multiple). Peer medians use a 5% robust trim "
        "when at least 20 peers are available. Confidence saturates at ~20 peers.",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        "Lens 2 -- Technical envelope: SMA/EMA 10-200, VWAP, VWMA, Bollinger Bands, classic + Camarilla pivots "
        "(S1/S2/R1/R2), and 1M/3M/6M/52W high-low ranges. Anchors are filtered per horizon (short MAs near-term, "
        "SMA50/EMA50 medium, SMA200/EMA200 long) and summarised into percentile bear/base/bull levels.",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        "Lens 3 -- Fundamental trajectory: project EPS, revenue, and EBITDA forward using the company's own growth "
        "rates (forward EPS preferred, then revenue YoY, then earnings YoY). Growth is hard-clamped to "
        f"[{MIN_GROWTH_RATE * 100:.0f}%, +{MAX_GROWTH_RATE * 100:.0f}%] and soft-capped at "
        f"{SUSTAINABLE_GROWTH_CAP_MULTIPLIER:.1f}x the company's sustainable growth rate (ROE * retention proxy). "
        "Long-horizon (>= 1.5 yr) projections are CAPM-discounted: k = risk_free + beta * ERP, "
        f"clamped to [{MIN_DISCOUNT_RATE * 100:.0f}%, {MAX_DISCOUNT_RATE * 100:.0f}%] "
        f"using rf={DEFAULT_RISK_FREE_RATE * 100:.1f}% and ERP={DEFAULT_EQUITY_RISK_PREMIUM * 100:.1f}%.",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        "Lens 4 -- Range / mean-reversion: 6-month + 52-week range. Stocks pinned at extremes are pulled "
        "toward the midpoint with strength = extremity * horizon_pull (0.30 near, 0.45 medium, 0.55 long). "
        "Caps overextension on momentum names; supports oversold names.",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        "Lens 5 -- Analyst consensus: TradingView's bundled price_target_low / median (or average) / high / 1y "
        "are used directly as bear/base/bull/long-term anchors. Confidence is scaled by AnalystRating "
        "(Strong Buy ~1.0; Sell ~0.55). Silent when no targets are published.",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        "Lens 6 -- Book value / Graham: Graham number sqrt(22.5 * EPS * BVPS) plus peer-median P/B reversion "
        "applied to BVPS. Acts as a value floor for asset-heavy businesses; silent for negative-equity firms "
        "and persistent loss-makers.",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        "Lens 7 -- Yield / dividend discount: Gordon DDM ( P = D1 / (k - g) ) using next-period DPS and the "
        "CAPM cost of equity, plus shareholder-yield reversion ( yield_$ / k ) which adds buybacks to "
        "dividends and reverts the total to k. Both anchors are silent for non-payers.",
    )
    log_to_file(log_file, "")

    # Lens-weight reference table.
    log_to_file(log_file, "Per-horizon lens weight matrix (each row sums to 1.0):")
    header = (
        f"  {'Horizon':<14}"
        + "".join(f"{LENS_LABELS[l][:11]:>13}" for l in LENS_ORDER)
        + f"{'Total':>10}"
    )
    log_to_file(log_file, header)
    for horizon in TARGET_HORIZONS:
        weights = DEFAULT_LENS_WEIGHTS[horizon]
        weight_str = "".join(f"{weights.get(l, 0.0):>12.0%} " for l in LENS_ORDER)
        total = sum(weights.values())
        log_to_file(log_file, f"  {horizon:<14}{weight_str}{total:>9.0%}")
    log_to_file(log_file, "")

    log_to_file(
        log_file,
        f"Scenario bands: bear={SCENARIO_MULTIPLIERS['bear']:.0%}, "
        f"bull={SCENARIO_MULTIPLIERS['bull']:.0%}, then adjusted by:\n"
        f"  - component scores  (quality+safety -> tighter bear; momentum+trend -> wider bull)\n"
        f"  - quality_bias      (strong fundamentals lift bear; weak fundamentals lower bear and bull)\n"
        f"  - cycle_risk_bias   (duration valuation, deterioration, cash burn, liquidity decay lower bear/bull)\n"
        f"  - lens dispersion   (max-min lens spread / base * 0.15, capped at +-10pp)\n"
        f"  - analyst spread    (analyst high-low / base * 0.10, capped at +-8pp)",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        f"Quality flags fire on Piotroski F-score >= {PIOTROSKI_STRONG} (strong) or <= {PIOTROSKI_WEAK} (weak), "
        f"Altman Z >= {ALTMAN_SAFE} (safe) or <= {ALTMAN_DISTRESS} (distress), "
        f"and Debt/EBITDA > {DEBT_TO_EBITDA_HIGH} (over-levered).",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        f"Opportunity score: base_upside * (0.6 + 0.4 * lens_coverage). "
        f"Strong Opportunity >= {STRONG_OPPORTUNITY_THRESHOLD:.0f}% | "
        f"Moderate Opportunity >= {MODERATE_OPPORTUNITY_THRESHOLD:.0f}% | Fair Value Range in between | "
        f"Moderate Overvaluation <= -{MODERATE_OPPORTUNITY_THRESHOLD:.0f}% | "
        f"Strong Overvaluation <= -{STRONG_OPPORTUNITY_THRESHOLD:.0f}%.",
    )
    log_to_file(
        log_file,
        f"Blend outlier control: raw upside anchors above {MAX_UPSIDE_BLEND_MULTIPLE:.1f}x close are capped only "
        "for blended-price contribution and are still exported raw in the lens-case CSV. Downside anchors are not capped.",
    )
    log_to_file(log_file, "")


def _log_peer_multiple_summary(
    log_file: Path,
    peer_multiple_stats: dict[str, dict[str, float | None]],
) -> None:
    log_to_file(log_file, "Peer-group valuation multiple distribution")
    log_to_file(log_file, "-" * 160)
    log_to_file(
        log_file,
        f"{'Multiple':<20} {'Raw':>8} {'Used':>8} {'Trim':>8} "
        f"{'P25':>12} {'Median':>12} {'P75':>12} {'Mean':>12}",
    )
    log_to_file(log_file, "-" * 104)
    for field_name, label in MULTIPLE_FIELDS:
        stats = peer_multiple_stats.get(field_name, {})
        count = stats.get("count") or 0
        raw_count = stats.get("raw_count") or count
        trimmed_count = stats.get("trimmed_count") or 0
        log_to_file(
            log_file,
            f"{label:<20} {raw_count:>8} {count:>8} {trimmed_count:>8} "
            f"{_format_multiple(stats.get('p25')):>12} "
            f"{_format_multiple(stats.get('median')):>12} {_format_multiple(stats.get('p75')):>12} "
            f"{_format_multiple(stats.get('mean')):>12}",
        )
    log_to_file(log_file, "")


def _log_horizon_targets_section(
    log_file: Path,
    results: list[CompanyTargetResult],
    horizon: str,
) -> None:
    label = TARGET_HORIZON_LABELS.get(horizon, horizon)
    log_to_file(log_file, f"Target estimates — {label}")
    log_to_file(log_file, "-" * 160)

    # Sort by opportunity score descending.
    scored_results = [
        r for r in results if r.horizon_targets[horizon].opportunity_score is not None
    ]
    scored_results.sort(
        key=lambda r: _sort_key_desc(r.horizon_targets[horizon].opportunity_score),
        reverse=True,
    )

    log_to_file(
        log_file,
        f"{'Ticker':<12} {'Company':<24} {'Industry':<22} {'MCap':>10} {'Close':>10} "
        f"{'Bear':>10} {'Base':>10} {'Bull':>10} {'Upside':>10} {'OppScore':>10} {'Label':<22} {'Cov':>6}",
    )
    log_to_file(log_file, "-" * 160)

    # Top opportunities (upside).
    log_to_file(log_file, "Top opportunities (highest implied upside)")
    for result in scored_results[:TOP_SECTION_ROWS]:
        ht = result.horizon_targets[horizon]
        log_to_file(
            log_file,
            f"{result.symbol:<12} {result.company_name[:24]:<24} {result.industry[:22]:<22} "
            f"{_format_market_cap(result.market_cap):>10} {_format_price(result.close):>10} "
            f"{_format_price(ht.bear_price):>10} {_format_price(ht.base_price):>10} {_format_price(ht.bull_price):>10} "
            f"{_format_percent(ht.base_upside_pct):>10} {_format_percent(ht.opportunity_score):>10} "
            f"{ht.opportunity_label[:22]:<22} {f'{ht.coverage:.0%}':>6}",
        )

    log_to_file(log_file, "")
    log_to_file(log_file, "Most overvalued (largest implied downside)")
    overvalued = (
        scored_results[-TOP_SECTION_ROWS:]
        if len(scored_results) > TOP_SECTION_ROWS
        else []
    )
    overvalued = sorted(
        overvalued, key=lambda r: r.horizon_targets[horizon].opportunity_score or 0
    )
    for result in overvalued[:TOP_SECTION_ROWS]:
        ht = result.horizon_targets[horizon]
        log_to_file(
            log_file,
            f"{result.symbol:<12} {result.company_name[:24]:<24} {result.industry[:22]:<22} "
            f"{_format_market_cap(result.market_cap):>10} {_format_price(result.close):>10} "
            f"{_format_price(ht.bear_price):>10} {_format_price(ht.base_price):>10} {_format_price(ht.bull_price):>10} "
            f"{_format_percent(ht.base_upside_pct):>10} {_format_percent(ht.opportunity_score):>10} "
            f"{ht.opportunity_label[:22]:<22} {f'{ht.coverage:.0%}':>6}",
        )
    log_to_file(log_file, "")


def _log_valuation_comparison_section(
    log_file: Path,
    results: list[CompanyTargetResult],
) -> None:
    """Log a section showing each company's multiples vs peer medians."""
    log_to_file(log_file, "Valuation multiple comparison vs peers")
    log_to_file(log_file, "-" * 160)

    # Show top 30 by market cap.
    sorted_by_mcap = sorted(
        results,
        key=lambda r: _sort_key_desc(r.market_cap),
        reverse=True,
    )[:TOP_SECTION_ROWS]

    header_multiples = ["P/E", "P/S", "EV/EBITDA", "P/FCF"]
    header = f"{'Ticker':<12} {'Company':<24} {'MCap':>10}"
    for m in header_multiples:
        header += f" {m+' Co':>10} {m+' Peer':>10} {'vs%':>8}"
    log_to_file(log_file, header)
    log_to_file(log_file, "-" * 160)

    for result in sorted_by_mcap:
        vs = result.valuation_summary
        line = (
            f"{result.symbol:<12} {result.company_name[:24]:<24} "
            f"{_format_market_cap(result.market_cap):>10}"
        )
        for m_label in header_multiples:
            co_val = vs.get(f"{m_label} TTM_company") or vs.get(f"{m_label}_company")
            peer_val = vs.get(f"{m_label} TTM_peer_median") or vs.get(
                f"{m_label}_peer_median"
            )
            vs_pct = vs.get(f"{m_label} TTM_vs_peer") or vs.get(f"{m_label}_vs_peer")
            line += f" {_format_multiple(co_val):>10} {_format_multiple(peer_val):>10} {_format_percent(vs_pct):>8}"
        log_to_file(log_file, line)
    log_to_file(log_file, "")


def _log_consensus_targets(
    log_file: Path,
    results: list[CompanyTargetResult],
) -> None:
    """Log companies where all horizons agree on direction."""
    log_to_file(log_file, "Consensus targets — all horizons")
    log_to_file(log_file, "-" * 160)

    all_upside = [
        r
        for r in results
        if all(
            (r.horizon_targets[h].base_upside_pct or 0) > MODERATE_OPPORTUNITY_THRESHOLD
            for h in TARGET_HORIZONS
            if r.horizon_targets[h].base_upside_pct is not None
        )
        and any(
            r.horizon_targets[h].base_upside_pct is not None for h in TARGET_HORIZONS
        )
    ]

    all_downside = [
        r
        for r in results
        if all(
            (r.horizon_targets[h].base_upside_pct or 0)
            < -MODERATE_OPPORTUNITY_THRESHOLD
            for h in TARGET_HORIZONS
            if r.horizon_targets[h].base_upside_pct is not None
        )
        and any(
            r.horizon_targets[h].base_upside_pct is not None for h in TARGET_HORIZONS
        )
    ]

    log_to_file(
        log_file, f"Upside consensus across all horizons ({len(all_upside)} names)"
    )
    if all_upside:
        all_upside.sort(
            key=lambda r: r.horizon_targets["medium_term"].base_upside_pct or 0,
            reverse=True,
        )
        for r in all_upside[:20]:
            nt = r.horizon_targets["near_term"]
            mt = r.horizon_targets["medium_term"]
            lt = r.horizon_targets["long_term"]
            log_to_file(
                log_file,
                f"  {r.symbol:<12} {r.company_name[:24]:<24} "
                f"NT={_format_percent(nt.base_upside_pct)} MT={_format_percent(mt.base_upside_pct)} "
                f"LT={_format_percent(lt.base_upside_pct)} | "
                f"mcap={_format_market_cap(r.market_cap)} close={_format_price(r.close)}",
            )
    else:
        log_to_file(log_file, "  none")
    log_to_file(log_file, "")

    log_to_file(
        log_file, f"Downside consensus across all horizons ({len(all_downside)} names)"
    )
    if all_downside:
        all_downside.sort(
            key=lambda r: r.horizon_targets["medium_term"].base_upside_pct or 0,
        )
        for r in all_downside[:20]:
            nt = r.horizon_targets["near_term"]
            mt = r.horizon_targets["medium_term"]
            lt = r.horizon_targets["long_term"]
            log_to_file(
                log_file,
                f"  {r.symbol:<12} {r.company_name[:24]:<24} "
                f"NT={_format_percent(nt.base_upside_pct)} MT={_format_percent(mt.base_upside_pct)} "
                f"LT={_format_percent(lt.base_upside_pct)} | "
                f"mcap={_format_market_cap(r.market_cap)} close={_format_price(r.close)}",
            )
    else:
        log_to_file(log_file, "  none")
    log_to_file(log_file, "")


def _log_component_quality_overlay(
    log_file: Path,
    results: list[CompanyTargetResult],
) -> None:
    """Cross-reference target upside with prediction-module component scores."""
    log_to_file(
        log_file, "Target-quality overlay (medium-term upside + component scores)"
    )
    log_to_file(log_file, "-" * 160)

    scored = [
        r
        for r in results
        if r.horizon_targets["medium_term"].base_upside_pct is not None
    ]
    scored.sort(
        key=lambda r: r.horizon_targets["medium_term"].base_upside_pct or 0,
        reverse=True,
    )

    log_to_file(
        log_file,
        f"{'Ticker':<12} {'Company':<24} {'MT Upside':>10} "
        f"{'Qual':>8} {'Value':>8} {'Safe':>8} {'Mom':>8} {'Trend':>8} {'Attn':>8} {'Label':<22}",
    )
    log_to_file(log_file, "-" * 160)

    for r in scored[:TOP_SECTION_ROWS]:
        mt = r.horizon_targets["medium_term"]
        cs = r.component_scores
        log_to_file(
            log_file,
            f"{r.symbol:<12} {r.company_name[:24]:<24} {_format_percent(mt.base_upside_pct):>10} "
            f"{_format_score(cs.get('quality')):>8} {_format_score(cs.get('valuation')):>8} "
            f"{_format_score(cs.get('safety')):>8} {_format_score(cs.get('momentum')):>8} "
            f"{_format_score(cs.get('trend')):>8} {_format_score(cs.get('attention')):>8} "
            f"{mt.opportunity_label[:22]:<22}",
        )
    log_to_file(log_file, "")


def _log_lens_decomposition_section(
    log_file: Path,
    results: list[CompanyTargetResult],
    horizon: str,
) -> None:
    """Decompose the medium-term blended base price into per-lens contributions.

    For each top opportunity, show the price each lens implied so users can
    see exactly which lens(es) drove the blend (transparency on component
    weight in the final forecast).
    """
    label = TARGET_HORIZON_LABELS.get(horizon, horizon)
    log_to_file(log_file, f"Lens decomposition -- {label} (per-lens implied price)")
    log_to_file(log_file, "-" * 160)

    scored = [r for r in results if r.horizon_targets[horizon].base_price is not None]
    scored.sort(
        key=lambda r: r.horizon_targets[horizon].opportunity_score or 0,
        reverse=True,
    )

    weights = DEFAULT_LENS_WEIGHTS[horizon]

    # Header row showing weights so users see "this lens contributed X% of the blend".
    weight_line = f"{'Configured weights':<24}{'Base':>10}"
    for lens in LENS_ORDER:
        weight_line += (
            f"{LENS_LABELS[lens][:9]+'(' + f'{weights.get(lens, 0):.0%}'+')':>14}"
        )
    log_to_file(log_file, weight_line)
    log_to_file(log_file, "-" * 160)

    for r in scored[:TOP_SECTION_ROWS]:
        ht = r.horizon_targets[horizon]
        line = f"{(r.symbol + ' ' + r.company_name[:14]):<24}{_format_price(ht.base_price):>10}"
        for lens in LENS_ORDER:
            price = ht.lens_prices.get(lens)
            line += f"{_format_price(price):>14}"
        log_to_file(log_file, line)
    log_to_file(
        log_file,
        f"  (Lens dispersion = max-min lens spread / base; widens scenario fan when lenses disagree.)",
    )
    log_to_file(log_file, "")


def _log_analyst_vs_blend_section(
    log_file: Path,
    results: list[CompanyTargetResult],
) -> None:
    """Surface companies where the model's blended view diverges from the street.

    A divergence > 30 pp on the long-horizon base upside indicates the model
    sees something analysts don't (or vice-versa) -- worth manual review.
    """
    log_to_file(
        log_file,
        "Analyst-vs-Model divergence (long-term blended base vs analyst median 1y)",
    )
    log_to_file(log_file, "-" * 160)

    DIVERGENCE_THRESHOLD = 30.0

    rows: list[tuple[float, CompanyTargetResult, float, float]] = []
    for r in results:
        asum = r.analyst_summary or {}
        analyst_upside = asum.get("analyst_base_upside_pct")
        long_term = r.horizon_targets["long_term"]
        model_upside = long_term.base_upside_pct
        if isinstance(analyst_upside, (int, float)) and model_upside is not None:
            divergence = model_upside - analyst_upside
            if abs(divergence) >= DIVERGENCE_THRESHOLD:
                rows.append((divergence, r, model_upside, float(analyst_upside)))

    if not rows:
        log_to_file(log_file, "  no names with divergence >= 30 pp")
        log_to_file(log_file, "")
        return

    rows.sort(key=lambda x: abs(x[0]), reverse=True)
    log_to_file(
        log_file,
        f"{'Ticker':<12} {'Company':<24} {'Rating':<14} "
        f"{'Model LT':>10} {'Analyst':>10} {'Divergence':>12} {'Direction':<24}",
    )
    log_to_file(log_file, "-" * 160)
    for divergence, r, model_up, analyst_up in rows[:TOP_SECTION_ROWS]:
        rating = (r.analyst_summary or {}).get("analyst_rating") or "-"
        direction = "Model more bullish" if divergence > 0 else "Model more bearish"
        log_to_file(
            log_file,
            f"{r.symbol:<12} {r.company_name[:24]:<24} {str(rating)[:14]:<14} "
            f"{_format_percent(model_up):>10} {_format_percent(analyst_up):>10} "
            f"{_format_percent(divergence):>12} {direction:<24}",
        )
    log_to_file(log_file, "")


def _log_quality_flags_section(
    log_file: Path,
    results: list[CompanyTargetResult],
) -> None:
    """Surface the strongest and weakest fundamentals across the universe."""
    log_to_file(
        log_file,
        "Fundamental quality flags (Piotroski / Altman Z / Debt-EBITDA -> bias)",
    )
    log_to_file(log_file, "-" * 160)

    flagged = [
        r
        for r in results
        if (r.quality_flags or {}).get("quality_bias")
        or (r.quality_flags or {}).get("cycle_risk_bias")
    ]
    if not flagged:
        log_to_file(log_file, "  no companies triggered quality or cycle-risk flags")
        log_to_file(log_file, "")
        return

    strongest = sorted(
        flagged, key=lambda r: r.quality_flags.get("quality_bias", 0), reverse=True
    )[:TOP_SECTION_ROWS]
    weakest = sorted(
        flagged,
        key=lambda r: r.quality_flags.get("quality_bias", 0)
        + r.quality_flags.get("cycle_risk_bias", 0),
    )[:TOP_SECTION_ROWS]

    header = (
        f"{'Ticker':<12} {'Company':<24} "
        f"{'Piotroski':>10} {'Altman Z':>10} {'D/EBITDA':>10} "
        f"{'Bias':>6} {'Cycle':>6} {'Flags':<40}"
    )

    log_to_file(log_file, "Strongest quality (positive bias tightens bear band):")
    log_to_file(log_file, header)
    log_to_file(log_file, "-" * 160)
    for r in strongest:
        qf = r.quality_flags or {}
        log_to_file(
            log_file,
            f"{r.symbol:<12} {r.company_name[:24]:<24} "
            f"{_format_score(qf.get('piotroski')):>10} "
            f"{_format_score(qf.get('altman_z')):>10} "
            f"{_format_score(qf.get('debt_to_ebitda')):>10} "
            f"{qf.get('quality_bias', 0):>+6} "
            f"{qf.get('cycle_risk_bias', 0):>+6} "
            f"{('|'.join(qf.get('flags', []) or []))[:40]:<40}",
        )

    log_to_file(log_file, "")
    log_to_file(log_file, "Weakest quality (negative bias widens bear band):")
    log_to_file(log_file, header)
    log_to_file(log_file, "-" * 160)
    for r in weakest:
        qf = r.quality_flags or {}
        log_to_file(
            log_file,
            f"{r.symbol:<12} {r.company_name[:24]:<24} "
            f"{_format_score(qf.get('piotroski')):>10} "
            f"{_format_score(qf.get('altman_z')):>10} "
            f"{_format_score(qf.get('debt_to_ebitda')):>10} "
            f"{qf.get('quality_bias', 0):>+6} "
            f"{qf.get('cycle_risk_bias', 0):>+6} "
            f"{('|'.join(qf.get('flags', []) or []))[:40]:<40}",
        )
    log_to_file(log_file, "")


def _target_estimate_input_values(
    result: CompanyTargetResult,
    estimate: TargetEstimate,
    peer_multiple_stats: dict[str, dict[str, float | None]],
) -> dict[str, Any]:
    """Return the raw inputs used by one target case."""
    row = result.raw_row
    inputs: dict[str, Any] = {
        "close": result.close,
        "market_cap_basic": result.market_cap,
    }

    def add_fields(field_names: list[str]) -> None:
        for field_name in field_names:
            inputs[field_name] = row.get(field_name)

    if estimate.lens == "multiple":
        multiple_field = next(
            (
                field_name
                for field_name, label in MULTIPLE_FIELDS
                if label == estimate.label
            ),
            None,
        )
        if multiple_field:
            add_fields([multiple_field])
            stats = peer_multiple_stats.get(multiple_field, {})
            inputs[f"peer_median_{multiple_field}"] = stats.get("median")
            inputs[f"peer_used_count_{multiple_field}"] = stats.get("count")
            inputs[f"peer_trimmed_count_{multiple_field}"] = stats.get("trimmed_count")
    elif estimate.lens == "technical":
        scope = _estimate_horizon_scope(estimate)
        technical_fields = {
            "near_term": [
                "SMA10",
                "SMA20",
                "EMA10",
                "EMA20",
                "BB.upper",
                "BB.lower",
                "Pivot.M.Classic.Middle",
                "Pivot.M.Camarilla.S1",
                "Pivot.M.Camarilla.S2",
                "Pivot.M.Camarilla.R1",
                "Pivot.M.Camarilla.R2",
                "High.1M",
                "Low.1M",
                "VWAP",
            ],
            "medium_term": [
                "SMA30",
                "SMA50",
                "EMA30",
                "EMA50",
                "VWMA",
                "High.3M",
                "Low.3M",
                "High.6M",
                "Low.6M",
            ],
            "long_term": [
                "SMA200",
                "EMA200",
                "price_52_week_high",
                "price_52_week_low",
            ],
        }
        add_fields(technical_fields.get(scope, TECHNICAL_ANCHOR_FIELDS))
    elif estimate.lens == "trajectory":
        add_fields(
            [
                "earnings_per_share_forecast_next_fq",
                "earnings_per_share_fq",
                "earnings_per_share_diluted_yoy_growth_ttm",
                "total_revenue_yoy_growth_ttm",
                "ebitda_yoy_growth_ttm",
                "net_income_yoy_growth_ttm",
                "free_cash_flow_yoy_growth_ttm",
                "sustainable_growth_rate_ttm",
                "total_revenue",
                "ebitda",
                "net_debt",
                "price_earnings_ttm",
                "price_sales_current",
                "enterprise_value_ebitda_ttm",
            ]
        )
        inputs["cost_of_equity"] = result.cost_of_equity
    elif estimate.lens == "range":
        if "near" in estimate.label:
            add_fields(["Low.6M", "High.6M"])
        else:
            add_fields(["price_52_week_low", "price_52_week_high"])
    elif estimate.lens == "analyst":
        add_fields(
            [
                "price_target_low",
                "price_target_median",
                "price_target_average",
                "price_target_high",
                "price_target_1y",
                "AnalystRating",
            ]
        )
    elif estimate.lens == "book_value":
        add_fields(
            [
                "book_value_per_share_fq",
                "earnings_per_share_diluted_ttm",
                "earnings_per_share_basic_ttm",
                "last_annual_eps",
                "price_book_fq",
            ]
        )
        stats = peer_multiple_stats.get("price_book_fq", {})
        inputs["peer_median_price_book_fq"] = stats.get("median")
    elif estimate.lens == "yield_dcf":
        add_fields(
            [
                "dividends_per_share_fq",
                "dividends_yield_current",
                "dividend_yield_recent",
                "buyback_yield",
                "dps_common_stock_prim_issue_yoy_growth_fy",
                "beta_1_year",
                "beta_3_year",
                "beta_5_year",
            ]
        )
        inputs["cost_of_equity"] = result.cost_of_equity

    return inputs


def _target_case_lens_suitability(
    result: CompanyTargetResult,
    estimate: TargetEstimate,
    peer_multiple_stats: dict[str, dict[str, float | None]],
) -> dict[str, str]:
    row = result.raw_row
    profile = _build_company_profile(row, result.quality_flags)
    profile_tags = profile["investment_style_profile"].split("|")
    market_cap_bucket = profile["market_cap_bucket"]
    industry = str(result.industry or "").lower()
    relative_volume = _coerce_numeric(row.get("relative_volume_10d_calc"))
    price_book = _coerce_numeric(row.get("price_book_fq"))
    dividend_yield = _coerce_numeric(
        row.get("dividends_yield_current")
    ) or _coerce_numeric(row.get("dividend_yield_recent"))
    buyback_yield = _coerce_numeric(row.get("buyback_yield"))
    total_shareholder_yield = (dividend_yield or 0.0) + (buyback_yield or 0.0)
    lens_fit = "medium"
    tags: list[str] = []

    if estimate.lens == "technical":
        if (
            "momentum_leader" in profile_tags
            or "oversold_recovery_candidate" in profile_tags
        ):
            lens_fit = "high"
            tags.append("tape_state_informative")
        if (
            relative_volume is not None
            and relative_volume < LOW_RELATIVE_VOLUME_THRESHOLD
        ):
            lens_fit = "caution"
            tags.append("weak_volume_confirmation")
    elif estimate.lens == "multiple":
        multiple_field = next(
            (
                field_name
                for field_name, label in MULTIPLE_FIELDS
                if label == estimate.label
            ),
            None,
        )
        peer_count = (
            peer_multiple_stats.get(multiple_field, {}).get("count")
            if multiple_field
            else 0
        )
        if peer_count and peer_count >= 10:
            lens_fit = "high"
            tags.append("adequate_peer_count")
        if (
            "expensive_duration_growth" in profile_tags
            or "fragile_balance_sheet_or_quality" in profile_tags
        ):
            tags.append("valuation_risk_relevant")
        if peer_count and peer_count < 5:
            lens_fit = "caution"
            tags.append("thin_peer_multiple_set")
    elif estimate.lens == "trajectory":
        if (
            "quality_compounder" in profile_tags
            or "profitable_or_scaling_growth" in profile_tags
        ):
            lens_fit = "high"
            tags.append("growth_path_matters")
        if "fundamental_deterioration" in profile["cycle_risk_flags"]:
            tags.append("deterioration_makes_downside_important")
    elif estimate.lens == "range":
        if (
            "momentum_leader" in profile_tags
            or "oversold_recovery_candidate" in profile_tags
        ):
            lens_fit = "high"
            tags.append("range_extremity_relevant")
    elif estimate.lens == "analyst":
        if market_cap_bucket in {"mega_cap", "large_cap", "mid_cap"}:
            lens_fit = "high"
            tags.append("likely_better_street_coverage")
        if result.analyst_summary.get("analyst_base_upside_pct") is None:
            lens_fit = "caution"
            tags.append("analyst_base_missing")
    elif estimate.lens == "book_value":
        asset_heavy = any(
            token in industry
            for token in ["bank", "financial", "insurance", "reit", "real estate"]
        )
        if asset_heavy or (price_book is not None and price_book <= 1.5):
            lens_fit = "high"
            tags.append("asset_value_relevant")
        elif "expensive_duration_growth" in profile_tags:
            lens_fit = "low"
            tags.append("book_value_less_relevant_for_duration_growth")
    elif estimate.lens == "yield_dcf":
        if total_shareholder_yield >= 4.0:
            lens_fit = "high"
            tags.append("shareholder_yield_relevant")
        else:
            lens_fit = "low"
            tags.append("low_or_no_yield")

    if not tags:
        tags.append("general_cross_check")
    return {"lens_fit": lens_fit, "lens_fit_tags": "|".join(tags)}


def _target_case_management_signal(
    estimate: TargetEstimate,
    horizon_target: HorizonTarget,
    blend_adjustment: str,
    lens_fit: str,
) -> str:
    upside = estimate.implied_upside_pct
    if upside is not None and upside <= SEVERE_DOWNSIDE_CASE_THRESHOLD:
        return "severe_downside_review_first"
    if upside is not None and upside <= DOWNSIDE_CASE_THRESHOLD:
        return "downside_risk_watch"
    if blend_adjustment:
        return "raw_upside_outlier_research_only"
    if (
        horizon_target.lens_dispersion is not None
        and horizon_target.lens_dispersion >= HIGH_LENS_DISPERSION_THRESHOLD
    ):
        return "size_with_caution_lens_disagreement"
    if (
        upside is not None
        and upside >= STRONG_OPPORTUNITY_THRESHOLD
        and lens_fit == "high"
    ):
        return "candidate_add_on_confirmation"
    if upside is not None and upside >= MODERATE_OPPORTUNITY_THRESHOLD:
        return "constructive_monitor"
    return "monitor"


def _build_lens_case_records(
    results: list[CompanyTargetResult],
    peer_multiple_stats: dict[str, dict[str, float | None]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for result in results:
        row = result.raw_row
        profile = _build_company_profile(row, result.quality_flags)
        for horizon in TARGET_HORIZONS:
            horizon_target = result.horizon_targets[horizon]
            configured_weights = DEFAULT_LENS_WEIGHTS[horizon]
            for estimate in horizon_target.lens_estimates:
                close = result.close or 0.0
                blend_price = _estimate_price_for_blend(estimate, close)
                blend_adjustment = _estimate_blend_adjustment(estimate, close)
                input_values = _target_estimate_input_values(
                    result, estimate, peer_multiple_stats
                )
                suitability = _target_case_lens_suitability(
                    result, estimate, peer_multiple_stats
                )
                management_signal = _target_case_management_signal(
                    estimate,
                    horizon_target,
                    blend_adjustment,
                    suitability["lens_fit"],
                )
                raw_upside = estimate.implied_upside_pct
                records.append(
                    {
                        "symbol": result.symbol,
                        "Company": result.company_name,
                        "industry": result.industry,
                        "sector": row.get("sector") or "",
                        "exchange": row.get("exchange") or "",
                        "market": row.get("market") or "",
                        "market_cap_basic": result.market_cap,
                        "market_cap_bucket": profile["market_cap_bucket"],
                        "investment_style_profile": profile["investment_style_profile"],
                        "cycle_risk_flags": profile["cycle_risk_flags"],
                        "horizon": horizon,
                        "horizon_label": TARGET_HORIZON_LABELS.get(horizon, horizon),
                        "estimate_scope": _estimate_horizon_scope(estimate),
                        "lens": estimate.lens,
                        "lens_label": estimate.label,
                        "case_role": _estimate_case_role(estimate),
                        "formula_summary": _estimate_formula_summary(estimate),
                        "input_fields": "|".join(input_values.keys()),
                        "input_values_json": json.dumps(
                            input_values,
                            ensure_ascii=True,
                            default=str,
                            sort_keys=True,
                        ),
                        "close": result.close,
                        "implied_price_raw": estimate.implied_price,
                        "blend_price_used": blend_price,
                        "blend_adjustment": blend_adjustment,
                        "implied_upside_pct_raw": raw_upside,
                        "case_confidence_weight": estimate.confidence_weight,
                        "configured_lens_weight": configured_weights.get(
                            estimate.lens, 0.0
                        ),
                        "lens_global_confidence": LENS_GLOBAL_CONFIDENCE.get(
                            estimate.lens, 1.0
                        ),
                        "aggregated_lens_price": horizon_target.lens_prices.get(
                            estimate.lens
                        ),
                        "blended_bear_price": horizon_target.bear_price,
                        "blended_base_price": horizon_target.base_price,
                        "blended_bull_price": horizon_target.bull_price,
                        "blended_base_upside_pct": horizon_target.base_upside_pct,
                        "lens_dispersion": horizon_target.lens_dispersion,
                        "opportunity_score": horizon_target.opportunity_score,
                        "opportunity_label": horizon_target.opportunity_label,
                        "downside_case_flag": bool(
                            raw_upside is not None
                            and raw_upside <= DOWNSIDE_CASE_THRESHOLD
                        ),
                        "severe_downside_flag": bool(
                            raw_upside is not None
                            and raw_upside <= SEVERE_DOWNSIDE_CASE_THRESHOLD
                        ),
                        "lens_fit": suitability["lens_fit"],
                        "lens_fit_tags": suitability["lens_fit_tags"],
                        "management_signal": management_signal,
                    }
                )
    return records


def _build_lens_case_csv_headers() -> list[str]:
    return [
        "symbol",
        "Company",
        "industry",
        "sector",
        "exchange",
        "market",
        "market_cap_basic",
        "market_cap_bucket",
        "investment_style_profile",
        "cycle_risk_flags",
        "horizon",
        "horizon_label",
        "estimate_scope",
        "lens",
        "lens_label",
        "case_role",
        "formula_summary",
        "input_fields",
        "input_values_json",
        "close",
        "implied_price_raw",
        "blend_price_used",
        "blend_adjustment",
        "implied_upside_pct_raw",
        "case_confidence_weight",
        "configured_lens_weight",
        "lens_global_confidence",
        "aggregated_lens_price",
        "blended_bear_price",
        "blended_base_price",
        "blended_bull_price",
        "blended_base_upside_pct",
        "lens_dispersion",
        "opportunity_score",
        "opportunity_label",
        "downside_case_flag",
        "severe_downside_flag",
        "lens_fit",
        "lens_fit_tags",
        "management_signal",
    ]


def _build_lens_case_csv_rows(
    records: list[dict[str, Any]], headers: list[str]
) -> list[list[str]]:
    return [
        [_csv_value(record.get(header)) for header in headers] for record in records
    ]


def _log_target_input_reference(log_file: Path) -> None:
    """Document target-price inputs in the main log."""
    log_to_file(log_file, "Target input reference and raw case export")
    log_to_file(log_file, "-" * 160)
    log_to_file(
        log_file,
        "The companion __lens_cases.csv file exports every raw target anchor by lens, horizon, ticker, "
        "input fields, input values, raw implied price, blend-used price, fit tags, and active-management signal.",
    )
    log_to_file(
        log_file,
        f"Upside outlier control: if a raw anchor is above {MAX_UPSIDE_BLEND_MULTIPLE:.1f}x close, "
        "only its contribution to the blended base is capped; the raw target remains visible in the case CSV. "
        "Downside anchors are not capped.",
    )
    log_to_file(log_file, "")
    rows = [
        (
            "multiple",
            "close, company multiple, robust-trimmed peer median/count",
            "close * peer_median / company_multiple",
            "Best when peer set is broad and company fundamentals are comparable; important downside signal when company multiple is rich vs peers.",
        ),
        (
            "technical",
            "SMA/EMA/VWAP/VWMA, Bollinger, pivots, 1M/3M/6M/52W ranges",
            "horizon-specific percentile envelope",
            "Best for entry/exit timing, overbought/oversold states, and active position sizing around tape structure.",
        ),
        (
            "trajectory",
            "EPS/revenue/EBITDA base, growth rates, SGR cap, current/peer multiples, CAPM k",
            "project fundamentals forward; discount long horizon",
            "Best for compounders and cyclical recoveries; deterioration flags make downside cases especially important.",
        ),
        (
            "range",
            "6M and 52W high/low, close position inside range",
            "pull close toward midpoint by extremity * horizon strength",
            "Useful for mean reversion, extended winners, and oversold recovery candidates.",
        ),
        (
            "analyst",
            "price_target_low/median/average/high/1y and AnalystRating",
            "street target used directly with rating-scaled confidence",
            "Most useful on liquid covered names; disagreement widens scenario bands.",
        ),
        (
            "book_value",
            "BVPS, EPS, peer P/B median",
            "Graham number and peer P/B applied to BVPS",
            "Best for asset-heavy, financial, REIT, insurance, and deep-value balance-sheet cases.",
        ),
        (
            "yield_dcf",
            "DPS, dividend yield, buyback yield, dividend growth, beta-derived CAPM k",
            "Gordon DDM and shareholder-yield reversion",
            "Best for income and buyback names; low/no-yield names are marked low fit.",
        ),
    ]
    log_to_file(
        log_file,
        f"{'Lens':<14} {'Inputs':<52} {'Formula':<48} {'Use / risk note'}",
    )
    log_to_file(log_file, "-" * 160)
    for lens, inputs, formula, note in rows:
        log_to_file(
            log_file,
            f"{lens:<14} {inputs[:52]:<52} {formula[:48]:<48} {note}",
        )
    log_to_file(log_file, "")


def _log_active_management_case_summary(
    log_file: Path,
    lens_case_csv_file: Path,
    records: list[dict[str, Any]],
    results: list[CompanyTargetResult],
) -> None:
    """Summarise lens cases that matter for active risk management."""
    log_to_file(log_file, "Active-management lens case summary")
    log_to_file(log_file, "-" * 160)

    lens_counts = {lens: 0 for lens in LENS_ORDER}
    cycle_flag_counts: dict[str, int] = {}
    for record in records:
        lens = str(record.get("lens") or "")
        if lens in lens_counts:
            lens_counts[lens] += 1
        for flag in str(record.get("cycle_risk_flags") or "").split("|"):
            if flag:
                cycle_flag_counts[flag] = cycle_flag_counts.get(flag, 0) + 1

    downside_count = sum(1 for record in records if record.get("downside_case_flag"))
    severe_downside_count = sum(
        1 for record in records if record.get("severe_downside_flag")
    )
    capped_count = sum(1 for record in records if record.get("blend_adjustment"))
    lens_count_text = ", ".join(
        f"{LENS_LABELS.get(lens, lens)}={count}" for lens, count in lens_counts.items()
    )
    log_to_file(log_file, f"Lens-case CSV exported to: {lens_case_csv_file}")
    log_to_file(log_file, f"Rows by lens: {lens_count_text}")
    log_to_file(
        log_file,
        f"Downside cases <= {DOWNSIDE_CASE_THRESHOLD:.0f}%: {downside_count} | "
        f"severe <= {SEVERE_DOWNSIDE_CASE_THRESHOLD:.0f}%: {severe_downside_count} | "
        f"raw upside anchors capped only for blend: {capped_count}",
    )
    if cycle_flag_counts:
        flag_text = ", ".join(
            f"{flag}={count}"
            for flag, count in sorted(
                cycle_flag_counts.items(), key=lambda item: item[1], reverse=True
            )[:10]
        )
        log_to_file(log_file, f"Current-cycle risk flags: {flag_text}")
    else:
        log_to_file(log_file, "Current-cycle risk flags: none triggered")
    log_to_file(log_file, "")

    numeric_records = [
        record
        for record in records
        if isinstance(record.get("implied_upside_pct_raw"), (int, float))
    ]
    worst_records = sorted(
        numeric_records, key=lambda record: float(record["implied_upside_pct_raw"])
    )
    unique_worst: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for record in worst_records:
        key = (
            str(record.get("symbol") or ""),
            str(record.get("lens") or ""),
            str(record.get("lens_label") or ""),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique_worst.append(record)
        if len(unique_worst) >= 15:
            break

    log_to_file(log_file, "Worst raw downside lens cases (raw anchors, not capped)")
    if unique_worst:
        log_to_file(
            log_file,
            f"{'Ticker':<12} {'Horizon':<12} {'Lens':<12} {'Case':<24} "
            f"{'RawTarget':>10} {'Upside':>10} {'Fit':<8} {'Signal':<32}",
        )
        log_to_file(log_file, "-" * 160)
        for record in unique_worst:
            log_to_file(
                log_file,
                f"{str(record.get('symbol') or ''):<12} "
                f"{str(record.get('horizon') or ''):<12} "
                f"{str(record.get('lens') or ''):<12} "
                f"{str(record.get('lens_label') or '')[:24]:<24} "
                f"{_format_price(record.get('implied_price_raw')):>10} "
                f"{_format_percent(record.get('implied_upside_pct_raw')):>10} "
                f"{str(record.get('lens_fit') or ''):<8} "
                f"{str(record.get('management_signal') or '')[:32]:<32}",
            )
    else:
        log_to_file(log_file, "  none")
    log_to_file(log_file, "")

    high_dispersion_rows: list[
        tuple[float, CompanyTargetResult, str, HorizonTarget]
    ] = []
    for result in results:
        for horizon in TARGET_HORIZONS:
            horizon_target = result.horizon_targets[horizon]
            if (
                horizon_target.lens_dispersion is not None
                and horizon_target.lens_dispersion >= HIGH_LENS_DISPERSION_THRESHOLD
            ):
                high_dispersion_rows.append(
                    (horizon_target.lens_dispersion, result, horizon, horizon_target)
                )
    high_dispersion_rows.sort(key=lambda item: item[0], reverse=True)

    log_to_file(log_file, "High lens-dispersion names (size with caution)")
    if high_dispersion_rows:
        log_to_file(
            log_file,
            f"{'Ticker':<12} {'Horizon':<12} {'Disp':>8} {'BaseUpside':>12} "
            f"{'Label':<24} {'RiskFlags':<48}",
        )
        log_to_file(log_file, "-" * 160)
        for dispersion, result, horizon, horizon_target in high_dispersion_rows[:20]:
            risk_flags = "|".join(result.quality_flags.get("flags", []) or [])
            log_to_file(
                log_file,
                f"{result.symbol:<12} {horizon:<12} {dispersion:>8.2f} "
                f"{_format_percent(horizon_target.base_upside_pct):>12} "
                f"{horizon_target.opportunity_label[:24]:<24} {risk_flags[:48]:<48}",
            )
    else:
        log_to_file(log_file, "  none")
    log_to_file(log_file, "")


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


def _build_csv_headers() -> list[str]:
    headers = [
        "symbol",
        "Company",
        "industry",
        "sector",
        "exchange",
        "market",
        "market_cap_basic",
        "market_cap_bucket",
        "investment_style_profile",
        "cycle_risk_flags",
        "close",
        "cost_of_equity",
    ]
    for horizon in TARGET_HORIZONS:
        label = (
            TARGET_HORIZON_LABELS[horizon]
            .split("(")[0]
            .strip()
            .replace(" ", "_")
            .replace("-", "_")
            .lower()
        )
        headers.extend(
            [
                f"{label}_bear_price",
                f"{label}_base_price",
                f"{label}_bull_price",
                f"{label}_bear_upside_pct",
                f"{label}_base_upside_pct",
                f"{label}_bull_upside_pct",
                f"{label}_opportunity_score",
                f"{label}_opportunity_label",
                f"{label}_coverage",
                f"{label}_lens_dispersion",
            ]
        )
        # Per-lens implied prices (transparency: how much each lens drove the blend).
        for lens in LENS_ORDER:
            headers.append(f"{label}_lens_{lens}_price")
    # Valuation multiples vs peer.
    for _field, label in MULTIPLE_FIELDS:
        headers.extend(
            [
                f"{label}_company",
                f"{label}_peer_median",
                f"{label}_vs_peer_pct",
            ]
        )
    # Component scores.
    for comp in COMPONENT_ORDER:
        headers.append(f"component_{comp}")
    # Analyst consensus snapshot.
    headers.extend(
        [
            "analyst_low",
            "analyst_median",
            "analyst_average",
            "analyst_high",
            "analyst_1y",
            "analyst_rating",
            "analyst_base_upside_pct",
        ]
    )
    # Fundamental quality flags.
    headers.extend(
        [
            "piotroski_f_score",
            "altman_z_score",
            "debt_to_ebitda",
            "quality_bias",
            "cycle_risk_bias",
            "quality_flags",
        ]
    )
    return headers


def _build_csv_row(result: CompanyTargetResult) -> list[str]:
    row = result.raw_row
    company_profile = _build_company_profile(row, result.quality_flags)
    csv_row = [
        result.symbol,
        result.company_name,
        result.industry,
        str(row.get("sector") or ""),
        str(row.get("exchange") or ""),
        str(row.get("market") or ""),
        str(result.market_cap or ""),
        company_profile["market_cap_bucket"],
        company_profile["investment_style_profile"],
        company_profile["cycle_risk_flags"],
        str(result.close or ""),
        str(result.cost_of_equity or ""),
    ]
    for horizon in TARGET_HORIZONS:
        ht = result.horizon_targets[horizon]
        csv_row.extend(
            [
                str(ht.bear_price or ""),
                str(ht.base_price or ""),
                str(ht.bull_price or ""),
                str(ht.bear_upside_pct or ""),
                str(ht.base_upside_pct or ""),
                str(ht.bull_upside_pct or ""),
                str(ht.opportunity_score or ""),
                ht.opportunity_label,
                str(ht.coverage),
                str(ht.lens_dispersion if ht.lens_dispersion is not None else ""),
            ]
        )
        for lens in LENS_ORDER:
            csv_row.append(str(ht.lens_prices.get(lens) or ""))
    for _field, label in MULTIPLE_FIELDS:
        vs = result.valuation_summary
        csv_row.extend(
            [
                str(vs.get(f"{label}_company") or ""),
                str(vs.get(f"{label}_peer_median") or ""),
                str(vs.get(f"{label}_vs_peer") or ""),
            ]
        )
    for comp in COMPONENT_ORDER:
        csv_row.append(str(result.component_scores.get(comp) or ""))
    asum = result.analyst_summary or {}
    csv_row.extend(
        [
            str(asum.get("analyst_low") or ""),
            str(asum.get("analyst_median") or ""),
            str(asum.get("analyst_average") or ""),
            str(asum.get("analyst_high") or ""),
            str(asum.get("analyst_1y") or ""),
            str(asum.get("analyst_rating") or ""),
            str(asum.get("analyst_base_upside_pct") or ""),
        ]
    )
    qf = result.quality_flags or {}
    csv_row.extend(
        [
            str(qf.get("piotroski") or ""),
            str(qf.get("altman_z") or ""),
            str(qf.get("debt_to_ebitda") or ""),
            str(qf.get("quality_bias") or ""),
            str(qf.get("cycle_risk_bias") or ""),
            "|".join(qf.get("flags", []) or []),
        ]
    )
    return csv_row


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_targets_scan(
    scan_data: list[dict[str, Any]],
    industries: list[str] | str | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    scoring_profile: str | ScoringProfile | None = None,
    output_dir: str | Path | None = None,
) -> Path:
    """Run the full forward-target estimation analysis on scan data.

    Parameters
    ----------
    scan_data:
        Mapped rows from ``ApiTradingViewClient.scan_global_market_move_prediction``.
    industries, min_market_cap_usd, max_market_cap_usd:
        Metadata for report labelling (the filtering is done at scan time).
    scoring_profile:
        Prediction-module scoring profile to use for component scores.
    output_dir:
        Override directory for report files.

    Returns
    -------
    Path to the generated log file.
    """
    industries_list = _normalize_industries(industries)
    resolved_profile = resolve_move_prediction_scoring_profile(scoring_profile)
    resolved_output_dir = Path(output_dir) if output_dir is not None else None

    log_file = _build_report_file_name(
        "tradingview_targets",
        industries_list,
        min_market_cap_usd,
        max_market_cap_usd,
        output_dir=resolved_output_dir,
    )
    csv_file = log_file.with_suffix(".csv")
    lens_case_csv_file = log_file.with_name(f"{log_file.stem}__lens_cases.csv")

    _reset_log_file(log_file)

    log_to_file(log_file, _build_report_title("TradingView forward target estimation"))
    log_to_file(log_file, "=" * 160)
    log_to_file(
        log_file,
        f"Rows returned: {len(scan_data)} | industries={industries_list or 'all'} | "
        f"min_market_cap_usd={min_market_cap_usd} | max_market_cap_usd={max_market_cap_usd} | "
        f"scoring_profile={resolved_profile.name}",
    )
    log_to_file(log_file, "")

    if not scan_data:
        log_to_file(log_file, "No rows returned for this scan.")
        return log_file

    # Pipeline.
    _enrich_with_peer_metrics(scan_data)
    derived_metrics = [_build_derived_metrics(row) for row in scan_data]
    profiles = _build_metric_profiles(scan_data, derived_metrics)
    peer_multiple_stats = _build_peer_multiple_stats(scan_data)

    # Build targets for each company.
    results: list[CompanyTargetResult] = []
    for row, derived_row in zip(scan_data, derived_metrics):
        result = _build_company_targets(
            row,
            derived_row,
            profiles,
            peer_multiple_stats,
            resolved_profile,
        )
        if result is not None:
            results.append(result)

    log_to_file(
        log_file,
        f"Target estimates generated for {len(results)} companies (out of {len(scan_data)} rows).",
    )
    log_to_file(log_file, "")

    lens_case_records = _build_lens_case_records(results, peer_multiple_stats)

    # Report sections.
    _log_methodology(log_file)
    _log_target_input_reference(log_file)
    _log_peer_multiple_summary(log_file, peer_multiple_stats)
    _log_active_management_case_summary(
        log_file, lens_case_csv_file, lens_case_records, results
    )

    for horizon in TARGET_HORIZONS:
        _log_horizon_targets_section(log_file, results, horizon)
        _log_lens_decomposition_section(log_file, results, horizon)

    _log_valuation_comparison_section(log_file, results)
    _log_consensus_targets(log_file, results)
    _log_analyst_vs_blend_section(log_file, results)
    _log_quality_flags_section(log_file, results)
    _log_component_quality_overlay(log_file, results)

    # CSV export.
    csv_headers = _build_csv_headers()
    csv_rows = [_build_csv_row(r) for r in results]
    log_rows_to_csv(csv_file, csv_headers, csv_rows)

    lens_case_headers = _build_lens_case_csv_headers()
    lens_case_rows = _build_lens_case_csv_rows(lens_case_records, lens_case_headers)
    log_rows_to_csv(lens_case_csv_file, lens_case_headers, lens_case_rows)

    log_to_file(log_file, f"CSV exported to {csv_file}")
    log_to_file(log_file, f"Lens-case CSV exported to {lens_case_csv_file}")
    log_to_file(log_file, "")

    return log_file


def run_targets_scan(
    scan_data: list[dict[str, Any]],
    industries: list[str] | str | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    scoring_profile: str | ScoringProfile | None = None,
    output_dir: str | Path | None = None,
) -> Path:
    """Convenience alias for ``analyze_targets_scan``."""
    return analyze_targets_scan(
        scan_data=scan_data,
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        scoring_profile=scoring_profile,
        output_dir=output_dir,
    )


def run_targets_scan_by_industry(
    api_client: ApiTradingViewClient,
    industries: list[str],
    markets: list[str] | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    scoring_profile: str | ScoringProfile | None = None,
    timeout: int = 30,
) -> dict[str, Path]:
    """Run target analysis for each industry in separate folders."""
    normalized = _normalize_industries(industries) or []
    generated: dict[str, Path] = {}

    for industry in normalized:
        industry_dir = LOG_DIR / _sanitize_industry_folder_name(industry)
        scan_data = api_client.scan_global_market_move_prediction(
            industries=[industry],
            markets=markets,
            min_market_cap_usd=min_market_cap_usd,
            max_market_cap_usd=max_market_cap_usd,
            timeout=timeout,
        ).get("data", [])

        generated[industry] = analyze_targets_scan(
            scan_data=scan_data,
            industries=[industry],
            min_market_cap_usd=min_market_cap_usd,
            max_market_cap_usd=max_market_cap_usd,
            scoring_profile=scoring_profile,
            output_dir=industry_dir,
        )

    return generated
