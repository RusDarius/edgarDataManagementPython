"""Forward target estimation engine for TradingView-sourced equity data.

This module builds on the same data pipeline used by the move-prediction
analysis but shifts the objective from directional scoring to concrete
forward price and valuation targets expressed as bear / base / bull ranges.

Target construction flow
------------------------
1. Fetch scan data via ``ApiTradingViewClient.scan_global_market_move_prediction``
   (same payload as the prediction module — all fields are reused).
2. Derive peer-relative metrics, normalized signals, and component scores
   (reusing ``_build_derived_metrics``, ``_build_metric_profiles``, and the
   full component scoring flow from the prediction module).
3. For each company, estimate forward targets through three independent
   lenses that are then blended:
   a. **Multiple-anchored targets** — apply peer-median and sector-median
      valuation multiples (P/E, EV/EBITDA, P/S, P/FCF, EV/Revenue) to the
      company's own trailing fundamentals, producing an implied fair-value
      price for each multiple.
   b. **Technical-anchored targets** — derive support / resistance levels
      from moving averages (SMA/EMA 10-200), Bollinger Bands, pivot points,
      and 52-week high/low positions, creating a technical price envelope.
   c. **Fundamental-trajectory targets** — project revenue, EBITDA, net
      income, and FCF forward using the company's own YoY / QoQ growth rates,
      then apply current (or peer) multiples to the projected figures.
4. Blend the three lenses into bear / base / bull targets per horizon
   (near-term, medium-term, long-term) with configurable lens weights.
5. Compute an implied upside/downside percentage from the current price and
   an opportunity score that summarizes how attractive the target range is
   relative to the current price.
6. Log a detailed report and export a CSV with all targets and supporting
   data.
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

# Valuation multiples used for multiple-anchored targets.
# Each entry: (field_name, invert_for_price, label)
# ``invert_for_price`` means the multiple is of the form price/fundamental,
# so implied_price = multiple * fundamental_per_share.  For EV-based multiples
# the conversion goes through enterprise_value → equity_value → price.
MULTIPLE_FIELDS: list[tuple[str, str]] = [
    ("price_earnings_ttm", "P/E TTM"),
    ("price_sales_current", "P/S"),
    ("price_book_fq", "P/B"),
    ("price_free_cash_flow_ttm", "P/FCF"),
    ("price_to_cash_f_operating_activities_ttm", "P/OCF"),
    ("enterprise_value_to_revenue_ttm", "EV/Revenue"),
    ("enterprise_value_to_ebit_ttm", "EV/EBIT"),
    ("enterprise_value_ebitda_ttm", "EV/EBITDA"),
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

# How much each lens contributes to the blended target per horizon.
DEFAULT_LENS_WEIGHTS: dict[str, dict[str, float]] = {
    "near_term": {
        "technical": 0.55,
        "multiple": 0.25,
        "trajectory": 0.20,
    },
    "medium_term": {
        "technical": 0.25,
        "multiple": 0.40,
        "trajectory": 0.35,
    },
    "long_term": {
        "technical": 0.10,
        "multiple": 0.35,
        "trajectory": 0.55,
    },
}

# Growth cap: clamp extreme growth rates so projections stay grounded.
MAX_GROWTH_RATE = 2.00  # 200%
MIN_GROWTH_RATE = -0.80  # -80%

# Scenario multipliers applied to base-case targets.
SCENARIO_MULTIPLIERS = {
    "bear": 0.85,
    "base": 1.00,
    "bull": 1.18,
}

# Opportunity score thresholds.
STRONG_OPPORTUNITY_THRESHOLD = 25.0  # percent implied upside
MODERATE_OPPORTUNITY_THRESHOLD = 10.0


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


# ---------------------------------------------------------------------------
# Peer statistics builder
# ---------------------------------------------------------------------------


def _build_peer_multiple_stats(
    scan_data: list[dict[str, Any]],
) -> dict[str, dict[str, float | None]]:
    """Compute peer-group statistics for each valuation multiple field.

    Returns a dict keyed by field_name with keys: median, p25, p75, mean, count.
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
            }
            continue
        sorted_vals = sorted(raw_values)
        stats[field_name] = {
            "median": median(sorted_vals),
            "p25": _percentile(sorted_vals, 0.25),
            "p75": _percentile(sorted_vals, 0.75),
            "mean": sum(sorted_vals) / len(sorted_vals),
            "count": len(sorted_vals),
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

    # Near-term anchors: short MAs, BBs, pivot, 1M range.
    near_prices: list[float] = []
    for f in [
        "SMA10",
        "SMA20",
        "EMA10",
        "EMA20",
        "BB.upper",
        "BB.lower",
        "Pivot.M.Classic.Middle",
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
# Lens 3: Fundamental-trajectory targets
# ---------------------------------------------------------------------------


def _trajectory_targets(
    row: dict[str, Any],
    peer_multiple_stats: dict[str, dict[str, float | None]],
    close: float,
    market_cap: float | None,
) -> list[TargetEstimate]:
    """Project fundamentals forward using the company's own growth rates
    and apply peer multiples to the projected figures.

    The projection uses a revenue-centric approach:
    - Near-term: 0.25 year of growth
    - Medium-term: 1.0 year of growth
    - Long-term: 2.0 years of growth (compounded, with decay)

    Implied price = (projected_fundamental * peer_multiple) / shares
    Simplified via the ratio approach when possible.
    """
    estimates: list[TargetEstimate] = []

    # Extract growth rates.
    rev_growth_yoy = _clamped_growth(
        _coerce_numeric(row.get("total_revenue_yoy_growth_ttm"))
    )
    rev_growth_qoq = _clamped_growth(
        _coerce_numeric(row.get("total_revenue_qoq_growth_fq"))
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

    # Best available growth rate (prefer forward EPS, then revenue, then earnings).
    primary_growth = eps_fwd_growth or rev_growth_yoy or eps_growth
    secondary_growth = ebitda_growth or ni_growth or fcf_growth

    if primary_growth is None and secondary_growth is None:
        return estimates

    best_growth = primary_growth if primary_growth is not None else secondary_growth

    # Revenue and EPS anchors for projection.
    total_revenue = _coerce_numeric(row.get("total_revenue"))
    ebitda_val = _coerce_numeric(row.get("ebitda"))
    net_income_val = _coerce_numeric(row.get("net_income"))
    eps_actual = _coerce_numeric(row.get("earnings_per_share_fq"))
    pe_current = _coerce_numeric(row.get("price_earnings_ttm"))
    ps_current = _coerce_numeric(row.get("price_sales_current"))
    ev_ebitda_current = _coerce_numeric(row.get("enterprise_value_ebitda_ttm"))

    pe_peer = peer_multiple_stats.get("price_earnings_ttm", {}).get("median")
    ps_peer = peer_multiple_stats.get("price_sales_current", {}).get("median")
    ev_ebitda_peer = peer_multiple_stats.get("enterprise_value_ebitda_ttm", {}).get(
        "median"
    )

    # Horizon projection factors.
    horizon_factors = {
        "near_term": 0.25,
        "medium_term": 1.0,
        "long_term": 2.0,
    }

    # Growth decay for long projections.
    growth_decay = {
        "near_term": 1.0,
        "medium_term": 0.90,
        "long_term": 0.70,
    }

    for horizon, years in horizon_factors.items():
        decay = growth_decay[horizon]
        effective_growth = best_growth * decay

        # EPS-based projection.
        if eps_actual is not None and eps_actual > 0:
            projected_eps = eps_actual * ((1.0 + effective_growth) ** years)
            # Apply current PE or peer PE (whichever is available).
            applied_pe = pe_current if pe_current and pe_current > 0 else pe_peer
            if applied_pe is not None and applied_pe > 0:
                implied_price = projected_eps * applied_pe
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
            projected_rev = total_revenue * ((1.0 + rev_growth_applied) ** years)
            applied_ps = ps_current if ps_current and ps_current > 0 else ps_peer
            if applied_ps is not None and applied_ps > 0:
                # implied_mcap = projected_rev * applied_ps
                # implied_price = close * (implied_mcap / market_cap)
                implied_mcap = projected_rev * applied_ps
                implied_price = close * (implied_mcap / market_cap)
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
                    implied_price = close * (implied_equity / market_cap)
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
    component_scores: dict[str, float | None],
    lens_weights: dict[str, float] | None = None,
) -> HorizonTarget:
    """Blend estimates from all three lenses into a bear/base/bull target.

    Within each lens, estimates are aggregated into a single implied price
    using their confidence weights.  Then the three lens values are blended
    using the horizon-specific lens weights.

    Scenario multipliers are applied to the blended base price to generate
    bear and bull targets. The component scores from the prediction module
    are used to tilt the scenario band:
    — strong quality + safety → tighter bear (less downside risk)
    — strong momentum + trend → wider bull band
    """
    weights = lens_weights or DEFAULT_LENS_WEIGHTS.get(
        horizon, DEFAULT_LENS_WEIGHTS["medium_term"]
    )

    def _aggregate_lens(estimates: list[TargetEstimate]) -> float | None:
        if not estimates:
            return None
        return _weighted_blend(
            [(e.implied_price, e.confidence_weight) for e in estimates]
        )

    # Filter estimates by horizon tag for technical and trajectory lenses.
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

    tech_price = _aggregate_lens(tech_for_horizon)
    mult_price = _aggregate_lens(multiple_estimates)
    traj_price = _aggregate_lens(traj_for_horizon)

    all_lens_estimates = tech_for_horizon + multiple_estimates + traj_for_horizon

    # Count how many lenses contributed.
    lens_coverage = (
        sum(
            [
                1.0 if tech_price is not None else 0.0,
                1.0 if mult_price is not None else 0.0,
                1.0 if traj_price is not None else 0.0,
            ]
        )
        / 3.0
    )

    base_price = _weighted_blend(
        [
            (tech_price, weights.get("technical", 0.0)),
            (mult_price, weights.get("multiple", 0.0)),
            (traj_price, weights.get("trajectory", 0.0)),
        ]
    )

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
        )

    # Tilt scenario band based on component scores.
    quality_score = component_scores.get("quality") or 0.0
    safety_score = component_scores.get("safety") or 0.0
    momentum_score = component_scores.get("momentum") or 0.0
    trend_score = component_scores.get("trend") or 0.0

    # Bear multiplier: tighter if quality+safety are strong (less downside).
    bear_adj = SCENARIO_MULTIPLIERS["bear"]
    bear_adj += _clamp((quality_score + safety_score) * 0.02, -0.05, 0.05)

    # Bull multiplier: wider if momentum+trend are strong (more upside room).
    bull_adj = SCENARIO_MULTIPLIERS["bull"]
    bull_adj += _clamp((momentum_score + trend_score) * 0.02, -0.05, 0.08)

    bear_price = base_price * bear_adj
    bull_price = base_price * bull_adj

    bear_upside = ((bear_price / close) - 1.0) * 100.0
    base_upside = ((base_price / close) - 1.0) * 100.0
    bull_upside = ((bull_price / close) - 1.0) * 100.0

    # Opportunity score: composite of base upside, band skew, and coverage.
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


def _build_company_targets(
    row: dict[str, Any],
    derived_row: dict[str, float | None],
    profiles: dict[str, dict[str, float | int | None]],
    peer_multiple_stats: dict[str, dict[str, float | None]],
    scoring_profile: ScoringProfile,
) -> CompanyTargetResult | None:
    """Build complete target estimates for one company."""
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

    # Lens 1: Multiple-anchored.
    multiple_estimates = _multiple_anchored_targets(row, peer_multiple_stats, close)

    # Lens 2: Technical-anchored.
    technical_estimates, envelope = _technical_anchored_targets(row, close)

    # Lens 3: Fundamental-trajectory.
    trajectory_estimates = _trajectory_targets(
        row, peer_multiple_stats, close, market_cap
    )

    # Blend into horizon targets.
    horizon_targets: dict[str, HorizonTarget] = {}
    for horizon in TARGET_HORIZONS:
        horizon_targets[horizon] = _blend_horizon_target(
            horizon=horizon,
            close=close,
            multiple_estimates=multiple_estimates,
            technical_estimates=technical_estimates,
            trajectory_estimates=trajectory_estimates,
            component_scores=component_scores,
        )

    # Valuation summary.
    valuation_summary = _build_valuation_summary(row, peer_multiple_stats)

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
        "This report estimates forward price and valuation targets using three independent lenses blended per horizon.",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        "Lens 1 — Multiple-anchored targets: For each standard valuation multiple (P/E, P/S, P/B, P/FCF, P/OCF, EV/Revenue, EV/EBIT, EV/EBITDA), "
        "the company's current multiple is compared to the peer-group median. The implied price that would bring the company's multiple in line with "
        "the peer median is computed as: implied_price = current_price * (peer_median_multiple / company_multiple). "
        "Each estimate carries a confidence weight based on peer-group coverage.",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        "Lens 2 — Technical-anchored targets: A price envelope is constructed from moving averages (SMA/EMA 10-200), VWAP, VWMA, "
        "Bollinger Bands, monthly pivot points, and multi-period high/low ranges (1M, 3M, 6M, 52W). "
        "Near-term anchors use short MAs and BBs; medium-term uses SMA50/EMA50 and 3-6M ranges; long-term uses SMA200/EMA200 and 52W range. "
        "The envelope is summarized into percentile-based bear/base/bull levels.",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        "Lens 3 — Fundamental-trajectory targets: The company's own growth rates (YoY revenue, EBITDA, net income, EPS forward) "
        "are used to project fundamentals forward over near-term (0.25 yr), medium-term (1 yr), and long-term (2 yr) horizons with "
        "growth decay applied to longer projections. Projected fundamentals are then multiplied by the current or peer valuation "
        "multiple to derive an implied price. Growth rates are clamped to [-80%, +200%] to avoid extreme projections.",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        "Blending: The three lens prices are combined per horizon using configurable weights "
        f"(near-term: tech={DEFAULT_LENS_WEIGHTS['near_term']['technical']:.0%}, mult={DEFAULT_LENS_WEIGHTS['near_term']['multiple']:.0%}, "
        f"traj={DEFAULT_LENS_WEIGHTS['near_term']['trajectory']:.0%} | "
        f"medium-term: tech={DEFAULT_LENS_WEIGHTS['medium_term']['technical']:.0%}, mult={DEFAULT_LENS_WEIGHTS['medium_term']['multiple']:.0%}, "
        f"traj={DEFAULT_LENS_WEIGHTS['medium_term']['trajectory']:.0%} | "
        f"long-term: tech={DEFAULT_LENS_WEIGHTS['long_term']['technical']:.0%}, mult={DEFAULT_LENS_WEIGHTS['long_term']['multiple']:.0%}, "
        f"traj={DEFAULT_LENS_WEIGHTS['long_term']['trajectory']:.0%}).",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        "Scenario bands: Bear and bull targets are derived from the base by applying scenario multipliers "
        f"(bear={SCENARIO_MULTIPLIERS['bear']:.0%}, bull={SCENARIO_MULTIPLIERS['bull']:.0%}) "
        "with small adjustments based on quality/safety scores (tighter bear band for strong fundamentals) "
        "and momentum/trend scores (wider bull band for strong tape confirmation).",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        f"Opportunity score: A composite of base-case implied upside weighted by lens coverage. "
        f"Strong Opportunity >= {STRONG_OPPORTUNITY_THRESHOLD:.0f}% | "
        f"Moderate Opportunity >= {MODERATE_OPPORTUNITY_THRESHOLD:.0f}% | Fair Value Range in between | "
        f"Moderate Overvaluation <= -{MODERATE_OPPORTUNITY_THRESHOLD:.0f}% | "
        f"Strong Overvaluation <= -{STRONG_OPPORTUNITY_THRESHOLD:.0f}%.",
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
        f"{'Multiple':<20} {'Count':>8} {'P25':>12} {'Median':>12} {'P75':>12} {'Mean':>12}",
    )
    log_to_file(log_file, "-" * 80)
    for field_name, label in MULTIPLE_FIELDS:
        stats = peer_multiple_stats.get(field_name, {})
        count = stats.get("count") or 0
        log_to_file(
            log_file,
            f"{label:<20} {count:>8} {_format_multiple(stats.get('p25')):>12} "
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
        "close",
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
            ]
        )
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
    return headers


def _build_csv_row(result: CompanyTargetResult) -> list[str]:
    row = result.raw_row
    csv_row = [
        result.symbol,
        result.company_name,
        result.industry,
        str(row.get("sector") or ""),
        str(row.get("exchange") or ""),
        str(row.get("market") or ""),
        str(result.market_cap or ""),
        str(result.close or ""),
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
            ]
        )
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

    # Report sections.
    _log_methodology(log_file)
    _log_peer_multiple_summary(log_file, peer_multiple_stats)

    for horizon in TARGET_HORIZONS:
        _log_horizon_targets_section(log_file, results, horizon)

    _log_valuation_comparison_section(log_file, results)
    _log_consensus_targets(log_file, results)
    _log_component_quality_overlay(log_file, results)

    # CSV export.
    csv_headers = _build_csv_headers()
    csv_rows = [_build_csv_row(r) for r in results]
    log_rows_to_csv(csv_file, csv_headers, csv_rows)

    log_to_file(log_file, f"CSV exported to {csv_file}")
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
