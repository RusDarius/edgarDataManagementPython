"""Curated ~100 all-fields columns for a single-ticker projection overview.

TradingView field names stay exact. This is intentionally much smaller than the
~3k+ all-fields schema: overview / projection scan only.
"""

from __future__ import annotations

from typing import Mapping

# ---------------------------------------------------------------------------
# Buckets (ordered). Counts target ~100 unique fields when flattened.
# ---------------------------------------------------------------------------

IDENTITY_OVERVIEW_FIELDS: tuple[str, ...] = (
    "symbol",
    "name",
    "description",
    "exchange",
    "market",
    "country",
    "sector",
    "industry",
    "type",
    "is_primary",
    "currency",
    "market_cap_basic",
)

PRICE_PERFORMANCE_OVERVIEW_FIELDS: tuple[str, ...] = (
    "close",
    "change",
    "change|1W",
    "change|1M",
    "Perf.5D",
    "Perf.1M",
    "Perf.3M",
    "Perf.6M",
    "Perf.Y",
    "Perf.YTD",
    "price_52_week_high",
    "price_52_week_low",
    "High.6M",
    "Low.6M",
)

VALUATION_OVERVIEW_FIELDS: tuple[str, ...] = (
    "enterprise_value_current",
    "enterprise_value_to_revenue_ttm",
    "enterprise_value_ebitda_ttm",
    "enterprise_value_to_ebit_ttm",
    "enterprise_value_to_free_cash_flow_ttm",
    "price_earnings_ttm",
    "price_earnings_forward_fy",
    "non_gaap_price_to_earnings_per_share_forecast_next_fy",
    "price_earnings_growth_ttm",
    "price_sales_current",
    "price_revenue_ttm",
    "price_book_ratio",
    "price_free_cash_flow_ttm",
    "price_to_cash_f_operating_activities_ttm",
    "earnings_yield",
    "dividend_yield_recent",
)

LEVELS_MARGINS_OVERVIEW_FIELDS: tuple[str, ...] = (
    "total_revenue_ttm",
    "total_revenue",
    "total_revenue_fq",
    "gross_profit_ttm",
    "ebitda_ttm",
    "ebitda",
    "ebit_ttm",
    "oper_income_ttm",
    "net_income_ttm",
    "gross_margin_ttm",
    "operating_margin_ttm",
    "ebitda_margin_ttm",
    "net_margin_ttm",
    "free_cash_flow_margin_ttm",
)

GROWTH_HISTORY_OVERVIEW_FIELDS: tuple[str, ...] = (
    "total_revenue_yoy_growth_ttm",
    "total_revenue_yoy_growth_fy",
    "total_revenue_cagr_5y",
    "total_revenue_5y_growth_fy",
    "gross_profit_yoy_growth_ttm",
    "ebitda_yoy_growth_ttm",
    "net_income_yoy_growth_ttm",
    "net_income_cagr_5y",
    "earnings_per_share_diluted_yoy_growth_ttm",
    "free_cash_flow_yoy_growth_ttm",
    "free_cash_flow_cagr_5y",
    "sustainable_growth_rate_ttm",
)

FORWARD_STREET_OVERVIEW_FIELDS: tuple[str, ...] = (
    "revenue_forecast_fq",
    "revenue_forecast_next_fq",
    "revenue_forecast_next_fy",
    "earnings_per_share_diluted_ttm",
    "earnings_per_share_diluted_fq",
    "earnings_per_share_forecast_fq",
    "earnings_per_share_forecast_next_fq",
    "earnings_per_share_forecast_next_fy",
    "eps_surprise_percent_fq",
    "price_target_median",
    "price_target_average",
    "price_target_1y",
    "price_target_low",
    "price_target_high",
    "price_target_1y_delta",
    "AnalystRating",
    "Recommend.All",
    "earnings_release_next_date",
    "earnings_release_next_calendar_date",
    "earnings_release_next_trading_date_fq",
)

QUALITY_BALANCE_OVERVIEW_FIELDS: tuple[str, ...] = (
    "return_on_equity",
    "return_on_assets",
    "return_on_invested_capital",
    "debt_to_equity",
    "debt_to_assets",
    "current_ratio",
    "quick_ratio",
    "total_debt",
    "net_debt",
    "cash_n_equivalents_fq",
    "altman_z_score_ttm",
    "total_shares_outstanding_current",
    "float_shares_outstanding_current",
)

CASH_FLOW_OVERVIEW_FIELDS: tuple[str, ...] = (
    "free_cash_flow_ttm",
    "free_cash_flow",
    "cash_f_operating_activities_ttm",
    "capital_expenditures_ttm",
    "cash_f_financing_activities_ttm",
    "free_cash_flow_per_share_ttm",
    "operating_cash_flow_per_share_ttm",
)

TECHNICAL_RISK_OVERVIEW_FIELDS: tuple[str, ...] = (
    "beta_1_year",
    "Volatility.D",
    "Volatility.W",
    "Volatility.M",
    "ATRP",
    "ADRP",
    "RSI",
    "SMA50",
    "SMA200",
    "relative_volume_10d_calc",
    "average_volume_10d_calc",
    "volume",
)

# Peer-relative context is most useful on these buckets.
PEER_PERCENTILE_BUCKETS: frozenset[str] = frozenset(
    {
        "valuation",
        "levels_margins",
        "growth_history",
        "quality_balance",
        "cash_flow",
    }
)

OVERVIEW_FIELD_BUCKETS: Mapping[str, tuple[str, ...]] = {
    "identity": IDENTITY_OVERVIEW_FIELDS,
    "price_performance": PRICE_PERFORMANCE_OVERVIEW_FIELDS,
    "valuation": VALUATION_OVERVIEW_FIELDS,
    "levels_margins": LEVELS_MARGINS_OVERVIEW_FIELDS,
    "growth_history": GROWTH_HISTORY_OVERVIEW_FIELDS,
    "forward_street": FORWARD_STREET_OVERVIEW_FIELDS,
    "quality_balance": QUALITY_BALANCE_OVERVIEW_FIELDS,
    "cash_flow": CASH_FLOW_OVERVIEW_FIELDS,
    "technical_risk": TECHNICAL_RISK_OVERVIEW_FIELDS,
}

TEXT_OVERVIEW_FIELDS: frozenset[str] = frozenset(
    {
        "symbol",
        "name",
        "description",
        "exchange",
        "market",
        "country",
        "sector",
        "industry",
        "type",
        "is_primary",
        "currency",
        "AnalystRating",
        "earnings_release_next_date",
        "earnings_release_next_calendar_date",
        "earnings_release_next_trading_date_fq",
    }
)


def _dedupe_preserve(fields: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for field in fields:
        if field in seen:
            continue
        seen.add(field)
        out.append(field)
    return tuple(out)


OVERVIEW_FIELD_WHITELIST: tuple[str, ...] = _dedupe_preserve(
    tuple(field for fields in OVERVIEW_FIELD_BUCKETS.values() for field in fields)
)

OVERVIEW_FIELD_TO_BUCKET: dict[str, str] = {}
for _bucket, _fields in OVERVIEW_FIELD_BUCKETS.items():
    for _field in _fields:
        OVERVIEW_FIELD_TO_BUCKET.setdefault(_field, _bucket)


def overview_field_count() -> int:
    return len(OVERVIEW_FIELD_WHITELIST)


def bucket_field_counts() -> dict[str, int]:
    return {bucket: len(fields) for bucket, fields in OVERVIEW_FIELD_BUCKETS.items()}
