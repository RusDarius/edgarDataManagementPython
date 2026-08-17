from financial_projection.config import load_projection_config
from financial_projection.growth_lanes import (
    GROWTH_LANE_OWN,
    GROWTH_LANE_PEER,
    GROWTH_LANE_UNIVERSE,
    estimate_universe_growth_priors,
    project_symbol_growth_lanes,
    resolve_own_revenue_growth,
)


def _row(**overrides: object) -> dict:
    row = {
        "symbol": "NASDAQ:TEST",
        "name": "Test Co",
        "sector": "Technology Services",
        "industry": "Packaged Software",
        "close": 100.0,
        "market_cap_basic": 1_000_000_000.0,
        "total_revenue_ttm": 200_000_000.0,
        "ebitda": 40_000_000.0,
        "ebitda_ttm": 40_000_000.0,
        "ebit_ttm": 30_000_000.0,
        "oper_income_ttm": 30_000_000.0,
        "net_income_ttm": 20_000_000.0,
        "ebitda_margin_ttm": 20.0,
        "operating_margin_ttm": 15.0,
        "net_margin_ttm": 10.0,
        "total_revenue_yoy_growth_ttm": 20.0,
        "total_revenue_cagr_5y": 12.0,
        "revenue_forecast_next_fy": 240_000_000.0,
    }
    row.update(overrides)
    return row


def _peer(**overrides: object) -> dict:
    peer = {
        "peer_scope": "industry",
        "peer_n": 25,
        "rev_growth_peer_median": 10.0,
        "rev_cagr_peer_median": 9.0,
        "peer_metric_medians": {
            "total_revenue_yoy_growth_ttm": 10.0,
            "total_revenue_cagr_5y": 9.0,
            "ebitda_margin_ttm": 25.0,
            "operating_margin_ttm": 18.0,
            "net_margin_ttm": 12.0,
        },
    }
    peer.update(overrides)
    return peer


def test_own_growth_prefers_forecast() -> None:
    result = resolve_own_revenue_growth(
        _row(),
        forecast_growth_max_fraction=1.5,
        max_starting_growth_fraction=1.0,
    )
    assert result["own_revenue_growth_source"] == "revenue_forecast_next_fy"
    assert result["own_revenue_growth_fraction"] is not None
    assert abs(result["own_revenue_growth_fraction"] - 0.20) < 1e-9


def test_three_growth_lanes_emit_summary_and_year_grid() -> None:
    config = load_projection_config()
    summaries, year_grids = project_symbol_growth_lanes(
        _row(),
        peer_context=_peer(),
        config=config,
        universe_growth_fraction=0.08,
        universe_ebitda_margin=0.22,
        universe_operating_margin=0.16,
        universe_net_margin=0.11,
    )
    lanes = {row["growth_lane"] for row in summaries if row.get("scenario") == "base"}
    assert lanes == {
        GROWTH_LANE_UNIVERSE,
        GROWTH_LANE_OWN,
        GROWTH_LANE_PEER,
    }
    assert all(row.get("valid") for row in summaries)
    own_base = next(
        row
        for row in summaries
        if row["growth_lane"] == GROWTH_LANE_OWN and row["scenario"] == "base"
    )
    assert own_base["revenue_cagr_implied_pct"] is not None
    assert own_base["terminal_total_revenue_ttm"] is not None
    assert own_base["terminal_ebitda"] is not None
    assert own_base["terminal_ebit_ttm"] is not None
    assert own_base["terminal_net_income_ttm"] is not None

    own_years = [
        row
        for row in year_grids
        if row["growth_lane"] == GROWTH_LANE_OWN and row["scenario"] == "base"
    ]
    assert [row["year"] for row in own_years] == list(range(0, config.horizon_years + 1))
    assert own_years[-1]["revenue_growth_vs_y0_pct"] is not None


def test_peer_lane_blends_toward_peer_margins() -> None:
    config = load_projection_config()
    summaries, _ = project_symbol_growth_lanes(
        _row(ebitda_margin_ttm=10.0, operating_margin_ttm=8.0, net_margin_ttm=5.0),
        peer_context=_peer(),
        config=config,
        universe_growth_fraction=0.08,
        universe_ebitda_margin=0.22,
        universe_operating_margin=0.16,
        universe_net_margin=0.11,
    )
    peer_base = next(
        row
        for row in summaries
        if row["growth_lane"] == GROWTH_LANE_PEER and row["scenario"] == "base"
    )
    own_base = next(
        row
        for row in summaries
        if row["growth_lane"] == GROWTH_LANE_OWN and row["scenario"] == "base"
    )
    assert peer_base["terminal_ebitda_margin_ttm"] is not None
    assert own_base["terminal_ebitda_margin_ttm"] is not None
    # Peer fades harder toward higher peer margins than own persistence.
    assert peer_base["terminal_ebitda_margin_ttm"] > own_base["terminal_ebitda_margin_ttm"]


def test_estimate_universe_growth_priors() -> None:
    priors = estimate_universe_growth_priors(
        {
            "A": _peer(rev_growth_peer_median=8.0),
            "B": _peer(rev_growth_peer_median=12.0),
        }
    )
    assert priors["universe_growth_fraction"] is not None
    assert 0.07 < float(priors["universe_growth_fraction"]) < 0.13
    assert priors["universe_ebitda_margin"] is not None
