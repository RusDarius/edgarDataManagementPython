"""Multi-view peer scales and custom peer-group mode."""

from financial_projection.peer_scales import (
    PEER_VIEW_CUSTOM,
    PEER_VIEW_GROWTH,
    PEER_VIEW_INDUSTRY,
    PEER_VIEW_MCAP,
    PEER_VIEW_PROFITABLE,
    build_peer_scale_context,
    view_trust_score,
)


def _row(
    *,
    symbol: str,
    market_cap_basic: float = 2_000_000_000.0,
    total_revenue_ttm: float = 500_000_000.0,
    enterprise_value_to_revenue_ttm: float = 5.0,
    total_revenue_yoy_growth_ttm: float = 12.0,
    ebitda: float = 80_000_000.0,
    industry: str = "Packaged Software",
    sector: str = "Technology Services",
) -> dict:
    return {
        "symbol": symbol,
        "name": symbol,
        "sector": sector,
        "industry": industry,
        "market_cap_basic": market_cap_basic,
        "total_revenue_ttm": total_revenue_ttm,
        "enterprise_value_to_revenue_ttm": enterprise_value_to_revenue_ttm,
        "total_revenue_yoy_growth_ttm": total_revenue_yoy_growth_ttm,
        "total_revenue_cagr_5y": total_revenue_yoy_growth_ttm * 0.9,
        "ebitda": ebitda,
        "enterprise_value_ebitda_ttm": 20.0 if ebitda > 0 else None,
    }


def test_industry_is_default_with_parallel_views() -> None:
    rows = []
    for i in range(30):
        rows.append(
            _row(
                symbol=f"NASDAQ:M{i}",
                market_cap_basic=1_500_000_000.0 + i * 50_000_000.0,
                total_revenue_ttm=400_000_000.0 + i * 10_000_000.0,
                enterprise_value_to_revenue_ttm=4.0 + (i % 5) * 0.3,
                total_revenue_yoy_growth_ttm=8.0 + (i % 4),
                ebitda=50_000_000.0,
            )
        )
    # Cap ladder so mcap banding has enough large peers.
    for i in range(12):
        rows.append(
            _row(
                symbol=f"NASDAQ:L{i}",
                market_cap_basic=40_000_000_000.0 + i * 8_000_000_000.0,
                total_revenue_ttm=8_000_000_000.0 + i * 500_000_000.0,
                enterprise_value_to_revenue_ttm=5.0 + i * 0.2,
                total_revenue_yoy_growth_ttm=11.0 + (i % 3),
                ebitda=1_000_000_000.0,
            )
        )
    # Mature large name
    rows.append(
        _row(
            symbol="NASDAQ:ADBE_L",
            market_cap_basic=80_000_000_000.0,
            total_revenue_ttm=20_000_000_000.0,
            enterprise_value_to_revenue_ttm=4.5,
            total_revenue_yoy_growth_ttm=10.0,
            ebitda=5_000_000_000.0,
        )
    )
    # Hypergrowth mid name
    rows.append(
        _row(
            symbol="NASDAQ:DDOG_L",
            market_cap_basic=30_000_000_000.0,
            total_revenue_ttm=2_000_000_000.0,
            enterprise_value_to_revenue_ttm=18.0,
            total_revenue_yoy_growth_ttm=35.0,
            ebitda=100_000_000.0,
        )
    )
    # Unprofitable growth names to pollute industry median a bit
    for i in range(10):
        rows.append(
            _row(
                symbol=f"NASDAQ:U{i}",
                market_cap_basic=800_000_000.0 + i * 20_000_000.0,
                total_revenue_ttm=120_000_000.0,
                enterprise_value_to_revenue_ttm=12.0 + i,
                total_revenue_yoy_growth_ttm=40.0,
                ebitda=-10_000_000.0,
            )
        )

    ctx = build_peer_scale_context(rows, min_peer_group_size=8, min_peer_n_trust=15)
    adbe = ctx["NASDAQ:ADBE_L"]
    ddog = ctx["NASDAQ:DDOG_L"]

    assert adbe["peer_scope"] == PEER_VIEW_INDUSTRY
    assert PEER_VIEW_MCAP in adbe["peer_views"]
    assert PEER_VIEW_GROWTH in adbe["peer_views"]
    assert PEER_VIEW_PROFITABLE in adbe["peer_views"]
    assert adbe["peer_dispersion_iqr_over_median"] is not None

    # Growth view should separate maturity classes when both exist.
    assert PEER_VIEW_GROWTH in ddog["peer_views"]
    growth_rel_adbe = adbe["peer_views"][PEER_VIEW_GROWTH]["ev_rev_relative_scale"]
    ind_rel_ddog = ddog["ev_rev_relative_scale"]
    assert ind_rel_ddog is not None
    # DDOG is rich vs full industry; growth view should usually be closer to 1.
    assert ddog["peer_views"][PEER_VIEW_GROWTH]["ev_rev_relative_scale"] <= ind_rel_ddog
    assert growth_rel_adbe is not None


def test_custom_peer_mode_uses_custom_group_default() -> None:
    rows = [
        _row(
            symbol=f"NASDAQ:C{i}",
            market_cap_basic=5_000_000_000.0 + i * 1_000_000_000.0,
            enterprise_value_to_revenue_ttm=6.0 + i * 0.2,
            total_revenue_yoy_growth_ttm=10.0 + i,
        )
        for i in range(12)
    ]
    ctx = build_peer_scale_context(
        rows,
        min_peer_group_size=8,
        custom_peer_mode=True,
    )
    sample = ctx["NASDAQ:C0"]
    assert sample["peer_scope"] == PEER_VIEW_CUSTOM
    assert PEER_VIEW_CUSTOM in sample["peer_views"]
    assert sample["custom_peer_mode"] is True


def test_view_trust_penalizes_dispersion_and_clip() -> None:
    base = view_trust_score(
        peer_n=40,
        min_peer_n_trust=15,
        valuation_lens="ev_revenue",
        ev_rev_relative_scale=1.1,
        relative_scale_clip_high=3.0,
        dispersion_iqr_over_median=0.4,
    )
    dispersed = view_trust_score(
        peer_n=40,
        min_peer_n_trust=15,
        valuation_lens="ev_revenue",
        ev_rev_relative_scale=1.1,
        relative_scale_clip_high=3.0,
        dispersion_iqr_over_median=2.2,
    )
    clipped = view_trust_score(
        peer_n=40,
        min_peer_n_trust=15,
        valuation_lens="ev_revenue",
        ev_rev_relative_scale=3.0,
        relative_scale_clip_high=3.0,
        dispersion_iqr_over_median=0.4,
    )
    assert dispersed < base
    assert clipped < base
