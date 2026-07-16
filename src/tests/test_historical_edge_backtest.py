from datetime import date

from edge_research_tools.historical_edge_backtest import (
    HistoricalEdgeExecutionRule,
    select_anchor_dates,
    simulate_historical_edge_execution,
)
from edge_research_tools.historical_edge_progression import (
    load_historical_progression_fields,
    merge_unified_historical_progression,
)


def test_select_anchor_dates_uses_available_trading_days() -> None:
    available = [
        date(2026, 4, 6),
        date(2026, 4, 7),
        date(2026, 4, 9),
        date(2026, 4, 10),
        date(2026, 4, 13),
        date(2026, 4, 14),
    ]

    assert select_anchor_dates(
        available,
        mode="every_n_trading_days",
        spacing_trading_days=2,
    ) == [date(2026, 4, 6), date(2026, 4, 9), date(2026, 4, 13)]


def test_select_anchor_dates_takes_first_and_last_session_per_week() -> None:
    available = [
        date(2026, 4, 6),
        date(2026, 4, 7),
        date(2026, 4, 9),
        date(2026, 4, 10),
        date(2026, 4, 13),
        date(2026, 4, 14),
    ]

    assert select_anchor_dates(available, mode="twice_weekly") == [
        date(2026, 4, 6),
        date(2026, 4, 10),
        date(2026, 4, 13),
        date(2026, 4, 14),
    ]


def test_execution_rule_exits_on_rank_deterioration_then_reenters() -> None:
    rows = [
        {
            "anchor_date": "2026-04-01",
            "symbol": "A",
            "historical_edge_rank": 5,
            "close_price": 100.0,
        },
        {
            "anchor_date": "2026-04-03",
            "symbol": "A",
            "historical_edge_rank": 8,
            "close_price": 105.0,
        },
        {
            "anchor_date": "2026-04-06",
            "symbol": "A",
            "historical_edge_rank": 30,
            "close_price": 110.0,
        },
        {
            "anchor_date": "2026-04-08",
            "symbol": "A",
            "historical_edge_rank": 4,
            "close_price": 108.0,
        },
        {
            "anchor_date": "2026-04-10",
            "symbol": "A",
            "historical_edge_rank": 3,
            "close_price": 112.0,
        },
    ]
    rule = HistoricalEdgeExecutionRule(
        rule_id="test",
        entry_rank=10,
        exit_rank=20,
        max_holding_observations=10,
    )

    summaries, trades = simulate_historical_edge_execution(rows, rules=[rule])

    assert len(trades) == 2
    assert trades[0]["exit_reason"] == "rank_exit"
    assert trades[0]["return_pct"] == 10.0
    assert trades[1]["entry_date"] == "2026-04-08"
    assert trades[1]["exit_reason"] == "window_end"
    assert summaries[0]["trade_count"] == 2


def test_execution_rule_uses_price_when_symbol_falls_outside_candidate_set() -> None:
    rows = [
        {
            "anchor_date": "2026-04-01",
            "symbol": "A",
            "historical_edge_rank": 5,
            "close_price": 100.0,
        },
        {
            "anchor_date": "2026-04-03",
            "symbol": "B",
            "historical_edge_rank": 2,
            "close_price": 80.0,
        },
    ]
    prices = [
        {"anchor_date": "2026-04-01", "symbol": "A", "close_price": 100.0},
        {"anchor_date": "2026-04-03", "symbol": "A", "close_price": 90.0},
    ]
    rule = HistoricalEdgeExecutionRule(
        rule_id="test",
        entry_rank=10,
        exit_rank=20,
        max_holding_observations=10,
    )

    _, trades = simulate_historical_edge_execution(
        rows, rules=[rule], price_rows=prices
    )

    assert trades[0]["exit_reason"] == "rank_exit"
    assert trades[0]["return_pct"] == -10.0


def test_progression_marks_latest_absence_as_exit_and_gap_reentry_as_fresh(
    tmp_path,
) -> None:
    import duckdb

    database_path = tmp_path / "history.duckdb"
    conn = duckdb.connect(database_path.as_posix())
    try:
        conn.execute("""
            CREATE TABLE edge_anchor_plan (
                config_hash VARCHAR, anchor_date DATE, status VARCHAR
            )
            """)
        conn.executemany(
            "INSERT INTO edge_anchor_plan VALUES ('cfg', ?, 'complete')",
            [("2026-04-01",), ("2026-04-02",), ("2026-04-03",)],
        )
        conn.execute("""
            CREATE TABLE edge_candidate_snapshots (
                config_hash VARCHAR, anchor_date DATE, symbol VARCHAR,
                historical_edge_rank INTEGER, historical_edge_score DOUBLE,
                hist_sample_count_5d INTEGER,
                hist_median_return_5d_pct DOUBLE,
                hist_win_rate_5d DOUBLE, any_setup_rate DOUBLE
            )
            """)
        conn.executemany(
            "INSERT INTO edge_candidate_snapshots VALUES ('cfg', ?, ?, ?, 0.7, 5, 2.0, 0.6, 0.1)",
            [
                ("2026-04-01", "EXIT_ME", 12),
                ("2026-04-01", "REENTER", 8),
                ("2026-04-02", "OTHER", 2),
                ("2026-04-03", "REENTER", 7),
            ],
        )
    finally:
        conn.close()

    fields = load_historical_progression_fields(
        historical_database_path=database_path,
        config_hash="cfg",
    )

    assert fields["EXIT_ME"]["historical_edge_daily_state"] == "EXIT"
    assert fields["EXIT_ME"]["historical_edge_current_rank"] is None
    assert fields["EXIT_ME"]["historical_edge_rank_exit_flag"] == 1
    assert fields["REENTER"]["historical_edge_daily_state"] == "ENTER_FRESH"
    assert fields["REENTER"]["historical_edge_fresh_top20_entry_flag"] == 1


def test_merge_progression_adds_fields_to_every_unified_name() -> None:
    rows = merge_unified_historical_progression(
        [{"symbol": "KNOWN", "unified_edge_highlight_rank": 1}, {"symbol": "NEW"}],
        {
            "KNOWN": {
                "symbol": "KNOWN",
                "historical_edge_current_rank": 4,
                "historical_edge_daily_state": "HOLD",
            }
        },
        config_hash="cfg",
        historical_database_path="history.duckdb",
    )

    assert rows[0]["historical_edge_current_rank"] == 4
    assert rows[0]["historical_edge_daily_state"] == "HOLD"
    assert rows[1]["historical_edge_daily_state"] == "OUTSIDE"
    assert rows[1]["historical_edge_config_hash"] == "cfg"
