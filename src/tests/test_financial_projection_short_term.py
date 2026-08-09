from financial_projection.short_term_outlook import (
    build_short_term_outlook,
    normalize_earnings_date,
)
from financial_projection.model import project_symbol_scenario
from financial_projection.config import ScenarioParams


def _scenario() -> ScenarioParams:
    return ScenarioParams(
        revenue_growth_override=None,
        revenue_growth_scale=1.0,
        growth_fade_per_year=0.9,
        terminal_multiple_scale_vs_peer=1.0,
        own_multiple_blend=0.5,
        net_debt_growth_per_year=0.0,
        sgr_cap_multiplier=10.0,
    )


def test_short_term_outlook_compares_forecast_to_actual() -> None:
    outlook = build_short_term_outlook(
        {
            "close": 100.0,
            "total_revenue_ttm": 1000.0,
            "total_revenue_fq": 250.0,
            "revenue_forecast_fq": 240.0,
            "revenue_forecast_next_fq": 275.0,
            "revenue_forecast_next_fy": 1200.0,
            "earnings_per_share_fq": 1.0,
            "earnings_per_share_forecast_fq": 0.9,
            "earnings_per_share_forecast_next_fq": 1.1,
            "earnings_per_share_diluted_ttm": 4.0,
            "earnings_per_share_forecast_next_fy": 4.8,
            "price_earnings_forward_fy": 20.0,
            "price_target_median": 120.0,
            "price_target_1y": 130.0,
            "earnings_release_next_calendar_date": "2026-08-15",
        },
        close=100.0,
        model_y1_price=110.0,
        model_y1_upside_pct=10.0,
    )
    assert outlook["total_revenue_fq"] == 250.0
    assert outlook["revenue_forecast_next_fy"] == 1200.0
    assert abs(outlook["st_next_fq_rev_growth_pct"] - 10.0) < 1e-9
    assert abs(outlook["st_next_fq_eps_growth_pct"] - 10.0) < 1e-9
    assert abs(outlook["st_fq_eps_vs_forecast_pct"] - (1.0 / 0.9 - 1.0) * 100.0) < 1e-6
    assert abs(outlook["st_next_fy_rev_growth_pct"] - 20.0) < 1e-9
    assert abs(outlook["st_next_fy_eps_growth_pct"] - 20.0) < 1e-9
    assert abs(outlook["st_street_price_upside_pct"] - 20.0) < 1e-9
    assert abs(outlook["st_fy_eps_implied_price"] - 96.0) < 1e-9
    assert outlook["st_fy_eps_implied_usable"] is True
    assert outlook["st_model_y1_upside_pct"] == 10.0
    assert abs(float(outlook["st_model_vs_street_upside_gap_pct"]) - (-10.0)) < 1e-9
    assert outlook["st_outlook"] in {"constructive", "neutral", "cautious"}
    assert outlook["st_earnings_release_next_calendar_date"] == "2026-08-15"
    assert abs(float(outlook["st_rev_eps_divergence_pp"]) - 10.0) < 1e-9


def test_fy_eps_implied_requires_forward_pe_not_ttm() -> None:
    outlook = build_short_term_outlook(
        {
            "close": 100.0,
            "earnings_per_share_diluted_ttm": 5.0,
            "earnings_per_share_forecast_next_fy": 6.0,
            "price_earnings_ttm": 20.0,  # would falsely imply ~20% upside
            # no forward PE
        },
        close=100.0,
    )
    assert outlook["st_fy_eps_implied_usable"] is False
    assert outlook["st_fy_eps_implied_price"] is None
    assert outlook["st_price_earnings_forward_source"] == "missing"


def test_nongaap_forward_pe_used_when_forward_fy_missing() -> None:
    outlook = build_short_term_outlook(
        {
            "close": 100.0,
            "earnings_per_share_forecast_next_fy": 5.0,
            "non_gaap_price_to_earnings_per_share_forecast_next_fy": 22.0,
        },
        close=100.0,
    )
    assert outlook["st_fy_eps_implied_usable"] is True
    assert abs(float(outlook["st_fy_eps_implied_price"]) - 110.0) < 1e-9
    assert (
        outlook["st_price_earnings_forward_source"]
        == "non_gaap_price_to_earnings_per_share_forecast_next_fy"
    )


def test_earnings_date_prefers_release_epoch() -> None:
    outlook = build_short_term_outlook(
        {
            "earnings_release_next_calendar_date": 1785456000,  # 2026-07-31 period-ish
            "earnings_release_next_date": 1787774700,  # later release-ish
        }
    )
    assert outlook["st_earnings_release_next_calendar_date"] == normalize_earnings_date(
        1787774700
    )


def test_projection_summary_includes_short_term_fields() -> None:
    summary, years = project_symbol_scenario(
        {
            "symbol": "NASDAQ:TEST",
            "name": "Test Co",
            "close": 100.0,
            "market_cap_basic": 1_000_000_000.0,
            "total_revenue_ttm": 200_000_000.0,
            "enterprise_value_to_revenue_ttm": 5.0,
            "net_debt": 0.0,
            "total_revenue_yoy_growth_ttm": 20.0,
            "revenue_forecast_next_fy": 240_000_000.0,
            "earnings_per_share_diluted_ttm": 5.0,
            "earnings_per_share_forecast_next_fy": 6.0,
            "price_target_median": 115.0,
            "earnings_per_share_fq": 1.2,
            "earnings_per_share_forecast_next_fq": 1.3,
            "total_revenue_fq": 50_000_000.0,
            "revenue_forecast_next_fq": 55_000_000.0,
            "sector": "Technology Services",
            "industry": "Packaged Software",
        },
        scenario_name="base",
        scenario=_scenario(),
        peer_context={
            "ev_rev_peer_median": 5.0,
            "peer_scope": "industry",
            "peer_n": 20,
            "ev_rev_relative_scale": 1.0,
        },
        horizon_years=5,
    )
    assert summary["valid"] is True
    assert summary["total_revenue_ttm"] == 200_000_000.0
    assert summary["enterprise_value_to_revenue_ttm"] == 5.0
    assert "st_next_fy_rev_growth_pct" in summary
    assert abs(float(summary["st_next_fy_rev_growth_pct"]) - 20.0) < 1e-9
    assert abs(float(summary["st_street_price_upside_pct"]) - 15.0) < 1e-9
    assert summary["model_y1_price"] is not None
    assert summary["st_model_y1_price"] == summary["model_y1_price"]
    assert summary["valuation_lens"] == "ev_revenue"
    assert "st_revenue_fq" not in summary
    assert summary["total_revenue_fq"] == 50_000_000.0
    assert len(years) == 6
