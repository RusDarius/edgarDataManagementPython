from financial_projection.config import ScenarioParams, load_projection_config
from financial_projection.diagnostics import (
    build_case_diagnostics,
    resolve_valuation_lens,
)
from financial_projection.model import (
    project_symbol_scenario,
    project_universe,
    resolve_starting_revenue_growth,
)
from financial_projection.peer_scales import build_peer_scale_context
from financial_projection.short_term_outlook import normalize_earnings_date


def _scenario(**overrides: float | None) -> ScenarioParams:
    base = {
        "revenue_growth_override": None,
        "revenue_growth_scale": 1.0,
        "growth_fade_per_year": 0.9,
        "terminal_multiple_scale_vs_peer": 1.0,
        "own_multiple_blend": 0.5,
        "net_debt_growth_per_year": 0.0,
        "sgr_cap_multiplier": 10.0,
    }
    base.update(overrides)
    return ScenarioParams(**base)  # type: ignore[arg-type]


def _row(**overrides: object) -> dict:
    row = {
        "symbol": "NASDAQ:TEST",
        "name": "Test Co",
        "exchange": "NASDAQ",
        "market": "america",
        "sector": "Technology Services",
        "industry": "Packaged Software",
        "close": 100.0,
        "market_cap_basic": 1_000_000_000.0,
        "total_revenue_ttm": 200_000_000.0,
        "enterprise_value_to_revenue_ttm": 5.0,
        "net_debt": 100_000_000.0,
        "total_revenue_yoy_growth_ttm": 20.0,  # percent points
        "ebitda": 40_000_000.0,
        "enterprise_value_ebitda_ttm": 20.0,
        "sustainable_growth_rate_ttm": None,
    }
    row.update(overrides)
    return row


def test_resolve_starting_growth_prefers_forecast() -> None:
    result = resolve_starting_revenue_growth(
        _row(
            total_revenue_ttm=100.0,
            revenue_forecast_next_fy=120.0,
            total_revenue_yoy_growth_ttm=5.0,
        )
    )
    assert result.source == "revenue_forecast_next_fy"
    assert result.growth is not None
    assert abs(result.growth - 0.20) < 1e-9
    assert result.forecast_rejected is False
    assert result.street_fy_rev_growth_fraction is not None
    assert abs(result.street_fy_rev_growth_fraction - 0.20) < 1e-9


def test_absurd_forecast_falls_back_and_surfaces_rejection() -> None:
    result = resolve_starting_revenue_growth(
        _row(
            total_revenue_ttm=1_000_000.0,
            revenue_forecast_next_fy=700_000_000.0,
            total_revenue_yoy_growth_ttm=250.0,  # 250% -> clipped to 100%
        ),
        forecast_growth_max_fraction=1.5,
        max_starting_growth_fraction=1.0,
    )
    assert result.forecast_rejected is True
    assert "fallback_yoy" in result.source
    assert result.street_fy_rev_growth_fraction is not None
    assert result.street_fy_rev_growth_fraction > 1.5
    assert result.growth is not None
    assert abs(result.growth - 1.0) < 1e-9


def test_growth_fade_and_peer_blend_produce_valid_grid() -> None:
    summary, years = project_symbol_scenario(
        _row(),
        scenario_name="base",
        scenario=_scenario(growth_fade_per_year=0.9, own_multiple_blend=0.5),
        peer_context={
            "peer_scope": "industry",
            "peer_n": 20,
            "ev_rev_peer_median": 4.0,
            "ev_ebitda_peer_median": 15.0,
            "rev_growth_peer_median": 10.0,
            "ev_rev_relative_scale": 1.25,
        },
        horizon_years=5,
        config=load_projection_config(),
    )
    assert summary["valid"] is True
    assert summary["total_revenue_ttm"] == 200_000_000.0
    assert summary["enterprise_value_to_revenue_ttm"] == 5.0
    assert summary["net_debt"] == 100_000_000.0
    assert "revenue_0" not in summary
    assert "ev_rev_0" not in summary
    assert len(years) == 6  # t=0..5
    assert years[0]["year"] == 0
    assert years[0]["growth_fraction"] is None
    assert abs(years[1]["growth_fraction"] - 0.20) < 1e-9
    assert abs(years[2]["growth_fraction"] - 0.18) < 1e-9
    assert years[-1]["price"] is not None
    assert summary["terminal_upside_pct"] is not None
    # start multiple blend: 0.5*5 + 0.5*4 = 4.5
    assert abs(years[0]["enterprise_value_to_revenue_ttm"] - 4.5) < 1e-9
    assert summary["valuation_lens"] == "ev_revenue"
    assert "rank_eligible" in summary


def test_non_positive_equity_marked_invalid() -> None:
    summary, years = project_symbol_scenario(
        _row(
            enterprise_value_to_revenue_ttm=0.5,
            net_debt=5_000_000_000.0,
            total_revenue_ttm=100_000_000.0,
        ),
        scenario_name="bear",
        scenario=_scenario(
            revenue_growth_scale=0.1,
            terminal_multiple_scale_vs_peer=0.5,
            own_multiple_blend=1.0,
        ),
        peer_context={"ev_rev_peer_median": 0.5, "peer_scope": "global", "peer_n": 100},
        horizon_years=5,
    )
    assert summary["valid"] is False
    assert summary["invalid_reason"] == "non_positive_equity"
    assert years  # grid still produced for inspection


