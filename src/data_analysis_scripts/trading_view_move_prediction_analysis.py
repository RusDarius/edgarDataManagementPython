from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
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
from generic_utils.log_to_files_util import log_to_file, log_rows_to_csv
from data_loaders.api_tradingview_client import ApiTradingViewClient


LOG_DIR = Path(
    r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\prediction_analysis"
)

TOP_SECTION_ROWS = 30

PERFORMANCE_TRACKING_FIELD_ORDER = [
    "Perf.W",
    "Perf.1M",
    "Perf.YTD",
    "Perf.Y",
    "Perf.5Y",
]

PERFORMANCE_TRACKING_LABELS = {
    "Perf.W": "1 Week",
    "Perf.1M": "1 Month",
    "Perf.YTD": "Year-to-Date",
    "Perf.Y": "1 Year",
    "Perf.5Y": "5 Year",
}

DEFAULT_PERFORMANCE_TRACKING_PERIODS = {
    "days": ["Perf.W"],
    "weeks": ["Perf.1M"],
    "months": ["Perf.YTD"],
    "years": ["Perf.Y", "Perf.5Y"],
}

ENTRY_METADATA_FIELDS = [
    "symbol",
    "name",
    "exchange",
    "country",
    "sector",
    "industry",
    "market",
    "earnings_release_date",
    "earnings_release_next_date",
]

RAW_PROFILE_FIELDS = [
    "market_cap_basic",
    "close",
    "cash_n_short_term_invest_fy",
    "cash_n_short_term_invest_fq",
    "cash_n_equivalents_fq",
    "float_shares_outstanding",
    "float_shares_percent_current",
    "volume",
    "average_volume_10d_calc",
    "relative_volume_10d_calc",
    "Value.Traded",
    "AvgValue.Traded_10d",
    "ADR",
    "ATR",
    "ATRP",
    "Volatility.D",
    "Volatility.W",
    "Volatility.M",
    "beta_1_year",
    "change",
    "gap",
    "premarket_gap",
    "premarket_change",
    "premarket_volume",
    "postmarket_change",
    "postmarket_volume",
    "Perf.5D",
    "Perf.W",
    "Perf.1M",
    "Perf.3M",
    "Perf.6M",
    "Perf.YTD",
    "Perf.Y",
    "Perf.5Y",
    "RSI",
    "RSI7",
    "MACD.macd",
    "MACD.signal",
    "Mom",
    "ROC",
    "Recommend.All",
    "Recommend.MA",
    "Recommend.Other",
    "VWAP",
    "VWMA",
    "SMA50",
    "SMA200",
    "EMA50",
    "EMA200",
    "current_ratio",
    "quick_ratio",
    "cash_ratio",
    "short_term_debt_fy",
    "short_term_debt_fq",
    "total_current_liabilities_fq",
    "total_debt",
    "net_debt",
    "debt_to_equity",
    "debt_to_revenue_ttm",
    "altman_z_score_ttm",
    "gross_margin",
    "operating_margin",
    "after_tax_margin",
    "return_on_assets",
    "return_on_equity",
    "return_on_invested_capital",
    "total_revenue_yoy_growth_ttm",
    "total_revenue_qoq_growth_fq",
    "ebitda_yoy_growth_ttm",
    "ebitda_qoq_growth_fq",
    "net_income_yoy_growth_ttm",
    "net_income_qoq_growth_fq",
    "free_cash_flow_yoy_growth_ttm",
    "free_cash_flow_qoq_growth_fq",
    "price_earnings_ttm",
    "price_earnings_growth_ttm",
    "price_sales_current",
    "price_book_fq",
    "price_free_cash_flow_ttm",
    "price_to_cash_f_operating_activities_ttm",
    "enterprise_value_to_revenue_ttm",
    "enterprise_value_to_ebit_ttm",
    "enterprise_value_ebitda_ttm",
    "price_52_week_high",
    "price_52_week_low",
    "High.6M",
    "Low.6M",
    "earnings_yield",
    "free_cash_flow_margin_ttm",
    "buyback_yield",
    "dividends_yield_current",
    "Aroon.Up",
    "Aroon.Down",
    "ADX",
    "ADX+DI",
    "ADX-DI",
    "BB.upper",
    "BB.lower",
    "total_revenue",
    "enterprise_value_fq",
    "number_of_employees",
    "_peer_market_cap_share",
    "_peer_revenue_share",
    "SMA10",
    "SMA20",
    "SMA30",
    "EMA10",
    "EMA20",
    "EMA30",
    "High.1M",
    "Low.1M",
    "High.3M",
    "Low.3M",
    "Stoch.RSI.K",
    "CCI20",
    "earnings_per_share_forecast_next_fq",
    "earnings_per_share_fq",
    "eps_surprise_percent_fq",
]

DERIVED_PROFILE_FIELDS = [
    "float_turnover",
    "dollar_turnover_intensity",
    "short_term_cash_coverage",
    "gap_severity",
    "event_intensity",
    "macd_spread",
    "rsi_centered",
    "rsi7_centered",
    "distance_from_52w_high",
    "range_position_52w",
    "aroon_spread",
    "adx_directional_spread",
    "bb_position",
    "bb_squeeze",
    "close_vs_sma10",
    "close_vs_sma20",
    "close_vs_sma30",
    "close_vs_ema10",
    "close_vs_ema20",
    "close_vs_ema30",
    "short_trend_emergence",
    "range_compression",
    "volatility_contraction",
    "eps_forward_growth",
    "stoch_rsi_centered",
]

EXTENDED_SIGNALS: frozenset[str] = frozenset(
    {
        "Perf.6M",
        "aroon_spread",
        "adx_directional_spread",
        "bb_position",
        "bb_squeeze",
        "free_cash_flow_margin_ttm",
        "buyback_yield",
        "dividends_yield_current",
        "earnings_yield",
        "distance_from_52w_high",
        "range_position_52w",
        "close_vs_sma10",
        "close_vs_sma20",
        "close_vs_sma30",
        "close_vs_ema10",
        "close_vs_ema20",
        "close_vs_ema30",
        "short_trend_emergence",
        "range_compression",
        "volatility_contraction",
        "eps_forward_growth",
        "stoch_rsi_centered",
        "CCI20",
        "eps_surprise_percent_fq",
    }
)

COMPONENT_ORDER = [
    "attention",
    "event",
    "momentum",
    "trend",
    "quality",
    "valuation",
    "safety",
    "scale",
]

COMPONENT_LABELS = {
    "attention": "Attention",
    "event": "Event",
    "momentum": "Momentum",
    "trend": "Trend",
    "quality": "Quality",
    "valuation": "Value",
    "safety": "Safety",
    "scale": "Scale",
}

STRONG_MOVE_SCORE_THRESHOLD = 1.10
DIRECTIONAL_MOVE_SCORE_THRESHOLD = 0.35

DEFAULT_HORIZON_WEIGHTS = {
    "days": {
        "attention": 0.16,
        "event": 0.12,
        "momentum": 0.20,
        "trend": 0.20,
        "quality": 0.12,
        "valuation": 0.06,
        "safety": 0.08,
        "scale": 0.06,
    },
    "weeks": {
        "attention": 0.12,
        "event": 0.06,
        "momentum": 0.20,
        "trend": 0.22,
        "quality": 0.14,
        "valuation": 0.08,
        "safety": 0.12,
        "scale": 0.06,
    },
    "months": {
        "attention": 0.06,
        "event": 0.03,
        "momentum": 0.14,
        "trend": 0.22,
        "quality": 0.24,
        "valuation": 0.14,
        "safety": 0.12,
        "scale": 0.05,
    },
    "years": {
        "attention": 0.02,
        "event": 0.02,
        "momentum": 0.04,
        "trend": 0.12,
        "quality": 0.32,
        "valuation": 0.24,
        "safety": 0.20,
        "scale": 0.04,
    },
}

HORIZON_TITLES = {
    "days": "Days horizon",
    "weeks": "Weeks horizon",
    "months": "Months horizon",
    "years": "Years horizon",
}

TOP_SECTION_ROWS = 30


@dataclass(frozen=True)
class DirectionalBias:
    positive_multiplier: float = 1.0
    negative_multiplier: float = 1.0


@dataclass(frozen=True)
class ScoringProfile:
    name: str
    description: str
    horizon_weights: dict[str, dict[str, float]] = field(default_factory=dict)
    component_signal_weights: dict[str, dict[str, float]] = field(default_factory=dict)
    component_directional_bias: dict[str, DirectionalBias] = field(default_factory=dict)
    missing_component_scores_by_horizon: dict[str, dict[str, float]] = field(
        default_factory=dict
    )
    performance_tracking_periods: dict[str, list[str]] = field(default_factory=dict)
    intro_metric_notes: list[str] = field(default_factory=list)
    confidence_multiplier: float = 1.0
    confidence_offset: float = 0.0


