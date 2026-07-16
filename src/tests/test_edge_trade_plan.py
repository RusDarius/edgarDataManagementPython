from edge_research_tools.edge_trade_plan import (
    DEFAULT_TRADE_PLAN_VARIANTS,
    TradePlanVariant,
    backtest_trade_plan_variants,
    build_current_trade_plan_rows,
)


def test_build_current_trade_plan_rows_marks_supported_momentum_entry() -> None:
    unified_rows = [
        {
            "symbol": "NASDAQ:TEST",
            "unified_edge_highlight_rank": 5,
            "hist_occurrence_count_in_setup": 8,
            "historical_validation_bucket": "supported",
            "lane_leader_median_fwd_5d_ci_low": 1.2,
            "safety_bucket": "balanced",
            "forward_valuation_upside_pct": 20.0,
        }
    ]
    latest_fields = {
        "NASDAQ:TEST": {
            "perf_5d": 4.0,
            "perf_1m": 12.0,
            "atrp": 3.0,
            "relative_volume_10d_calc": 1.4,
            "recommend_all": 0.3,
        }
    }

    rows = build_current_trade_plan_rows(unified_rows, latest_fields)

    assert rows[0]["entry_state"] == "ENTER_STARTER"
    assert "momentum_confirmed_5d" in rows[0]["matched_entry_variants"]
    assert rows[0]["trade_plan_quality_score"] == 1.0
    assert rows[0]["extension_atr_units"] == 1.3333


def test_build_current_trade_plan_rows_rejects_unconfirmed_speculative_name() -> None:
    rows = build_current_trade_plan_rows(
        [
            {
                "symbol": "NASDAQ:RISK",
                "unified_edge_highlight_rank": 1,
                "hist_occurrence_count_in_setup": 1,
                "historical_validation_bucket": "thin",
                "safety_bucket": "speculative",
                "forward_valuation_upside_pct": -15.0,
            }
        ],
        {
            "NASDAQ:RISK": {
                "perf_5d": 30.0,
                "perf_1m": 50.0,
                "atrp": 5.0,
                "relative_volume_10d_calc": 5.0,
                "recommend_all": -0.4,
            }
        },
    )

    assert rows[0]["entry_state"] == "REJECT"
    assert rows[0]["entry_variant_count"] == 0


def test_backtest_trade_plan_variants_uses_non_overlapping_target_exit() -> None:
    rows = [
        {
            "source_date": f"2026-04-{day:02d}",
            "symbol": "NASDAQ:TEST",
            "close_price": close,
            "perf_5d": 2.0,
            "perf_1m": 8.0,
            "atrp": 2.0,
            "relative_volume_10d_calc": 1.3,
            "recommend_all": 0.2,
        }
        for day, close in enumerate(
            [100.0, 104.0, 111.0, 110.0, 109.0, 112.0, 114.0],
            start=1,
        )
    ]
    variant = TradePlanVariant(
        name="momentum_confirmed_5d",
        description="test",
        max_holding_days=5,
        target_pct=10.0,
        stop_pct=7.0,
    )

    summaries, trades = backtest_trade_plan_variants(rows, variants=[variant])

    assert len(trades) == 2
    assert trades[0]["entry_date"] == "2026-04-01"
    assert trades[0]["exit_date"] == "2026-04-03"
    assert trades[0]["exit_reason"] == "target_exit"
    assert trades[0]["return_pct"] == 11.0
    assert trades[1]["entry_date"] == "2026-04-04"
    assert summaries[0]["trade_count"] == 2


def test_default_variants_include_baseline_and_three_timing_methods() -> None:
    assert [variant.name for variant in DEFAULT_TRADE_PLAN_VARIANTS] == [
        "baseline_5d",
        "momentum_confirmed_5d",
        "controlled_pullback_10d",
        "risk_controlled_10d",
    ]