def test_project_universe_emits_three_scenarios_and_width() -> None:
    config = load_projection_config()
    rows = [
        _row(symbol="NASDAQ:AAA", industry="Packaged Software"),
        _row(symbol="NASDAQ:BBB", industry="Packaged Software", close=50.0),
    ]
    for row in rows:
        row["enterprise_value_to_revenue_ttm"] = 5.0
        row["total_revenue_yoy_growth_ttm"] = 20.0
    peer_rows = [
        _row(
            symbol=f"NASDAQ:P{i}",
            enterprise_value_to_revenue_ttm=4.0 + (i % 3),
            total_revenue_yoy_growth_ttm=10.0 + i,
        )
        for i in range(12)
    ]
    peer_context = build_peer_scale_context(
        rows + peer_rows,
        min_peer_group_size=8,
        peer_trim_fraction=0.05,
    )
    summaries, year_grids = project_universe(
        rows, config=config, peer_context_by_symbol=peer_context
    )
    assert len(summaries) == 6  # 2 symbols × 3 scenarios
    assert {row["scenario"] for row in summaries} == {"bear", "base", "bull"}
    assert len(year_grids) == 6 * (config.horizon_years + 1)
    assert all("scenario_width_y5_pp" in row for row in summaries)


def test_valuation_lens_routes_finance_and_managed_care() -> None:
    assert (
        resolve_valuation_lens(
            industry="Packaged Software", sector="Technology Services"
        )
        == "ev_revenue"
    )
    assert (
        resolve_valuation_lens(
            industry="Managed Health Care", sector="Health Services"
        )
        == "earnings"
    )
    assert (
        resolve_valuation_lens(industry="Major Banks", sector="Finance")
        == "unsuitable"
    )


def test_rich_growth_and_outlier_diagnostics() -> None:
    rich = build_case_diagnostics(
        industry="Packaged Software",
        sector="Technology Services",
        valid=True,
        terminal_upside_pct=-40.0,
        enterprise_value_to_revenue_ttm=20.0,
        ev_rev_relative_scale=3.0,
        relative_scale_clip_high=3.0,
        street_fy_rev_growth_pct=25.0,
        next_fq_eps_growth_pct=5.0,
        street_price_upside_pct=40.0,
        fy_eps_implied_upside_pct=None,
        fy_eps_implied_usable=False,
        ev_ebitda_crosscheck_upside_pct=10.0,
        peer_n=50,
        forecast_rejected=False,
    )
    assert rich["valuation_regime"] == "rich_growth"
    assert rich["rank_eligible"] is False
    assert rich["primary_upside_source"] == "street_target_rich_growth"

    outlier = build_case_diagnostics(
        industry="Packaged Software",
        sector="Technology Services",
        valid=True,
        terminal_upside_pct=900.0,
        enterprise_value_to_revenue_ttm=0.8,
        ev_rev_relative_scale=0.5,
        relative_scale_clip_high=3.0,
        street_fy_rev_growth_pct=5.0,
        next_fq_eps_growth_pct=2.0,
        street_price_upside_pct=10.0,
        fy_eps_implied_upside_pct=None,
        fy_eps_implied_usable=False,
        ev_ebitda_crosscheck_upside_pct=20.0,
        peer_n=50,
        forecast_rejected=False,
    )
    assert outlier["outlier_flag"] is True
    assert outlier["rank_eligible"] is False


def test_normalize_earnings_epoch() -> None:
    assert normalize_earnings_date(1785456000) == "2026-07-31"
    assert normalize_earnings_date("2026-08-15") == "2026-08-15"


def test_peer_mcap_refinement_for_large_industry() -> None:
    rows = []
    for i in range(50):
        rows.append(
            _row(
                symbol=f"NASDAQ:S{i}",
                market_cap_basic=1_000_000_000.0 * (1.0 + (i % 5) * 0.1),
                enterprise_value_to_revenue_ttm=4.0 + i * 0.01,
            )
        )
    rows.append(
        _row(
            symbol="NASDAQ:MEGA",
            market_cap_basic=80_000_000_000.0,
            enterprise_value_to_revenue_ttm=12.0,
        )
    )
    for i in range(10):
        rows.append(
            _row(
                symbol=f"NASDAQ:L{i}",
                market_cap_basic=40_000_000_000.0 + i * 5_000_000_000.0,
                enterprise_value_to_revenue_ttm=8.0 + i * 0.2,
            )
        )
    ctx = build_peer_scale_context(
        rows,
        min_peer_group_size=8,
        peer_mcap_refine_min_industry_n=40,
        peer_mcap_band_low=0.25,
        peer_mcap_band_high=4.0,
    )
    assert ctx["NASDAQ:MEGA"]["peer_scope"] == "industry_mcap"
    assert ctx["NASDAQ:MEGA"]["peer_n"] < ctx["NASDAQ:S0"]["peer_n"] or ctx[
        "NASDAQ:S0"
    ]["peer_scope"] in {"industry", "industry_mcap"}
