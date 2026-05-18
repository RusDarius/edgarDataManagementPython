from __future__ import annotations

import csv
import collections
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from statistics import median
import sys
from typing import Any, Mapping

from data_analysis_scripts._shared_analysis_utils import (
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
RAW_MARKET_DATA_DIR = Path(
    r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\trading_view_all_fields_data"
)
BACKSCAN_OUTPUT_DIR = LOG_DIR / "raw_csv_backscan"
BACKSCAN_RAW_CSV_GLOB = "tradingview_global_all_tdfields_*.csv"

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
    "type",
    "typespecs",
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
    "average_volume_30d_calc",
    "Stoch.RSI.D",
    "change_from_open",
    "earnings_per_share_diluted_yoy_growth_ttm",
    "ebitda",
    "net_income",
    "Pivot.M.Classic.Middle",
    "gross_profit_margin_fy",
    "price_target_average",
    "price_target_median",
    "price_target_high",
    "price_target_low",
    "price_target_1y",
    "book_value_per_share_fq",
    "sustainable_growth_rate_ttm",
    "piotroski_f_score_ttm",
    "dividend_yield_recent",
    "dividends_per_share_fq",
    "dps_common_stock_prim_issue_yoy_growth_fy",
    "enterprise_value_to_free_cash_flow_ttm",
    "enterprise_value_to_gross_profit_ttm",
    "total_debt_to_ebitda_fq",
    "Pivot.M.Camarilla.S1",
    "Pivot.M.Camarilla.S2",
    "Pivot.M.Camarilla.R1",
    "Pivot.M.Camarilla.R2",
    "price_earnings_forward_fy",
    "graham_numbers_ttm",
    "graham_numbers_fy",
    "book_tangible_per_share_current",
    "book_tangible_per_share_fq",
    "cash_per_share_current",
    "cash_per_share_fq",
    "free_cash_flow_ttm",
    "cash_f_operating_activities_ttm",
    "operating_cash_flow_per_share_ttm",
    "free_cash_flow_per_share_ttm",
    "ebit_ttm",
    "gross_profit_ttm",
    "enterprise_value_current",
    "total_revenue_cagr_5y",
    "free_cash_flow_cagr_5y",
    "net_income_cagr_5y",
    "earnings_per_share_basic_cagr_5y",
    "earnings_per_share_diluted_5y_growth_fy",
    "gross_margin_ttm",
    "operating_margin_ttm",
    "net_margin_ttm",
    "ebitda_margin_ttm",
    "asset_turnover_current",
    "return_on_capital_employed_fy",
    "return_on_common_equity_ttm",
    "return_on_tang_equity_fy",
    "sloan_ratio_ttm",
    "interst_cover_ttm",
    "ebitda_interst_cover_ttm",
    "cash_dividend_coverage_ratio_ttm",
    "cash_n_short_term_invest_to_total_debt_fq",
    "cash_n_short_term_invest_to_total_debt_fy",
    "cash_n_short_term_invest_to_total_current_liabilities_fq",
    "cash_n_short_term_invest_to_total_current_liabilities_fy",
    "total_debt_to_ebitda_fy",
    "net_debt_to_ebitda_fq",
    "net_debt_to_ebitda_fy",
    "dividend_payout_ratio_ttm",
    "ncavps_ratio_current",
    "ncavps_ratio_fq",
    "zmijewski_score_ttm",
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
    "volume_trend",
    "stoch_rsi_crossover",
    "intraday_momentum",
    "pivot_distance",
    "revenue_per_employee",
    "price_target_upside_average",
    "price_target_upside_median",
    "price_target_downside_floor",
    "price_target_dispersion",
    "book_value_discount",
    "peer_revenue_value_gap",
    "beta_adjusted_atrp",
    "close_vs_camarilla_s1",
    "close_vs_camarilla_s2",
    "close_vs_camarilla_r1",
    "close_vs_camarilla_r2",
    "graham_value_gap",
    "tangible_book_value_gap",
    "cash_per_share_value_gap",
    "free_cash_flow_yield",
    "operating_cash_flow_yield",
    "free_cash_flow_per_share_yield",
    "operating_cash_flow_per_share_yield",
    "ebit_yield_ev",
    "gross_profit_yield_ev",
    "shareholder_yield",
    "net_cash_to_market_cap",
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
        "volume_trend",
        "stoch_rsi_crossover",
        "intraday_momentum",
        "pivot_distance",
        "revenue_per_employee",
        "earnings_per_share_diluted_yoy_growth_ttm",
        "gross_profit_margin_fy",
        "price_target_upside_average",
        "price_target_upside_median",
        "price_target_downside_floor",
        "price_target_dispersion",
        "book_value_discount",
        "peer_revenue_value_gap",
        "sustainable_growth_rate_ttm",
        "piotroski_f_score_ttm",
        "dividend_yield_recent",
        "dps_common_stock_prim_issue_yoy_growth_fy",
        "enterprise_value_to_free_cash_flow_ttm",
        "enterprise_value_to_gross_profit_ttm",
        "total_debt_to_ebitda_fq",
        "beta_adjusted_atrp",
        "close_vs_camarilla_s1",
        "close_vs_camarilla_s2",
        "close_vs_camarilla_r1",
        "close_vs_camarilla_r2",
        "price_earnings_forward_fy",
        "graham_numbers_ttm",
        "graham_numbers_fy",
        "book_tangible_per_share_current",
        "book_tangible_per_share_fq",
        "cash_per_share_current",
        "cash_per_share_fq",
        "free_cash_flow_ttm",
        "cash_f_operating_activities_ttm",
        "operating_cash_flow_per_share_ttm",
        "free_cash_flow_per_share_ttm",
        "ebit_ttm",
        "gross_profit_ttm",
        "enterprise_value_current",
        "total_revenue_cagr_5y",
        "free_cash_flow_cagr_5y",
        "net_income_cagr_5y",
        "earnings_per_share_basic_cagr_5y",
        "earnings_per_share_diluted_5y_growth_fy",
        "gross_margin_ttm",
        "operating_margin_ttm",
        "net_margin_ttm",
        "ebitda_margin_ttm",
        "asset_turnover_current",
        "return_on_capital_employed_fy",
        "return_on_common_equity_ttm",
        "return_on_tang_equity_fy",
        "sloan_ratio_ttm",
        "interst_cover_ttm",
        "ebitda_interst_cover_ttm",
        "cash_dividend_coverage_ratio_ttm",
        "cash_n_short_term_invest_to_total_debt_fq",
        "cash_n_short_term_invest_to_total_debt_fy",
        "cash_n_short_term_invest_to_total_current_liabilities_fq",
        "cash_n_short_term_invest_to_total_current_liabilities_fy",
        "total_debt_to_ebitda_fy",
        "net_debt_to_ebitda_fq",
        "net_debt_to_ebitda_fy",
        "dividend_payout_ratio_ttm",
        "ncavps_ratio_current",
        "ncavps_ratio_fq",
        "zmijewski_score_ttm",
        "graham_value_gap",
        "tangible_book_value_gap",
        "cash_per_share_value_gap",
        "free_cash_flow_yield",
        "operating_cash_flow_yield",
        "free_cash_flow_per_share_yield",
        "operating_cash_flow_per_share_yield",
        "ebit_yield_ev",
        "gross_profit_yield_ev",
        "shareholder_yield",
        "net_cash_to_market_cap",
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
    # ──────────────────────────────────────────────────────────────────────────
    # IDEA 1 — TREND FOLLOWING: where the market is moving
    # ──────────────────────────────────────────────────────────────────────────
    "breakout_long": ScoringProfile(
        name="breakout_long",
        description=(
            "Trend-following profile for confirmed upside continuation. Rewards "
            "strong participation, confirmed momentum across multiple timeframes, "
            "and aligned trend structure. Fundamentals carry minimal weight — this "
            "is a pure tape-reading lens. Noise reduction: requires volume "
            "confirmation (volume_trend activated), intraday momentum, and "
            "multi-timeframe MA alignment to distinguish genuine breakouts from "
            "low-conviction noise spikes. ADX directional spread confirms the "
            "move has directional strength, not just volatility."
        ),
        horizon_weights={
            "days": {
                "attention": 0.30,
                "event": 0.18,
                "momentum": 0.30,
                "trend": 0.16,
                "quality": 0.00,
                "valuation": 0.00,
                "safety": 0.06,
            },
            "weeks": {
                "attention": 0.20,
                "event": 0.10,
                "momentum": 0.28,
                "trend": 0.28,
                "quality": 0.02,
                "valuation": 0.00,
                "safety": 0.12,
            },
            "months": {
                "attention": 0.10,
                "event": 0.06,
                "momentum": 0.22,
                "trend": 0.30,
                "quality": 0.10,
                "valuation": 0.04,
                "safety": 0.18,
            },
            "years": {
                "attention": 0.03,
                "event": 0.03,
                "momentum": 0.12,
                "trend": 0.22,
                "quality": 0.18,
                "valuation": 0.12,
                "safety": 0.30,
            },
        },
        component_signal_weights={
            "attention": {
                "relative_volume_10d_calc": 1.50,
                "float_turnover": 1.35,
                "dollar_turnover_intensity": 1.25,
                "Value.Traded": 1.20,
                "AvgValue.Traded_10d": 1.10,
                "volume_trend": 1.40,
                "intraday_momentum": 1.25,
                "bb_squeeze": 0.60,
                "range_compression": 0.50,
                "volatility_contraction": 0.50,
            },
            "event": {
                "premarket_change": 1.20,
                "postmarket_change": 1.20,
                "gap": 1.20,
                "gap_severity": 1.35,
                "event_intensity": 1.30,
                "eps_surprise_percent_fq": 1.15,
            },
            "momentum": {
                "change": 1.20,
                "Perf.5D": 1.40,
                "Perf.W": 1.30,
                "Perf.1M": 1.15,
                "Perf.3M": 0.85,
                "Perf.6M": 0.40,
                "Perf.YTD": 0.45,
                "Perf.Y": 0.20,
                "ROC": 1.15,
                "Mom": 1.15,
                "macd_spread": 1.20,
                "Recommend.All": 0.90,
                "Recommend.MA": 1.15,
                "Recommend.Other": 0.85,
                "rsi_centered": 0.85,
                "rsi7_centered": 1.15,
                "aroon_spread": 1.45,
                "adx_directional_spread": 1.50,
                "stoch_rsi_centered": 0.90,
                "CCI20": 0.85,
                "stoch_rsi_crossover": 1.10,
            },
            "trend": {
                "trend_alignment": 1.35,
                "close_vs_sma10": 1.30,
                "close_vs_sma20": 1.15,
                "close_vs_sma30": 1.00,
                "close_vs_sma50": 1.20,
                "close_vs_sma200": 0.55,
                "close_vs_ema10": 1.30,
                "close_vs_ema20": 1.15,
                "close_vs_ema30": 1.00,
                "close_vs_ema50": 1.20,
                "close_vs_ema200": 0.60,
                "close_vs_vwap": 1.25,
                "close_vs_vwma": 1.25,
                "bb_position": 1.20,
                "short_trend_emergence": 1.20,
                "pivot_distance": 0.90,
            },
        },
        component_directional_bias={
            "attention": DirectionalBias(
                positive_multiplier=1.35, negative_multiplier=0.70
            ),
            "event": DirectionalBias(
                positive_multiplier=1.25, negative_multiplier=0.75
            ),
            "momentum": DirectionalBias(
                positive_multiplier=1.40, negative_multiplier=0.65
            ),
            "trend": DirectionalBias(
                positive_multiplier=1.30, negative_multiplier=0.75
            ),
            "quality": DirectionalBias(
                positive_multiplier=1.00, negative_multiplier=0.80
            ),
            "valuation": DirectionalBias(
                positive_multiplier=0.90, negative_multiplier=0.75
            ),
            "safety": DirectionalBias(
                positive_multiplier=1.00, negative_multiplier=0.80
            ),
        },
        missing_component_scores_by_horizon={
            "days": {
                "attention": -0.85,
                "event": -0.55,
                "momentum": -0.80,
                "trend": -0.50,
            },
            "weeks": {
                "attention": -0.60,
                "momentum": -0.65,
                "trend": -0.45,
            },
            "months": {
                "momentum": -0.45,
                "trend": -0.40,
            },
        },
        intro_metric_notes=[
            "Pure trend-following profile for confirmed upside continuation.",
            "Volume confirmation: volume_trend=1.40 requires sustained accumulation, not one-day spikes. intraday_momentum=1.25 confirms real-time move conviction.",
            "ADX directional spread=1.50 confirms directional strength — distinguishes true breakouts from volatility noise.",
            "Aroon spread=1.45 detects new uptrend formation at the indicator level.",
            "Short-term MA override: SMA10/EMA10=1.30, SMA50/EMA50=1.20. Long-term trend (200-day) damped to 0.55-0.60 so the profile stays tactical.",
            "Trailing momentum aggressively decayed: Perf.Y=0.20, Perf.YTD=0.45, Perf.6M=0.40. Names already mid-run are deprioritized.",
            "Directional bias amplifies upside signals (momentum +1.40x) while heavily suppressing downside readings (momentum -0.65x).",
        ],
        confidence_multiplier=1.07,
    ),
    "early_momentum_inflection": ScoringProfile(
        name="early_momentum_inflection",
        description=(
            "Early trend detection profile that catches names at the beginning of "
            "a directional move before crowd confirmation. Core differentiator: "
            "the coiled-energy suite (bb_squeeze, range_compression, volatility_"
            "contraction) combined with early directional indicators (aroon_spread, "
            "adx_directional_spread, stoch_rsi_crossover). This profile specifically "
            "looks for names exiting consolidation phases — price compression "
            "followed by the first signs of directional expansion. Established "
            "momentum is aggressively damped so the leaderboard surfaces "
            "genuinely nascent moves, not names mid-run."
        ),
        horizon_weights={
            "days": {
                "attention": 0.26,
                "event": 0.10,
                "momentum": 0.30,
                "trend": 0.26,
                "quality": 0.02,
                "valuation": 0.01,
                "safety": 0.05,
            },
            "weeks": {
                "attention": 0.20,
                "event": 0.06,
                "momentum": 0.28,
                "trend": 0.32,
                "quality": 0.04,
                "valuation": 0.02,
                "safety": 0.08,
            },
            "months": {
                "attention": 0.12,
                "event": 0.04,
                "momentum": 0.22,
                "trend": 0.28,
                "quality": 0.12,
                "valuation": 0.06,
                "safety": 0.16,
            },
            "years": {
                "attention": 0.02,
                "event": 0.02,
                "momentum": 0.08,
                "trend": 0.18,
                "quality": 0.24,
                "valuation": 0.18,
                "safety": 0.28,
            },
        },
        component_signal_weights={
            "attention": {
                "relative_volume_10d_calc": 1.25,
                "float_turnover": 1.20,
                "dollar_turnover_intensity": 1.10,
                "Value.Traded": 1.00,
                "AvgValue.Traded_10d": 0.90,
                "bb_squeeze": 1.55,
                "range_compression": 1.50,
                "volatility_contraction": 1.45,
                "volume_trend": 1.40,
                "intraday_momentum": 1.00,
            },
            "event": {
                "premarket_change": 1.10,
                "postmarket_change": 1.10,
                "gap": 1.10,
                "gap_severity": 1.20,
                "event_intensity": 1.15,
                "eps_surprise_percent_fq": 0.90,
            },
            "momentum": {
                "change": 0.95,
                "Perf.5D": 1.30,
                "Perf.W": 1.10,
                "Perf.1M": 0.65,
                "Perf.3M": 0.40,
                "Perf.6M": 0.20,
                "Perf.YTD": 0.15,
                "Perf.Y": 0.10,
                "ROC": 1.30,
                "Mom": 1.25,
                "macd_spread": 1.35,
                "Recommend.All": 0.75,
                "Recommend.MA": 0.90,
                "Recommend.Other": 0.80,
                "rsi_centered": 0.65,
                "rsi7_centered": 1.15,
                "aroon_spread": 1.65,
                "adx_directional_spread": 1.55,
                "stoch_rsi_centered": 1.15,
                "CCI20": 0.95,
                "stoch_rsi_crossover": 1.40,
            },
            "trend": {
                "close_vs_sma10": 1.40,
                "close_vs_sma20": 1.25,
                "close_vs_sma30": 1.15,
                "close_vs_sma50": 0.90,
                "close_vs_sma200": 0.50,
                "close_vs_ema10": 1.40,
                "close_vs_ema20": 1.25,
                "close_vs_ema30": 1.15,
                "close_vs_ema50": 0.90,
                "close_vs_ema200": 0.50,
                "close_vs_vwap": 1.20,
                "close_vs_vwma": 1.15,
                "trend_alignment": 0.75,
                "bb_position": 1.30,
                "short_trend_emergence": 1.55,
                "pivot_distance": 0.80,
            },
        },
        component_directional_bias={
            "attention": DirectionalBias(
                positive_multiplier=1.30, negative_multiplier=0.72
            ),
            "event": DirectionalBias(
                positive_multiplier=1.12, negative_multiplier=0.88
            ),
            "momentum": DirectionalBias(
                positive_multiplier=1.40, negative_multiplier=0.65
            ),
            "trend": DirectionalBias(
                positive_multiplier=1.30, negative_multiplier=0.75
            ),
            "quality": DirectionalBias(
                positive_multiplier=1.00, negative_multiplier=0.82
            ),
            "safety": DirectionalBias(
                positive_multiplier=1.00, negative_multiplier=0.82
            ),
        },
        missing_component_scores_by_horizon={
            "days": {
                "attention": -0.75,
                "momentum": -0.80,
                "trend": -0.55,
            },
            "weeks": {
                "attention": -0.50,
                "momentum": -0.60,
                "trend": -0.45,
            },
            "months": {
                "momentum": -0.40,
                "trend": -0.30,
            },
        },
        intro_metric_notes=[
            "Early inflection profile for catching moves BEFORE confirmation.",
            "Coiled-energy detection: bb_squeeze=1.55 (inverted band width — tighter bands = more energy stored), range_compression=1.50 (inverted 3-month range — tighter range = pending expansion), volatility_contraction=1.45 (daily vol below monthly = compression before release).",
            "Early directional indicators: aroon_spread=1.65 (highest in system — new uptrend at indicator level), adx_directional_spread=1.55 (bullish directional divergence emerging), stoch_rsi_crossover=1.40 (first bullish crossover signal).",
            "Short-term MA override: SMA10/EMA10=1.40 catch first crossovers. long-term alignment (SMA200/EMA200) reduced to 0.50. trend_alignment reduced to 0.75 — established alignment is irrelevant for nascent moves.",
            "Established momentum crushed: Perf.Y=0.10, Perf.YTD=0.15, Perf.6M=0.20, Perf.3M=0.40. Names already mid-run are invisible.",
            "Momentum bias is the most aggressive in the system: upside +1.40x, downside 0.65x. This makes early positive readings amplified while negative legacy does not dominate.",
        ],
        confidence_multiplier=1.05,
    ),
    # ──────────────────────────────────────────────────────────────────────────
    # IDEA 2 — REAL QUALITY: where quality lies ahead of time
    # ──────────────────────────────────────────────────────────────────────────
    "quality_value_compounder": ScoringProfile(
        name="quality_value_compounder",
        description=(
            "Durable quality identification profile. Surfaces companies with "
            "persistent profitability, capital efficiency, and margin stability "
            "that justify premium positioning. This is NOT a value profile — it "
            "rewards operational excellence at any reasonable valuation. Noise "
            "reduction: all extended quality signals activated (FCF margin, "
            "buyback yield, revenue per employee, gross profit margin FY, EPS "
            "diluted growth) to build a multi-dimensional quality picture that "
            "cannot be gamed by a single strong metric. Short-term momentum is "
            "nearly zeroed so quality ranking is not polluted by recent price "
            "moves. Missing quality/safety evidence is heavily penalized."
        ),
        horizon_weights={
            "days": {
                "attention": 0.04,
                "event": 0.01,
                "momentum": 0.04,
                "trend": 0.16,
                "quality": 0.35,
                "valuation": 0.16,
                "safety": 0.24,
                "scale": 0.00,
            },
            "weeks": {
                "attention": 0.03,
                "event": 0.01,
                "momentum": 0.05,
                "trend": 0.14,
                "quality": 0.34,
                "valuation": 0.18,
                "safety": 0.25,
                "scale": 0.00,
            },
            "months": {
                "attention": 0.02,
                "event": 0.01,
                "momentum": 0.04,
                "trend": 0.12,
                "quality": 0.36,
                "valuation": 0.20,
                "safety": 0.25,
                "scale": 0.00,
            },
            "years": {
                "attention": 0.01,
                "event": 0.00,
                "momentum": 0.01,
                "trend": 0.06,
                "quality": 0.42,
                "valuation": 0.24,
                "safety": 0.26,
                "scale": 0.00,
            },
        },
        component_signal_weights={
            "momentum": {
                "change": 0.10,
                "Perf.5D": 0.10,
                "Perf.W": 0.15,
                "Perf.1M": 0.30,
                "Perf.3M": 0.80,
                "Perf.6M": 0.90,
                "Perf.YTD": 1.05,
                "Perf.Y": 1.25,
                "ROC": 0.30,
                "Mom": 0.35,
                "macd_spread": 0.25,
                "Recommend.All": 0.60,
                "Recommend.MA": 0.45,
                "Recommend.Other": 0.55,
                "rsi_centered": 0.15,
                "rsi7_centered": 0.05,
            },
            "trend": {
                "close_vs_sma50": 0.80,
                "close_vs_sma200": 1.40,
                "close_vs_ema50": 0.80,
                "close_vs_ema200": 1.40,
                "close_vs_vwap": 0.20,
                "close_vs_vwma": 0.30,
                "trend_alignment": 1.25,
                "pivot_distance": 0.90,
            },
            "quality": {
                "total_revenue_yoy_growth_ttm": 1.15,
                "total_revenue_qoq_growth_fq": 0.80,
                "ebitda_yoy_growth_ttm": 1.20,
                "ebitda_qoq_growth_fq": 0.85,
                "net_income_yoy_growth_ttm": 1.10,
                "net_income_qoq_growth_fq": 0.80,
                "free_cash_flow_yoy_growth_ttm": 1.30,
                "free_cash_flow_qoq_growth_fq": 0.95,
                "gross_margin": 1.15,
                "operating_margin": 1.35,
                "after_tax_margin": 1.15,
                "return_on_assets": 1.10,
                "return_on_equity": 1.15,
                "return_on_invested_capital": 1.40,
                "free_cash_flow_margin_ttm": 1.35,
                "buyback_yield": 0.90,
                "dividends_yield_current": 0.70,
                "eps_forward_growth": 1.20,
                "revenue_per_employee": 1.20,
                "earnings_per_share_diluted_yoy_growth_ttm": 1.20,
                "gross_profit_margin_fy": 1.20,
                "sustainable_growth_rate_ttm": 1.30,
                "piotroski_f_score_ttm": 1.35,
                "dividend_yield_recent": 0.70,
                "dps_common_stock_prim_issue_yoy_growth_fy": 0.75,
            },
            "valuation": {
                "price_earnings_ttm": 1.10,
                "price_earnings_growth_ttm": 1.20,
                "price_sales_current": 1.00,
                "price_book_fq": 0.85,
                "price_free_cash_flow_ttm": 1.25,
                "price_to_cash_f_operating_activities_ttm": 1.10,
                "enterprise_value_to_revenue_ttm": 1.00,
                "enterprise_value_to_ebit_ttm": 0.90,
                "enterprise_value_ebitda_ttm": 1.15,
                "earnings_yield": 1.10,
                "enterprise_value_to_free_cash_flow_ttm": 1.25,
                "enterprise_value_to_gross_profit_ttm": 1.00,
                "price_target_upside_median": 0.85,
                "price_target_downside_floor": 0.75,
            },
            "safety": {
                "current_ratio": 1.10,
                "quick_ratio": 1.10,
                "cash_ratio": 1.05,
                "short_term_cash_coverage": 1.25,
                "altman_z_score_ttm": 1.30,
                "debt_to_equity": 1.30,
                "debt_to_revenue_ttm": 1.25,
                "net_debt": 1.15,
                "beta_1_year": 0.70,
                "total_debt_to_ebitda_fq": 1.35,
                "beta_adjusted_atrp": 0.85,
                "price_target_dispersion": 0.70,
            },
        },
        component_directional_bias={
            "attention": DirectionalBias(
                positive_multiplier=0.70, negative_multiplier=1.50
            ),
            "momentum": DirectionalBias(
                positive_multiplier=1.00, negative_multiplier=0.95
            ),
            "trend": DirectionalBias(
                positive_multiplier=1.05, negative_multiplier=1.15
            ),
            "quality": DirectionalBias(
                positive_multiplier=1.28, negative_multiplier=1.38
            ),
            "valuation": DirectionalBias(
                positive_multiplier=1.10, negative_multiplier=1.25
            ),
            "safety": DirectionalBias(
                positive_multiplier=1.20, negative_multiplier=1.35
            ),
        },
        missing_component_scores_by_horizon={
            "days": {
                "attention": -0.50,
                "quality": -1.00,
                "valuation": -0.80,
                "safety": -0.75,
            },
            "weeks": {
                "attention": -0.40,
                "quality": -1.10,
                "valuation": -0.85,
                "safety": -0.85,
            },
            "months": {
                "attention": -0.30,
                "quality": -1.20,
                "valuation": -0.95,
                "safety": -0.90,
            },
            "years": {
                "attention": -0.20,
                "quality": -1.30,
                "valuation": -1.05,
                "safety": -1.00,
            },
        },
        intro_metric_notes=[
            "Durable quality identification — NOT a value profile.",
            "All extended quality signals activated: ROIC=1.40, operating_margin=1.35, FCF_margin=1.35, revenue_per_employee=1.20, gross_profit_margin_fy=1.20, EPS_diluted_growth=1.20.",
            "Fundamental durability strengthened with Piotroski F-score=1.35 and sustainable_growth_rate=1.30; these help separate reliable compounders from single-period earners.",
            "Risk-adjusted quality anchors strengthened: debt/EBITDA=1.35, short_term_cash_coverage=1.25, beta_adjusted_atrp=0.85, and target dispersion=0.70. Quality must be financially durable, not merely profitable.",
            "YoY growth is prioritized over QoQ (1.10-1.25 vs 0.80-0.95) because quality compounders show persistent annual improvement, not quarterly noise.",
            "Momentum nearly zeroed (short-term 0.05-0.15) so quality rank is independent of recent price action. Only Perf.Y=1.25 retained to confirm secular winners.",
            "Attention INVERTED: positive=0.70x, negative=1.50x. Strong crowd inversion means popular names must earn their quality rank; low-volume names face structural penalty for missing data, not for being quiet.",
            "Scale is explicitly zeroed so the profile identifies strong earners and balance sheets, not simply large companies.",
            "Missing quality penalty is the harshest in the system: -1.30 at years horizon. Names without fundamental data cannot rank.",
            "Safety carries 24-26% weight across all horizons — the floor against quality traps with hidden leverage.",
        ],
        confidence_multiplier=1.02,
    ),
    "durable_value_compounder": ScoringProfile(
        name="durable_value_compounder",
        description=(
            "Quality-led value investing profile. Surfaces operating companies "
            "where durable profitability, cash conversion, accounting quality, "
            "and balance-sheet safety are available at attractive valuation "
            "anchors. Unlike quality_value_compounder, this is explicitly a "
            "value profile: premium operators still need visible price discipline "
            "through earnings yield, FCF yield, EV/FCF, book/tangible-book support, "
            "Graham-value gap, street downside floor, and peer-revenue value gap. "
            "Unlike deep_value_momentum, it does not require tactical reversal; "
            "momentum is only a small confirmation layer. Designed for months/years "
            "positioning where safe, cash-generative performers are opportune, not "
            "just cheap. Scale is explicitly zeroed so the profile does not rank "
            "companies for size."
        ),
        horizon_weights={
            "days": {
                "attention": 0.02,
                "event": 0.00,
                "momentum": 0.02,
                "trend": 0.08,
                "quality": 0.34,
                "valuation": 0.34,
                "safety": 0.20,
                "scale": 0.00,
            },
            "weeks": {
                "attention": 0.02,
                "event": 0.00,
                "momentum": 0.03,
                "trend": 0.08,
                "quality": 0.34,
                "valuation": 0.33,
                "safety": 0.20,
                "scale": 0.00,
            },
            "months": {
                "attention": 0.01,
                "event": 0.00,
                "momentum": 0.02,
                "trend": 0.07,
                "quality": 0.36,
                "valuation": 0.36,
                "safety": 0.18,
                "scale": 0.00,
            },
            "years": {
                "attention": 0.00,
                "event": 0.00,
                "momentum": 0.01,
                "trend": 0.04,
                "quality": 0.38,
                "valuation": 0.38,
                "safety": 0.19,
                "scale": 0.00,
            },
        },
        component_signal_weights={
            "attention": {
                "relative_volume_10d_calc": 0.75,
                "float_turnover": 0.70,
                "dollar_turnover_intensity": 0.80,
                "Value.Traded": 0.65,
                "AvgValue.Traded_10d": 0.65,
                "volume_trend": 0.45,
            },
            "momentum": {
                "change": 0.05,
                "Perf.5D": 0.05,
                "Perf.W": 0.08,
                "Perf.1M": 0.12,
                "Perf.3M": 0.25,
                "Perf.6M": 0.30,
                "Perf.YTD": 0.30,
                "Perf.Y": 0.45,
                "Perf.5Y": 0.55,
                "ROC": 0.12,
                "Mom": 0.12,
                "macd_spread": 0.10,
                "Recommend.All": 0.25,
                "Recommend.MA": 0.20,
                "Recommend.Other": 0.25,
                "rsi_centered": 0.08,
                "rsi7_centered": 0.05,
            },
            "trend": {
                "close_vs_sma50": 0.55,
                "close_vs_sma200": 0.85,
                "close_vs_ema50": 0.55,
                "close_vs_ema200": 0.85,
                "close_vs_vwap": 0.30,
                "close_vs_vwma": 0.35,
                "trend_alignment": 0.70,
                "pivot_distance": 0.55,
                "close_vs_camarilla_s1": 0.55,
                "close_vs_camarilla_s2": 0.45,
                "close_vs_camarilla_r1": 0.35,
                "close_vs_camarilla_r2": 0.25,
            },
            "quality": {
                "total_revenue_yoy_growth_ttm": 1.10,
                "total_revenue_qoq_growth_fq": 0.70,
                "ebitda_yoy_growth_ttm": 1.15,
                "ebitda_qoq_growth_fq": 0.75,
                "net_income_yoy_growth_ttm": 1.15,
                "net_income_qoq_growth_fq": 0.75,
                "free_cash_flow_yoy_growth_ttm": 1.30,
                "free_cash_flow_qoq_growth_fq": 0.90,
                "gross_margin": 1.05,
                "operating_margin": 1.25,
                "after_tax_margin": 1.15,
                "return_on_assets": 1.10,
                "return_on_equity": 1.10,
                "return_on_invested_capital": 1.45,
                "free_cash_flow_margin_ttm": 1.45,
                "buyback_yield": 0.95,
                "dividends_yield_current": 0.85,
                "eps_forward_growth": 1.05,
                "revenue_per_employee": 1.05,
                "earnings_per_share_diluted_yoy_growth_ttm": 1.15,
                "gross_profit_margin_fy": 1.15,
                "sustainable_growth_rate_ttm": 1.35,
                "piotroski_f_score_ttm": 1.50,
                "dividend_yield_recent": 0.75,
                "dps_common_stock_prim_issue_yoy_growth_fy": 0.80,
                "total_revenue_cagr_5y": 1.10,
                "free_cash_flow_cagr_5y": 1.35,
                "net_income_cagr_5y": 1.25,
                "earnings_per_share_basic_cagr_5y": 1.20,
                "earnings_per_share_diluted_5y_growth_fy": 1.20,
                "gross_margin_ttm": 1.15,
                "operating_margin_ttm": 1.35,
                "net_margin_ttm": 1.20,
                "ebitda_margin_ttm": 1.15,
                "asset_turnover_current": 0.85,
                "return_on_capital_employed_fy": 1.40,
                "return_on_common_equity_ttm": 1.10,
                "return_on_tang_equity_fy": 1.10,
                "sloan_ratio_ttm": 1.15,
            },
            "valuation": {
                "price_earnings_ttm": 1.15,
                "price_earnings_forward_fy": 1.25,
                "price_earnings_growth_ttm": 1.15,
                "price_sales_current": 0.95,
                "price_book_fq": 1.30,
                "price_free_cash_flow_ttm": 1.45,
                "price_to_cash_f_operating_activities_ttm": 1.30,
                "enterprise_value_to_revenue_ttm": 1.00,
                "enterprise_value_to_ebit_ttm": 1.15,
                "enterprise_value_ebitda_ttm": 1.20,
                "earnings_yield": 1.50,
                "enterprise_value_to_free_cash_flow_ttm": 1.55,
                "enterprise_value_to_gross_profit_ttm": 1.20,
                "price_target_upside_average": 1.05,
                "price_target_upside_median": 1.15,
                "price_target_downside_floor": 1.25,
                "book_value_discount": 1.30,
                "peer_revenue_value_gap": 1.15,
                "graham_value_gap": 1.40,
                "tangible_book_value_gap": 1.35,
                "cash_per_share_value_gap": 0.80,
                "free_cash_flow_yield": 1.55,
                "operating_cash_flow_yield": 1.35,
                "free_cash_flow_per_share_yield": 1.35,
                "operating_cash_flow_per_share_yield": 1.15,
                "ebit_yield_ev": 1.25,
                "gross_profit_yield_ev": 1.15,
                "shareholder_yield": 1.15,
                "net_cash_to_market_cap": 0.90,
                "ncavps_ratio_current": 1.10,
                "ncavps_ratio_fq": 1.10,
            },
            "safety": {
                "current_ratio": 1.05,
                "quick_ratio": 1.10,
                "cash_ratio": 1.05,
                "short_term_cash_coverage": 1.20,
                "altman_z_score_ttm": 1.35,
                "debt_to_equity": 1.30,
                "debt_to_revenue_ttm": 1.20,
                "net_debt": 1.20,
                "beta_1_year": 0.70,
                "total_debt_to_ebitda_fq": 1.35,
                "total_debt_to_ebitda_fy": 1.25,
                "net_debt_to_ebitda_fq": 1.35,
                "net_debt_to_ebitda_fy": 1.25,
                "beta_adjusted_atrp": 0.90,
                "price_target_dispersion": 0.80,
                "interst_cover_ttm": 1.25,
                "ebitda_interst_cover_ttm": 1.20,
                "cash_dividend_coverage_ratio_ttm": 1.00,
                "cash_n_short_term_invest_to_total_debt_fq": 1.20,
                "cash_n_short_term_invest_to_total_debt_fy": 1.10,
                "cash_n_short_term_invest_to_total_current_liabilities_fq": 1.15,
                "cash_n_short_term_invest_to_total_current_liabilities_fy": 1.05,
                "dividend_payout_ratio_ttm": 0.80,
                "zmijewski_score_ttm": 1.25,
            },
        },
        component_directional_bias={
            "attention": DirectionalBias(
                positive_multiplier=0.55, negative_multiplier=1.20
            ),
            "momentum": DirectionalBias(
                positive_multiplier=0.80, negative_multiplier=0.55
            ),
            "trend": DirectionalBias(
                positive_multiplier=0.90, negative_multiplier=0.75
            ),
            "quality": DirectionalBias(
                positive_multiplier=1.35, negative_multiplier=1.45
            ),
            "valuation": DirectionalBias(
                positive_multiplier=1.45, negative_multiplier=1.20
            ),
            "safety": DirectionalBias(
                positive_multiplier=1.25, negative_multiplier=1.45
            ),
        },
        missing_component_scores_by_horizon={
            "days": {
                "attention": -0.35,
                "quality": -1.15,
                "valuation": -1.15,
                "safety": -0.80,
            },
            "weeks": {
                "attention": -0.30,
                "quality": -1.20,
                "valuation": -1.25,
                "safety": -0.90,
            },
            "months": {
                "attention": -0.20,
                "quality": -1.30,
                "valuation": -1.35,
                "safety": -1.00,
            },
            "years": {
                "quality": -1.40,
                "valuation": -1.45,
                "safety": -1.10,
            },
        },
        intro_metric_notes=[
            "Quality-led value investing profile for months/years positioning.",
            "Quality and valuation are the co-primary lenses: 0.68-0.76 combined horizon weight; scale is zero at every horizon.",
            "Quality requires durable cash economics: Piotroski=1.50, ROIC=1.45, ROCE=1.40, FCF margin=1.45, operating margin TTM=1.35, FCF CAGR=1.35, sustainable growth=1.35.",
            "Value is not only low multiples: EV/FCF=1.55, FCF yield=1.55, earnings_yield=1.50, Graham gap=1.40, tangible book gap=1.35, book value discount=1.30, and target downside floor=1.25.",
            "Safety blocks value traps: Altman Z=1.35, debt/EBITDA and net debt/EBITDA=1.25-1.35, interest cover=1.25, Zmijewski inverted=1.25, and negative safety gets a 1.45x penalty.",
            "Momentum is almost invisible and downside momentum is forgiven (negative=0.55x). Cheap durable names do not need a tactical reversal to qualify.",
            "Attention is a liquidity/data-quality check, not a popularity reward: positive=0.55x and missing attention is mildly penalized.",
            "Missing quality or valuation evidence is harshly penalized; the profile should not rank sparse-data names on cheapness alone.",
        ],
        confidence_multiplier=1.04,
    ),
    "sector_relative_outperformer": ScoringProfile(
        name="sector_relative_outperformer",
        description=(
            "Operational excellence profile that surfaces best-in-class operators "
            "within their peer group. Emphasizes revenue efficiency per employee, "
            "ROIC, margin durability, capital return discipline (buyback yield, "
            "dividend yield), and forward earnings growth. Differentiator from "
            "quality_value_compounder: this profile rewards RELATIVE strength "
            "via long-duration momentum (Perf.Y, Perf.6M) to identify companies "
            "that the market has already begun to recognize as superior operators. "
            "Noise reduction: volume_trend detects sustained institutional "
            "accumulation; pivot_distance confirms structural support. Short-term "
            "tape noise is aggressively damped."
        ),
        horizon_weights={
            "days": {
                "attention": 0.04,
                "event": 0.02,
                "momentum": 0.08,
                "trend": 0.20,
                "quality": 0.32,
                "valuation": 0.14,
                "safety": 0.20,
            },
            "weeks": {
                "attention": 0.03,
                "event": 0.02,
                "momentum": 0.08,
                "trend": 0.18,
                "quality": 0.32,
                "valuation": 0.17,
                "safety": 0.20,
            },
            "months": {
                "attention": 0.02,
                "event": 0.01,
                "momentum": 0.06,
                "trend": 0.14,
                "quality": 0.35,
                "valuation": 0.22,
                "safety": 0.20,
            },
            "years": {
                "attention": 0.01,
                "event": 0.01,
                "momentum": 0.03,
                "trend": 0.07,
                "quality": 0.40,
                "valuation": 0.26,
                "safety": 0.22,
            },
        },
        component_signal_weights={
            "attention": {
                "relative_volume_10d_calc": 0.85,
                "float_turnover": 0.80,
                "dollar_turnover_intensity": 0.75,
                "Value.Traded": 0.70,
                "AvgValue.Traded_10d": 0.65,
                "volume_trend": 1.50,
                "intraday_momentum": 0.40,
            },
            "momentum": {
                "change": 0.15,
                "Perf.5D": 0.15,
                "Perf.W": 0.25,
                "Perf.1M": 0.45,
                "Perf.3M": 1.05,
                "Perf.6M": 1.20,
                "Perf.YTD": 1.15,
                "Perf.Y": 1.30,
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
                "close_vs_sma200": 1.40,
                "close_vs_ema50": 0.85,
                "close_vs_ema200": 1.40,
                "close_vs_vwap": 0.30,
                "close_vs_vwma": 0.40,
                "trend_alignment": 1.30,
                "pivot_distance": 1.15,
            },
            "quality": {
                "total_revenue_yoy_growth_ttm": 1.20,
                "total_revenue_qoq_growth_fq": 0.85,
                "ebitda_yoy_growth_ttm": 1.25,
                "ebitda_qoq_growth_fq": 0.90,
                "net_income_yoy_growth_ttm": 1.15,
                "net_income_qoq_growth_fq": 0.85,
                "free_cash_flow_yoy_growth_ttm": 1.30,
                "free_cash_flow_qoq_growth_fq": 1.00,
                "gross_margin": 1.15,
                "operating_margin": 1.30,
                "after_tax_margin": 1.10,
                "return_on_assets": 1.15,
                "return_on_equity": 1.20,
                "return_on_invested_capital": 1.40,
                "free_cash_flow_margin_ttm": 1.30,
                "buyback_yield": 1.10,
                "dividends_yield_current": 0.85,
                "eps_forward_growth": 1.35,
                "revenue_per_employee": 1.30,
                "earnings_per_share_diluted_yoy_growth_ttm": 1.20,
                "gross_profit_margin_fy": 1.15,
            },
            "valuation": {
                "price_earnings_ttm": 1.05,
                "price_earnings_growth_ttm": 1.25,
                "price_sales_current": 0.90,
                "price_book_fq": 0.80,
                "price_free_cash_flow_ttm": 1.20,
                "price_to_cash_f_operating_activities_ttm": 1.05,
                "enterprise_value_to_revenue_ttm": 0.95,
                "enterprise_value_to_ebit_ttm": 0.85,
                "enterprise_value_ebitda_ttm": 1.10,
                "earnings_yield": 1.20,
            },
            "safety": {
                "current_ratio": 1.05,
                "quick_ratio": 1.05,
                "cash_ratio": 0.90,
                "short_term_cash_coverage": 1.10,
                "altman_z_score_ttm": 1.20,
                "debt_to_equity": 1.15,
                "debt_to_revenue_ttm": 1.10,
                "net_debt": 1.10,
                "beta_1_year": 0.65,
            },
        },
        component_directional_bias={
            "attention": DirectionalBias(
                positive_multiplier=0.70, negative_multiplier=1.40
            ),
            "momentum": DirectionalBias(
                positive_multiplier=1.08, negative_multiplier=0.85
            ),
            "trend": DirectionalBias(
                positive_multiplier=1.12, negative_multiplier=1.12
            ),
            "quality": DirectionalBias(
                positive_multiplier=1.30, negative_multiplier=1.35
            ),
            "valuation": DirectionalBias(
                positive_multiplier=1.18, negative_multiplier=1.12
            ),
            "safety": DirectionalBias(
                positive_multiplier=1.15, negative_multiplier=1.28
            ),
        },
        missing_component_scores_by_horizon={
            "days": {
                "attention": -0.45,
                "quality": -0.90,
                "valuation": -0.70,
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
                "safety": -0.90,
            },
            "years": {
                "quality": -1.20,
                "valuation": -1.00,
                "safety": -0.95,
            },
        },
        intro_metric_notes=[
            "Operational excellence profile — rewards relative strength within peer group.",
            "Efficiency core: revenue_per_employee=1.30 (highest in system), ROIC=1.40, operating_margin=1.30, FCF_margin=1.30.",
            "Capital return discipline: buyback_yield=1.10, dividends_yield=0.85. Companies returning cash to shareholders consistently outperform over long horizons.",
            "Forward conviction: eps_forward_growth=1.35, EPS_diluted_growth=1.20. The market prices forward earnings, not backward.",
            "Long-duration momentum confirms market recognition: Perf.Y=1.30, Perf.6M=1.20, Perf.YTD=1.15. Short-term tape crushed (change=0.15, Perf.5D=0.15).",
            "Volume accumulation: volume_trend=1.50 (highest in system) detects sustained institutional buying pattern — not one-day attention spikes.",
            "Attention strongly inverted: positive=0.70x, negative=1.40x. Quality must stand on its own merits, not popularity.",
        ],
        confidence_multiplier=1.02,
    ),
    "forward_edge_active": ScoringProfile(
        name="forward_edge_active",
        description=(
            "Forward-looking active management profile that identifies quality "
            "improvement BEFORE the market prices it in. Core differentiators: "
            "eps_forward_growth (analyst EPS revision direction), QoQ sequential "
            "improvement metrics, and EPS surprise history. This profile answers "
            "'what is getting better?' not 'what has been good?'. Noise reduction: "
            "trailing performance is nearly invisible (Perf.Y=0.05), attention is "
            "maximally inverted, and missing fundamental data faces the heaviest "
            "penalties. Designed for weeks-to-months outperformance positioning."
        ),
        horizon_weights={
            "days": {
                "attention": 0.05,
                "event": 0.08,
                "momentum": 0.14,
                "trend": 0.26,
                "quality": 0.22,
                "valuation": 0.10,
                "safety": 0.15,
            },
            "weeks": {
                "attention": 0.04,
                "event": 0.06,
                "momentum": 0.12,
                "trend": 0.22,
                "quality": 0.26,
                "valuation": 0.14,
                "safety": 0.16,
            },
            "months": {
                "attention": 0.03,
                "event": 0.03,
                "momentum": 0.08,
                "trend": 0.16,
                "quality": 0.30,
                "valuation": 0.18,
                "safety": 0.22,
            },
            "years": {
                "attention": 0.02,
                "event": 0.01,
                "momentum": 0.03,
                "trend": 0.10,
                "quality": 0.32,
                "valuation": 0.22,
                "safety": 0.30,
            },
        },
        component_signal_weights={
            "attention": {
                "relative_volume_10d_calc": 0.70,
                "float_turnover": 0.65,
                "dollar_turnover_intensity": 0.60,
                "Value.Traded": 0.50,
                "AvgValue.Traded_10d": 0.45,
                "bb_squeeze": 1.60,
                "range_compression": 1.55,
                "volatility_contraction": 1.50,
                "volume_trend": 1.50,
                "intraday_momentum": 0.80,
            },
            "event": {
                "premarket_change": 0.80,
                "postmarket_change": 0.80,
                "gap": 0.70,
                "gap_severity": 0.75,
                "event_intensity": 0.80,
                "eps_surprise_percent_fq": 1.60,
            },
            "momentum": {
                "change": 0.60,
                "Perf.5D": 1.05,
                "Perf.W": 0.90,
                "Perf.1M": 0.75,
                "Perf.3M": 0.50,
                "Perf.6M": 0.25,
                "Perf.YTD": 0.12,
                "Perf.Y": 0.05,
                "ROC": 1.20,
                "Mom": 1.15,
                "macd_spread": 1.30,
                "Recommend.All": 0.60,
                "Recommend.MA": 0.75,
                "Recommend.Other": 0.55,
                "rsi_centered": 0.45,
                "rsi7_centered": 0.70,
                "aroon_spread": 1.40,
                "adx_directional_spread": 1.30,
                "stoch_rsi_centered": 1.15,
                "CCI20": 1.00,
                "stoch_rsi_crossover": 1.25,
            },
            "trend": {
                "close_vs_sma10": 1.50,
                "close_vs_sma20": 1.40,
                "close_vs_sma30": 1.30,
                "close_vs_ema10": 1.50,
                "close_vs_ema20": 1.40,
                "close_vs_ema30": 1.30,
                "short_trend_emergence": 1.65,
                "close_vs_sma50": 0.85,
                "close_vs_sma200": 0.50,
                "close_vs_ema50": 0.85,
                "close_vs_ema200": 0.50,
                "close_vs_vwap": 1.05,
                "close_vs_vwma": 1.00,
                "trend_alignment": 0.70,
                "bb_position": 1.25,
                "pivot_distance": 1.00,
            },
            "quality": {
                "total_revenue_yoy_growth_ttm": 0.95,
                "total_revenue_qoq_growth_fq": 1.35,
                "ebitda_yoy_growth_ttm": 0.95,
                "ebitda_qoq_growth_fq": 1.40,
                "net_income_yoy_growth_ttm": 0.90,
                "net_income_qoq_growth_fq": 1.35,
                "free_cash_flow_yoy_growth_ttm": 1.00,
                "free_cash_flow_qoq_growth_fq": 1.45,
                "gross_margin": 0.85,
                "operating_margin": 1.10,
                "after_tax_margin": 0.85,
                "return_on_assets": 0.80,
                "return_on_equity": 0.90,
                "return_on_invested_capital": 1.20,
                "free_cash_flow_margin_ttm": 1.35,
                "buyback_yield": 0.65,
                "dividends_yield_current": 0.30,
                "eps_forward_growth": 1.75,
                "revenue_per_employee": 0.80,
                "earnings_per_share_diluted_yoy_growth_ttm": 1.15,
                "gross_profit_margin_fy": 0.85,
            },
            "valuation": {
                "price_earnings_ttm": 0.80,
                "price_earnings_growth_ttm": 1.30,
                "price_sales_current": 0.75,
                "price_book_fq": 0.70,
                "price_free_cash_flow_ttm": 1.15,
                "price_to_cash_f_operating_activities_ttm": 0.85,
                "enterprise_value_to_revenue_ttm": 0.75,
                "enterprise_value_to_ebit_ttm": 0.80,
                "enterprise_value_ebitda_ttm": 0.90,
                "earnings_yield": 1.25,
                "distance_from_52w_high": 1.20,
                "range_position_52w": 1.15,
            },
            "safety": {
                "current_ratio": 1.00,
                "quick_ratio": 1.00,
                "cash_ratio": 0.90,
                "short_term_cash_coverage": 1.10,
                "altman_z_score_ttm": 1.25,
                "debt_to_equity": 1.20,
                "debt_to_revenue_ttm": 1.15,
                "net_debt": 1.15,
                "beta_1_year": 0.65,
            },
        },
        component_directional_bias={
            "attention": DirectionalBias(
                positive_multiplier=0.50, negative_multiplier=1.50
            ),
            "event": DirectionalBias(
                positive_multiplier=1.20, negative_multiplier=0.82
            ),
            "momentum": DirectionalBias(
                positive_multiplier=1.15, negative_multiplier=0.72
            ),
            "trend": DirectionalBias(
                positive_multiplier=1.30, negative_multiplier=0.82
            ),
            "quality": DirectionalBias(
                positive_multiplier=1.22, negative_multiplier=1.35
            ),
            "valuation": DirectionalBias(
                positive_multiplier=1.12, negative_multiplier=0.78
            ),
            "safety": DirectionalBias(
                positive_multiplier=1.10, negative_multiplier=1.40
            ),
        },
        missing_component_scores_by_horizon={
            "days": {
                "quality": -0.65,
                "safety": -0.65,
                "trend": -0.50,
            },
            "weeks": {
                "quality": -0.75,
                "safety": -0.75,
                "trend": -0.45,
            },
            "months": {
                "quality": -0.90,
                "safety": -0.90,
                "valuation": -0.60,
            },
            "years": {
                "quality": -1.00,
                "safety": -1.00,
                "valuation": -0.80,
            },
        },
        performance_tracking_periods={
            "days": ["Perf.W"],
            "weeks": ["Perf.1M"],
            "months": ["Perf.YTD", "Perf.1M"],
            "years": ["Perf.Y"],
        },
        intro_metric_notes=[
            "Forward-looking quality profile — answers 'what is getting better?' not 'what has been good?'.",
            "THE core differentiator: eps_forward_growth=1.75 (highest weight in entire system). Names where analysts expect next-quarter EPS improvement rank highest.",
            "QoQ growth dominates YoY: QoQ=1.35-1.45 vs YoY=0.90-1.00. Sequential improvement captures acceleration that annual figures lag.",
            "EPS surprise history: eps_surprise_percent_fq=1.60. Persistent beaters have demonstrated ability to deliver above expectations.",
            "Trailing performance nearly invisible: Perf.Y=0.05, Perf.YTD=0.12, Perf.6M=0.25. This prevents recent winners from dominating — the profile finds quality BEFORE price follows.",
            "Attention inversion is the strongest in the system: positive=0.50x, negative=1.50x. The most anti-crowd profile.",
            "Safety penalty on negative readings is 1.40x — the highest safety punishment. Falling knives with improving QoQ metrics but deteriorating balance sheets are blocked.",
            "Short-term trend emergence=1.65 (highest) — earliest possible trend detection layer.",
        ],
        confidence_multiplier=1.03,
    ),
    # ──────────────────────────────────────────────────────────────────────────
    # IDEA 2b — OVERLOOKED FUNDAMENTALS: quality not rewarded by the market
    # ──────────────────────────────────────────────────────────────────────────
    "asymmetric_value": ScoringProfile(
        name="asymmetric_value",
        description=(
            "Overlooked fundamentals profile that surfaces names where strong "
            "quality and cheap valuation have NOT been rewarded by market "
            "performance. The key mechanism: valuation signals dominate the "
            "profile while quality and safety act as gates. It rewards low "
            "multiples, peer-revenue value gaps, book/cash-flow support, street "
            "upside, and predictable future cash-flow evidence, while tactical "
            "momentum is only a small recovery hint. This creates a focused "
            "market-disconnect detector for quality companies the crowd has "
            "overlooked or punished unfairly. Safety is the primary value-trap "
            "filter: names with weak balance sheets cannot make the leaderboard "
            "regardless of cheapness."
        ),
        horizon_weights={
            "days": {
                "attention": 0.04,
                "event": 0.01,
                "momentum": 0.03,
                "trend": 0.07,
                "quality": 0.23,
                "valuation": 0.38,
                "safety": 0.24,
                "scale": 0.00,
            },
            "weeks": {
                "attention": 0.03,
                "event": 0.02,
                "momentum": 0.04,
                "trend": 0.06,
                "quality": 0.25,
                "valuation": 0.37,
                "safety": 0.23,
                "scale": 0.00,
            },
            "months": {
                "attention": 0.03,
                "event": 0.01,
                "momentum": 0.02,
                "trend": 0.04,
                "quality": 0.28,
                "valuation": 0.39,
                "safety": 0.23,
                "scale": 0.00,
            },
            "years": {
                "attention": 0.01,
                "event": 0.00,
                "momentum": 0.01,
                "trend": 0.02,
                "quality": 0.31,
                "valuation": 0.40,
                "safety": 0.25,
                "scale": 0.00,
            },
        },
        component_signal_weights={
            "momentum": {
                "change": 0.12,
                "Perf.5D": 0.15,
                "Perf.W": 0.20,
                "Perf.1M": 0.30,
                "Perf.3M": 0.80,
                "Perf.6M": 0.70,
                "Perf.YTD": 0.65,
                "Perf.Y": 0.50,
                "ROC": 0.35,
                "Mom": 0.35,
                "macd_spread": 0.30,
                "Recommend.All": 0.45,
                "Recommend.MA": 0.35,
                "Recommend.Other": 0.40,
                "rsi_centered": 0.20,
                "rsi7_centered": 0.10,
                "stoch_rsi_centered": 0.55,
                "stoch_rsi_crossover": 1.15,
            },
            "trend": {
                "close_vs_sma50": 0.60,
                "close_vs_sma200": 0.90,
                "close_vs_ema50": 0.60,
                "close_vs_ema200": 0.90,
                "close_vs_vwap": 0.35,
                "close_vs_vwma": 0.40,
                "trend_alignment": 0.75,
                "pivot_distance": 0.80,
                "short_trend_emergence": 0.85,
            },
            "quality": {
                "total_revenue_yoy_growth_ttm": 1.15,
                "total_revenue_qoq_growth_fq": 0.95,
                "ebitda_yoy_growth_ttm": 1.20,
                "ebitda_qoq_growth_fq": 1.00,
                "net_income_yoy_growth_ttm": 1.15,
                "net_income_qoq_growth_fq": 0.95,
                "free_cash_flow_yoy_growth_ttm": 1.40,
                "free_cash_flow_qoq_growth_fq": 1.15,
                "gross_margin": 1.15,
                "operating_margin": 1.25,
                "after_tax_margin": 1.10,
                "return_on_assets": 1.10,
                "return_on_equity": 1.15,
                "return_on_invested_capital": 1.40,
                "free_cash_flow_margin_ttm": 1.45,
                "buyback_yield": 1.20,
                "dividends_yield_current": 1.00,
                "eps_forward_growth": 1.55,
                "revenue_per_employee": 1.25,
                "earnings_per_share_diluted_yoy_growth_ttm": 1.20,
                "gross_profit_margin_fy": 1.10,
                "sustainable_growth_rate_ttm": 1.15,
                "piotroski_f_score_ttm": 1.40,
                "dividend_yield_recent": 0.90,
                "dps_common_stock_prim_issue_yoy_growth_fy": 0.80,
            },
            "valuation": {
                "price_earnings_ttm": 1.25,
                "price_earnings_growth_ttm": 1.25,
                "price_sales_current": 1.15,
                "price_book_fq": 1.30,
                "price_free_cash_flow_ttm": 1.40,
                "price_to_cash_f_operating_activities_ttm": 1.25,
                "enterprise_value_to_revenue_ttm": 1.15,
                "enterprise_value_to_ebit_ttm": 1.15,
                "enterprise_value_ebitda_ttm": 1.25,
                "earnings_yield": 1.55,
                "distance_from_52w_high": 1.30,
                "range_position_52w": 1.35,
                "enterprise_value_to_free_cash_flow_ttm": 1.50,
                "enterprise_value_to_gross_profit_ttm": 1.15,
                "price_target_upside_average": 1.25,
                "price_target_upside_median": 1.35,
                "price_target_downside_floor": 1.30,
                "book_value_discount": 1.40,
                "peer_revenue_value_gap": 1.35,
            },
            "safety": {
                "current_ratio": 1.15,
                "quick_ratio": 1.15,
                "cash_ratio": 1.10,
                "short_term_cash_coverage": 1.30,
                "altman_z_score_ttm": 1.35,
                "debt_to_equity": 1.30,
                "debt_to_revenue_ttm": 1.25,
                "net_debt": 1.25,
                "beta_1_year": 0.75,
                "total_debt_to_ebitda_fq": 1.35,
                "beta_adjusted_atrp": 0.85,
                "price_target_dispersion": 0.90,
            },
        },
        component_directional_bias={
            "attention": DirectionalBias(
                positive_multiplier=0.65, negative_multiplier=1.45
            ),
            "momentum": DirectionalBias(
                positive_multiplier=0.70, negative_multiplier=0.45
            ),
            "trend": DirectionalBias(
                positive_multiplier=0.90, negative_multiplier=0.70
            ),
            "quality": DirectionalBias(
                positive_multiplier=1.22, negative_multiplier=1.35
            ),
            "valuation": DirectionalBias(
                positive_multiplier=1.45, negative_multiplier=1.10
            ),
            "safety": DirectionalBias(
                positive_multiplier=1.15, negative_multiplier=1.40
            ),
        },
        missing_component_scores_by_horizon={
            "days": {
                "attention": -0.45,
                "valuation": -1.10,
                "quality": -1.05,
                "safety": -0.75,
            },
            "weeks": {
                "attention": -0.35,
                "valuation": -1.20,
                "quality": -1.15,
                "safety": -0.85,
            },
            "months": {
                "attention": -0.25,
                "valuation": -1.30,
                "quality": -1.25,
                "safety": -0.95,
            },
            "years": {
                "valuation": -1.35,
                "quality": -1.35,
                "safety": -1.05,
            },
        },
        intro_metric_notes=[
            "Overlooked fundamentals profile — finds cheap operating quality with asymmetric upside the market has NOT rewarded.",
            "Value is now the dominant lens: horizon valuation weights rise to 0.38/0.37/0.39/0.40 while tactical momentum and trend fall sharply. This keeps the profile focused on valuation disconnect, not price chase.",
            "Core value anchors: earnings_yield=1.55, EV/FCF=1.50, book_value_discount=1.40, price_free_cash_flow=1.40, price_book=1.30, target upside median=1.35, target downside floor=1.30.",
            "Peer-relative cheapness added: peer_revenue_value_gap=1.35 rewards companies whose revenue share exceeds market-cap share inside the current scan universe, especially industry scans.",
            "Forward cash-flow quality gate: eps_forward_growth=1.55, FCF_margin=1.45, FCF_growth=1.40, ROIC=1.40, Piotroski F-score=1.40. Cheapness must come with operating strength and future cash-flow visibility.",
            "Momentum is a small recovery hint only: positive=0.70x, negative=0.45x and low horizon weights. Weak price action is forgiven, not treated as a standalone reason to buy.",
            "Scale is explicitly zeroed so large companies do not rank just for size; relative value must come from valuation, quality, and safety evidence.",
            "Safety is the value-trap filter: altman_z_score=1.35, debt/EBITDA=1.35, short-term cash coverage=1.30, negative safety bias=1.40x. Weak balance sheets block cheap names.",
            "Missing valuation/quality evidence is punished more heavily. Asymmetry requires visible discount plus durable fundamentals, not sparse data.",
        ],
        confidence_multiplier=1.02,
    ),
    "value_recovery": ScoringProfile(
        name="value_recovery",
        description=(
            "Recovery trajectory profile that targets turnaround candidates where "
            "cheap valuation and SEQUENTIAL fundamental improvement combine. The "
            "key differentiator from asymmetric_value: this profile rewards names "
            "that are actively IMPROVING (QoQ metrics prioritized), not just "
            "statically cheap. Forward EPS growth expectations confirm the recovery "
            "thesis. Stoch RSI crosses detect the oversold-to-recovery technical "
            "turn. Noise reduction: momentum is damped so recent weakness does not "
            "penalize recovering names, but negative quality IS penalized — the "
            "profile requires evidence that the turn is real."
        ),
        horizon_weights={
            "days": {
                "attention": 0.06,
                "event": 0.04,
                "momentum": 0.04,
                "trend": 0.12,
                "quality": 0.24,
                "valuation": 0.26,
                "safety": 0.24,
            },
            "weeks": {
                "attention": 0.06,
                "event": 0.04,
                "momentum": 0.06,
                "trend": 0.14,
                "quality": 0.22,
                "valuation": 0.25,
                "safety": 0.23,
            },
            "months": {
                "attention": 0.04,
                "event": 0.03,
                "momentum": 0.05,
                "trend": 0.14,
                "quality": 0.28,
                "valuation": 0.26,
                "safety": 0.20,
            },
            "years": {
                "attention": 0.02,
                "event": 0.01,
                "momentum": 0.01,
                "trend": 0.07,
                "quality": 0.30,
                "valuation": 0.32,
                "safety": 0.27,
            },
        },
        component_signal_weights={
            "momentum": {
                "change": 0.35,
                "Perf.5D": 0.25,
                "Perf.W": 0.30,
                "Perf.1M": 0.50,
                "Perf.3M": 1.15,
                "Perf.YTD": 1.00,
                "Perf.Y": 0.70,
                "ROC": 0.50,
                "Mom": 0.55,
                "macd_spread": 0.45,
                "Recommend.All": 0.65,
                "Recommend.MA": 0.55,
                "Recommend.Other": 0.65,
                "rsi_centered": 0.20,
                "rsi7_centered": 0.12,
                "stoch_rsi_centered": 0.55,
                "stoch_rsi_crossover": 1.35,
            },
            "valuation": {
                "price_earnings_ttm": 1.15,
                "price_earnings_growth_ttm": 1.25,
                "price_book_fq": 1.25,
                "price_sales_current": 1.10,
                "price_free_cash_flow_ttm": 1.10,
                "price_to_cash_f_operating_activities_ttm": 1.00,
                "enterprise_value_to_revenue_ttm": 1.10,
                "enterprise_value_to_ebit_ttm": 1.00,
                "enterprise_value_ebitda_ttm": 1.20,
                "earnings_yield": 1.25,
                "distance_from_52w_high": 1.20,
                "range_position_52w": 1.10,
                "enterprise_value_to_free_cash_flow_ttm": 1.15,
                "enterprise_value_to_gross_profit_ttm": 0.90,
                "price_target_upside_average": 1.20,
                "price_target_upside_median": 1.20,
                "price_target_downside_floor": 1.00,
                "book_value_discount": 1.10,
            },
            "quality": {
                "total_revenue_yoy_growth_ttm": 0.85,
                "total_revenue_qoq_growth_fq": 1.25,
                "ebitda_yoy_growth_ttm": 0.90,
                "ebitda_qoq_growth_fq": 1.20,
                "net_income_yoy_growth_ttm": 0.95,
                "net_income_qoq_growth_fq": 1.30,
                "free_cash_flow_yoy_growth_ttm": 1.10,
                "free_cash_flow_qoq_growth_fq": 1.35,
                "gross_margin": 0.90,
                "operating_margin": 1.10,
                "after_tax_margin": 1.00,
                "return_on_assets": 0.90,
                "return_on_equity": 0.95,
                "return_on_invested_capital": 1.10,
                "free_cash_flow_margin_ttm": 1.20,
                "buyback_yield": 0.65,
                "dividends_yield_current": 0.55,
                "eps_forward_growth": 1.50,
                "revenue_per_employee": 0.80,
                "earnings_per_share_diluted_yoy_growth_ttm": 1.00,
                "gross_profit_margin_fy": 0.85,
                "sustainable_growth_rate_ttm": 1.10,
                "piotroski_f_score_ttm": 1.20,
                "dps_common_stock_prim_issue_yoy_growth_fy": 0.50,
            },
            "safety": {
                "current_ratio": 1.05,
                "quick_ratio": 1.05,
                "cash_ratio": 1.00,
                "short_term_cash_coverage": 1.15,
                "altman_z_score_ttm": 1.25,
                "debt_to_equity": 1.20,
                "debt_to_revenue_ttm": 1.15,
                "net_debt": 1.20,
                "beta_1_year": 0.80,
                "total_debt_to_ebitda_fq": 1.25,
                "beta_adjusted_atrp": 0.75,
                "price_target_dispersion": 0.65,
            },
            "trend": {
                "close_vs_sma50": 0.85,
                "close_vs_sma200": 1.10,
                "close_vs_ema50": 0.85,
                "close_vs_ema200": 1.10,
                "close_vs_vwap": 0.55,
                "close_vs_vwma": 0.70,
                "trend_alignment": 1.05,
                "pivot_distance": 1.10,
                "short_trend_emergence": 1.20,
            },
        },
        component_directional_bias={
            "attention": DirectionalBias(
                positive_multiplier=0.82, negative_multiplier=1.32
            ),
            "momentum": DirectionalBias(
                positive_multiplier=0.85, negative_multiplier=0.60
            ),
            "trend": DirectionalBias(
                positive_multiplier=1.08, negative_multiplier=0.85
            ),
            "valuation": DirectionalBias(
                positive_multiplier=1.30, negative_multiplier=1.12
            ),
            "quality": DirectionalBias(
                positive_multiplier=1.18, negative_multiplier=1.18
            ),
            "safety": DirectionalBias(
                positive_multiplier=1.18, negative_multiplier=1.20
            ),
        },
        missing_component_scores_by_horizon={
            "days": {
                "attention": -0.40,
                "valuation": -0.85,
                "quality": -0.55,
                "safety": -0.65,
            },
            "weeks": {
                "attention": -0.30,
                "valuation": -0.95,
                "quality": -0.65,
                "safety": -0.75,
            },
            "months": {
                "attention": -0.20,
                "valuation": -1.05,
                "quality": -0.75,
                "safety": -0.85,
            },
            "years": {
                "valuation": -1.15,
                "quality": -0.85,
                "safety": -0.95,
            },
        },
        intro_metric_notes=[
            "Recovery trajectory profile — rewards active IMPROVEMENT, not static cheapness.",
            "QoQ growth dominates YoY: QoQ=1.20-1.35 vs YoY=0.85-1.10. Sequential positive inflection is the recovery signal.",
            "Forward recovery conviction: eps_forward_growth=1.50 (highest in system). Analyst revision toward positive EPS confirms the recovery thesis.",
            "Street-value confirmation added: target upside average/median=1.20, target downside floor=1.00, book_value_discount=1.10. Recovery candidates need upside support and a tolerable downside floor.",
            "Value-trap controls strengthened with Piotroski F-score=1.20 and debt/EBITDA=1.25.",
            "Technical recovery detection: stoch_rsi_crossover=1.35 (raised from 0.65) is the PRIMARY entry trigger — oversold-to-recovery momentum crossover at the start of the move.",
            "Trend reversal confirmation: short_trend_emergence=1.20 (raised from 0.75) — detects first meaningful break above compressed price action.",
            "Momentum bias inverted for downside: negative=0.60x so recent poor performance does not dominate rankings. Recovering names need room to breathe.",
            "Valuation discount detection: distance_from_52w_high=1.20, price_book=1.25 (tangible asset value relevant for recovery plays), enterprise_value_ebitda=1.20.",
            "Quality directional bias symmetrical: positive=1.18x, negative=1.18x. Equally rewards improving quality and punishes deteriorating quality — the turn must be real.",
        ],
    ),
    # ──────────────────────────────────────────────────────────────────────────
    # IDEA 2b — AGGRESSIVE VALUE + CATALYST: value is awakening detector
    # ──────────────────────────────────────────────────────────────────────────
    "deep_value_momentum": ScoringProfile(
        name="deep_value_momentum",
        description=(
            "Aggressive value + catalyst profile for nimble positioning. Identifies "
            "operating companies with deep valuation discounts AND confirmed early "
            "momentum reversal signals. Designed for investors who want the margin "
            "of safety of value metrics COMBINED with evidence the market is beginning "
            "to recognize that value. Requires BOTH deep cheapness AND early technical "
            "reversal — pure cheapness or pure momentum alone is insufficient. "
            "Excludes passive vehicles (investment trusts, gold ETFs) by requiring "
            "active operating quality metrics including forward EPS growth and "
            "sequential EBITDA/FCF improvement. Targets: semiconductor turnarounds, "
            "deep value industrials, beaten-down growth companies with improving "
            "margins. The combination of earnings_yield, EV/EBITDA, price_book "
            "PLUS stoch_rsi_crossover and short_trend_emergence is the defining "
            "signal pair for the value-driven aggressive risk style."
        ),
        horizon_weights={
            "days": {
                "attention": 0.07,
                "event": 0.03,
                "momentum": 0.22,
                "trend": 0.22,
                "quality": 0.16,
                "valuation": 0.22,
                "safety": 0.08,
            },
            "weeks": {
                "attention": 0.05,
                "event": 0.03,
                "momentum": 0.16,
                "trend": 0.22,
                "quality": 0.22,
                "valuation": 0.24,
                "safety": 0.08,
            },
            "months": {
                "attention": 0.03,
                "event": 0.02,
                "momentum": 0.10,
                "trend": 0.16,
                "quality": 0.28,
                "valuation": 0.28,
                "safety": 0.13,
            },
            "years": {
                "attention": 0.02,
                "event": 0.01,
                "momentum": 0.05,
                "trend": 0.10,
                "quality": 0.32,
                "valuation": 0.32,
                "safety": 0.18,
            },
        },
        component_signal_weights={
            "attention": {
                "relative_volume_10d_calc": 1.10,
                "float_turnover": 1.05,
                "dollar_turnover_intensity": 1.00,
                "volume_trend": 1.35,
                "intraday_momentum": 0.80,
            },
            "momentum": {
                "change": 0.50,
                "Perf.5D": 0.70,
                "Perf.W": 0.80,
                "Perf.1M": 0.60,
                "Perf.3M": 0.35,
                "Perf.6M": 0.20,
                "Perf.YTD": 0.15,
                "Perf.Y": 0.10,
                "ROC": 0.80,
                "Mom": 0.75,
                "macd_spread": 0.90,
                "Recommend.All": 0.80,
                "Recommend.MA": 0.75,
                "Recommend.Other": 0.70,
                "rsi_centered": 0.60,
                "rsi7_centered": 0.85,
                "aroon_spread": 1.35,
                "adx_directional_spread": 1.30,
                "stoch_rsi_centered": 1.10,
                "stoch_rsi_crossover": 1.55,
                "CCI20": 0.70,
            },
            "trend": {
                "close_vs_sma10": 1.10,
                "close_vs_sma20": 1.00,
                "close_vs_sma50": 0.80,
                "close_vs_sma200": 0.50,
                "close_vs_ema10": 1.10,
                "close_vs_ema20": 1.00,
                "close_vs_ema50": 0.80,
                "close_vs_ema200": 0.50,
                "close_vs_vwap": 1.00,
                "close_vs_vwma": 1.00,
                "trend_alignment": 0.90,
                "bb_position": 1.10,
                "short_trend_emergence": 1.40,
                "pivot_distance": 0.80,
                "close_vs_camarilla_s1": 0.90,
                "close_vs_camarilla_s2": 0.70,
                "close_vs_camarilla_r1": 1.00,
                "close_vs_camarilla_r2": 0.80,
            },
            "quality": {
                "total_revenue_yoy_growth_ttm": 0.80,
                "total_revenue_qoq_growth_fq": 1.20,
                "ebitda_yoy_growth_ttm": 0.85,
                "ebitda_qoq_growth_fq": 1.25,
                "net_income_yoy_growth_ttm": 0.90,
                "net_income_qoq_growth_fq": 1.30,
                "free_cash_flow_yoy_growth_ttm": 1.00,
                "free_cash_flow_qoq_growth_fq": 1.40,
                "gross_margin": 0.85,
                "operating_margin": 1.10,
                "after_tax_margin": 0.95,
                "return_on_assets": 0.90,
                "return_on_equity": 0.95,
                "return_on_invested_capital": 1.10,
                "free_cash_flow_margin_ttm": 1.15,
                "buyback_yield": 0.70,
                "dividends_yield_current": 0.50,
                "eps_forward_growth": 1.55,
                "revenue_per_employee": 0.80,
                "earnings_per_share_diluted_yoy_growth_ttm": 1.05,
                "gross_profit_margin_fy": 0.80,
                "sustainable_growth_rate_ttm": 1.15,
                "piotroski_f_score_ttm": 1.25,
                "dps_common_stock_prim_issue_yoy_growth_fy": 0.45,
            },
            "valuation": {
                "price_earnings_ttm": 1.20,
                "price_earnings_growth_ttm": 1.10,
                "price_sales_current": 0.90,
                "price_book_fq": 1.35,
                "price_free_cash_flow_ttm": 1.25,
                "price_to_cash_f_operating_activities_ttm": 1.15,
                "enterprise_value_to_revenue_ttm": 1.00,
                "enterprise_value_to_ebit_ttm": 1.10,
                "enterprise_value_ebitda_ttm": 1.30,
                "earnings_yield": 1.40,
                "distance_from_52w_high": 1.20,
                "range_position_52w": 1.10,
                "enterprise_value_to_free_cash_flow_ttm": 1.30,
                "enterprise_value_to_gross_profit_ttm": 1.00,
                "price_target_upside_average": 1.25,
                "price_target_upside_median": 1.30,
                "price_target_downside_floor": 1.15,
                "book_value_discount": 1.25,
            },
            "safety": {
                "current_ratio": 1.00,
                "quick_ratio": 1.05,
                "cash_ratio": 1.00,
                "short_term_cash_coverage": 1.10,
                "altman_z_score_ttm": 1.20,
                "debt_to_equity": 1.15,
                "debt_to_revenue_ttm": 1.10,
                "net_debt": 1.15,
                "beta_1_year": 0.75,
                "total_debt_to_ebitda_fq": 1.20,
                "beta_adjusted_atrp": 0.85,
                "price_target_dispersion": 0.70,
            },
        },
        component_directional_bias={
            "attention": DirectionalBias(
                positive_multiplier=0.90, negative_multiplier=1.20
            ),
            "momentum": DirectionalBias(
                positive_multiplier=1.35, negative_multiplier=0.70
            ),
            "trend": DirectionalBias(
                positive_multiplier=1.30, negative_multiplier=0.75
            ),
            "quality": DirectionalBias(
                positive_multiplier=1.20, negative_multiplier=1.20
            ),
            "valuation": DirectionalBias(
                positive_multiplier=1.30, negative_multiplier=1.10
            ),
            "safety": DirectionalBias(
                positive_multiplier=1.10, negative_multiplier=1.30
            ),
        },
        missing_component_scores_by_horizon={
            "days": {
                "attention": -0.30,
                "quality": -1.00,
                "valuation": -0.85,
                "safety": -0.60,
            },
            "weeks": {
                "attention": -0.25,
                "quality": -1.10,
                "valuation": -0.95,
                "safety": -0.70,
            },
            "months": {
                "attention": -0.15,
                "quality": -1.20,
                "valuation": -1.05,
                "safety": -0.80,
            },
            "years": {
                "quality": -1.30,
                "valuation": -1.15,
                "safety": -0.90,
            },
        },
        intro_metric_notes=[
            "Aggressive value + catalyst profile — the 'value is awakening' detector for the nimble aggressive style.",
            "Primary signal pair: deep valuation discount (price_book=1.35, earnings_yield=1.40, EV/EBITDA=1.30) COMBINED WITH technical reversal (stoch_rsi_crossover=1.55, short_trend_emergence=1.40).",
            "Value detection broadened with book_value_discount=1.25, EV/FCF=1.30, target upside median=1.30, and target downside floor=1.15 — value must be visible across accounting value, cash flow value, and street fair-value gap.",
            "Risk guardrails: debt/EBITDA=1.20, beta_adjusted_atrp=0.85, and target dispersion=0.70 prevent the profile from blindly chasing volatile value traps.",
            "Sequential improvement is the quality gate: QoQ FCF growth=1.40, QoQ net income=1.30, QoQ EBITDA=1.25. Confirms the fundamental turn is real.",
            "Forward earnings catalyst required: eps_forward_growth=1.55 (highest in system). ETFs and Investment Trusts score near zero here — the primary passive vehicle filter.",
            "Momentum directional bias AMPLIFIED (positive=1.35x, negative=0.70x) for early reversal confirmation. Unlike asymmetric_value, this profile REWARDS early price recovery.",
            "Missing quality penalty at -1.00 to -1.30 across horizons: passive vehicles without operating quality data cannot make the leaderboard.",
            "Designed for: semiconductor turnarounds, deep value industrials, beaten-down companies with improving margins. The value + catalyst intersection.",
        ],
        confidence_multiplier=1.05,
    ),
    # ──────────────────────────────────────────────────────────────────────────
    # IDEA 3 — HEDGING: capture drastic event opportunities and short pressure
    # ──────────────────────────────────────────────────────────────────────────
    "fragility_short": ScoringProfile(
        name="fragility_short",
        description=(
            "Hedging and short opportunity profile that identifies structurally "
            "fragile names positioned for downside dislocation. The scoring is "
            "INVERTED: negative event pressure, deteriorating momentum, breaking "
            "trend, and weak safety are all REWARDED. This profile specifically "
            "answers 'what is most likely to break?' for hedging basket "
            "construction and short watchlist management. Noise reduction: "
            "requires convergence of MULTIPLE weakness signals — a single "
            "component failure is insufficient. Heavy safety emphasis at longer "
            "horizons catches leverage-driven blowup risk. EPS surprise history "
            "and forward growth are activated to detect earnings deterioration "
            "catalysts."
        ),
        horizon_weights={
            "days": {
                "attention": 0.16,
                "event": 0.30,
                "momentum": 0.28,
                "trend": 0.18,
                "quality": 0.02,
                "valuation": 0.01,
                "safety": 0.05,
            },
            "weeks": {
                "attention": 0.08,
                "event": 0.20,
                "momentum": 0.28,
                "trend": 0.24,
                "quality": 0.04,
                "valuation": 0.02,
                "safety": 0.14,
            },
            "months": {
                "attention": 0.04,
                "event": 0.08,
                "momentum": 0.20,
                "trend": 0.22,
                "quality": 0.14,
                "valuation": 0.04,
                "safety": 0.28,
            },
            "years": {
                "attention": 0.00,
                "event": 0.02,
                "momentum": 0.06,
                "trend": 0.12,
                "quality": 0.18,
                "valuation": 0.05,
                "safety": 0.57,
            },
        },
        component_signal_weights={
            "attention": {
                "relative_volume_10d_calc": 1.20,
                "float_turnover": 1.15,
                "dollar_turnover_intensity": 1.10,
                "Value.Traded": 1.05,
                "AvgValue.Traded_10d": 1.00,
                "volume_trend": 1.30,
                "intraday_momentum": 1.20,
            },
            "event": {
                "premarket_change": 1.20,
                "postmarket_change": 1.20,
                "gap": 1.20,
                "gap_severity": 1.35,
                "event_intensity": 1.15,
                "eps_surprise_percent_fq": 1.25,
            },
            "momentum": {
                "change": 1.20,
                "Perf.5D": 1.25,
                "Perf.W": 1.25,
                "Perf.1M": 1.15,
                "Perf.3M": 1.10,
                "Perf.6M": 0.90,
                "Perf.YTD": 0.85,
                "Perf.Y": 0.70,
                "ROC": 1.15,
                "Mom": 1.10,
                "macd_spread": 1.10,
                "Recommend.All": 0.95,
                "Recommend.MA": 0.90,
                "Recommend.Other": 0.95,
                "rsi_centered": 0.80,
                "rsi7_centered": 0.85,
                "aroon_spread": 1.15,
                "adx_directional_spread": 1.20,
                "stoch_rsi_centered": 0.75,
                "CCI20": 0.80,
            },
            "trend": {
                "close_vs_sma10": 1.10,
                "close_vs_sma20": 1.05,
                "close_vs_sma50": 1.15,
                "close_vs_sma200": 1.20,
                "close_vs_ema10": 1.10,
                "close_vs_ema20": 1.05,
                "close_vs_ema50": 1.15,
                "close_vs_ema200": 1.20,
                "close_vs_vwap": 1.10,
                "close_vs_vwma": 1.10,
                "trend_alignment": 1.25,
                "short_trend_emergence": 0.90,
                "pivot_distance": 0.95,
            },
            "quality": {
                "total_revenue_yoy_growth_ttm": 1.10,
                "total_revenue_qoq_growth_fq": 1.15,
                "ebitda_yoy_growth_ttm": 1.10,
                "ebitda_qoq_growth_fq": 1.15,
                "net_income_yoy_growth_ttm": 1.10,
                "net_income_qoq_growth_fq": 1.15,
                "free_cash_flow_yoy_growth_ttm": 1.20,
                "free_cash_flow_qoq_growth_fq": 1.20,
                "gross_margin": 1.00,
                "operating_margin": 1.15,
                "after_tax_margin": 1.00,
                "return_on_assets": 1.00,
                "return_on_equity": 1.05,
                "return_on_invested_capital": 1.10,
                "free_cash_flow_margin_ttm": 1.20,
                "eps_forward_growth": 1.15,
                "earnings_per_share_diluted_yoy_growth_ttm": 1.10,
                "sustainable_growth_rate_ttm": 1.10,
                "piotroski_f_score_ttm": 1.25,
            },
            "valuation": {
                "price_target_downside_floor": 1.20,
                "enterprise_value_to_free_cash_flow_ttm": 1.10,
                "enterprise_value_to_gross_profit_ttm": 1.05,
                "book_value_discount": 0.80,
            },
            "safety": {
                "current_ratio": 1.10,
                "quick_ratio": 1.10,
                "cash_ratio": 1.05,
                "short_term_cash_coverage": 1.15,
                "debt_to_equity": 1.35,
                "debt_to_revenue_ttm": 1.30,
                "net_debt": 1.25,
                "altman_z_score_ttm": 1.35,
                "beta_1_year": 1.15,
                "total_debt_to_ebitda_fq": 1.45,
                "beta_adjusted_atrp": 1.25,
            },
        },
        component_directional_bias={
            "attention": DirectionalBias(
                positive_multiplier=1.00, negative_multiplier=1.00
            ),
            "event": DirectionalBias(
                positive_multiplier=0.85, negative_multiplier=1.30
            ),
            "momentum": DirectionalBias(
                positive_multiplier=0.80, negative_multiplier=1.30
            ),
            "trend": DirectionalBias(
                positive_multiplier=0.85, negative_multiplier=1.35
            ),
            "quality": DirectionalBias(
                positive_multiplier=0.85, negative_multiplier=1.25
            ),
            "valuation": DirectionalBias(
                positive_multiplier=0.90, negative_multiplier=1.20
            ),
            "safety": DirectionalBias(
                positive_multiplier=0.80, negative_multiplier=1.45
            ),
        },
        missing_component_scores_by_horizon={
            "days": {
                "event": -0.45,
                "momentum": -0.50,
                "trend": -0.40,
            },
            "weeks": {
                "momentum": -0.55,
                "trend": -0.45,
                "safety": -0.50,
            },
            "months": {
                "trend": -0.50,
                "safety": -0.70,
                "quality": -0.40,
            },
            "years": {
                "safety": -0.90,
                "quality": -0.45,
                "valuation": -0.30,
            },
        },
        intro_metric_notes=[
            "Hedging and short identification profile — answers 'what is most likely to break?'.",
            "Scoring is INVERTED: all directional biases amplify NEGATIVE readings (event -1.30x, momentum -1.30x, trend -1.35x, quality -1.25x, safety -1.45x) while suppressing positive readings.",
            "Safety is THE dominant factor: years horizon safety=0.57 (highest single-component weight in system). Leverage-driven blowup risk is the strongest structural short thesis.",
            "Safety signal weights: debt_to_equity=1.35, altman_z_score=1.35, debt_to_revenue=1.30, net_debt=1.25, beta=1.15. Multiple leverage metrics must converge.",
            "Fragility risk strengthened with debt/EBITDA=1.45, beta_adjusted_atrp=1.25, weak Piotroski=1.25, and target downside floor=1.20. This surfaces shorts where leverage, volatility, and street downside align.",
            "Catalyst detection: eps_surprise_percent_fq=1.25 catches names with history of missing. eps_forward_growth=1.15 detects analyst downgrades.",
            "Event pressure heavily weighted at days horizon: event=0.30, with gap_severity=1.35 and gap=1.20. Earnings gaps and intraday collapses are key short-term catalysts.",
            "Beta is NOT inverted for this profile: beta_1_year=1.15 means higher beta HELPS the fragility score — more volatile names are more susceptible to downside dislocations.",
            "Attention is neutral (1.0/1.0) — fragility can strike crowded AND uncrowded names equally.",
        ],
        confidence_multiplier=1.07,
    ),
}