PRESET_SCORING_PROFILES = {
    "balanced": ScoringProfile(
        name="balanced",
        description=(
            "Default balanced profile. It keeps the base scoreflow intact so short-term tape action, trend quality, valuation, and safety all contribute according to the default horizon map. Light missing-component fallbacks now prevent names with sparse tactical or fundamental data from floating up through silent renormalization."
        ),
        missing_component_scores_by_horizon={
            "days": {
                "attention": -0.30,
                "momentum": -0.30,
                "scale": -0.20,
            },
            "weeks": {
                "momentum": -0.25,
                "trend": -0.20,
                "scale": -0.20,
            },
            "months": {
                "quality": -0.35,
                "valuation": -0.25,
                "scale": -0.25,
            },
            "years": {
                "quality": -0.50,
                "valuation": -0.40,
                "safety": -0.35,
                "scale": -0.30,
            },
        },
    ),
    "breakout_long": ScoringProfile(
        name="breakout_long",
        description=(
            "Bias toward upside continuation setups where unusual participation, "
            "event pressure, fast momentum, and confirmed trend continuation "
            "matter materially more than deep valuation support. Extended signals "
            "now activate short-term MA alignment, Bollinger Band position, "
            "Stoch.RSI, CCI20, and EPS surprise for catalyst-confirmed "
            "continuation. 200-day trend markers are further dampened so the "
            "profile stays firmly tactical. Missing tactical confirmation is "
            "penalized so the leaderboard does not drift back toward the balanced "
            "profile."
        ),
        horizon_weights={
            "days": {
                "attention": 0.32,
                "event": 0.20,
                "momentum": 0.28,
                "trend": 0.14,
                "quality": 0.02,
                "valuation": 0.00,
                "safety": 0.04,
            },
            "weeks": {
                "attention": 0.22,
                "event": 0.12,
                "momentum": 0.28,
                "trend": 0.24,
                "quality": 0.04,
                "valuation": 0.01,
                "safety": 0.09,
            },
            "months": {
                "attention": 0.12,
                "event": 0.08,
                "momentum": 0.22,
                "trend": 0.26,
                "quality": 0.12,
                "valuation": 0.04,
                "safety": 0.16,
            },
            "years": {
                "attention": 0.04,
                "event": 0.05,
                "momentum": 0.14,
                "trend": 0.20,
                "quality": 0.18,
                "valuation": 0.10,
                "safety": 0.29,
            },
        },
        component_signal_weights={
            "attention": {
                "relative_volume_10d_calc": 1.45,
                "float_turnover": 1.30,
                "dollar_turnover_intensity": 1.20,
                "Value.Traded": 1.15,
                "AvgValue.Traded_10d": 1.10,
            },
            "event": {
                "premarket_change": 1.15,
                "postmarket_change": 1.15,
                "gap": 1.15,
                "gap_severity": 1.30,
                "event_intensity": 1.25,
                "eps_surprise_percent_fq": 1.10,
            },
            "momentum": {
                "change": 1.15,
                "Perf.5D": 1.35,
                "Perf.W": 1.25,
                "Perf.1M": 1.15,
                "Perf.3M": 1.05,
                "Perf.6M": 0.55,
                "Perf.YTD": 0.65,
                "Perf.Y": 0.30,
                "ROC": 1.10,
                "Mom": 1.10,
                "macd_spread": 1.15,
                "Recommend.MA": 1.10,
                "rsi7_centered": 1.10,
                "stoch_rsi_centered": 0.85,
                "CCI20": 0.80,
            },
            "trend": {
                "trend_alignment": 1.30,
                "close_vs_sma10": 1.25,
                "close_vs_sma20": 1.10,
                "close_vs_sma30": 0.95,
                "close_vs_sma50": 1.15,
                "close_vs_sma200": 0.65,
                "close_vs_ema10": 1.25,
                "close_vs_ema20": 1.10,
                "close_vs_ema30": 0.95,
                "close_vs_ema50": 1.15,
                "close_vs_ema200": 0.70,
                "close_vs_vwap": 1.20,
                "close_vs_vwma": 1.20,
                "bb_position": 1.15,
                "short_trend_emergence": 1.15,
            },
        },
        component_directional_bias={
            "attention": DirectionalBias(
                positive_multiplier=1.28, negative_multiplier=0.78
            ),
            "event": DirectionalBias(
                positive_multiplier=1.22, negative_multiplier=0.78
            ),
            "momentum": DirectionalBias(
                positive_multiplier=1.32, negative_multiplier=0.72
            ),
            "trend": DirectionalBias(
                positive_multiplier=1.22, negative_multiplier=0.82
            ),
            "quality": DirectionalBias(
                positive_multiplier=1.00, negative_multiplier=0.85
            ),
            "valuation": DirectionalBias(
                positive_multiplier=0.95, negative_multiplier=0.80
            ),
            "safety": DirectionalBias(
                positive_multiplier=1.00, negative_multiplier=0.82
            ),
        },
        missing_component_scores_by_horizon={
            "days": {
                "attention": -0.75,
                "event": -0.50,
                "momentum": -0.70,
                "trend": -0.45,
            },
            "weeks": {
                "attention": -0.50,
                "momentum": -0.55,
                "trend": -0.40,
            },
            "months": {
                "momentum": -0.40,
                "trend": -0.35,
            },
        },
        confidence_multiplier=1.07,
    ),
    "quality_value_compounder": ScoringProfile(
        name="quality_value_compounder",
        description=(
            "Bias toward durable long ideas where profitability, balance-sheet support, capital efficiency, and reasonable valuation matter more than near-term excitement. Attention weight was raised and negative attention is amplified so passive low-volume vehicles like investment trusts face a real drag. Extended quality signals (FCF margin, buyback yield) now activate to better distinguish operating quality from financial holding structures."
        ),
        horizon_weights={
            "days": {
                "attention": 0.06,
                "event": 0.01,
                "momentum": 0.06,
                "trend": 0.18,
                "quality": 0.31,
                "valuation": 0.16,
                "safety": 0.22,
            },
            "weeks": {
                "attention": 0.05,
                "event": 0.02,
                "momentum": 0.07,
                "trend": 0.16,
                "quality": 0.30,
                "valuation": 0.18,
                "safety": 0.22,
            },
            "months": {
                "attention": 0.04,
                "event": 0.02,
                "momentum": 0.08,
                "trend": 0.16,
                "quality": 0.29,
                "valuation": 0.18,
                "safety": 0.23,
            },
            "years": {
                "attention": 0.02,
                "event": 0.01,
                "momentum": 0.02,
                "trend": 0.08,
                "quality": 0.39,
                "valuation": 0.25,
                "safety": 0.23,
            },
        },
        component_signal_weights={
            "momentum": {
                "change": 0.15,
                "Perf.5D": 0.15,
                "Perf.W": 0.20,
                "Perf.1M": 0.40,
                "Perf.3M": 1.05,
                "Perf.YTD": 1.05,
                "Perf.Y": 1.20,
                "ROC": 0.40,
                "Mom": 0.45,
                "macd_spread": 0.35,
                "Recommend.All": 0.70,
                "Recommend.MA": 0.55,
                "Recommend.Other": 0.65,
                "rsi_centered": 0.20,
                "rsi7_centered": 0.10,
            },
            "trend": {
                "close_vs_sma50": 0.85,
                "close_vs_sma200": 1.35,
                "close_vs_ema50": 0.85,
                "close_vs_ema200": 1.35,
                "close_vs_vwap": 0.25,
                "close_vs_vwma": 0.40,
                "trend_alignment": 1.20,
            },
            "quality": {
                "total_revenue_yoy_growth_ttm": 1.10,
                "total_revenue_qoq_growth_fq": 0.85,
                "ebitda_yoy_growth_ttm": 1.15,
                "ebitda_qoq_growth_fq": 0.90,
                "net_income_yoy_growth_ttm": 1.05,
                "net_income_qoq_growth_fq": 0.85,
                "free_cash_flow_yoy_growth_ttm": 1.20,
                "free_cash_flow_qoq_growth_fq": 1.00,
                "gross_margin": 1.10,
                "operating_margin": 1.20,
                "after_tax_margin": 1.10,
                "return_on_assets": 1.05,
                "return_on_equity": 1.10,
                "return_on_invested_capital": 1.30,
                "free_cash_flow_margin_ttm": 1.15,
                "buyback_yield": 0.85,
            },
            "valuation": {
                "price_earnings_ttm": 1.10,
                "price_earnings_growth_ttm": 1.15,
                "price_sales_current": 1.05,
                "price_book_fq": 0.90,
                "price_free_cash_flow_ttm": 1.20,
                "price_to_cash_f_operating_activities_ttm": 1.10,
                "enterprise_value_to_revenue_ttm": 1.05,
                "enterprise_value_to_ebit_ttm": 0.95,
                "enterprise_value_ebitda_ttm": 1.15,
            },
            "safety": {
                "current_ratio": 1.05,
                "quick_ratio": 1.05,
                "cash_ratio": 1.00,
                "short_term_cash_coverage": 1.15,
                "altman_z_score_ttm": 1.20,
                "debt_to_equity": 1.20,
                "debt_to_revenue_ttm": 1.15,
                "net_debt": 1.10,
                "beta_1_year": 0.75,
            },
        },
        component_directional_bias={
            "attention": DirectionalBias(
                positive_multiplier=0.80, negative_multiplier=1.40
            ),
            "momentum": DirectionalBias(
                positive_multiplier=1.00, negative_multiplier=0.95
            ),
            "trend": DirectionalBias(
                positive_multiplier=1.05, negative_multiplier=1.10
            ),
            "quality": DirectionalBias(
                positive_multiplier=1.20, negative_multiplier=1.25
            ),
            "valuation": DirectionalBias(
                positive_multiplier=1.18, negative_multiplier=1.15
            ),
            "safety": DirectionalBias(
                positive_multiplier=1.15, negative_multiplier=1.25
            ),
        },
        missing_component_scores_by_horizon={
            "days": {
                "attention": -0.45,
                "quality": -0.90,
                "valuation": -0.75,
                "safety": -0.70,
            },
            "weeks": {
                "attention": -0.35,
                "quality": -1.00,
                "valuation": -0.80,
                "safety": -0.80,
            },
            "months": {
                "attention": -0.25,
                "quality": -1.10,
                "valuation": -0.90,
                "safety": -0.85,
            },
            "years": {
                "attention": -0.15,
                "quality": -1.20,
                "valuation": -1.00,
                "safety": -0.95,
            },
        },
        confidence_multiplier=1.02,
    ),
    "value_recovery": ScoringProfile(
        name="value_recovery",
        description=(
            "Bias toward recovery candidates where cheap valuation, improving "
            "sequential fundamentals, and stabilizing safety matter more than "
            "already-strong short-term momentum. The profile now emphasizes "
            "QoQ growth metrics over YoY to detect genuine recovery trajectory "
            "rather than static cheapness. Extended signals include "
            "distance_from_52w_high and range_position_52w for discount depth, "
            "eps_forward_growth for analyst recovery expectations, and "
            "stoch_rsi_centered for oversold-to-recovery turn detection. "
            "Attention weight is raised and negative attention amplified so "
            "low-volume passive vehicles are penalized."
        ),
        horizon_weights={
            "days": {
                "attention": 0.07,
                "event": 0.05,
                "momentum": 0.04,
                "trend": 0.12,
                "quality": 0.22,
                "valuation": 0.26,
                "safety": 0.24,
            },
            "weeks": {
                "attention": 0.07,
                "event": 0.05,
                "momentum": 0.08,
                "trend": 0.16,
                "quality": 0.20,
                "valuation": 0.23,
                "safety": 0.21,
            },
            "months": {
                "attention": 0.05,
                "event": 0.03,
                "momentum": 0.06,
                "trend": 0.16,
                "quality": 0.25,
                "valuation": 0.25,
                "safety": 0.20,
            },
            "years": {
                "attention": 0.02,
                "event": 0.01,
                "momentum": 0.01,
                "trend": 0.08,
                "quality": 0.28,
                "valuation": 0.33,
                "safety": 0.27,
            },
        },
        component_signal_weights={
            "momentum": {
                "change": 0.40,
                "Perf.5D": 0.30,
                "Perf.W": 0.35,
                "Perf.1M": 0.55,
                "Perf.3M": 1.20,
                "Perf.YTD": 1.05,
                "Perf.Y": 0.80,
                "ROC": 0.55,
                "Mom": 0.60,
                "macd_spread": 0.50,
                "Recommend.All": 0.70,
                "Recommend.MA": 0.60,
                "Recommend.Other": 0.70,
                "rsi_centered": 0.25,
                "rsi7_centered": 0.15,
                "stoch_rsi_centered": 0.45,
            },
            "valuation": {
                "price_earnings_ttm": 1.10,
                "price_earnings_growth_ttm": 1.20,
                "price_book_fq": 1.20,
                "price_sales_current": 1.10,
                "price_free_cash_flow_ttm": 1.05,
                "enterprise_value_to_revenue_ttm": 1.10,
                "enterprise_value_ebitda_ttm": 1.15,
                "earnings_yield": 1.20,
                "distance_from_52w_high": 1.15,
                "range_position_52w": 1.05,
            },
            "quality": {
                "total_revenue_yoy_growth_ttm": 0.95,
                "total_revenue_qoq_growth_fq": 1.15,
                "ebitda_yoy_growth_ttm": 1.00,
                "ebitda_qoq_growth_fq": 1.10,
                "net_income_yoy_growth_ttm": 1.05,
                "net_income_qoq_growth_fq": 1.20,
                "free_cash_flow_yoy_growth_ttm": 1.15,
                "free_cash_flow_qoq_growth_fq": 1.25,
                "operating_margin": 1.10,
                "after_tax_margin": 1.05,
                "free_cash_flow_margin_ttm": 1.15,
                "buyback_yield": 0.70,
                "dividends_yield_current": 0.60,
                "eps_forward_growth": 1.25,
            },
            "safety": {
                "short_term_cash_coverage": 1.10,
                "altman_z_score_ttm": 1.20,
                "debt_to_equity": 1.15,
                "debt_to_revenue_ttm": 1.10,
                "net_debt": 1.15,
            },
            "trend": {
                "close_vs_sma50": 0.90,
                "close_vs_sma200": 1.15,
                "close_vs_ema50": 0.90,
                "close_vs_ema200": 1.15,
                "close_vs_vwap": 0.60,
                "close_vs_vwma": 0.75,
                "trend_alignment": 1.10,
            },
        },
        component_directional_bias={
            "attention": DirectionalBias(
                positive_multiplier=0.85, negative_multiplier=1.30
            ),
            "momentum": DirectionalBias(
                positive_multiplier=0.90, negative_multiplier=0.65
            ),
            "trend": DirectionalBias(
                positive_multiplier=1.05, negative_multiplier=0.90
            ),
            "valuation": DirectionalBias(
                positive_multiplier=1.28, negative_multiplier=1.10
            ),
            "quality": DirectionalBias(
                positive_multiplier=1.15, negative_multiplier=1.10
            ),
            "safety": DirectionalBias(
                positive_multiplier=1.15, negative_multiplier=1.15
            ),
        },
        missing_component_scores_by_horizon={
            "days": {
                "attention": -0.35,
                "valuation": -0.80,
                "quality": -0.50,
                "safety": -0.60,
            },
            "weeks": {
                "attention": -0.25,
                "valuation": -0.90,
                "quality": -0.60,
                "safety": -0.70,
            },
            "months": {
                "attention": -0.15,
                "valuation": -1.00,
                "quality": -0.70,
                "safety": -0.80,
            },
            "years": {
                "valuation": -1.10,
                "quality": -0.80,
                "safety": -0.90,
            },
        },
        intro_metric_notes=[
            "Recovery trajectory emphasis: QoQ growth metrics are weighted 1.10-1.25 while YoY are 0.95-1.15. This surfaces names with sequential improvement even if trailing annual figures are still negative.",
            "Extended valuation signals: distance_from_52w_high (1.15) and range_position_52w (1.05) now detect beaten-down names with room to recover, differentiating from asymmetric_value which weights these more aggressively for static cheapness.",
            "Forward recovery conviction: eps_forward_growth (1.25) captures analyst expectations of EPS improvement, confirming the recovery trajectory thesis.",
            "Momentum recovery detection: stoch_rsi_centered (0.45) is activated to detect when oversold names start turning, a key recovery inflection signal.",
            "Quality directional bias tightened: negative quality (1.10x) is now penalized more than before (was 0.95x) so names with deteriorating fundamentals cannot ride cheap multiples alone.",
        ],
    ),
    "fragility_short": ScoringProfile(
        name="fragility_short",
        description=(
            "Bias toward short candidates where balance-sheet weakness, deteriorating trend, negative event pressure, and downside momentum should be penalized more aggressively. Missing fragility evidence is treated more cautiously so the profile does not just echo the balanced tape leaders."
        ),
        horizon_weights={
            "days": {
                "attention": 0.18,
                "event": 0.28,
                "momentum": 0.28,
                "trend": 0.18,
                "quality": 0.02,
                "valuation": 0.01,
                "safety": 0.05,
            },
            "weeks": {
                "attention": 0.10,
                "event": 0.18,
                "momentum": 0.28,
                "trend": 0.24,
                "quality": 0.05,
                "valuation": 0.01,
                "safety": 0.14,
            },
            "months": {
                "attention": 0.04,
                "event": 0.08,
                "momentum": 0.22,
                "trend": 0.24,
                "quality": 0.12,
                "valuation": 0.04,
                "safety": 0.26,
            },
            "years": {
                "attention": 0.00,
                "event": 0.03,
                "momentum": 0.08,
                "trend": 0.15,
                "quality": 0.16,
                "valuation": 0.05,
                "safety": 0.53,
            },
        },
        component_signal_weights={
            "event": {
                "premarket_change": 1.15,
                "postmarket_change": 1.15,
                "gap": 1.15,
                "gap_severity": 1.25,
                "event_intensity": 1.10,
            },
            "momentum": {
                "change": 1.15,
                "Perf.5D": 1.20,
                "Perf.W": 1.20,
                "Perf.1M": 1.10,
                "Perf.3M": 1.05,
                "ROC": 1.10,
            },
            "trend": {
                "close_vs_sma50": 1.10,
                "close_vs_sma200": 1.15,
                "close_vs_ema50": 1.10,
                "close_vs_ema200": 1.15,
                "close_vs_vwap": 1.10,
                "close_vs_vwma": 1.10,
                "trend_alignment": 1.20,
            },
            "safety": {
                "current_ratio": 1.05,
                "short_term_cash_coverage": 1.10,
                "debt_to_equity": 1.25,
                "debt_to_revenue_ttm": 1.20,
                "net_debt": 1.15,
                "altman_z_score_ttm": 1.25,
                "beta_1_year": 1.05,
            },
        },
        component_directional_bias={
            "event": DirectionalBias(
                positive_multiplier=0.90, negative_multiplier=1.20
            ),
            "momentum": DirectionalBias(
                positive_multiplier=0.85, negative_multiplier=1.20
            ),
            "trend": DirectionalBias(
                positive_multiplier=0.90, negative_multiplier=1.25
            ),
            "quality": DirectionalBias(
                positive_multiplier=0.90, negative_multiplier=1.15
            ),
            "safety": DirectionalBias(
                positive_multiplier=0.85, negative_multiplier=1.35
            ),
        },
        missing_component_scores_by_horizon={
            "days": {
                "event": -0.40,
                "momentum": -0.45,
                "trend": -0.35,
            },
            "weeks": {
                "momentum": -0.50,
                "trend": -0.40,
                "safety": -0.45,
            },
            "months": {
                "trend": -0.45,
                "safety": -0.60,
            },
            "years": {
                "safety": -0.80,
                "quality": -0.35,
            },
        },
        confidence_multiplier=1.07,
    ),
    "backtest_period_ladder": ScoringProfile(
        name="backtest_period_ladder",
        description=(
            "Purpose-built for retrospective horizon tracking against TradingView trailing performance windows. Short horizons lean on attention, event pressure, and fresh trend confirmation, while longer horizons progressively hand off to trend durability, quality, valuation, and safety."
        ),
        horizon_weights={
            "days": {
                "attention": 0.29,
                "event": 0.24,
                "momentum": 0.17,
                "trend": 0.21,
                "quality": 0.02,
                "valuation": 0.01,
                "safety": 0.06,
            },
            "weeks": {
                "attention": 0.19,
                "event": 0.10,
                "momentum": 0.17,
                "trend": 0.27,
                "quality": 0.08,
                "valuation": 0.04,
                "safety": 0.15,
            },
            "months": {
                "attention": 0.07,
                "event": 0.04,
                "momentum": 0.10,
                "trend": 0.24,
                "quality": 0.22,
                "valuation": 0.12,
                "safety": 0.21,
            },
            "years": {
                "attention": 0.01,
                "event": 0.02,
                "momentum": 0.03,
                "trend": 0.18,
                "quality": 0.31,
                "valuation": 0.22,
                "safety": 0.23,
            },
        },
        component_signal_weights={
            "momentum": {
                "Perf.W": 0.0,
                "Perf.1M": 0.0,
                "Perf.YTD": 0.0,
                "Perf.Y": 0.0,
                "Perf.5D": 1.15,
                "Perf.3M": 1.15,
                "Perf.6M": 1.10,
                "ROC": 1.10,
                "Mom": 1.10,
                "macd_spread": 1.10,
            },
            "trend": {
                "trend_alignment": 1.25,
                "close_vs_sma50": 1.10,
                "close_vs_sma200": 1.20,
                "close_vs_ema50": 1.10,
                "close_vs_ema200": 1.20,
                "close_vs_vwap": 1.05,
            },
            "quality": {
                "total_revenue_yoy_growth_ttm": 1.10,
                "ebitda_yoy_growth_ttm": 1.10,
                "free_cash_flow_yoy_growth_ttm": 1.20,
                "operating_margin": 1.10,
                "return_on_invested_capital": 1.20,
            },
            "valuation": {
                "price_earnings_ttm": 1.05,
                "price_free_cash_flow_ttm": 1.10,
                "enterprise_value_ebitda_ttm": 1.10,
                "enterprise_value_to_revenue_ttm": 1.05,
            },
            "safety": {
                "current_ratio": 1.05,
                "short_term_cash_coverage": 1.10,
                "altman_z_score_ttm": 1.15,
                "debt_to_equity": 1.10,
                "debt_to_revenue_ttm": 1.10,
            },
        },
        component_directional_bias={
            "trend": DirectionalBias(
                positive_multiplier=1.10, negative_multiplier=1.05
            ),
            "quality": DirectionalBias(
                positive_multiplier=1.10, negative_multiplier=1.00
            ),
            "valuation": DirectionalBias(
                positive_multiplier=1.10, negative_multiplier=1.05
            ),
            "safety": DirectionalBias(
                positive_multiplier=1.05, negative_multiplier=1.15
            ),
        },
        performance_tracking_periods={
            "days": ["Perf.W"],
            "weeks": ["Perf.1M"],
            "months": ["Perf.YTD"],
            "years": ["Perf.Y", "Perf.5Y"],
        },
        intro_metric_notes=[
            "Perf.5Y was added as a five-year trailing price-performance benchmark so long-duration calls can be checked against secular winners instead of only one-year moves.",
            "This profile intentionally mutes Perf.W, Perf.1M, Perf.YTD, and Perf.Y inside the momentum component so the companion tracking report is less circular when it compares horizon scores against those same realized-return windows.",
            "Tracking map: days to Perf.W, weeks to Perf.1M, months to Perf.YTD, and years to Perf.Y plus Perf.5Y.",
        ],
        confidence_multiplier=1.03,
        missing_component_scores_by_horizon={
            "days": {
                "attention": -0.25,
            },
            "weeks": {
                "attention": -0.20,
            },
            "months": {
                "quality": -0.25,
            },
            "years": {
                "quality": -0.35,
                "valuation": -0.25,
            },
        },
    ),
    "asymmetric_value": ScoringProfile(
        name="asymmetric_value",
        description=(
            "Surface names with deep value discounts where the fundamental floor "
            "remains intact, creating asymmetric upside potential. The profile "
            "combines traditional multiple-based valuation with forward-looking "
            "signals — earnings yield, FCF margin, shareholder returns — and "
            "price-position metrics that measure how far a stock sits below its "
            "52-week ceiling. Attention weight was raised and negative attention is "
            "amplified so passive low-volume vehicles face a meaningful drag. Safety "
            "carries meaningful weight across all horizons so the leaderboard avoids "
            "value traps."
        ),
        horizon_weights={
            "days": {
                "attention": 0.05,
                "event": 0.02,
                "momentum": 0.06,
                "trend": 0.15,
                "quality": 0.20,
                "valuation": 0.32,
                "safety": 0.20,
            },
            "weeks": {
                "attention": 0.05,
                "event": 0.03,
                "momentum": 0.08,
                "trend": 0.14,
                "quality": 0.22,
                "valuation": 0.28,
                "safety": 0.20,
            },
            "months": {
                "attention": 0.04,
                "event": 0.02,
                "momentum": 0.06,
                "trend": 0.12,
                "quality": 0.26,
                "valuation": 0.30,
                "safety": 0.20,
            },
            "years": {
                "attention": 0.02,
                "event": 0.01,
                "momentum": 0.02,
                "trend": 0.07,
                "quality": 0.28,
                "valuation": 0.36,
                "safety": 0.24,
            },
        },
        component_signal_weights={
            "momentum": {
                "change": 0.25,
                "Perf.5D": 0.30,
                "Perf.W": 0.35,
                "Perf.1M": 0.55,
                "Perf.3M": 1.15,
                "Perf.6M": 1.10,
                "Perf.YTD": 1.05,
                "Perf.Y": 0.80,
                "ROC": 0.50,
                "Mom": 0.55,
                "macd_spread": 0.45,
                "Recommend.All": 0.65,
                "Recommend.MA": 0.55,
                "Recommend.Other": 0.60,
                "rsi_centered": 0.20,
                "rsi7_centered": 0.10,
            },
            "trend": {
                "close_vs_sma50": 0.80,
                "close_vs_sma200": 1.30,
                "close_vs_ema50": 0.80,
                "close_vs_ema200": 1.30,
                "close_vs_vwap": 0.50,
                "close_vs_vwma": 0.60,
                "trend_alignment": 1.15,
            },
            "quality": {
                "total_revenue_yoy_growth_ttm": 1.05,
                "total_revenue_qoq_growth_fq": 0.90,
                "ebitda_yoy_growth_ttm": 1.10,
                "ebitda_qoq_growth_fq": 0.95,
                "net_income_yoy_growth_ttm": 1.05,
                "net_income_qoq_growth_fq": 0.90,
                "free_cash_flow_yoy_growth_ttm": 1.25,
                "free_cash_flow_qoq_growth_fq": 1.05,
                "gross_margin": 1.05,
                "operating_margin": 1.15,
                "after_tax_margin": 1.05,
                "return_on_assets": 1.00,
                "return_on_equity": 1.05,
                "return_on_invested_capital": 1.30,
                "free_cash_flow_margin_ttm": 1.30,
                "buyback_yield": 1.10,
                "dividends_yield_current": 0.90,
            },
            "valuation": {
                "price_earnings_ttm": 1.10,
                "price_earnings_growth_ttm": 1.15,
                "price_sales_current": 1.00,
                "price_book_fq": 1.10,
                "price_free_cash_flow_ttm": 1.20,
                "price_to_cash_f_operating_activities_ttm": 1.05,
                "enterprise_value_to_revenue_ttm": 1.00,
                "enterprise_value_to_ebit_ttm": 0.95,
                "enterprise_value_ebitda_ttm": 1.10,
                "earnings_yield": 1.30,
                "distance_from_52w_high": 1.40,
                "range_position_52w": 1.25,
            },
            "safety": {
                "current_ratio": 1.05,
                "quick_ratio": 1.05,
                "cash_ratio": 1.00,
                "short_term_cash_coverage": 1.15,
                "altman_z_score_ttm": 1.25,
                "debt_to_equity": 1.20,
                "debt_to_revenue_ttm": 1.15,
                "net_debt": 1.15,
                "beta_1_year": 0.80,
            },
        },
        component_directional_bias={
            "attention": DirectionalBias(
                positive_multiplier=0.80, negative_multiplier=1.35
            ),
            "momentum": DirectionalBias(
                positive_multiplier=0.85, negative_multiplier=0.60
            ),
            "trend": DirectionalBias(
                positive_multiplier=1.00, negative_multiplier=0.85
            ),
            "quality": DirectionalBias(
                positive_multiplier=1.15, negative_multiplier=1.20
            ),
            "valuation": DirectionalBias(
                positive_multiplier=1.30, negative_multiplier=1.00
            ),
            "safety": DirectionalBias(
                positive_multiplier=1.10, negative_multiplier=1.30
            ),
        },
        missing_component_scores_by_horizon={
            "days": {
                "attention": -0.40,
                "valuation": -0.80,
                "quality": -0.55,
                "safety": -0.60,
            },
            "weeks": {
                "attention": -0.30,
                "valuation": -0.90,
                "quality": -0.65,
                "safety": -0.70,
            },
            "months": {
                "attention": -0.20,
                "valuation": -1.00,
                "quality": -0.75,
                "safety": -0.80,
            },
            "years": {
                "valuation": -1.10,
                "quality": -0.85,
                "safety": -0.90,
            },
        },
        intro_metric_notes=[
            "This profile activates extended valuation signals: earnings_yield (higher = cheaper), distance_from_52w_high (inverted; further below peak = deeper discount), and range_position_52w (inverted; lower in 52-week range = more upside room).",
            "Quality is also extended with free_cash_flow_margin_ttm, buyback_yield, and dividends_yield_current to capture forward-looking capital return and cash generation quality.",
            "Momentum is intentionally damped so cheap-but-unloved names are not penalized for weak recent price action. Safety is elevated across all horizons to screen out value traps.",
        ],
        confidence_multiplier=1.02,
    ),
    "early_momentum_inflection": ScoringProfile(
        name="early_momentum_inflection",
        description=(
            "Catch names at the very beginning of a directional move before the "
            "crowd notices. The profile now fully activates its coiled-energy "
            "detection suite: range_compression, volatility_contraction, and "
            "bb_squeeze in attention alongside Aroon spread and ADX directional "
            "spread for new-trend detection. Short-term MA alignment (SMA10/20/30, "
            "EMA10/20/30) and short_trend_emergence are activated in trend. "
            "stoch_rsi_centered and CCI20 are activated in momentum for early "
            "impulse detection. Established long-duration momentum is damped even "
            "more aggressively and 200-day trend markers are further reduced so "
            "the leaderboard surfaces genuine inflection candidates."
        ),
        horizon_weights={
            "days": {
                "attention": 0.24,
                "event": 0.12,
                "momentum": 0.30,
                "trend": 0.24,
                "quality": 0.03,
                "valuation": 0.02,
                "safety": 0.05,
            },
            "weeks": {
                "attention": 0.18,
                "event": 0.06,
                "momentum": 0.28,
                "trend": 0.30,
                "quality": 0.06,
                "valuation": 0.03,
                "safety": 0.09,
            },
            "months": {
                "attention": 0.10,
                "event": 0.04,
                "momentum": 0.22,
                "trend": 0.26,
                "quality": 0.14,
                "valuation": 0.08,
                "safety": 0.16,
            },
            "years": {
                "attention": 0.02,
                "event": 0.02,
                "momentum": 0.10,
                "trend": 0.18,
                "quality": 0.24,
                "valuation": 0.18,
                "safety": 0.26,
            },
        },
        component_signal_weights={
            "attention": {
                "relative_volume_10d_calc": 1.30,
                "float_turnover": 1.25,
                "dollar_turnover_intensity": 1.15,
                "Value.Traded": 1.10,
                "AvgValue.Traded_10d": 1.00,
                "bb_squeeze": 1.40,
                "range_compression": 1.35,
                "volatility_contraction": 1.30,
            },
            "event": {
                "premarket_change": 1.10,
                "postmarket_change": 1.10,
                "gap": 1.10,
                "gap_severity": 1.20,
                "event_intensity": 1.15,
                "eps_surprise_percent_fq": 0.85,
            },
            "momentum": {
                "change": 1.00,
                "Perf.5D": 1.35,
                "Perf.W": 1.15,
                "Perf.1M": 0.75,
                "Perf.3M": 0.50,
                "Perf.6M": 0.30,
                "Perf.YTD": 0.25,
                "Perf.Y": 0.15,
                "ROC": 1.25,
                "Mom": 1.20,
                "macd_spread": 1.30,
                "Recommend.All": 0.80,
                "Recommend.MA": 0.95,
                "Recommend.Other": 0.85,
                "rsi_centered": 0.70,
                "rsi7_centered": 1.10,
                "aroon_spread": 1.55,
                "adx_directional_spread": 1.45,
                "stoch_rsi_centered": 1.10,
                "CCI20": 0.90,
            },
            "trend": {
                "close_vs_sma10": 1.30,
                "close_vs_sma20": 1.20,
                "close_vs_sma30": 1.10,
                "close_vs_sma50": 1.00,
                "close_vs_sma200": 0.60,
                "close_vs_ema10": 1.30,
                "close_vs_ema20": 1.20,
                "close_vs_ema30": 1.10,
                "close_vs_ema50": 1.00,
                "close_vs_ema200": 0.60,
                "close_vs_vwap": 1.25,
                "close_vs_vwma": 1.20,
                "trend_alignment": 0.85,
                "bb_position": 1.25,
                "short_trend_emergence": 1.45,
            },
        },
        component_directional_bias={
            "attention": DirectionalBias(
                positive_multiplier=1.25, negative_multiplier=0.78
            ),
            "event": DirectionalBias(
                positive_multiplier=1.10, negative_multiplier=0.90
            ),
            "momentum": DirectionalBias(
                positive_multiplier=1.35, negative_multiplier=0.70
            ),
            "trend": DirectionalBias(
                positive_multiplier=1.25, negative_multiplier=0.80
            ),
            "quality": DirectionalBias(
                positive_multiplier=1.00, negative_multiplier=0.85
            ),
            "safety": DirectionalBias(
                positive_multiplier=1.00, negative_multiplier=0.85
            ),
        },
        missing_component_scores_by_horizon={
            "days": {
                "attention": -0.65,
                "momentum": -0.70,
                "trend": -0.50,
            },
            "weeks": {
                "attention": -0.40,
                "momentum": -0.55,
                "trend": -0.40,
            },
            "months": {
                "momentum": -0.35,
                "trend": -0.25,
            },
        },
        intro_metric_notes=[
            "This profile activates extended momentum signals: aroon_spread (1.55, Aroon.Up minus Aroon.Down; positive = new uptrend forming) and adx_directional_spread (1.45, ADX+DI minus ADX-DI; positive = bullish directional strength emerging). Both are boosted from prior values.",
            "Coiled-energy suite fully activated in attention: bb_squeeze (1.40, inverted band width), range_compression (1.35, inverted 3-month range), volatility_contraction (1.30, inverted daily-vs-monthly vol ratio). These detect names where price is consolidating before an expansion.",
            "Short-term MA alignment activated in trend: close_vs_sma10/20/30 and close_vs_ema10/20/30 (1.30/1.20/1.10) plus short_trend_emergence (1.45). These catch the very first MA crossovers before SMA50/200 confirm.",
            "stoch_rsi_centered (1.10) and CCI20 (0.90) activated for early momentum impulse and directional thrust detection.",
            "Longer-duration momentum further damped: Perf.Y=0.15, Perf.YTD=0.25, Perf.6M=0.30, Perf.3M=0.50, Perf.1M=0.75. 200-day trend signals reduced to 0.60. trend_alignment reduced to 0.85 (established alignment is less relevant for nascent direction).",
        ],
        confidence_multiplier=1.05,
    ),
    "forward_edge_active": ScoringProfile(
        name="forward_edge_active",
        description=(
            "Forward-looking active management profile targeting risk-adjusted "
            "outperformance on the weeks-to-months timescale. Designed to catch "
            "setups before the crowd by combining early trend emergence (short-term "
            "MA alignment), volatility contraction (coiled energy), forward EPS "
            "growth expectations, and earnings surprise history. Trailing long-"
            "horizon performance is now damped even more aggressively — Perf.Y "
            "at 0.05 and Perf.YTD at 0.15. QoQ growth metrics are boosted to "
            "1.30-1.40 to prioritize what is changing over what was. Attention "
            "inversion is stronger (positive 0.55x, negative 1.40x) so the "
            "profile surfaces genuinely undiscovered names. Quality and safety "
            "carry meaningful weight across all horizons with heavier missing-"
            "component penalties to avoid catching falling knives."
        ),
        horizon_weights={
            "days": {
                "attention": 0.07,
                "event": 0.10,
                "momentum": 0.16,
                "trend": 0.28,
                "quality": 0.18,
                "valuation": 0.09,
                "safety": 0.12,
            },
            "weeks": {
                "attention": 0.05,
                "event": 0.07,
                "momentum": 0.14,
                "trend": 0.24,
                "quality": 0.22,
                "valuation": 0.12,
                "safety": 0.16,
            },
            "months": {
                "attention": 0.04,
                "event": 0.04,
                "momentum": 0.10,
                "trend": 0.18,
                "quality": 0.26,
                "valuation": 0.16,
                "safety": 0.22,
            },
            "years": {
                "attention": 0.02,
                "event": 0.02,
                "momentum": 0.04,
                "trend": 0.12,
                "quality": 0.30,
                "valuation": 0.22,
                "safety": 0.28,
            },
        },
        component_signal_weights={
            "attention": {
                "relative_volume_10d_calc": 0.80,
                "float_turnover": 0.75,
                "dollar_turnover_intensity": 0.70,
                "Value.Traded": 0.60,
                "AvgValue.Traded_10d": 0.50,
                "bb_squeeze": 1.55,
                "range_compression": 1.50,
                "volatility_contraction": 1.45,
            },
            "event": {
                "premarket_change": 0.85,
                "postmarket_change": 0.85,
                "gap": 0.75,
                "gap_severity": 0.80,
                "event_intensity": 0.85,
                "eps_surprise_percent_fq": 1.50,
            },
            "momentum": {
                "change": 0.70,
                "Perf.5D": 1.10,
                "Perf.W": 0.95,
                "Perf.1M": 0.80,
                "Perf.3M": 0.60,
                "Perf.6M": 0.30,
                "Perf.YTD": 0.15,
                "Perf.Y": 0.05,
                "ROC": 1.20,
                "Mom": 1.15,
                "macd_spread": 1.30,
                "Recommend.All": 0.65,
                "Recommend.MA": 0.80,
                "Recommend.Other": 0.60,
                "rsi_centered": 0.50,
                "rsi7_centered": 0.75,
                "aroon_spread": 1.40,
                "adx_directional_spread": 1.30,
                "stoch_rsi_centered": 1.15,
                "CCI20": 1.00,
            },
            "trend": {
                "close_vs_sma10": 1.45,
                "close_vs_sma20": 1.35,
                "close_vs_sma30": 1.25,
                "close_vs_ema10": 1.45,
                "close_vs_ema20": 1.35,
                "close_vs_ema30": 1.25,
                "short_trend_emergence": 1.60,
                "close_vs_sma50": 0.90,
                "close_vs_sma200": 0.55,
                "close_vs_ema50": 0.90,
                "close_vs_ema200": 0.55,
                "close_vs_vwap": 1.10,
                "close_vs_vwma": 1.05,
                "trend_alignment": 0.75,
                "bb_position": 1.20,
            },
            "quality": {
                "total_revenue_yoy_growth_ttm": 1.00,
                "total_revenue_qoq_growth_fq": 1.30,
                "ebitda_yoy_growth_ttm": 1.00,
                "ebitda_qoq_growth_fq": 1.35,
                "net_income_yoy_growth_ttm": 0.95,
                "net_income_qoq_growth_fq": 1.30,
                "free_cash_flow_yoy_growth_ttm": 1.05,
                "free_cash_flow_qoq_growth_fq": 1.40,
                "gross_margin": 0.90,
                "operating_margin": 1.10,
                "after_tax_margin": 0.90,
                "return_on_assets": 0.85,
                "return_on_equity": 0.95,
                "return_on_invested_capital": 1.20,
                "free_cash_flow_margin_ttm": 1.30,
                "buyback_yield": 0.70,
                "dividends_yield_current": 0.35,
                "eps_forward_growth": 1.65,
            },
            "valuation": {
                "price_earnings_ttm": 0.85,
                "price_earnings_growth_ttm": 1.25,
                "price_sales_current": 0.80,
                "price_book_fq": 0.75,
                "price_free_cash_flow_ttm": 1.10,
                "price_to_cash_f_operating_activities_ttm": 0.90,
                "enterprise_value_to_revenue_ttm": 0.80,
                "enterprise_value_to_ebit_ttm": 0.85,
                "enterprise_value_ebitda_ttm": 0.95,
                "earnings_yield": 1.20,
                "distance_from_52w_high": 1.15,
                "range_position_52w": 1.10,
            },
            "safety": {
                "current_ratio": 1.00,
                "quick_ratio": 1.00,
                "cash_ratio": 0.95,
                "short_term_cash_coverage": 1.10,
                "altman_z_score_ttm": 1.25,
                "debt_to_equity": 1.20,
                "debt_to_revenue_ttm": 1.15,
                "net_debt": 1.15,
                "beta_1_year": 0.70,
            },
        },
        component_directional_bias={
            "attention": DirectionalBias(
                positive_multiplier=0.55, negative_multiplier=1.40
            ),
            "event": DirectionalBias(
                positive_multiplier=1.18, negative_multiplier=0.85
            ),
            "momentum": DirectionalBias(
                positive_multiplier=1.12, negative_multiplier=0.75
            ),
            "trend": DirectionalBias(
                positive_multiplier=1.28, negative_multiplier=0.85
            ),
            "quality": DirectionalBias(
                positive_multiplier=1.18, negative_multiplier=1.30
            ),
            "valuation": DirectionalBias(
                positive_multiplier=1.10, negative_multiplier=0.80
            ),
            "safety": DirectionalBias(
                positive_multiplier=1.08, negative_multiplier=1.35
            ),
        },
        missing_component_scores_by_horizon={
            "days": {
                "quality": -0.55,
                "safety": -0.60,
                "trend": -0.45,
            },
            "weeks": {
                "quality": -0.65,
                "safety": -0.70,
                "trend": -0.40,
            },
            "months": {
                "quality": -0.80,
                "safety": -0.85,
                "valuation": -0.55,
            },
            "years": {
                "quality": -0.90,
                "safety": -0.95,
                "valuation": -0.75,
            },
        },
        performance_tracking_periods={
            "days": ["Perf.W"],
            "weeks": ["Perf.1M"],
            "months": ["Perf.YTD", "Perf.1M"],
            "years": ["Perf.Y"],
        },
        intro_metric_notes=[
            "Forward-edge active profile for weeks-to-months risk-adjusted outperformance.",
            "Short-term trend emergence: uses SMA10/20/30 and EMA10/20/30 comparisons to detect price crossing above short-term moving averages before SMA50/200 confirm. short_trend_emergence weight boosted to 1.60 (from 1.50). 200-day markers reduced to 0.55 (from 0.70). trend_alignment reduced to 0.75 (from 0.90) — the profile values emerging alignment, not established confirmation.",
            "Volatility contraction signals boosted: bb_squeeze=1.55, range_compression=1.50, volatility_contraction=1.45. Traditional volume attention signals further dampened (0.50-0.80) to shift attention from crowd participation toward coiled-energy detection.",
            "EPS forward growth boosted to 1.65 (from 1.50) — THE core differentiator. QoQ growth metrics boosted to 1.30-1.40 (from 1.20-1.25), YoY growth reduced to 0.95-1.05 (from 1.05-1.15). This profile cares about what is CHANGING, not what has been.",
            "EPS surprise boosted to 1.50 (from 1.40) — persistent beaters tend to outperform.",
            "Trailing performance damping more aggressive: Perf.Y=0.05, Perf.YTD=0.15, Perf.6M=0.30 (previously 0.10, 0.25, 0.40). Names already in multi-month runs are nearly invisible.",
            "Attention inversion stronger: positive attention damped to 0.55x (from 0.65x), negative amplified to 1.40x (from 1.30x). This is the most anti-crowd profile in the system.",
            "Quality and safety carry more weight: days quality=0.18 (from 0.16), weeks safety=0.16 (from 0.14), months safety=0.22 (from 0.19), years safety=0.28 (from 0.26). Missing-component penalties increased across all horizons.",
            "Stoch.RSI.K boosted to 1.15 (from 1.10) and CCI20 to 1.00 (from 0.95) for sharper early impulse detection.",
        ],
        confidence_multiplier=1.03,
    ),
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _format_score(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2f}"


def _format_confidence(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.0f}"


def _format_multiple(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}x"


def _format_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def _normalize_industries(
    industries: list[str] | str | None,
) -> list[str] | None:
    if industries is None:
        return None
    if isinstance(industries, str):
        normalized = [industries]
    else:
        normalized = [str(industry) for industry in industries if str(industry).strip()]
    return normalized or None


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _directional_hit_rate(values: list[float], expect_positive: bool) -> float | None:
    if not values:
        return None
    hit_count = (
        sum(1 for value in values if value > 0)
        if expect_positive
        else sum(1 for value in values if value < 0)
    )
    return (hit_count / len(values)) * 100.0


def list_move_prediction_scoring_profiles() -> dict[str, str]:
    return {
        name: profile.description for name, profile in PRESET_SCORING_PROFILES.items()
    }


def resolve_move_prediction_scoring_profile(
    scoring_profile: str | ScoringProfile | None,
) -> ScoringProfile:
    if scoring_profile is None:
        return PRESET_SCORING_PROFILES["balanced"]
    if isinstance(scoring_profile, ScoringProfile):
        return scoring_profile
    profile = PRESET_SCORING_PROFILES.get(str(scoring_profile))
    if profile is None:
        available = ", ".join(sorted(PRESET_SCORING_PROFILES))
        raise ValueError(
            f"Unknown scoring profile '{scoring_profile}'. Available profiles: {available}"
        )
    return profile


def _get_symbol_name(row: dict[str, Any]) -> str:
    ticker_view = row.get("ticker-view")
    if isinstance(ticker_view, dict):
        name = ticker_view.get("name")
        if name:
            return str(name)
    return str(row.get("symbol") or row.get("name") or "N/A")


def _get_company_name(row: dict[str, Any]) -> str:
    ticker_view = row.get("ticker-view")
    if isinstance(ticker_view, dict):
        description = ticker_view.get("description")
        if description:
            return str(description)

    return "N/A"


def _get_company_description(row: dict[str, Any]) -> str:
    ticker_view = row.get("ticker-view")
    if isinstance(ticker_view, dict):
        description = ticker_view.get("description")
        if description:
            return str(description)
    return str(row.get("name") or "")


def _resolve_horizon_weights(
    scoring_profile: ScoringProfile,
) -> dict[str, dict[str, float]]:
    resolved_weights = {
        horizon_name: dict(component_weights)
        for horizon_name, component_weights in DEFAULT_HORIZON_WEIGHTS.items()
    }
    for horizon_name, component_weights in scoring_profile.horizon_weights.items():
        resolved_weights.setdefault(horizon_name, {})
        resolved_weights[horizon_name].update(component_weights)
    return resolved_weights


def _build_report_file_name(
    report_slug: str,
    industries: list[str] | str | None,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
    scoring_profile_name: str | None = None,
    output_dir: Path | None = None,
) -> Path:
    base_output_dir = output_dir or LOG_DIR
    industries = _normalize_industries(industries)
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
    profile_segment = ""
    if scoring_profile_name and scoring_profile_name != "balanced":
        profile_segment = f"__profile_{_slugify(scoring_profile_name)}"
    return (
        base_output_dir
        / f"{report_slug}__{industry_segment}__{min_segment}__{max_segment}{profile_segment}.log"
    )


def _build_log_file_name(
    industries: list[str] | str | None,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
    scoring_profile_name: str | None = None,
    output_dir: Path | None = None,
) -> Path:
    return _build_report_file_name(
        report_slug="tradingview_move_prediction",
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        scoring_profile_name=scoring_profile_name,
        output_dir=output_dir,
    )


def _build_csv_file_name(
    industries: list[str] | str | None,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
    scoring_profile_name: str | None = None,
    output_dir: Path | None = None,
) -> Path:
    return _build_log_file_name(
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        scoring_profile_name=scoring_profile_name,
        output_dir=output_dir,
    ).with_suffix(".csv")


def _build_raw_csv_file_name(
    industries: list[str] | str | None,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
    scoring_profile_name: str | None = None,
    output_dir: Path | None = None,
) -> Path:
    csv_file = _build_csv_file_name(
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        scoring_profile_name=scoring_profile_name,
        output_dir=output_dir,
    )
    return csv_file.with_name(f"{csv_file.stem}__raw_data.csv")


def _build_tracking_log_file_name(
    industries: list[str] | str | None,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
    scoring_profile_name: str | None = None,
    output_dir: Path | None = None,
) -> Path:
    return _build_report_file_name(
        report_slug="tradingview_move_prediction_tracking",
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        scoring_profile_name=scoring_profile_name,
        output_dir=output_dir,
    )


def _serialize_csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _build_raw_csv_headers(scan_data: list[dict[str, Any]]) -> list[str]:
    if not scan_data:
        return ["symbol", "Company"]

    first_row = scan_data[0]
    headers = ["symbol", "Company"]
    headers.extend(key for key in first_row.keys() if key != "symbol")
    return headers


def _build_raw_csv_rows(scan_data: list[dict[str, Any]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in scan_data:
        csv_row = [_serialize_csv_value(row.get("symbol")), _get_company_name(row)]
        csv_row.extend(
            _serialize_csv_value(value) for key, value in row.items() if key != "symbol"
        )
        rows.append(csv_row)
    return rows


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


def _parse_event_days(value: Any) -> int | None:
    if value in (None, ""):
        return None

    parsed_datetime: datetime | None = None
    numeric_value = _coerce_numeric(value)
    if numeric_value is not None:
        if numeric_value > 1_000_000_000_000:
            parsed_datetime = datetime.fromtimestamp(
                numeric_value / 1000, tz=timezone.utc
            )
        elif numeric_value > 1_000_000_000:
            parsed_datetime = datetime.fromtimestamp(numeric_value, tz=timezone.utc)
    elif isinstance(value, str):
        stripped_value = value.strip()
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed_datetime = datetime.strptime(stripped_value[:19], fmt).replace(
                    tzinfo=timezone.utc
                )
                break
            except ValueError:
                continue

    if parsed_datetime is None:
        return None

    today = datetime.now(tz=timezone.utc).date()
    return (parsed_datetime.date() - today).days


def _build_derived_metrics(row: dict[str, Any]) -> dict[str, float | None]:
    close = _coerce_numeric(row.get("close"))
    cash_n_short_term_invest_fy = _coerce_numeric(
        row.get("cash_n_short_term_invest_fy")
    )
    cash_n_short_term_invest = _coerce_numeric(row.get("cash_n_short_term_invest_fq"))
    volume = _coerce_numeric(row.get("volume"))
    float_shares = _coerce_numeric(row.get("float_shares_outstanding"))
    avg_value_traded_10d = _coerce_numeric(row.get("AvgValue.Traded_10d"))
    market_cap = _coerce_numeric(row.get("market_cap_basic"))
    short_term_debt_fy = _coerce_numeric(row.get("short_term_debt_fy"))
    short_term_debt = _coerce_numeric(row.get("short_term_debt_fq"))
    gap = _coerce_numeric(row.get("gap"))
    atrp = _coerce_numeric(row.get("ATRP"))
    premarket_volume = _coerce_numeric(row.get("premarket_volume"))
    average_volume_10d = _coerce_numeric(row.get("average_volume_10d_calc"))
    macd_value = _coerce_numeric(row.get("MACD.macd"))
    macd_signal = _coerce_numeric(row.get("MACD.signal"))
    rsi_value = _coerce_numeric(row.get("RSI"))
    rsi7_value = _coerce_numeric(row.get("RSI7"))
    price_52w_high = _coerce_numeric(row.get("price_52_week_high"))
    price_52w_low = _coerce_numeric(row.get("price_52_week_low"))
    aroon_up = _coerce_numeric(row.get("Aroon.Up"))
    aroon_down = _coerce_numeric(row.get("Aroon.Down"))
    adx_plus = _coerce_numeric(row.get("ADX+DI"))
    adx_minus = _coerce_numeric(row.get("ADX-DI"))
    bb_upper = _coerce_numeric(row.get("BB.upper"))
    bb_lower = _coerce_numeric(row.get("BB.lower"))
    high_3m = _coerce_numeric(row.get("High.3M"))
    low_3m = _coerce_numeric(row.get("Low.3M"))
    vol_d = _coerce_numeric(row.get("Volatility.D"))
    vol_m = _coerce_numeric(row.get("Volatility.M"))
    eps_forecast_next_fq = _coerce_numeric(
        row.get("earnings_per_share_forecast_next_fq")
    )
    eps_actual_fq = _coerce_numeric(row.get("earnings_per_share_fq"))
    stoch_rsi_k = _coerce_numeric(row.get("Stoch.RSI.K"))
    eps_surprise_pct = _coerce_numeric(row.get("eps_surprise_percent_fq"))

    short_term_cash_coverage = _safe_ratio(
        cash_n_short_term_invest_fy,
        short_term_debt_fy,
    )
    if short_term_cash_coverage is None:
        short_term_cash_coverage = _safe_ratio(
            cash_n_short_term_invest,
            short_term_debt,
        )

    comparisons = []
    for average_field in ["SMA50", "SMA200", "EMA50", "EMA200", "VWAP", "VWMA"]:
        average_value = _coerce_numeric(row.get(average_field))
        if close is None or average_value is None:
            comparisons.append(None)
        else:
            comparisons.append(1.0 if close >= average_value else -1.0)

    valid_comparisons = [value for value in comparisons if value is not None]
    trend_alignment = (
        sum(valid_comparisons) / len(valid_comparisons) if valid_comparisons else None
    )

    distance_from_52w_high: float | None = None
    if close is not None and price_52w_high is not None and price_52w_high != 0:
        distance_from_52w_high = (close - price_52w_high) / abs(price_52w_high)

    range_position_52w: float | None = None
    if (
        close is not None
        and price_52w_high is not None
        and price_52w_low is not None
        and price_52w_high != price_52w_low
    ):
        range_position_52w = (close - price_52w_low) / (price_52w_high - price_52w_low)

    aroon_spread: float | None = None
    if aroon_up is not None and aroon_down is not None:
        aroon_spread = aroon_up - aroon_down

    adx_directional_spread: float | None = None
    if adx_plus is not None and adx_minus is not None:
        adx_directional_spread = adx_plus - adx_minus

    bb_position: float | None = None
    if (
        close is not None
        and bb_upper is not None
        and bb_lower is not None
        and bb_upper != bb_lower
    ):
        bb_position = (close - bb_lower) / (bb_upper - bb_lower)

    bb_squeeze: float | None = None
    if (
        bb_upper is not None
        and bb_lower is not None
        and close is not None
        and close != 0
    ):
        bb_squeeze = (bb_upper - bb_lower) / close

    short_ma_comparisons = []
    for short_avg_field in ["SMA10", "SMA20", "SMA30", "EMA10", "EMA20", "EMA30"]:
        short_avg_value = _coerce_numeric(row.get(short_avg_field))
        if close is None or short_avg_value is None:
            short_ma_comparisons.append(None)
        else:
            short_ma_comparisons.append(1.0 if close >= short_avg_value else -1.0)

    valid_short_comparisons = [v for v in short_ma_comparisons if v is not None]
    short_trend_emergence: float | None = (
        sum(valid_short_comparisons) / len(valid_short_comparisons)
        if valid_short_comparisons
        else None
    )

    range_compression: float | None = None
    if high_3m is not None and low_3m is not None and close is not None and close != 0:
        range_compression = (high_3m - low_3m) / close

    volatility_contraction: float | None = None
    if vol_d is not None and vol_m is not None and vol_m != 0:
        volatility_contraction = vol_d / vol_m

    eps_forward_growth: float | None = None
    if (
        eps_forecast_next_fq is not None
        and eps_actual_fq is not None
        and abs(eps_actual_fq) > 0.001
    ):
        eps_forward_growth = (eps_forecast_next_fq - eps_actual_fq) / abs(eps_actual_fq)

    stoch_rsi_centered: float | None = (
        None if stoch_rsi_k is None else stoch_rsi_k - 50.0
    )

    return {
        "float_turnover": _safe_ratio(volume, float_shares),
        "dollar_turnover_intensity": _safe_ratio(avg_value_traded_10d, market_cap),
        "short_term_cash_coverage": short_term_cash_coverage,
        "gap_severity": _safe_ratio(gap, atrp),
        "event_intensity": _safe_ratio(premarket_volume, average_volume_10d),
        "macd_spread": (
            None
            if macd_value is None or macd_signal is None
            else macd_value - macd_signal
        ),
        "rsi_centered": None if rsi_value is None else rsi_value - 50.0,
        "rsi7_centered": None if rsi7_value is None else rsi7_value - 50.0,
        "close_vs_sma50": comparisons[0],
        "close_vs_sma200": comparisons[1],
        "close_vs_ema50": comparisons[2],
        "close_vs_ema200": comparisons[3],
        "close_vs_vwap": comparisons[4],
        "close_vs_vwma": comparisons[5],
        "trend_alignment": trend_alignment,
        "distance_from_52w_high": distance_from_52w_high,
        "range_position_52w": range_position_52w,
        "aroon_spread": aroon_spread,
        "adx_directional_spread": adx_directional_spread,
        "bb_position": bb_position,
        "bb_squeeze": bb_squeeze,
        "close_vs_sma10": short_ma_comparisons[0],
        "close_vs_sma20": short_ma_comparisons[1],
        "close_vs_sma30": short_ma_comparisons[2],
        "close_vs_ema10": short_ma_comparisons[3],
        "close_vs_ema20": short_ma_comparisons[4],
        "close_vs_ema30": short_ma_comparisons[5],
        "short_trend_emergence": short_trend_emergence,
        "range_compression": range_compression,
        "volatility_contraction": volatility_contraction,
        "eps_forward_growth": eps_forward_growth,
        "stoch_rsi_centered": stoch_rsi_centered,
    }


def _build_metric_profiles(
    scan_data: list[dict[str, Any]],
    derived_metrics: list[dict[str, float | None]],
) -> dict[str, dict[str, float | int | None]]:
    profiles: dict[str, dict[str, float | int | None]] = {}
    all_fields = RAW_PROFILE_FIELDS + DERIVED_PROFILE_FIELDS

    for field_name in all_fields:
        if field_name in DERIVED_PROFILE_FIELDS:
            numeric_values = [
                value
                for value in (metrics.get(field_name) for metrics in derived_metrics)
                if value is not None
            ]
        else:
            numeric_values = [
                value
                for value in (_coerce_numeric(row.get(field_name)) for row in scan_data)
                if value is not None
            ]

        if not numeric_values:
            profiles[field_name] = {
                "count": 0,
                "mean": None,
                "median": None,
                "mad": None,
                "min": None,
                "max": None,
            }
            continue

        sorted_values = sorted(numeric_values)
        median_value = median(numeric_values)
        profiles[field_name] = {
            "count": len(numeric_values),
            "mean": sum(numeric_values) / len(numeric_values),
            "median": median_value,
            "mad": _median_absolute_deviation(numeric_values, median_value),
            "min": sorted_values[0],
            "max": sorted_values[-1],
        }

    return profiles


def _enrich_with_peer_metrics(scan_data: list[dict[str, Any]]) -> None:
    """Compute peer-relative size metrics and add them to each row in-place."""
    total_market_cap = sum(
        _coerce_numeric(row.get("market_cap_basic")) or 0 for row in scan_data
    )
    total_revenue = sum(
        _coerce_numeric(row.get("total_revenue")) or 0 for row in scan_data
    )

    for row in scan_data:
        mcap = _coerce_numeric(row.get("market_cap_basic"))
        rev = _coerce_numeric(row.get("total_revenue"))
        row["_peer_market_cap_share"] = (
            (mcap / total_market_cap * 100) if mcap and total_market_cap else None
        )
        row["_peer_revenue_share"] = (
            (rev / total_revenue * 100) if rev and total_revenue else None
        )


def _robust_signal(
    value: float | None, profile: dict[str, float | int | None] | None
) -> float | None:
    if value is None or not profile:
        return None

    median_value = profile.get("median")
    mad_value = profile.get("mad")
    if median_value is None:
        return None

    if mad_value in (None, 0):
        difference = value - float(median_value)
        if difference == 0:
            return 0.0
        return 1.0 if difference > 0 else -1.0

    scaled_mad = float(mad_value) * 1.4826
    if scaled_mad == 0:
        return 0.0

    return _clamp((value - float(median_value)) / scaled_mad, -3.0, 3.0)


def _field_signal(
    row: dict[str, Any],
    profiles: dict[str, dict[str, float | int | None]],
    field_name: str,
    invert: bool = False,
    positive_only: bool = False,
) -> float | None:
    raw_value = _coerce_numeric(row.get(field_name))
    if raw_value is None:
        return None
    if positive_only and raw_value <= 0:
        return None
    signal = _robust_signal(raw_value, profiles.get(field_name))
    if signal is None:
        return None
    return -signal if invert else signal


def _derived_signal(
    derived_row: dict[str, float | None],
    profiles: dict[str, dict[str, float | int | None]],
    field_name: str,
    invert: bool = False,
    positive_only: bool = False,
) -> float | None:
    raw_value = derived_row.get(field_name)
    if raw_value is None:
        return None
    if positive_only and raw_value <= 0:
        return None
    signal = _robust_signal(raw_value, profiles.get(field_name))
    if signal is None:
        return None
    return -signal if invert else signal


def _resolve_signal_weight(
    scoring_profile: ScoringProfile,
    component_name: str,
    signal_name: str,
) -> float:
    override = scoring_profile.component_signal_weights.get(component_name, {}).get(
        signal_name
    )
    if override is not None:
        return float(override)
    if signal_name in EXTENDED_SIGNALS:
        return 0.0
    return 1.0


def _apply_directional_bias(
    value: float | None,
    scoring_profile: ScoringProfile,
    component_name: str,
) -> float | None:
    if value is None:
        return None
    bias = scoring_profile.component_directional_bias.get(
        component_name, DirectionalBias()
    )
    if value > 0:
        return _clamp(value * bias.positive_multiplier, -3.0, 3.0)
    if value < 0:
        return _clamp(value * bias.negative_multiplier, -3.0, 3.0)
    return 0.0


def _resolve_missing_component_score(
    scoring_profile: ScoringProfile,
    horizon_name: str,
    component_name: str,
) -> float | None:
    value = scoring_profile.missing_component_scores_by_horizon.get(
        horizon_name, {}
    ).get(component_name)
    if value is None:
        return None
    return _clamp(float(value), -3.0, 3.0)


def _weighted_average_signals(
    signals: Mapping[str, float | None],
    scoring_profile: ScoringProfile,
    component_name: str,
) -> float | None:
    weighted_total = 0.0
    total_weight = 0.0

    for signal_name, signal_value in signals.items():
        if signal_value is None:
            continue
        signal_weight = _resolve_signal_weight(
            scoring_profile, component_name, signal_name
        )
        if signal_weight <= 0:
            continue
        weighted_total += signal_value * signal_weight
        total_weight += signal_weight

    if total_weight == 0:
        return None
    return weighted_total / total_weight


def _build_component_signal_map(
    row: dict[str, Any],
    derived_row: dict[str, float | None],
    profiles: dict[str, dict[str, float | int | None]],
) -> dict[str, dict[str, float | None]]:
    return {
        "attention": {
            "relative_volume_10d_calc": _field_signal(
                row, profiles, "relative_volume_10d_calc"
            ),
            "float_turnover": _derived_signal(derived_row, profiles, "float_turnover"),
            "dollar_turnover_intensity": _derived_signal(
                derived_row, profiles, "dollar_turnover_intensity"
            ),
            "Value.Traded": _field_signal(row, profiles, "Value.Traded"),
            "AvgValue.Traded_10d": _field_signal(row, profiles, "AvgValue.Traded_10d"),
            "bb_squeeze": _derived_signal(
                derived_row, profiles, "bb_squeeze", invert=True
            ),
            "range_compression": _derived_signal(
                derived_row, profiles, "range_compression", invert=True
            ),
            "volatility_contraction": _derived_signal(
                derived_row, profiles, "volatility_contraction", invert=True
            ),
        },
        "event": {
            "premarket_change": _field_signal(row, profiles, "premarket_change"),
            "postmarket_change": _field_signal(row, profiles, "postmarket_change"),
            "gap": _field_signal(row, profiles, "gap"),
            "gap_severity": _derived_signal(derived_row, profiles, "gap_severity"),
            "event_intensity": _derived_signal(
                derived_row, profiles, "event_intensity"
            ),
            "eps_surprise_percent_fq": _field_signal(
                row, profiles, "eps_surprise_percent_fq"
            ),
        },
        "momentum": {
            "change": _field_signal(row, profiles, "change"),
            "Perf.5D": _field_signal(row, profiles, "Perf.5D"),
            "Perf.W": _field_signal(row, profiles, "Perf.W"),
            "Perf.1M": _field_signal(row, profiles, "Perf.1M"),
            "Perf.3M": _field_signal(row, profiles, "Perf.3M"),
            "Perf.YTD": _field_signal(row, profiles, "Perf.YTD"),
            "Perf.Y": _field_signal(row, profiles, "Perf.Y"),
            "ROC": _field_signal(row, profiles, "ROC"),
            "Mom": _field_signal(row, profiles, "Mom"),
            "macd_spread": _derived_signal(derived_row, profiles, "macd_spread"),
            "Recommend.All": _field_signal(row, profiles, "Recommend.All"),
            "Recommend.MA": _field_signal(row, profiles, "Recommend.MA"),
            "Recommend.Other": _field_signal(row, profiles, "Recommend.Other"),
            "rsi_centered": _derived_signal(derived_row, profiles, "rsi_centered"),
            "rsi7_centered": _derived_signal(derived_row, profiles, "rsi7_centered"),
            "Perf.6M": _field_signal(row, profiles, "Perf.6M"),
            "aroon_spread": _derived_signal(derived_row, profiles, "aroon_spread"),
            "adx_directional_spread": _derived_signal(
                derived_row, profiles, "adx_directional_spread"
            ),
            "stoch_rsi_centered": _derived_signal(
                derived_row, profiles, "stoch_rsi_centered"
            ),
            "CCI20": _field_signal(row, profiles, "CCI20"),
        },
        "trend": {
            "close_vs_sma50": derived_row.get("close_vs_sma50"),
            "close_vs_sma200": derived_row.get("close_vs_sma200"),
            "close_vs_ema50": derived_row.get("close_vs_ema50"),
            "close_vs_ema200": derived_row.get("close_vs_ema200"),
            "close_vs_vwap": derived_row.get("close_vs_vwap"),
            "close_vs_vwma": derived_row.get("close_vs_vwma"),
            "trend_alignment": derived_row.get("trend_alignment"),
            "bb_position": _derived_signal(derived_row, profiles, "bb_position"),
            "close_vs_sma10": derived_row.get("close_vs_sma10"),
            "close_vs_sma20": derived_row.get("close_vs_sma20"),
            "close_vs_sma30": derived_row.get("close_vs_sma30"),
            "close_vs_ema10": derived_row.get("close_vs_ema10"),
            "close_vs_ema20": derived_row.get("close_vs_ema20"),
            "close_vs_ema30": derived_row.get("close_vs_ema30"),
            "short_trend_emergence": derived_row.get("short_trend_emergence"),
        },
        "quality": {
            "total_revenue_yoy_growth_ttm": _field_signal(
                row, profiles, "total_revenue_yoy_growth_ttm"
            ),
            "total_revenue_qoq_growth_fq": _field_signal(
                row, profiles, "total_revenue_qoq_growth_fq"
            ),
            "ebitda_yoy_growth_ttm": _field_signal(
                row, profiles, "ebitda_yoy_growth_ttm"
            ),
            "ebitda_qoq_growth_fq": _field_signal(
                row, profiles, "ebitda_qoq_growth_fq"
            ),
            "net_income_yoy_growth_ttm": _field_signal(
                row, profiles, "net_income_yoy_growth_ttm"
            ),
            "net_income_qoq_growth_fq": _field_signal(
                row, profiles, "net_income_qoq_growth_fq"
            ),
            "free_cash_flow_yoy_growth_ttm": _field_signal(
                row, profiles, "free_cash_flow_yoy_growth_ttm"
            ),
            "free_cash_flow_qoq_growth_fq": _field_signal(
                row, profiles, "free_cash_flow_qoq_growth_fq"
            ),
            "gross_margin": _field_signal(row, profiles, "gross_margin"),
            "operating_margin": _field_signal(row, profiles, "operating_margin"),
            "after_tax_margin": _field_signal(row, profiles, "after_tax_margin"),
            "return_on_assets": _field_signal(row, profiles, "return_on_assets"),
            "return_on_equity": _field_signal(row, profiles, "return_on_equity"),
            "return_on_invested_capital": _field_signal(
                row, profiles, "return_on_invested_capital"
            ),
            "free_cash_flow_margin_ttm": _field_signal(
                row, profiles, "free_cash_flow_margin_ttm"
            ),
            "buyback_yield": _field_signal(row, profiles, "buyback_yield"),
            "dividends_yield_current": _field_signal(
                row, profiles, "dividends_yield_current"
            ),
            "eps_forward_growth": _derived_signal(
                derived_row, profiles, "eps_forward_growth"
            ),
        },
        "valuation": {
            "price_earnings_ttm": _field_signal(
                row, profiles, "price_earnings_ttm", invert=True, positive_only=True
            ),
            "price_earnings_growth_ttm": _field_signal(
                row,
                profiles,
                "price_earnings_growth_ttm",
                invert=True,
                positive_only=True,
            ),
            "price_sales_current": _field_signal(
                row, profiles, "price_sales_current", invert=True, positive_only=True
            ),
            "price_book_fq": _field_signal(
                row, profiles, "price_book_fq", invert=True, positive_only=True
            ),
            "price_free_cash_flow_ttm": _field_signal(
                row,
                profiles,
                "price_free_cash_flow_ttm",
                invert=True,
                positive_only=True,
            ),
            "price_to_cash_f_operating_activities_ttm": _field_signal(
                row,
                profiles,
                "price_to_cash_f_operating_activities_ttm",
                invert=True,
                positive_only=True,
            ),
            "enterprise_value_to_revenue_ttm": _field_signal(
                row,
                profiles,
                "enterprise_value_to_revenue_ttm",
                invert=True,
                positive_only=True,
            ),
            "enterprise_value_to_ebit_ttm": _field_signal(
                row,
                profiles,
                "enterprise_value_to_ebit_ttm",
                invert=True,
                positive_only=True,
            ),
            "enterprise_value_ebitda_ttm": _field_signal(
                row,
                profiles,
                "enterprise_value_ebitda_ttm",
                invert=True,
                positive_only=True,
            ),
            "earnings_yield": _field_signal(row, profiles, "earnings_yield"),
            "distance_from_52w_high": _derived_signal(
                derived_row, profiles, "distance_from_52w_high", invert=True
            ),
            "range_position_52w": _derived_signal(
                derived_row, profiles, "range_position_52w", invert=True
            ),
        },
        "safety": {
            "current_ratio": _field_signal(row, profiles, "current_ratio"),
            "quick_ratio": _field_signal(row, profiles, "quick_ratio"),
            "cash_ratio": _field_signal(row, profiles, "cash_ratio"),
            "short_term_cash_coverage": _derived_signal(
                derived_row,
                profiles,
                "short_term_cash_coverage",
                positive_only=True,
            ),
            "altman_z_score_ttm": _field_signal(row, profiles, "altman_z_score_ttm"),
            "debt_to_equity": _field_signal(
                row, profiles, "debt_to_equity", invert=True
            ),
            "debt_to_revenue_ttm": _field_signal(
                row, profiles, "debt_to_revenue_ttm", invert=True
            ),
            "net_debt": _field_signal(row, profiles, "net_debt", invert=True),
            "beta_1_year": _field_signal(row, profiles, "beta_1_year", invert=True),
        },
        "scale": {
            "market_cap_basic": _field_signal(row, profiles, "market_cap_basic"),
            "enterprise_value_fq": _field_signal(row, profiles, "enterprise_value_fq"),
            "total_revenue": _field_signal(row, profiles, "total_revenue"),
            "number_of_employees": _field_signal(row, profiles, "number_of_employees"),
            "_peer_market_cap_share": _field_signal(
                row, profiles, "_peer_market_cap_share"
            ),
            "_peer_revenue_share": _field_signal(row, profiles, "_peer_revenue_share"),
        },
    }


def _build_component_scores(
    row: dict[str, Any],
    derived_row: dict[str, float | None],
    profiles: dict[str, dict[str, float | int | None]],
    scoring_profile: ScoringProfile,
) -> dict[str, float | None]:
    component_signal_map = _build_component_signal_map(row, derived_row, profiles)
    component_scores: dict[str, float | None] = {}

    for component_name in COMPONENT_ORDER:
        component_scores[component_name] = _apply_directional_bias(
            _weighted_average_signals(
                component_signal_map.get(component_name, {}),
                scoring_profile,
                component_name,
            ),
            scoring_profile,
            component_name,
        )

    return component_scores


def _direction_label(score: float | None) -> str:
    if score is None:
        return "N/A"
    if score >= STRONG_MOVE_SCORE_THRESHOLD:
        return "Strong Up"
    if score >= DIRECTIONAL_MOVE_SCORE_THRESHOLD:
        return "Up"
    if score <= -STRONG_MOVE_SCORE_THRESHOLD:
        return "Strong Down"
    if score <= -DIRECTIONAL_MOVE_SCORE_THRESHOLD:
        return "Down"
    return "Neutral"


def _log_score_scale_summary(log_file: Path) -> None:
    log_to_file(log_file, "Score scale summary")
    log_to_file(log_file, "-" * 160)
    log_to_file(
        log_file,
        f"Scores are clamped to a -3.00 to +3.00 range. Bigger absolute values mean a name stands out more versus the current scan universe.",
    )
    log_to_file(
        log_file,
        f"+{STRONG_MOVE_SCORE_THRESHOLD:.2f} to +3.00: Strong Up | standout upside setup with unusually strong alignment.",
    )
    log_to_file(
        log_file,
        f"+{DIRECTIONAL_MOVE_SCORE_THRESHOLD:.2f} to +{STRONG_MOVE_SCORE_THRESHOLD:.2f}: Up | constructive positive setup, but less extreme.",
    )
    log_to_file(
        log_file,
        f"-{DIRECTIONAL_MOVE_SCORE_THRESHOLD:.2f} to +{DIRECTIONAL_MOVE_SCORE_THRESHOLD:.2f}: Neutral | mixed or only modestly differentiated from the pack.",
    )
    log_to_file(
        log_file,
        f"-{STRONG_MOVE_SCORE_THRESHOLD:.2f} to -{DIRECTIONAL_MOVE_SCORE_THRESHOLD:.2f}: Down | constructive bearish setup, but not an extreme outlier.",
    )
    log_to_file(
        log_file,
        f"-3.00 to -{STRONG_MOVE_SCORE_THRESHOLD:.2f}: Strong Down | standout downside setup with unusually weak alignment.",
    )
    log_to_file(log_file, "")


def _classify_setup(
    score: float | None, component_scores: dict[str, float | None]
) -> str:
    if score is None:
        return "insufficient-data"

    ranked_components = [
        component_name
        for component_name, value in sorted(
            (
                (component_name, value)
                for component_name, value in component_scores.items()
                if value is not None
            ),
            key=lambda item: abs(item[1]),
            reverse=True,
        )
    ]
    top_components = set(ranked_components[:2])

    if score >= 0.35:
        if {"attention", "momentum"}.issubset(top_components):
            return "attention-led continuation"
        if {"trend", "momentum"}.issubset(top_components):
            return "trend continuation"
        if {"quality", "valuation"}.issubset(top_components):
            return "fundamental rerating"
        if "event" in top_components:
            return "event-driven upside"
        return "broad bullish alignment"

    if score <= -0.35:
        if {"attention", "event"}.issubset(top_components):
            return "event-led downside"
        if {"trend", "momentum"}.issubset(top_components):
            return "trend breakdown"
        if "safety" in top_components:
            return "balance-sheet risk unwind"
        return "broad bearish alignment"

    return "mixed / neutral"


def _build_horizon_prediction(
    horizon_name: str,
    component_scores: dict[str, float | None],
    earnings_days_to_next: int | None,
    scoring_profile: ScoringProfile,
    horizon_weights: dict[str, dict[str, float]],
) -> dict[str, float | str | None]:
    weighted_score = 0.0
    used_weight = 0.0
    total_possible_weight = sum(horizon_weights[horizon_name].values())

    for component_name, component_weight in horizon_weights[horizon_name].items():
        component_value = component_scores.get(component_name)
        if component_value is None:
            component_value = _resolve_missing_component_score(
                scoring_profile,
                horizon_name,
                component_name,
            )
            if component_value is None:
                continue
        weighted_score += component_value * component_weight
        used_weight += component_weight

    if used_weight == 0:
        return {
            "score": None,
            "direction": "N/A",
            "confidence": None,
            "coverage": 0.0,
            "setup": "insufficient-data",
        }

    final_score = weighted_score / used_weight
    coverage_ratio = (
        used_weight / total_possible_weight if total_possible_weight else 0.0
    )
    event_penalty = 0.0
    if earnings_days_to_next is not None and earnings_days_to_next >= 0:
        if horizon_name == "days":
            if earnings_days_to_next <= 3:
                event_penalty = 18.0
            elif earnings_days_to_next <= 7:
                event_penalty = 10.0
        elif horizon_name == "weeks":
            if earnings_days_to_next <= 7:
                event_penalty = 10.0
            elif earnings_days_to_next <= 14:
                event_penalty = 5.0
        elif horizon_name in {"months", "years"} and earnings_days_to_next <= 7:
            event_penalty = 4.0 if horizon_name == "months" else 2.0

    final_score = _clamp(final_score, -3.0, 3.0)
    confidence = _clamp(
        (28.0 + (abs(final_score) * 18.0) + (coverage_ratio * 40.0) - event_penalty)
        * scoring_profile.confidence_multiplier
        + scoring_profile.confidence_offset,
        5.0,
        99.0,
    )
    return {
        "score": final_score,
        "direction": _direction_label(final_score),
        "confidence": confidence,
        "coverage": coverage_ratio,
        "setup": _classify_setup(final_score, component_scores),
    }


def _build_prediction_rows(
    scan_data: list[dict[str, Any]],
    profiles: dict[str, dict[str, float | int | None]],
    derived_metrics: list[dict[str, float | None]],
    scoring_profile: ScoringProfile,
) -> list[dict[str, Any]]:
    prediction_rows: list[dict[str, Any]] = []
    horizon_weights = _resolve_horizon_weights(scoring_profile)

    for row, derived_row in zip(scan_data, derived_metrics):
        component_scores = _build_component_scores(
            row, derived_row, profiles, scoring_profile
        )
        earnings_days_to_next = _parse_event_days(row.get("earnings_release_next_date"))
        horizons = {
            horizon_name: _build_horizon_prediction(
                horizon_name,
                component_scores,
                earnings_days_to_next,
                scoring_profile,
                horizon_weights,
            )
            for horizon_name in horizon_weights
        }
        prediction_rows.append(
            {
                "row": row,
                "derived": derived_row,
                "components": component_scores,
                "horizons": horizons,
                "earnings_days_to_next": earnings_days_to_next,
                "scoring_profile": scoring_profile.name,
            }
        )

    return prediction_rows


def _resolve_performance_tracking_periods(
    scoring_profile: ScoringProfile,
) -> dict[str, list[str]]:
    tracking_periods = {
        horizon_name: list(periods)
        for horizon_name, periods in DEFAULT_PERFORMANCE_TRACKING_PERIODS.items()
    }
    for horizon_name, periods in scoring_profile.performance_tracking_periods.items():
        tracking_periods[horizon_name] = list(periods)
    return tracking_periods


def _build_csv_headers(horizon_names: list[str]) -> list[str]:
    headers = list(ENTRY_METADATA_FIELDS)
    headers.insert(1, "Company")
    headers.extend(
        [
            "scoring_profile",
            "market_cap_basic",
            "close",
            "Perf.5D",
            "Perf.W",
            "Perf.1M",
            "Perf.YTD",
            "Perf.Y",
            "Perf.5Y",
            "earnings_days_to_next",
            "float_shares_percent_current",
            "float_turnover",
            "dollar_turnover_intensity",
            "short_term_cash_coverage",
            "gap_severity",
            "event_intensity",
            "trend_alignment",
        ]
    )

    for component_name in COMPONENT_ORDER:
        headers.append(f"component_{component_name}")

    for horizon_name in horizon_names:
        headers.extend(
            [
                f"{horizon_name}_score",
                f"{horizon_name}_direction",
                f"{horizon_name}_confidence",
                f"{horizon_name}_coverage",
                f"{horizon_name}_setup",
            ]
        )

    return headers


def _build_csv_rows(
    prediction_rows: list[dict[str, Any]], horizon_names: list[str]
) -> list[list[str]]:
    rows: list[list[str]] = []
    for prediction in prediction_rows:
        row = prediction["row"]
        derived = prediction["derived"]
        components = prediction["components"]
        horizons = prediction["horizons"]

        csv_row = [str(row.get(field_name, "")) for field_name in ENTRY_METADATA_FIELDS]
        csv_row.insert(1, _get_company_name(row))
        csv_row.extend(
            [
                str(prediction.get("scoring_profile", "")),
                str(row.get("market_cap_basic", "")),
                str(row.get("close", "")),
                str(row.get("Perf.5D", "")),
                str(row.get("Perf.W", "")),
                str(row.get("Perf.1M", "")),
                str(row.get("Perf.YTD", "")),
                str(row.get("Perf.Y", "")),
                str(row.get("Perf.5Y", "")),
                str(prediction.get("earnings_days_to_next", "")),
                str(row.get("float_shares_percent_current", "")),
                str(derived.get("float_turnover", "")),
                str(derived.get("dollar_turnover_intensity", "")),
                str(derived.get("short_term_cash_coverage", "")),
                str(derived.get("gap_severity", "")),
                str(derived.get("event_intensity", "")),
                str(derived.get("trend_alignment", "")),
            ]
        )

        for component_name in COMPONENT_ORDER:
            csv_row.append(str(components.get(component_name, "")))

        for horizon_name in horizon_names:
            horizon = horizons[horizon_name]
            csv_row.extend(
                [
                    str(horizon.get("score", "")),
                    str(horizon.get("direction", "")),
                    str(horizon.get("confidence", "")),
                    str(horizon.get("coverage", "")),
                    str(horizon.get("setup", "")),
                ]
            )

        rows.append(csv_row)

    return rows


def _row_label(prediction: dict[str, Any]) -> str:
    row = prediction["row"]
    company_description = _get_company_description(row)
    ticker = _get_symbol_name(row)
    if company_description:
        return f"{ticker} ({company_description})"
    return ticker


def _log_methodology(log_file: Path, scoring_profile: ScoringProfile) -> None:
    if scoring_profile.intro_metric_notes:
        log_to_file(log_file, "Added metrics and tracking notes")
        log_to_file(log_file, "-" * 160)
        for note in scoring_profile.intro_metric_notes:
            log_to_file(log_file, f"- {note}")
        log_to_file(log_file, "")

    log_to_file(log_file, "Methodology")
    log_to_file(log_file, "-" * 160)
    log_to_file(
        log_file,
        "This report is a heuristic move-direction model, not a trained statistical model. It combines activity, event pressure, momentum, trend, quality, valuation, safety, and scale signals.",
    )
    log_to_file(
        log_file,
        "Scores are built from scan-relative robust normalization so the report ranks names against the current returned universe instead of relying on fixed global thresholds.",
    )
    log_to_file(
        log_file,
        "Score flow: raw field values and derived metrics are converted into robust scan-relative signals, grouped into component scores, optionally biased by the selected scoring profile, then combined into horizon scores using horizon-specific component weights.",
    )
    log_to_file(
        log_file,
        "Direction bias works by multiplying positive and negative component scores differently. This lets you emphasize upside continuation, recovery, or fragility-short setups without rewriting the core signal logic.",
    )
    log_to_file(
        log_file,
        "Profiles can also assign fallback component scores when important evidence is missing. That makes quality, valuation, safety, or tactical-confirmation gaps show up explicitly instead of disappearing through partial-weight renormalization.",
    )
    log_to_file(
        log_file,
        "Safety also includes a short-term cash coverage input that uses cash_n_short_term_invest_fy divided by short_term_debt_fy, and falls back to cash_n_short_term_invest_fq divided by short_term_debt_fq when the yearly pair is not available.",
    )
    log_to_file(
        log_file,
        f"Scoring profile: {scoring_profile.name} | {scoring_profile.description}",
    )
    log_to_file(log_file, "")
    _log_score_scale_summary(log_file)


def _log_scoring_profile_details(
    log_file: Path, scoring_profile: ScoringProfile
) -> None:
    log_to_file(log_file, "Scoring profile details")
    log_to_file(log_file, "-" * 160)

    horizon_weights = _resolve_horizon_weights(scoring_profile)
    for horizon_name, component_weights in horizon_weights.items():
        weight_summary = ", ".join(
            f"{component_name}={component_weight:.2f}"
            for component_name, component_weight in component_weights.items()
        )
        log_to_file(
            log_file,
            f"{HORIZON_TITLES.get(horizon_name, horizon_name)}: {weight_summary}",
        )

    if scoring_profile.component_signal_weights:
        log_to_file(log_file, "")
        log_to_file(log_file, "Signal emphasis overrides")
        for component_name in COMPONENT_ORDER:
            signal_weights = scoring_profile.component_signal_weights.get(
                component_name
            )
            if not signal_weights:
                continue
            signal_summary = ", ".join(
                f"{signal_name}={signal_weight:.2f}"
                for signal_name, signal_weight in signal_weights.items()
            )
            log_to_file(log_file, f"  {component_name}: {signal_summary}")

    if scoring_profile.component_directional_bias:
        log_to_file(log_file, "")
        log_to_file(log_file, "Directional bias overrides")
        for component_name in COMPONENT_ORDER:
            bias = scoring_profile.component_directional_bias.get(component_name)
            if bias is None:
                continue
            log_to_file(
                log_file,
                (
                    f"  {component_name}: positive x{bias.positive_multiplier:.2f}, "
                    f"negative x{bias.negative_multiplier:.2f}"
                ),
            )

    if scoring_profile.missing_component_scores_by_horizon:
        log_to_file(log_file, "")
        log_to_file(log_file, "Missing-component fallback scores")
        for (
            horizon_name,
            missing_scores,
        ) in scoring_profile.missing_component_scores_by_horizon.items():
            missing_summary = ", ".join(
                f"{component_name}={component_score:.2f}"
                for component_name, component_score in missing_scores.items()
            )
            log_to_file(
                log_file,
                f"  {HORIZON_TITLES.get(horizon_name, horizon_name)}: {missing_summary}",
            )
    log_to_file(log_file, "")


def _log_horizon_section(
    log_file: Path,
    prediction_rows: list[dict[str, Any]],
    horizon_name: str,
) -> None:
    horizon_title = HORIZON_TITLES[horizon_name]
    scored_predictions = [
        prediction
        for prediction in prediction_rows
        if prediction["horizons"][horizon_name]["score"] is not None
    ]

    log_to_file(log_file, horizon_title)
    log_to_file(log_file, "-" * 160)
    log_to_file(
        log_file,
        (
            f"{'Ticker':<12} {'Company':<28} {'Industry':<26} {'MCap':>10} {'Float%':>8} {'Score':>8} {'Dir':<12} {'Conf':>6} "
            f"{'Attn':>8} {'Event':>8} {'Mom':>8} {'Trend':>8} {'Qual':>8} {'Value':>8} {'Safe':>8} {'Scale':>8} {'STSafe':>8} {'Setup':<28}"
        ),
    )
    log_to_file(log_file, "-" * 160)

    top_upside = sorted(
        scored_predictions,
        key=lambda prediction: _sort_key_desc(
            prediction["horizons"][horizon_name]["score"]
        ),
        reverse=True,
    )[:TOP_SECTION_ROWS]

    for prediction in top_upside:
        row = prediction["row"]
        horizon = prediction["horizons"][horizon_name]
        components = prediction["components"]
        log_to_file(
            log_file,
            (
                f"{_get_symbol_name(row):<12} {_get_company_name(row)[:28]:<28} {str(row.get('industry') or '')[:26]:<26} "
                f"{_format_market_cap(_coerce_numeric(row.get('market_cap_basic'))):>10} {_format_percent(_coerce_numeric(row.get('float_shares_percent_current'))):>8} {_format_score(horizon['score']):>8} "
                f"{str(horizon['direction']):<12} {_format_confidence(horizon['confidence']):>6} "
                f"{_format_score(components['attention']):>8} {_format_score(components['event']):>8} {_format_score(components['momentum']):>8} "
                f"{_format_score(components['trend']):>8} {_format_score(components['quality']):>8} {_format_score(components['valuation']):>8} "
                f"{_format_score(components['safety']):>8} {_format_score(components.get('scale')):>8} {_format_multiple(prediction['derived'].get('short_term_cash_coverage')):>8} {str(horizon['setup'])[:28]:<28}"
            ),
        )

    log_to_file(log_file, "")
    log_to_file(log_file, f"{horizon_title} downside candidates")
    log_to_file(log_file, "-" * 160)

    top_downside = sorted(
        scored_predictions,
        key=lambda prediction: _sort_key_desc(
            -prediction["horizons"][horizon_name]["score"]
        ),
        reverse=True,
    )[:TOP_SECTION_ROWS]

    for prediction in top_downside:
        row = prediction["row"]
        horizon = prediction["horizons"][horizon_name]
        components = prediction["components"]
        log_to_file(
            log_file,
            (
                f"{_get_symbol_name(row):<12} {_get_company_name(row)[:28]:<28} {str(row.get('industry') or '')[:26]:<26} "
                f"{_format_market_cap(_coerce_numeric(row.get('market_cap_basic'))):>10} {_format_percent(_coerce_numeric(row.get('float_shares_percent_current'))):>8} {_format_score(horizon['score']):>8} "
                f"{str(horizon['direction']):<12} {_format_confidence(horizon['confidence']):>6} "
                f"{_format_score(components['attention']):>8} {_format_score(components['event']):>8} {_format_score(components['momentum']):>8} "
                f"{_format_score(components['trend']):>8} {_format_score(components['quality']):>8} {_format_score(components['valuation']):>8} "
                f"{_format_score(components['safety']):>8} {_format_score(components.get('scale')):>8} {_format_multiple(prediction['derived'].get('short_term_cash_coverage')):>8} {str(horizon['setup'])[:28]:<28}"
            ),
        )

    log_to_file(log_file, "")


def _log_consensus_section(
    log_file: Path, prediction_rows: list[dict[str, Any]]
) -> None:
    log_to_file(log_file, "Consensus and divergence")
    log_to_file(log_file, "-" * 160)

    bullish_consensus = [
        prediction
        for prediction in prediction_rows
        if all(
            prediction["horizons"][horizon_name]["score"] is not None
            and prediction["horizons"][horizon_name]["score"] >= 0.35
            for horizon_name in prediction["horizons"]
        )
    ]
    bearish_consensus = [
        prediction
        for prediction in prediction_rows
        if all(
            prediction["horizons"][horizon_name]["score"] is not None
            and prediction["horizons"][horizon_name]["score"] <= -0.35
            for horizon_name in prediction["horizons"]
        )
    ]
    short_positive_long_negative = [
        prediction
        for prediction in prediction_rows
        if prediction["horizons"]["days"]["score"] is not None
        and prediction["horizons"]["months"]["score"] is not None
        and prediction["horizons"]["days"]["score"] >= 0.35
        and prediction["horizons"]["months"]["score"] <= -0.35
    ]
    short_negative_long_positive = [
        prediction
        for prediction in prediction_rows
        if prediction["horizons"]["days"]["score"] is not None
        and prediction["horizons"]["months"]["score"] is not None
        and prediction["horizons"]["days"]["score"] <= -0.35
        and prediction["horizons"]["months"]["score"] >= 0.35
    ]

    def _log_bucket(title: str, items: list[dict[str, Any]], sort_horizon: str) -> None:
        log_to_file(log_file, title)
        if not items:
            log_to_file(log_file, "  none")
            log_to_file(log_file, "")
            return
        ranked_items = sorted(
            items,
            key=lambda prediction: _sort_key_desc(
                abs(prediction["horizons"][sort_horizon]["score"])
            ),
            reverse=True,
        )[:20]
        for prediction in ranked_items:
            row = prediction["row"]
            log_to_file(
                log_file,
                (
                    f"  {_row_label(prediction)} | industry={row.get('industry') or 'N/A'} | "
                    f"mcap={_format_market_cap(_coerce_numeric(row.get('market_cap_basic')))} | "
                    f"days={_format_score(prediction['horizons']['days']['score'])} | "
                    f"weeks={_format_score(prediction['horizons']['weeks']['score'])} | "
                    f"months={_format_score(prediction['horizons']['months']['score'])} | "
                    f"years={_format_score(prediction['horizons']['years']['score'])} | "
                    f"scale={_format_score(prediction['components'].get('scale'))}"
                ),
            )
        log_to_file(log_file, "")

    _log_bucket(
        "Bullish across days, weeks, months, and years", bullish_consensus, "years"
    )
    _log_bucket(
        "Bearish across days, weeks, months, and years", bearish_consensus, "years"
    )
    _log_bucket(
        "Short-term upside but long-term drag", short_positive_long_negative, "days"
    )
    _log_bucket(
        "Short-term weakness but long-term recovery",
        short_negative_long_positive,
        "years",
    )


def _log_blind_spot_sections(
    log_file: Path, prediction_rows: list[dict[str, Any]]
) -> None:
    log_to_file(log_file, "Blind spots and alternative opportunity lenses")
    log_to_file(log_file, "-" * 160)

    def _rank_rows(rows: list[dict[str, Any]], score_builder) -> list[dict[str, Any]]:
        ranked: list[tuple[dict[str, Any], float]] = []
        for prediction in rows:
            score = score_builder(prediction)
            if score is None:
                continue
            ranked.append((prediction, score))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return [prediction for prediction, _ in ranked[:TOP_SECTION_ROWS]]

    underfollowed_quality = _rank_rows(
        [
            prediction
            for prediction in prediction_rows
            if (prediction["components"].get("quality") or -99) >= 0.70
            and (prediction["components"].get("valuation") or -99) >= 0.30
            and (prediction["components"].get("safety") or -99) >= 0.20
            and (prediction["components"].get("attention") or 99) <= 0.10
            and (prediction["horizons"].get("years", {}).get("score") or -99) >= 0.45
        ],
        lambda prediction: (
            (prediction["components"].get("quality") or 0)
            + (prediction["components"].get("valuation") or 0)
            + (prediction["components"].get("safety") or 0)
            - max(prediction["components"].get("attention") or 0, 0)
        ),
    )

    crowded_fragility = _rank_rows(
        [
            prediction
            for prediction in prediction_rows
            if (prediction["components"].get("attention") or -99) >= 0.75
            and (prediction["components"].get("momentum") or -99) >= 0.25
            and (
                (prediction["components"].get("safety") or 99) <= -0.25
                or (prediction["components"].get("valuation") or 99) <= -0.25
            )
        ],
        lambda prediction: (
            (prediction["components"].get("attention") or 0)
            + (prediction["components"].get("momentum") or 0)
            - (prediction["components"].get("safety") or 0)
            - (prediction["components"].get("valuation") or 0)
        ),
    )

    event_dislocation = _rank_rows(
        [
            prediction
            for prediction in prediction_rows
            if abs(prediction["components"].get("event") or 0) >= 0.85
            and abs(prediction["components"].get("trend") or 0) <= 0.15
        ],
        lambda prediction: abs(prediction["components"].get("event") or 0),
    )

    recovery_candidates = _rank_rows(
        [
            prediction
            for prediction in prediction_rows
            if (prediction["horizons"].get("days", {}).get("score") or 99) <= 0.25
            and (prediction["horizons"].get("months", {}).get("score") or -99) >= 0.35
            and (prediction["components"].get("valuation") or -99) >= 0.35
            and (prediction["components"].get("quality") or -99) >= 0.15
        ],
        lambda prediction: (
            (prediction["horizons"].get("months", {}).get("score") or 0)
            + (prediction["components"].get("valuation") or 0)
            + (prediction["components"].get("quality") or 0)
            - max(prediction["horizons"].get("days", {}).get("score") or 0, 0)
        ),
    )

    categories = [
        (
            "Underfollowed quality",
            "High quality, solid valuation, low current attention. Useful for slower compounders that the tape is not chasing yet.",
            underfollowed_quality,
        ),
        (
            "Crowded fragility",
            "High attention and momentum but weak safety or stretched valuation. Useful as a warning list or short watchlist.",
            crowded_fragility,
        ),
        (
            "Event dislocation",
            "Large event signal without much trend confirmation. Useful for catalyst follow-through checks and false-break screens.",
            event_dislocation,
        ),
        (
            "Recovery candidates",
            "Weak or muted near-term tape with better medium-term quality and valuation support. Useful for rerating or turn-around ideas.",
            recovery_candidates,
        ),
    ]

    for title, description, rows in categories:
        log_to_file(log_file, title)
        log_to_file(log_file, description)
        if not rows:
            log_to_file(log_file, "  none")
            log_to_file(log_file, "")
            continue
        for prediction in rows:
            row = prediction["row"]
            log_to_file(
                log_file,
                (
                    f"  {_row_label(prediction)} | industry={row.get('industry') or 'N/A'} | "
                    f"mcap={_format_market_cap(_coerce_numeric(row.get('market_cap_basic')))} | "
                    f"days={_format_score(prediction['horizons'].get('days', {}).get('score'))} | "
                    f"months={_format_score(prediction['horizons'].get('months', {}).get('score'))} | "
                    f"years={_format_score(prediction['horizons'].get('years', {}).get('score'))} | "
                    f"attn={_format_score(prediction['components'].get('attention'))} | "
                    f"qual={_format_score(prediction['components'].get('quality'))} | "
                    f"value={_format_score(prediction['components'].get('valuation'))} | "
                    f"safe={_format_score(prediction['components'].get('safety'))} | "
                    f"scale={_format_score(prediction['components'].get('scale'))}"
                ),
            )
        log_to_file(log_file, "")


def _build_tracking_rows(
    prediction_rows: list[dict[str, Any]], horizon_name: str, perf_field: str
) -> list[tuple[dict[str, Any], float]]:
    rows: list[tuple[dict[str, Any], float]] = []
    for prediction in prediction_rows:
        score = prediction["horizons"].get(horizon_name, {}).get("score")
        performance_value = _coerce_numeric(prediction["row"].get(perf_field))
        if score is None or performance_value is None:
            continue
        rows.append((prediction, float(score)))
    return rows


def _log_tracking_period_section(
    log_file: Path,
    prediction_rows: list[dict[str, Any]],
    horizon_name: str,
    perf_field: str,
) -> None:
    tracking_rows = _build_tracking_rows(prediction_rows, horizon_name, perf_field)
    horizon_title = HORIZON_TITLES.get(horizon_name, horizon_name.title())
    period_label = PERFORMANCE_TRACKING_LABELS.get(perf_field, perf_field)

    log_to_file(
        log_file, f"{horizon_title} tracked against {period_label} ({perf_field})"
    )
    log_to_file(log_file, "-" * 160)

    if not tracking_rows:
        log_to_file(
            log_file,
            "No rows had both a horizon score and realized performance for this tracking period.",
        )
        log_to_file(log_file, "")
        return

    cohort_size = min(
        len(tracking_rows),
        TOP_SECTION_ROWS,
        max(5, len(tracking_rows) // 5),
    )
    top_rows = sorted(tracking_rows, key=lambda item: item[1], reverse=True)[
        :cohort_size
    ]
    bottom_rows = sorted(tracking_rows, key=lambda item: item[1])[:cohort_size]
    bullish_rows = [
        item for item in tracking_rows if item[1] >= DIRECTIONAL_MOVE_SCORE_THRESHOLD
    ]
    bearish_rows = [
        item for item in tracking_rows if item[1] <= -DIRECTIONAL_MOVE_SCORE_THRESHOLD
    ]

    top_performances = [
        _coerce_numeric(prediction["row"].get(perf_field))
        for prediction, _ in top_rows
        if _coerce_numeric(prediction["row"].get(perf_field)) is not None
    ]
    bottom_performances = [
        _coerce_numeric(prediction["row"].get(perf_field))
        for prediction, _ in bottom_rows
        if _coerce_numeric(prediction["row"].get(perf_field)) is not None
    ]
    bullish_performances = [
        _coerce_numeric(prediction["row"].get(perf_field))
        for prediction, _ in bullish_rows
        if _coerce_numeric(prediction["row"].get(perf_field)) is not None
    ]
    bearish_performances = [
        _coerce_numeric(prediction["row"].get(perf_field))
        for prediction, _ in bearish_rows
        if _coerce_numeric(prediction["row"].get(perf_field)) is not None
    ]

    top_average = _average(top_performances)
    bottom_average = _average(bottom_performances)
    bullish_average = _average(bullish_performances)
    bearish_average = _average(bearish_performances)
    long_short_spread = (
        top_average - bottom_average
        if top_average is not None and bottom_average is not None
        else None
    )
    short_book_return = -bottom_average if bottom_average is not None else None

    log_to_file(
        log_file,
        (
            f"Coverage={len(tracking_rows)} | cohort_size={cohort_size} | "
            f"top_avg={_format_percent(top_average)} | top_median={_format_percent(median(top_performances) if top_performances else None)} | "
            f"bottom_avg={_format_percent(bottom_average)} | short_book_avg={_format_percent(short_book_return)} | "
            f"long_short_spread={_format_percent(long_short_spread)}"
        ),
    )
    log_to_file(
        log_file,
        (
            f"Bullish threshold cohort={len(bullish_rows)} | avg={_format_percent(bullish_average)} | hit_rate={_format_percent(_directional_hit_rate(bullish_performances, expect_positive=True))}"
        ),
    )
    log_to_file(
        log_file,
        (
            f"Bearish threshold cohort={len(bearish_rows)} | raw_avg={_format_percent(bearish_average)} | short_hit_rate={_format_percent(_directional_hit_rate(bearish_performances, expect_positive=False))}"
        ),
    )
    log_to_file(log_file, "")

    log_to_file(log_file, "Top predicted longs")
    log_to_file(
        log_file,
        (
            f"{'Ticker':<12} {'Company':<28} {'Industry':<26} {'Score':>8} {'Dir':<12} {'Perf.W':>10} {'Perf.1M':>10} {'Perf.YTD':>10} {'Perf.Y':>10} {'Perf.5Y':>10}"
        ),
    )
    log_to_file(log_file, "-" * 160)
    for prediction, score in top_rows:
        row = prediction["row"]
        log_to_file(
            log_file,
            (
                f"{_get_symbol_name(row):<12} {_get_company_name(row)[:28]:<28} {str(row.get('industry') or '')[:26]:<26} "
                f"{_format_score(score):>8} {str(prediction['horizons'][horizon_name]['direction']):<12} "
                f"{_format_percent(_coerce_numeric(row.get('Perf.W'))):>10} {_format_percent(_coerce_numeric(row.get('Perf.1M'))):>10} "
                f"{_format_percent(_coerce_numeric(row.get('Perf.YTD'))):>10} {_format_percent(_coerce_numeric(row.get('Perf.Y'))):>10} "
                f"{_format_percent(_coerce_numeric(row.get('Perf.5Y'))):>10}"
            ),
        )

    log_to_file(log_file, "")
    log_to_file(log_file, "Top predicted shorts")
    log_to_file(
        log_file,
        (
            f"{'Ticker':<12} {'Company':<28} {'Industry':<26} {'Score':>8} {'Dir':<12} {'Perf.W':>10} {'Perf.1M':>10} {'Perf.YTD':>10} {'Perf.Y':>10} {'Perf.5Y':>10}"
        ),
    )
    log_to_file(log_file, "-" * 160)
    for prediction, score in bottom_rows:
        row = prediction["row"]
        log_to_file(
            log_file,
            (
                f"{_get_symbol_name(row):<12} {_get_company_name(row)[:28]:<28} {str(row.get('industry') or '')[:26]:<26} "
                f"{_format_score(score):>8} {str(prediction['horizons'][horizon_name]['direction']):<12} "
                f"{_format_percent(_coerce_numeric(row.get('Perf.W'))):>10} {_format_percent(_coerce_numeric(row.get('Perf.1M'))):>10} "
                f"{_format_percent(_coerce_numeric(row.get('Perf.YTD'))):>10} {_format_percent(_coerce_numeric(row.get('Perf.Y'))):>10} "
                f"{_format_percent(_coerce_numeric(row.get('Perf.5Y'))):>10}"
            ),
        )
    log_to_file(log_file, "")


def _log_performance_tracking_analysis(
    log_file: Path,
    prediction_rows: list[dict[str, Any]],
    scoring_profile: ScoringProfile,
    scan_data_count: int,
    industries: list[str] | None,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
) -> None:
    _reset_log_file(log_file)
    log_to_file(
        log_file,
        _build_report_title("TradingView move prediction performance tracking"),
    )
    log_to_file(log_file, "=" * 160)
    log_to_file(
        log_file,
        (
            f"Rows returned: {scan_data_count} | industries={industries or 'all'} | "
            f"min_market_cap_usd={min_market_cap_usd} | max_market_cap_usd={max_market_cap_usd} | "
            f"scoring_profile={scoring_profile.name}"
        ),
    )
    log_to_file(
        log_file,
        "This is a retrospective alignment report, not a true point-in-time backtest. It checks whether the current model ranking lines up with TradingView trailing return windows already attached to the rows.",
    )
    log_to_file(
        log_file,
        "Use it to inspect whether a horizon design is pointing toward the kinds of realized periods you care about. Do not treat it as a historical simulation of what the exact portfolio would have been on the period start date.",
    )
    log_to_file(log_file, "")

    if scoring_profile.intro_metric_notes:
        log_to_file(log_file, "Added metrics and tracking notes")
        log_to_file(log_file, "-" * 160)
        for note in scoring_profile.intro_metric_notes:
            log_to_file(log_file, f"- {note}")
        log_to_file(log_file, "")

    tracking_periods = _resolve_performance_tracking_periods(scoring_profile)
    log_to_file(log_file, "Tracking map")
    log_to_file(log_file, "-" * 160)
    for horizon_name, period_fields in tracking_periods.items():
        period_summary = ", ".join(
            f"{PERFORMANCE_TRACKING_LABELS.get(period_field, period_field)} ({period_field})"
            for period_field in period_fields
        )
        log_to_file(
            log_file,
            f"{HORIZON_TITLES.get(horizon_name, horizon_name.title())}: {period_summary}",
        )
    log_to_file(log_file, "")

    for horizon_name, period_fields in tracking_periods.items():
        for period_field in period_fields:
            _log_tracking_period_section(
                log_file,
                prediction_rows,
                horizon_name,
                period_field,
            )


def analyze_move_prediction_scan(
    scan_data: list[dict[str, Any]],
    industries: list[str] | str | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    scoring_profile: str | ScoringProfile | None = None,
    include_blind_spot_sections: bool = True,
    output_dir: str | Path | None = None,
) -> Path:
    industries = _normalize_industries(industries)
    resolved_profile = resolve_move_prediction_scoring_profile(scoring_profile)
    horizon_names = list(_resolve_horizon_weights(resolved_profile).keys())
    resolved_output_dir = Path(output_dir) if output_dir is not None else None
    log_file = _build_log_file_name(
        industries,
        min_market_cap_usd,
        max_market_cap_usd,
        scoring_profile_name=resolved_profile.name,
        output_dir=resolved_output_dir,
    )
    csv_file = _build_csv_file_name(
        industries,
        min_market_cap_usd,
        max_market_cap_usd,
        scoring_profile_name=resolved_profile.name,
        output_dir=resolved_output_dir,
    )
    raw_csv_file = _build_raw_csv_file_name(
        industries,
        min_market_cap_usd,
        max_market_cap_usd,
        scoring_profile_name=resolved_profile.name,
        output_dir=resolved_output_dir,
    )
    _reset_log_file(log_file)

    log_to_file(log_file, _build_report_title("TradingView move prediction scan"))
    log_to_file(log_file, "=" * 160)
    log_to_file(
        log_file,
        (
            f"Rows returned: {len(scan_data)} | industries={industries or 'all'} | "
            f"min_market_cap_usd={min_market_cap_usd} | max_market_cap_usd={max_market_cap_usd} | "
            f"scoring_profile={resolved_profile.name}"
        ),
    )
    log_to_file(log_file, "Sorted by query on relative_volume_10d_calc descending.")
    log_to_file(log_file, "")

    if not scan_data:
        log_to_file(log_file, "No rows returned for this scan.")
        return log_file

    log_rows_to_csv(
        raw_csv_file,
        _build_raw_csv_headers(scan_data),
        _build_raw_csv_rows(scan_data),
    )

    _enrich_with_peer_metrics(scan_data)
    derived_metrics = [_build_derived_metrics(row) for row in scan_data]
    profiles = _build_metric_profiles(scan_data, derived_metrics)
    prediction_rows = _build_prediction_rows(
        scan_data,
        profiles,
        derived_metrics,
        resolved_profile,
    )

    log_rows_to_csv(
        csv_file,
        _build_csv_headers(horizon_names),
        _build_csv_rows(prediction_rows, horizon_names),
    )

    _log_methodology(log_file, resolved_profile)
    _log_scoring_profile_details(log_file, resolved_profile)
    for horizon_name in horizon_names:
        _log_horizon_section(log_file, prediction_rows, horizon_name)
    _log_consensus_section(log_file, prediction_rows)
    if include_blind_spot_sections:
        _log_blind_spot_sections(log_file, prediction_rows)

    tracking_log_file = _build_tracking_log_file_name(
        industries,
        min_market_cap_usd,
        max_market_cap_usd,
        scoring_profile_name=resolved_profile.name,
        output_dir=resolved_output_dir,
    )
    _log_performance_tracking_analysis(
        log_file=tracking_log_file,
        prediction_rows=prediction_rows,
        scoring_profile=resolved_profile,
        scan_data_count=len(scan_data),
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
    )
    return log_file


def run_move_prediction_scan(
    scan_data: list[dict[str, Any]],
    industries: list[str] | str | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    scoring_profile: str | ScoringProfile | None = None,
    include_blind_spot_sections: bool = True,
    output_dir: str | Path | None = None,
) -> Path:
    return analyze_move_prediction_scan(
        scan_data=scan_data,
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        scoring_profile=scoring_profile,
        include_blind_spot_sections=include_blind_spot_sections,
        output_dir=output_dir,
    )


def run_move_prediction_profile_suite(
    scan_data: list[dict[str, Any]],
    profile_names: list[str] | None = None,
    industries: list[str] | str | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    include_blind_spot_sections: bool = False,
    output_dir: str | Path | None = None,
) -> dict[str, Path]:
    if profile_names is None:
        profile_names = [
            "balanced",
            "backtest_period_ladder",
            "breakout_long",
            "quality_value_compounder",
            "value_recovery",
            "fragility_short",
            "asymmetric_value",
            "early_momentum_inflection",
            "forward_edge_active",
        ]

    industries = _normalize_industries(industries)

    generated_logs: dict[str, Path] = {}
    for profile_name in profile_names:
        generated_logs[profile_name] = analyze_move_prediction_scan(
            scan_data=scan_data,
            industries=industries,
            min_market_cap_usd=min_market_cap_usd,
            max_market_cap_usd=max_market_cap_usd,
            scoring_profile=profile_name,
            include_blind_spot_sections=include_blind_spot_sections,
            output_dir=output_dir,
        )
    return generated_logs


def run_move_prediction_profile_suite_by_industry(
    api_client: ApiTradingViewClient,
    industries: list[str],
    profile_names: list[str] | None = None,
    markets: list[str] | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    include_blind_spot_sections: bool = False,
    timeout: int = 30,
) -> dict[str, dict[str, Path]]:
    normalized_industries = _normalize_industries(industries) or []
    generated_logs: dict[str, dict[str, Path]] = {}

    for industry in normalized_industries:
        industry_output_dir = LOG_DIR / _sanitize_industry_folder_name(industry)
        scan_data = api_client.scan_global_market_move_prediction(
            industries=[industry],
            markets=markets,
            min_market_cap_usd=min_market_cap_usd,
            max_market_cap_usd=max_market_cap_usd,
            timeout=timeout,
        ).get("data", [])

        generated_logs[industry] = run_move_prediction_profile_suite(
            scan_data=scan_data,
            profile_names=profile_names,
            industries=[industry],
            min_market_cap_usd=min_market_cap_usd,
            max_market_cap_usd=max_market_cap_usd,
            include_blind_spot_sections=include_blind_spot_sections,
            output_dir=industry_output_dir,
        )

    return generated_logs
