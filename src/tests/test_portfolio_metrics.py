from datetime import datetime

from portofolio_integration_analysis.portfolio_metrics import (
    PortfolioPositionRecord,
    build_portfolio_summary,
)


def test_build_portfolio_summary_aggregates_market_value_and_returns():
    positions = [
        PortfolioPositionRecord(
            portfolio_item_id=1,
            portfolio_id=7,
            portfolio_name="Core Compounders",
            company_internal_id=10,
            symbol="AAA",
            company_name="Alpha Inc.",
            shares_held=10,
            average_entry_price=100.0,
            cost_basis_total=1000.0,
            realized_pnl=50.0,
            last_price=120.0,
            last_change_pct=3.0,
            last_perf_w=5.0,
            last_perf_1m=12.0,
            last_perf_y=30.0,
            opened_at=datetime(2025, 1, 1),
        ),
        PortfolioPositionRecord(
            portfolio_item_id=2,
            portfolio_id=7,
            portfolio_name="Core Compounders",
            company_internal_id=11,
            symbol="BBB",
            company_name="Beta Corp.",
            shares_held=5,
            average_entry_price=200.0,
            cost_basis_total=1000.0,
            realized_pnl=-25.0,
            last_price=180.0,
            last_change_pct=-1.5,
            last_perf_w=-2.0,
            last_perf_1m=4.0,
            last_perf_y=15.0,
            opened_at=datetime(2024, 6, 1),
        ),
    ]

    summary, metrics = build_portfolio_summary(positions, as_of=datetime(2026, 1, 1))

    assert summary is not None
    assert len(metrics) == 2
    assert round(summary.total_cost_basis, 2) == 2000.0
    assert round(summary.total_market_value or 0, 2) == 2100.0
    assert round(summary.total_unrealized_pnl or 0, 2) == 100.0
    assert round(summary.total_realized_pnl, 2) == 25.0
    assert round(summary.total_pnl or 0, 2) == 125.0
    assert round(summary.total_return_pct or 0, 2) == 6.25
    assert round(summary.price_coverage_ratio, 2) == 1.0
    assert round(summary.largest_position_weight or 0, 4) == round(1200.0 / 2100.0, 4)
    assert summary.average_holding_days is not None
    assert summary.weighted_holding_days is not None
    assert summary.annualized_total_return_pct is not None
    assert metrics[0].holding_days is not None
    assert metrics[0].annualized_return_pct is not None


def test_build_portfolio_summary_handles_missing_prices():
    positions = [
        PortfolioPositionRecord(
            portfolio_item_id=3,
            portfolio_id=9,
            portfolio_name="Unpriced Names",
            company_internal_id=12,
            symbol="CCC",
            company_name="Gamma Ltd.",
            shares_held=8,
            average_entry_price=50.0,
            cost_basis_total=400.0,
            opened_at=datetime(2025, 6, 1),
        )
    ]

    summary, metrics = build_portfolio_summary(positions, as_of=datetime(2026, 1, 1))

    assert summary is not None
    assert len(metrics) == 1
    assert summary.total_market_value is None
    assert summary.total_unrealized_pnl is None
    assert summary.total_pnl is None
    assert summary.total_return_pct is None
    assert summary.price_coverage_ratio == 0.0
    assert metrics[0].holding_days is not None


def test_build_portfolio_summary_computes_holding_period_metrics():
    positions = [
        PortfolioPositionRecord(
            portfolio_item_id=4,
            portfolio_id=10,
            portfolio_name="Holding Period Test",
            company_internal_id=13,
            symbol="DDD",
            company_name="Delta PLC",
            shares_held=4,
            average_entry_price=100.0,
            cost_basis_total=400.0,
            last_price=460.0 / 4,
            opened_at=datetime(2025, 1, 1),
        )
    ]

    summary, metrics = build_portfolio_summary(positions, as_of=datetime(2026, 1, 1))

    assert summary is not None
    assert round(metrics[0].holding_days or 0, 1) == 365.0
    assert round(metrics[0].total_return_pct or 0, 2) == 15.0
    assert round(metrics[0].annualized_return_pct or 0, 2) == 15.0
