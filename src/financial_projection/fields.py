"""Whitelist of TradingView all-fields columns used by iteration-1 projections."""

from __future__ import annotations

# Identity / context
IDENTITY_FIELDS: tuple[str, ...] = (
    "symbol",
    "name",
    "exchange",
    "market",
    "sector",
    "industry",
    "close",
    "market_cap_basic",
)

# Fundamental levels
LEVEL_FIELDS: tuple[str, ...] = (
    "total_revenue",
    "total_revenue_ttm",
    "ebitda",
    "ebitda_ttm",
    "ebit_ttm",
    "oper_income_ttm",
    "net_income_ttm",
    "ebitda_margin_ttm",
    "operating_margin_ttm",
    "net_margin_ttm",
    "enterprise_value_current",
    "total_debt",
    "net_debt",
    "cash_n_equivalents_fq",
)

# Valuation multiples
MULTIPLE_FIELDS: tuple[str, ...] = (
    "enterprise_value_to_revenue_ttm",
    "enterprise_value_ebitda_ttm",
    "price_earnings_ttm",
    "price_earnings_forward_fy",
    # TradingView often leaves price_earnings_forward_fy empty; this is the
    # usable next-FY forward P/E when present.
    "non_gaap_price_to_earnings_per_share_forecast_next_fy",
    "price_sales_current",
)

# Growth / sustainability / history lanes
GROWTH_FIELDS: tuple[str, ...] = (
    "total_revenue_yoy_growth_ttm",
    "total_revenue_yoy_growth_fy",
    "total_revenue_cagr_5y",
    "total_revenue_5y_growth_fy",
    "ebitda_yoy_growth_ttm",
    "net_income_yoy_growth_ttm",
    "net_income_cagr_5y",
    "free_cash_flow_cagr_5y",
    "sustainable_growth_rate_ttm",
)

# Street / near-term forward (also feeds short-term outlook summaries)
FORWARD_FIELDS: tuple[str, ...] = (
    "total_revenue_fq",
    "revenue_forecast_fq",
    "revenue_forecast_next_fq",
    "revenue_forecast_next_fy",
    "earnings_per_share_fq",
    "earnings_per_share_diluted_fq",
    "earnings_per_share_diluted_ttm",
    "earnings_per_share_basic_ttm",
    "earnings_per_share_forecast_fq",
    "earnings_per_share_forecast_next_fq",
    "earnings_per_share_forecast_next_fy",
    "price_earnings_forward_fy",
    "non_gaap_price_to_earnings_per_share_forecast_next_fy",
    "price_target_median",
    "price_target_average",
    "price_target_1y",
    "price_target_low",
    "price_target_high",
    "earnings_release_next_calendar_date",
    "earnings_release_next_date",
    "earnings_release_next_trading_date_fq",
)

PROJECTION_FIELD_WHITELIST: tuple[str, ...] = (
    IDENTITY_FIELDS
    + LEVEL_FIELDS
    + MULTIPLE_FIELDS
    + GROWTH_FIELDS
    + FORWARD_FIELDS
)

# Metrics used for peer/industry relative scales
PEER_METRIC_FIELDS: tuple[str, ...] = (
    "enterprise_value_to_revenue_ttm",
    "enterprise_value_ebitda_ttm",
    "price_earnings_ttm",
    "price_earnings_forward_fy",
    "non_gaap_price_to_earnings_per_share_forecast_next_fy",
    "total_revenue_yoy_growth_ttm",
    "total_revenue_cagr_5y",
    "free_cash_flow_cagr_5y",
    "ebitda_yoy_growth_ttm",
    "ebitda_margin_ttm",
    "operating_margin_ttm",
    "net_margin_ttm",
    "net_income_yoy_growth_ttm",
    "net_income_cagr_5y",
)

REQUIRED_SCENARIO_KEYS: tuple[str, ...] = ("bear", "base", "bull")

SCENARIO_PARAM_KEYS: tuple[str, ...] = (
    "revenue_growth_override",
    "revenue_growth_scale",
    "growth_fade_per_year",
    "terminal_multiple_scale_vs_peer",
    "own_multiple_blend",
    "net_debt_growth_per_year",
    "sgr_cap_multiplier",
)
