from __future__ import annotations

from datetime import date, timedelta

import duckdb

from edge_research_tools.earnings_priority_lens import (
    build_earnings_priority_lens,
    classify_earnings_window_bucket,
    load_earnings_window_candidates,
    merge_earnings_priority_row,
    rank_earnings_priority_rows,
)


def _build_all_fields_db(db_path, *, as_of_date: date, rows: list[dict]) -> None:
    conn = duckdb.connect(db_path.as_posix())
    try:
        conn.execute("""
            CREATE TABLE run_metadata (
                run_id VARCHAR,
                created_at_utc TIMESTAMP
            )
        """)
        conn.execute(
            "INSERT INTO run_metadata VALUES ('run_1', ?)",
            [as_of_date],
        )
        conn.execute("""
            CREATE TABLE all_fields_rows (
                run_id VARCHAR,
                row_number BIGINT,
                symbol VARCHAR,
                name VARCHAR,
                market VARCHAR,
                exchange VARCHAR,
                country VARCHAR,
                sector VARCHAR,
                industry VARCHAR,
                close DOUBLE,
                "Perf.5D" DOUBLE,
                "Perf.1M" DOUBLE,
                "Perf.3M" DOUBLE,
                "ATRP" DOUBLE,
                relative_volume_10d_calc DOUBLE,
                "Recommend.All" DOUBLE,
                "SMA20" DOUBLE,
                "SMA50" DOUBLE,
                market_cap_basic DOUBLE,
                volume DOUBLE,
                earnings_release_next_trading_date_fq DATE
            )
        """)
        for index, row in enumerate(rows):
            conn.execute(
                """
                INSERT INTO all_fields_rows VALUES
                ('run_1', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    index,
                    row["symbol"],
                    row.get("name", row["symbol"]),
                    row.get("market", "america"),
                    row.get("exchange", "NASDAQ"),
                    row.get("country", "UNITED STATES"),
                    row.get("sector", "Technology"),
                    row.get("industry", "Software"),
                    row.get("close", 100.0),
                    row.get("perf_5d", 1.0),
                    row.get("perf_1m", 2.0),
                    row.get("perf_3m", 3.0),
                    row.get("atrp", 4.0),
                    row.get("relative_volume_10d_calc", 1.1),
                    row.get("recommend_all", 0.2),
                    row.get("sma20", 95.0),
                    row.get("sma50", 90.0),
                    row.get("market_cap_basic", 1_000_000_000.0),
                    row.get("volume", 500_000.0),
                    row["earnings_date"],
                ],
            )
    finally:
        conn.close()


def test_classify_earnings_window_bucket_boundaries() -> None:
    assert classify_earnings_window_bucket(0) == "0_7d"
    assert classify_earnings_window_bucket(7) == "0_7d"
    assert classify_earnings_window_bucket(8) == "8_14d"
    assert classify_earnings_window_bucket(14) == "8_14d"
    assert classify_earnings_window_bucket(15) == "15_21d"
    assert classify_earnings_window_bucket(21) == "15_21d"
    assert classify_earnings_window_bucket(22) == "22_30d"
    assert classify_earnings_window_bucket(30) == "22_30d"
    assert classify_earnings_window_bucket(31) == "beyond_30d"
    assert classify_earnings_window_bucket(None) == "unknown"


def test_load_earnings_window_candidates_filters_by_window_and_region(tmp_path) -> None:
    as_of = date(2026, 7, 10)
    db_path = tmp_path / "all_fields.duckdb"
    _build_all_fields_db(
        db_path,
        as_of_date=as_of,
        rows=[
            {
                "symbol": "NASDAQ:INSIDE",
                "earnings_date": as_of + timedelta(days=10),
            },
            {
                "symbol": "NASDAQ:TOOFAR",
                "earnings_date": as_of + timedelta(days=45),
            },
            {
                "symbol": "NASDAQ:PAST",
                "earnings_date": as_of - timedelta(days=1),
            },
            {
                "symbol": "TSX:WRONGMARKET",
                "market": "canada",
                "earnings_date": as_of + timedelta(days=5),
            },
            {
                "symbol": "NASDAQ:TOOSMALL",
                "market_cap_basic": 10_000_000.0,
                "earnings_date": as_of + timedelta(days=3),
            },
        ],
    )

    rows = load_earnings_window_candidates(
        source_database_path=db_path,
        as_of_date=as_of.isoformat(),
        lookahead_days=30,
        markets=["america"],
        min_market_cap_usd=500_000_000.0,
    )

    symbols = {row["symbol"] for row in rows}
    assert symbols == {"NASDAQ:INSIDE"}
    row = rows[0]
    assert row["bare_ticker"] == "INSIDE"
    assert row["earnings_days_until"] == 10
    assert row["earnings_window_bucket"] == "8_14d"


def test_merge_earnings_priority_row_prefers_unified_then_backfills_partial_tiers() -> (
    None
):
    earnings_row = {
        "symbol": "NASDAQ:FULL",
        "earnings_days_until": 5,
        "earnings_window_bucket": "0_7d",
        "close": 50.0,
    }

    unified_row = {
        "symbol": "NASDAQ:FULL",
        "unified_edge_highlight_rank": 3,
        "unified_edge_highlight_score": 0.91,
        "big_mover_rank": 2,
    }
    merged_full = merge_earnings_priority_row(
        earnings_row,
        unified_row=unified_row,
        screen_row=None,
        safety_row=None,
    )
    assert merged_full["data_coverage_tier"] == "unified_pipeline"
    assert merged_full["in_unified_pipeline_flag"] == 1
    assert merged_full["unified_edge_highlight_rank"] == 3
    assert merged_full["close"] == 50.0  # earnings-only context preserved

    partial_earnings_row = {
        "symbol": "NASDAQ:PARTIAL",
        "earnings_days_until": 12,
        "earnings_window_bucket": "8_14d",
    }
    screen_row = {
        "symbol": "NASDAQ:PARTIAL",
        "screen_rank": 44,
        "composite_score": 0.62,
        "adrp_directional_universe_percentile": 0.7,
    }
    safety_row = {
        "symbol": "NASDAQ:PARTIAL",
        "safety_rank": 12,
        "balance_sheet_safety_score": 0.8,
        "safety_companion_score": 0.75,
    }
    merged_partial = merge_earnings_priority_row(
        partial_earnings_row,
        unified_row=None,
        screen_row=screen_row,
        safety_row=safety_row,
    )
    assert merged_partial["data_coverage_tier"] == "partial_scan_coverage"
    assert merged_partial["in_unified_pipeline_flag"] == 0
    assert merged_partial["in_screen_universe_flag"] == 1
    assert merged_partial["in_safety_scored_universe_flag"] == 1
    assert merged_partial["screen_rank"] == 44
    assert merged_partial["adrp_pct_today"] == 0.7
    assert merged_partial["safety_rank_global"] == 12
    assert merged_partial["balance_sheet_safety_score"] == 0.8

    earnings_only_row = {
        "symbol": "NASDAQ:ONLY",
        "earnings_days_until": 20,
        "earnings_window_bucket": "15_21d",
    }
    merged_only = merge_earnings_priority_row(
        earnings_only_row,
        unified_row=None,
        screen_row=None,
        safety_row=None,
    )
    assert merged_only["data_coverage_tier"] == "earnings_only"
    assert merged_only["in_unified_pipeline_flag"] == 0
    assert merged_only["in_screen_universe_flag"] == 0
    assert merged_only["in_safety_scored_universe_flag"] == 0


def test_rank_earnings_priority_rows_orders_by_days_until_without_rewriting_ranks() -> (
    None
):
    rows = [
        {
            "symbol": "NASDAQ:LATER",
            "earnings_days_until": 20,
            "data_coverage_tier": "unified_pipeline",
            "unified_edge_highlight_rank": 1,
        },
        {
            "symbol": "NASDAQ:SOON",
            "earnings_days_until": 2,
            "data_coverage_tier": "earnings_only",
            "unified_edge_highlight_rank": "",
        },
        {
            "symbol": "NASDAQ:MID",
            "earnings_days_until": 10,
            "data_coverage_tier": "partial_scan_coverage",
            "screen_rank": 5,
        },
    ]

    ordered = rank_earnings_priority_rows(rows)

    assert [row["symbol"] for row in ordered] == [
        "NASDAQ:SOON",
        "NASDAQ:MID",
        "NASDAQ:LATER",
    ]
    assert [row["earnings_priority_rank"] for row in ordered] == [1, 2, 3]
    # Inherited rank fields must be copied through unchanged, not renumbered.
    later_row = next(row for row in ordered if row["symbol"] == "NASDAQ:LATER")
    assert later_row["unified_edge_highlight_rank"] == 1


def test_build_earnings_priority_lens_end_to_end(tmp_path) -> None:
    as_of = date(2026, 7, 10)
    db_path = tmp_path / "all_fields.duckdb"
    _build_all_fields_db(
        db_path,
        as_of_date=as_of,
        rows=[
            {"symbol": "NASDAQ:COVERED", "earnings_date": as_of + timedelta(days=3)},
            {"symbol": "NASDAQ:UNCOVERED", "earnings_date": as_of + timedelta(days=6)},
        ],
    )

    unified_csv = tmp_path / "unified.csv"
    unified_csv.write_text(
        "symbol,unified_edge_highlight_rank,unified_edge_highlight_score\n"
        "NASDAQ:COVERED,7,0.81\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "earnings_priority_lens"
    result = build_earnings_priority_lens(
        output_dir=output_dir,
        source_database_path=db_path,
        unified_csv_path=unified_csv,
        scan_day=as_of.isoformat(),
        lookahead_days=30,
        markets=["america"],
    )

    assert result["row_count"] == 2
    assert result["tier_counts"]["unified_pipeline"] == 1
    assert result["tier_counts"]["earnings_only"] == 1
    assert result["candidates_csv"].exists()
    assert result["database_path"].exists()

    conn = duckdb.connect(result["database_path"].as_posix(), read_only=True)
    try:
        rows = conn.execute(
            "SELECT symbol, data_coverage_tier, unified_edge_highlight_rank, "
            "earnings_priority_rank FROM earnings_priority_candidates "
            "ORDER BY earnings_priority_rank"
        ).fetchall()
    finally:
        conn.close()

    assert rows[0][0] == "NASDAQ:COVERED"
    assert rows[0][1] == "unified_pipeline"
    assert rows[0][2] == 7
    assert rows[1][0] == "NASDAQ:UNCOVERED"
    assert rows[1][1] == "earnings_only"
