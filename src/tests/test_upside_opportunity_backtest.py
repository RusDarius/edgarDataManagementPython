from __future__ import annotations

import duckdb

from edge_research_tools.upside_opportunity_backtest import (
    load_historical_momentum_observations,
    run_upside_momentum_backtest,
    score_and_bucket_momentum_observations,
)


def _build_snapshot_db(db_path) -> None:
    conn = duckdb.connect(db_path.as_posix())
    try:
        conn.execute("""
            CREATE TABLE symbol_day_feature_snapshot (
                source_day_label VARCHAR,
                source_date DATE,
                symbol VARCHAR,
                market VARCHAR,
                exchange VARCHAR,
                country VARCHAR
            )
        """)
        conn.execute("""
            CREATE TABLE symbol_day_feature_values (
                source_day_label VARCHAR,
                source_date DATE,
                symbol VARCHAR,
                field_name VARCHAR,
                directional_universe_percentile DOUBLE
            )
        """)
        conn.execute("""
            CREATE TABLE symbol_day_sleeve_scores (
                source_day_label VARCHAR,
                source_date DATE,
                symbol VARCHAR,
                sleeve_name VARCHAR,
                avg_directional_universe_percentile DOUBLE
            )
        """)
        conn.execute("""
            CREATE TABLE symbol_day_forward_labels (
                source_day_label VARCHAR,
                symbol VARCHAR,
                forward_return_5d_pct DOUBLE
            )
        """)

        # Two days, several symbols each, with momentum roughly correlated to
        # forward return so bucket ordering can be validated.
        days = [("01_01_2024", "2024-01-01"), ("02_01_2024", "2024-01-02")]
        symbols = [
            ("NASDAQ:AAA", 0.9, 15.0),
            ("NASDAQ:BBB", 0.7, 8.0),
            ("NASDAQ:CCC", 0.5, 2.0),
            ("NASDAQ:DDD", 0.3, -3.0),
            ("NASDAQ:EEE", 0.1, -10.0),
        ]
        for day_label, iso_date in days:
            for symbol, pct, fwd_return in symbols:
                conn.execute(
                    "INSERT INTO symbol_day_feature_snapshot VALUES (?, ?, ?, 'america', 'NASDAQ', 'UNITED STATES')",
                    [day_label, iso_date, symbol],
                )
                conn.execute(
                    "INSERT INTO symbol_day_feature_values VALUES (?, ?, ?, 'ADRP', ?)",
                    [day_label, iso_date, symbol, pct],
                )
                conn.execute(
                    "INSERT INTO symbol_day_feature_values VALUES (?, ?, ?, 'relative_volume_10d_calc', ?)",
                    [day_label, iso_date, symbol, pct],
                )
                conn.execute(
                    "INSERT INTO symbol_day_sleeve_scores VALUES (?, ?, ?, 'momentum_context', ?)",
                    [day_label, iso_date, symbol, pct],
                )
                conn.execute(
                    "INSERT INTO symbol_day_forward_labels VALUES (?, ?, ?)",
                    [day_label, symbol, fwd_return],
                )
    finally:
        conn.close()


def test_load_historical_momentum_observations_resolves_horizon_and_filters(
    tmp_path,
) -> None:
    db_path = tmp_path / "snapshot.duckdb"
    _build_snapshot_db(db_path)

    observations, resolved_horizon = load_historical_momentum_observations(
        snapshot_database_path=db_path,
        markets=["america"],
        ranking_horizon=5,
    )

    assert resolved_horizon == 5
    assert len(observations) == 10  # 2 days x 5 symbols
    aaa_rows = [row for row in observations if row["symbol"] == "NASDAQ:AAA"]
    assert len(aaa_rows) == 2
    assert aaa_rows[0]["adrp_pct_today"] == 0.9
    assert aaa_rows[0]["forward_return_pct"] == 15.0


def test_load_historical_momentum_observations_falls_back_to_max_horizon(
    tmp_path,
) -> None:
    db_path = tmp_path / "snapshot.duckdb"
    _build_snapshot_db(db_path)

    _observations, resolved_horizon = load_historical_momentum_observations(
        snapshot_database_path=db_path,
        ranking_horizon=999,
    )
    assert resolved_horizon == 5


def test_score_and_bucket_momentum_observations_orders_buckets_by_score(
    tmp_path,
) -> None:
    db_path = tmp_path / "snapshot.duckdb"
    _build_snapshot_db(db_path)

    observations, _horizon = load_historical_momentum_observations(
        snapshot_database_path=db_path,
    )
    bucket_rows = score_and_bucket_momentum_observations(observations, bucket_count=5)

    assert len(bucket_rows) == 5
    total_samples = sum(row["sample_count"] for row in bucket_rows)
    assert total_samples == len(observations)
    # Highest bucket should show a higher median forward return than lowest.
    lowest = bucket_rows[0]
    highest = bucket_rows[-1]
    assert highest["median_forward_return_pct"] > lowest["median_forward_return_pct"]


def test_score_and_bucket_momentum_observations_handles_empty_input() -> None:
    assert score_and_bucket_momentum_observations([]) == []


def test_run_upside_momentum_backtest_end_to_end(tmp_path) -> None:
    db_path = tmp_path / "snapshot.duckdb"
    _build_snapshot_db(db_path)

    output_dir = tmp_path / "upside_momentum_backtest"
    result = run_upside_momentum_backtest(
        output_dir=output_dir,
        snapshot_database_path=db_path,
        bucket_count=5,
    )

    assert result["observation_count"] == 10
    assert result["resolved_horizon"] == 5
    assert result["buckets_csv"].exists()
    assert result["database_path"].exists()
    assert result["report_md"].exists()
    assert result["manifest_path"].exists()

    conn = duckdb.connect(result["database_path"].as_posix(), read_only=True)
    try:
        rows = conn.execute(
            "SELECT COUNT(*) FROM upside_momentum_backtest_buckets"
        ).fetchone()
    finally:
        conn.close()
    assert rows[0] == 5