# Backwards compatibility: resolve 'balanced' to 'quality_value_compounder'
# and 'backtest_period_ladder' is no longer available.
_PROFILE_ALIASES = {
    "balanced": "quality_value_compounder",
    "backtest_period_ladder": "sector_relative_outperformer",
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
        return PRESET_SCORING_PROFILES["quality_value_compounder"]
    if isinstance(scoring_profile, ScoringProfile):
        return scoring_profile
    profile_key = str(scoring_profile)
    profile_key = _PROFILE_ALIASES.get(profile_key, profile_key)
    profile = PRESET_SCORING_PROFILES.get(profile_key)
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

    description = row.get("description")
    if description:
        return str(description)

    name = row.get("name")
    if name:
        return str(name)

    return "N/A"


def _get_company_description(row: dict[str, Any]) -> str:
    ticker_view = row.get("ticker-view")
    if isinstance(ticker_view, dict):
        description = ticker_view.get("description")
        if description:
            return str(description)
    description = row.get("description")
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
    avg_volume_30d = _coerce_numeric(row.get("average_volume_30d_calc"))
    stoch_rsi_d = _coerce_numeric(row.get("Stoch.RSI.D"))
    change_from_open = _coerce_numeric(row.get("change_from_open"))
    total_revenue_val = _coerce_numeric(row.get("total_revenue"))
    num_employees = _coerce_numeric(row.get("number_of_employees"))
    pivot_middle = _coerce_numeric(row.get("Pivot.M.Classic.Middle"))
    price_target_average = _coerce_numeric(row.get("price_target_average"))
    price_target_median = _coerce_numeric(row.get("price_target_median"))
    price_target_high = _coerce_numeric(row.get("price_target_high"))
    price_target_low = _coerce_numeric(row.get("price_target_low"))
    book_value_per_share = _coerce_numeric(row.get("book_value_per_share_fq"))
    beta_1_year = _coerce_numeric(row.get("beta_1_year"))
    peer_market_cap_share = _coerce_numeric(row.get("_peer_market_cap_share"))
    peer_revenue_share = _coerce_numeric(row.get("_peer_revenue_share"))
    camarilla_s1 = _coerce_numeric(row.get("Pivot.M.Camarilla.S1"))
    camarilla_s2 = _coerce_numeric(row.get("Pivot.M.Camarilla.S2"))
    camarilla_r1 = _coerce_numeric(row.get("Pivot.M.Camarilla.R1"))
    camarilla_r2 = _coerce_numeric(row.get("Pivot.M.Camarilla.R2"))
    graham_number = _coerce_numeric(row.get("graham_numbers_ttm"))
    if graham_number is None:
        graham_number = _coerce_numeric(row.get("graham_numbers_fy"))
    tangible_book_per_share = _coerce_numeric(
        row.get("book_tangible_per_share_current")
    )
    if tangible_book_per_share is None:
        tangible_book_per_share = _coerce_numeric(row.get("book_tangible_per_share_fq"))
    cash_per_share = _coerce_numeric(row.get("cash_per_share_current"))
    if cash_per_share is None:
        cash_per_share = _coerce_numeric(row.get("cash_per_share_fq"))
    free_cash_flow_ttm = _coerce_numeric(row.get("free_cash_flow_ttm"))
    if free_cash_flow_ttm is None:
        free_cash_flow_ttm = _coerce_numeric(row.get("free_cash_flow"))
    operating_cash_flow_ttm = _coerce_numeric(
        row.get("cash_f_operating_activities_ttm")
    )
    free_cash_flow_per_share = _coerce_numeric(row.get("free_cash_flow_per_share_ttm"))
    operating_cash_flow_per_share = _coerce_numeric(
        row.get("operating_cash_flow_per_share_ttm")
    )
    ebit_ttm = _coerce_numeric(row.get("ebit_ttm"))
    gross_profit_ttm = _coerce_numeric(row.get("gross_profit_ttm"))
    enterprise_value = _coerce_numeric(row.get("enterprise_value_current"))
    if enterprise_value is None:
        enterprise_value = _coerce_numeric(row.get("enterprise_value_fq"))
    buyback_yield_value = _coerce_numeric(row.get("buyback_yield"))
    dividends_yield_value = _coerce_numeric(row.get("dividends_yield_current"))
    if dividends_yield_value is None:
        dividends_yield_value = _coerce_numeric(row.get("dividend_yield_recent"))
    net_debt_value = _coerce_numeric(row.get("net_debt"))

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

    volume_trend: float | None = None
    if (
        average_volume_10d is not None
        and avg_volume_30d is not None
        and avg_volume_30d > 0
    ):
        volume_trend = average_volume_10d / avg_volume_30d

    stoch_rsi_crossover: float | None = None
    if stoch_rsi_k is not None and stoch_rsi_d is not None:
        stoch_rsi_crossover = stoch_rsi_k - stoch_rsi_d

    intraday_momentum: float | None = change_from_open

    pivot_distance: float | None = None
    if close is not None and pivot_middle is not None and close != 0:
        pivot_distance = (close - pivot_middle) / close

    revenue_per_employee: float | None = None
    if (
        total_revenue_val is not None
        and num_employees is not None
        and num_employees > 0
    ):
        revenue_per_employee = total_revenue_val / num_employees

    price_target_upside_average: float | None = None
    if close is not None and close != 0 and price_target_average is not None:
        price_target_upside_average = (price_target_average - close) / close

    price_target_upside_median: float | None = None
    if close is not None and close != 0 and price_target_median is not None:
        price_target_upside_median = (price_target_median - close) / close

    price_target_downside_floor: float | None = None
    if close is not None and close != 0 and price_target_low is not None:
        price_target_downside_floor = (price_target_low - close) / close

    price_target_dispersion: float | None = None
    if (
        close is not None
        and close != 0
        and price_target_high is not None
        and price_target_low is not None
    ):
        price_target_dispersion = (price_target_high - price_target_low) / close

    book_value_discount: float | None = None
    if close is not None and close != 0 and book_value_per_share is not None:
        book_value_discount = (book_value_per_share - close) / close

    def _per_share_value_gap(anchor_value: float | None) -> float | None:
        if close is None or close == 0 or anchor_value is None:
            return None
        return (anchor_value - close) / close

    graham_value_gap = _per_share_value_gap(graham_number)
    tangible_book_value_gap = _per_share_value_gap(tangible_book_per_share)
    cash_per_share_value_gap = _per_share_value_gap(cash_per_share)

    peer_revenue_value_gap: float | None = None
    if peer_revenue_share is not None and peer_market_cap_share is not None:
        peer_revenue_value_gap = peer_revenue_share - peer_market_cap_share

    beta_adjusted_atrp: float | None = None
    if atrp is not None:
        beta_adjusted_atrp = atrp * (beta_1_year if beta_1_year is not None else 1.0)

    free_cash_flow_yield = _safe_ratio(free_cash_flow_ttm, market_cap)
    operating_cash_flow_yield = _safe_ratio(operating_cash_flow_ttm, market_cap)
    free_cash_flow_per_share_yield = _safe_ratio(free_cash_flow_per_share, close)
    operating_cash_flow_per_share_yield = _safe_ratio(
        operating_cash_flow_per_share, close
    )
    ebit_yield_ev = _safe_ratio(ebit_ttm, enterprise_value)
    gross_profit_yield_ev = _safe_ratio(gross_profit_ttm, enterprise_value)
    shareholder_yield_inputs = [
        value
        for value in (buyback_yield_value, dividends_yield_value)
        if value is not None
    ]
    shareholder_yield = (
        sum(shareholder_yield_inputs) if shareholder_yield_inputs else None
    )
    net_cash_to_market_cap = (
        _safe_ratio(-net_debt_value, market_cap) if net_debt_value is not None else None
    )

    def _pivot_distance(pivot_value: float | None) -> float | None:
        if close is None or close == 0 or pivot_value is None:
            return None
        return (close - pivot_value) / close

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
        "volume_trend": volume_trend,
        "stoch_rsi_crossover": stoch_rsi_crossover,
        "intraday_momentum": intraday_momentum,
        "pivot_distance": pivot_distance,
        "revenue_per_employee": revenue_per_employee,
        "price_target_upside_average": price_target_upside_average,
        "price_target_upside_median": price_target_upside_median,
        "price_target_downside_floor": price_target_downside_floor,
        "price_target_dispersion": price_target_dispersion,
        "book_value_discount": book_value_discount,
        "peer_revenue_value_gap": peer_revenue_value_gap,
        "beta_adjusted_atrp": beta_adjusted_atrp,
        "close_vs_camarilla_s1": _pivot_distance(camarilla_s1),
        "close_vs_camarilla_s2": _pivot_distance(camarilla_s2),
        "close_vs_camarilla_r1": _pivot_distance(camarilla_r1),
        "close_vs_camarilla_r2": _pivot_distance(camarilla_r2),
        "graham_value_gap": graham_value_gap,
        "tangible_book_value_gap": tangible_book_value_gap,
        "cash_per_share_value_gap": cash_per_share_value_gap,
        "free_cash_flow_yield": free_cash_flow_yield,
        "operating_cash_flow_yield": operating_cash_flow_yield,
        "free_cash_flow_per_share_yield": free_cash_flow_per_share_yield,
        "operating_cash_flow_per_share_yield": operating_cash_flow_per_share_yield,
        "ebit_yield_ev": ebit_yield_ev,
        "gross_profit_yield_ev": gross_profit_yield_ev,
        "shareholder_yield": shareholder_yield,
        "net_cash_to_market_cap": net_cash_to_market_cap,
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
            "volume_trend": _derived_signal(derived_row, profiles, "volume_trend"),
            "intraday_momentum": _derived_signal(
                derived_row, profiles, "intraday_momentum"
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
            "stoch_rsi_crossover": _derived_signal(
                derived_row, profiles, "stoch_rsi_crossover"
            ),
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
            "pivot_distance": _derived_signal(derived_row, profiles, "pivot_distance"),
            "close_vs_camarilla_s1": _derived_signal(
                derived_row, profiles, "close_vs_camarilla_s1"
            ),
            "close_vs_camarilla_s2": _derived_signal(
                derived_row, profiles, "close_vs_camarilla_s2"
            ),
            "close_vs_camarilla_r1": _derived_signal(
                derived_row, profiles, "close_vs_camarilla_r1"
            ),
            "close_vs_camarilla_r2": _derived_signal(
                derived_row, profiles, "close_vs_camarilla_r2"
            ),
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
            "revenue_per_employee": _derived_signal(
                derived_row, profiles, "revenue_per_employee"
            ),
            "earnings_per_share_diluted_yoy_growth_ttm": _field_signal(
                row, profiles, "earnings_per_share_diluted_yoy_growth_ttm"
            ),
            "gross_profit_margin_fy": _field_signal(
                row, profiles, "gross_profit_margin_fy"
            ),
            "sustainable_growth_rate_ttm": _field_signal(
                row, profiles, "sustainable_growth_rate_ttm"
            ),
            "piotroski_f_score_ttm": _field_signal(
                row, profiles, "piotroski_f_score_ttm"
            ),
            "dividend_yield_recent": _field_signal(
                row, profiles, "dividend_yield_recent"
            ),
            "dps_common_stock_prim_issue_yoy_growth_fy": _field_signal(
                row, profiles, "dps_common_stock_prim_issue_yoy_growth_fy"
            ),
            "total_revenue_cagr_5y": _field_signal(
                row, profiles, "total_revenue_cagr_5y"
            ),
            "free_cash_flow_cagr_5y": _field_signal(
                row, profiles, "free_cash_flow_cagr_5y"
            ),
            "net_income_cagr_5y": _field_signal(row, profiles, "net_income_cagr_5y"),
            "earnings_per_share_basic_cagr_5y": _field_signal(
                row, profiles, "earnings_per_share_basic_cagr_5y"
            ),
            "earnings_per_share_diluted_5y_growth_fy": _field_signal(
                row, profiles, "earnings_per_share_diluted_5y_growth_fy"
            ),
            "gross_margin_ttm": _field_signal(row, profiles, "gross_margin_ttm"),
            "operating_margin_ttm": _field_signal(
                row, profiles, "operating_margin_ttm"
            ),
            "net_margin_ttm": _field_signal(row, profiles, "net_margin_ttm"),
            "ebitda_margin_ttm": _field_signal(row, profiles, "ebitda_margin_ttm"),
            "asset_turnover_current": _field_signal(
                row, profiles, "asset_turnover_current"
            ),
            "return_on_capital_employed_fy": _field_signal(
                row, profiles, "return_on_capital_employed_fy"
            ),
            "return_on_common_equity_ttm": _field_signal(
                row, profiles, "return_on_common_equity_ttm"
            ),
            "return_on_tang_equity_fy": _field_signal(
                row, profiles, "return_on_tang_equity_fy"
            ),
            "sloan_ratio_ttm": _field_signal(
                row, profiles, "sloan_ratio_ttm", invert=True
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
            "enterprise_value_to_free_cash_flow_ttm": _field_signal(
                row,
                profiles,
                "enterprise_value_to_free_cash_flow_ttm",
                invert=True,
                positive_only=True,
            ),
            "enterprise_value_to_gross_profit_ttm": _field_signal(
                row,
                profiles,
                "enterprise_value_to_gross_profit_ttm",
                invert=True,
                positive_only=True,
            ),
            "price_target_upside_average": _derived_signal(
                derived_row, profiles, "price_target_upside_average"
            ),
            "price_target_upside_median": _derived_signal(
                derived_row, profiles, "price_target_upside_median"
            ),
            "price_target_downside_floor": _derived_signal(
                derived_row, profiles, "price_target_downside_floor"
            ),
            "book_value_discount": _derived_signal(
                derived_row, profiles, "book_value_discount"
            ),
            "peer_revenue_value_gap": _derived_signal(
                derived_row, profiles, "peer_revenue_value_gap"
            ),
            "price_earnings_forward_fy": _field_signal(
                row,
                profiles,
                "price_earnings_forward_fy",
                invert=True,
                positive_only=True,
            ),
            "graham_value_gap": _derived_signal(
                derived_row, profiles, "graham_value_gap"
            ),
            "tangible_book_value_gap": _derived_signal(
                derived_row, profiles, "tangible_book_value_gap"
            ),
            "cash_per_share_value_gap": _derived_signal(
                derived_row, profiles, "cash_per_share_value_gap"
            ),
            "free_cash_flow_yield": _derived_signal(
                derived_row, profiles, "free_cash_flow_yield"
            ),
            "operating_cash_flow_yield": _derived_signal(
                derived_row, profiles, "operating_cash_flow_yield"
            ),
            "free_cash_flow_per_share_yield": _derived_signal(
                derived_row, profiles, "free_cash_flow_per_share_yield"
            ),
            "operating_cash_flow_per_share_yield": _derived_signal(
                derived_row, profiles, "operating_cash_flow_per_share_yield"
            ),
            "ebit_yield_ev": _derived_signal(derived_row, profiles, "ebit_yield_ev"),
            "gross_profit_yield_ev": _derived_signal(
                derived_row, profiles, "gross_profit_yield_ev"
            ),
            "shareholder_yield": _derived_signal(
                derived_row, profiles, "shareholder_yield"
            ),
            "net_cash_to_market_cap": _derived_signal(
                derived_row, profiles, "net_cash_to_market_cap"
            ),
            "ncavps_ratio_current": _field_signal(
                row, profiles, "ncavps_ratio_current"
            ),
            "ncavps_ratio_fq": _field_signal(row, profiles, "ncavps_ratio_fq"),
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
            "total_debt_to_ebitda_fq": _field_signal(
                row,
                profiles,
                "total_debt_to_ebitda_fq",
                invert=True,
                positive_only=True,
            ),
            "beta_adjusted_atrp": _derived_signal(
                derived_row,
                profiles,
                "beta_adjusted_atrp",
                invert=True,
                positive_only=True,
            ),
            "price_target_dispersion": _derived_signal(
                derived_row,
                profiles,
                "price_target_dispersion",
                invert=True,
                positive_only=True,
            ),
            "interst_cover_ttm": _field_signal(row, profiles, "interst_cover_ttm"),
            "ebitda_interst_cover_ttm": _field_signal(
                row, profiles, "ebitda_interst_cover_ttm"
            ),
            "cash_dividend_coverage_ratio_ttm": _field_signal(
                row, profiles, "cash_dividend_coverage_ratio_ttm"
            ),
            "cash_n_short_term_invest_to_total_debt_fq": _field_signal(
                row, profiles, "cash_n_short_term_invest_to_total_debt_fq"
            ),
            "cash_n_short_term_invest_to_total_debt_fy": _field_signal(
                row, profiles, "cash_n_short_term_invest_to_total_debt_fy"
            ),
            "cash_n_short_term_invest_to_total_current_liabilities_fq": _field_signal(
                row,
                profiles,
                "cash_n_short_term_invest_to_total_current_liabilities_fq",
            ),
            "cash_n_short_term_invest_to_total_current_liabilities_fy": _field_signal(
                row,
                profiles,
                "cash_n_short_term_invest_to_total_current_liabilities_fy",
            ),
            "total_debt_to_ebitda_fy": _field_signal(
                row,
                profiles,
                "total_debt_to_ebitda_fy",
                invert=True,
                positive_only=True,
            ),
            "net_debt_to_ebitda_fq": _field_signal(
                row,
                profiles,
                "net_debt_to_ebitda_fq",
                invert=True,
                positive_only=True,
            ),
            "net_debt_to_ebitda_fy": _field_signal(
                row,
                profiles,
                "net_debt_to_ebitda_fy",
                invert=True,
                positive_only=True,
            ),
            "dividend_payout_ratio_ttm": _field_signal(
                row,
                profiles,
                "dividend_payout_ratio_ttm",
                invert=True,
                positive_only=True,
            ),
            "zmijewski_score_ttm": _field_signal(
                row, profiles, "zmijewski_score_ttm", invert=True
            ),
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


def _risk_tier(component_scores: dict[str, float | None]) -> str:
    safety_score = component_scores.get("safety")
    if safety_score is None:
        return "risk-unknown"
    if safety_score >= 0.70:
        return "risk-contained"
    if safety_score >= 0.10:
        return "risk-watch"
    if safety_score >= -0.60:
        return "risk-elevated"
    return "high-risk"


def _risk_adjusted_score(
    score: float | None, component_scores: dict[str, float | None]
) -> float | None:
    if score is None:
        return None

    safety = component_scores.get("safety") or 0.0
    quality = component_scores.get("quality") or 0.0
    valuation = component_scores.get("valuation") or 0.0

    if score >= 0:
        penalty = (
            max(0.0, -safety) * 0.35
            + max(0.0, -quality) * 0.16
            + max(0.0, -valuation) * 0.10
        )
        bonus = max(0.0, safety) * 0.08 + max(0.0, quality) * 0.05
        return _clamp(score * (1.0 + bonus) / (1.0 + penalty), -3.0, 3.0)

    short_bonus = (
        max(0.0, -safety) * 0.16
        + max(0.0, -quality) * 0.10
        + max(0.0, -valuation) * 0.08
    )
    short_penalty = max(0.0, safety) * 0.18 + max(0.0, quality) * 0.10
    return _clamp(score * (1.0 + short_bonus) / (1.0 + short_penalty), -3.0, 3.0)


def _manager_action_signal(
    horizons: dict[str, dict[str, Any]],
    component_scores: dict[str, float | None],
) -> str:
    days = horizons.get("days", {}).get("score")
    weeks = horizons.get("weeks", {}).get("score")
    months = horizons.get("months", {}).get("score")
    years = horizons.get("years", {}).get("score")

    attention = component_scores.get("attention") or 0.0
    momentum = component_scores.get("momentum") or 0.0
    trend = component_scores.get("trend") or 0.0
    quality = component_scores.get("quality") or 0.0
    valuation = component_scores.get("valuation") or 0.0
    safety = component_scores.get("safety") or 0.0

    if valuation >= 0.55 and (quality <= -0.35 or safety <= -0.55):
        return "avoid_value_trap"
    if (
        weeks is not None
        and days is not None
        and weeks >= 0.75
        and days >= 0.35
        and momentum >= 0.45
        and trend >= 0.35
        and safety >= -0.35
    ):
        return "add_long_breakout"
    if (
        months is not None
        and months >= 0.55
        and valuation >= 0.45
        and quality >= 0.15
        and (momentum >= 0.10 or trend >= 0.20)
        and safety >= -0.50
    ):
        return "accumulate_value_catalyst"
    if (
        months is not None
        and days is not None
        and months >= 0.35
        and days <= 0.25
        and valuation >= 0.65
        and quality >= 0.00
        and safety >= -0.35
    ):
        return "watch_value_reversal"
    if (
        weeks is not None
        and months is not None
        and weeks <= -0.55
        and months <= -0.35
        and safety <= -0.30
        and (momentum <= -0.30 or trend <= -0.30)
    ):
        return "hedge_or_short"
    if (
        days is not None
        and months is not None
        and days <= -0.35
        and months >= 0.35
        and valuation >= 0.35
        and quality >= 0.05
    ):
        return "mean_reversion_watch"
    if (
        years is not None
        and years >= 0.45
        and quality >= 0.60
        and safety >= 0.10
        and attention <= 0.40
    ):
        return "hold_quality_long"
    return "neutral_watch"


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
            "risk_adjusted_score": None,
            "risk_tier": _risk_tier(component_scores),
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
        "risk_adjusted_score": _risk_adjusted_score(final_score, component_scores),
        "risk_tier": _risk_tier(component_scores),
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
                "manager_action_signal": _manager_action_signal(
                    horizons, component_scores
                ),
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
            "manager_action_signal",
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
            "price_target_upside_average",
            "price_target_upside_median",
            "price_target_downside_floor",
            "price_target_dispersion",
            "book_value_discount",
            "peer_revenue_value_gap",
            "beta_adjusted_atrp",
            "graham_value_gap",
            "tangible_book_value_gap",
            "cash_per_share_value_gap",
            "free_cash_flow_yield",
            "operating_cash_flow_yield",
            "free_cash_flow_per_share_yield",
            "operating_cash_flow_per_share_yield",
            "ebit_yield_ev",
            "gross_profit_yield_ev",
            "shareholder_yield",
            "net_cash_to_market_cap",
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
                f"{horizon_name}_risk_adjusted_score",
                f"{horizon_name}_risk_tier",
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
                str(prediction.get("manager_action_signal", "")),
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
                str(derived.get("price_target_upside_average", "")),
                str(derived.get("price_target_upside_median", "")),
                str(derived.get("price_target_downside_floor", "")),
                str(derived.get("price_target_dispersion", "")),
                str(derived.get("book_value_discount", "")),
                str(derived.get("peer_revenue_value_gap", "")),
                str(derived.get("beta_adjusted_atrp", "")),
                str(derived.get("graham_value_gap", "")),
                str(derived.get("tangible_book_value_gap", "")),
                str(derived.get("cash_per_share_value_gap", "")),
                str(derived.get("free_cash_flow_yield", "")),
                str(derived.get("operating_cash_flow_yield", "")),
                str(derived.get("free_cash_flow_per_share_yield", "")),
                str(derived.get("operating_cash_flow_per_share_yield", "")),
                str(derived.get("ebit_yield_ev", "")),
                str(derived.get("gross_profit_yield_ev", "")),
                str(derived.get("shareholder_yield", "")),
                str(derived.get("net_cash_to_market_cap", "")),
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
                    str(horizon.get("risk_adjusted_score", "")),
                    str(horizon.get("risk_tier", "")),
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
            f" {'RAdj':>8} {'Risk':<14} {'Action':<24}"
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
                f"{_format_score(components['safety']):>8} {_format_score(components.get('scale')):>8} {_format_multiple(prediction['derived'].get('short_term_cash_coverage')):>8} {str(horizon['setup'])[:28]:<28} "
                f"{_format_score(horizon.get('risk_adjusted_score')):>8} {str(horizon.get('risk_tier'))[:14]:<14} {str(prediction.get('manager_action_signal'))[:24]:<24}"
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
                f"{_format_score(components['safety']):>8} {_format_score(components.get('scale')):>8} {_format_multiple(prediction['derived'].get('short_term_cash_coverage')):>8} {str(horizon['setup'])[:28]:<28} "
                f"{_format_score(horizon.get('risk_adjusted_score')):>8} {str(horizon.get('risk_tier'))[:14]:<14} {str(prediction.get('manager_action_signal'))[:24]:<24}"
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


DEFAULT_MOVE_PREDICTION_PROFILE_SUITE = [
    "breakout_long",
    "quality_value_compounder",
    "durable_value_compounder",
    "value_recovery",
    "fragility_short",
    "asymmetric_value",
    "deep_value_momentum",
    "early_momentum_inflection",
    "forward_edge_active",
    "sector_relative_outperformer",
]


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
        profile_names = list(DEFAULT_MOVE_PREDICTION_PROFILE_SUITE)

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


# ──────────────────────────────────────────────────────────────────────────────
# CONSENSUS AGGREGATOR — cross-profile ranking
# ──────────────────────────────────────────────────────────────────────────────

CONSENSUS_PROFILE_WEIGHTS: dict[str, float] = {
    # Empirically strongest predictor for 1-month performance — highest weight
    "breakout_long": 0.20,
    # Second-best for catching nascent moves early
    "early_momentum_inflection": 0.12,
    # Forward earnings quality — solid but slower horizon
    "quality_value_compounder": 0.10,
    # Quality-led value investing lens for months/years positioning
    "durable_value_compounder": 0.09,
    # Sector-relative setups — reduced; market-relative is less nimble
    "sector_relative_outperformer": 0.09,
    # Forward estimate revisions — strong complementary signal
    "forward_edge_active": 0.11,
    # Overlooked fundamentals (redesigned to filter ETFs)
    "asymmetric_value": 0.09,
    # Active fundamental recovery — requires actual turn confirmation
    "value_recovery": 0.08,
    # New: value + catalyst intersection profile
    "deep_value_momentum": 0.05,
    # Short/fragility hedge — inverted in consensus (score = -score)
    "fragility_short": 0.07,
}

MINIMUM_COVERAGE_FOR_CONSENSUS = 0.40


def _build_consensus_scores(
    scan_data: list[dict[str, Any]],
    profile_names: list[str] | None = None,
    min_coverage: float = MINIMUM_COVERAGE_FOR_CONSENSUS,
) -> list[dict[str, Any]]:
    if profile_names is None:
        profile_names = list(CONSENSUS_PROFILE_WEIGHTS.keys())

    profiles_data: dict[str, list[dict[str, Any]]] = {}
    shared_profiles: dict[str, dict[str, float | int | None]] | None = None
    shared_derived: list[dict[str, float | None]] | None = None

    for profile_name in profile_names:
        resolved_profile = resolve_move_prediction_scoring_profile(profile_name)
        if shared_profiles is None:
            _enrich_with_peer_metrics(scan_data)
            shared_derived = [_build_derived_metrics(row) for row in scan_data]
            shared_profiles = _build_metric_profiles(scan_data, shared_derived)
        prediction_rows = _build_prediction_rows(
            scan_data,
            shared_profiles,
            shared_derived,
            resolved_profile,
        )
        profiles_data[profile_name] = prediction_rows

    horizon_names = list(DEFAULT_HORIZON_WEIGHTS.keys())
    consensus_rows: list[dict[str, Any]] = []

    for row_idx, row in enumerate(scan_data):
        ticker = _get_symbol_name(row)
        company = _get_company_name(row)
        market_cap = _coerce_numeric(row.get("market_cap_basic"))

        profile_scores: dict[str, dict[str, dict[str, Any]]] = {}
        profile_components: dict[str, dict[str, float | None]] = {}

        for profile_name in profile_names:
            prediction = profiles_data[profile_name][row_idx]
            profile_scores[profile_name] = prediction["horizons"]
            profile_components[profile_name] = prediction["components"]

        consensus_horizons: dict[str, dict[str, Any]] = {}
        for horizon_name in horizon_names:
            weighted_score = 0.0
            weight_used = 0.0
            scores_collected: list[float] = []
            agreements_positive = 0
            agreements_negative = 0
            total_opinions = 0

            for profile_name in profile_names:
                horizon_data = profile_scores[profile_name].get(horizon_name, {})
                score = horizon_data.get("score")
                coverage = horizon_data.get("coverage", 0.0)

                if score is None or (coverage or 0) < min_coverage:
                    continue

                profile_weight = CONSENSUS_PROFILE_WEIGHTS.get(profile_name, 0.10)

                if profile_name == "fragility_short":
                    score = -score

                weighted_score += score * profile_weight
                weight_used += profile_weight
                scores_collected.append(score)
                total_opinions += 1
                if score >= DIRECTIONAL_MOVE_SCORE_THRESHOLD:
                    agreements_positive += 1
                elif score <= -DIRECTIONAL_MOVE_SCORE_THRESHOLD:
                    agreements_negative += 1

            if weight_used == 0 or total_opinions < 3:
                consensus_horizons[horizon_name] = {
                    "score": None,
                    "direction": "N/A",
                    "confidence": None,
                    "agreement_ratio": 0.0,
                    "opinions": total_opinions,
                }
                continue

            final_score = _clamp(weighted_score / weight_used, -3.0, 3.0)

            score_variance = (
                sum((s - final_score) ** 2 for s in scores_collected)
                / len(scores_collected)
                if scores_collected
                else 0.0
            )
            consistency_bonus = max(0.0, 1.0 - score_variance) * 10.0

            max_agreement = max(agreements_positive, agreements_negative)
            agreement_ratio = max_agreement / total_opinions if total_opinions else 0.0

            confidence = _clamp(
                25.0
                + abs(final_score) * 15.0
                + agreement_ratio * 30.0
                + consistency_bonus
                + (total_opinions / len(profile_names)) * 15.0,
                5.0,
                99.0,
            )

            consensus_horizons[horizon_name] = {
                "score": final_score,
                "direction": _direction_label(final_score),
                "confidence": confidence,
                "agreement_ratio": agreement_ratio,
                "opinions": total_opinions,
            }

        consensus_components: dict[str, float | None] = {}
        for component_name in COMPONENT_ORDER:
            component_values: list[float] = []
            for profile_name in profile_names:
                val = profile_components[profile_name].get(component_name)
                if val is not None:
                    component_values.append(val)
            consensus_components[component_name] = (
                sum(component_values) / len(component_values)
                if component_values
                else None
            )

        for horizon_data in consensus_horizons.values():
            horizon_data["risk_adjusted_score"] = _risk_adjusted_score(
                horizon_data.get("score"), consensus_components
            )
            horizon_data["risk_tier"] = _risk_tier(consensus_components)

        manager_action_signal = _manager_action_signal(
            consensus_horizons, consensus_components
        )

        consensus_rows.append(
            {
                "row": row,
                "ticker": ticker,
                "company": company,
                "market_cap": market_cap,
                "horizons": consensus_horizons,
                "components": consensus_components,
                "profile_scores": profile_scores,
                "manager_action_signal": manager_action_signal,
            }
        )

    return consensus_rows


def _log_consensus_aggregator_report(
    log_file: Path,
    consensus_rows: list[dict[str, Any]],
    scan_data_count: int,
    industries: list[str] | None,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
    profile_names: list[str],
) -> None:
    _reset_log_file(log_file)
    log_to_file(
        log_file,
        _build_report_title("TradingView consensus aggregator — cross-profile ranking"),
    )
    log_to_file(log_file, "=" * 160)
    log_to_file(
        log_file,
        (
            f"Rows: {scan_data_count} | industries={industries or 'all'} | "
            f"min_market_cap={min_market_cap_usd} | max_market_cap={max_market_cap_usd}"
        ),
    )
    log_to_file(
        log_file,
        f"Profiles aggregated: {', '.join(profile_names)} | "
        f"Profile weights: {', '.join(f'{k}={v:.2f}' for k, v in CONSENSUS_PROFILE_WEIGHTS.items() if k in profile_names)}",
    )
    log_to_file(log_file, "")
    log_to_file(log_file, "Methodology")
    log_to_file(log_file, "-" * 160)
    log_to_file(
        log_file,
        "Each profile scores every name independently. The consensus score is a weighted average across all profiles per horizon. "
        "Fragility_short scores are sign-inverted before aggregation (a bearish fragility call becomes a negative consensus contribution). "
        "Agreement ratio measures what fraction of profiles with directional opinions agree on direction. "
        "Score variance penalizes inconsistent cross-profile readings — names where profiles disagree heavily get lower confidence.",
    )
    log_to_file(
        log_file,
        f"Minimum coverage threshold: {MINIMUM_COVERAGE_FOR_CONSENSUS:.0%} — profiles with insufficient component data for a name are excluded from that name's consensus.",
    )
    log_to_file(log_file, "")

    horizon_names = list(DEFAULT_HORIZON_WEIGHTS.keys())

    for horizon_name in horizon_names:
        scored_rows = [
            r
            for r in consensus_rows
            if r["horizons"].get(horizon_name, {}).get("score") is not None
        ]
        if not scored_rows:
            continue

        scored_rows_sorted = sorted(
            scored_rows,
            key=lambda r: r["horizons"][horizon_name]["score"] or 0,
            reverse=True,
        )

        horizon_title = HORIZON_TITLES.get(horizon_name, horizon_name.title())
        log_to_file(log_file, f"CONSENSUS — {horizon_title}")
        log_to_file(log_file, "=" * 160)
        log_to_file(
            log_file,
            f"{'Rank':<6} {'Ticker':<12} {'Company':<28} {'Score':>8} {'Dir':<12} "
            f"{'RAdj':>8} {'Risk':<14} {'Action':<24} {'Conf':>6} {'Agree':>7} {'Opinions':>9} "
            f"{'MCap':>14} {'Perf.W':>8} {'Perf.1M':>8} {'Perf.YTD':>8}",
        )
        log_to_file(log_file, "-" * 160)

        top_count = min(TOP_SECTION_ROWS, len(scored_rows_sorted))
        for rank, consensus_row in enumerate(scored_rows_sorted[:top_count], 1):
            horizon_data = consensus_row["horizons"][horizon_name]
            row = consensus_row["row"]
            log_to_file(
                log_file,
                f"{rank:<6} {consensus_row['ticker']:<12} "
                f"{(consensus_row['company'] or 'N/A')[:27]:<28} "
                f"{_format_score(horizon_data['score']):>8} "
                f"{horizon_data['direction']:<12} "
                f"{_format_score(horizon_data.get('risk_adjusted_score')):>8} "
                f"{str(horizon_data.get('risk_tier'))[:14]:<14} "
                f"{str(consensus_row.get('manager_action_signal'))[:24]:<24} "
                f"{_format_confidence(horizon_data['confidence']):>6} "
                f"{horizon_data['agreement_ratio']:.0%}{'':<3} "
                f"{horizon_data['opinions']:>9} "
                f"{_format_market_cap(consensus_row['market_cap']):>14} "
                f"{_format_percent(_coerce_numeric(row.get('Perf.W'))):>8} "
                f"{_format_percent(_coerce_numeric(row.get('Perf.1M'))):>8} "
                f"{_format_percent(_coerce_numeric(row.get('Perf.YTD'))):>8}",
            )
        log_to_file(log_file, "")

        log_to_file(log_file, f"CONSENSUS — {horizon_title} — Bottom {top_count}")
        log_to_file(log_file, "-" * 160)
        log_to_file(
            log_file,
            f"{'Rank':<6} {'Ticker':<12} {'Company':<28} {'Score':>8} {'Dir':<12} "
            f"{'RAdj':>8} {'Risk':<14} {'Action':<24} {'Conf':>6} {'Agree':>7} {'Opinions':>9} "
            f"{'MCap':>14} {'Perf.W':>8} {'Perf.1M':>8} {'Perf.YTD':>8}",
        )
        log_to_file(log_file, "-" * 160)
        bottom_rows = scored_rows_sorted[-top_count:]
        for rank, consensus_row in enumerate(reversed(bottom_rows), 1):
            horizon_data = consensus_row["horizons"][horizon_name]
            row = consensus_row["row"]
            log_to_file(
                log_file,
                f"{rank:<6} {consensus_row['ticker']:<12} "
                f"{(consensus_row['company'] or 'N/A')[:27]:<28} "
                f"{_format_score(horizon_data['score']):>8} "
                f"{horizon_data['direction']:<12} "
                f"{_format_score(horizon_data.get('risk_adjusted_score')):>8} "
                f"{str(horizon_data.get('risk_tier'))[:14]:<14} "
                f"{str(consensus_row.get('manager_action_signal'))[:24]:<24} "
                f"{_format_confidence(horizon_data['confidence']):>6} "
                f"{horizon_data['agreement_ratio']:.0%}{'':<3} "
                f"{horizon_data['opinions']:>9} "
                f"{_format_market_cap(consensus_row['market_cap']):>14} "
                f"{_format_percent(_coerce_numeric(row.get('Perf.W'))):>8} "
                f"{_format_percent(_coerce_numeric(row.get('Perf.1M'))):>8} "
                f"{_format_percent(_coerce_numeric(row.get('Perf.YTD'))):>8}",
            )
        log_to_file(log_file, "")

    log_to_file(log_file, "CROSS-PROFILE AGREEMENT — High conviction names")
    log_to_file(log_file, "=" * 160)
    log_to_file(
        log_file,
        "Names where 5+ profiles agree on direction with agreement ratio >= 60%:",
    )
    log_to_file(log_file, "-" * 160)

    for horizon_name in horizon_names:
        high_conviction = [
            r
            for r in consensus_rows
            if (r["horizons"].get(horizon_name, {}).get("agreement_ratio") or 0) >= 0.60
            and (r["horizons"].get(horizon_name, {}).get("opinions") or 0) >= 5
            and r["horizons"].get(horizon_name, {}).get("score") is not None
        ]
        if not high_conviction:
            continue

        high_conviction_sorted = sorted(
            high_conviction,
            key=lambda r: abs(r["horizons"][horizon_name]["score"] or 0),
            reverse=True,
        )
        horizon_title = HORIZON_TITLES.get(horizon_name, horizon_name.title())
        log_to_file(
            log_file,
            f"  {horizon_title} — {len(high_conviction_sorted)} high-conviction names",
        )
        for consensus_row in high_conviction_sorted[:15]:
            h = consensus_row["horizons"][horizon_name]
            log_to_file(
                log_file,
                f"    {consensus_row['ticker']:<12} "
                f"{(consensus_row['company'] or '')[:25]:<26} "
                f"score={_format_score(h['score'])} "
                f"dir={h['direction']:<12} "
                f"agree={h['agreement_ratio']:.0%} "
                f"opinions={h['opinions']}",
            )
        log_to_file(log_file, "")

    log_to_file(log_file, "ACTIVE MANAGER SHORTLIST — action signals")
    log_to_file(log_file, "=" * 160)
    action_labels = [
        ("add_long_breakout", "Momentum longs with risk filter"),
        ("accumulate_value_catalyst", "Value plus catalyst longs"),
        ("watch_value_reversal", "Cheap names waiting for reversal"),
        ("hold_quality_long", "Lower-drama quality longs"),
        ("hedge_or_short", "Short / hedge candidates"),
        ("avoid_value_trap", "Cheap but structurally risky"),
    ]

    for action_value, title in action_labels:
        action_rows = [
            row
            for row in consensus_rows
            if row.get("manager_action_signal") == action_value
        ]
        if not action_rows:
            continue

        action_rows.sort(
            key=lambda row: abs(
                row["horizons"].get("weeks", {}).get("risk_adjusted_score") or 0.0
            ),
            reverse=True,
        )
        log_to_file(log_file, title)
        log_to_file(log_file, "-" * 160)
        log_to_file(
            log_file,
            f"  {'Ticker':<12} {'Company':<28} {'Industry':<26} {'Weeks':>8} {'RAdj':>8} {'Risk':<14} {'Agree':>7} {'Qual':>8} {'Value':>8} {'Safe':>8}",
        )
        for consensus_row in action_rows[:20]:
            h = consensus_row["horizons"].get("weeks", {})
            components = consensus_row["components"]
            row = consensus_row["row"]
            log_to_file(
                log_file,
                f"  {consensus_row['ticker']:<12} "
                f"{(consensus_row['company'] or '')[:27]:<28} "
                f"{str(row.get('industry') or '')[:25]:<26} "
                f"{_format_score(h.get('score')):>8} "
                f"{_format_score(h.get('risk_adjusted_score')):>8} "
                f"{str(h.get('risk_tier'))[:14]:<14} "
                f"{(h.get('agreement_ratio') or 0):>6.0%} "
                f"{_format_score(components.get('quality')):>8} "
                f"{_format_score(components.get('valuation')):>8} "
                f"{_format_score(components.get('safety')):>8}",
            )
        log_to_file(log_file, "")


def run_consensus_aggregator(
    scan_data: list[dict[str, Any]],
    profile_names: list[str] | None = None,
    industries: list[str] | str | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    output_dir: str | Path | None = None,
) -> Path:
    industries = _normalize_industries(industries)
    if profile_names is None:
        profile_names = list(CONSENSUS_PROFILE_WEIGHTS.keys())

    resolved_output_dir = Path(output_dir) if output_dir is not None else None
    log_file = _build_report_file_name(
        report_slug="tradingview_consensus_aggregator",
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        scoring_profile_name="consensus",
        output_dir=resolved_output_dir,
    )

    consensus_rows = _build_consensus_scores(
        scan_data=scan_data,
        profile_names=profile_names,
    )

    _log_consensus_aggregator_report(
        log_file=log_file,
        consensus_rows=consensus_rows,
        scan_data_count=len(scan_data),
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        profile_names=profile_names,
    )

    csv_file = log_file.with_suffix(".csv")
    horizon_names = list(DEFAULT_HORIZON_WEIGHTS.keys())
    csv_headers = [
        "ticker",
        "company",
        "market_cap",
        "manager_action_signal",
    ]
    for horizon_name in horizon_names:
        csv_headers.extend(
            [
                f"consensus_{horizon_name}_score",
                f"consensus_{horizon_name}_direction",
                f"consensus_{horizon_name}_confidence",
                f"consensus_{horizon_name}_agreement",
                f"consensus_{horizon_name}_opinions",
                f"consensus_{horizon_name}_risk_adjusted_score",
                f"consensus_{horizon_name}_risk_tier",
            ]
        )
    for profile_name in profile_names:
        for horizon_name in horizon_names:
            csv_headers.append(f"{profile_name}_{horizon_name}_score")

    csv_rows: list[list[str]] = []
    for consensus_row in consensus_rows:
        csv_row = [
            consensus_row["ticker"],
            consensus_row["company"] or "",
            str(consensus_row["market_cap"] or ""),
            str(consensus_row.get("manager_action_signal", "")),
        ]
        for horizon_name in horizon_names:
            h = consensus_row["horizons"].get(horizon_name, {})
            csv_row.extend(
                [
                    str(h.get("score", "")),
                    str(h.get("direction", "")),
                    str(h.get("confidence", "")),
                    str(h.get("agreement_ratio", "")),
                    str(h.get("opinions", "")),
                    str(h.get("risk_adjusted_score", "")),
                    str(h.get("risk_tier", "")),
                ]
            )
        for profile_name in profile_names:
            for horizon_name in horizon_names:
                profile_horizon = (
                    consensus_row["profile_scores"]
                    .get(profile_name, {})
                    .get(horizon_name, {})
                )
                csv_row.append(str(profile_horizon.get("score", "")))
        csv_rows.append(csv_row)

    log_rows_to_csv(csv_file, csv_headers, csv_rows)

    return log_file


def run_full_analysis_suite(
    scan_data: list[dict[str, Any]],
    profile_names: list[str] | None = None,
    industries: list[str] | str | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    include_blind_spot_sections: bool = False,
    output_dir: str | Path | None = None,
) -> dict[str, Path]:
    profile_logs = run_move_prediction_profile_suite(
        scan_data=scan_data,
        profile_names=profile_names,
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        include_blind_spot_sections=include_blind_spot_sections,
        output_dir=output_dir,
    )

    consensus_log = run_consensus_aggregator(
        scan_data=scan_data,
        profile_names=profile_names,
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        output_dir=output_dir,
    )

    result = dict(profile_logs)
    result["_consensus_aggregator"] = consensus_log
    return result


# ──────────────────────────────────────────────────────────────────────────────
# EARNINGS PRIORITY AGGREGATOR — chronological upcoming-earnings ranking
# ──────────────────────────────────────────────────────────────────────────────
#
# This block layers an additional report on top of the standard consensus
# aggregator output. It re-uses the per-profile scoring already computed by
# ``_build_consensus_scores`` and re-orders the universe by *upcoming earnings
# date* (``earnings_release_next_date``) instead of by score magnitude.
#
# Goal: surface names whose next earnings release is imminent, so the
# move-prediction signals can be reviewed in catalyst-time order — earnings
# are typically a high-volatility event and a strong buy/sell decision
# trigger. Each row keeps its full per-profile coverage and consensus scores
# so the user can see *both* "when is the catalyst" and "what does each
# profile think about the name".

EARNINGS_PRIORITY_BUCKETS: list[tuple[str, int | None]] = [
    ("This week (≤7 days)", 7),
    ("Next 8–14 days", 14),
    ("Next 15–30 days", 30),
    ("Next 31–60 days", 60),
    ("Next 61–90 days", 90),
    ("Beyond 90 days", None),
]

EARNINGS_PRIORITY_SORT_FLAVOURS: dict[str, str] = {
    "flavour_marketcap": "Within each exact earnings date, rows are sorted by market cap descending.",
    "flavour_score": "Within each exact earnings date, rows are sorted by weekly score descending.",
}

EARNINGS_PRIORITY_SCORE_SORT_HORIZON = "weeks"


def _parse_earnings_timestamp(value: Any) -> datetime | None:
    """Best-effort parser for TradingView earnings date fields.

    TradingView typically returns Unix timestamps (seconds) for ``time`` typed
    fields, but occasionally returns ISO-8601 strings or millisecond
    timestamps. Any unparseable / falsy value returns ``None``.
    """
    if value is None or value == "":
        return None
    # Numeric epoch (seconds or milliseconds)
    if isinstance(value, (int, float)):
        epoch = float(value)
        # Treat very large values as milliseconds.
        if epoch > 1e12:
            epoch = epoch / 1000.0
        try:
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    # String: try ISO-8601, then numeric epoch fallback.
    text = str(value).strip()
    if not text:
        return None
    try:
        # ``fromisoformat`` accepts e.g. "2026-05-01" and "2026-05-01T20:00:00".
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return _parse_earnings_timestamp(float(text))
    except ValueError:
        return None


def _format_earnings_date(parsed_dt: datetime | None) -> str:
    if parsed_dt is None:
        return "N/A"
    return parsed_dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _days_until(parsed_dt: datetime | None, reference: datetime) -> float | None:
    if parsed_dt is None:
        return None
    delta = parsed_dt - reference
    return delta.total_seconds() / 86400.0


def _build_earnings_priority_entries(
    consensus_rows: list[dict[str, Any]],
    reference_time: datetime,
    include_past_window_days: float = 1.0,
) -> list[dict[str, Any]]:
    """Attach parsed earnings metadata to each consensus row and keep only
    rows with an upcoming earnings date.

    A small ``include_past_window_days`` grace window keeps names whose
    earnings released within the last day, so post-earnings drift signals
    are not lost the morning after a release.
    """
    entries: list[dict[str, Any]] = []
    for consensus_row in consensus_rows:
        row = consensus_row["row"]
        next_dt = _parse_earnings_timestamp(row.get("earnings_release_next_date"))
        days_until = _days_until(next_dt, reference_time)
        if days_until is None:
            continue
        if days_until < -include_past_window_days:
            continue

        last_dt = _parse_earnings_timestamp(row.get("earnings_release_date"))
        next_calendar = _parse_earnings_timestamp(
            row.get("earnings_release_next_calendar_date")
        )
        next_time_raw = row.get("earnings_release_next_time")

        # Profile coverage: how many profiles produced a usable score
        # (non-None) on each horizon for this name.
        profile_scores: dict[str, dict[str, dict[str, Any]]] = consensus_row.get(
            "profile_scores", {}
        )
        coverage_per_horizon: dict[str, int] = {}
        for horizon_name in DEFAULT_HORIZON_WEIGHTS:
            covered = 0
            for profile_name, horizon_map in profile_scores.items():
                horizon_data = horizon_map.get(horizon_name) or {}
                if horizon_data.get("score") is not None:
                    covered += 1
            coverage_per_horizon[horizon_name] = covered

        entries.append(
            {
                "consensus_row": consensus_row,
                "next_earnings_dt": next_dt,
                "next_earnings_calendar_dt": next_calendar,
                "next_earnings_time_raw": next_time_raw,
                "last_earnings_dt": last_dt,
                "days_until": days_until,
                "coverage_per_horizon": coverage_per_horizon,
                "total_profiles": len(profile_scores) or 1,
            }
        )

    entries.sort(key=lambda entry: entry["days_until"])
    return entries


def _bucket_for_days(days_until: float) -> str:
    days_clamped = max(0.0, days_until)
    for label, upper in EARNINGS_PRIORITY_BUCKETS:
        if upper is None or days_clamped <= upper:
            return label
    return EARNINGS_PRIORITY_BUCKETS[-1][0]


def _earnings_priority_flavour_title_suffix(sort_flavour: str | None) -> str:
    if sort_flavour is None:
        return ""
    if sort_flavour == "flavour_marketcap":
        return " - grouped by earnings date, market cap within date"
    if sort_flavour == "flavour_score":
        return " - grouped by earnings date, weekly score within date"
    raise ValueError(f"Unsupported earnings-priority sort flavour: {sort_flavour}")


def _earnings_priority_flavour_note(
    sort_flavour: str | None,
    profile_name: str | None = None,
) -> str | None:
    if sort_flavour is None:
        return None
    if sort_flavour == "flavour_marketcap":
        return EARNINGS_PRIORITY_SORT_FLAVOURS[sort_flavour]
    if sort_flavour == "flavour_score":
        score_subject = "weekly consensus score"
        if profile_name is not None:
            score_subject = f"{profile_name} weekly score"
        return (
            "Within each exact earnings date, rows are sorted by "
            f"{score_subject} descending."
        )
    raise ValueError(f"Unsupported earnings-priority sort flavour: {sort_flavour}")


def _get_earnings_priority_weekly_score(
    entry: dict[str, Any],
    profile_name: str | None = None,
) -> float | None:
    if profile_name is None:
        return _coerce_numeric(
            (entry["consensus_row"].get("horizons") or {})
            .get(EARNINGS_PRIORITY_SCORE_SORT_HORIZON, {})
            .get("score")
        )

    return _coerce_numeric(
        (entry["consensus_row"].get("profile_scores") or {})
        .get(profile_name, {})
        .get(EARNINGS_PRIORITY_SCORE_SORT_HORIZON, {})
        .get("score")
    )


def _descending_numeric_sort_key(value: float | None) -> tuple[int, float]:
    if value is None:
        return (1, 0.0)
    return (0, -value)


def _sort_earnings_priority_date_group(
    date_entries: list[dict[str, Any]],
    sort_flavour: str | None,
    profile_name: str | None = None,
) -> list[dict[str, Any]]:
    if sort_flavour is None:
        return list(date_entries)

    def _market_cap(entry: dict[str, Any]) -> float | None:
        return _coerce_numeric(entry["consensus_row"].get("market_cap"))

    def _market_cap_key(entry: dict[str, Any]) -> tuple[int, float, int, float, str]:
        return (
            *_descending_numeric_sort_key(_market_cap(entry)),
            *_descending_numeric_sort_key(
                _get_earnings_priority_weekly_score(entry, profile_name)
            ),
            str(entry["consensus_row"].get("ticker") or ""),
        )

    def _score_key(entry: dict[str, Any]) -> tuple[int, float, int, float, str]:
        return (
            *_descending_numeric_sort_key(
                _get_earnings_priority_weekly_score(entry, profile_name)
            ),
            *_descending_numeric_sort_key(_market_cap(entry)),
            str(entry["consensus_row"].get("ticker") or ""),
        )

    if sort_flavour == "flavour_marketcap":
        return sorted(date_entries, key=_market_cap_key)
    if sort_flavour == "flavour_score":
        return sorted(date_entries, key=_score_key)
    raise ValueError(f"Unsupported earnings-priority sort flavour: {sort_flavour}")


def _group_earnings_priority_entries_by_date(
    entries: list[dict[str, Any]],
    sort_flavour: str | None = None,
    profile_name: str | None = None,
) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: collections.OrderedDict[str, list[dict[str, Any]]] = (
        collections.OrderedDict()
    )
    for entry in entries:
        date_label = _format_earnings_date(entry["next_earnings_dt"])
        grouped.setdefault(date_label, []).append(entry)

    return [
        (
            date_label,
            _sort_earnings_priority_date_group(
                date_entries,
                sort_flavour=sort_flavour,
                profile_name=profile_name,
            ),
        )
        for date_label, date_entries in grouped.items()
    ]


def _iter_earnings_priority_bucket_groups(
    entries: list[dict[str, Any]],
    sort_flavour: str | None = None,
    profile_name: str | None = None,
) -> list[tuple[str, list[tuple[str, list[dict[str, Any]]]]]]:
    bucketed: collections.OrderedDict[str, list[dict[str, Any]]] = (
        collections.OrderedDict((label, []) for label, _ in EARNINGS_PRIORITY_BUCKETS)
    )
    for entry in entries:
        bucketed[_bucket_for_days(entry["days_until"])].append(entry)

    grouped_buckets: list[tuple[str, list[tuple[str, list[dict[str, Any]]]]]] = []
    for bucket_label, bucket_entries in bucketed.items():
        if not bucket_entries:
            continue
        grouped_buckets.append(
            (
                bucket_label,
                _group_earnings_priority_entries_by_date(
                    bucket_entries,
                    sort_flavour=sort_flavour,
                    profile_name=profile_name,
                ),
            )
        )
    return grouped_buckets


def _flatten_earnings_priority_entries(
    entries: list[dict[str, Any]],
    sort_flavour: str | None = None,
    profile_name: str | None = None,
) -> list[dict[str, Any]]:
    ordered_entries: list[dict[str, Any]] = []
    for _, date_entries in _group_earnings_priority_entries_by_date(
        entries,
        sort_flavour=sort_flavour,
        profile_name=profile_name,
    ):
        ordered_entries.extend(date_entries)
    return ordered_entries


def _log_earnings_priority_report(
    log_file: Path,
    entries: list[dict[str, Any]],
    scan_data_count: int,
    industries: list[str] | None,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
    profile_names: list[str],
    reference_time: datetime,
    sort_flavour: str | None = None,
) -> None:
    _reset_log_file(log_file)
    log_to_file(
        log_file,
        _build_report_title(
            "TradingView earnings-priority ranking — chronological upcoming catalysts"
            + _earnings_priority_flavour_title_suffix(sort_flavour)
        ),
    )
    log_to_file(log_file, "=" * 200)
    log_to_file(
        log_file,
        (
            f"Universe rows scanned: {scan_data_count} | "
            f"rows with usable upcoming earnings: {len(entries)} | "
            f"reference time (UTC): {reference_time.strftime('%Y-%m-%d %H:%M')} | "
            f"industries={industries or 'all'} | "
            f"min_market_cap={min_market_cap_usd} | max_market_cap={max_market_cap_usd}"
        ),
    )
    log_to_file(
        log_file,
        f"Profiles aggregated: {', '.join(profile_names)}",
    )
    log_to_file(log_file, "")
    log_to_file(log_file, "Methodology")
    log_to_file(log_file, "-" * 200)
    log_to_file(
        log_file,
        "Each row carries the consensus score per horizon (built by the same logic as the consensus aggregator) "
        "plus the count of profiles that produced a usable score on that horizon ('coverage'). "
        "Names are ordered chronologically by ``earnings_release_next_date``. "
        "Use this report to see which signals are about to be tested by an earnings catalyst — strong "
        "consensus + imminent earnings is a higher-conviction tactical setup than the same score with "
        "no near-term catalyst.",
    )
    sort_note = _earnings_priority_flavour_note(sort_flavour)
    if sort_note is not None:
        log_to_file(log_file, sort_note)
    log_to_file(log_file, "")

    if not entries:
        log_to_file(log_file, "(no rows with an upcoming earnings date were found)")
        return

    horizon_names = list(DEFAULT_HORIZON_WEIGHTS.keys())

    # ── Bucketed sections (chronological) ────────────────────────────────
    for bucket_label, date_groups in _iter_earnings_priority_bucket_groups(
        entries,
        sort_flavour=sort_flavour,
    ):
        bucket_size = sum(len(date_entries) for _, date_entries in date_groups)
        log_to_file(log_file, f"BUCKET — {bucket_label} ({bucket_size} names)")
        log_to_file(log_file, "=" * 200)
        log_to_file(
            log_file,
            f"{'Days':>6} {'Earnings':<11} {'Ticker':<12} {'Company':<28} "
            f"{'Sector':<22} {'MCap':>14} "
            f"{'Days/Score':>11} {'Days/Cov':>9} "
            f"{'Wks/Score':>11} {'Wks/Cov':>9} "
            f"{'Mos/Score':>11} {'Mos/Cov':>9} "
            f"{'Yrs/Score':>11} {'Yrs/Cov':>9}",
        )
        log_to_file(log_file, "-" * 200)
        for date_label, date_entries in date_groups:
            if sort_flavour is not None:
                log_to_file(
                    log_file,
                    f"DATE GROUP: {date_label} ({len(date_entries)} names)",
                )
            for entry in date_entries:
                consensus_row = entry["consensus_row"]
                row = consensus_row["row"]
                horizon_cells: list[str] = []
                for horizon_name in horizon_names:
                    horizon_data = consensus_row["horizons"].get(horizon_name, {}) or {}
                    horizon_cells.append(
                        f"{_format_score(horizon_data.get('score')):>11}"
                    )
                    horizon_cells.append(
                        f"{entry['coverage_per_horizon'].get(horizon_name, 0):>9}"
                    )
                log_to_file(
                    log_file,
                    f"{entry['days_until']:>6.1f} "
                    f"{_format_earnings_date(entry['next_earnings_dt']):<11} "
                    f"{consensus_row['ticker']:<12} "
                    f"{(consensus_row['company'] or 'N/A')[:27]:<28} "
                    f"{str(row.get('sector') or 'N/A')[:21]:<22} "
                    f"{_format_market_cap(consensus_row['market_cap']):>14} "
                    + " ".join(horizon_cells),
                )
        log_to_file(log_file, "")

    # ── High-conviction near-term setups (≤30 days + decisive consensus) ─
    log_to_file(log_file, "HIGH-CONVICTION NEAR-TERM EARNINGS SETUPS")
    log_to_file(log_file, "=" * 200)
    log_to_file(
        log_file,
        "Names with an earnings catalyst within 30 days AND |days-horizon consensus| "
        f">= {DIRECTIONAL_MOVE_SCORE_THRESHOLD:.2f} with at least 3 profile opinions:",
    )
    log_to_file(log_file, "-" * 200)

    near_term_strong = [
        entry
        for entry in entries
        if entry["days_until"] <= 30
        and (entry["consensus_row"]["horizons"].get("days") or {}).get("score")
        is not None
        and abs(entry["consensus_row"]["horizons"]["days"].get("score") or 0.0)
        >= DIRECTIONAL_MOVE_SCORE_THRESHOLD
        and (entry["consensus_row"]["horizons"]["days"].get("opinions") or 0) >= 3
    ]
    near_term_strong = _flatten_earnings_priority_entries(
        near_term_strong,
        sort_flavour=sort_flavour,
    )

    if not near_term_strong:
        log_to_file(log_file, "  (no qualifying near-term high-conviction names)")
    else:
        log_to_file(
            log_file,
            f"{'Days':>6} {'Earnings':<11} {'Ticker':<12} {'Company':<28} "
            f"{'DaysScore':>10} {'Dir':<10} {'Conf':>5} {'Agree':>6} {'Opin':>5}",
        )
        log_to_file(log_file, "-" * 200)
        for date_label, date_entries in _group_earnings_priority_entries_by_date(
            near_term_strong,
            sort_flavour=sort_flavour,
        ):
            if sort_flavour is not None:
                log_to_file(
                    log_file,
                    f"DATE GROUP: {date_label} ({len(date_entries)} names)",
                )
            for entry in date_entries:
                consensus_row = entry["consensus_row"]
                horizon_data = consensus_row["horizons"].get("days") or {}
                log_to_file(
                    log_file,
                    f"{entry['days_until']:>6.1f} "
                    f"{_format_earnings_date(entry['next_earnings_dt']):<11} "
                    f"{consensus_row['ticker']:<12} "
                    f"{(consensus_row['company'] or 'N/A')[:27]:<28} "
                    f"{_format_score(horizon_data.get('score')):>10} "
                    f"{str(horizon_data.get('direction') or 'N/A'):<10} "
                    f"{_format_confidence(horizon_data.get('confidence')):>5} "
                    f"{(horizon_data.get('agreement_ratio') or 0.0):>6.0%} "
                    f"{(horizon_data.get('opinions') or 0):>5}",
                )
    log_to_file(log_file, "")


def _log_earnings_priority_report_for_profile(
    log_file: Path,
    entries: list[dict[str, Any]],
    profile_name: str,
    scan_data_count: int,
    industries: list[str] | None,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
    reference_time: datetime,
    sort_flavour: str | None = None,
) -> None:
    """Write a per-profile earnings-priority report.

    Same chronological bucket structure as the consensus earnings-priority
    report but columns show scores, direction, confidence and coverage
    sourced exclusively from ``profile_name`` rather than the cross-profile
    consensus.  A high-conviction near-term section is appended at the end.
    """
    _reset_log_file(log_file)
    log_to_file(
        log_file,
        _build_report_title(
            f"TradingView earnings-priority — profile: {profile_name}"
            + _earnings_priority_flavour_title_suffix(sort_flavour)
        ),
    )
    log_to_file(log_file, "=" * 220)
    log_to_file(
        log_file,
        (
            f"Universe rows scanned: {scan_data_count} | "
            f"rows with usable upcoming earnings: {len(entries)} | "
            f"reference time (UTC): {reference_time.strftime('%Y-%m-%d %H:%M')} | "
            f"profile: {profile_name} | "
            f"industries={industries or 'all'} | "
            f"min_market_cap={min_market_cap_usd} | max_market_cap={max_market_cap_usd}"
        ),
    )
    log_to_file(log_file, "")
    log_to_file(log_file, "Methodology")
    log_to_file(log_file, "-" * 220)
    log_to_file(
        log_file,
        "Names are ordered chronologically by earnings_release_next_date. "
        "Each row shows this profile's per-horizon score, direction, confidence, and component coverage "
        "so that upcoming-catalyst names can be reviewed through the lens of a single scoring profile.",
    )
    sort_note = _earnings_priority_flavour_note(
        sort_flavour,
        profile_name=profile_name,
    )
    if sort_note is not None:
        log_to_file(log_file, sort_note)
    log_to_file(log_file, "")

    if not entries:
        log_to_file(log_file, "(no rows with an upcoming earnings date were found)")
        return

    horizon_names = list(DEFAULT_HORIZON_WEIGHTS.keys())

    # ── Bucketed sections (chronological) ────────────────────────────────
    for bucket_label, date_groups in _iter_earnings_priority_bucket_groups(
        entries,
        sort_flavour=sort_flavour,
        profile_name=profile_name,
    ):
        bucket_size = sum(len(date_entries) for _, date_entries in date_groups)
        log_to_file(log_file, f"BUCKET — {bucket_label} ({bucket_size} names)")
        log_to_file(log_file, "=" * 220)
        # Header: base identity columns + per-horizon (Score Dir Conf Cov)
        col_header = (
            f"{'Days':>6} {'Earnings':<11} {'Ticker':<12} {'Company':<28} "
            f"{'Sector':<22} {'MCap':>14}"
        )
        for h_name in horizon_names:
            lbl = h_name.capitalize()[:3]
            col_header += f"  {lbl+'/Score':>10} {lbl+'/Dir':<11} {lbl+'/Conf':>7} {lbl+'/Cov':>6}"
        log_to_file(log_file, col_header)
        log_to_file(log_file, "-" * 220)

        for date_label, date_entries in date_groups:
            if sort_flavour is not None:
                log_to_file(
                    log_file,
                    f"DATE GROUP: {date_label} ({len(date_entries)} names)",
                )
            for entry in date_entries:
                consensus_row = entry["consensus_row"]
                row = consensus_row["row"]
                profile_horizons = (
                    consensus_row.get("profile_scores", {}).get(profile_name) or {}
                )
                horizon_cells = ""
                for h_name in horizon_names:
                    h = profile_horizons.get(h_name) or {}
                    score = h.get("score")
                    direction = str(h.get("direction") or "N/A")[:10]
                    confidence = h.get("confidence")
                    cov = h.get("coverage")
                    horizon_cells += (
                        f"  {_format_score(score):>10} {direction:<11} "
                        f"{_format_confidence(confidence):>7} "
                        f"{f'{cov:.0%}' if cov is not None else 'N/A':>6}"
                    )
                log_to_file(
                    log_file,
                    f"{entry['days_until']:>6.1f} "
                    f"{_format_earnings_date(entry['next_earnings_dt']):<11} "
                    f"{consensus_row['ticker']:<12} "
                    f"{(consensus_row['company'] or 'N/A')[:27]:<28} "
                    f"{str(row.get('sector') or 'N/A')[:21]:<22} "
                    f"{_format_market_cap(consensus_row['market_cap']):>14}"
                    f"{horizon_cells}",
                )
        log_to_file(log_file, "")

    # ── High-conviction near-term setups ─────────────────────────────────
    log_to_file(log_file, "HIGH-CONVICTION NEAR-TERM EARNINGS SETUPS")
    log_to_file(log_file, "=" * 220)
    log_to_file(
        log_file,
        f"Names with an earnings catalyst within 30 days AND |days-horizon score| "
        f">= {DIRECTIONAL_MOVE_SCORE_THRESHOLD:.2f}:",
    )
    log_to_file(log_file, "-" * 220)

    near_term_strong: list[dict[str, Any]] = []
    for entry in entries:
        if entry["days_until"] > 30:
            continue
        h = (
            entry["consensus_row"]
            .get("profile_scores", {})
            .get(profile_name, {})
            .get("days")
            or {}
        )
        score = h.get("score")
        if score is not None and abs(score) >= DIRECTIONAL_MOVE_SCORE_THRESHOLD:
            near_term_strong.append(entry)
    near_term_strong = _flatten_earnings_priority_entries(
        near_term_strong,
        sort_flavour=sort_flavour,
        profile_name=profile_name,
    )

    if not near_term_strong:
        log_to_file(log_file, "  (no qualifying near-term high-conviction names)")
    else:
        log_to_file(
            log_file,
            f"{'Days':>6} {'Earnings':<11} {'Ticker':<12} {'Company':<28} "
            f"{'DaysScore':>10} {'Dir':<11} {'Conf':>7} {'Cov':>6}",
        )
        log_to_file(log_file, "-" * 220)
        for date_label, date_entries in _group_earnings_priority_entries_by_date(
            near_term_strong,
            sort_flavour=sort_flavour,
            profile_name=profile_name,
        ):
            if sort_flavour is not None:
                log_to_file(
                    log_file,
                    f"DATE GROUP: {date_label} ({len(date_entries)} names)",
                )
            for entry in date_entries:
                consensus_row = entry["consensus_row"]
                h = (
                    consensus_row.get("profile_scores", {})
                    .get(profile_name, {})
                    .get("days")
                    or {}
                )
                cov_val = h.get("coverage")
                cov_str = f"{cov_val:.0%}" if cov_val is not None else "N/A"
                log_to_file(
                    log_file,
                    f"{entry['days_until']:>6.1f} "
                    f"{_format_earnings_date(entry['next_earnings_dt']):<11} "
                    f"{consensus_row['ticker']:<12} "
                    f"{(consensus_row['company'] or 'N/A')[:27]:<28} "
                    f"{_format_score(h.get('score')):>10} "
                    f"{str(h.get('direction') or 'N/A'):<11} "
                    f"{_format_confidence(h.get('confidence')):>7} "
                    f"{cov_str:>6}",
                )
    log_to_file(log_file, "")


def _write_earnings_priority_profile_csv(
    csv_file: Path,
    entries: list[dict[str, Any]],
    profile_name: str,
    sort_flavour: str | None = None,
) -> None:
    """Write the per-profile earnings-priority CSV.

    One row per name (sorted chronologically). Columns cover the full
    earnings date metadata plus this profile's per-horizon score, direction,
    confidence, component coverage and setup label.
    """
    horizon_names = list(DEFAULT_HORIZON_WEIGHTS.keys())
    csv_headers: list[str] = [
        "rank_chronological",
        "days_until_earnings",
        "earnings_release_next_date",
        "earnings_release_next_calendar_date",
        "earnings_release_next_time",
        "last_earnings_release_date",
        "ticker",
        "company",
        "sector",
        "industry",
        "market_cap",
        "bucket",
    ]
    for h_name in horizon_names:
        csv_headers.extend(
            [
                f"{h_name}_score",
                f"{h_name}_direction",
                f"{h_name}_confidence",
                f"{h_name}_coverage",
                f"{h_name}_setup",
            ]
        )

    ordered_entries = _flatten_earnings_priority_entries(
        entries,
        sort_flavour=sort_flavour,
        profile_name=profile_name,
    )

    csv_rows: list[list[str]] = []
    for rank, entry in enumerate(ordered_entries, start=1):
        consensus_row = entry["consensus_row"]
        row = consensus_row["row"]
        profile_horizons = (
            consensus_row.get("profile_scores", {}).get(profile_name) or {}
        )
        csv_row: list[str] = [
            str(rank),
            f"{entry['days_until']:.2f}",
            _format_earnings_date(entry["next_earnings_dt"]),
            _format_earnings_date(entry["next_earnings_calendar_dt"]),
            str(entry["next_earnings_time_raw"] or ""),
            _format_earnings_date(entry["last_earnings_dt"]),
            consensus_row["ticker"],
            consensus_row["company"] or "",
            str(row.get("sector") or ""),
            str(row.get("industry") or ""),
            (
                f"{consensus_row['market_cap']:.0f}"
                if consensus_row["market_cap"] is not None
                else ""
            ),
            _bucket_for_days(entry["days_until"]),
        ]
        for h_name in horizon_names:
            h = profile_horizons.get(h_name) or {}
            score = h.get("score")
            cov = h.get("coverage")
            csv_row.extend(
                [
                    f"{score:.4f}" if score is not None else "",
                    str(h.get("direction") or ""),
                    (
                        f"{h.get('confidence'):.2f}"
                        if h.get("confidence") is not None
                        else ""
                    ),
                    f"{cov:.4f}" if cov is not None else "",
                    str(h.get("setup") or ""),
                ]
            )
        csv_rows.append(csv_row)

    log_rows_to_csv(csv_file, csv_headers, csv_rows)


def _write_earnings_priority_consensus_csv(
    csv_file: Path,
    entries: list[dict[str, Any]],
    profile_names: list[str],
    sort_flavour: str | None = None,
) -> None:
    horizon_names = list(DEFAULT_HORIZON_WEIGHTS.keys())
    csv_headers: list[str] = [
        "rank_chronological",
        "days_until_earnings",
        "earnings_release_next_date",
        "earnings_release_next_calendar_date",
        "earnings_release_next_time",
        "last_earnings_release_date",
        "ticker",
        "company",
        "sector",
        "industry",
        "market_cap",
        "bucket",
    ]
    for h_name in horizon_names:
        csv_headers.extend(
            [
                f"consensus_{h_name}_score",
                f"consensus_{h_name}_direction",
                f"consensus_{h_name}_confidence",
                f"consensus_{h_name}_agreement_ratio",
                f"consensus_{h_name}_opinions",
                f"consensus_{h_name}_profile_coverage",
            ]
        )
    for profile_name in profile_names:
        for h_name in horizon_names:
            csv_headers.append(f"{profile_name}__{h_name}_score")

    ordered_entries = _flatten_earnings_priority_entries(
        entries,
        sort_flavour=sort_flavour,
    )
    csv_rows: list[list[str]] = []
    for rank, entry in enumerate(ordered_entries, start=1):
        consensus_row = entry["consensus_row"]
        row = consensus_row["row"]
        csv_row: list[str] = [
            str(rank),
            f"{entry['days_until']:.2f}",
            _format_earnings_date(entry["next_earnings_dt"]),
            _format_earnings_date(entry["next_earnings_calendar_dt"]),
            str(entry["next_earnings_time_raw"] or ""),
            _format_earnings_date(entry["last_earnings_dt"]),
            consensus_row["ticker"],
            consensus_row["company"] or "",
            str(row.get("sector") or ""),
            str(row.get("industry") or ""),
            (
                f"{consensus_row['market_cap']:.0f}"
                if consensus_row["market_cap"] is not None
                else ""
            ),
            _bucket_for_days(entry["days_until"]),
        ]
        for h_name in horizon_names:
            h_data = consensus_row["horizons"].get(h_name) or {}
            score = h_data.get("score")
            csv_row.extend(
                [
                    f"{score:.4f}" if score is not None else "",
                    str(h_data.get("direction") or ""),
                    (
                        f"{h_data.get('confidence'):.2f}"
                        if h_data.get("confidence") is not None
                        else ""
                    ),
                    (
                        f"{h_data.get('agreement_ratio'):.4f}"
                        if h_data.get("agreement_ratio") is not None
                        else ""
                    ),
                    str(h_data.get("opinions") or 0),
                    str(entry["coverage_per_horizon"].get(h_name, 0)),
                ]
            )
        for profile_name in profile_names:
            for h_name in horizon_names:
                profile_horizon = (
                    consensus_row.get("profile_scores", {})
                    .get(profile_name, {})
                    .get(h_name)
                    or {}
                )
                score = profile_horizon.get("score")
                csv_row.append(f"{score:.4f}" if score is not None else "")
        csv_rows.append(csv_row)

    log_rows_to_csv(csv_file, csv_headers, csv_rows)


def run_earnings_priority_aggregator(
    scan_data: list[dict[str, Any]],
    profile_names: list[str] | None = None,
    industries: list[str] | str | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    dedicated_output_dir: Path | None = None,
    reference_time: datetime | None = None,
) -> dict[str, Path]:
    """Run the consensus aggregation, then write a chronologically-ordered
    earnings-priority report plus one dedicated report per scoring profile.

    All outputs go into ``dedicated_output_dir`` (a single folder that
    contains only earnings-priority files).  The caller is responsible for
    creating/choosing that folder.

    Returns the default consensus/profile outputs plus two additional flavour
    variants for each: ``flavour_marketcap`` and ``flavour_score``. The
    matching ``.csv`` files live alongside their ``.log`` counterparts.

    ``scan_data`` MUST come from a scan that includes the
    ``earnings_release_next_date`` column (see
    ``ApiTradingViewClient.scan_global_market_move_prediction_with_earnings``).
    Names without a parseable upcoming earnings date are excluded from all
    reports in this aggregator — they are still covered by the standard
    consensus aggregator.
    """
    industries_normalized = _normalize_industries(industries)
    if profile_names is None:
        profile_names = list(CONSENSUS_PROFILE_WEIGHTS.keys())

    # All files go into the dedicated folder, using a fixed slug prefix so
    # file names within the folder are short and easy to distinguish.
    def _ep_file(
        scoring_profile_slug: str,
        sort_flavour: str | None = None,
    ) -> Path:
        effective_profile_slug = scoring_profile_slug
        if sort_flavour is not None:
            effective_profile_slug = f"{effective_profile_slug}_{sort_flavour}"
        return _build_report_file_name(
            report_slug="tradingview_earnings_priority",
            industries=industries_normalized,
            min_market_cap_usd=min_market_cap_usd,
            max_market_cap_usd=max_market_cap_usd,
            scoring_profile_name=effective_profile_slug,
            output_dir=dedicated_output_dir,
        )

    # ── Build consensus scores (computes per-profile data as a side-effect) ──
    consensus_rows = _build_consensus_scores(
        scan_data=scan_data,
        profile_names=profile_names,
    )

    reference = reference_time or datetime.now(tz=timezone.utc)
    entries = _build_earnings_priority_entries(
        consensus_rows=consensus_rows,
        reference_time=reference,
    )

    flavour_variants: list[str | None] = [None, *EARNINGS_PRIORITY_SORT_FLAVOURS]

    # ── Consensus log + CSV ───────────────────────────────────────────────
    generated: dict[str, Path] = {}
    for sort_flavour in flavour_variants:
        consensus_log = _ep_file("consensus", sort_flavour=sort_flavour)
        _log_earnings_priority_report(
            log_file=consensus_log,
            entries=entries,
            scan_data_count=len(scan_data),
            industries=industries_normalized,
            min_market_cap_usd=min_market_cap_usd,
            max_market_cap_usd=max_market_cap_usd,
            profile_names=profile_names,
            reference_time=reference,
            sort_flavour=sort_flavour,
        )
        _write_earnings_priority_consensus_csv(
            csv_file=consensus_log.with_suffix(".csv"),
            entries=entries,
            profile_names=profile_names,
            sort_flavour=sort_flavour,
        )
        result_key = "_consensus"
        if sort_flavour is not None:
            result_key = f"consensus_{sort_flavour}"
        generated[result_key] = consensus_log

    # ── Per-profile log + CSV ─────────────────────────────────────────────
    for profile_name in profile_names:
        for sort_flavour in flavour_variants:
            profile_log = _ep_file(profile_name, sort_flavour=sort_flavour)
            _log_earnings_priority_report_for_profile(
                log_file=profile_log,
                entries=entries,
                profile_name=profile_name,
                scan_data_count=len(scan_data),
                industries=industries_normalized,
                min_market_cap_usd=min_market_cap_usd,
                max_market_cap_usd=max_market_cap_usd,
                reference_time=reference,
                sort_flavour=sort_flavour,
            )
            _write_earnings_priority_profile_csv(
                csv_file=profile_log.with_suffix(".csv"),
                entries=entries,
                profile_name=profile_name,
                sort_flavour=sort_flavour,
            )
            result_key = profile_name
            if sort_flavour is not None:
                result_key = f"{profile_name}_{sort_flavour}"
            generated[result_key] = profile_log

    return generated


def run_full_analysis_suite_with_earnings_priority(
    scan_data: list[dict[str, Any]],
    profile_names: list[str] | None = None,
    industries: list[str] | str | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    include_blind_spot_sections: bool = False,
    output_dir: str | Path | None = None,
    reference_time: datetime | None = None,
) -> dict[str, Path]:
    """Variant of :func:`run_full_analysis_suite` that also writes a full set
    of earnings-priority reports into a dedicated sub-folder.

    The standard per-profile and consensus analysis reports are written to
    ``output_dir`` exactly as ``run_full_analysis_suite`` would.

    In addition, a separate ``earnings_priority/`` sub-folder is created
    inside ``output_dir`` (or ``LOG_DIR`` when ``output_dir`` is ``None``)
    that contains:

    * **consensus** ``.log`` + ``.csv`` — cross-profile earnings ranking
      (same logic as the normal consensus aggregator, re-sorted by upcoming
      earnings date chronologically).
    * **one** ``.log`` + ``.csv`` **per scoring profile** — same chronological
      view but scores, direction, confidence and coverage come from that
      individual profile only, so each profile's perspective on the
      catalyst-ranked universe is self-contained.

    ``scan_data`` should come from
    ``ApiTradingViewClient.scan_global_market_move_prediction_with_earnings``
    (which guarantees the earnings columns are present).  Rows without a
    parseable upcoming earnings date are excluded from the earnings-priority
    reports only — they remain fully covered by the standard profile and
    consensus reports.

    Returns a dict with all generated log paths:
    - Per-profile keys from the standard suite (e.g. ``"breakout_long"``)
    - ``"_consensus_aggregator"`` — the standard consensus log
    - ``"_earnings_priority_consensus"`` — earnings-priority consensus log
        - ``"_earnings_priority_consensus_flavour_marketcap"`` — earnings-priority
            consensus grouped by earnings date, then sorted by market cap within date
        - ``"_earnings_priority_consensus_flavour_score"`` — earnings-priority
            consensus grouped by earnings date, then sorted by weekly score within date
    - ``"_earnings_priority_<profile_name>"`` — per-profile earnings logs
        - ``"_earnings_priority_<profile_name>_flavour_marketcap"`` — per-profile
            earnings logs grouped by earnings date, then sorted by market cap within date
        - ``"_earnings_priority_<profile_name>_flavour_score"`` — per-profile
            earnings logs grouped by earnings date, then sorted by weekly score within date
    """
    # ── Standard full analysis (all profiles + consensus) ────────────────
    base_result = run_full_analysis_suite(
        scan_data=scan_data,
        profile_names=profile_names,
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        include_blind_spot_sections=include_blind_spot_sections,
        output_dir=output_dir,
    )

    # ── Dedicated earnings-priority subfolder ─────────────────────────────
    base_dir = Path(output_dir) if output_dir is not None else LOG_DIR
    earnings_priority_dir = base_dir / "earnings_priority"
    earnings_priority_dir.mkdir(parents=True, exist_ok=True)

    # ── Earnings-priority aggregator (consensus + per-profile) ────────────
    earnings_logs = run_earnings_priority_aggregator(
        scan_data=scan_data,
        profile_names=profile_names,
        industries=industries,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        dedicated_output_dir=earnings_priority_dir,
        reference_time=reference_time,
    )

    # ── Merge results ─────────────────────────────────────────────────────
    result = dict(base_result)
    for key, path in earnings_logs.items():
        result_key = "_earnings_priority_consensus"
        if key != "_consensus":
            result_key = f"_earnings_priority_{key}"
        result[result_key] = path
    return result


# ──────────────────────────────────────────────────────────────────────────────
# RAW ALL-FIELDS CSV BACKSCAN SUITE
# ──────────────────────────────────────────────────────────────────────────────


def _set_safe_csv_field_size_limit() -> None:
    field_size_limit = sys.maxsize
    while field_size_limit > 0:
        try:
            csv.field_size_limit(field_size_limit)
            return
        except OverflowError:
            field_size_limit //= 10
    raise OverflowError("Unable to configure csv.field_size_limit for this platform.")


def _deserialize_raw_csv_value(value: str | None) -> Any:
    if value is None:
        return None

    text = value.strip()
    if not text:
        return None

    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"none", "null", "nan"}:
        return None

    if text[0] in "[{" and text[-1] in "]}":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    try:
        numeric_value = float(text)
    except ValueError:
        return text

    if not math.isfinite(numeric_value):
        return None
    return numeric_value


def _required_raw_backscan_fields() -> set[str]:
    return {
        "symbol",
        "name",
        "description",
        "ticker-view",
        "is_primary",
        "active_symbol",
        "earnings_release_calendar_date",
        "earnings_release_next_calendar_date",
        "earnings_release_next_time",
        "earnings_release_time",
        *ENTRY_METADATA_FIELDS,
        *RAW_PROFILE_FIELDS,
    }


def load_tradingview_raw_csv_scan_data(
    csv_path: str | Path,
    required_fields: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Load a saved TradingView all-fields CSV into the scan_data format.

    The all-fields exports are intentionally wide, often several GB per day.
    This loader keeps only the columns used by the move-prediction scorer and
    its output labels, while deserializing numeric strings back to floats so the
    scoring engine sees the same shape it gets from live API rows.
    """
    resolved_csv_path = Path(csv_path)
    fields_to_keep = required_fields or _required_raw_backscan_fields()
    _set_safe_csv_field_size_limit()

    rows: list[dict[str, Any]] = []
    with resolved_csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        fieldnames = reader.fieldnames or []
        kept_fields = [field for field in fieldnames if field in fields_to_keep]
        if "symbol" not in kept_fields and "symbol" in fieldnames:
            kept_fields.insert(0, "symbol")

        for raw_row in reader:
            row = {
                field_name: _deserialize_raw_csv_value(raw_row.get(field_name))
                for field_name in kept_fields
            }
            if row.get("symbol"):
                rows.append(row)

    return rows


def _filter_scan_data_by_market_cap(
    scan_data: list[dict[str, Any]],
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
) -> list[dict[str, Any]]:
    if min_market_cap_usd is None and max_market_cap_usd is None:
        return scan_data

    filtered_rows: list[dict[str, Any]] = []
    for row in scan_data:
        market_cap = _coerce_numeric(row.get("market_cap_basic"))
        if market_cap is None:
            continue
        if min_market_cap_usd is not None and market_cap < min_market_cap_usd:
            continue
        if max_market_cap_usd is not None and market_cap > max_market_cap_usd:
            continue
        filtered_rows.append(row)
    return filtered_rows


def _find_raw_backscan_csv(folder_path: Path, csv_glob: str) -> Path:
    preferred_matches = sorted(folder_path.glob(csv_glob))
    if preferred_matches:
        return preferred_matches[0]

    csv_matches = sorted(folder_path.glob("*.csv"))
    if not csv_matches:
        raise FileNotFoundError(f"No CSV file found in raw data folder: {folder_path}")
    return csv_matches[0]


def _date_from_backscan_label(label: str) -> datetime | None:
    try:
        return datetime.strptime(label, "%d_%m_%Y")
    except ValueError:
        return None


def _infer_raw_backscan_date_label(csv_path: Path, folder_path: Path) -> str:
    folder_label = folder_path.name
    if _date_from_backscan_label(folder_label) is not None:
        return folder_label

    stem_parts = csv_path.stem.split("_")
    if len(stem_parts) >= 3:
        candidate = "_".join(stem_parts[-3:])
        if _date_from_backscan_label(candidate) is not None:
            return candidate

    return _slugify(folder_label) or _slugify(csv_path.stem)


def _sort_raw_backscan_folder_key(
    folder_path: Path, csv_glob: str = BACKSCAN_RAW_CSV_GLOB
) -> tuple[int, datetime, str]:
    csv_path = _find_raw_backscan_csv(folder_path, csv_glob)
    label = _infer_raw_backscan_date_label(csv_path, folder_path)
    parsed_date = _date_from_backscan_label(label)
    if parsed_date is None:
        return (1, datetime.max, str(folder_path))
    return (0, parsed_date, str(folder_path))


def _parse_backscan_score(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _write_backscan_horizon_snapshot_csv(
    source_csv: Path,
    output_csv: Path,
    profile_name: str,
    horizon_name: str,
    source_date_label: str,
    is_consensus: bool = False,
) -> None:
    """Write a narrow per-profile/per-horizon snapshot from suite CSV output."""
    if is_consensus:
        score_col = f"consensus_{horizon_name}_score"
        direction_col = f"consensus_{horizon_name}_direction"
        confidence_col = f"consensus_{horizon_name}_confidence"
        coverage_col = f"consensus_{horizon_name}_agreement"
        setup_col = "manager_action_signal"
        risk_adjusted_col = f"consensus_{horizon_name}_risk_adjusted_score"
        risk_tier_col = f"consensus_{horizon_name}_risk_tier"
        symbol_col = "ticker"
        company_col = "company"
        market_cap_col = "market_cap"
    else:
        score_col = f"{horizon_name}_score"
        direction_col = f"{horizon_name}_direction"
        confidence_col = f"{horizon_name}_confidence"
        coverage_col = f"{horizon_name}_coverage"
        setup_col = f"{horizon_name}_setup"
        risk_adjusted_col = f"{horizon_name}_risk_adjusted_score"
        risk_tier_col = f"{horizon_name}_risk_tier"
        symbol_col = "symbol"
        company_col = "Company"
        market_cap_col = "market_cap_basic"

    ranked_rows: list[tuple[dict[str, str], float]] = []
    with source_csv.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            score = _parse_backscan_score(row.get(score_col))
            if score is None:
                continue
            ranked_rows.append((row, score))

    ranked_rows.sort(key=lambda item: item[1], reverse=True)

    headers = [
        "rank",
        "source_date",
        "profile_name",
        "horizon",
        "symbol",
        "company",
        "sector",
        "industry",
        "market_cap",
        "close",
        "score",
        "direction",
        "confidence",
        "coverage_or_agreement",
        "setup_or_action",
        "risk_adjusted_score",
        "risk_tier",
        "source_csv",
    ]
    output_rows: list[list[str]] = []
    for rank, (row, score) in enumerate(ranked_rows, start=1):
        output_rows.append(
            [
                str(rank),
                source_date_label,
                profile_name,
                horizon_name,
                str(row.get(symbol_col) or ""),
                str(row.get(company_col) or ""),
                str(row.get("sector") or ""),
                str(row.get("industry") or ""),
                str(row.get(market_cap_col) or ""),
                str(row.get("close") or ""),
                f"{score:.6f}",
                str(row.get(direction_col) or ""),
                str(row.get(confidence_col) or ""),
                str(row.get(coverage_col) or ""),
                str(row.get(setup_col) or ""),
                str(row.get(risk_adjusted_col) or ""),
                str(row.get(risk_tier_col) or ""),
                str(source_csv),
            ]
        )

    log_rows_to_csv(output_csv, headers, output_rows)


def _validate_backscan_inputs(
    profile_names: list[str], horizon_names: list[str]
) -> None:
    unknown_profiles = []
    for profile_name in profile_names:
        try:
            resolve_move_prediction_scoring_profile(profile_name)
        except ValueError:
            unknown_profiles.append(profile_name)
    if unknown_profiles:
        available = ", ".join(sorted(PRESET_SCORING_PROFILES))
        raise ValueError(
            f"Unknown raw backscan profile(s): {unknown_profiles}. Available: {available}"
        )

    unknown_horizons = [
        horizon_name
        for horizon_name in horizon_names
        if horizon_name not in DEFAULT_HORIZON_WEIGHTS
    ]
    if unknown_horizons:
        available = ", ".join(DEFAULT_HORIZON_WEIGHTS)
        raise ValueError(
            f"Unknown raw backscan horizon(s): {unknown_horizons}. Available: {available}"
        )


def run_full_analysis_suite_from_raw_csv_folders(
    raw_data_folders: list[str | Path],
    profile_names: list[str] | None = None,
    horizon_names: list[str] | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    include_blind_spot_sections: bool = False,
    output_root: str | Path | None = None,
    run_label: str | None = None,
    csv_glob: str = BACKSCAN_RAW_CSV_GLOB,
    include_consensus: bool = True,
) -> dict[str, Any]:
    """Replay the latest move-prediction suite over saved all-fields CSV days.

    ``raw_data_folders`` should contain folders like ``01_04_2026`` where each
    folder contains a saved all-fields CSV such as
    ``tradingview_global_all_tdfields_01_04_2026.csv``. Outputs are written
    under ``logs/tradingview_analysis/prediction_analysis/raw_csv_backscan`` by
    default using this shape::

        <run>/
          profile_<profile>/
            all_horizons/<dd_mm_yyyy>/      full suite log/csv/tracking output
            days/<dd_mm_yyyy>/              narrow ranked snapshot CSV
            weeks/<dd_mm_yyyy>/
            months/<dd_mm_yyyy>/
            years/<dd_mm_yyyy>/
          _consensus_aggregator/<horizon>/<dd_mm_yyyy>/
          _raw_csv_backscan_manifest.csv
          _raw_csv_backscan_overview.log

    This is a model replay, not a historical live simulation: it applies the
    current scoring model to old saved market rows so you can compare how a new
    model iteration would rank prior universes.
    """
    if not raw_data_folders:
        raise ValueError("raw_data_folders must contain at least one folder path.")

    resolved_profiles = list(profile_names or DEFAULT_MOVE_PREDICTION_PROFILE_SUITE)
    resolved_horizons = list(horizon_names or DEFAULT_HORIZON_WEIGHTS.keys())
    _validate_backscan_inputs(resolved_profiles, resolved_horizons)

    folder_paths = [Path(folder_path) for folder_path in raw_data_folders]
    for folder_path in folder_paths:
        if not folder_path.exists() or not folder_path.is_dir():
            raise FileNotFoundError(f"Raw data folder does not exist: {folder_path}")

    folder_paths = sorted(
        folder_paths,
        key=lambda folder_path: _sort_raw_backscan_folder_key(folder_path, csv_glob),
    )

    if output_root is None:
        effective_run_label = run_label or datetime.now().strftime(
            "raw_csv_backscan_%Y%m%d_%H%M%S"
        )
        root_dir = BACKSCAN_OUTPUT_DIR / _slugify(effective_run_label)
    else:
        root_dir = Path(output_root)
        if run_label:
            root_dir = root_dir / _slugify(run_label)
    root_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "_root_dir": root_dir,
        "_profiles": resolved_profiles,
        "_horizons": resolved_horizons,
        "_days": {},
    }
    manifest_rows: list[list[str]] = []

    for folder_path in folder_paths:
        csv_path = _find_raw_backscan_csv(folder_path, csv_glob)
        date_label = _infer_raw_backscan_date_label(csv_path, folder_path)
        scan_data_raw = load_tradingview_raw_csv_scan_data(csv_path)
        scan_data = _filter_scan_data_by_market_cap(
            scan_data_raw,
            min_market_cap_usd=min_market_cap_usd,
            max_market_cap_usd=max_market_cap_usd,
        )

        day_result: dict[str, Any] = {
            "source_csv": csv_path,
            "rows_loaded": len(scan_data_raw),
            "rows_used": len(scan_data),
            "profiles": {},
        }

        for profile_name in resolved_profiles:
            profile_all_dir = (
                root_dir / f"profile_{profile_name}" / "all_horizons" / date_label
            )
            profile_all_dir.mkdir(parents=True, exist_ok=True)
            profile_log = analyze_move_prediction_scan(
                scan_data=scan_data,
                industries=None,
                min_market_cap_usd=min_market_cap_usd,
                max_market_cap_usd=max_market_cap_usd,
                scoring_profile=profile_name,
                include_blind_spot_sections=include_blind_spot_sections,
                output_dir=profile_all_dir,
            )

            profile_day_result: dict[str, Any] = {
                "all_horizons_log": profile_log,
                "horizons": {},
            }
            for horizon_name in resolved_horizons:
                horizon_dir = (
                    root_dir / f"profile_{profile_name}" / horizon_name / date_label
                )
                horizon_dir.mkdir(parents=True, exist_ok=True)
                horizon_csv = horizon_dir / (
                    f"tradingview_move_prediction_backscan__profile_{_slugify(profile_name)}"
                    f"__horizon_{_slugify(horizon_name)}__{date_label}.csv"
                )
                _write_backscan_horizon_snapshot_csv(
                    source_csv=profile_log.with_suffix(".csv"),
                    output_csv=horizon_csv,
                    profile_name=profile_name,
                    horizon_name=horizon_name,
                    source_date_label=date_label,
                )
                profile_day_result["horizons"][horizon_name] = horizon_csv

            day_result["profiles"][profile_name] = profile_day_result

        if include_consensus:
            consensus_dir = (
                root_dir / "_consensus_aggregator" / "all_horizons" / date_label
            )
            consensus_dir.mkdir(parents=True, exist_ok=True)
            consensus_log = run_consensus_aggregator(
                scan_data=scan_data,
                profile_names=resolved_profiles,
                industries=None,
                min_market_cap_usd=min_market_cap_usd,
                max_market_cap_usd=max_market_cap_usd,
                output_dir=consensus_dir,
            )
            consensus_result: dict[str, Any] = {
                "all_horizons_log": consensus_log,
                "horizons": {},
            }
            for horizon_name in resolved_horizons:
                horizon_dir = (
                    root_dir / "_consensus_aggregator" / horizon_name / date_label
                )
                horizon_dir.mkdir(parents=True, exist_ok=True)
                horizon_csv = horizon_dir / (
                    "tradingview_move_prediction_backscan__profile_consensus"
                    f"__horizon_{_slugify(horizon_name)}__{date_label}.csv"
                )
                _write_backscan_horizon_snapshot_csv(
                    source_csv=consensus_log.with_suffix(".csv"),
                    output_csv=horizon_csv,
                    profile_name="consensus",
                    horizon_name=horizon_name,
                    source_date_label=date_label,
                    is_consensus=True,
                )
                consensus_result["horizons"][horizon_name] = horizon_csv
            day_result["consensus"] = consensus_result

        result["_days"][date_label] = day_result
        manifest_rows.append(
            [
                date_label,
                str(folder_path),
                str(csv_path),
                str(len(scan_data_raw)),
                str(len(scan_data)),
                ",".join(resolved_profiles),
                ",".join(resolved_horizons),
                str(min_market_cap_usd or ""),
                str(max_market_cap_usd or ""),
            ]
        )

    manifest_csv = root_dir / "_raw_csv_backscan_manifest.csv"
    log_rows_to_csv(
        manifest_csv,
        [
            "date_label",
            "source_folder",
            "source_csv",
            "rows_loaded",
            "rows_used",
            "profiles",
            "horizons",
            "min_market_cap_usd",
            "max_market_cap_usd",
        ],
        manifest_rows,
    )

    overview_log = root_dir / "_raw_csv_backscan_overview.log"
    _reset_log_file(overview_log)
    log_to_file(overview_log, _build_report_title("Raw CSV move-prediction backscan"))
    log_to_file(overview_log, "=" * 160)
    log_to_file(overview_log, f"Root: {root_dir}")
    log_to_file(overview_log, f"Profiles: {', '.join(resolved_profiles)}")
    log_to_file(overview_log, f"Horizons: {', '.join(resolved_horizons)}")
    log_to_file(
        overview_log,
        f"Market-cap filter: min={min_market_cap_usd} | max={max_market_cap_usd}",
    )
    log_to_file(overview_log, "")
    for row in manifest_rows:
        log_to_file(
            overview_log,
            f"{row[0]} | rows_loaded={row[3]} | rows_used={row[4]} | source={row[2]}",
        )

    result["_manifest_csv"] = manifest_csv
    result["_overview_log"] = overview_log
    return result


# ──────────────────────────────────────────────────────────────────────────────
# INDUSTRY-SCOPED FULL ANALYSIS SUITE
# ──────────────────────────────────────────────────────────────────────────────

DATE_FOLDER_FORMAT = "%d_%m_%Y"


def _group_scan_data_by_field(
    scan_data: list[dict[str, Any]],
    group_field: str = "sector",
) -> dict[str, list[dict[str, Any]]]:
    """Group scan rows by a categorical field (sector or industry).

    Returns a dict mapping each distinct field value to its list of rows.
    Rows where the field is missing or empty are grouped under ``"Unknown"``.
    """
    groups: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in scan_data:
        value = row.get(group_field)
        key = str(value).strip() if value else "Unknown"
        groups[key].append(row)
    return dict(groups)


def _build_industry_coverage_stats(
    scan_data: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build per-sector coverage statistics to surface structural bias.

    For each sector, computes:
    - row_count / share of total scan
    - sub-industry count
    - median market cap
    - median close price
    - aggregate market cap share (sector total market cap / universe total)
    - top 3 sub-industries by count
    """
    total_rows = len(scan_data)
    if total_rows == 0:
        return []

    sector_groups = _group_scan_data_by_field(scan_data, "sector")
    universe_total_mcap = sum(
        _coerce_numeric(r.get("market_cap_basic")) or 0.0 for r in scan_data
    )

    stats: list[dict[str, Any]] = []
    for sector, rows in sorted(sector_groups.items(), key=lambda x: -len(x[1])):
        mcaps = [
            m
            for r in rows
            if (m := _coerce_numeric(r.get("market_cap_basic"))) is not None
        ]
        sector_total_mcap = sum(mcaps) if mcaps else 0.0

        sub_industries = collections.Counter(
            str(r.get("industry", "Unknown")).strip() for r in rows
        )

        stats.append(
            {
                "sector": sector,
                "row_count": len(rows),
                "row_share": len(rows) / total_rows if total_rows else 0.0,
                "sub_industry_count": len(sub_industries),
                "median_market_cap": median(mcaps) if mcaps else None,
                "sector_total_market_cap": sector_total_mcap,
                "market_cap_share": (
                    sector_total_mcap / universe_total_mcap
                    if universe_total_mcap > 0
                    else 0.0
                ),
                "top_sub_industries": sub_industries.most_common(5),
            }
        )
    return stats


def _log_industry_coverage_report(
    log_file: Path,
    scan_data: list[dict[str, Any]],
) -> None:
    """Write an industry coverage / bias analysis report."""
    _reset_log_file(log_file)
    log_to_file(
        log_file, _build_report_title("Industry Coverage & Structural Bias Analysis")
    )
    log_to_file(log_file, "=" * 160)
    log_to_file(log_file, f"Total universe rows: {len(scan_data)}")
    log_to_file(log_file, "")

    stats = _build_industry_coverage_stats(scan_data)
    if not stats:
        log_to_file(log_file, "No data to analyse.")
        return

    # ── Sector summary table ──
    log_to_file(log_file, "SECTOR COVERAGE SUMMARY")
    log_to_file(log_file, "-" * 160)
    log_to_file(
        log_file,
        f"{'Sector':<35} {'Rows':>6} {'Share':>8} {'McapShare':>10} "
        f"{'MedianMcap':>14} {'SubInds':>8}  Top sub-industries",
    )
    log_to_file(log_file, "-" * 160)

    for s in stats:
        top_subs = ", ".join(
            f"{name} ({cnt})" for name, cnt in s["top_sub_industries"][:3]
        )
        log_to_file(
            log_file,
            f"{s['sector']:<35} {s['row_count']:>6} {s['row_share']:>7.1%} "
            f"{s['market_cap_share']:>9.1%} "
            f"{_format_market_cap(s['median_market_cap']):>14} "
            f"{s['sub_industry_count']:>8}  {top_subs}",
        )
    log_to_file(log_file, "")

    # ── Bias analysis ──
    log_to_file(log_file, "STRUCTURAL BIAS ANALYSIS")
    log_to_file(log_file, "-" * 160)

    # Detect sectors whose row share is disproportionate to their market-cap share
    overrepresented = [
        s
        for s in stats
        if s["row_share"] > 0.05 and s["row_share"] > s["market_cap_share"] * 1.5
    ]
    underrepresented = [
        s
        for s in stats
        if s["market_cap_share"] > 0.05 and s["market_cap_share"] > s["row_share"] * 1.5
    ]
    proportional = [
        s
        for s in stats
        if s not in overrepresented
        and s not in underrepresented
        and s["row_share"] > 0.02
    ]

    if overrepresented:
        log_to_file(
            log_file,
            "Overrepresented sectors (row share > 1.5x their market-cap share):",
        )
        for s in overrepresented:
            log_to_file(
                log_file,
                f"  {s['sector']}: {s['row_share']:.1%} of rows vs {s['market_cap_share']:.1%} of market cap "
                f"({s['row_count']} names, {s['sub_industry_count']} sub-industries)",
            )
        log_to_file(log_file, "")
        log_to_file(
            log_file,
            "  Interpretation: These sectors have many listed entities relative to their "
            "combined market capitalisation. This is structural — sectors like Finance "
            "include REITs, regional banks, investment trusts and insurance companies "
            "which are numerous but individually smaller. The data is not biased; "
            "the market simply has more Finance-sector names above the market-cap floor.",
        )
    else:
        log_to_file(
            log_file,
            "No overrepresented sectors detected relative to market-cap share.",
        )
    log_to_file(log_file, "")

    if underrepresented:
        log_to_file(
            log_file,
            "Underrepresented sectors (market-cap share > 1.5x their row share):",
        )
        for s in underrepresented:
            log_to_file(
                log_file,
                f"  {s['sector']}: {s['row_share']:.1%} of rows vs {s['market_cap_share']:.1%} of market cap "
                f"({s['row_count']} names, {s['sub_industry_count']} sub-industries)",
            )
        log_to_file(log_file, "")
        log_to_file(
            log_file,
            "  Interpretation: These sectors are dominated by mega-cap names. "
            "Few listed entities but large aggregate capitalisation.",
        )
    log_to_file(log_file, "")

    if proportional:
        log_to_file(log_file, "Proportionally represented sectors:")
        for s in proportional:
            log_to_file(
                log_file,
                f"  {s['sector']}: {s['row_share']:.1%} of rows, {s['market_cap_share']:.1%} of market cap",
            )
    log_to_file(log_file, "")

    # ── Sub-industry concentration within the largest sector ──
    largest = stats[0] if stats else None
    if largest and largest["row_count"] > 50:
        log_to_file(
            log_file,
            f"LARGEST SECTOR DEEP DIVE: {largest['sector']} ({largest['row_count']} rows, "
            f"{largest['row_share']:.1%} of universe)",
        )
        log_to_file(log_file, "-" * 160)
        for name, cnt in largest["top_sub_industries"]:
            sub_share = cnt / largest["row_count"]
            log_to_file(
                log_file,
                f"  {name:<50} {cnt:>5} rows ({sub_share:>6.1%} of sector)",
            )
        log_to_file(log_file, "")
        log_to_file(
            log_file,
            "  Note: When running per-industry analysis the suite automatically isolates "
            "each sector so cross-sector count imbalance does not contaminate relative "
            "scoring. Peer metrics and component scores are computed within-sector.",
        )
    log_to_file(log_file, "")


def _log_cross_industry_aggregate_report(
    log_file: Path,
    industry_results: dict[str, dict[str, Path]],
    scan_data: list[dict[str, Any]],
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
) -> None:
    """Write a cross-industry aggregate report summarising top/bottom names per sector.

    Reads the consensus CSV from each industry's results and extracts the
    top-ranked names per horizon. This surfaces cross-industry performers
    without requiring the user to open each sector folder.
    """
    import csv as _csv

    _reset_log_file(log_file)
    log_to_file(
        log_file, _build_report_title("Cross-Industry Aggregate — Top Performers")
    )
    log_to_file(log_file, "=" * 160)
    log_to_file(
        log_file,
        f"Industries analysed: {len(industry_results)} | "
        f"min_market_cap={min_market_cap_usd} | max_market_cap={max_market_cap_usd}",
    )
    log_to_file(log_file, "")

    horizon_names = list(DEFAULT_HORIZON_WEIGHTS.keys())

    # Collect consensus rows from each industry's CSV
    all_consensus_rows: list[dict[str, Any]] = []
    for sector, paths in sorted(industry_results.items()):
        consensus_path = paths.get("_consensus_aggregator")
        if consensus_path is None:
            continue
        csv_path = Path(consensus_path).with_suffix(".csv")
        if not csv_path.exists():
            continue
        try:
            with csv_path.open(encoding="utf-8-sig") as f:
                reader = _csv.DictReader(f)
                for row in reader:
                    row["_sector"] = sector
                    all_consensus_rows.append(row)
        except Exception:
            continue

    if not all_consensus_rows:
        log_to_file(log_file, "No consensus data available.")
        return

    log_to_file(
        log_file, f"Total names across all industries: {len(all_consensus_rows)}"
    )
    log_to_file(log_file, "")

    # ── Per-horizon top and bottom names ──
    for horizon_name in horizon_names:
        score_col = f"consensus_{horizon_name}_score"
        direction_col = f"consensus_{horizon_name}_direction"
        confidence_col = f"consensus_{horizon_name}_confidence"
        agreement_col = f"consensus_{horizon_name}_agreement"

        scored_rows = []
        for row in all_consensus_rows:
            try:
                score = float(row.get(score_col, ""))
            except (ValueError, TypeError):
                continue
            scored_rows.append((row, score))

        if not scored_rows:
            continue

        scored_rows.sort(key=lambda x: x[1], reverse=True)
        horizon_title = HORIZON_TITLES.get(horizon_name, horizon_name.title())

        log_to_file(log_file, f"{horizon_title.upper()} — CROSS-INDUSTRY RANKING")
        log_to_file(log_file, "=" * 160)

        # Top bullish
        log_to_file(
            log_file, f"  Top {min(20, len(scored_rows))} bullish consensus names:"
        )
        log_to_file(log_file, "-" * 160)
        log_to_file(
            log_file,
            f"  {'Rank':>4}  {'Ticker':<16} {'Company':<35} {'Sector':<30} "
            f"{'Score':>8} {'Dir':>12} {'Conf':>8} {'Agree':>8}",
        )
        log_to_file(log_file, "-" * 160)
        for rank, (row, score) in enumerate(scored_rows[:20], 1):
            log_to_file(
                log_file,
                f"  {rank:>4}  {row.get('ticker', 'N/A'):<16} "
                f"{(row.get('company', '') or '')[:34]:<35} "
                f"{row.get('_sector', '')[:29]:<30} "
                f"{score:>+8.3f} "
                f"{row.get(direction_col, 'N/A'):>12} "
                f"{row.get(confidence_col, 'N/A'):>8} "
                f"{row.get(agreement_col, 'N/A'):>8}",
            )
        log_to_file(log_file, "")

        # Bottom bearish
        log_to_file(
            log_file, f"  Top {min(20, len(scored_rows))} bearish consensus names:"
        )
        log_to_file(log_file, "-" * 160)
        for rank, (row, score) in enumerate(reversed(scored_rows[-20:]), 1):
            log_to_file(
                log_file,
                f"  {rank:>4}  {row.get('ticker', 'N/A'):<16} "
                f"{(row.get('company', '') or '')[:34]:<35} "
                f"{row.get('_sector', '')[:29]:<30} "
                f"{score:>+8.3f} "
                f"{row.get(direction_col, 'N/A'):>12} "
                f"{row.get(confidence_col, 'N/A'):>8} "
                f"{row.get(agreement_col, 'N/A'):>8}",
            )
        log_to_file(log_file, "")

    # ── Per-sector summary: best name per horizon ──
    log_to_file(log_file, "PER-SECTOR BEST NAME — QUICK REFERENCE")
    log_to_file(log_file, "=" * 160)
    for horizon_name in horizon_names:
        score_col = f"consensus_{horizon_name}_score"
        horizon_title = HORIZON_TITLES.get(horizon_name, horizon_name.title())
        log_to_file(log_file, f"  {horizon_title}:")
        log_to_file(log_file, "-" * 160)

        sector_best: dict[str, tuple[dict[str, Any], float]] = {}
        for row in all_consensus_rows:
            try:
                score = float(row.get(score_col, ""))
            except (ValueError, TypeError):
                continue
            sector = row.get("_sector", "Unknown")
            if sector not in sector_best or score > sector_best[sector][1]:
                sector_best[sector] = (row, score)

        for sector in sorted(sector_best, key=lambda s: -sector_best[s][1]):
            row, score = sector_best[sector]
            log_to_file(
                log_file,
                f"    {sector:<30} {row.get('ticker', 'N/A'):<16} "
                f"{(row.get('company', '') or '')[:34]:<35} score={score:>+.3f}",
            )
        log_to_file(log_file, "")

    # ── Sector average score comparison ──
    log_to_file(log_file, "SECTOR AVERAGE CONSENSUS SCORE COMPARISON")
    log_to_file(log_file, "=" * 160)
    for horizon_name in horizon_names:
        score_col = f"consensus_{horizon_name}_score"
        horizon_title = HORIZON_TITLES.get(horizon_name, horizon_name.title())

        sector_scores: dict[str, list[float]] = collections.defaultdict(list)
        for row in all_consensus_rows:
            try:
                score = float(row.get(score_col, ""))
            except (ValueError, TypeError):
                continue
            sector_scores[row.get("_sector", "Unknown")].append(score)

        ranked = sorted(
            sector_scores.items(),
            key=lambda item: sum(item[1]) / len(item[1]) if item[1] else 0,
            reverse=True,
        )
        log_to_file(log_file, f"  {horizon_title}:")
        log_to_file(
            log_file,
            f"    {'Sector':<30} {'AvgScore':>10} {'Names':>6} "
            f"{'Bullish%':>10} {'Bearish%':>10}",
        )
        log_to_file(log_file, "    " + "-" * 70)
        for sector, scores in ranked:
            avg = sum(scores) / len(scores) if scores else 0
            bullish_pct = (
                sum(1 for s in scores if s > 0) / len(scores) * 100 if scores else 0
            )
            bearish_pct = (
                sum(1 for s in scores if s < 0) / len(scores) * 100 if scores else 0
            )
            log_to_file(
                log_file,
                f"    {sector:<30} {avg:>+10.3f} {len(scores):>6} "
                f"{bullish_pct:>9.1f}% {bearish_pct:>9.1f}%",
            )
        log_to_file(log_file, "")


def _build_cross_industry_aggregate_csv(
    csv_file: Path,
    industry_results: dict[str, dict[str, Path]],
) -> None:
    """Build a single CSV combining consensus scores from all industries."""
    import csv as _csv

    horizon_names = list(DEFAULT_HORIZON_WEIGHTS.keys())

    all_rows: list[list[str]] = []
    for sector, paths in sorted(industry_results.items()):
        consensus_path = paths.get("_consensus_aggregator")
        if consensus_path is None:
            continue
        source_csv = Path(consensus_path).with_suffix(".csv")
        if not source_csv.exists():
            continue
        try:
            with source_csv.open(encoding="utf-8-sig") as f:
                reader = _csv.DictReader(f)
                for row in reader:
                    csv_row = [
                        sector,
                        row.get("ticker", ""),
                        row.get("company", ""),
                        row.get("market_cap", ""),
                    ]
                    for horizon_name in horizon_names:
                        csv_row.extend(
                            [
                                row.get(f"consensus_{horizon_name}_score", ""),
                                row.get(f"consensus_{horizon_name}_direction", ""),
                                row.get(f"consensus_{horizon_name}_confidence", ""),
                                row.get(f"consensus_{horizon_name}_agreement", ""),
                                row.get(f"consensus_{horizon_name}_opinions", ""),
                            ]
                        )
                    all_rows.append(csv_row)
        except Exception:
            continue

    headers = ["sector", "ticker", "company", "market_cap"]
    for horizon_name in horizon_names:
        headers.extend(
            [
                f"consensus_{horizon_name}_score",
                f"consensus_{horizon_name}_direction",
                f"consensus_{horizon_name}_confidence",
                f"consensus_{horizon_name}_agreement",
                f"consensus_{horizon_name}_opinions",
            ]
        )
    log_rows_to_csv(csv_file, headers, all_rows)


def run_full_analysis_suite_by_industry(
    scan_data: list[dict[str, Any]],
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    include_blind_spot_sections: bool = False,
    group_field: str = "sector",
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Orchestrate the full analysis suite split by industry/sector.

    Creates the following folder structure under the output directory::

        full_analysis_suite_DD_MM_YYYY/
            _global/                       ← full-universe analysis
               (all profile logs, consensus, etc.)
            _coverage_bias_analysis.log    ← industry coverage report
            _cross_industry_aggregate.log  ← cross-industry ranked report
            _cross_industry_aggregate.csv  ← combined consensus CSV
            Finance/                       ← one folder per sector
               (all profile logs, consensus for this sector)
            Health Technology/
            Technology Services/
            ...

    Parameters
    ----------
    scan_data : list[dict]
        Raw scan rows from ``ApiTradingViewClient.scan_global_market_move_prediction``.
    min_market_cap_usd / max_market_cap_usd : float | None
        Market-cap bounds (used for report labelling; data is already filtered).
    include_blind_spot_sections : bool
        Whether to include blind-spot analysis in per-profile logs.
    group_field : str
        Field to group rows by. ``"sector"`` (default) gives ~20 groups.
        ``"industry"`` gives ~130 groups (finer granularity).
    output_dir : str | Path | None
        Override the root output directory. Defaults to ``LOG_DIR``.

    Returns
    -------
    dict[str, Any]
        ``{ "_root_dir": Path, "_global": dict, "_coverage": Path,
        "_cross_industry_aggregate": Path, "<sector>": dict, ... }``
    """
    base_dir = Path(output_dir) if output_dir is not None else LOG_DIR
    date_folder = datetime.now().strftime(DATE_FOLDER_FORMAT)
    suite_root = base_dir / f"full_analysis_suite_{date_folder}"
    suite_root.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {"_root_dir": suite_root}

    # ── 1. Global analysis (full universe) ──
    global_dir = suite_root / "_global"
    global_dir.mkdir(parents=True, exist_ok=True)
    global_logs = run_full_analysis_suite(
        scan_data=scan_data,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        include_blind_spot_sections=include_blind_spot_sections,
        output_dir=global_dir,
    )
    result["_global"] = global_logs

    # ── 2. Coverage / bias analysis ──
    coverage_log = suite_root / "_coverage_bias_analysis.log"
    _log_industry_coverage_report(coverage_log, scan_data)
    result["_coverage"] = coverage_log

    # ── 3. Per-industry analysis ──
    groups = _group_scan_data_by_field(scan_data, group_field)
    industry_results: dict[str, dict[str, Path]] = {}

    for group_name in sorted(groups):
        group_rows = groups[group_name]
        if len(group_rows) < 3:
            continue

        industry_folder = suite_root / _sanitize_industry_folder_name(group_name)
        industry_folder.mkdir(parents=True, exist_ok=True)

        industry_logs = run_full_analysis_suite(
            scan_data=group_rows,
            industries=[group_name],
            min_market_cap_usd=min_market_cap_usd,
            max_market_cap_usd=max_market_cap_usd,
            include_blind_spot_sections=include_blind_spot_sections,
            output_dir=industry_folder,
        )
        industry_results[group_name] = industry_logs
        result[group_name] = industry_logs

    # ── 4. Cross-industry aggregate report ──
    aggregate_log = suite_root / "_cross_industry_aggregate.log"
    _log_cross_industry_aggregate_report(
        log_file=aggregate_log,
        industry_results=industry_results,
        scan_data=scan_data,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
    )
    result["_cross_industry_aggregate"] = aggregate_log

    aggregate_csv = suite_root / "_cross_industry_aggregate.csv"
    _build_cross_industry_aggregate_csv(aggregate_csv, industry_results)

    return result
