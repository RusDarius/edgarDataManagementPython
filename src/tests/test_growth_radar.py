"""Growth radar lanes stay separate. No blended score."""

from generic_utils.growth_radar import (
    GrowthRadarSpec,
    build_growth_tables,
    prepare_growth_row,
    prepare_universe,
    quote_earnings_growth,
    quote_revenue_growth,
)


def test_market_cap_is_not_applied_inside_the_rank() -> None:
    """Threshold 1 is the loader's job. A prepared small name can still be ranked in a unit test."""
    row = prepare_growth_row(
        {
            "symbol": "NASDAQ:TINY",
            "industry": "Software",
            "market_cap_basic": 50_000_000,
            "total_revenue_yoy_growth_ttm": 40,
            "net_income_yoy_growth_ttm": 10,
        }
    )
    assert row["mcap"] == 50_000_000
    assert row["rev_growth"] == 40


def test_revenue_quote_prefers_the_projection_step() -> None:
    value, source = quote_revenue_growth(
        {"fp_rev_y1": 130, "total_revenue_ttm": 100, "total_revenue_yoy_growth_ttm": 5}
    )
    assert source == "fp_rev_y1"
    assert value == 30.0


def test_earnings_quote_skips_a_negative_trailing_base() -> None:
    value, source = quote_earnings_growth(
        {
            "fp_ni_y1": 20,
            "net_income_ttm": -5,
            "net_income_yoy_growth_ttm": 25,
        }
    )
    assert source == "earn_yoy"
    assert value == 25.0


def test_two_lanes_do_not_blend() -> None:
    spec = GrowthRadarSpec(min_growth_pct=15, top=10, industry_cap=4)
    rows = prepare_universe(
        [
            {"symbol": "NASDAQ:REV", "industry": "Software", "total_revenue_yoy_growth_ttm": 30, "net_income_yoy_growth_ttm": 4},
            {"symbol": "NASDAQ:EARN", "industry": "Software", "total_revenue_yoy_growth_ttm": 4, "net_income_yoy_growth_ttm": 40},
            {"symbol": "NASDAQ:BOTH", "industry": "Hardware", "total_revenue_yoy_growth_ttm": 22, "net_income_yoy_growth_ttm": 28},
            {"symbol": "NASDAQ:SLOW", "industry": "Hardware", "total_revenue_yoy_growth_ttm": 3, "net_income_yoy_growth_ttm": 3},
        ]
    )
    tables = build_growth_tables(rows, spec)
    revenue = {row["symbol"] for row in tables["revenue_growth"]}
    earnings = {row["symbol"] for row in tables["earnings_delivery"]}
    assert revenue == {"NASDAQ:REV", "NASDAQ:BOTH"}
    assert earnings == {"NASDAQ:EARN", "NASDAQ:BOTH"}
    assert "NASDAQ:SLOW" not in revenue
    assert "NASDAQ:SLOW" not in earnings
    assert all("_composite_score" not in row for row in tables["revenue_growth"])
    both = next(row for row in tables["revenue_growth"] if row["symbol"] == "NASDAQ:BOTH")
    assert both["lanes"] == "revenue+earnings"
    assert both["earn_outrun"] is False


def test_earnings_lane_ranks_by_earnings_growth() -> None:
    spec = GrowthRadarSpec(min_growth_pct=15, top=5, industry_cap=5)
    rows = prepare_universe(
        [
            {"symbol": "NASDAQ:A", "industry": "Software", "net_income_yoy_growth_ttm": 20, "total_revenue_yoy_growth_ttm": 40},
            {"symbol": "NASDAQ:B", "industry": "Hardware", "net_income_yoy_growth_ttm": 50, "total_revenue_yoy_growth_ttm": 16},
        ]
    )
    tables = build_growth_tables(rows, spec)
    assert [row["symbol"] for row in tables["earnings_delivery"]] == ["NASDAQ:B", "NASDAQ:A"]
    assert tables["earnings_delivery"][0]["earn_outrun"] is True


def test_overlooked_needs_a_forward_discount_and_unused_range() -> None:
    spec = GrowthRadarSpec(min_growth_pct=15, overlooked_rng_max=60)
    cheap = prepare_growth_row(
        {
            "symbol": "NASDAQ:QUIET",
            "industry": "Software",
            "total_revenue_yoy_growth_ttm": 25,
            "close": 10,
            "price_52_week_low": 8,
            "price_52_week_high": 40,
        }
    )
    cheap["pe_fwd_vs_ind"] = -20
    cheap["pe_fwd_peer"] = "DISCOUNT"
    paid = dict(cheap)
    paid["symbol"] = "NASDAQ:LOUD"
    paid["rng"] = 90
    tables = build_growth_tables([cheap, paid], spec)
    flags = {row["symbol"]: row["overlooked"] for row in tables["revenue_growth"]}
    assert flags["NASDAQ:QUIET"] is True
    assert flags["NASDAQ:LOUD"] is False


def test_clipped_projection_and_base_effect_fall_through() -> None:
    value, source = quote_revenue_growth(
        {
            "fp_rev_y1": 250,
            "total_revenue_ttm": 100,
            "total_revenue_yoy_growth_ttm": 40,
        }
    )
    assert source == "rev_yoy"
    assert value == 40.0
    value, source = quote_earnings_growth(
        {"net_income_yoy_growth_ttm": 19900, "net_income_cagr_5y": 25}
    )
    assert source == "earn_cagr"
    assert value == 25.0


def test_fractional_yoy_is_scaled_to_percent() -> None:
    value, source = quote_revenue_growth({"total_revenue_yoy_growth_ttm": 0.18})
    assert source == "rev_yoy"
    assert value == 18.0
