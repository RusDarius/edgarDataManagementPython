from datetime import datetime

from portfolio_performance_tracking.performance_calculator import (
    CashFlowPoint,
    build_cumulative_performance_series,
    compute_active_capital_timeline,
    modified_dietz_return,
)
from portfolio_performance_tracking.benchmark_comparator import (
    build_shadow_benchmark_series,
)


def test_modified_dietz_return_weights_flow_by_time_remaining():
    period_start = datetime(2025, 1, 1)
    period_end = datetime(2025, 2, 1)  # 31 days
    flows = [CashFlowPoint(executed_at=datetime(2025, 1, 15), amount=5000.0)]

    result = modified_dietz_return(
        begin_value=10_000.0,
        end_value=15_500.0,
        flows=flows,
        period_start=period_start,
        period_end=period_end,
    )

    assert result is not None
    assert round(result, 3) == 3.924


def test_build_cumulative_performance_series_chains_sub_periods():
    nav_snapshots = [
        {"snapshot_at": datetime(2025, 1, 1), "total_nav": 10_000.0},
        {"snapshot_at": datetime(2025, 2, 1), "total_nav": 15_500.0},
        {"snapshot_at": datetime(2025, 3, 1), "total_nav": 16_000.0},
    ]
    external_flows = [
        {"executed_at": datetime(2025, 1, 15), "amount": 5000.0},
    ]

    series = build_cumulative_performance_series(nav_snapshots, external_flows)

    assert len(series) == 3
    assert series[0].period_return_pct is None
    assert series[0].cumulative_return_pct == 0.0
    assert round(series[1].cumulative_return_pct, 3) == 3.924
    # Second sub-period has no flows, so period return is a plain NAV ratio.
    assert round(series[2].period_return_pct, 4) == round(
        (16_000.0 - 15_500.0) / 15_500.0 * 100.0, 4
    )
    # Cumulative growth is compounded (geometrically linked), not summed.
    expected_cumulative = (
        (1 + series[1].period_return_pct / 100.0)
        * (1 + series[2].period_return_pct / 100.0)
        - 1
    ) * 100.0
    assert round(series[2].cumulative_return_pct, 6) == round(expected_cumulative, 6)


def test_build_cumulative_performance_series_tracks_contributions_and_withdrawals():
    nav_snapshots = [
        {"snapshot_at": datetime(2025, 1, 1), "total_nav": 10_000.0},
        {"snapshot_at": datetime(2025, 2, 1), "total_nav": 14_500.0},
        {"snapshot_at": datetime(2025, 3, 1), "total_nav": 13_000.0},
    ]
    external_flows = [
        {"executed_at": datetime(2025, 1, 15), "amount": 5000.0},
        {"executed_at": datetime(2025, 2, 15), "amount": -1000.0},
    ]

    series = build_cumulative_performance_series(nav_snapshots, external_flows)

    assert series[0].cumulative_contributions == 0.0
    assert series[1].cumulative_contributions == 5000.0
    assert series[2].cumulative_withdrawals == 1000.0


def test_build_shadow_benchmark_series_matches_portfolio_methodology():
    nav_snapshots = [
        {
            "snapshot_at": datetime(2025, 1, 1),
            "total_nav": 10_000.0,
            "benchmark_price": 100.0,
        },
        {
            "snapshot_at": datetime(2025, 2, 1),
            "total_nav": 11_000.0,
            "benchmark_price": 110.0,
        },
    ]
    external_flows = [
        {
            "executed_at": datetime(2025, 1, 1),
            "amount": 10_000.0,
            "benchmark_price": 100.0,
        },
    ]

    comparison = build_shadow_benchmark_series(nav_snapshots, external_flows)

    assert len(comparison) == 2
    # First point: everything is baked into the opening NAV/shadow value (0% by definition).
    assert comparison[0].shadow_benchmark_cumulative_return_pct == 0.0
    assert comparison[0].alpha_pct == 0.0
    # Second point: benchmark rose 10% (100 -> 110), portfolio rose 10% too -> zero alpha.
    assert round(comparison[1].shadow_benchmark_cumulative_return_pct, 3) == 10.0
    assert round(comparison[1].portfolio_cumulative_return_pct, 3) == 10.0
    assert round(comparison[1].alpha_pct, 6) == 0.0


def test_build_shadow_benchmark_series_skips_unpriced_points_gracefully():
    nav_snapshots = [
        {"snapshot_at": datetime(2025, 1, 1), "total_nav": 10_000.0},
        {"snapshot_at": datetime(2025, 2, 1), "total_nav": 11_000.0},
    ]
    external_flows = [
        {"executed_at": datetime(2025, 1, 1), "amount": 10_000.0},
    ]

    comparison = build_shadow_benchmark_series(nav_snapshots, external_flows)

    assert len(comparison) == 2
    assert all(point.shadow_benchmark_value is None for point in comparison)
    assert all(point.alpha_pct is None for point in comparison)
    # Portfolio's own return is unaffected by missing benchmark pricing.
    assert round(comparison[1].portfolio_cumulative_return_pct, 3) == 10.0


def test_compute_active_capital_timeline_tracks_running_capital_and_age():
    flows = [
        {
            "executed_at": datetime(2024, 1, 1),
            "amount": 1000.0,
            "flow_type": "CONTRIBUTION",
        },
        {
            "executed_at": datetime(2024, 6, 1),
            "amount": 500.0,
            "flow_type": "CONTRIBUTION",
        },
        {
            "executed_at": datetime(2024, 9, 1),
            "amount": -200.0,
            "flow_type": "WITHDRAWAL",
        },
    ]

    rows = compute_active_capital_timeline(flows, as_of=datetime(2025, 1, 1))

    assert [row["running_active_capital"] for row in rows] == [1000.0, 1500.0, 1300.0]
    assert rows[0]["days_active"] > rows[1]["days_active"] > rows[2]["days_active"]
