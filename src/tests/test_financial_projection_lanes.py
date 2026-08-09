from financial_projection.config import ScenarioParams
from financial_projection.lanes import (
    build_decision_lanes,
    history_lane_trust,
    peer_lane_trust,
    suggest_history_coeff_adjustments,
)
from financial_projection.model import project_symbol_scenario


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


def test_peer_trust_drops_when_thin_or_clipped() -> None:
    assert (
        peer_lane_trust(
            peer_n=50,
            min_peer_n_trust=15,
            valuation_lens="ev_revenue",
            ev_rev_relative_scale=1.2,
            relative_scale_clip_high=3.0,
        )
        == 1.0
    )
    assert (
        peer_lane_trust(
            peer_n=5,
            min_peer_n_trust=15,
            valuation_lens="ev_revenue",
            ev_rev_relative_scale=1.2,
            relative_scale_clip_high=3.0,
        )
        < 0.5
    )
    assert (
        peer_lane_trust(
            peer_n=50,
            min_peer_n_trust=15,
            valuation_lens="unsuitable",
            ev_rev_relative_scale=1.2,
            relative_scale_clip_high=3.0,
        )
        == 0.0
    )


def test_history_coeff_pulls_down_when_street_above_cagr() -> None:
    adj = suggest_history_coeff_adjustments(
        scenario=_scenario(),
        cagr_fraction=0.08,
        yoy_fraction=0.10,
        street_fy_fraction=0.35,
        peer_cagr_fraction=0.07,
        hist_trust=0.9,
        peer_trust=0.9,
    )
    assert adj["lane_hist_growth_scale_adj"] < 1.0
    assert "street_above_history" in adj["lane_hist_coeff_notes"]
    assert adj["adjusted_scenario"].revenue_growth_scale < 1.0


def test_four_lanes_on_projection_summary() -> None:
    summary, _years = project_symbol_scenario(
        {
            "symbol": "NASDAQ:TEST",
            "name": "Test Co",
            "sector": "Technology Services",
            "industry": "Packaged Software",
            "close": 100.0,
            "market_cap_basic": 1_000_000_000.0,
            "total_revenue_ttm": 200_000_000.0,
            "enterprise_value_to_revenue_ttm": 5.0,
            "net_debt": 0.0,
            "total_revenue_yoy_growth_ttm": 12.0,
            "total_revenue_cagr_5y": 10.0,
            "revenue_forecast_next_fy": 230_000_000.0,
            "price_target_median": 120.0,
            "ebitda": 40_000_000.0,
            "enterprise_value_ebitda_ttm": 18.0,
        },
        scenario_name="base",
        scenario=_scenario(),
        peer_context={
            "peer_scope": "industry_mcap",
            "peer_n": 40,
            "ev_rev_peer_median": 4.0,
            "ev_ebitda_peer_median": 15.0,
            "rev_growth_peer_median": 8.0,
            "rev_cagr_peer_median": 9.0,
            "ev_rev_relative_scale": 1.25,
            "rev_growth_relative_scale": 1.1,
            "rev_cagr_relative_scale": 1.11,
        },
        horizon_years=5,
    )
    assert summary["valid"] is True
    assert summary["lane_core_upside_pct"] == summary["terminal_upside_pct"]
    assert summary["lane_street_upside_pct"] == summary["st_street_price_upside_pct"]
    assert summary["total_revenue_cagr_5y"] == 10.0
    assert summary["lane_hist_trust"] > 0
    assert summary["lane_peer_trust"] > 0
    assert summary["lane_peer_scope"] == "industry_mcap"
    assert summary["lane_hist_adjusted_upside_pct"] is not None
    assert "lane_hist_vs_core_gap_pp" in summary


def test_build_decision_lanes_standalone() -> None:
    lanes = build_decision_lanes(
        row={
            "total_revenue_cagr_5y": 15.0,
            "total_revenue_yoy_growth_ttm": 40.0,
        },
        summary={
            "terminal_upside_pct": 20.0,
            "model_y1_upside_pct": 5.0,
            "st_street_price_upside_pct": 25.0,
            "st_next_fy_rev_growth_pct": 30.0,
            "st_outlook": "constructive",
            "street_fy_rev_growth_fraction": 0.30,
            "peer_scope": "industry",
            "peer_n": 20,
            "ev_rev_peer_median": 4.0,
            "rev_cagr_peer_median": 10.0,
            "ev_rev_relative_scale": 1.5,
            "rev_cagr_relative_scale": 1.5,
            "valuation_lens": "ev_revenue",
        },
        scenario=_scenario(),
    )
    assert lanes["lane_core_source"] == "global_scenario_coeffs"
    assert lanes["lane_street_source"] == "price_target_and_forecasts"
    assert lanes["lane_hist_source"] == "cagr_yoy_persistence"
    assert lanes["lane_peer_source"] == "industry_mcap_sector_global"
    assert history_lane_trust(
        peer_trust=1.0, cagr_fraction=0.15, yoy_fraction=0.40
    ) < history_lane_trust(
        peer_trust=1.0, cagr_fraction=0.15, yoy_fraction=0.16
    )
